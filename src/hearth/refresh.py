"""Bringing the books up to date with the household's connections: sync each one, keep each
brokerage's holdings and today's price of each, and its activity, then recompute.

This is the one path every sync takes: `hearth serve` and the Mac app's `hearth bridge` at
start-up and every morning, the app's sync control, and `hearth plaid sync` run by hand. A sync
is only worth running for what it changes in the books, so none ends at the fetch: each
recomputes after it, and says whether the books now hold what it brought
(`Refreshed.books_failed`). A recompute that fails keeps the previous books, labelled with their
time, beside what went wrong (``hearth.books``).

A process that syncs on its own, at start-up, every morning and when asked, does it through one
`Refresher`, which runs one sync at a time in the background and remembers how the last went.
"""

import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum

from hearth import books, db
from hearth.clock import Clock
from hearth.config import Settings
from hearth.connections import Answered, Trouble
from hearth.plaid.activity import Fetched, fetch_activity
from hearth.plaid.approval import Operation
from hearth.plaid.client import PlaidUnreachableError
from hearth.plaid.connector import sync_all
from hearth.plaid.holdings import Priced, price_holdings
from hearth.plaid.setup import PlaidSetup
from hearth.plaid.sync import Failed, Skipped, Synced
from hearth.vault.errors import VaultError

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
    activity: Fetched | None = None
    """Each brokerage's investment activity, or None when asking for it stopped."""
    stopped: Trouble | None = None
    """What stopped the sync, the holdings or the activity before it could ask every
    connection, when something did: never one connection's own trouble, which stops only it."""

    @property
    def books_failed(self) -> books.Failure | None:
        """Why the books do not hold what this sync brought, when they do not."""
        return self.status.failure

    def answered(self) -> dict[str, Answered]:
        """How each connection asked answered, by Item: how many new transactions its sync
        brought, and the first trouble its sync, its holdings or its activity met. One that
        another sync was already bringing up to date was not asked, so it is not here."""
        found: dict[str, Answered] = {}
        for synced in self.synced or ():
            match synced:
                case Synced(item_id=item_id, run=run, trouble=trouble):
                    found[item_id] = Answered(run.added, trouble)
                case Failed(item_id=item_id, trouble=trouble):
                    found[item_id] = Answered(0, trouble)
        brokerages = [
            *((a.item_id, a.trouble) for a in (self.priced.asked if self.priced else ())),
            *((a.item_id, a.trouble) for a in (self.activity.asked if self.activity else ())),
        ]
        for item_id, trouble in brokerages:
            before = found.get(item_id, Answered(0))
            found[item_id] = Answered(before.added, before.trouble or trouble)
        return found


def sync_then_recompute(
    settings: Settings, plaid: PlaidSetup, bookkeeper: books.Bookkeeper, clock: Clock
) -> Refreshed:
    """Sync every working connection and keep every brokerage's holdings and today's price of
    each, and its activity, each refused for a real connection unless approved, then recompute
    the books, which record how current each connection is: the morning's recompute starts
    with the sync, so it may finish first.

    A brokerage's holdings and prices are today's only (``hearth.plaid.holdings``), so they are
    asked for every morning: a morning missed is a day with neither, for good. That holds too
    for a fund no position in the books holds, as when an inherited ledger keeps the brokerage
    account and holds none of the fund: its price is kept for the day it does, and the log says
    so rather than leaving a live call that values nothing today unexplained."""
    synced: list[Synced | Skipped | Failed] | None = None
    priced: Priced | None = None
    activity: Fetched | None = None
    stopped: list[Trouble] = []
    conn = db.connect(settings.database)
    try:
        synced = sync_all(conn, settings.feed_database, plaid, clock)
    except Exception as error:
        _log.exception("the sync stopped")
        stopped.append(_trouble(error))
    try:
        priced = price_holdings(conn, settings.feed_database, plaid, clock)
    except Exception as error:
        _log.exception("reading holdings stopped")
        stopped.append(_trouble(error))
    try:
        activity = fetch_activity(conn, settings.feed_database, plaid, clock)
    except Exception as error:
        _log.exception("reading investment activity stopped")
        stopped.append(_trouble(error))
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
    return Refreshed(synced, priced, status, activity, next(iter(stopped), None))


