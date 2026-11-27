"""The books read the way a business reads its own: what came in and went out over a period,
what was held at its start and its end, and why the one became the other.

A recompute builds one `Ledger` of the books (``books.Books.ledger``); every statement asked
of it afterwards is a few lookups, whatever the size of the history. It answers three things
that share one set of numbers, so they always agree:

- `Ledger.flows`: every income and expense account's money over a period, and every other
  amount that moved what the statement's accounts hold;
- `Ledger.held`: what each account held at the end of a day, at cost and at market value;
- `bridge`: why what was held at the start became what was held at the end.

A statement reads the accounts it is given (`scope`) and nothing else; who may see which is
``hearth.disclosure``'s to decide, and it decides before anything here is asked.

- A transaction whose balance-sheet postings are all in scope is read in full: its income and
  expense postings by account, each as the kind of money its account is
  (``hearth.categories``), and its equity postings as opening balances and adjustments, or, for
  a row that waits for review with nowhere to go, as money not placed yet.
- A transaction that also moves an account outside the scope is a move across the scope's
  edge: what it did to the accounts in scope is one amount, and none of its income or expense
  postings is counted, as nothing says whose they were.

How what is held is valued:

- Money is its units. A lot is held at its cost, and at market value at the latest price on or
  before the day. A lot with no price is not valued at market value, as Overview and Holdings
  do not value it: it is named, and the figure is a floor.
- A holding booked without a lot, as hearth books what a brokerage reports, costs what it was
  worth the day it was booked. One booked before anything priced it enters the books on its
  first price, as an opening, at cost and at market value alike. Less of one gives up its
  average cost; moved between accounts, it keeps it.
- Units of a holding already held, added against equity with no money and no price, as a split
  is booked, change its units and nothing else: no cost, no value, no opening.

Why it changed. Every transaction balances, so at cost what the accounts hold changes by
exactly what its other postings weigh, plus what a holding with no lot gave up beyond what it
weighs. At market value each posting is marked at the price of its day as well: market movement
is what prices did to what was held between those days, and the difference between a holding's
mark and what it was booked at is said where it belongs: with an opening or a move across the
edge that brought it, or, when a gain is booked as income on a sale, as a gain already counted
in value, so market movement reads nothing when prices did not move. Anything left is
`Bridge.difference`, shown, never hidden: it can only come from an amount nothing can price.
Nothing here reads a clock or decides who may see anything.
"""

import calendar
from array import array
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from beancount.core.amount import Amount
from beancount.core.data import Directive, Posting, Transaction
from beancount.core.position import Cost

from hearth.categories import Kind
from hearth.reports.common import is_balance_sheet, root, units
from hearth.review.pile import LEDGER_BANK, WAITING
from hearth.valuation.networth import STALE_AFTER_MISSED_SESSIONS, missed_sessions

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class Material:
    """How large a change must be, both in money and in proportion, before it is coloured."""

    amount: Decimal
    share: Decimal


MATERIAL = Material(amount=Decimal(1000), share=Decimal("0.10"))
"""A change is worth a colour only when it is over 1,000 in the operating currency and over
10% of what it is set against: either alone is ordinary in a household's year."""


class Better(StrEnum):
    """Which way a figure is good news for the household, whichever way is up."""

    MORE = "more"
    LESS = "less"
    NEITHER = "neither"


class Tone(StrEnum):
    """What a change means for the household: never up or down, which the sign says."""

    FAVOURABLE = "favourable"
    UNFAVOURABLE = "unfavourable"
    QUIET = "quiet"


def tone(now: Decimal | None, before: Decimal | None, better: Better) -> Tone:
    """Favourable or unfavourable only when the change is material by `MATERIAL` in both
    senses; quiet otherwise, or when either figure is not known."""
    if now is None or before is None or better is Better.NEITHER:
        return Tone.QUIET
    change = now - before
    if abs(change) <= MATERIAL.amount:
        return Tone.QUIET
    if before != 0 and abs(change) <= MATERIAL.share * abs(before):
        return Tone.QUIET
    return Tone.FAVOURABLE if (change > 0) == (better is Better.MORE) else Tone.UNFAVOURABLE


