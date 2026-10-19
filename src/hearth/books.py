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

The books are hearth's own (``hearth.ownbooks``), derived from what members connected and
decided, laid over a ledger of the household's own only when there is one to inherit. Each
recompute that succeeds writes them out to the books file, an output hearth never reads.

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
from beancount.core.account import is_valid as is_valid_account
from beancount.core.data import Balance, Directive, Open, Pad, Transaction, entry_sortkey
from beancount.core.interpolate import BalanceError
from beancount.ops import validation
from beancount.parser import options

from hearth import db, ownbooks
from hearth.clock import Clock
from hearth.config import Settings
from hearth.connections import Connections, RegisteredConnections, item_of
from hearth.months import Month
from hearth.plaid import booking, feed
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

REVIEW_EQUITY = "Equity:Review:"
"""Where a row waits for review (``hearth.review.pile.WAITING``): never what an account held."""

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
    awaiting: frozenset[str] = frozenset()
    """Accounts hearth opened for a connected bank account that the books hold nothing for
    yet (`awaiting`): whatever the bank says, their balance is not known here, never zero."""
    unanchored: frozenset[str] = frozenset()
    """Accounts hearth opened whose transactions the books hold, but not what the account held
    before the first of them (`unanchored`): their balance is not known here either."""

    @property
    def balance_unknown(self) -> frozenset[str]:
        """Every account whose balance the books do not know, whatever its bank says."""
        return self.awaiting | self.unanchored

    @property
    def month(self) -> Month:
        return Month.of(self.as_of)

    def behind(self, day: date) -> bool:
        """Whether data ending on `day` stops long enough before the recompute to say so."""
        return self.as_of - day > BEHIND_AFTER


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
    entries, _ = _load(path)
    return entries


def _load(path: Path) -> tuple[tuple[Directive, ...], dict[str, Any]]:
    entries, errors, options_map = _read(path)
    problems = [error for error in errors if not isinstance(error, BalanceError)]
    if problems:
        source = getattr(problems[0], "source", None) or {}
        where = f"{Path(source['filename']).name}, line {source['lineno']}" if source else None
        count = len(problems)
        summary = f"The ledger has {count} error{'s' if count > 1 else ''} hearth cannot read past"
        raise LedgerError(f"{summary}{f', the first at {where}' if where else ''}.")
    return tuple(entries), options_map


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
    # The household's own day, as every page names it, not the day it already is in UTC.
    today = now.astimezone().date()
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
        awaiting=awaiting(entries, today),
        unanchored=unanchored(entries, today),
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
    for account, entry, source in _account_entries(entries):
        if account in synced and item_of(source) == synced[account][0]:
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
    for account, entry, _ in _account_entries(entries, own):
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
    for account, entry, _ in _account_entries(entries, own):
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


def awaiting(entries: Sequence[Directive], today: date) -> frozenset[str]:
    """Accounts hearth opened from a connection that reach no month: no transaction and no
    balance assertion names them.

    Their bank has a balance; the books do not, until its history is booked, so any figure
    that includes one is not known yet rather than counting it as zero. An inherited ledger's
    own accounts keep beancount's meaning, where an account nothing has moved holds nothing.
    """
    return frozenset(_connected(entries) - account_reaches(entries, today).keys())


def unanchored(entries: Sequence[Directive], today: date) -> frozenset[str]:
    """Accounts hearth opened from a connection that have transactions but no anchor: nothing
    in the books says what the account held, only how it moved.

    A connection sends a window of history, not the account's life, so its rows added up are
    what moved over the window, not the balance; read as one, a card owing thousands could show
    as owing nothing. An account is anchored by a balance assertion on it, a `pad` into it, or a
    transaction balancing it against equity outside ``Equity:Review``, as an opening balance
    is: each says what it held. Until one does, its balance is not known, never the sum of its
    rows. An inherited ledger's own accounts keep beancount's meaning, as in `awaiting`.
    """
    anchored: set[str] = set()
    for entry in entries:
        if isinstance(entry, Balance | Pad):
            anchored.add(entry.account)
        elif isinstance(entry, Transaction) and any(
            root(p.account) == "Equity" and not p.account.startswith(REVIEW_EQUITY)
            for p in entry.postings
        ):
            anchored.update(p.account for p in entry.postings)
    return frozenset((_connected(entries) & account_reaches(entries, today).keys()) - anchored)


