"""Statements, as one viewer may see them: the books read the way a business reads its own.

A statement reads the parts the viewer may see in full (``access.may_read_statements``): their
own accounts, joint accounts with their co-owners, and another member's only when that member
shares every facet. Every other part is left out of every statement and named ("without
Casey"), never added in at a coarser grain. The scope is decided once, before any figure is
worked out, and every figure on every tab, for every period and at cost or at market value, is
of that scope alone (``hearth.reports.statements``): so no figure, and no difference of two,
holds anything of a part left out.

Within the scope the rules are the books' own. A move between an account in the statement and
one outside it, another member's or one hearth does not see, is one amount, "moved to or from
accounts outside the statement", and none of its categories is counted. Accounts hearth does
not see are never valued (`books.unseen`); the statement says it is without them. While an
account's balance is not known yet, nothing that adds up balances is a figure.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from beancount.core.data import Directive

from hearth import figures
from hearth.accounts import Account
from hearth.categories import Kind
from hearth.disclosure.common import Household, LeftOut, PartRef, left_out
from hearth.disclosure.gate import Concealed
from hearth.figures import Part
from hearth.months import Month
from hearth.names import readable
from hearth.reports.common import root
from hearth.reports.statements import (
    Better,
    Bridge,
    Columns,
    Flows,
    Held,
    Ledger,
    Other,
    PayBooking,
    Period,
    Tone,
    Unreviewed,
    bridge,
    columns,
    movement,
    share,
    tone,
    total,
)
from hearth.valuation.holdings import holdings_at

__all__ = [
    "LABELS",
    "BalanceSheet",
    "Bridge",
    "Columns",
    "Held",
    "IncomeStatement",
    "Line",
    "Node",
    "Period",
    "Scope",
    "SheetRow",
    "Summary",
    "Total",
    "Unreviewed",
    "Valuing",
    "WithTax",
    "Years",
    "balance_sheet",
    "income_statement",
    "share",
    "summary",
    "years",
]

SPENDING_LINES = 6
"""How many of spending's largest branches the summary names; the rest are "Other spending"."""

MOST_COLUMNS = 36
"""The most columns an income statement is split into: three years of months."""

SPARSE_SHARE = Decimal("0.5")
"""A year is sparse when the books hold fewer than this share of its months in full (of those
it has had so far, for the year in progress): too little history to set against another."""

OTHER_SPENDING = "Other spending"

SPENDING = "Spending"
SPENDING_WITH_TAX = "Spending and tax"
"""Spending as the summary, Year by year and the income statement name it: without tax, and
with tax counted in it, which the app may show instead (`WithTax`)."""

PAY = "Take-home pay"
PAY_BEFORE_TAX = "Pay before tax"
PAY_MIXED = "Pay"
"""Pay as the books hold it is called what it is: where the scope books pay before what was
withheld from it (`pay_label`), "take-home" would not be true; anywhere else, pay is what
reached the bank, whatever tax was paid apart from it."""

SAVINGS_RATE = "Savings rate"
SAVINGS_RATE_AFTER_TAX = "Savings rate, after tax"
SAVINGS_RATE_WITH_TAX = "Savings rate, with tax"
"""The savings rate's name says how tax is counted, wherever the scope holds any: saved from
work over pay after tax, or over pay with tax counted as spending. Without tax in the scope
there is one rate, and it needs no saying."""

FLOOR = date(1900, 1, 1)
"""No statement starts before this, whatever the books hold: a day of a year with no year
before it to set against is no period anyone keeps books for."""

GROUPS = {
    "Assets": ("Cash", "Investments", "Other assets"),
    "Liabilities": ("Credit", "Other liabilities"),
}
"""How the balance sheet groups accounts, in this order. Each account falls in exactly one."""

LABELS: Mapping[Other, str] = {
    Other.OTHER_INCOME: "Other income",
    Other.NOT_SPENDING: "Not counted as spending",
    Other.OPENING: "Opening balances and adjustments",
    Other.UNPLACED: "Not placed yet",
    Other.OUTSIDE: "Moved to or from accounts outside the statement",
    Other.TAKEN: "Gains already counted in value, booked as income",
    Other.UNPRICED: "Holdings with no price, not valued",
    Other.REALISED: "Gains not booked as income",
    Other.ROUNDING: "Rounding the books allow",
}