def share(now: Decimal | None, before: Decimal | None) -> Decimal | None:
    """The change as a share of what it is set against; None against nothing."""
    if now is None or before is None or before == 0:
        return None
    return (now - before) / abs(before)


@dataclass(frozen=True, slots=True)
class Period:
    """The days from `start` to `end`, both included."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"start {self.start} is after end {self.end}")

    def __contains__(self, day: object) -> bool:
        return isinstance(day, date) and self.start <= day <= self.end

    @property
    def before(self) -> date:
        """The last day before it: its opening sheet is what was held at the end of it."""
        return self.start - timedelta(days=1)

    def a_year_before(self) -> Period:
        """The same days a year before, so part of a year is never set against a whole one."""
        return Period(_a_year_before(self.start), _a_year_before(self.end))


def _a_year_before(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        return day.replace(year=day.year - 1, day=28)


class Columns(StrEnum):
    """How an income statement splits its period."""

    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


def columns(period: Period, by: Columns) -> list[Period]:
    """The calendar months, quarters or years the period touches, each cut to the period."""
    found = []
    start = period.start
    while start <= period.end:
        end = min(_column_end(start, by), period.end)
        found.append(Period(start, end))
        start = end + timedelta(days=1)
    return found


def _column_end(day: date, by: Columns) -> date:
    if by is Columns.YEAR:
        return date(day.year, 12, 31)
    months = 3 if by is Columns.QUARTER else 1
    last = ((day.month - 1) // months + 1) * months
    return date(day.year, last, calendar.monthrange(day.year, last)[1])


# -- what the engine is given -----------------------------------------------------------------


class Prices(Protocol):
    """The prices a ledger is valued at (``valuation.pricetable.PriceTable``)."""

    def price(self, symbol: str, on_or_before: date, currency: str) -> Decimal | None: ...

    def day(self, symbol: str, on_or_before: date) -> date | None: ...

    def first(self, symbol: str, currency: str) -> date | None: ...


# -- what each account held -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Held:
    """What one account, or a group of them, held at the end of a day, in the operating
    currency, at cost and at market value.

    A lot with no price is in `cost` and not in `market`, and named in `unpriced`, as no screen
    values it. A holding with neither a price nor a cost is in neither and named in `unvalued`.
    """

    cost: Decimal = ZERO
    market: Decimal = ZERO
    unpriced: frozenset[str] = frozenset()
    unvalued: frozenset[str] = frozenset()
    priced: date | None = None
    """The newest price day behind `market`, when any position was priced."""
    stale: int = 0
    """Positions valued at a price older than ``networth.STALE_AFTER_MISSED_SESSIONS`` allows."""
    securities: bool = False
    """Whether anything held is not the operating currency."""

    def __add__(self, other: Held) -> Held:
        days = [d for d in (self.priced, other.priced) if d is not None]
        return Held(
            self.cost + other.cost,
            self.market + other.market,
            self.unpriced | other.unpriced,
            self.unvalued | other.unvalued,
            max(days, default=None),
            self.stale + other.stale,
            self.securities or other.securities,
        )

    def value(self, market: bool) -> Decimal:
        return self.market if market else self.cost

    @property
    def is_floor(self) -> bool:
        """Whether its market value leaves out holdings with no price."""
        return bool(self.unpriced)


def total(held: Iterable[Held]) -> Held:
    found = Held()
    for each in held:
        found += each
    return found


# -- the parts of a change -------------------------------------------------------------------


class Other(StrEnum):
    """What moved what the statement's accounts hold without being earned from work, earned
    from investments or spent: the parts of "Other" in `Bridge`, each its own line."""

    OTHER_INCOME = "other_income"
    """Income of no other kind (``categories.Kind.OTHER_INCOME``)."""
    NOT_SPENDING = "not_spending"
    """Booked as an expense but kept, not spent, such as a retirement contribution: it left
    these accounts, so it is less held, never spending."""
    OPENING = "opening"
    """Opening balances and adjustments: what an account held before its history in the
    books, a pad, a difference reconciling an imported ledger with a bank; at market value,
    with any gain a holding came in with."""
    UNPLACED = "unplaced"
    """Rows nobody has said what they were yet, which count in no income or spending."""
    OUTSIDE = "outside"
    """Moves to and from accounts outside the statement, at market value with the gain the
    holdings moved carried with them."""
    TAKEN = "taken"
    """At market value: a gain booked as income when a holding was sold, which its value
    already counted. Investment income says it; this takes it back out, so it is not counted
    twice, and market movement says only what prices did."""
    UNPRICED = "unpriced"
    """At market value: what was paid for lots with no price, which no screen values, and
    what a lot was worth when its first price came."""
    REALISED = "realised"
    """At cost: a holding with no lot given up for more or less than its average cost, a gain
    the books did not book as income."""
    ROUNDING = "rounding"
    """What entries leave over within the tolerance beancount balances them to."""


@dataclass(frozen=True, slots=True)
class Unreviewed:
    """Of a flow, what rests on rows nobody has checked yet, at their suggested category, and
    the rows nothing says what they were yet, which are in no category at all."""

    rows: int = 0
    income: Decimal = ZERO
    expenses: Decimal = ZERO
    unplaced: int = 0
    unplaced_out: Decimal = ZERO
    unplaced_in: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class Flows:
    """What each income and expense account came to over a period, and what else moved what
    the statement's accounts hold, at cost (`at_cost`) and at market value (`at_market`).

    Amounts are signed as a person reads them: income positive when earned, expenses positive
    when paid; each of the others positive when it added to what was held.
    """

    period: Period
    by_account: Mapping[str, Decimal]
    kinds: Mapping[str, Kind]
    at_cost: Mapping[Other, Decimal]
    at_market: Mapping[Other, Decimal]
    marks: Decimal
    """At market value: every posting moved what was held by its value on its day."""
    priced: Decimal
    """At market value: lots first priced in the period, at that price."""
    beside: Decimal
    """At market value: the gap between a mark and what a posting was booked at, in rows that
    neither open, nor move across the edge, nor book a gain, such as a buy a little off the
    day's close. It is what the market did, so it is market movement's."""
    unreviewed: Unreviewed
    unconverted: int
    """Postings with no price to convert them: left out, and the difference if any."""
    later: int = 0
    """Postings booked before anything priced what they are in, converted on their own day at
    the first price there was: said beside the figure, as a stale price is."""

    def of_kind(self, kind: Kind) -> Decimal:
        return sum(
            (n for account, n in self.by_account.items() if self.kinds[account] is kind), ZERO
        )

    @property
    def pay(self) -> Decimal:
        """Take-home pay: pay as the books hold it, which is pay as it reached the bank; a
        ledger that books pay before what was withheld from it shows that here, and what was
        withheld under tax."""
        return self.of_kind(Kind.PAY)

    @property
    def tax(self) -> Decimal:
        return self.of_kind(Kind.TAX)

    @property
    def spending(self) -> Decimal:
        return self.of_kind(Kind.SPENDING)

    @property
    def not_spending(self) -> Decimal:
        return self.of_kind(Kind.NOT_SPENDING)

    @property
    def investment_income(self) -> Decimal:
        return self.of_kind(Kind.INVESTMENT_INCOME)

    @property
    def other_income(self) -> Decimal:
        return self.of_kind(Kind.OTHER_INCOME)

    @property
    def saved(self) -> Decimal:
        """Saved from work: take-home pay less tax and spending."""
        return self.pay - self.tax - self.spending

    @property
    def savings_rate(self) -> Decimal | None:
        """Saved from work over pay after tax; None without any, since a rate would be invented."""
        after_tax = self.pay - self.tax
        return self.saved / after_tax if after_tax > 0 else None

    def others(self, market: bool) -> dict[Other, Decimal]:
        """Each part of "Other", at cost or at market value."""
        found = {
            Other.OTHER_INCOME: self.other_income,
            Other.NOT_SPENDING: -self.not_spending,
            **(self.at_market if market else self.at_cost),
        }
        return {key: found.get(key, ZERO) for key in Other}


# -- why it changed ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Bridge:
    """Why what the statement's accounts hold changed over a period, at cost or at market
    value: from `start` through saved from work, investment income, market movement and each
    part of "Other" to `end`. `difference` is whatever is left, shown, never hidden."""

    market: bool
    start: Decimal
    end: Decimal
    saved: Decimal
    investment_income: Decimal
    movement: Decimal | None
    """What prices did to what was held; None at cost."""
    others: Mapping[Other, Decimal]

    @property
    def other(self) -> Decimal:
        return sum(self.others.values(), ZERO)

    @property
    def explained(self) -> Decimal:
        return (
            self.start + self.saved + self.investment_income + (self.movement or ZERO) + self.other
        )

    @property
    def difference(self) -> Decimal:
        return self.end - self.explained


def movement(opening: Held, closing: Held, moved: Flows) -> Decimal:
    """What prices did to what was held over the period: its market value at the end, less at
    the start, less every posting's value on its day and each lot's value on its first price,
    and with what the market made of rows booked a little off the day's price."""
    return closing.market - opening.market - moved.marks - moved.priced + moved.beside


