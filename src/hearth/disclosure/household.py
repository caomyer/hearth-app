"""The household dashboard, as one viewer may see it.

Every household figure here is a sum over parts, and only over parts the viewer may see that
figure of. When a part is withheld the figure says "without" it and names it, so a partial sum
never poses as the whole; and because each part in the sum is one the viewer may see on its
own, nothing shown can be taken apart into something withheld.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from hearth import figures
from hearth.disclosure.common import Household, PartRef, Worth, is_withheld, stale_positions
from hearth.disclosure.gate import Disclosed, Withheld
from hearth.figures import MonthFlows, Part
from hearth.months import Month
from hearth.reports.flows import Owners
from hearth.sharing import DESCRIPTIONS, Facet
from hearth.valuation.networth import NetWorth

CATEGORY_ROWS = 8
OVER_TYPICAL_SHARE = Decimal("0.25")
OVER_TYPICAL_AMOUNT = Decimal(50)


@dataclass(frozen=True, slots=True)
class Headline:
    """A household total over the parts the viewer may see, and what it leaves out."""

    value: Decimal
    unpriced: tuple[str, ...]
    without: tuple[PartRef, ...]
    since_month_start: Decimal | None
    over_year: Decimal | None
    priced_on: date | None
    stale: int
    """Stale prices among holdings the viewer may see; others' holdings are not counted, so
    "except one" never says anything about positions their owner keeps private."""

    @property
    def is_partial(self) -> bool:
        return bool(self.without)

    @property
    def is_floor(self) -> bool:
        return bool(self.unpriced)


@dataclass(frozen=True, slots=True)
class Slice:
    part: PartRef
    value: Decimal


@dataclass(frozen=True, slots=True)
class MonthPoint:
    day: date
    value: Decimal
    is_floor: bool


@dataclass(frozen=True, slots=True)
class MonthBar:
    month: Month
    spent: Decimal
    covered: bool


@dataclass(frozen=True, slots=True)
class SpendingPanel:
    month: Month
    to_day: int
    spent: Decimal
    spent_without: tuple[PartRef, ...]
    earned: Decimal
    earned_without: tuple[PartRef, ...]
    saved_rate: Decimal | None
    saved_month: Month
    saved_without: tuple[PartRef, ...]
    bars: tuple[MonthBar, ...]
    typical: Decimal | None
    leaves_out_unassigned: bool
    """Some spending went through accounts nobody owns, or several owners at once. It is in
    no part, so it is not counted, and its amount is not shown: it may be anyone's."""


@dataclass(frozen=True, slots=True)
class CategoryRow:
    name: str
    amount: Decimal
    typical: Decimal | None

    @property
    def over_by(self) -> Decimal | None:
        """How far above typical-by-this-date the category is, when it is notably above.

        Nothing is "over" a typical of nothing: a category usually paid later in the month
        has a typical of zero by now, and paying it early is not overspending.
        """
        if self.typical is None or self.typical <= 0:
            return None
        over = self.amount - self.typical
        if over > OVER_TYPICAL_AMOUNT and over > self.typical * OVER_TYPICAL_SHARE:
            return over
        return None


@dataclass(frozen=True, slots=True)
class FixedRow:
    """A bill paid once a month at a steady amount, and when it was paid this month."""

    name: str
    amount: Decimal
    paid_on: date


@dataclass(frozen=True, slots=True)
class CategoryPanel:
    month: Month
    to_day: int
    timed: bool
    """Whether rows carry typical by this date, which only a viewer of the transactions gets."""
    fixed: tuple[FixedRow, ...]
    rows: tuple[CategoryRow, ...]
    totals_only: tuple[Slice, ...]
    unreviewed: int

    @property
    def scale(self) -> Decimal:
        """The largest amount or typical on the panel, so every bar shares one scale."""
        values = [r.amount for r in self.rows] + [r.typical for r in self.rows if r.typical]
        return max(values, default=Decimal(0))

    @property
    def has_typical(self) -> bool:
        """Whether any row has a typical to compare with, so a tick is drawn somewhere."""
        return any(row.typical is not None for row in self.rows)

    @property
    def most_over(self) -> CategoryRow | None:
        over = [row for row in self.rows if row.over_by is not None]
        return max(over, key=lambda row: row.over_by or 0, default=None)


@dataclass(frozen=True, slots=True)
class MemberRow:
    part: PartRef
    shares: tuple[str, ...]
    net_worth: Disclosed[Worth]
    change: Disclosed[Decimal | None]
    accounts: int | None
    connected: bool


@dataclass(frozen=True, slots=True)
class HouseholdView:
    headline: Headline
    composition: tuple[Slice, ...]
    history: tuple[MonthPoint, ...]
    spending: SpendingPanel
    categories: CategoryPanel
    members: tuple[MemberRow, ...]
    joint: tuple[MemberRow, ...]


