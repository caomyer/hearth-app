"""The books read the way a business reads its own: what came in and went out over a period,
what was held at its start and its end, and why the one became the other.

Three views share one set of numbers, so they always agree:

- flows (`flows`): every income and expense account's money over a period, and the money that
  moved what was held without being earned or spent;
- sheets (`sheets`): what each account held at the end of a day, at cost and at market value;
- the change between two sheets (`Bridge`): saved from work, investment income, market
  movement and everything else.

Every transaction balances, so what the accounts in a statement hold at cost changes by exactly
what its other postings say: income, expenses, equity, and accounts outside the statement.
Market movement is the change in gain not yet realised, which is value less cost. The change
therefore adds up to the sheets to the cent, and anything left is `Bridge.difference`, shown,
never hidden: it can only come from an amount in a currency with no price to convert it.

A statement reads the accounts it is given (`scope`) and nothing else; who may see which is
``hearth.disclosure``'s to decide, and it decides before anything here is asked.

- A transaction whose balance-sheet postings are all in scope is read in full: its income and
  expense postings by account, each as the kind of money its account is
  (``hearth.categories``), and its equity postings as opening balances and adjustments, or, for
  a row that waits for review with nowhere to go, as money not placed yet.
- A transaction that also moves an account outside the scope is a move across the scope's
  edge: what it did to the accounts in scope is one amount, and none of its income or expense
  postings is counted, as nothing says whose they were.

A position is valued at the latest price on or before the day; one with no price at all is
valued at its cost and named. A lot carries its cost; a holding booked without one, as hearth
books what a brokerage reports, costs what it was worth the day it was booked, and a holding
reduced without a lot gives up its average cost. A holding that arrives with a gain it had
already made, booked against an opening balance or moved in from outside the scope, brings that
gain with it, which is not the period's market movement but part of the move that brought it.
Nothing here reads a clock or decides who may see anything.
"""

import calendar
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from beancount.core.amount import Amount
from beancount.core.data import Directive, Posting, Transaction
from beancount.core.position import Cost

from hearth.categories import Kind
from hearth.reports.common import is_balance_sheet, root, units
from hearth.review.pile import WAITING
from hearth.valuation.networth import PriceLookup

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


# -- what each account held -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Held:
    """What one account, or a group of them, held at the end of a day, in the operating
    currency: at cost, at market value, and what neither could value.

    `market` values each position at the latest price on or before the day; one with no price
    is valued at its cost and named in `at_cost`. One whose cost is not known either, but has
    a price, costs its market value and is named in `cost_unknown`; one with neither is left
    out of both and named in `unvalued`.
    """

    cost: Decimal = ZERO
    market: Decimal = ZERO
    at_cost: frozenset[str] = frozenset()
    cost_unknown: frozenset[str] = frozenset()
    unvalued: frozenset[str] = frozenset()
    priced: date | None = None
    """The newest price day behind `market`, when any position was priced."""
    securities: bool = False
    """Whether anything held is not the operating currency."""

    def __add__(self, other: Held) -> Held:
        days = [d for d in (self.priced, other.priced) if d is not None]
        return Held(
            self.cost + other.cost,
            self.market + other.market,
            self.at_cost | other.at_cost,
            self.cost_unknown | other.cost_unknown,
            self.unvalued | other.unvalued,
            max(days, default=None),
            self.securities or other.securities,
        )

    @property
    def gain(self) -> Decimal:
        """Gain not yet realised: what it is worth less what it cost."""
        return self.market - self.cost

    def value(self, market: bool) -> Decimal:
        return self.market if market else self.cost


def total(held: Iterable[Held]) -> Held:
    found = Held()
    for each in held:
        found += each
    return found


@dataclass(slots=True)
class _Position:
    units: Decimal = ZERO
    cost: Decimal | None = ZERO
    """Its cost so far, or None once any of it was booked at a cost nothing could say."""


def _price(prices: PriceLookup, commodity: str, day: date, currency: str) -> Decimal | None:
    quote = prices(commodity, day)
    if quote is None or quote.currency != currency:
        return None
    return quote.price