def bridge(opening: Held, closing: Held, moved: Flows, *, market: bool) -> Bridge:
    return Bridge(
        market=market,
        start=opening.value(market),
        end=closing.value(market),
        saved=moved.saved,
        investment_income=moved.investment_income,
        movement=movement(opening, closing, moved) if market else None,
        others=moved.others(market),
    )


# -- the ledger, as one recompute reads it once ----------------------------------------------


@dataclass(frozen=True, slots=True)
class _Leg:
    """A balance-sheet posting, as the ledger read it."""

    account: str
    commodity: str
    units: Decimal
    weight: Decimal | None
    """What it weighs in the operating currency: what the entry balances by, at its day."""
    cost: Decimal | None
    """What it moved its position's cost by."""
    mark: Decimal | None
    """What it is worth at its day's price; None for a holding with no price yet."""
    lot: bool = False
    """Whether it reduces a lot: what of its mark is over its cost is a gain the books may
    book as income, which its value already held."""


@dataclass(frozen=True, slots=True)
class _Event:
    """One entry, or the part of it that balances in one currency: a part in a currency
    nothing priced on its day is read on the day it is first priced."""

    day: date
    accounts: frozenset[str]
    legs: tuple[_Leg, ...]
    others: tuple[tuple[str, Decimal | None], ...]
    flag: str
    first: bool
    """Whether it is the entry's first part, which counts the entry once."""
    later: int = 0
    """How many of its postings were converted at a later day's first price."""

    @property
    def opens(self) -> bool:
        return any(root(a) == "Equity" and a not in WAITING for a, _ in self.others)

    @property
    def earns(self) -> bool:
        return any(root(a) == "Income" for a, _ in self.others)


