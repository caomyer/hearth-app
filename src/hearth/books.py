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
decided, laid over a ledger of the household's own only when there is one: the base it imported
and froze (``hearth.base``), or one it inherits where it is (`inherited`). Each recompute that
succeeds writes them out to the books file, an output hearth never reads.

Nothing here decides who may see what; pages reach the books only through
``hearth.disclosure``.
"""

import logging
import os
import sqlite3
import threading
from bisect import bisect_right
from collections.abc import Callable, Collection, Hashable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from beancount import loader
from beancount.core.account import is_valid as is_valid_account
from beancount.core.data import (
    Balance,
    Close,
    Commodity,
    Custom,
    Directive,
    Open,
    Pad,
    Transaction,
    entry_sortkey,
)
from beancount.core.interpolate import BalanceError
from beancount.ops import validation
from beancount.parser import booking as beancount_booking
from beancount.parser import options

from hearth import base, carrying, categories, db, ownbooks
from hearth.base import Base, BaseError
from hearth.carrying import Carry
from hearth.categories import Kind
from hearth.clock import Clock
from hearth.config import Settings
from hearth.connections import Connections, RegisteredConnections, item_of
from hearth.months import Month
from hearth.plaid import booking, feed, opening
from hearth.plaid.opening import Unknown
from hearth.proposals import MayLearn, Opens, learn_nothing
from hearth.reports.balances import AssertionCheck, check_balance_assertions
from hearth.reports.common import is_balance_sheet, root
from hearth.reports.coverage import AccountCoverage, account_coverage
from hearth.review.decision import Decision, guarded, postable
from hearth.review.overlay import reviewed
from hearth.review.pile import UNRESOLVED, PileItem, read_pile
from hearth.review.rulings import RulingStore
from hearth.valuation import institution
from hearth.valuation.holdings import holdings_at
from hearth.valuation.networth import NetWorth, PriceLookup, value_holdings
from hearth.valuation.pricestore import PriceStore
from hearth.valuation.quotes import Quote

_log = logging.getLogger(__name__)

CURRENCY = "USD"
HISTORY_MONTHS = 12
DAILY_AT = time(6, 0)
WATCH_EVERY = 5.0
"""How often, in seconds, a serving hearth asks whether what its books are computed from has
changed without it, as when `hearth plaid sync` is run by hand in another process."""

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
    """Accounts hearth opened for a connected bank account whose history the books hold none
    of yet (`awaiting`): nothing says yet what they spend, nor that they spent nothing."""
    unanchored: frozenset[str] = frozenset()
    """Accounts hearth opened that the books hold no balance for (`unanchored`): neither what
    they held before their history nor what their bank says they hold. Their balance is not
    known here, never the sum of their transactions, never zero."""
    unknown: dict[str, Unknown] = field(default_factory=dict)
    """Accounts hearth opened whose opening balance could not be derived from what their bank
    reported, and accounts it carries on from the base whose bank's balance cannot prove
    them, with why (``hearth.plaid.opening``). Each is `unanchored`."""
    unseen: frozenset[str] = frozenset()
    """Accounts the household holds that hearth does not see, whose balance it is never told:
    each a member named (`unseen`), of which only what moved between it and connected
    accounts is in the books, and each claimed that nothing opens (``ownbooks.Derived.unfed``),
    of which nothing is. None is `awaiting`, a named one is `unanchored`, and no valuation
    holds a figure for any of them."""
    invested: frozenset[str] = frozenset()
    """Accounts hearth opened for a brokerage account, whatever it holds: Plaid sends them no
    history, so none is `awaiting`, and each is an investment account whether or not its
    holdings are in yet."""
    positioned: dict[str, date] = field(default_factory=dict)
    """Each account that holds what its brokerage reported, with the day it reported it
    (`positioned`). Nothing says what it held on any day before, so no month before that day
    is whole for it (``figures.covers``)."""
    carried: dict[str, date] = field(default_factory=dict)
    """Each account of the imported base hearth carries on, with its cut-over: its history
    before that day is the base's, and hearth books its bank's rows from then on
    (``hearth.carrying``). Each is an account whose balance its bank reports."""
    reconciling: frozenset[str] = frozenset()
    """Carried accounts whose bank's balance agrees with the books only by an entry that
    reconciles the base with the bank, which nobody has confirmed yet (`reconciling`): wherever
    such an account's balance would be shown as proved by its bank, it says so instead."""
    unseen_between: dict[str, tuple[date, date]] = field(default_factory=dict)
    """Each carried account with the days from its cut-over that neither the base nor its
    bank's history holds (``carrying.Carry.unseen``), the last capped at the books' own day:
    no month among them is whole (``figures.covers``)."""
    held_back: dict[str, tuple[date, date]] = field(default_factory=dict)
    """Each account of the base matched to a connection that is not carried on yet, as the
    base holds something on it dated after today: the last day the base knows of it today,
    after which no month is whole (``figures.covers``), and the latest day the base holds
    anything on it, which holds it back. Each is its bank's in Review all the same."""
    begun: frozenset[str] = frozenset()
    """Carried accounts with a ruling on one of their review rows (`begun`): from then on the
    match, and the base, are fixed."""
    kinds: Mapping[str, Kind] = field(default_factory=dict)
    """Each income and expense account the books name, with what kind of money it is: as a
    person said, or as its name says (``hearth.categories``)."""
    prices: PriceLookup = field(default=lambda symbol, day: None)
    """The prices the books were valued at, for valuing them on any other day: a statement's
    start and end (``hearth.reports.statements``). Held in memory, as the recompute read them,
    so a page never reads the price table itself."""
    price_sources: tuple[str, ...] = PRICE_SOURCES
    """The sources the stored prices it was valued at were taken from (`stored_prices`), so
    that a price's history is read from exactly those."""

    def kind(self, account: str) -> Kind:
        """What kind of money an income or expense account is (``hearth.categories``)."""
        return self.kinds.get(account) or categories.guess(account)

    def spends(self, account: str) -> bool:
        """Whether money booked to `account` is spending: an expense account of any kind but
        "not spending", such as a retirement contribution booked as an expense, which is money
        kept somewhere else. What it leaves out of spending is in what was left over."""
        return root(account) == "Expenses" and self.kind(account) is not Kind.NOT_SPENDING

    @property
    def balance_unknown(self) -> frozenset[str]:
        """Every account whose balance the books do not know, whatever its bank says."""
        return self.unanchored

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
    reported_by: str | None = None
    """The bank whose reported balance `disagreement` asserts, when a bank's it is."""


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
        *,
        reported_by: str | None = None,
    ) -> None:
        super().__init__(summary)
        self.summary = summary
        self.disagreement = disagreement
        self.location = location
        self.reported_by = reported_by