@dataclass(frozen=True, slots=True)
class Scope:
    """The accounts a viewer's statements read, and what they leave out."""

    parts: tuple[Part, ...]
    refs: tuple[PartRef, ...]
    without: tuple[PartRef, ...]
    """Parts with accounts that the viewer may not see in full, named, in household order."""
    accounts: tuple[Account, ...]
    """Every account of the parts in it that hearth sees."""
    unknown: frozenset[str]
    """Of those, the ledger accounts whose balance the books do not know yet."""
    unseen: tuple[LeftOut, ...]
    """Each part in it that leaves out accounts hearth does not see, and how many."""

    @property
    def ledger(self) -> frozenset[str]:
        return frozenset(a.ledger_account for a in self.accounts)

    @property
    def known(self) -> bool:
        """Whether every balance in it is known, so that a sum of balances is a figure."""
        return not self.unknown

    @property
    def leaves_out(self) -> str:
        return left_out(self.unseen)


def scope(h: Household) -> Scope:
    parts = [p for p in h.parts if p.accounts]
    full = tuple(p for p in parts if h.gate.may_read_statements(p.owners))
    seen = tuple(a for p in full for a in h.seen(p))
    return Scope(
        parts=full,
        refs=tuple(h.part_ref(p) for p in full),
        without=tuple(h.part_ref(p) for p in parts if p not in full),
        accounts=seen,
        unknown=frozenset(a.ledger_account for a in seen) & h.balance_unknown,
        unseen=tuple(LeftOut(h.part_ref(p), h.unseen(p)) for p in full if h.unseen(p)),
    )


@dataclass(frozen=True, slots=True)
class Valuing:
    """How the holdings behind a statement were valued, in words a page names them by."""

    market: bool
    priced_on: date | None
    """The newest price day behind a market value."""
    unpriced: tuple[str, ...]
    """Holdings with no price, not valued at market value, as no screen values them: every
    market value they are in is a floor."""
    unvalued: tuple[str, ...]
    """Holdings with neither a price nor a cost, left out at cost and at market value."""
    stale: int
    """How many positions are valued at an old price (``networth.Freshness.STALE``)."""
    later: int = 0
    """How many amounts were booked before anything priced them, and converted on their own
    day at the first price there was."""


def _valuing(held: Sequence[Held], market: bool, end: Held) -> Valuing:
    """How `held` was valued, with the stale prices behind `end`, the figure a page shows."""
    every = total(held)
    return Valuing(
        market=market,
        priced_on=every.priced if market else None,
        unpriced=tuple(sorted(every.unpriced)) if market else (),
        unvalued=tuple(sorted(every.unvalued)),
        stale=end.stale if market else 0,
    )


@dataclass(frozen=True, slots=True)
class Reading:
    """One period of the books, over a statement's scope."""

    period: Period
    flows: Flows
    opening: dict[str, Held]
    closing: dict[str, Held]
    covered: bool
    """Whether the books hold every month of it in full for every part in the scope."""
    late: tuple[Account, ...] = ()
    """Accounts of the scope whose holdings the books know only from a day after the period
    began (`books.positioned`): what they held at its start is not known, never nothing."""

    @property
    def start(self) -> Held:
        return total(self.opening.values())

    @property
    def end(self) -> Held:
        return total(self.closing.values())


def ledger(h: Household) -> Ledger:
    """The books as statements read them, which the recompute read once (`Books.ledger`):
    never read again on an ask."""
    found = h.books.ledger
    if found is None:
        raise RuntimeError("books without a ledger: statements are read from a recompute's")
    return found


def period_of(h: Household, s: Scope, start: date | None, end: date | None) -> Period:
    """The days a statement is asked for: this year so far unless told, and never past the
    books' own day. One that starts after it, or before the year the viewer's statements
    begin in (or `FLOOR`), or ends before it, is not there: the floor is of what the viewer
    reads, so whether an ask is there says nothing of anyone else's history."""
    today = h.books.as_of
    began = ledger(h).first_day(s.ledger)
    floor = max(date(began.year, 1, 1) if began else date(today.year, 1, 1), FLOOR)
    first = start or date(today.year, 1, 1)
    last = min(end or today, today)
    if first < floor or first > last:
        raise Concealed
    return Period(first, last)


