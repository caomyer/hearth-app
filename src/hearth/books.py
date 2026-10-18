"""The household's books as of one recompute: the ledger as loaded, and what it was worth.

Figures are established here, daily and when someone asks, never on page load (decision 6),
so every number on a page has a knowable as-of time. A recompute that cannot trust the ledger,
because it does not load or a balance assertion disagrees with the postings, establishes
nothing: the previous books stay in place, labelled with their age, next to what went wrong.
A figure a day old is better than a figure that is wrong.

The books are the ledger as its members have reviewed it (``hearth.review.overlay``): every
imported row is in them, a row nobody has checked still flagged `!` at its proposed account, so
each figure can say how much of it rests on rows nobody has looked at. A ruling recomputes the
books, so what a member decides shows in the figures within one recompute.

Nothing here decides who may see what; pages reach the books only through
``hearth.disclosure``.
"""

import logging
import sqlite3
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from beancount import loader
from beancount.core.data import Directive, Transaction
from beancount.core.interpolate import BalanceError

from hearth import db
from hearth.clock import Clock
from hearth.months import Month
from hearth.reports.balances import AssertionCheck, check_balance_assertions
from hearth.reports.coverage import AccountCoverage, account_coverage
from hearth.review.decision import Decision
from hearth.review.overlay import reviewed
from hearth.review.pile import PileItem, read_pile
from hearth.valuation.holdings import holdings_at
from hearth.valuation.networth import NetWorth, PriceLookup, value_holdings
from hearth.valuation.pricestore import PriceStore
from hearth.valuation.quotes import Quote

_log = logging.getLogger(__name__)

CURRENCY = "USD"
HISTORY_MONTHS = 12
DAILY_AT = time(6, 0)

PRICE_SOURCES = ("eodhd", "hand")
"""Where stored prices come from: the market data source, and prices a member typed in.

A hand-entered price is stored under its own source, so it never passes as the source's.
"""


@dataclass(frozen=True, slots=True)
class Books:
    """Everything one recompute established. Valuations hold every household position.

    `month_ends` are the last day of each of the previous `HISTORY_MONTHS` months, oldest
    first; the last of them is the end of last month, which "since the 1st" compares with.
    """

    computed_at: datetime
    as_of: date
    currency: str
    entries: tuple[Directive, ...]
    today: NetWorth
    month_ends: tuple[NetWorth, ...]
    coverage: tuple[AccountCoverage, ...]
    pile: tuple[PileItem, ...]
    """Every review row as the import wrote it, before any ruling: the `!` entries that carry
    a review id. `entries` hold the same rows as their rulings make them."""

    @property
    def month(self) -> Month:
        return Month.of(self.as_of)


@dataclass(frozen=True, slots=True)
class Failure:
    """Why the latest recompute established nothing.

    `disagreement` is the balance assertion the postings do not reproduce, when that is the
    reason; it names an account and amounts, so only that account's owners may read it.
    """

    at: datetime
    summary: str
    disagreement: AssertionCheck | None = None
    location: str | None = None


@dataclass(frozen=True, slots=True)
class Status:
    """What a page can say about the books right now."""

    books: Books | None
    failure: Failure | None
    recomputing: bool


class LedgerError(Exception):
    """The ledger could not be trusted, so nothing was recomputed."""

    def __init__(
        self,
        summary: str,
        disagreement: AssertionCheck | None = None,
        location: str | None = None,
    ) -> None:
        super().__init__(summary)
        self.summary = summary
        self.disagreement = disagreement
        self.location = location


def load_entries(path: Path) -> tuple[Directive, ...]:
    """The ledger at `path`, refusing one beancount reports errors in.

    A failed balance assertion is left to `compute`, which explains it to the account's
    owners alone. Beancount's own messages name accounts and amounts, so no other error's
    message is ever passed on: only where it is, which is enough to find it.
    """
    entries, errors, _ = loader.load_file(str(path))
    problems = [error for error in errors if not isinstance(error, BalanceError)]
    if problems:
        source = getattr(problems[0], "source", None) or {}
        where = f"{Path(source['filename']).name}, line {source['lineno']}" if source else None
        count = len(problems)
        summary = f"The ledger has {count} error{'s' if count > 1 else ''} hearth cannot read past"
        raise LedgerError(f"{summary}{f', the first at {where}' if where else ''}.")
    return tuple(entries)


type Decide = Callable[[Sequence[PileItem]], Mapping[str, Decision]]
"""Every review row's decision, as the rulings members may make on it decide it."""


def compute(
    ledger: Sequence[Directive], lookup: PriceLookup, *, now: datetime, decide: Decide
) -> Books:
    """Lay the rulings over the ledger, check it, and value the household today and at each
    recent month end.

    Raises `LedgerError` when a balance assertion disagrees with the postings, because every
    figure downstream would inherit the error.
    """
    pile = tuple(read_pile(e for e in ledger if _set_aside(e)))
    entries = tuple(reviewed(ledger, decide(pile)))
    for check in check_balance_assertions(entries):
        if not check.agrees:
            raise LedgerError(f"A balance check failed on {check.date:%b} {check.date.day}.", check)
    today = now.date()

    def worth(day: date) -> NetWorth:
        return value_holdings(holdings_at(entries, day), lookup, as_of=day, currency=CURRENCY)

    return Books(
        computed_at=now,
        as_of=today,
        currency=CURRENCY,
        entries=entries,
        today=worth(today),
        month_ends=tuple(worth(day) for day in month_ends(today, HISTORY_MONTHS)),
        coverage=tuple(account_coverage(entries, as_of=today)),
        pile=pile,
    )