@dataclass(frozen=True, slots=True)
class Loaded:
    """A ledger as hearth reads it: its entries, beancount's options, and every file it read."""

    entries: tuple[Directive, ...]
    options: dict[str, Any]
    files: tuple[Path, ...]


def load_entries(ledger: Path | Base) -> tuple[Directive, ...]:
    """The ledger at `ledger`, or the base, refusing one beancount reports errors in
    (`load_ledger`)."""
    return load_ledger(ledger).entries


def load_ledger(ledger: Path | Base) -> Loaded:
    """The ledger at `ledger`, or the base hearth imported (``hearth.base``), refusing one
    beancount reports errors in. Every ledger hearth reads, a base, an inherited ledger or one
    it compares with, is read here and nowhere else.

    Beancount's own plugins run; any other plugin a ledger names is kept in its text and never
    run, nor imported (`runs`). Custom directives are kept in the text and left out of the
    entries. A base is read only once its files are as it was imported, and only if it reads
    no file outside itself.

    A failed balance assertion is left to `compute`, which explains it to the account's
    owners alone. Beancount's own messages name accounts and amounts, so no other error's
    message is ever passed on: only where it is, which is enough to find it.
    """
    frozen = ledger if isinstance(ledger, Base) else None
    if frozen is not None:
        try:
            frozen.verify()
        except BaseError as error:
            raise LedgerError(str(error)) from error
    path = frozen.root_path if frozen is not None else ledger
    assert isinstance(path, Path)  # noqa: S101 - a Base gives its root, a Path is one
    entries, errors, options_map = _read(path)
    problems = [error for error in errors if not isinstance(error, BalanceError)]
    if problems:
        source = getattr(problems[0], "source", None) or {}
        # Beancount files an error of loading, such as an include that matches nothing, at
        # "<load>", which is no place in the ledger.
        placed = source and not str(source["filename"]).startswith("<")
        where = f"{Path(source['filename']).name}, line {source['lineno']}" if placed else None
        count = len(problems)
        summary = f"The ledger has {count} error{'s' if count > 1 else ''} hearth cannot read past"
        raise LedgerError(f"{summary}{f', the first at {where}' if where else ''}.")
    files = tuple(Path(name) for name in options_map["include"])
    if frozen is not None and set(files) != {Path(os.path.normpath(p)) for p in frozen.paths()}:
        outside = sorted(str(f) for f in files if not f.is_relative_to(frozen.directory))
        raise LedgerError(
            f"The imported ledger reads {outside[0]}, outside its frozen copy."
            if outside
            else "The imported ledger does not read the files it was imported with."
        )
    kept = tuple(e for e in entries if not isinstance(e, Custom))
    return Loaded(kept, options_map, files)


BUILT_IN = "beancount."
"""The package beancount's own plugins are in: `beancount.plugins` and `beancount.ops`."""

SKIPPED = frozenset({"beancount.ops.documents"})
"""Beancount's own plugins hearth never runs: `documents` looks for files a ledger names
outside its text, which a base does not copy and hearth never opens."""