def _converted(amount: Amount, day: date, prices: PriceLookup, currency: str) -> Decimal | None:
    """An amount in the operating currency, at the latest price on or before `day`."""
    number = amount.number
    if not isinstance(number, Decimal):
        return None
    if amount.currency == currency:
        return number
    price = _price(prices, amount.currency, day, currency)
    return None if price is None else number * price


def weight(
    posting: Posting, entry: Transaction, prices: PriceLookup, currency: str
) -> Decimal | None:
    """What a posting weighs in the operating currency: what beancount balances the entry by
    (its cost, its price, or its units), converted at the entry's day where it is not money."""
    amount = units(posting, entry)
    cost, price = posting.cost, posting.price
    if isinstance(cost, Cost) and isinstance(cost.number, Decimal):
        weighs = Amount(amount.number * cost.number, cost.currency)
    elif isinstance(price, Amount) and isinstance(price.number, Decimal):
        weighs = Amount(amount.number * price.number, price.currency)
    else:
        weighs = Amount(amount.number, amount.currency)
    return _converted(weighs, entry.date, prices, currency)


@dataclass(frozen=True, slots=True)
class _Booked:
    """What one in-scope posting did to its position's cost, against what it weighs."""

    cost: Decimal | None
    weight: Decimal | None

    @property
    def unbooked(self) -> Decimal:
        """How much its position's cost moved beyond what the entry balanced by: a holding
        given up without a lot, at its average cost, for more or less than that."""
        if self.cost is None or self.weight is None:
            return ZERO
        return self.cost - self.weight


def _book(
    held: _Position, posting: Posting, entry: Transaction, prices: PriceLookup, currency: str
) -> _Booked:
    amount = units(posting, entry)
    weighs = weight(posting, entry, prices, currency)
    number = amount.number
    if amount.currency == currency or posting.cost is not None:
        moved = weighs
    elif held.units == 0 or (held.units > 0) == (number > 0):
        # More of a holding with no lot: it costs what it weighs, the day it was booked.
        moved = weighs
    else:
        # Less of one: it gives up its average cost, however much it went for.
        moved = None if held.cost is None else held.cost * number / held.units
    held.units += number
    if held.cost is not None:
        held.cost = None if moved is None else held.cost + moved
    if held.units == 0:
        held.cost = ZERO
    return _Booked(moved, weighs)


def _transactions(entries: Iterable[Directive]) -> list[Transaction]:
    found = [e for e in entries if isinstance(e, Transaction)]
    found.sort(key=lambda entry: entry.date)
    return found


def sheets(
    entries: Iterable[Directive],
    days: Collection[date],
    scope: Collection[str],
    prices: PriceLookup,
    currency: str,
) -> dict[date, dict[str, Held]]:
    """What each account in `scope` held at the end of each of `days`, in one pass.

    An account holding nothing on a day is not in that day's sheet."""
    wanted = sorted(set(days))
    positions: defaultdict[str, defaultdict[str, _Position]] = defaultdict(
        lambda: defaultdict(_Position)
    )
    found: dict[date, dict[str, Held]] = {}
    index = 0
    for entry in _transactions(entries):
        while index < len(wanted) and entry.date > wanted[index]:
            found[wanted[index]] = _snapshot(positions, wanted[index], prices, currency)
            index += 1
        if index == len(wanted):
            break
        for posting in entry.postings:
            if posting.account in scope:
                commodity = units(posting, entry).currency
                _book(positions[posting.account][commodity], posting, entry, prices, currency)
    for day in wanted[index:]:
        found[day] = _snapshot(positions, day, prices, currency)
    return found


def _snapshot(
    positions: Mapping[str, Mapping[str, _Position]],
    day: date,
    prices: PriceLookup,
    currency: str,
) -> dict[str, Held]:
    found = {}
    for account, held in positions.items():
        each = [
            h
            for commodity, p in held.items()
            if (h := _valued(commodity, p, day, prices, currency)) is not None
        ]
        if each:
            found[account] = total(each)
    return found


