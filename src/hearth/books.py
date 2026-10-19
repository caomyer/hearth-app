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
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from beancount import loader
from beancount.core.data import Balance, Directive, Transaction
from beancount.core.interpolate import BalanceError

from hearth import db
from hearth.clock import Clock
from hearth.connections import Connections, RegisteredConnections, item_of
from hearth.months import Month
from hearth.reports.balances import AssertionCheck, check_balance_assertions
from hearth.reports.common import is_balance_sheet, root
from hearth.reports.coverage import AccountCoverage, account_coverage
from hearth.review.decision import Decision
from hearth.review.overlay import reviewed
from hearth.review.pile import PileItem, read_pile
from hearth.valuation import institution
from hearth.valuation.holdings import holdings_at
from hearth.valuation.networth import NetWorth, PriceLookup, value_holdings
from hearth.valuation.pricestore import PriceStore
from hearth.valuation.quotes import Quote

_log = logging.getLogger(__name__)

CURRENCY = "USD"
HISTORY_MONTHS = 12
DAILY_AT = time(6, 0)

BEHIND_AFTER = timedelta(days=7)
"""A ledger a few days short is a feed that has not caught up; a week short is worth saying."""

PRICE_SOURCES = ("eodhd", institution.SOURCE, "hand")
"""Where stored prices come from: the market data source, the price each institution reports
for its own holdings through Plaid, and prices a member typed in.

Each is stored under its own source, so none ever passes as another's.
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
    account_ends: dict[str, date] = field(default_factory=dict)
    """Each balance-sheet account's last day in the ledger, by the rule of `account_ends`."""
    account_reaches: dict[str, Reach] = field(default_factory=dict)
    """How far each balance-sheet account reaches in the ledger, by month (`account_reaches`)."""
    flowing: frozenset[str] = frozenset()
    """Balance-sheet accounts that can move a spending or income figure (`flowing`)."""
    synced: dict[str, date] = field(default_factory=dict)
    """Each ledger account its connection vouches for, with the day that connection last synced
    successfully, as it stood at this recompute (`vouched`). It says the account is still
    reporting, which the ledger cannot: `figures.covers` reads it."""

    @property
    def month(self) -> Month:
        return Month.of(self.as_of)

    def behind(self, day: date) -> bool:
        """Whether data ending on `day` stops long enough before the recompute to say so."""
        return self.computed_at.date() - day > BEHIND_AFTER


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
    entries, errors, _ = _read(path)
    problems = [error for error in errors if not isinstance(error, BalanceError)]
    if problems:
        source = getattr(problems[0], "source", None) or {}
        where = f"{Path(source['filename']).name}, line {source['lineno']}" if source else None
        count = len(problems)
        summary = f"The ledger has {count} error{'s' if count > 1 else ''} hearth cannot read past"
        raise LedgerError(f"{summary}{f', the first at {where}' if where else ''}.")
    return tuple(entries)


def _read(path: Path) -> tuple[list[Directive], list[Any], Any]:
    """Load the ledger without touching the directory it lives in.

    `loader.load_file` goes through beancount's pickle cache, which writes a
    `.<name>.picklecache` beside the ledger whenever a load takes over a second, and deletes
    one it finds stale. The ledger and its directory belong to the household, and hearth only
    reads them, so the cache is bypassed for the loader's own uncached path.
    """
    return loader._load([(str(path), True)], None, None, None)


type Decide = Callable[[Sequence[PileItem]], Mapping[str, Decision]]
"""Every review row's decision, as the rulings members may make on it decide it."""


def compute(
    ledger: Sequence[Directive],
    lookup: PriceLookup,
    *,
    now: datetime,
    decide: Decide,
    synced: Mapping[str, tuple[str, date]] | None = None,
) -> Books:
    """Lay the rulings over the ledger, check it, and value the household today and at each
    recent month end. `synced` is each fed ledger account's connection and its last successful
    sync (`synced_through`).

    Raises `LedgerError` when a balance assertion disagrees with the postings, because every
    figure downstream would inherit the error.
    """
    pile = tuple(read_pile(e for e in ledger if _set_aside(e)))
    entries = tuple(reviewed(ledger, decide(pile)))
    for check in check_balance_assertions(entries):
        if not check.agrees:
            raise LedgerError(f"A balance check failed on {check.date:%b} {check.date.day}.", check)
    today = now.date()
    fed = synced or {}
    vouches = vouched(entries, fed, today)
    own = {account: fed[account][0] for account in vouches}

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
        account_ends=account_ends(entries, today, own),
        account_reaches=account_reaches(entries, today, own),
        flowing=flowing(entries),
        synced=vouches,
        pile=pile,
    )


def synced_through(connections: Connections) -> dict[str, tuple[str, date]]:
    """Each ledger account a connection feeds, with that connection's Plaid Item and last
    successful sync.

    An account fed by a connection that has never synced is left out, as nothing has come from
    it yet. One ledger account is fed by one connection at most (``docs/connections.md``); were
    there ever two, the fresher one is taken.
    """
    through: dict[str, tuple[str, date]] = {}
    for connection in connections.all():
        if connection.item_id is None or connection.data_until is None:
            continue
        for account in connection.ledger_accounts:
            if account not in through or connection.data_until > through[account][1]:
                through[account] = (connection.item_id, connection.data_until)
    return through