def runs(plugin: str) -> bool:
    """Whether hearth runs a plugin a ledger names: only beancount's own.

    Any other runs code hearth did not ship, which could read, write or send anything, and
    would make the books depend on what is installed beside hearth on the day; a ledger's
    `insert_pythonpath` would even run code from the ledger's own folder. It is kept in the
    ledger's text, inert, and ``hearth import-ledger`` says so, with where it is.
    """
    return plugin.startswith(BUILT_IN) and plugin not in SKIPPED


def _read(path: Path) -> tuple[list[Directive], list[Any], Any]:
    """Load the ledger without touching the directory it lives in, running only beancount's
    own plugins (`runs`).

    This is beancount's own uncached load, step for step, with the plugins chosen here.
    `loader.load_file` goes through beancount's pickle cache, which writes a
    `.<name>.picklecache` beside the ledger whenever a load takes over a second, and deletes
    one it finds stale. The ledger and its directory belong to the household, and hearth only
    reads them, so the cache is never used. Nor is the ledger's `insert_pythonpath`: no code
    from beside a ledger is ever imported.
    """
    entries, errors, options_map = loader._parse_recursive([(str(path), True)], None, None)
    entries.sort(key=entry_sortkey)
    entries, booking_errors = beancount_booking.book(entries, options_map)  # type: ignore[no-untyped-call]
    errors.extend(booking_errors)
    chosen = [(name, config) for name, config in options_map["plugin"] if runs(name)]
    if options_map["plugin_processing_mode"] == "default":
        chosen = [
            *(p for p in loader.PLUGINS_PRE if runs(p[0])),
            *chosen,
            *loader.PLUGINS_AUTO,
            *loader.PLUGINS_POST,
        ]
    # Every plugin is named here; beancount adds none of its own in raw mode.
    plugged = {**options_map, "plugin": chosen, "plugin_processing_mode": "raw"}
    entries, errors = loader.run_transformations(entries, errors, plugged, None)
    errors.extend(validation.validate(entries, options_map))  # type: ignore[no-untyped-call]
    return entries, errors, options_map


type Decide = Callable[[Sequence[PileItem]], Mapping[str, Decision]]
"""Every review row's decision, as the rulings members may make on it decide it."""


def compute(
    ledger: Sequence[Directive],
    lookup: PriceLookup,
    *,
    now: datetime,
    decide: Decide,
    synced: Mapping[str, tuple[str, date]] | None = None,
    unknown: Mapping[str, Unknown] | None = None,
    invested: frozenset[str] = frozenset(),
    unfed: frozenset[str] = frozenset(),
    carried: Mapping[str, Carry] | None = None,
    unproved: Mapping[str, Unknown] | None = None,
    held_back: Mapping[str, tuple[date, date]] | None = None,
    kinds: Mapping[str, Kind] | None = None,
    price_sources: Sequence[str] = PRICE_SOURCES,
) -> Books:
    """Lay the rulings over the ledger, check it, and value the household today and at each
    recent month end. `synced` is each fed ledger account's connection and its last successful
    sync (`synced_through`); `unknown`, each account whose opening balance could not be
    derived, with why; `invested`, each account a brokerage account feeds; `unfed`, each the
    household holds that nothing opens; `carried`, each account of the base hearth carries on;
    `unproved`, each of those its bank's balance cannot prove yet, with why; `kinds`, each
    income or expense account a person said the kind of (``hearth.categories``); and
    `price_sources`, the sources `lookup` reads stored prices from.

    Raises `LedgerError` when a balance assertion disagrees with the postings, because every
    figure downstream would inherit the error.
    """
    carried = carried or {}
    pile = tuple(read_pile(e for e in ledger if _set_aside(e)))
    decisions = decide(pile)
    # No ruling on record may put a posting on a carried account its bank never sent.
    # Nor may one lay a row on an account the books do not hold open for it on its day.
    opens = _opens(ledger)
    decided = {
        item.review_id: postable(item, guarded(item, decisions[item.review_id], carried), opens)
        for item in pile
        if item.review_id in decisions
    }
    entries = tuple(reviewed(ledger, decided))
    for check in check_balance_assertions(entries):
        if not check.agrees:
            raise _disagreement(entries, check)
    # The household's own day, as every page names it, not the day it already is in UTC.
    today = now.astimezone().date()
    # What each account held before its history, and holds now by its bank's word, says
    # nothing of which days the history covers.
    history = tuple(e for e in entries if not opening.anchors(e))
    fed = synced or {}
    held = {a: c.held_through for a, c in carried.items() if c.held_through is not None}
    vouches = vouched(history, fed, today, held)
    own = {account: fed[account][0] for account in vouches}

    hidden = unseen(entries) | unfed

    def worth(day: date) -> NetWorth:
        # What moved into an account hearth does not see is not what it holds, so it is
        # never valued as though it were: its balance is left out, not summed from its rows.
        held = [h for h in holdings_at(entries, day) if h.account not in hidden]
        return value_holdings(held, lookup, as_of=day, currency=CURRENCY)

    return Books(
        computed_at=now,
        as_of=today,
        currency=CURRENCY,
        entries=entries,
        today=worth(today),
        month_ends=tuple(worth(day) for day in month_ends(today, HISTORY_MONTHS)),
        coverage=tuple(account_coverage(history, as_of=today)),
        account_ends=account_ends(history, today, own),
        account_reaches=account_reaches(history, today, own),
        flowing=flowing(entries),
        synced=vouches,
        # Plaid sends an investment account no transactions at all, so waiting on its
        # history would leave every month's spending incomplete for good.
        awaiting=awaiting(history, today) - invested,
        # A carried account its bank cannot prove yet is not known, as a connected account in
        # the same state is: never the base and the rows since, unproved, shown as known.
        unanchored=unanchored(entries) | set(unproved or {}),
        unknown={**(unknown or {}), **(unproved or {})},
        unseen=hidden,
        invested=invested,
        positioned=positioned(entries),
        pile=pile,
        carried={account: c.cut_over for account, c in carried.items()},
        held_back=dict(held_back or {}),
        unseen_between={
            account: (c.unseen[0], min(c.unseen[1], today))
            for account, c in carried.items()
            if c.unseen is not None
        },
        reconciling=reconciling(entries),
        begun=begun(ledger, carried, {r for r, d in decided.items() if d.ruling is not None}),
        kinds=categories.kinds(_categories(entries), kinds or {}),
        prices=lookup,
        price_sources=tuple(price_sources),
    )


