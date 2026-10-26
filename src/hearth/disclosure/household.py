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
from hearth.disclosure.common import (
    DataEnds,
    Household,
    LeftOut,
    PartRef,
    Unreviewed,
    Worth,
    is_withheld,
    stale_positions,
    unreviewed,
)
from hearth.disclosure.gate import Disclosed, Withheld
from hearth.figures import MonthFlows, Part
from hearth.months import Month
from hearth.names import readable
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
    ends: DataEnds | None
    """Where the data behind the total stops, over the parts it adds up."""
    awaiting: int | None = 0
    """Accounts behind the total whose balance the books do not hold yet; while there are
    any, the total is not known (`Worth.awaiting`). None when a part with some withholds
    its count, so the total never sums a count the viewer may not see."""
    unseen: tuple[LeftOut, ...] = ()
    """Each part behind the total that leaves out accounts hearth does not see, with how
    many where the viewer may see that part's balances (`Worth.unseen`). The total is what
    the rest add up to, and says it is without them."""
    counted: bool = True
    """Whether any account behind the total is one hearth sees (`Worth.counted`)."""

    @property
    def is_known(self) -> bool:
        return self.awaiting == 0 and self.counted

    @property
    def leaves_out(self) -> bool:
        return bool(self.unseen)

    @property
    def is_partial(self) -> bool:
        return bool(self.without)

    @property
    def is_floor(self) -> bool:
        return bool(self.unpriced)


@dataclass(frozen=True, slots=True)
class Slice:
    part: PartRef
    value: Decimal | None
    """None when the figure is not known: it still takes its place, as its absence would say
    something of the part the figure does not."""


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
    covered: bool
    """Whether the ledger reaches this month for every part behind `spent` and `earned`;
    if not, they are not this month's figures, only what the ledger holds of it so far."""
    spent: Decimal
    spent_without: tuple[PartRef, ...]
    spent_unreviewed: Unreviewed
    earned: Decimal
    earned_without: tuple[PartRef, ...]
    earned_unreviewed: Unreviewed
    saved_rate: Decimal | None
    """Last month's saving as a share of its income; None when there was no income, or when
    whether there was is not known (`saved_covered`)."""
    saved_month: Month
    saved_without: tuple[PartRef, ...]
    saved_covered: bool
    """Whether last month is known in full for every part behind `saved_rate`, so that no
    income in it is an established fact rather than history that has not come in."""
    bars: tuple[MonthBar, ...]
    typical: Decimal | None
    leaves_out_unassigned: bool
    """Some spending went through accounts nobody owns, or several owners at once. It is in
    no part, so it is not counted, and its amount is not shown: it may be anyone's."""
    spent_counted: bool = True
    """Whether any part behind `spent` has an account that could show spending. When none
    has, the sum is of nothing that says what was spent, not a month in which nothing was, so
    it is not a figure. What kind each account is goes with its balance, so only a viewer who
    may see every such part's balances is told so; anyone else reads the figure as not known
    (`covered`), as though those accounts had not sent their history yet."""
    earned_counted: bool = True
    """`spent_counted`, for `earned`."""

    @property
    def charted(self) -> bool:
        """Whether any month is known in full. Until one is, a chart would draw months nobody
        knows as months of nothing, so none is drawn; nor while no account is behind it."""
        return self.spent_counted and any(bar.covered for bar in self.bars)


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
    unreviewed: Unreviewed
    """What of the categories shown rests on unreviewed rows, at their suggested category."""

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
    priced_from: date | None
    """Where `history` starts when earlier month ends had no prices to value them at."""
    spending: SpendingPanel
    categories: CategoryPanel
    members: tuple[MemberRow, ...]
    joint: tuple[MemberRow, ...]

    @property
    def connected(self) -> bool:
        """Whether anyone in the household has an account in the books yet. Until someone
        does, there is no figure to show: a total over nothing is not a total of zero."""
        return any(row.connected for row in (*self.members, *self.joint))