def read(h: Household, s: Scope, period: Period) -> Reading:
    """One period over the scope. Accounts whose holdings are known only from later than its
    start (`books.positioned`) are left out of what it compares, and named."""
    found = ledger(h)
    late = tuple(
        a for a in s.accounts if h.books.positioned.get(a.ledger_account, date.min) > period.before
    )
    held = frozenset(a.ledger_account for a in s.accounts if a not in late)
    return Reading(
        period=period,
        flows=found.flows(period, held, h.books.kind),
        opening=found.held(period.before, held),
        closing=found.held(period.end, held),
        covered=covered(h, s, period),
        late=late,
    )


def covered(h: Household, s: Scope, period: Period) -> bool:
    """Whether the books hold every month the period touches in full, for every part of the
    scope that can move income or spending (`figures.flows_cover`): by month alone, the same
    for every viewer of the part."""
    if not s.parts:
        return False
    months = _months(period)
    return all(
        figures.flows_cover(h.books, part.accounts, month)
        for part in s.parts
        if h.spends(part)
        for month in months
    )


def taxed(h: Household, s: Scope) -> bool:
    """Whether the scope's books hold any tax, over all their history: whether a figure with
    tax counted in it would ever differ from one without. Judged on the scope's own accounts
    alone, by what moved them, so it says nothing of any account or category outside it. A
    posting is enough: tax refunded to nothing over the years was still tax."""
    return any(h.books.kind(a) is Kind.TAX for a in ledger(h).books_to(s.ledger))


def pay_label(h: Household, s: Scope) -> str:
    """What pay is called over the scope (`Ledger.pay_booking`): "Pay before tax" where every
    pay posting is booked beside its tax, "Take-home pay" where none is, and plain "Pay" where
    the books do some of each, as one member's payslip beside another's take-home pay."""
    booking = ledger(h).pay_booking(s.ledger, h.books.kind)
    return {
        PayBooking.GROSS: PAY_BEFORE_TAX,
        PayBooking.NET: PAY,
        PayBooking.MIXED: PAY_MIXED,
    }[booking]


def _months(period: Period) -> list[Month]:
    found = [Month.of(period.start)]
    while found[-1] < Month.of(period.end):
        found.append(found[-1].following())
    return found


# -- Summary ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Line:
    """One line of a statement: this period and the one it is set against.

    `before` is None when the books do not hold the earlier period in full, as a figure from
    before the books' history is not one of zero. `categories` are the categories whose rows
    it adds up, for the journal; empty for a line that is no sum of rows.
    """

    key: str
    label: str
    now: Decimal | None
    before: Decimal | None
    tone: Tone
    categories: tuple[str, ...] = ()
    total: bool = False
    depth: int = 0

    @property
    def change(self) -> Decimal | None:
        if self.now is None or self.before is None:
            return None
        return self.now - self.before


@dataclass(frozen=True, slots=True)
class WithTax:
    """The figures that change when tax is counted as spending, each worked out here so that
    nothing is added up again to show them: spending with tax in it, the savings rate over pay
    with tax counted as spending, and the compact income statement with tax under spending,
    still its own line."""

    spending: Line
    savings_rate: Line
    lines: tuple[Line, ...]


@dataclass(frozen=True, slots=True)
class Summary:
    period: Period
    against: Period
    scope: Scope
    covered: bool
    against_covered: bool
    net_worth: Line
    """What the statement's accounts hold at the end, set against the start of the period."""
    savings_rate: Line
    """Saved from work over pay after tax."""
    spending: Line
    """Spending, without tax, which is its own line."""
    market: Decimal | None
    """What the market added or took away over the period, at market value; None while a
    balance is not known."""
    lines: tuple[Line, ...]
    bridge: Bridge | None
    """Why net worth changed; None while a balance is not known."""
    valuing: Valuing
    unreviewed: Unreviewed
    late: tuple[Account, ...] = ()
    """Accounts whose holdings the books know only from inside the period: net worth's start,
    and why it changed, are without them, and say so."""
    floor: bool = False
    """Whether net worth leaves out holdings with no price, so is at least what it says."""
    taxed: bool = False
    """Whether the scope's books hold any tax (`taxed`)."""
    with_tax: WithTax | None = None
    """The same figures with tax counted as spending; None when the scope holds no tax, as
    they would be these."""