def _categories(entries: Sequence[Directive]) -> set[str]:
    """Every income and expense account `entries` open or post to."""
    found = {e.account for e in entries if isinstance(e, Open)}
    found |= {p.account for e in entries if isinstance(e, Transaction) for p in e.postings}
    return {a for a in found if categories.root(a) is not None}


def begun_in(settings: Settings, today: date | None = None) -> frozenset[str]:
    """The carried accounts on which carrying has begun (`begun`), read from the household's
    entries before any ruling and every ruling recorded, whoever made it, for the commands
    that must not change what the books rest on (``hearth.matching``, ``hearth.ledger_import``).
    Raises `LedgerError` when the household's entries cannot be derived, as then nobody can
    tell."""
    household = household_entries(settings, today)
    if not household.carried:
        return frozenset()
    try:
        pile = read_pile(e for e in household.entries if _set_aside(e))
    except ValueError as error:
        raise LedgerError("A review row in the books cannot be ruled on.") from error
    conn = db.connect(settings.database)
    try:
        ruled = {c.review_id for c in RulingStore(conn).covering([i.review_id for i in pile])}
    finally:
        conn.close()
    return begun(household.entries, household.carried, ruled)


def reconciling(entries: Sequence[Directive]) -> frozenset[str]:
    """Carried accounts whose reconciling entry (``opening.Carrying``) is still unconfirmed:
    flagged, as the import wrote it, once the rulings are laid over the books."""
    return frozenset(
        p.account
        for e in entries
        if isinstance(e, Transaction) and opening.reconciling(e) and e.flag == "!"
        for p in e.postings
        if is_balance_sheet(p.account)
    )


def begun(
    entries: Sequence[Directive], carried: Collection[str], ruled: Collection[str]
) -> frozenset[str]:
    """Carried accounts on which carrying has begun: a member has made a ruling on a review row
    carried on the account, its reconciling entry included. `entries` are the books before any
    ruling, and `ruled` the review ids some ruling covers.

    A booked row alone does not begin it: every entry hearth books is derived afresh on each
    recompute, so a wrong match put right before anyone has ruled loses nothing, and its rows
    move with it. A ruling is what a later change would orphan, so from the first one the
    match and the base it rests on are fixed (``hearth.matching``, ``hearth.ledger_import``)."""
    found: set[str] = set()
    for entry in ownbooks.own(entries):
        if not isinstance(entry, Transaction):
            continue
        if (entry.meta or {}).get("review-id") in ruled:
            found |= {p.account for p in entry.postings} & set(carried)
    return frozenset(found)


def _disagreement(entries: Sequence[Directive], check: AssertionCheck) -> LedgerError:
    """The failed check, saying whose balance it was: a statement's in the ledger, or one a
    bank reported, which hearth derived an opening balance from (``hearth.plaid.opening``)."""
    asserted = next(
        (
            e
            for e in entries
            if isinstance(e, Balance) and e.account == check.account and e.date == check.date
        ),
        None,
    )
    if asserted is not None and opening.anchors(asserted):
        bank = str(asserted.meta["reported-by"])
        day = check.date - timedelta(days=1)
        what = "holdings" if opening.held(asserted) else "balance"
        verb = "are" if opening.held(asserted) else "is"
        return LedgerError(
            f"The {what} {bank} reported for {day:%b} {day.day} {verb} not what its"
            " transactions add up to.",
            check,
            reported_by=bank,
        )
    return LedgerError(f"A balance check failed on {check.date:%b} {check.date.day}.", check)


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