def _valued(
    commodity: str, position: _Position, day: date, prices: PriceLookup, currency: str
) -> Held | None:
    if position.units == 0:
        return None
    if commodity == currency:
        return Held(position.units, position.units)
    quote = prices(commodity, day)
    price = quote.price if quote is not None and quote.currency == currency else None
    market = None if price is None else position.units * price
    priced = quote.as_of if quote is not None and price is not None else None
    if position.cost is None:
        if market is None:
            return Held(unvalued=frozenset({commodity}), securities=True)
        return Held(
            market, market, cost_unknown=frozenset({commodity}), priced=priced, securities=True
        )
    if market is None:
        return Held(position.cost, position.cost, at_cost=frozenset({commodity}), securities=True)
    return Held(position.cost, market, priced=priced, securities=True)


# -- what came in and went out ----------------------------------------------------------------


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
    books, a pad, a difference reconciling an imported ledger with a bank."""
    UNPLACED = "unplaced"
    """Rows nobody has said what they were yet, which count in no income or spending."""
    OUTSIDE = "outside"
    """Moves to and from accounts outside the statement."""
    REALISED = "realised"
    """A holding with no lot given up for more or less than its average cost: a gain the
    books did not book as income."""
    ROUNDING = "rounding"
    """What entries leave over within the tolerance beancount balances them to, such as a lot
    whose units times its cost is not to the cent of the money paid for it."""


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
    the statement's accounts hold.

    Amounts are signed as a person reads them: income positive when earned, expenses positive
    when paid. Each of `other` is positive when it added to what was held, at cost. `carried`
    is the gain not yet realised that came in with a move across the statement's edge, and
    left with one, which at market value belongs to that move, not to the market.
    """

    period: Period
    by_account: Mapping[str, Decimal]
    kinds: Mapping[str, Kind]
    other: Mapping[Other, Decimal]
    carried: Mapping[Other, Decimal]
    unreviewed: Unreviewed
    unconverted: int
    """Postings in a currency or commodity with no price to convert them: left out."""

    def of_kind(self, kind: Kind) -> Decimal:
        return sum(
            (n for account, n in self.by_account.items() if self.kinds[account] is kind), ZERO
        )

    @property
    def pay(self) -> Decimal:
        """Take-home pay: pay as it reached the bank, after whatever was taken from it."""
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

    @property
    def income(self) -> Decimal:
        return sum((n for a, n in self.by_account.items() if root(a) == "Income"), ZERO)

    @property
    def expenses(self) -> Decimal:
        return sum((n for a, n in self.by_account.items() if root(a) == "Expenses"), ZERO)

    def others(self, market: bool) -> dict[Other, Decimal]:
        """Each part of "Other", at cost or, with what moves carried, at market value."""
        found = {
            Other.OTHER_INCOME: self.other_income,
            Other.NOT_SPENDING: -self.not_spending,
            **self.other,
        }
        if market:
            for key, gain in self.carried.items():
                found[key] = found.get(key, ZERO) + gain
        return {key: found.get(key, ZERO) for key in Other}