def household(h: Household) -> HouseholdView:
    parts = [part for part in h.parts if part.accounts]
    this_month = figures.month_flows(h.books, h.ownership, h.books.month, to_day=h.books.as_of.day)
    return HouseholdView(
        headline=headline(h, parts),
        composition=_composition(h, parts),
        history=_history(h, parts),
        spending=_spending(h, parts, this_month),
        categories=_categories(h, parts, this_month),
        members=tuple(_member_row(h, part) for part in h.parts if not part.is_joint),
        joint=tuple(_member_row(h, part) for part in h.parts if part.is_joint),
    )


def headline(h: Household, parts: Sequence[Part]) -> Headline:
    """Household net worth over the parts whose net worth the viewer may see."""
    shown = [(part, h.disclosed_worth(part)) for part in parts]
    visible = [(part, worth) for part, worth in shown if isinstance(worth, Worth)]
    return Headline(
        value=sum((worth.value for _, worth in visible), Decimal(0)),
        unpriced=tuple(
            f"{h.part_ref(part).name}: {missing}"
            for part, worth in visible
            for missing in worth.unpriced
        ),
        without=tuple(h.part_ref(part) for part, worth in shown if is_withheld(worth)),
        since_month_start=_change(h, [p for p, _ in visible], h.month_start_valuation),
        over_year=_change(h, [p for p, _ in visible], h.books.month_ends[0]),
        priced_on=max(
            (
                position.quote.as_of
                for part, _ in visible
                for position in figures.worth(h.books.today, part).positions
                if position.quote is not None
            ),
            default=None,
        ),
        stale=sum(
            stale_positions(figures.worth(h.books.today, part))
            for part, _ in visible
            if h.gate.may(Facet.ACCOUNT_BALANCES, part.owners)
        ),
    )


def _change(h: Household, parts: Sequence[Part], since: NetWorth) -> Decimal | None:
    changes = [h.change_since(part, since) for part in parts]
    if any(change is None or isinstance(change, Withheld) for change in changes):
        return None
    return sum((c for c in changes if isinstance(c, Decimal)), Decimal(0))


def _composition(h: Household, parts: Sequence[Part]) -> tuple[Slice, ...]:
    """Who holds what. Drawn only when the total is whole: a bar of the known parts alone
    would draw each of them as a bigger share of the household than they are."""
    slices = []
    for part in parts:
        worth = h.disclosed_worth(part)
        if not isinstance(worth, Worth) or worth.is_floor:
            return ()
        slices.append(Slice(h.part_ref(part), worth.value))
    return tuple(slices)


def _history(h: Household, parts: Sequence[Part]) -> tuple[MonthPoint, ...]:
    """Net worth at each month end and today, over the same parts as the headline, so the
    trend compares like with like rather than jumping when a member withdraws."""
    visible = [part for part in parts if h.gate.may(Facet.NET_WORTH_TOTAL, part.owners)]
    valuations = [*h.books.month_ends, h.books.today]
    points = []
    for valuation in valuations:
        worths = [figures.worth(valuation, part) for part in visible]
        points.append(
            MonthPoint(
                day=valuation.as_of,
                value=sum((w.known_total for w in worths), Decimal(0)),
                is_floor=any(not w.complete for w in worths),
            )
        )
    return tuple(points)


def _split(
    h: Household, parts: Sequence[Part], facets: Sequence[Facet]
) -> tuple[list[Part], tuple[PartRef, ...]]:
    """The parts the viewer may see every one of `facets` of, and the rest, named."""
    shown = [p for p in parts if all(h.gate.may(facet, p.owners) for facet in facets)]
    hidden = tuple(h.part_ref(p) for p in parts if p not in shown)
    return shown, hidden


def _spending(h: Household, parts: Sequence[Part], this_month: MonthFlows) -> SpendingPanel:
    spenders, spent_without = _split(h, parts, [Facet.MONTHLY_SPENDING_TOTAL])
    earners, earned_without = _split(h, parts, [Facet.MONTHLY_INCOME_TOTAL])
    savers, saved_without = _split(
        h, parts, [Facet.MONTHLY_SPENDING_TOTAL, Facet.MONTHLY_INCOME_TOTAL]
    )
    month = h.books.month
    past = figures.past_months(month, 12)
    full = {m: figures.month_flows(h.books, h.ownership, m) for m in past}
    last = full[past[-1]]
    earned_last = sum((last.earned(p.owners) for p in savers), Decimal(0))
    spent_last = sum((last.spent(p.owners) for p in savers), Decimal(0))
    bars = [
        MonthBar(m, _sum(full[m], spenders, MonthFlows.spent), _covers(h, spenders, m))
        for m in past
    ]
    bars.append(MonthBar(month, _sum(this_month, spenders, MonthFlows.spent), True))
    return SpendingPanel(
        month=month,
        to_day=h.books.as_of.day,
        spent=_sum(this_month, spenders, MonthFlows.spent),
        spent_without=spent_without,
        earned=_sum(this_month, earners, MonthFlows.earned),
        earned_without=earned_without,
        saved_rate=(earned_last - spent_last) / earned_last if earned_last > 0 else None,
        saved_month=past[-1],
        saved_without=saved_without,
        bars=tuple(bars),
        typical=figures.typical([bar.spent for bar in bars[:-1] if bar.covered]),
        leaves_out_unassigned=bool(this_month.spending.unattributed),
    )