def frozen_through(base: carrying.Held, today: date) -> dict[str, date]:
    """Each account a frozen base opens, with the last day the base knows of it on the
    household's own day `today`: the day before its cut-over (``carrying.cut_over``), the one
    definition of where the base's own record of an account stops, or, where the base runs
    ahead of today, what it holds up to today (``carrying.known_through``)."""
    return {account: carrying.known_through(base, account, today) for account in base.opened}


def vouched_over_base(
    synced: Mapping[str, tuple[str, date]],
    frozen: Mapping[str, date],
    carried: Collection[str],
) -> dict[str, tuple[str, date]]:
    """`synced` (`synced_through`), with each account a frozen base opens that hearth does
    not carry on vouched for no further than the base's own last day on it
    (`frozen_through`), never through a later sync: hearth books nothing into such an account,
    a brokerage say (``carrying.carries``), so nothing after the base was frozen is in the
    books, however current its connection is. A month the base does not reach then reads as
    incomplete, as it is, rather than as quiet.

    An account hearth carries on is vouched for through its connection's last successful sync
    like any it keeps itself, as hearth books every row of it from its cut-over on; the base's
    entries that hold the bank's own rows count as that connection's (`vouched`). A live
    ledger inherited at `HEARTH_LEDGER` is not capped: an import outside hearth may still be
    adding to it."""
    return {
        account: (
            item,
            min(day, frozen[account]) if account in frozen and account not in carried else day,
        )
        for account, (item, day) in synced.items()
    }


def vouched(
    entries: Sequence[Directive],
    synced: Mapping[str, tuple[str, date]],
    today: date,
    held: Mapping[str, date] | None = None,
) -> dict[str, date]:
    """Each fed account its connection's last sync vouches for, known through that sync day,
    or `today` if earlier.

    A sync vouches for an account only as far as the books hold that connection's data: its
    newest entry on the account booked from the connection (`connections.plaid_source`) must
    be on or before the sync. An account with no such entry is left out, so its connection
    says nothing about it and the ledger alone judges it, as if it had no connection.

    `held` is each carried account with the newest day among its bank's rows that the base
    holds already (``carrying.Carry.held_through``): the base's entries for them are that
    connection's data in the books too, so a quiet carried account is vouched for by the
    connection that sent what the base holds.
    """
    booked: dict[str, date] = {
        account: day for account, day in (held or {}).items() if account in synced
    }
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
    """Accounts hearth opened from a connection whose history the books hold none of: in
    `entries`, the books' history, no transaction and no balance assertion names them.

    Nothing says yet what one of them spends or earns, so a figure of spending or income is
    judged as though it might (``figures.flows_cover``), and its months read as not known.
    What it holds may be known all the same, from its bank (`unanchored`). An inherited
    ledger's own accounts keep beancount's meaning, where an account nothing has moved holds
    nothing.

    An account hearth does not see (`unseen`) is never awaiting: no history of its own will
    ever arrive, so waiting on it would hold every spending figure hostage for good, and what
    it spends is not in the household's figures to wait on.
    """
    reached = account_reaches(entries, today).keys()
    return frozenset(_connected(entries) - reached - unseen(entries))