@dataclass(frozen=True, slots=True)
class _Compared:
    """Two readings, this period and the same days a year before, that lines are made from."""

    now: Reading
    then: Reading

    def line(
        self,
        key: str,
        label: str,
        mine: Decimal | None,
        theirs: Decimal | None,
        better: Better,
        categories: Sequence[str] = (),
        *,
        total: bool = False,
        depth: int = 0,
    ) -> Line:
        """A line, with the earlier figure only where the books hold that period in full, and
        coloured only where they hold both."""
        earlier = theirs if self.then.covered else None
        heard = tone(mine, earlier, better) if self.now.covered else Tone.QUIET
        return Line(key, label, mine, earlier, heard, tuple(categories), total, depth)

    def of_kind(self, *kinds: Kind) -> tuple[str, ...]:
        """Every category of any of `kinds` either period books to."""
        found = {
            a
            for f in (self.now.flows, self.then.flows)
            for a in f.by_account
            if f.kinds[a] in kinds
        }
        return tuple(sorted(found))


def summary(h: Household, period: Period, *, market: bool) -> Summary:
    """This period against the same days a year before, over the viewer's statement scope."""
    s = scope(h)
    now = read(h, s, period)
    against = period.a_year_before()
    both = _Compared(now, read(h, s, against))
    n, t = now.flows, both.then.flows
    start, end = now.start, now.end
    whole = total(ledger(h).held(period.end, s.ledger).values())
    has_tax = taxed(h, s)
    rate = SAVINGS_RATE_AFTER_TAX if has_tax else SAVINGS_RATE
    return Summary(
        period=period,
        against=against,
        scope=s,
        covered=now.covered,
        against_covered=both.then.covered,
        net_worth=_since_start(
            whole.value(market) if s.known and not _late(h, s, period.end) else None,
            start.value(market) if s.known and now.covered and not now.late else None,
        ),
        savings_rate=both.line(
            "savings_rate", rate, n.savings_rate, t.savings_rate, Better.NEITHER
        ),
        spending=both.line(
            "spending", SPENDING, n.spending, t.spending, Better.LESS, both.of_kind(Kind.SPENDING)
        ),
        market=movement(start, end, n) if s.known else None,
        lines=_lines(both, with_tax=False, rate=rate, pay=pay_label(h, s)),
        bridge=bridge(start, end, n, market=market) if s.known else None,
        valuing=replace(_valuing([start, whole], market, whole), later=n.later),
        unreviewed=n.unreviewed,
        late=now.late,
        floor=market and whole.is_floor,
        taxed=has_tax,
        with_tax=_with_tax(both, pay_label(h, s)) if has_tax else None,
    )


def _with_tax(both: _Compared, pay: str) -> WithTax:
    n, t = both.now.flows, both.then.flows
    return WithTax(
        spending=both.line(
            "spending",
            SPENDING_WITH_TAX,
            n.spending_with_tax,
            t.spending_with_tax,
            Better.LESS,
            both.of_kind(Kind.SPENDING, Kind.TAX),
        ),
        savings_rate=both.line(
            "savings_rate",
            SAVINGS_RATE_WITH_TAX,
            n.savings_rate_with_tax,
            t.savings_rate_with_tax,
            Better.NEITHER,
        ),
        lines=_lines(both, with_tax=True, rate=SAVINGS_RATE_WITH_TAX, pay=pay),
    )


def _late(h: Household, s: Scope, day: date) -> bool:
    """Whether an account of the scope has holdings the books know only from after `day`."""
    return any(h.books.positioned.get(a.ledger_account, date.min) > day for a in s.accounts)


def _since_start(end: Decimal | None, start: Decimal | None) -> Line:
    """Net worth at the end of the period, set against its start, not a year before: the
    start only where the books hold the whole period, as before its history nothing was held
    that the books know of."""
    return Line("net_worth", "Net worth", end, start, tone(end, start, Better.MORE))


