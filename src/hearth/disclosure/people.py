"""One member's finances: in full for the member themself, as shared for anyone else.

A member's own page and someone else's are built from the same pieces. The difference is only
what the gate allows: the owner of a part may see every facet of it, so the owner's page is
simply the case where nothing is withheld.
"""

import calendar
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from hearth import figures
from hearth.accounts import Account
from hearth.disclosure.common import Household, MemberRef, PartRef, Worth
from hearth.disclosure.gate import Concealed, Disclosed
from hearth.disclosure.household import CategoryPanel, category_panel
from hearth.figures import Line, MonthFlows, Part
from hearth.members import Member
from hearth.months import Month
from hearth.reports.common import root
from hearth.reports.coverage import AccountCoverage, Span
from hearth.sharing import DESCRIPTIONS, Facet, FacetDescription
from hearth.valuation.networth import Freshness, Valued


@dataclass(frozen=True, slots=True)
class AccountRow:
    id: str
    name: str
    kind: str
    owners: tuple[MemberRef, ...]
    value: Worth
    stale: bool
    spans: tuple[Span, ...]
    gaps: int
    updated: date | None

    @property
    def is_owed(self) -> bool:
        return self.value.value < 0


ACCOUNT_KINDS = ("Cash", "Credit", "Investments")


@dataclass(frozen=True, slots=True)
class Day:
    day: date
    spent: Decimal
    lines: tuple[Line, ...]


@dataclass(frozen=True, slots=True)
class Totals:
    """A month so far: what was spent and earned, and how that compares with typical."""

    month: Month
    to_day: int
    spent: Disclosed[Decimal]
    earned: Disclosed[Decimal]
    payments_in: Disclosed[int]
    typical_spent: Decimal | None

    @property
    def over_typical(self) -> Decimal | None:
        if self.typical_spent is None or not isinstance(self.spent, Decimal):
            return None
        return self.spent - self.typical_spent


@dataclass(frozen=True, slots=True)
class Joint:
    """A joint group the viewer may see into, with its accounts and this month's lines."""

    part: PartRef
    accounts: Disclosed[tuple[AccountRow, ...]]
    total: Disclosed[Worth]
    days: Disclosed[tuple[Day, ...]]


@dataclass(frozen=True, slots=True)
class PersonView:
    member: MemberRef
    is_viewer: bool
    connected: bool
    shares: tuple[tuple[FacetDescription, bool], ...]
    worth: Disclosed[Worth]
    change: Disclosed[Decimal | None]
    totals: Totals
    pace: Disclosed[figures.Pace]
    categories: Disclosed[CategoryPanel]
    accounts: Disclosed[tuple[AccountRow, ...]]
    days: Disclosed[tuple[Day, ...]]
    joint: tuple[Joint, ...]

    @property
    def withheld(self) -> tuple[FacetDescription, ...]:
        return tuple(description for description, shared in self.shares if not shared)

    @property
    def joint_total(self) -> Decimal:
        return sum((j.total.value for j in self.joint if isinstance(j.total, Worth)), Decimal(0))


def person(h: Household, username: str) -> PersonView:
    member = next((m for m in h.members if m.username == username), None)
    if member is None:
        raise Concealed
    return person_of(h, member)


def person_of(h: Household, member: Member) -> PersonView:
    part = h.solo(member)
    gate = h.gate
    this_month = figures.month_flows(h.books, h.ownership, h.books.month, to_day=h.books.as_of.day)
    shared = gate.shared_by(member.id)
    return PersonView(
        member=h.ref(member),
        is_viewer=member.id == h.viewer.id,
        connected=bool(part.accounts),
        shares=tuple((DESCRIPTIONS[facet], facet in shared) for facet in Facet),
        worth=h.disclosed_worth(part),
        change=h.change_since(part, h.month_start_valuation),
        totals=_totals(h, part, this_month),
        # Day by day is finer than "one number a month": it reads off when money went out,
        # which is transaction detail, so it needs the transactions facet.
        pace=gate.disclose(
            Facet.TRANSACTIONS,
            part.owners,
            lambda: figures.pace(
                h.books,
                h.ownership,
                part.owners,
                _covered_months(h, part, figures.TYPICAL_MONTHS),
            ),
        ),
        categories=gate.disclose(
            Facet.CATEGORY_BREAKDOWN,
            part.owners,
            lambda: category_panel(h, [part], [], this_month),
        ),
        accounts=gate.disclose(
            Facet.ACCOUNT_BALANCES, part.owners, lambda: account_rows(h, part.accounts)
        ),
        days=gate.disclose(
            Facet.TRANSACTIONS, part.owners, lambda: days(h, part.accounts, h.books.month)
        ),
        joint=tuple(_joint(h, p) for p in h.joint_parts_of(member) if _sees_into(h, p)),
    )