def unanchored(entries: Sequence[Directive]) -> frozenset[str]:
    """Accounts hearth opened from a connection with no anchor: nothing in the books says what
    the account held, at most how it moved.

    A connection sends a window of history, not the account's life, so its rows added up are
    what moved over the window, not the balance; read as one, a card owing thousands could show
    as owing nothing. An account is anchored by a balance assertion on it, a `pad` into it, or a
    transaction balancing it against equity outside ``Equity:Review``, as an opening balance
    is: each says what it held. Hearth anchors each from the balance its bank reports
    (``hearth.plaid.opening``); until one does, its balance is not known, never the sum of its
    rows, nor zero. An inherited ledger's own accounts keep beancount's meaning, as in
    `awaiting`. That includes every account a member named (`unseen`), which nothing anchors.
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
    return frozenset(_connected(entries) - anchored)


def positioned(entries: Sequence[Directive]) -> dict[str, date]:
    """Each account anchored at what its brokerage reported it holds, with the day it did.

    Plaid sends no trades, so that day is the first the account's holdings are known on: an
    earlier day would value it as holding nothing, or its cash alone, which nobody said.
    """
    return {
        e.account: datetime.fromisoformat(str(e.meta["reported-at"])).astimezone().date()
        for e in entries
        if isinstance(e, Balance) and opening.held(e)
    }


def unseen(entries: Sequence[Directive]) -> frozenset[str]:
    """Accounts hearth opened for an account a member named, held by name alone
    (``ownbooks.moves_only``): savings at a bank nobody connected, a car loan.

    Hearth sees only the money that moves between such an account and the connected ones, so
    its rows are not its history and their sum is not its balance: a savings account that has
    taken twenty transfers holds them, its interest, and whatever was spent from it, none of
    which reaches hearth. It is `unanchored`, its balance not known, and always will be, so a
    total that includes it leaves it out and says so rather than read "not known" for good.
    """
    return frozenset(
        e.account for e in ownbooks.own(entries) if isinstance(e, Open) and ownbooks.moves_only(e)
    )


def _connected(entries: Sequence[Directive]) -> set[str]:
    """The balance-sheet accounts hearth opened, one for each account a member connected or
    named; not the categories it opens beside them."""
    return {
        e.account
        for e in ownbooks.own(entries)
        if isinstance(e, Open) and is_balance_sheet(e.account)
    }


def flowing(entries: Sequence[Directive]) -> frozenset[str]:
    """Balance-sheet accounts with a posting beside an Expenses or Income one anywhere, or
    beside a row waiting to be told what it was that is no move between accounts (`uncategorised`).

    Only they can move a spending or income figure; an account that only ever takes transfers,
    such as a retirement account, cannot. A row nothing was suggested for, such as one from a
    merchant never seen before, is spending or income all the same once a member says which.
    """
    return frozenset(
        posting.account
        for entry in entries
        if isinstance(entry, Transaction)
        and (
            any(root(p.account) in ("Expenses", "Income") for p in entry.postings)
            or uncategorised(entry)
        )
        for posting in entry.postings
        if is_balance_sheet(posting.account)
    )


def uncategorised(entry: Transaction) -> bool:
    """Whether `entry` is a review row waiting against `UNRESOLVED` that is not a move to or
    from an account the row does not name (``booking.moved``): what it was is still to say."""
    group = (entry.meta or {}).get("review-group")
    return (
        entry.flag == "!"
        and isinstance(group, str)
        and booking.moved(group) is None
        and any(p.account == UNRESOLVED for p in entry.postings)
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

    `standing` says where what the books are computed from stands (`standing`): each recompute
    notes it as it starts, so `recompute_on_change` can tell when something changed it since,
    whichever process did.
    """

    def __init__(
        self, establish: Establish, clock: Clock, *, standing: Standing | None = None
    ) -> None:
        self._establish = establish
        self._clock = clock
        self._standing = standing
        self._noted: Hashable = _UNNOTED
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
        # Noted before anything is read, never after: a change made while this one runs is
        # one it may have missed, so it is still a change the next look finds.
        self._noted = self._where()
        now = self._clock()
        try:
            books = self._establish(now)
        except LedgerError as error:
            self._fail(
                Failure(now, error.summary, error.disagreement, error.location, error.reported_by)
            )
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

    def recompute_on_change(self, every: float = WATCH_EVERY) -> threading.Event:
        """Every `every` seconds, recompute if what the books are computed from changed since
        the last recompute started, in a background thread.

        This is how books a page shows take in what another process brought, as `hearth plaid
        sync` run by hand brings it: that process cannot reach these books, only the databases
        both read. A change this process made is noted by the recompute it starts, so it is
        not recomputed twice; a recompute that failed is not retried until something changes.
        A change is taken up only once it has settled, unchanged across two looks: a sync
        writes in steps, its run, the balances, its end and the connection's last sync, and
        books recomputed between them would hold half of it. Setting the event returned stops
        it.
        """
        if self._standing is None:
            raise ValueError("a bookkeeper told nothing to watch cannot watch it")

        stop = threading.Event()

        def run() -> None:
            seen: Hashable = _UNNOTED
            while not stop.wait(every):
                if self._noted is _UNNOTED:
                    continue  # the first recompute has not started; it notes where things stand
                where = self._where()
                if where != self._noted and where == seen:
                    self.recompute()
                seen = where

        threading.Thread(target=run, name="hearth-recompute-on-change", daemon=True).start()
        return stop

    def _where(self) -> Hashable:
        if self._standing is None:
            return None
        try:
            return self._standing()
        except Exception:
            _log.exception("could not tell where the books' sources stand")
            return _UNKNOWN

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


def remembered_prices(
    store: PriceStore, symbols: Iterable[str], sources: Sequence[str] = PRICE_SOURCES
) -> PriceLookup:
    """`stored_prices` for `symbols`, read once and held in memory: the books keep it, so any
    day can be valued after the database is closed, by the same rule."""
    by_symbol: dict[str, tuple[list[date], list[Quote]]] = {}
    for symbol in set(symbols):
        quotes = sorted(
            (quote for source in sources for quote in store.history(symbol, source)),
            key=lambda quote: (quote.as_of, quote.fetched_at),
        )
        if quotes:
            by_symbol[symbol] = ([quote.as_of for quote in quotes], quotes)

    def lookup(symbol: str, on_or_before: date) -> Quote | None:
        found = by_symbol.get(symbol)
        if found is None:
            return None
        days, quotes = found
        index = bisect_right(days, on_or_before)
        return quotes[index - 1] if index else None

    return lookup