def _trouble(error: Exception) -> Trouble:
    """What stopped a whole round of asking, in words: the Keychain, Plaid, or anything else."""
    if isinstance(error, VaultError):
        return Trouble.KEYS
    if isinstance(error, PlaidUnreachableError):
        return Trouble.UNREACHABLE
    return Trouble.FAILED


ASKING = frozenset({Operation.READ, Operation.HOLDINGS, Operation.ACTIVITY})
"""What a sync may ask of Plaid: every live call it makes is one of these. Linking a bank and
removing a connection are never part of one."""


class Begun(StrEnum):
    """What asking a `Refresher` to sync did."""

    STARTED = "started"
    RUNNING = "running"
    """A sync was already running, which brings in what this one would have; none was started."""
    NOT_ALLOWED = "not_allowed"
    """This run of hearth was approved for none of `ASKING`, so it never asks anything."""


@dataclass(frozen=True, slots=True)
class Syncing:
    """Where a `Refresher` stands, at one moment."""

    asking: frozenset[Operation]
    """What this run of hearth was approved to ask of Plaid, of `ASKING`."""
    running: bool
    finished_at: datetime | None
    """When the last sync this process ran ended, its recompute included."""
    last: Refreshed | None
    """How that sync went."""

    @property
    def answered(self) -> Mapping[str, Answered]:
        return self.last.answered() if self.last is not None else {}


NOT_SYNCING = Syncing(frozenset(), running=False, finished_at=None, last=None)
"""Where a process that never syncs stands."""


class Refresher:
    """One process's syncs: one at a time, each in the background, at start-up and every
    morning (`daily`) and whenever asked (`start`), every one through `sync_then_recompute`.

    It starts nothing unless this run of hearth was approved for something a sync asks: a run
    approved for none is told `Begun.NOT_ALLOWED`, and `daily` starts nothing. Approval is still
    enforced where it always is, by the Plaid client, whatever is asked here."""

    def __init__(
        self,
        settings: Settings,
        plaid: PlaidSetup,
        bookkeeper: books.Bookkeeper,
        clock: Clock,
        *,
        local: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._settings = settings
        self._plaid = plaid
        self._bookkeeper = bookkeeper
        self._clock = clock
        self._local = local
        """The local time, in which the morning is reckoned."""
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._finished_at: datetime | None = None
        self._last: Refreshed | None = None

    @property
    def asking(self) -> frozenset[Operation]:
        return self._plaid.approval.operations & ASKING

    @property
    def status(self) -> Syncing:
        with self._lock:
            return Syncing(self.asking, self._thread is not None, self._finished_at, self._last)

    def start(self) -> Begun:
        """Sync now, in the background, unless a sync is already running."""
        if not self.asking:
            return Begun.NOT_ALLOWED
        with self._lock:
            if self._thread is not None:
                return Begun.RUNNING
            self._thread = threading.Thread(target=self._run, name="hearth-sync", daemon=True)
            self._thread.start()
        return Begun.STARTED

    def wait(self, timeout: float | None = None) -> None:
        """Return once the sync running now, if any, has ended."""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def daily(self, at: time = books.DAILY_AT) -> threading.Event:
        """Sync now and then every morning at `at`, local time, unless nothing a sync asks was
        approved. Setting the event returned stops it."""
        stop = threading.Event()
        if not self.asking:
            return stop

        def run() -> None:
            while not stop.is_set():
                self.start()
                self.wait()
                stop.wait(books.seconds_until(at, self._local()))

        threading.Thread(target=run, name="hearth-daily-sync", daemon=True).start()
        return stop

    def _run(self) -> None:
        refreshed: Refreshed | None = None
        try:
            refreshed = sync_then_recompute(
                self._settings, self._plaid, self._bookkeeper, self._clock
            )
        except Exception:
            _log.exception("a sync stopped before its recompute")
        finally:
            with self._lock:
                self._thread = None
                self._finished_at = self._clock()
                self._last = refreshed or Refreshed(
                    None, None, self._bookkeeper.status, stopped=Trouble.FAILED
                )