class _Running:
    """A running total that changes on some days, read as the total over any span of days."""

    __slots__ = ("days", "totals")

    def __init__(self) -> None:
        self.days: array[int] = array("l")
        self.totals: list[Decimal] = []

    def add(self, day: int, amount: Decimal) -> None:
        last = self.totals[-1] if self.totals else ZERO
        if self.days and self.days[-1] == day:
            self.totals[-1] = last + amount
        else:
            self.days.append(day)
            self.totals.append(last + amount)

    def at(self, day: int) -> Decimal:
        index = bisect_right(self.days, day)
        return self.totals[index - 1] if index else ZERO

    def over(self, after: int, through: int) -> Decimal:
        return self.at(through) - self.at(after)


@dataclass(slots=True)
class _Signature:
    """Every entry that moves exactly these balance-sheet accounts, added up by day."""

    accounts: frozenset[str]
    sums: dict[tuple[str, ...], _Running] = field(default_factory=dict)

    def add(self, key: tuple[str, ...], day: int, amount: Decimal) -> None:
        if amount == 0:
            return
        self.sums.setdefault(key, _Running()).add(day, amount)

    def over(self, key: tuple[str, ...], after: int, through: int) -> Decimal:
        found = self.sums.get(key)
        return found.over(after, through) if found is not None else ZERO

    @property
    def others(self) -> list[str]:
        return [key[1] for key in self.sums if key[0] == "o"]