def _connected(entries: Sequence[Directive]) -> set[str]:
    """The balance-sheet accounts hearth opened, one for each account a member connected; not
    the categories it opens beside them."""
    return {
        e.account
        for e in ownbooks.own(entries)
        if isinstance(e, Open) and is_balance_sheet(e.account)
    }


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
) -> Iterator[tuple[str, Transaction | Balance, object]]:
    """Each balance-sheet account with each transaction or balance assertion that names it,
    and the `source` its side of the entry was booked from.

    That is the posting's own `source`, where it has one, else the entry's: a payment from a
    bank account to a card is one entry booked from two connections' rows, one for each side
    (``hearth.plaid.booking``).

    An account in `own`, which a connection vouches for, is not paired with an entry another
    connection booked: a card payment arriving through the checking account's feed is one
    transaction on the card, and says nothing of what the card's own feed has not delivered.
    """
    own = own or {}
    for entry in entries:
        found: dict[str, object] = {}
        source = (entry.meta or {}).get("source")
        if isinstance(entry, Transaction):
            for posting in entry.postings:
                found.setdefault(posting.account, (posting.meta or {}).get("source", source))
        elif isinstance(entry, Balance):
            found[entry.account] = source
        else:
            continue
        for account, booked_from in found.items():
            if not is_balance_sheet(account):
                continue
            booked = item_of(booked_from)
            if account in own and booked is not None and booked != own[account]:
                continue
            yield account, entry, booked_from


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


def _names(entry: Directive) -> list[str]:
    """Every account an entry hearth derived names."""
    if isinstance(entry, Transaction):
        return [posting.account for posting in entry.postings]
    return [entry.account] if isinstance(entry, Open) else []


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
        """Recompute in a thread of its own; the status says so from the moment it is asked
        for, so a page served before the thread starts does not read as up to date."""
        self._state = Status(self._state.books, self._state.failure, recomputing=True)
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


def from_household(
    settings: Settings,
    *,
    rulings: Rulings,
    sources: Sequence[str] = PRICE_SOURCES,
    connections: Connections | None = None,
) -> Establish:
    """The recompute hearth runs: derive hearth's own entries, over the ledger it inherits if
    there is one, lay the rulings over them and value them at stored prices, noting how current
    each connection that feeds them is; then write the books out for a person to open
    (``hearth.ownbooks``).

    `connections` are the Plaid connections registered in the app database unless given.
    """
    known = connections or RegisteredConnections(settings.database)

    def establish(now: datetime) -> Books:
        # Each sync is noted before the feed is read, never after: a sync that finishes in
        # between then vouches for rows the books hold, not for rows they do not hold yet.
        synced = synced_through(known)
        entries = household_entries(settings)
        conn = db.connect(settings.database)
        try:
            prices = stored_prices(PriceStore(conn), sources)
            books = compute(entries, prices, now=now, decide=rulings(conn), synced=synced)
        finally:
            conn.close()
        written = ownbooks.render(ownbooks.own(books.entries), inherits=settings.ledger)
        ownbooks.write(settings.books_file, written, now=now)
        return books

    return establish


def household_entries(settings: Settings) -> tuple[Directive, ...]:
    """The household's entries before any ruling: hearth's own over the inherited ledger.

    Checked as beancount checks a file it loads, so the books written out load as cleanly as
    they were computed; hearth's entries that would not are refused, never served.
    """
    if settings.ledger is not None:
        inherited, options_map = _load(settings.ledger)
    else:
        inherited, options_map = (), options.OPTIONS_DEFAULTS.copy()
    opened = {e.account for e in inherited if isinstance(e, Open)}
    since = min((e.date for e in inherited), default=None)
    conn = db.connect(settings.database)
    try:
        with feed.Feed.open(settings.feed_database) as fed:
            own = ownbooks.derive(conn, fed, opened=opened, since=since)
    except booking.Refused as refused:
        raise LedgerError(str(refused)) from refused
    finally:
        conn.close()
    entries = tuple(sorted((*inherited, *own), key=entry_sortkey))
    if own:
        # Beancount checks a name's spelling only as it parses a file, which these never were.
        misnamed = sorted({n for entry in own for n in _names(entry) if not is_valid_account(n)})
        problems = misnamed or validation.validate(list(entries), options_map)  # type: ignore[no-untyped-call]
        if problems:
            count = len(problems)
            raise LedgerError(
                f"Hearth's own books have {count} error{'s' if count > 1 else ''}"
                " it will not serve figures past."
            )
    return entries
