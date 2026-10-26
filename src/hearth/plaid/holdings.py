"""Asking each connected brokerage for its holdings, and keeping what it holds and the price it
gives each.

A brokerage's own price for a holding (``hearth.valuation.institution``) is today's and no
other, and so is what it says each account holds: Plaid keeps no history of either, so a day
nobody asked is a day with neither, for good. So hearth asks every morning, with the daily
sync, and whenever ``hearth plaid holdings`` is run. Each price is stored with its source, the
Plaid security it priced, the day it was current and the time it was fetched; what each
account holds is kept in the feed, as everything Plaid says of an account is, with when it
was said, and the books hold the latest of it (``hearth.plaid.opening``).

Every working connection reaching an investment account is asked, whoever holds it: a price
is a fact about the market, not about the household, and pages show it only beside a position
their viewer may see. Holdings are ``/investments/holdings/get``, positions and their prices.
A brokerage's trades and dividends are ``/investments/transactions/get``, a separate call and
a separate subscription, which hearth does not make: what an account holds is known on the
day it was asked, and how it came to hold it is not known at all.

Each ask is a `HOLDINGS` call, refused unless approved: the first one adds Plaid's Investments
product to an Item that only offered it (``hearth.plaid.approval``). An Item that offers it
needs no new sign-in for it, so nothing here opens Link.
"""

import logging
import sqlite3
from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from hearth.clock import Clock
from hearth.plaid.approval import Operation
from hearth.plaid.client import LOGIN_REQUIRED, Holdings, PlaidError, PlaidRejectedError, held
from hearth.plaid.credentials import CredentialStore
from hearth.plaid.feed import Feed
from hearth.plaid.registry import ItemState, Registry
from hearth.plaid.setup import PlaidSetup
from hearth.valuation import institution
from hearth.valuation.institution import HoldingsMalformedError, Unpriceable
from hearth.valuation.pricestore import PriceStore, Recorded
from hearth.vault.errors import VaultError
from hearth.vault.tokens import TokenVault

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Asked:
    """One connection asked for its holdings, and how it answered."""

    item_id: str
    institution: str
    holdings: int
    """How many holdings it sent, cash included."""
    problem: str | None = None
    """Why it sent none: Plaid's error code, or what else went wrong."""


@dataclass(frozen=True, slots=True)
class Priced:
    """What one round of asking found and stored."""

    fetched_at: datetime
    asked: tuple[Asked, ...] = ()
    recorded: Counter[Recorded] = field(default_factory=Counter)
    unpriceable: tuple[Unpriceable, ...] = ()
    """Holdings whose price was not stored, each with why (``institution.read_many``)."""
    symbols: tuple[str, ...] = ()
    """The ledger commodity each price stored, or already stored, is for."""


def price_holdings(
    conn: sqlite3.Connection,
    feed_path: Path,
    setup: PlaidSetup,
    clock: Clock,
    *,
    only: Collection[str] | None = None,
) -> Priced:
    """Ask every working connection that reaches an investment account for its holdings, keep
    what each of its accounts holds, and store the price its brokerage gives each holding.

    A connection is not asked unless reading holdings is approved for its environment in this
    run, so a run that approved only syncing sends nothing here. One that cannot be asked stops
    only itself; one that needs its member to sign in again is marked so, as a sync marks it,
    and what it said before is all the books know of it. With `only`, only those Items are
    asked: one just connected, so its holdings need not wait for the morning."""
    registry = Registry(conn)
    vault = TokenVault(conn, setup.keys())
    store = CredentialStore(conn, vault)
    fetched_at = clock()
    asked: list[Asked] = []
    answers: list[Mapping[str, Any]] = []
    reports: list[tuple[str, Holdings]] = []
    for item in registry.all_items():
        if item.state is not ItemState.WORKING or (only is not None and item.item_id not in only):
            continue
        if not any(account.is_investment for account in registry.accounts_of(item.item_id)):
            continue
        saved = store.get(item.credentials)
        if saved is not None and not setup.allows(saved.environment, Operation.HOLDINGS):
            continue
        problem: str | None = None
        try:
            client = setup.client(store.credentials(item.credentials))
            answer = client.holdings(vault.get(item.member, item.item_id))
            # Read on its own first, so one brokerage's malformed answer loses only its own.
            institution.read_holdings(answer, fetched_at=fetched_at)
            reported = held(answer)
        except PlaidRejectedError as error:
            problem = error.error_code
            if error.error_code == LOGIN_REQUIRED:
                registry.set_state(item.item_id, ItemState.LOGIN_REQUIRED, problem=problem)
        except (PlaidError, HoldingsMalformedError) as error:
            problem = str(error)
        except VaultError, LookupError:
            problem = "its access token or its Plaid account's keys could not be opened"
        if problem is not None:
            _log.warning("could not read a connection's holdings: %s", problem)
            asked.append(Asked(item.item_id, item.institution, 0, problem))
            continue
        answers.append(answer)
        reports.append((item.item_id, reported))
        asked.append(Asked(item.item_id, item.institution, len(answer["holdings"])))
    with Feed.open(feed_path) as feed:
        for item_id, reported in reports:
            feed.record_holdings(item_id, reported, now=fetched_at)
    prices = institution.read_many(answers, fetched_at=fetched_at)
    return Priced(
        fetched_at=fetched_at,
        asked=tuple(asked),
        recorded=Counter(institution.record(PriceStore(conn), prices)),
        unpriceable=prices.unpriceable,
        symbols=tuple(quote.symbol for quote in prices.quotes),
    )