def commodities(entries: Iterable[Directive]) -> frozenset[str]:
    """Every commodity a posting holds or is valued in: what the books may need a price of."""
    found: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Transaction):
            continue
        for posting in entry.postings:
            if posting.units is not None and isinstance(posting.units.currency, str):
                found.add(posting.units.currency)
            for amount in (posting.cost, posting.price):
                currency = getattr(amount, "currency", None)
                if isinstance(currency, str):
                    found.add(currency)
    return frozenset(found - {CURRENCY})


type Establish = Callable[[datetime], Books]

type Standing = Callable[[], Hashable]
"""Where what the books are computed from stands now (`standing`)."""

_UNNOTED: Hashable = object()
"""No recompute has started yet, so nothing is noted."""

_UNKNOWN: Hashable = object()
"""Where things stand could not be read; never equal to anything but itself."""

type Rulings = Callable[[sqlite3.Connection], Decide]
"""How the rulings recorded in a database decide review rows (``hearth.disclosure``)."""


def from_household(
    settings: Settings,
    *,
    rulings: Rulings,
    learns: MayLearn,
    sources: Sequence[str] = PRICE_SOURCES,
    connections: Connections | None = None,
) -> Establish:
    """The recompute hearth runs: derive hearth's own entries, over the ledger it inherits if
    there is one, lay the rulings over them and value them at stored prices, noting how current
    each connection that feeds them is; then write the books out for a person to open
    (``hearth.ownbooks``).

    `connections` are the Plaid connections registered in the app database unless given.
    `learns` is the access policy's say on what each row's suggestion may learn from
    (``access.may_learn``, through ``hearth.disclosure``).
    """
    known = connections or RegisteredConnections(settings.database)

    def establish(now: datetime) -> Books:
        # Each sync is noted before the feed is read, never after: a sync that finishes in
        # between then vouches for rows the books hold, not for rows they do not hold yet.
        synced = synced_through(known)
        household = household_entries(settings, now.astimezone().date(), learns=learns)
        if household.frozen is not None:
            synced = vouched_over_base(synced, household.frozen, household.carried)
        conn = db.connect(settings.database)
        try:
            prices = remembered_prices(PriceStore(conn), commodities(household.entries), sources)
            books = compute(
                household.entries,
                prices,
                now=now,
                decide=rulings(conn),
                synced=synced,
                unknown=household.unknown,
                unproved=household.unproved,
                invested=household.invested,
                unfed=household.unfed,
                carried=household.carried,
                held_back=household.held_back,
                kinds=categories.stored(conn),
                price_sources=sources,
            )
        finally:
            conn.close()
        written = ownbooks.render(ownbooks.own(books.entries), inherits=household.ledger)
        ownbooks.write(settings.books_file, written, now=now)
        return books

    return establish


def standing(settings: Settings) -> Hashable:
    """Where everything a recompute reads stands: it changes whenever something that could
    change the books changes, in either database, made by this process or another. Cheap
    enough to ask every few seconds, and it says nothing of what any row holds.

    The feed's runs, holdings reports and balances (``Feed.standing``); and in the app database
    the stored prices, every connection and the account each of its accounts feeds, the
    household's accounts and their owners, each named account, each ruling, each uploaded
    statement and each kind of category a person set. An inherited ledger is the household's,
    read on every recompute, so its size and modification time are part of it too; and so is
    the digest of the base hearth imported, which changes when an import, in this process or
    another, replaces it (``hearth.base``).
    """
    with feed.Feed.open(settings.feed_database) as fed:
        fed_at = fed.standing()
    conn = db.connect(settings.database)
    try:
        app = tuple(
            tuple(tuple(row) for row in conn.execute(query).fetchall())
            for query in (
                "SELECT max(id) FROM price",
                "SELECT item_id, state, last_synced_at, history FROM plaid_items ORDER BY item_id",
                "SELECT account_id, hearth_account FROM plaid_accounts ORDER BY account_id",
                "SELECT id, ledger_account, name FROM accounts ORDER BY id",
                "SELECT account_id, member_id FROM account_owners ORDER BY account_id, member_id",
                "SELECT account_id FROM named_accounts ORDER BY account_id",
                "SELECT max(id) FROM review_rulings",
                "SELECT max(id) FROM statement_uploads",
                "SELECT id, before, after FROM carried_over ORDER BY id",
                "SELECT account, kind FROM category_kinds ORDER BY account",
            )
        )
    finally:
        conn.close()
    ledger: tuple[int, int] | None = None
    if settings.ledger is not None:
        try:
            stat = settings.ledger.stat()
            ledger = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            ledger = None
    return (fed_at, app, ledger, base.standing(settings.ledger_base))