def _sum(
    flows: MonthFlows, parts: Sequence[Part], measure: Callable[[MonthFlows, Owners], Decimal]
) -> Decimal:
    return sum((measure(flows, part.owners) for part in parts), Decimal(0))


def _covers(h: Household, parts: Sequence[Part], month: Month) -> bool:
    return figures.covers(h.books, [a for part in parts for a in part.accounts], month)


def _categories(h: Household, parts: Sequence[Part], this_month: MonthFlows) -> CategoryPanel:
    """Spending by category from parts that share it; parts that share only the total appear
    as one labelled row each, so the panel adds up without opening up their categories."""
    detailed, _ = _split(h, parts, [Facet.CATEGORY_BREAKDOWN])
    totals = [
        p for p in parts if p not in detailed and h.gate.may(Facet.MONTHLY_SPENDING_TOTAL, p.owners)
    ]
    return category_panel(h, detailed, totals, this_month)


def category_panel(
    h: Household, detailed: Sequence[Part], totals: Sequence[Part], this_month: MonthFlows
) -> CategoryPanel:
    """Categories of `detailed`, and `totals` as one row each.

    The caller has already decided the viewer may see what each part contributes. Typical by
    this date and the day a fixed bill was paid are day-level detail, finer than a category a
    month, so they are worked out only when the viewer may see the transactions of every part
    in the rows; otherwise each category is its month-to-date amount alone.
    """
    today = h.books.as_of.day
    owners = [part.owners for part in detailed]
    timed = all(h.gate.may(Facet.TRANSACTIONS, part.owners) for part in detailed)
    fixed: dict[str, figures.Payment] = {}
    history: list[MonthFlows] = []
    if timed:
        bills = figures.fixed_categories(
            h.books,
            h.ownership,
            owners,
            [m for m in figures.past_months(h.books.month, 6) if _covers(h, detailed, m)],
        )
        payments = figures.category_payments(h.books, h.ownership, owners, h.books.month)
        fixed = {
            category: paid[0] for category in bills if len(paid := payments.get(category, [])) == 1
        }
        history = [
            figures.month_flows(h.books, h.ownership, m, to_day=today)
            for m in figures.past_months(h.books.month, figures.TYPICAL_MONTHS)
            if _covers(h, detailed, m)
        ]
    rows = [
        CategoryRow(
            name=_category_name(category),
            amount=amount,
            typical=figures.typical([_category(flows, detailed, category) for flows in history]),
        )
        for category, amount in _by_category(this_month, detailed).items()
        if category not in fixed
    ]
    rows.sort(key=lambda row: row.amount, reverse=True)
    return CategoryPanel(
        month=h.books.month,
        to_day=today,
        timed=timed,
        fixed=tuple(
            FixedRow(_category_name(category), fixed[category].amount, fixed[category].day)
            for category in sorted(fixed)
        ),
        rows=tuple(rows[:CATEGORY_ROWS]) + _other(rows[CATEGORY_ROWS:]),
        totals_only=tuple(Slice(h.part_ref(p), this_month.spent(p.owners)) for p in totals),
        unreviewed=sum(this_month.unreviewed.get(p.owners, 0) for p in detailed),
    )


def _by_category(flows: MonthFlows, parts: Sequence[Part]) -> dict[str, Decimal]:
    total: dict[str, Decimal] = {}
    for part in parts:
        for category, amount in flows.spent_by_category(part.owners).items():
            total[category] = total.get(category, Decimal(0)) + amount
    return total


def _category(flows: MonthFlows, parts: Sequence[Part], category: str) -> Decimal:
    return _by_category(flows, parts).get(category, Decimal(0))


def _other(rest: Sequence[CategoryRow]) -> tuple[CategoryRow, ...]:
    if not rest:
        return ()
    return (CategoryRow("Other", sum((row.amount for row in rest), Decimal(0)), None),)


def _category_name(account: str) -> str:
    """ "Expenses:Food:Dining" reads as "Dining": the most specific name is the useful one."""
    return account.rsplit(":", 1)[-1]


def _member_row(h: Household, part: Part) -> MemberRow:
    sees_accounts = h.gate.may(Facet.ACCOUNT_BALANCES, part.owners)
    shares: tuple[str, ...] = ()
    if not part.is_joint:
        member = next(iter(part.owners))
        shared = h.gate.shared_by(member)
        shares = tuple(DESCRIPTIONS[facet].tag for facet in Facet if facet in shared)
    return MemberRow(
        part=h.part_ref(part),
        shares=shares,
        net_worth=h.disclosed_worth(part),
        change=h.change_since(part, h.month_start_valuation),
        accounts=len(part.accounts) if sees_accounts else None,
        connected=bool(part.accounts),
    )