def household(h: Household) -> HouseholdView:
    parts = [part for part in h.parts if part.accounts]
    this_month = figures.month_flows(h.books, h.ownership, h.books.month, to_day=h.books.as_of.day)
    return HouseholdView(
        headline=headline(h, parts),
        composition=_composition(h, parts),
        history=_history(h, parts),
        priced_from=h.priced_from(_charted(h, parts)),
        spending=_spending(h, parts, this_month),
        categories=_categories(h, parts, this_month),
        members=tuple(_member_row(h, part) for part in h.parts if not part.is_joint),
        joint=tuple(_member_row(h, part) for part in h.parts if part.is_joint),
    )


def _awaiting(worths: Sequence[Worth]) -> int | None:
    counts = [worth.awaiting for worth in worths]
    return None if None in counts else sum(count for count in counts if count is not None)


def headline(h: Household, parts: Sequence[Part]) -> Headline:
    """Household net worth over the parts whose net worth the viewer may see."""
    shown = [(part, h.disclosed_worth(part)) for part in parts]
    visible = [(part, worth) for part, worth in shown if isinstance(worth, Worth)]
    return Headline(
        ends=h.ends([part for part, _ in visible]),
        value=sum((worth.value for _, worth in visible), Decimal(0)),
        awaiting=_awaiting([worth for _, worth in visible]),
        unpriced=tuple(
            f"{h.part_ref(part).name}: {missing}"
            for part, worth in visible
            for missing in worth.unpriced
        ),
        without=tuple(h.part_ref(part) for part, worth in shown if is_withheld(worth)),
        unseen=tuple(
            LeftOut(h.part_ref(part), worth.unseen) for part, worth in visible if worth.leaves_out
        ),
        counted=not visible or any(worth.counted for _, worth in visible),
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
    would draw each of them as a bigger share of the household than they are, and so would
    a bar of parts without the accounts hearth does not see."""
    slices = []
    for part in parts:
        worth = h.disclosed_worth(part)
        if not isinstance(worth, Worth) or worth.is_floor or not worth.is_known or worth.leaves_out:
            return ()
        slices.append(Slice(h.part_ref(part), worth.value))
    return tuple(slices)


def _history(h: Household, parts: Sequence[Part]) -> tuple[MonthPoint, ...]:
    """Net worth at each month end and today, over the same parts as the headline, so the
    trend compares like with like rather than jumping when a member withdraws."""
    visible = _charted(h, parts)
    points = []
    for valuation in h.history(visible):
        worths = [figures.worth(valuation, part) for part in visible]
        points.append(
            MonthPoint(
                day=valuation.as_of,
                value=sum((w.known_total for w in worths), Decimal(0)),
                is_floor=any(not w.complete for w in worths),
            )
        )
    return tuple(points)


def _charted(h: Household, parts: Sequence[Part]) -> list[Part]:
    return [part for part in parts if h.gate.may(Facet.NET_WORTH_TOTAL, part.owners)]


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
    saved_covered = _covers(h, savers, past[-1])
    earned_last = sum((last.earned(p.owners) for p in savers), Decimal(0))
    spent_last = sum((last.spent(p.owners) for p in savers), Decimal(0))
    bars = [
        MonthBar(m, _sum(full[m], spenders, MonthFlows.spent), _covers(h, spenders, m))
        for m in past
    ]
    bars.append(
        MonthBar(month, _sum(this_month, spenders, MonthFlows.spent), _covers(h, spenders, month))
    )
    return SpendingPanel(
        month=month,
        to_day=h.books.as_of.day,
        covered=_covers(h, [*spenders, *earners], month),
        spent=_sum(this_month, spenders, MonthFlows.spent),
        spent_without=spent_without,
        spent_unreviewed=_unreviewed(h, spenders, this_month, MonthFlows.spent_unreviewed),
        earned=_sum(this_month, earners, MonthFlows.earned),
        earned_without=earned_without,
        earned_unreviewed=_unreviewed(h, earners, this_month, MonthFlows.earned_unreviewed),
        saved_rate=(
            (earned_last - spent_last) / earned_last if saved_covered and earned_last > 0 else None
        ),
        saved_month=past[-1],
        saved_without=saved_without,
        saved_covered=saved_covered,
        bars=tuple(bars),
        typical=figures.typical([bar.spent for bar in bars[:-1] if bar.covered]),
        leaves_out_unassigned=bool(this_month.spending.unattributed),
        spent_counted=any(h.spends(part) for part in spenders) or not _explained(h, spenders),
        earned_counted=any(h.spends(part) for part in earners) or not _explained(h, earners),
    )


def _sum(
    flows: MonthFlows, parts: Sequence[Part], measure: Callable[[MonthFlows, Owners], Decimal]
) -> Decimal:
    return sum((measure(flows, part.owners) for part in parts), Decimal(0))


def _covers(h: Household, parts: Sequence[Part], month: Month) -> bool:
    """Whether every part behind a figure is whole for `month`. One whose accounts cannot show
    spending or income, as brokerages cannot, adds nothing to either; but that is what kind
    its accounts are, so it adds nothing only for a viewer who may see its balances. To anyone
    else it is not known, as a part whose accounts have sent no history yet is."""
    return all(
        figures.flows_cover(h.books, part.accounts, month)
        if h.spends(part)
        else _explained(h, [part])
        for part in parts
    )


def _explained(h: Household, parts: Sequence[Part]) -> bool:
    """Whether the viewer may be told what kind of accounts `parts` have: only with their
    balances (`Totals.spends`)."""
    return all(h.gate.may(Facet.ACCOUNT_BALANCES, part.owners) for part in parts)


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
    by_category = _by_category(this_month, detailed)
    names = category_names([*by_category, *fixed])
    rows = [
        CategoryRow(
            name=names[category],
            amount=amount,
            typical=figures.typical([_category(flows, detailed, category) for flows in history]),
        )
        for category, amount in by_category.items()
        if category not in fixed
    ]
    rows.sort(key=lambda row: row.amount, reverse=True)
    return CategoryPanel(
        month=h.books.month,
        to_day=today,
        timed=timed,
        fixed=tuple(
            FixedRow(names[category], fixed[category].amount, fixed[category].day)
            for category in sorted(fixed)
        ),
        rows=tuple(rows[:CATEGORY_ROWS]) + _other(rows[CATEGORY_ROWS:]),
        # A part that cannot have spent anything hearth sees has no total to show, not $0.
        totals_only=tuple(
            Slice(h.part_ref(p), this_month.spent(p.owners) if h.spends(p) else None)
            for p in totals
        ),
        unreviewed=_unreviewed(h, [*detailed, *totals], this_month, MonthFlows.spent_unreviewed),
    )


def _unreviewed(
    h: Household,
    parts: Sequence[Part],
    flows: MonthFlows,
    measure: Callable[[MonthFlows, Owners], Decimal],
) -> Unreviewed:
    return unreviewed(
        h.gate,
        parts,
        h.part_ref,
        lambda part: measure(flows, part.owners),
        lambda part: flows.unreviewed.get(part.owners, 0),
    )


def _by_category(flows: MonthFlows, parts: Sequence[Part]) -> dict[str, Decimal]:
    total: dict[str, Decimal] = {}
    for part in parts:
        for category, amount in flows.spent_by_category(part.owners).items():
            total[category] = total.get(category, Decimal(0)) + amount
    return total


def _category(flows: MonthFlows, parts: Sequence[Part], category: str) -> Decimal:
    return _by_category(flows, parts).get(category, Decimal(0))


OTHER = "Other"


def _other(rest: Sequence[CategoryRow]) -> tuple[CategoryRow, ...]:
    if not rest:
        return ()
    return (CategoryRow(OTHER, sum((row.amount for row in rest), Decimal(0)), None),)


def category_names(accounts: Sequence[str]) -> dict[str, str]:
    """What each category reads as on one panel: its most specific name, made unambiguous.

    "Expenses:Food:Dining" reads as "Dining". Two categories with the same last name, such as
    "Transport:Other" and "Travel:Other", each keep as many parent names as tell them apart,
    and none is ever just "Other", which is the panel's row for everything left over.
    """
    names = {}
    for account in accounts:
        parts = account.split(":")[1:] or [account]
        for depth in range(1, len(parts) + 1):
            name = parts[-depth:]
            clashes = any(
                other != account and other.split(":")[-depth:] == name for other in accounts
            )
            if not clashes and name != [OTHER]:
                break
        if name == [OTHER]:
            name = account.split(":")
        names[account] = ": ".join(readable(part) for part in name)
    return names


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
