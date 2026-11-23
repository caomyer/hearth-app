"""Asking each connected brokerage for its investment activity, and keeping it in the feed.

Activity is what a brokerage account did: each buy and sell, dividend and interest payment,
contribution, fee, and transfer of cash or shares. Plaid sends it from
``/investments/transactions/get``, a separate subscription from holdings, which the first ask
adds to the Item for as long as the Item lasts; so it is its own operation, `ACTIVITY`, and
never asked for unless approved.

Plaid keeps no cursor for it. The first ask covers `HISTORY_DAYS`, the most Plaid gives; each
later one goes back `REFRESH_DAYS` before the last day asked for, since a brokerage may
correct or cancel what it sent recently, and everything found is kept by Plaid's own id as it
was last sent (``Feed.record_activity``).

Nothing in hearth's books reads it: a brokerage account there is anchored at what its
brokerage last reported holding (``hearth.plaid.opening``), never derived from activity.
It is kept for a ledger outside hearth, which needs each trade and dividend to keep the
account row by row.
"""

import logging
import sqlite3
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from hearth.clock import Clock
from hearth.plaid.approval import Operation
from hearth.plaid.client import (
    LOGIN_REQUIRED,
    Activity,
    PlaidClient,
    PlaidError,
    PlaidRejectedError,
    Security,
)
from hearth.plaid.credentials import CredentialStore
from hearth.plaid.feed import Feed
from hearth.plaid.registry import ItemState, Registry
from hearth.plaid.setup import PlaidSetup
from hearth.vault.errors import VaultError
from hearth.vault.secret import Secret
from hearth.vault.tokens import TokenVault

_log = logging.getLogger(__name__)

HISTORY_DAYS = 730
"""How far back the first ask reaches: the 24 months Plaid keeps."""

REFRESH_DAYS = 30
"""How far before the last day asked for each later ask starts again, for what a brokerage
corrected or cancelled since."""

MAX_PAGES = 200
"""More pages than this for one Item means Plaid's total is not settling; stop rather than
loop."""


@dataclass(frozen=True, slots=True)
class Asked:
    """One connection asked for its activity, and how it answered."""

    item_id: str
    institution: str
    start: date
    end: date
    activity: int
    """How many activities it sent over those days."""
    problem: str | None = None
    """Why it sent none: Plaid's error code, or what else went wrong."""


@dataclass(frozen=True, slots=True)
class Fetched:
    """What one round of asking found."""

    fetched_at: datetime
    asked: tuple[Asked, ...] = ()


def fetch_activity(
    conn: sqlite3.Connection,
    feed_path: Path,
    setup: PlaidSetup,
    clock: Clock,
    *,
    only: Collection[str] | None = None,
) -> Fetched:
    """Ask every working connection that reaches an investment account for its activity since
    it was last asked, and keep what each sends.

    A connection is not asked unless reading activity is approved for its environment in this
    run. One that cannot be asked stops only itself and keeps nothing of the ask; one that
    needs its member to sign in again is marked so, as a sync marks it. With `only`, only
    those Items are asked."""
    registry = Registry(conn)
    vault = TokenVault(conn, setup.keys())
    store = CredentialStore(conn, vault)
    fetched_at = clock()
    today = fetched_at.astimezone().date()
    asked: list[Asked] = []
    for item in registry.all_items():
        if item.state is not ItemState.WORKING or (only is not None and item.item_id not in only):
            continue
        if not any(account.is_investment for account in registry.accounts_of(item.item_id)):
            continue
        saved = store.get(item.credentials)
        if saved is not None and not setup.allows(saved.environment, Operation.ACTIVITY):
            continue
        with Feed.open(feed_path) as feed:
            through = feed.activity_through(item.item_id)
        start = (
            today - timedelta(days=HISTORY_DAYS)
            if through is None
            else through - timedelta(days=REFRESH_DAYS)
        )
        problem: str | None = None
        login = False
        found: list[Activity] = []
        securities: dict[str, Security] = {}
        try:
            client = setup.client(store.credentials(item.credentials))
            found, securities = _every_page(
                client, vault.get(item.member, item.item_id), start, today
            )
        except PlaidRejectedError as error:
            problem = error.error_code
            if error.error_code == LOGIN_REQUIRED:
                login = True
                registry.set_state(item.item_id, ItemState.LOGIN_REQUIRED, problem=problem)
        except PlaidError as error:
            problem = str(error)
        except VaultError, LookupError:
            problem = "its access token or its Plaid account's keys could not be opened"
        with Feed.open(feed_path) as feed:
            if problem is None:
                feed.record_activity(
                    item.item_id, start, today, found, list(securities.values()), now=fetched_at
                )
            else:
                _log.warning("could not read a connection's investment activity: %s", problem)
                feed.activity_failed(
                    item.item_id, start, today, problem, login=login, now=fetched_at
                )
        asked.append(Asked(item.item_id, item.institution, start, today, len(found), problem))
    return Fetched(fetched_at=fetched_at, asked=tuple(asked))


def _every_page(
    client: PlaidClient, access_token: Secret, start: date, end: date
) -> tuple[list[Activity], dict[str, Security]]:
    """Every activity over the days, page by page until Plaid's total is reached."""
    found: dict[str, Activity] = {}
    securities: dict[str, Security] = {}
    offset = 0
    for _ in range(MAX_PAGES):
        page = client.activity_page(access_token, start, end, offset)
        for activity in page.activity:
            found[activity.activity_id] = activity
        for security in page.securities:
            securities[security.security_id] = security
        offset += len(page.activity)
        if not page.activity or offset >= page.total:
            return list(found.values()), securities
    raise PlaidError("Plaid kept sending investment activity past its own total")