def _lines(both: _Compared, *, with_tax: bool, rate: str, pay: str) -> tuple[Line, ...]:
    """The compact income statement: take-home pay, tax, spending by its largest branches,
    saved from work, its rate, what was not counted as spending, investment income and other
    income, each set against the same days a year before.

    `with_tax` counts tax as spending: spending with tax in it, and tax the first line under
    it, so tax is still its own line and saved from work is the same either way."""
    n, t = both.now.flows, both.then.flows
    line = both.line
    found = [line("pay", pay, n.pay, t.pay, Better.MORE, both.of_kind(Kind.PAY))]
    tax = line(
        "tax",
        _tax_label(n.tax, t.tax),
        n.tax,
        t.tax,
        Better.LESS,
        both.of_kind(Kind.TAX),
        depth=int(with_tax),
    )
    if (n.tax or t.tax) and not with_tax:
        found.append(tax)
    found.append(
        line(
            "spending",
            SPENDING_WITH_TAX if with_tax else SPENDING,
            n.spending_with_tax if with_tax else n.spending,
            t.spending_with_tax if with_tax else t.spending,
            Better.LESS,
            both.of_kind(Kind.SPENDING, Kind.TAX) if with_tax else both.of_kind(Kind.SPENDING),
            total=True,
        )
    )
    if (n.tax or t.tax) and with_tax:
        found.append(tax)
    found += [
        line(key, label, a, b, Better.LESS, cats, depth=1)
        for key, label, a, b, cats in _branches(both.now, both.then)
    ]
    found.append(line("saved", "Saved from work", n.saved, t.saved, Better.MORE, total=True))
    found.append(
        line(
            "savings_rate",
            rate,
            n.savings_rate_with_tax if with_tax else n.savings_rate,
            t.savings_rate_with_tax if with_tax else t.savings_rate,
            Better.NEITHER,
        )
    )
    if n.not_spending or t.not_spending:
        found.append(
            line(
                "not_spending",
                LABELS[Other.NOT_SPENDING],
                n.not_spending,
                t.not_spending,
                Better.NEITHER,
                both.of_kind(Kind.NOT_SPENDING),
            )
        )
    found.append(
        line(
            "investment_income",
            "Investment income",
            n.investment_income,
            t.investment_income,
            Better.MORE,
            both.of_kind(Kind.INVESTMENT_INCOME),
        )
    )
    if n.other_income or t.other_income:
        found.append(
            line(
                "other_income",
                LABELS[Other.OTHER_INCOME],
                n.other_income,
                t.other_income,
                Better.NEITHER,
                both.of_kind(Kind.OTHER_INCOME),
            )
        )
    return tuple(found)


def _tax_label(*amounts: Decimal) -> str:
    """Tax, called what it is where any of `amounts` refunded more than was paid: a negative
    figure under "Tax, less refunds" is tax that came back."""
    return "Tax, less refunds" if any(a < 0 for a in amounts) else "Tax"


def _branches(
    now: Reading, then: Reading
) -> list[tuple[str, str, Decimal, Decimal, tuple[str, ...]]]:
    """Spending by the branch of Expenses each category is under, largest first: the first
    `SPENDING_LINES` by name, the rest as "Other spending", so every category is in one."""
    by: defaultdict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
    under: defaultdict[str, set[str]] = defaultdict(set)
    for i, f in enumerate((now.flows, then.flows)):
        for account, amount in f.by_account.items():
            if f.kinds[account] is Kind.SPENDING:
                branch = ":".join(account.split(":")[:2])
                by[branch][i] += amount
                under[branch].add(account)
    ranked = sorted(by, key=lambda b: (-by[b][0], -by[b][1], b))
    named, rest = ranked[:SPENDING_LINES], ranked[SPENDING_LINES:]
    if len(rest) == 1:
        named, rest = ranked, []
    found = [
        (f"spending:{b}", readable(b.split(":")[-1]), by[b][0], by[b][1], tuple(sorted(under[b])))
        for b in named
    ]
    if rest:
        found.append(
            (
                "spending:other",
                OTHER_SPENDING,
                sum((by[b][0] for b in rest), Decimal(0)),
                sum((by[b][1] for b in rest), Decimal(0)),
                tuple(sorted(c for b in rest for c in under[b])),
            )
        )
    return found


# -- Income statement -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Node:
    """One income or expense account and everything beneath it, a cell per column."""

    name: str
    """Its ledger name: a category, or a branch of them, which narrows the journal to it."""
    label: str
    kind: Kind | None
    """What kind of money it is, for an account the books book to; None for a branch."""
    cells: tuple[Decimal, ...]
    total: Decimal
    children: tuple[Node, ...]