def _set_aside(entry: Directive) -> bool:
    """Whether an entry is a review row: flagged, and keyed for a durable ruling.

    A `!` entry without a review id is simply unreviewed, not something the review desk can
    rule on, so it is counted as unreviewed in the figures and left out of the pile.
    """
    return (
        isinstance(entry, Transaction)
        and entry.flag == "!"
        and isinstance(entry.meta, dict)
        and "review-id" in entry.meta
    )


def month_ends(today: date, count: int) -> list[date]:
    """The last day of each of the `count` months before `today`'s month, oldest first."""
    ends = []
    end = today.replace(day=1) - timedelta(days=1)
    for _ in range(count):
        ends.append(end)
        end = end.replace(day=1) - timedelta(days=1)
    return ends[::-1]


class Bookkeeper:
    """Holds the latest books and runs recomputes, one at a time.

    `establish` does the work and raises `LedgerError` when the ledger cannot be trusted. Its
    result replaces the books only when it succeeds.

    A recompute asked for while one is running is not dropped: the running one goes round once
    more when it finishes, so a ruling made mid-recompute is in the books that follow it.
    """

    def __init__(self, establish: Establish, clock: Clock) -> None:
        self._establish = establish
        self._clock = clock
        self._lock = threading.Lock()
        self._wanted = threading.Event()
        self._state = Status(books=None, failure=None, recomputing=False)

    @property
    def status(self) -> Status:
        return self._state

    def recompute(self, *, wait: bool = False) -> None:
        """Recompute now, in this thread, or once more after the one already running.

        With `wait`, return only once books computed after this call are in place.
        """
        self._wanted.set()
        # Checked again after releasing, so a request made just as the last round ended
        # is taken up by whichever caller gets the lock next.
        while self._wanted.is_set() and self._lock.acquire(blocking=wait):
            try:
                while self._wanted.is_set():
                    self._wanted.clear()
                    self._once()
            finally:
                self._lock.release()
        if wait:
            # Another caller may have taken this request up and still be running it.
            with self._lock:
                pass

    def _once(self) -> None:
        self._state = Status(self._state.books, self._state.failure, recomputing=True)
        now = self._clock()
        try:
            books = self._establish(now)
        except LedgerError as error:
            self._fail(Failure(now, error.summary, error.disagreement, error.location))
        except Exception:
            _log.exception("recompute failed")
            self._fail(Failure(now, "The recompute stopped on an unexpected error."))
        else:
            self._state = Status(books, None, recomputing=False)

    def recompute_in_background(self) -> None:
        threading.Thread(target=self.recompute, name="hearth-recompute", daemon=True).start()

    def recompute_daily(self, at: time = DAILY_AT) -> None:
        """Recompute now, then every day at `at`, local time, in a background thread."""

        def run() -> None:
            while True:
                self.recompute()
                threading.Event().wait(seconds_until(at, datetime.now()))

        threading.Thread(target=run, name="hearth-daily-recompute", daemon=True).start()

    def _fail(self, failure: Failure) -> None:
        self._state = Status(self._state.books, failure, recomputing=False)


def seconds_until(at: time, now: datetime) -> float:
    next_run = datetime.combine(now.date(), at)
    if next_run <= now:
        next_run += timedelta(days=1)
    return (next_run - now).total_seconds()


def stored_prices(store: PriceStore, sources: Sequence[str] = PRICE_SOURCES) -> PriceLookup:
    """A price lookup over every source: the newest day wins, then the latest fetched."""

    def lookup(symbol: str, on_or_before: date) -> Quote | None:
        quotes = [store.latest(symbol, on_or_before, source) for source in sources]
        found = [quote for quote in quotes if quote is not None]
        return max(found, key=lambda quote: (quote.as_of, quote.fetched_at), default=None)

    return lookup


type Establish = Callable[[datetime], Books]

type Rulings = Callable[[sqlite3.Connection], Decide]
"""How the rulings recorded in a database decide review rows (``hearth.disclosure``)."""


def from_ledger(
    ledger: Path | None,
    database: Path,
    *,
    rulings: Rulings,
    sources: Sequence[str] = PRICE_SOURCES,
) -> Establish:
    """The recompute hearth runs: load the ledger file, lay the rulings over it, then value it
    at stored prices."""

    def establish(now: datetime) -> Books:
        if ledger is None:
            raise LedgerError("No ledger is set. Set HEARTH_LEDGER to the household's ledger.")
        entries = load_entries(ledger)
        conn = db.connect(database)
        try:
            prices = stored_prices(PriceStore(conn), sources)
            return compute(entries, prices, now=now, decide=rulings(conn))
        finally:
            conn.close()

    return establish