def flows(
    entries: Iterable[Directive],
    period: Period,
    scope: Collection[str],
    kind: Callable[[str], Kind],
    prices: PriceLookup,
    currency: str,
) -> Flows:
    """Every income and expense account's money over `period`, and what else moved what the
    accounts in `scope` hold, read as the module says."""
    by_account: defaultdict[str, Decimal] = defaultdict(Decimal)
    other: defaultdict[Other, Decimal] = defaultdict(Decimal)
    carried: defaultdict[Other, Decimal] = defaultdict(Decimal)
    unconverted = 0
    rows = 0
    unreviewed_in = unreviewed_out = ZERO
    unplaced = 0
    unplaced_out = unplaced_in = ZERO
    # Each position's cost, as the sheets keep it, so a holding given up without a lot gives up
    # its average cost here too: the whole history, as cost is made of all of it.
    positions: defaultdict[tuple[str, str], _Position] = defaultdict(_Position)
    for entry in _transactions(entries):
        if entry.date > period.end:
            break
        inside = [p for p in entry.postings if p.account in scope]
        if not inside:
            continue
        booked = [
            _book(positions[p.account, units(p, entry).currency], p, entry, prices, currency)
            for p in inside
        ]
        if entry.date < period.start:
            continue
        outside = [p for p in entry.postings if p.account not in scope]
        crosses = any(is_balance_sheet(p.account) for p in outside)
        equity = [p for p in outside if root(p.account) == "Equity"]
        opens = any(p.account not in WAITING for p in equity)
        weights = [weight(p, entry, prices, currency) for p in outside]
        unconverted += sum(1 for w in weights if w is None)
        unbooked = sum((b.unbooked for b in booked), ZERO)
        inner = [b.weight for b in booked]
        if all(w is not None for w in (*inner, *weights)):
            # An entry balances only to its tolerance, and what it leaves over is the books'.
            other[Other.ROUNDING] += sum((w for w in (*inner, *weights) if w is not None), ZERO)
        if crosses:
            other[Other.OUTSIDE] -= sum((w for w in weights if w is not None), ZERO)
            other[Other.OUTSIDE] += unbooked
        else:
            other[Other.REALISED] += unbooked
            placed = False
            for posting, w in zip(outside, weights, strict=True):
                if w is None:
                    continue
                if root(posting.account) in ("Income", "Expenses"):
                    by_account[posting.account] += -w if root(posting.account) == "Income" else w
                elif posting.account in WAITING:
                    other[Other.UNPLACED] -= w
                    placed = True
                    # It balances what moved: money out of the accounts waits as more here.
                    if w > 0:
                        unplaced_out += w
                    else:
                        unplaced_in -= w
                else:
                    other[Other.OPENING] -= w
            unplaced += placed
            if entry.flag == "!":
                rows += 1
                for posting, w in zip(outside, weights, strict=True):
                    if w is not None and root(posting.account) == "Income":
                        unreviewed_in -= w
                    elif w is not None and root(posting.account) == "Expenses":
                        unreviewed_out += w
        if crosses or opens:
            gain = sum(
                (
                    _carried(p, entry, prices, currency, b)
                    for p, b in zip(inside, booked, strict=True)
                ),
                ZERO,
            )
            carried[Other.OUTSIDE if crosses else Other.OPENING] += gain
    return Flows(
        period=period,
        by_account=dict(sorted(by_account.items())),
        kinds={account: kind(account) for account in by_account},
        other=dict(other),
        carried=dict(carried),
        unreviewed=Unreviewed(
            rows, unreviewed_in, unreviewed_out, unplaced, unplaced_out, unplaced_in
        ),
        unconverted=unconverted,
    )


def _carried(
    posting: Posting, entry: Transaction, prices: PriceLookup, currency: str, booked: _Booked
) -> Decimal:
    """The gain not yet realised that a posting moving a holding across the statement's edge
    carries with it: what it is worth that day less what it costs. Money carries none, and nor
    does a holding with no price that day, which is valued at its cost."""
    amount = units(posting, entry)
    if amount.currency == currency or booked.cost is None:
        return ZERO
    price = _price(prices, amount.currency, entry.date, currency)
    if price is None:
        return ZERO
    return amount.number * price - booked.cost


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
    """The change in gain not yet realised, less what moves carried; None at cost."""
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
    """What the market added or took away: the change in gain not yet realised, less the gain
    moves across the statement's edge carried in or out with them."""
    return closing.gain - opening.gain - sum(moved.carried.values(), ZERO)


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


def first_day(entries: Iterable[Directive], scope: Collection[str]) -> date | None:
    """The first day any account in `scope` moved."""
    return min(
        (
            e.date
            for e in entries
            if isinstance(e, Transaction) and any(p.account in scope for p in e.postings)
        ),
        default=None,
    )