def _sees_into(h: Household, part: Part) -> bool:
    return h.gate.may(Facet.ACCOUNT_BALANCES, part.owners) or h.gate.may(
        Facet.TRANSACTIONS, part.owners
    )


def _joint(h: Household, part: Part) -> Joint:
    return Joint(
        part=h.part_ref(part),
        accounts=h.gate.disclose(
            Facet.ACCOUNT_BALANCES, part.owners, lambda: account_rows(h, part.accounts)
        ),
        total=h.gate.disclose(
            Facet.ACCOUNT_BALANCES, part.owners, lambda: h.worth(h.books.today, part)
        ),
        days=h.gate.disclose(
            Facet.TRANSACTIONS, part.owners, lambda: days(h, part.accounts, h.books.month)
        ),
    )


def _totals(h: Household, part: Part, this_month: MonthFlows) -> Totals:
    gate = h.gate
    spent = gate.disclose(
        Facet.MONTHLY_SPENDING_TOTAL, part.owners, lambda: this_month.spent(part.owners)
    )
    typical: Decimal | None = None
    # Typical "by the 18th" is built from earlier months cut at a day, which is day-level
    # detail, so it goes with the transactions facet like the day-by-day chart.
    if gate.may(Facet.TRANSACTIONS, part.owners):
        history = [
            figures.month_flows(h.books, h.ownership, m, to_day=h.books.as_of.day)
            for m in _covered_months(h, part, figures.TYPICAL_MONTHS)
        ]
        typical = figures.typical([flows.spent(part.owners) for flows in history])
    return Totals(
        month=h.books.month,
        to_day=h.books.as_of.day,
        spent=spent,
        earned=gate.disclose(
            Facet.MONTHLY_INCOME_TOTAL, part.owners, lambda: this_month.earned(part.owners)
        ),
        payments_in=gate.disclose(Facet.TRANSACTIONS, part.owners, lambda: _payments_in(h, part)),
        typical_spent=typical,
    )


def _payments_in(h: Household, part: Part) -> int:
    start = h.books.as_of.replace(day=1)
    lines = figures.lines(h.books, part.accounts, start, h.books.as_of)
    return sum(1 for line in lines if line.category and root(line.category) == "Income")


def _covered_months(h: Household, part: Part, count: int) -> list[Month]:
    return [
        m
        for m in figures.past_months(h.books.month, count)
        if figures.covers(h.books, part.accounts, m)
    ]


def account_rows(h: Household, accounts: Sequence[Account]) -> tuple[AccountRow, ...]:
    """Each account's value today, history and freshness, grouped Cash, Credit, Investments."""
    coverage = {c.account: c for c in h.books.coverage}
    rows = [_account_row(h, account, coverage.get(account.ledger_account)) for account in accounts]
    return tuple(sorted(rows, key=lambda row: (ACCOUNT_KINDS.index(row.kind), row.name)))


def _account_row(h: Household, account: Account, coverage: AccountCoverage | None) -> AccountRow:
    positions = [p for p in h.books.today.positions if p.holding.account == account.ledger_account]
    return AccountRow(
        id=account.id,
        name=account.name,
        kind=_kind(account, positions, h.books.currency),
        owners=tuple(h.ref(m) for m in h.members if m.id in account.owners),
        value=Worth(
            sum((p.value for p in positions if p.value is not None), Decimal(0)),
            tuple(p.holding.commodity for p in positions if p.freshness is Freshness.UNPRICED),
        ),
        stale=any(p.freshness is Freshness.STALE for p in positions),
        spans=coverage.spans if coverage else (),
        gaps=len(coverage.gaps) if coverage else 0,
        updated=coverage.latest if coverage else None,
    )


def _kind(account: Account, positions: Sequence[Valued], currency: str) -> str:
    if root(account.ledger_account) == "Liabilities":
        return "Credit"
    if any(p.holding.commodity != currency for p in positions):
        return "Investments"
    return "Cash"


def days(h: Household, accounts: Sequence[Account], month: Month) -> tuple[Day, ...]:
    """This month's lines on `accounts`, grouped by day, newest first, with each day's spending.

    A day's figure counts spending only: income and transfers between the accounts are left
    out, which the page says.
    """
    start = date(month.year, month.month, 1)
    end = min(h.books.as_of, start.replace(day=calendar.monthrange(month.year, month.month)[1]))
    grouped: defaultdict[date, list[Line]] = defaultdict(list)
    for line in figures.lines(h.books, accounts, start, end):
        grouped[line.date].append(line)
    return tuple(
        Day(
            day=day,
            spent=sum(
                (
                    -line.amount
                    for line in found
                    if line.category and root(line.category) == "Expenses"
                ),
                Decimal(0),
            ),
            lines=tuple(found),
        )
        for day, found in sorted(grouped.items(), reverse=True)
    )