@dataclass(slots=True)
class _Position:
    units: Decimal = ZERO
    cost: Decimal | None = ZERO
    days: array[int] = field(default_factory=lambda: array("l"))
    unit_history: list[Decimal] = field(default_factory=list)
    cost_history: list[Decimal | None] = field(default_factory=list)

    def note(self, day: int) -> None:
        if self.days and self.days[-1] == day:
            self.unit_history[-1], self.cost_history[-1] = self.units, self.cost
        else:
            self.days.append(day)
            self.unit_history.append(self.units)
            self.cost_history.append(self.cost)

    def at(self, day: int) -> tuple[Decimal, Decimal | None]:
        index = bisect_right(self.days, day)
        return (
            (self.unit_history[index - 1], self.cost_history[index - 1]) if index else (ZERO, ZERO)
        )


class Ledger:
    """The books, read once, so a statement over any accounts and days is a few lookups."""

    def __init__(self, prices: Prices, currency: str) -> None:
        self.prices = prices
        self.currency = currency
        self._signatures: dict[frozenset[str], _Signature] = {}
        self._positions: dict[tuple[str, str], _Position] = {}
        self._first: dict[str, date] = {}

    @classmethod
    def read(cls, entries: Iterable[Directive], prices: Prices, currency: str) -> Ledger:
        ledger = cls(prices, currency)
        events = sorted(ledger._parts(entries), key=lambda pair: pair[0])
        for (_, _), part in events:
            ledger._book(*part)
        return ledger

    # Reading entries into the parts that balance in one currency.

    def _parts(
        self, entries: Iterable[Directive]
    ) -> Iterator[tuple[tuple[date, int], tuple[Transaction, list[Posting], date, bool]]]:
        order = 0
        for entry in entries:
            if not isinstance(entry, Transaction):
                continue
            groups: defaultdict[str, list[Posting]] = defaultdict(list)
            for posting in entry.postings:
                groups[_weight(posting, entry).currency].append(posting)
            first = True
            for postings in groups.values():
                order += 1
                yield (entry.date, order), (entry, postings, entry.date, first)
                first = False

    def _rate(self, commodity: str, day: date) -> Decimal | None:
        """What one of `commodity` was worth on `day`: its latest price on or before it."""
        if commodity == self.currency:
            return Decimal(1)
        return self.prices.price(commodity, day, self.currency)

    def _converted(self, commodity: str, day: date) -> tuple[Decimal | None, bool]:
        """The rate an amount in `commodity` is converted at on `day`, and whether it is a
        later day's: an amount booked before anything priced it is converted at its first
        price, on its own day, and the figure says so (`Flows.later`)."""
        rate = self._rate(commodity, day)
        if rate is not None:
            return rate, False
        first = self.prices.first(commodity, self.currency)
        if first is None:
            return None, False
        return self.prices.price(commodity, first, self.currency), True

    def _book(self, entry: Transaction, postings: list[Posting], day: date, first: bool) -> None:
        ordinal = day.toordinal()
        legs_in = [p for p in postings if is_balance_sheet(p.account)]
        converted = {id(p): self._weighs(p, entry, day) for p in postings}
        weights: dict[int, Decimal | None] = {key: w for key, (w, _) in converted.items()}
        later = sum(1 for _, late in converted.values() if late)
        splits = self._splits(entry, postings, legs_in)
        if splits:
            weights = dict.fromkeys(weights, ZERO)
        # What holdings with no lot gave up, by commodity, for holdings moved into others.
        pool: defaultdict[str, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
        moved: list[tuple[Posting, _Position, Decimal | None]] = []
        given: Decimal | None
        lots: set[int] = set()
        for posting in legs_in:
            amount = units(posting, entry)
            held = self._positions.setdefault((posting.account, amount.currency), _Position())
            bare = posting.cost is None and amount.currency != self.currency
            reducing = held.units != 0 and (held.units > 0) != (amount.number > 0)
            if reducing and posting.cost is not None:
                lots.add(id(posting))
            if splits:
                # A split changes units and nothing else: no leg gives up or takes on cost.
                moved.append((posting, held, ZERO))
            elif bare and reducing and held.cost is not None:
                given = held.cost * amount.number / held.units
                pool[amount.currency][0] += -amount.number
                pool[amount.currency][1] += -given
                moved.append((posting, held, given))
            else:
                moved.append((posting, held, None))
        legs: list[_Leg] = []
        for posting, held, given in moved:
            amount = units(posting, entry)
            weighs = weights[id(posting)]
            cost: Decimal | None
            if given is not None:
                cost = given
            elif posting.cost is None and amount.currency != self.currency and amount.number > 0:
                # A holding with no lot, moved in from another account in the same entry,
                # keeps what it cost there; anything more costs what it weighs.
                units_left, cost_left = pool[amount.currency]
                taken = min(units_left, amount.number)
                carried = cost_left * taken / units_left if units_left else ZERO
                pool[amount.currency] = [units_left - taken, cost_left - carried]
                rest = amount.number - taken
                if rest == 0:
                    cost = carried
                elif weighs is None:
                    cost = None
                else:
                    cost = carried + weighs * rest / amount.number
            else:
                cost = weighs
            held.units += amount.number
            held.cost = None if held.cost is None or cost is None else held.cost + cost
            if held.units == 0 and not splits:
                held.cost = ZERO
            held.note(ordinal)
            rate = self._rate(amount.currency, day)
            mark = ZERO if splits else (None if rate is None else amount.number * rate)
            legs.append(
                _Leg(
                    posting.account,
                    amount.currency,
                    amount.number,
                    weighs,
                    cost,
                    mark,
                    id(posting) in lots,
                )
            )
            if amount.currency != self.currency and amount.currency not in self._first:
                found = self.prices.first(amount.currency, self.currency)
                if found is not None:
                    self._first[amount.currency] = found
        others = tuple(
            (p.account, weights[id(p)]) for p in postings if not is_balance_sheet(p.account)
        )
        event = _Event(
            day,
            # Whether an entry crosses the statement's edge is the whole entry's to say, in
            # every currency it moves, as the journal of a statement's rows reads it.
            frozenset(p.account for p in entry.postings if is_balance_sheet(p.account)),
            tuple(legs),
            others,
            entry.flag or "*",
            first,
            later,
        )
        if legs:
            self._file(event)

    def _weighs(
        self, posting: Posting, entry: Transaction, day: date
    ) -> tuple[Decimal | None, bool]:
        weighs = _weight(posting, entry)
        rate, late = self._converted(weighs.currency, day)
        if rate is None or weighs.number is None:
            return None, False
        return weighs.number * rate, late

    def _splits(self, entry: Transaction, postings: list[Posting], legs: list[Posting]) -> bool:
        """Whether a part is a split of a holding with no lot, in one commodity, with no lot
        and no price, which changes units and nothing else. It is one when it takes the old
        position out of an account and puts the new one in the same account; or when it adds
        or removes units of a commodity each of its accounts already holds, against an equity
        account that is neither an opening nor a reconciling one (`opening_equity`), such as
        `Equity:Splits`. Units moved against an opening or a reconciling entry are never one:
        they came from somewhere, and are an opening."""
        commodities = {units(p, entry).currency for p in postings}
        if len(commodities) != 1 or self.currency in commodities:
            return False
        if any(p.cost is not None or p.price is not None for p in postings):
            return False
        by_account: defaultdict[str, set[bool]] = defaultdict(set)
        for posting in legs:
            by_account[posting.account].add(units(posting, entry).number > 0)
        if any(signs == {True, False} for signs in by_account.values()):
            return True
        equity = [p.account for p in postings if root(p.account) == "Equity"]
        if not legs or not equity or len(equity) + len(legs) != len(postings):
            return False
        if any(opening_equity(account) or account in WAITING for account in equity):
            return False
        (commodity,) = commodities
        return all(
            self._positions.get((p.account, commodity), _Position()).units != 0 for p in legs
        )

    def _file(self, event: _Event) -> None:
        signature = self._signatures.setdefault(event.accounts, _Signature(event.accounts))
        day = event.day.toordinal()
        add = signature.add
        weights = [leg.weight for leg in event.legs] + [w for _, w in event.others]
        known = [w for w in weights if w is not None]
        add(("unconverted",), day, Decimal(len(weights) - len(known)))
        if len(known) == len(weights):
            add(("rounding",), day, sum(known, ZERO))
        add(("later",), day, Decimal(event.later))
        for leg in event.legs:
            a = leg.account
            # A gap at an opening is the opening's; one on a lot given up for a gain booked as
            # income is a gain its value already held; any other, a buy off the day's close,
            # a dividend reinvested, a split of lots, is what the market did.
            kind = "open" if event.opens else "earn" if leg.lot and event.earns else "beside"
            if leg.weight is not None:
                add(("w", a), day, leg.weight)
                if leg.cost is not None:
                    add(("cost-w", a), day, leg.cost - leg.weight)
                if leg.mark is not None:
                    add(("mark", a), day, leg.mark)
                    add(("mark-w", kind, a), day, leg.mark - leg.weight)
                else:
                    add(("unpriced", a), day, leg.weight)
        for account, weighs in event.others:
            if weighs is not None:
                add(("o", account), day, weighs)
        if event.flag == "!" and event.first:
            add(("rows",), day, Decimal(1))
            income = [w for a, w in event.others if w is not None and root(a) == "Income"]
            spent = [w for a, w in event.others if w is not None and root(a) == "Expenses"]
            add(("unreviewed-in",), day, -sum(income, ZERO))
            add(("unreviewed-out",), day, sum(spent, ZERO))
        waiting = [w for a, w in event.others if a in WAITING and w is not None]
        if waiting:
            add(("prow",), day, Decimal(1))
            add(("unplaced-out",), day, sum((w for w in waiting if w > 0), ZERO))
            add(("unplaced-in",), day, -sum((w for w in waiting if w < 0), ZERO))

    # Asking it.

    def first_day(self, scope: Collection[str] | None) -> date | None:
        """The first day any account in `scope` moved, or any account at all."""
        days = [
            date.fromordinal(p.days[0])
            for (account, _), p in self._positions.items()
            if (scope is None or account in scope) and p.days
        ]
        return min(days, default=None)

    def held(self, day: date, scope: Collection[str]) -> dict[str, Held]:
        """What each account in `scope` held at the end of `day`. One holding nothing is not
        in it."""
        ordinal = day.toordinal()
        found: dict[str, Held] = {}
        for (account, commodity), position in self._positions.items():
            if account not in scope:
                continue
            quantity, cost = position.at(ordinal)
            if quantity == 0:
                continue
            held = self._value(commodity, quantity, cost, day)
            found[account] = found[account] + held if account in found else held
        return found

    def _value(self, commodity: str, quantity: Decimal, cost: Decimal | None, day: date) -> Held:
        if commodity == self.currency:
            return Held(quantity, quantity)
        price = self.prices.price(commodity, day, self.currency)
        if cost is None:
            if price is None:
                return Held(unvalued=frozenset({commodity}), securities=True)
            # Nothing said what it cost; it costs what it is worth, as Holdings has no cost.
            return Held(quantity * price, quantity * price, securities=True)
        if price is None:
            return Held(cost, ZERO, unpriced=frozenset({commodity}), securities=True)
        priced = self.prices.day(commodity, day)
        stale = priced is not None and missed_sessions(priced, day) > STALE_AFTER_MISSED_SESSIONS
        return Held(cost, quantity * price, priced=priced, stale=int(stale), securities=True)

    def flows(self, period: Period, scope: Collection[str], kind: Callable[[str], Kind]) -> Flows:
        """Every income and expense account's money over `period`, and what else moved what
        the accounts in `scope` hold, read as the module says."""
        inside_scope = frozenset(scope)
        after, through = period.before.toordinal(), period.end.toordinal()
        by_account: defaultdict[str, Decimal] = defaultdict(Decimal)
        cost: defaultdict[Other, Decimal] = defaultdict(Decimal)
        market: defaultdict[Other, Decimal] = defaultdict(Decimal)
        marks = beside = ZERO
        counts: defaultdict[str, Decimal] = defaultdict(Decimal)
        for accounts, signature in self._signatures.items():
            inside = accounts & inside_scope
            if not inside:
                continue

            def over(*key: str, signature: _Signature = signature) -> Decimal:
                return signature.over(key, after, through)

            others = signature.others
            counts["unconverted"] += over("unconverted")
            counts["later"] += over("later")
            rounding = over("rounding")
            cost[Other.ROUNDING] += rounding
            market[Other.ROUNDING] += rounding
            marks += sum((over("mark", a) for a in inside), ZERO)
            market[Other.UNPRICED] -= sum((over("unpriced", a) for a in inside), ZERO)
            gaps = {k: sum((over("mark-w", k, a) for a in inside), ZERO) for k in _KINDS}
            given = sum((over("cost-w", a) for a in inside), ZERO)
            if not accounts <= inside_scope:
                # Across the edge: one amount, whatever the entries were for.
                edge = -sum((over("o", o) for o in others), ZERO)
                edge -= sum((over("w", a) for a in accounts - inside_scope), ZERO)
                cost[Other.OUTSIDE] += edge + given
                market[Other.OUTSIDE] += edge + sum(gaps.values(), ZERO)
                continue
            for other in others:
                amount = over("o", other)
                if root(other) in ("Income", "Expenses"):
                    by_account[other] += -amount if root(other) == "Income" else amount
                elif other in WAITING:
                    cost[Other.UNPLACED] -= amount
                    market[Other.UNPLACED] -= amount
                else:
                    cost[Other.OPENING] -= amount
                    market[Other.OPENING] -= amount
            cost[Other.REALISED] += given
            market[Other.OPENING] += gaps["open"]
            market[Other.TAKEN] += gaps["earn"]
            beside += gaps["beside"]
            for key in (
                "rows",
                "unreviewed-in",
                "unreviewed-out",
                "prow",
                "unplaced-out",
                "unplaced-in",
            ):
                counts[key] += over(key)
        priced = self._priced(period, inside_scope)
        market[Other.UNPRICED] += priced
        kept = {a: n for a, n in sorted(by_account.items()) if n != 0}
        return Flows(
            period=period,
            by_account=kept,
            kinds={account: kind(account) for account in kept},
            at_cost=dict(cost),
            at_market=dict(market),
            marks=marks,
            priced=priced,
            beside=beside,
            unreviewed=Unreviewed(
                int(counts["rows"]),
                counts["unreviewed-in"],
                counts["unreviewed-out"],
                int(counts["prow"]),
                counts["unplaced-out"],
                counts["unplaced-in"],
            ),
            unconverted=int(counts["unconverted"]),
            later=int(counts["later"]),
        )

    def _priced(self, period: Period, scope: frozenset[str]) -> Decimal:
        """Lots in `scope` first priced inside the period, at that first price: until then
        they were not valued."""
        found = ZERO
        for (account, commodity), position in self._positions.items():
            first = self._first.get(commodity)
            if account not in scope or first is None or not period.start <= first <= period.end:
                continue
            quantity, _ = position.at(first.toordinal() - 1)
            price = self.prices.price(commodity, first, self.currency)
            if quantity and price is not None:
                found += quantity * price
        return found


def opening_equity(account: str) -> bool:
    """Whether an equity account is one that opens what an account holds, or reconciles it
    with its bank: an opening balance (any equity account named for an opening, as
    `Equity:Opening-Balances` or a pad's), or `Equity:Ledger-Bank-Difference`. Units moved
    against one came from somewhere, and are an opening, never a split."""
    if account == LEDGER_BANK:
        return True
    return root(account) == "Equity" and any(
        "opening" in part.lower() for part in account.split(":")[1:]
    )


_KINDS = ("open", "earn", "beside")
_COUNTED = ("rows", "unreviewed-in", "unreviewed-out", "prow", "unplaced-out", "unplaced-in")


def _weight(posting: Posting, entry: Transaction) -> Amount:
    """What a posting weighs: its cost, its price, or its units, as beancount balances it."""
    amount = units(posting, entry)
    cost, price = posting.cost, posting.price
    if isinstance(cost, Cost) and isinstance(cost.number, Decimal):
        return Amount(amount.number * cost.number, cost.currency)
    if isinstance(price, Amount) and isinstance(price.number, Decimal):
        return Amount(amount.number * price.number, price.currency)
    return Amount(amount.number, amount.currency)


def read(entries: Iterable[Directive], prices: Prices, currency: str) -> Ledger:
    """The books, read once (``books.compute`` keeps it)."""
    return Ledger.read(entries, prices, currency)