@dataclass(frozen=True, slots=True)
class Column:
    period: Period
    partial: bool
    """Whether the period cuts the month, quarter or year short."""
    unreviewed: Unreviewed


@dataclass(frozen=True, slots=True)
class Total:
    """A figure the income statement adds up by kind, not by the account tree: a cell per
    column, the period's total, and the categories whose rows it adds up."""

    key: str
    label: str
    cells: tuple[Decimal, ...]
    total: Decimal
    categories: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IncomeStatement:
    period: Period
    scope: Scope
    covered: bool
    by: Columns
    columns: tuple[Column, ...]
    income: Node
    expenses: Node
    net: tuple[Decimal, ...]
    net_total: Decimal
    unreviewed: Unreviewed
    """Of the whole period."""
    later: int = 0
    """Amounts converted on their own day at a later first price (`Flows.later`)."""
    taxed: bool = False
    """Whether the scope's books hold any tax (`taxed`)."""
    totals: tuple[Total, ...] = ()
    """Spending without tax, tax, and spending with tax, each column worked out here."""


def income_statement(h: Household, period: Period, by: Columns) -> IncomeStatement:
    s = scope(h)
    books = h.books
    spans = columns(period, by)
    if len(spans) > MOST_COLUMNS:
        raise Concealed
    found = ledger(h)
    per = [found.flows(span, s.ledger, books.kind) for span in spans]
    whole = found.flows(period, s.ledger, books.kind)
    income = _tree("Income", per, whole)
    expenses = _tree("Expenses", per, whole)
    return IncomeStatement(
        period=period,
        scope=s,
        covered=covered(h, s, period),
        by=by,
        columns=tuple(
            Column(span, span != _whole(span, by), f.unreviewed)
            for span, f in zip(spans, per, strict=True)
        ),
        income=income,
        expenses=expenses,
        net=tuple(i - e for i, e in zip(income.cells, expenses.cells, strict=True)),
        net_total=income.total - expenses.total,
        unreviewed=whole.unreviewed,
        later=whole.later,
        taxed=taxed(h, s),
        totals=_totals(per, whole),
    )


def _totals(per: Sequence[Flows], whole: Flows) -> tuple[Total, ...]:
    """Spending, tax, and spending with tax in it, by column: each worked out here, so a page
    shows whichever it is asked for and adds nothing up."""

    def categories(*kinds: Kind) -> tuple[str, ...]:
        found = {a for f in (*per, whole) for a in f.by_account if f.kinds[a] in kinds}
        return tuple(sorted(found))

    return (
        Total(
            "spending",
            SPENDING,
            tuple(f.spending for f in per),
            whole.spending,
            categories(Kind.SPENDING),
        ),
        Total(
            "tax",
            _tax_label(whole.tax, *(f.tax for f in per)),
            tuple(f.tax for f in per),
            whole.tax,
            categories(Kind.TAX),
        ),
        Total(
            "spending_with_tax",
            SPENDING_WITH_TAX,
            tuple(f.spending_with_tax for f in per),
            whole.spending_with_tax,
            categories(Kind.SPENDING, Kind.TAX),
        ),
    )


def _whole(span: Period, by: Columns) -> Period:
    """The whole calendar month, quarter or year a column is of."""
    year = Period(date(span.start.year, 1, 1), date(span.start.year, 12, 31))
    return next(p for p in columns(year, by) if span.start in p)


def _tree(top: str, per: Sequence[Flows], whole: Flows) -> Node:
    accounts = sorted({a for f in (*per, whole) for a in f.by_account if root(a) == top})

    def node(name: str) -> Node:
        beneath = [a for a in accounts if a == name or a.startswith(name + ":")]
        children = sorted(
            {":".join(a.split(":")[: name.count(":") + 2]) for a in beneath if a != name}
        )
        kids = tuple(node(child) for child in children)
        cells = tuple(
            sum((f.by_account.get(a, Decimal(0)) for a in beneath), Decimal(0)) for f in per
        )
        return Node(
            name=name,
            label=readable(name.split(":")[-1]),
            kind=whole.kinds.get(name)
            or next((f.kinds[name] for f in per if name in f.kinds), None),
            cells=cells,
            total=sum((whole.by_account.get(a, Decimal(0)) for a in beneath), Decimal(0)),
            children=tuple(sorted(kids, key=lambda k: (-k.total, k.label))),
        )

    return node(top)