def vouched(
    entries: Sequence[Directive], synced: Mapping[str, tuple[str, date]], today: date
) -> dict[str, date]:
    """Each fed account its connection's last sync vouches for, known through that sync day,
    or `today` if earlier.

    A sync vouches for an account only as far as the books hold that connection's data: its
    newest entry on the account booked from the connection (`connections.plaid_source`) must
    be on or before the sync. An account with no such entry is left out, so its connection
    says nothing about it and the ledger alone judges it, as if it had no connection.
    """
    booked: dict[str, date] = {}
    for account, entry in _account_entries(entries):
        if account in synced and item_of((entry.meta or {}).get("source")) == synced[account][0]:
            booked[account] = max(entry.date, booked.get(account, date.min))
    return {
        account: min(day, today)
        for account, (_, day) in synced.items()
        if account in booked and day >= booked[account]
    }


def account_ends(
    entries: Sequence[Directive], today: date, own: Mapping[str, str] | None = None
) -> dict[str, date]:
    """The last day the ledger knows about each balance-sheet account, or `today` if earlier.

    That is the account's newest transaction, or the day before its newest balance assertion,
    which states the balance at the start of its day and so vouches for everything before it:
    a statement import closes each cycle that way. The ledger knows nothing about the days
    after: a statement import stops at its period's end, and a bank feed stops when it last
    ran. Accounts with neither are left out.

    `own` is each account a connection vouches for, with that connection's Item: such an
    account counts only entries no other connection booked (`_account_entries`).
    """
    ends: dict[str, date] = {}
    for account, entry in _account_entries(entries, own):
        day = min(_known_through(entry), today)
        if day > ends.get(account, date.min):
            ends[account] = day
    return ends


@dataclass(frozen=True, slots=True)
class Reach:
    """The newest month an account has a transaction in, and a balance assertion in.

    Months only, never days: which months count as complete must not tell anyone the day an
    account's money last moved.
    """

    transacted: Month | None = None
    asserted: Month | None = None

    def holds(self, month: Month, current: Month) -> bool:
        """Whether the account's data is whole for `month`, `current` being this month.

        A past month is whole once a transaction or a balance assertion is dated in a later
        month: a statement closing on Sep 30 is imported with an Oct 1 assertion, which makes
        September whole. This month is whole only with a transaction in it, as an assertion
        dated in it says nothing about the rest of the month.
        """
        if month == current:
            return self.transacted == current
        return any(
            newest is not None and newest > month for newest in (self.transacted, self.asserted)
        )


def account_reaches(
    entries: Sequence[Directive], today: date, own: Mapping[str, str] | None = None
) -> dict[str, Reach]:
    """Each balance-sheet account's `Reach`, counting an entry dated after `today` as today's,
    and, for an account in `own`, only entries no other connection booked (`account_ends`)."""
    reaches: dict[str, Reach] = {}
    for account, entry in _account_entries(entries, own):
        month = Month.of(min(entry.date, today))
        reach = reaches.get(account, Reach())
        if isinstance(entry, Transaction) and (
            reach.transacted is None or month > reach.transacted
        ):
            reach = Reach(month, reach.asserted)
        elif isinstance(entry, Balance) and (reach.asserted is None or month > reach.asserted):
            reach = Reach(reach.transacted, month)
        reaches[account] = reach
    return reaches


def flowing(entries: Sequence[Directive]) -> frozenset[str]:
    """Balance-sheet accounts with a posting beside an Expenses or Income one anywhere.

    Only they can move a spending or income figure; an account that only ever takes transfers,
    such as a retirement account, cannot.
    """
    return frozenset(
        posting.account
        for entry in entries
        if isinstance(entry, Transaction)
        and any(root(p.account) in ("Expenses", "Income") for p in entry.postings)
        for posting in entry.postings
        if is_balance_sheet(posting.account)
    )


def _account_entries(
    entries: Sequence[Directive], own: Mapping[str, str] | None = None
) -> Iterator[tuple[str, Transaction | Balance]]:
    """Each balance-sheet account with each transaction or balance assertion that names it.

    An account in `own`, which a connection vouches for, is not paired with an entry another
    connection booked: a card payment arriving through the checking account's feed is one
    transaction on the card, and says nothing of what the card's own feed has not delivered.
    """
    own = own or {}
    for entry in entries:
        if isinstance(entry, Transaction):
            accounts = {posting.account for posting in entry.postings}
        elif isinstance(entry, Balance):
            accounts = {entry.account}
        else:
            continue
        booked = item_of((entry.meta or {}).get("source"))
        for account in accounts:
            if not is_balance_sheet(account):
                continue
            if account in own and booked is not None and booked != own[account]:
                continue
            yield account, entry


def _known_through(entry: Transaction | Balance) -> date:
    return entry.date if isinstance(entry, Transaction) else entry.date - timedelta(days=1)


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
    connections: Connections | None = None,
) -> Establish:
    """The recompute hearth runs: load the ledger file, lay the rulings over it, then value it
    at stored prices, noting how current each connection that feeds it is.

    `connections` are the Plaid connections registered in `database` unless given.
    """
    known = connections or RegisteredConnections(database)

    def establish(now: datetime) -> Books:
        if ledger is None:
            raise LedgerError("No ledger is set. Set HEARTH_LEDGER to the household's ledger.")
        entries = load_entries(ledger)
        conn = db.connect(database)
        try:
            prices = stored_prices(PriceStore(conn), sources)
            return compute(
                entries, prices, now=now, decide=rulings(conn), synced=synced_through(known)
            )
        finally:
            conn.close()

    return establish