@dataclass(frozen=True, slots=True)
class Entries:
    """The household's entries before any ruling, each account hearth opened whose opening
    balance could not be derived, with why, each a brokerage account feeds
    (``hearth.plaid.opening``), and each the household holds that nothing opens
    (``ownbooks.Derived.unfed``); and the ledger they go on top of, if any (`inherited`)."""

    entries: tuple[Directive, ...]
    unknown: Mapping[str, Unknown] = field(default_factory=dict)
    invested: frozenset[str] = frozenset()
    unfed: frozenset[str] = frozenset()
    ledger: Path | Base | None = None
    frozen: Mapping[str, date] | None = None
    """Each account the base opens, with its last day in it (`frozen_through`), when the
    ledger is a frozen base; None for a live ledger, or none."""
    carried: Mapping[str, Carry] = field(default_factory=dict)
    """Each account of the base hearth carries on, and what carrying made of its bank's rows
    (``hearth.carrying``)."""
    unproved: Mapping[str, Unknown] = field(default_factory=dict)
    """Each carried account whose bank's balance cannot prove it yet, with why."""
    held_back: Mapping[str, tuple[date, date]] = field(default_factory=dict)
    """Each account of the base matched to a connection whose cut-over has not come, with
    the last day the base knows of it today and the latest day the base holds anything on it
    (``carrying.known_through``, ``carrying.ahead``)."""


def _opens(entries: Sequence[Directive]) -> Opens:
    """Each account `entries` open: the day it opens, the day it closes, if it does, and the
    currencies it takes, if it names any."""
    found: dict[str, tuple[date, date | None, frozenset[str] | None]] = {}
    closes: dict[str, date] = {}
    for entry in entries:
        if isinstance(entry, Open):
            found[entry.account] = (
                entry.date,
                None,
                frozenset(entry.currencies) if entry.currencies else None,
            )
        elif isinstance(entry, Close):
            closes[entry.account] = entry.date
    return {
        account: (opens, closes.get(account), currencies)
        for account, (opens, _, currencies) in found.items()
    }


BOTH = (
    "Hearth was given two ledgers: HEARTH_LEDGER, and the ledger imported into its data"
    " directory. It reads the imported one now, so unset HEARTH_LEDGER."
)


def inherited(settings: Settings) -> Path | Base | None:
    """The ledger hearth's own entries go on top of: the base it imported and froze
    (``hearth.base``), or the household's own ledger at `HEARTH_LEDGER`, or None.

    Never both: which one held the books would be a guess, so a recompute told of both
    refuses with a sentence saying so.
    """
    try:
        found = base.current(settings.ledger_base)
    except BaseError as error:
        raise LedgerError(str(error)) from error
    if found is not None and settings.ledger is not None:
        raise LedgerError(BOTH)
    return found.base if found is not None else settings.ledger


def household_entries(
    settings: Settings, today: date | None = None, *, learns: MayLearn = learn_nothing
) -> Entries:
    """The household's entries before any ruling: hearth's own over the ledger it inherits,
    imported or not (`inherited`), each read exactly alike (`load_ledger`).

    Checked as beancount checks a file it loads, so the books written out load as cleanly as
    they were computed; hearth's entries that would not are refused, never served.

    `today` is the household's own day, the clock's local day, which decides whether a carried
    account's cut-over has come (``carrying.postponed``); the system clock's when not given.

    Each row hearth books is suggested a category from what `learns` lets its owners learn
    (``hearth.proposals``); by default from nothing, which suits a reader of the books that
    shows no suggestion, such as a command that only needs which accounts there are.
    """
    today = today or datetime.now().astimezone().date()
    ledger = inherited(settings)
    if ledger is not None:
        loaded = load_ledger(ledger)
        theirs, options_map = loaded.entries, loaded.options
    else:
        theirs, options_map = (), options.OPTIONS_DEFAULTS.copy()
    opened = {e.account for e in theirs if isinstance(e, Open)}
    declared = {e.currency for e in theirs if isinstance(e, Commodity)}
    since = min((e.date for e in theirs), default=None)
    # Only an imported base is carried on: a ledger inherited where it is may still be growing.
    held = carrying.read(theirs) if isinstance(ledger, Base) else None
    conn = db.connect(settings.database)
    try:
        with feed.Feed.open(settings.feed_database) as fed:
            derived = ownbooks.derive(
                conn,
                fed,
                opened=opened,
                declared=declared,
                since=since,
                base=held,
                today=today,
                learns=learns,
                opens=_opens(theirs),
            )
    except booking.Refused as refused:
        raise LedgerError(str(refused)) from refused
    finally:
        conn.close()
    own = derived.entries
    entries = tuple(sorted((*theirs, *own), key=entry_sortkey))
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
    frozen = frozen_through(held, today) if held is not None else None
    held_back = {}
    if held is not None:
        for account in derived.postponed:
            beyond = carrying.ahead(held, account)
            assert beyond is not None  # noqa: S101 - a cut-over after today is the base's
            held_back[account] = (carrying.known_through(held, account, today), beyond)
    return Entries(
        entries,
        derived.unknown,
        derived.invested,
        derived.unfed,
        ledger,
        frozen,
        derived.carried,
        derived.unproved,
        held_back,
    )