# -- Balance sheet ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SheetRow:
    """An account, or a group of them, at the start and the end of the period. `start` and
    `end` are None while a balance in it is not known."""

    label: str
    start: Held | None
    end: Held | None
    account: Account | None = None
    since: date | None = None
    """For an account whose history in the books begins inside the period, the day it does."""
    children: tuple[SheetRow, ...] = ()
    paid: tuple[Decimal | None, Decimal | None] = (None, None)
    """What it cost at the start and the end by its lots, as Holdings says it
    (``valuation.holdings.Holding.cost``): money at its units, every security by its lots at
    cost. None where any security is held with no lot, as a brokerage reports it, whose cost
    nothing says, or while a balance is not known."""


@dataclass(frozen=True, slots=True)
class BalanceSheet:
    period: Period
    scope: Scope
    market: bool
    assets: SheetRow
    liabilities: SheetRow
    net_worth: SheetRow
    valuing: Valuing
    taxed: bool = False
    """Whether the scope's books hold any tax (`taxed`): nothing here changes with it, but every
    statement says it of its scope alike."""


def balance_sheet(h: Household, period: Period, *, market: bool) -> BalanceSheet:
    s = scope(h)
    books = h.books
    found = ledger(h)
    opening, closing = found.held(period.before, s.ledger), found.held(period.end, s.ledger)
    paid = {
        day: _paid(books.entries, day, s.ledger, books.currency)
        for day in (period.before, period.end)
    }
    began = {c.account: c.opened for c in books.coverage}
    rows: defaultdict[str, list[SheetRow]] = defaultdict(list)
    for account in sorted(s.accounts, key=lambda a: a.name):
        name = account.ledger_account
        reported = books.positioned.get(name, date.min) > period.before
        if not ({name} & (opening.keys() | closing.keys() | s.unknown)) and not reported:
            continue
        unknown = name in s.unknown
        # What a brokerage held before the day it first reported is not known, not nothing.
        known_from = books.positioned.get(name, date.min)
        late = known_from > period.before
        start = None if unknown or late else opening.get(name, Held())
        end = None if unknown or known_from > period.end else closing.get(name, Held())
        opened = began.get(name)
        rows[_group(account, start, end, books.invested)].append(
            SheetRow(
                label=account.name,
                start=start,
                end=end,
                account=account,
                since=opened if opened is not None and opened > period.before else None,
                paid=(
                    None if unknown or late else paid[period.before].get(name, Decimal(0)),
                    None if end is None else paid[period.end].get(name, Decimal(0)),
                ),
            )
        )
    sides = {
        top: SheetRow(
            label=top,
            start=_sum([r.start for g in groups for r in rows[g]]),
            end=_sum([r.end for g in groups for r in rows[g]]),
            paid=_paid_sum([r for g in groups for r in rows[g]]),
            children=tuple(
                SheetRow(
                    label=g,
                    start=_sum([r.start for r in rows[g]]),
                    end=_sum([r.end for r in rows[g]]),
                    children=tuple(rows[g]),
                    paid=_paid_sum(rows[g]),
                )
                for g in groups
                if rows[g]
            ),
        )
        for top, groups in GROUPS.items()
    }
    assets, liabilities = sides["Assets"], sides["Liabilities"]
    return BalanceSheet(
        period=period,
        scope=s,
        market=market,
        assets=assets,
        liabilities=liabilities,
        net_worth=SheetRow(
            "Net worth",
            _sum([assets.start, liabilities.start]),
            _sum([assets.end, liabilities.end]),
            paid=_paid_sum([assets, liabilities]),
        ),
        valuing=_valuing([*opening.values(), *closing.values()], market, total(closing.values())),
        taxed=taxed(h, s),
    )


def _paid(
    entries: Sequence[Directive], day: date, scope: frozenset[str], currency: str
) -> dict[str, Decimal | None]:
    """What each account in `scope` cost at the end of `day` by its lots, from the holdings
    Holdings reads (`holdings_at`), so the two screens never disagree."""
    found: dict[str, Decimal | None] = {}
    for holding in holdings_at(entries, day):
        if holding.account not in scope:
            continue
        before = found.get(holding.account, Decimal(0))
        if holding.commodity == currency:
            cost: Decimal | None = holding.units
        elif holding.cost is not None and holding.cost.currency == currency:
            cost = holding.cost.number
        else:
            cost = None
        found[holding.account] = None if before is None or cost is None else before + cost
    return found


