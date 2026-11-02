"""Bringing the books up to date with the household's connections: sync each one, keep each
brokerage's holdings and today's price of each, then recompute.

This is the one path every sync takes: `hearth serve` at start-up and every morning, and
`hearth plaid sync` run by hand. A sync is only worth running for what it changes in the books,
so none ends at the fetch: each recomputes after it, and says whether the books now hold what it
brought (`Refreshed.books_failed`). A recompute that fails keeps the previous books, labelled with
their time, beside what went wrong (``hearth.books``).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from hearth import books, db
from hearth.clock import Clock
from hearth.config import Settings
from hearth.plaid.connector import sync_all
from hearth.plaid.holdings import Priced, price_holdings
from hearth.plaid.setup import PlaidSetup
from hearth.plaid.sync import Failed, Skipped, Synced

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Refreshed:
    """What one sync and the recompute after it did."""

    synced: Sequence[Synced | Skipped | Failed] | None
    """Each connection's sync, or None when the sync stopped before it could say."""
    priced: Priced | None
    """Each brokerage's holdings, or None when asking for them stopped."""
    status: books.Status
    """The books after the recompute: new ones, or the previous ones beside its failure."""

    @property
    def books_failed(self) -> books.Failure | None:
        """Why the books do not hold what this sync brought, when they do not."""
        return self.status.failure


def sync_then_recompute(
    settings: Settings, plaid: PlaidSetup, bookkeeper: books.Bookkeeper, clock: Clock
) -> Refreshed:
    """Sync every working connection and keep every brokerage's holdings and today's price of
    each, each refused for a real connection unless approved, then recompute the books, which
    record how current each connection is: the morning's recompute starts with the sync, so it
    may finish first.

    A brokerage's holdings and prices are today's only (``hearth.plaid.holdings``), so they are
    asked for every morning: a morning missed is a day with neither, for good. That holds too
    for a fund no position in the books holds, as when an inherited ledger keeps the brokerage
    account and holds none of the fund: its price is kept for the day it does, and the log says
    so rather than leaving a live call that values nothing today unexplained."""
    synced: list[Synced | Skipped | Failed] | None = None
    priced: Priced | None = None
    conn = db.connect(settings.database)
    try:
        synced = sync_all(conn, settings.feed_database, plaid, clock)
    except Exception:
        _log.exception("the sync stopped")
    try:
        priced = price_holdings(conn, settings.feed_database, plaid, clock)
    except Exception:
        _log.exception("reading holdings stopped")
    finally:
        conn.close()
    bookkeeper.recompute(wait=True)
    status = bookkeeper.status
    computed = status.books
    if priced is not None and computed is not None and status.failure is None:
        held = {valued.holding.commodity for valued in computed.today.positions}
        if unheld := sorted(set(priced.symbols) - held):
            _log.info(
                "kept today's price of %s, which no position in the books holds yet: a"
                " brokerage's price is today's only, so a day not asked can never be priced"
                " later",
                ", ".join(unheld),
            )
    return Refreshed(synced, priced, status)