def _paid_sum(rows: Sequence[SheetRow]) -> tuple[Decimal | None, Decimal | None]:
    def one(index: int) -> Decimal | None:
        each = [r.paid[index] for r in rows]
        if any(p is None for p in each):
            return None
        return sum((p for p in each if p is not None), Decimal(0))

    return one(0), one(1)


def _sum(held: Sequence[Held | None]) -> Held | None:
    if any(h is None for h in held):
        return None
    return total(h for h in held if h is not None)


def _group(account: Account, start: Held | None, end: Held | None, invested: frozenset[str]) -> str:
    """Which group of the balance sheet an account is in: a brokerage, or one holding a
    security hearth can value, is an investment; one holding only money, cash or credit; and
    one holding only what hearth cannot value is "other", so nothing is ever left out."""
    held = [h for h in (start, end) if h is not None]
    valued = [h for h in held if h.cost or h.market or not h.unvalued]
    if root(account.ledger_account) == "Liabilities":
        money = all(not h.securities and not h.unvalued for h in held)
        return "Credit" if money else "Other liabilities"
    if account.ledger_account in invested or any(
        h.securities and (h.cost or h.market) for h in held
    ):
        return "Investments"
    if held and not valued:
        return "Other assets"
    return "Cash"


# -- Year by year -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Year:
    year: int
    partial: bool
    """The year in progress: so far, to the books' own day."""
    sparse: bool
    """The books hold too little of it to set against another (`SPARSE_SHARE`)."""
    net_worth: Decimal | None
    """At its end, or the books' own day; None while a balance is not known."""
    pay: Decimal
    spending: Decimal
    """Without tax, which is its own figure."""
    saved: Decimal
    savings_rate: Decimal | None
    """Saved from work over pay after tax."""
    tax: Decimal = Decimal(0)
    spending_with_tax: Decimal = Decimal(0)
    savings_rate_with_tax: Decimal | None = None
    """Saved from work over pay, tax counted as spending."""


@dataclass(frozen=True, slots=True)
class Years:
    scope: Scope
    market: bool
    years: tuple[Year, ...]
    taxed: bool = False
    """Whether the scope's books hold any tax (`taxed`)."""
    pay_label: str = PAY
    """What pay is called in every year (`pay_label`)."""


def years(h: Household, *, market: bool) -> Years:
    """Every year the books hold of the viewer's statement scope, oldest first."""
    s = scope(h)
    books = h.books
    books_read = ledger(h)
    first = books_read.first_day(s.ledger)
    if first is None:
        return Years(s, market, ())
    today = books.as_of
    spans = [
        Period(date(y, 1, 1), min(date(y, 12, 31), today))
        for y in range(first.year, today.year + 1)
    ]
    found = []
    for span in spans:
        moved = books_read.flows(span, s.ledger, books.kind)
        # A year that ended before an account's holdings were first reported has no net worth
        # the books know: never one without them that reads as whole.
        late = any(books.positioned.get(a, date.min) > span.end for a in s.ledger)
        months = _months(span)
        whole = sum(
            1
            for m in months
            if all(figures.flows_cover(books, p.accounts, m) for p in s.parts if h.spends(p))
        )
        found.append(
            Year(
                year=span.start.year,
                partial=span.end != date(span.start.year, 12, 31),
                sparse=Decimal(whole) < SPARSE_SHARE * len(months),
                net_worth=(
                    total(books_read.held(span.end, s.ledger).values()).value(market)
                    if s.known and not late
                    else None
                ),
                pay=moved.pay,
                spending=moved.spending,
                saved=moved.saved,
                savings_rate=moved.savings_rate,
                tax=moved.tax,
                spending_with_tax=moved.spending_with_tax,
                savings_rate_with_tax=moved.savings_rate_with_tax,
            )
        )
    return Years(s, market, tuple(found), taxed=taxed(h, s), pay_label=pay_label(h, s))


def within(h: Household) -> frozenset[str]:
    """The ledger accounts the viewer's statements read, for narrowing the journal to exactly
    the rows a statement's figure adds up."""
    return scope(h).ledger
