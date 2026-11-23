"""One member's finances: in full for the member themself, as shared for anyone else.

A member's own page and someone else's are built from the same pieces. The difference is only
what the gate allows: the owner of a part may see every facet of it, so the owner's page is
simply the case where nothing is withheld.
"""

import calendar
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from hearth import figures
from hearth.accounts import Account
from hearth.books import Books
from hearth.disclosure.common import (
    DataEnds,
    Household,
    LeftOut,
    MemberRef,
    PartRef,
    Unreviewed,
    Worth,
    unreviewed,
)
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
    since: Month | None
    """The month the account's history in the ledger starts."""
    spans: Disclosed[tuple[Span, ...]]
    updated: Disclosed[date | None]
    """Each source's days and the account's last day are when its money moved, which is
    transaction detail, so they go with the transactions facet."""
    arriving: bool = False
    """Whether the books have not caught up with the account yet (`Household.arriving`)."""
    awaiting: bool = False
    """Whether the books hold none of its history yet (`books.awaiting`): its bank has not sent
    it, which is not the same as there being none, whatever its balance."""
    unseen: bool = False
    """Whether it is an account hearth does not see (`books.unseen`): its
    balance is never known, so it is listed apart from the accounts a total adds up."""
    where: str | None = None
    """Where an account hearth does not see is: its bank's name, or a person's."""
    mask: str | None = None
    """The last digits of an account hearth does not see, when its owner gave them."""
    invested: bool = False
    """Whether it is a brokerage account (`books.invested`): its value is what it holds, which
    no history of its own will ever explain, as Plaid sends none."""
    priced: date | None = None
    """The day of the newest price its holdings are valued at, as a brokerage's are."""

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
    covered: bool
    """Whether the ledger reaches far enough into the month for its figures to mean anything;
    when it does not, they are not shown as figures, nor set against typical."""
    spent: Disclosed[Decimal]
    earned: Disclosed[Decimal]
    payments_in: Disclosed[int]
    typical_spent: Decimal | None
    spent_unreviewed: Disclosed[Unreviewed]
    earned_unreviewed: Disclosed[Unreviewed]
    spends: bool = True
    """Whether any of the accounts behind it could show spending: not when every one is a
    brokerage, which Plaid sends no transactions for, or one hearth does not see, so no figure
    will ever arrive. What kind each account is goes with its balance, so a page says so to
    the part's owners alone."""
    brokerages: bool = False
    """Whether any of the accounts behind it is a brokerage hearth sees (`books.invested`).
    With `unseen`, why a part that `spends` nothing shows no figure: each is a reason that
    applies, said to the part's owners alone, as `spends` is."""
    unseen: bool = False
    """Whether any of the accounts behind it is one hearth does not see (`books.unseen`)."""

    @property
    def compares(self) -> bool:
        """Whether the month is far enough along to set against typical (`figures.compares`)."""
        return figures.compares(self.to_day)

    @property
    def over_typical(self) -> Decimal | None:
        if (
            not self.covered
            or not self.compares
            or self.typical_spent is None
            or not isinstance(self.spent, Decimal)
        ):
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
    ends: DataEnds | None
    """Where this member's own data stops, when the page should say so."""
    left_out: tuple[LeftOut, ...] = ()
    """What `worth` leaves out of accounts hearth does not see, if it may be seen at all."""

    @property
    def withheld(self) -> tuple[FacetDescription, ...]:
        return tuple(description for description, shared in self.shares if not shared)

    @property
    def joint_total(self) -> Decimal | None:
        """What the joint accounts the viewer sees add up to; None while one is not known."""
        totals = [j.total for j in self.joint if isinstance(j.total, Worth)]
        if not all(total.is_known for total in totals):
            return None
        return sum((total.value for total in totals), Decimal(0))


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
    worth = h.disclosed_worth(part)
    return PersonView(
        member=h.ref(member),
        is_viewer=member.id == h.viewer.id,
        connected=bool(part.accounts),
        shares=tuple((DESCRIPTIONS[facet], facet in shared) for facet in Facet),
        worth=worth,
        left_out=(
            (LeftOut(h.part_ref(part), worth.unseen),)
            if isinstance(worth, Worth) and worth.leaves_out
            else ()
        ),
        change=h.change_since(part, h.month_start_valuation),
        totals=_totals(h, part, this_month),
        ends=h.ends([part]),
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
        covered=figures.flows_cover(h.books, part.accounts, h.books.month),
        spends=bool(h.spends(part)),
        brokerages=any(a.ledger_account in h.books.invested for a in h.seen(part)),
        unseen=h.unseen(part) > 0,
        spent=spent,
        earned=gate.disclose(
            Facet.MONTHLY_INCOME_TOTAL, part.owners, lambda: this_month.earned(part.owners)
        ),
        payments_in=gate.disclose(Facet.TRANSACTIONS, part.owners, lambda: _payments_in(h, part)),
        typical_spent=typical,
        spent_unreviewed=gate.disclose(
            Facet.MONTHLY_SPENDING_TOTAL,
            part.owners,
            lambda: _unreviewed(h, part, this_month.spent_unreviewed(part.owners), this_month),
        ),
        earned_unreviewed=gate.disclose(
            Facet.MONTHLY_INCOME_TOTAL,
            part.owners,
            lambda: _unreviewed(h, part, this_month.earned_unreviewed(part.owners), this_month),
        ),
    )


def _unreviewed(h: Household, part: Part, amount: Decimal, flows: MonthFlows) -> Unreviewed:
    rows = flows.unreviewed.get(part.owners, 0)
    return unreviewed(h.gate, [part], h.part_ref, lambda _: amount, lambda _: rows)


def _payments_in(h: Household, part: Part) -> int:
    start = h.books.as_of.replace(day=1)
    lines = figures.lines(h.books, part.accounts, start, h.books.as_of)
    return sum(1 for line in lines if line.category and root(line.category) == "Income")


def _covered_months(h: Household, part: Part, count: int) -> list[Month]:
    return [
        m
        for m in figures.past_months(h.books.month, count)
        if figures.flows_cover(h.books, part.accounts, m)
    ]


def account_rows(h: Household, accounts: Sequence[Account]) -> tuple[AccountRow, ...]:
    """Each account's value today, history and freshness, grouped Cash, Credit, Investments."""
    coverage = {c.account: c for c in h.books.coverage}
    rows = [_account_row(h, account, coverage.get(account.ledger_account)) for account in accounts]
    return tuple(sorted(rows, key=lambda row: (ACCOUNT_KINDS.index(row.kind), row.name)))


def _account_row(h: Household, account: Account, coverage: AccountCoverage | None) -> AccountRow:
    positions = [p for p in h.books.today.positions if p.holding.account == account.ledger_account]
    unseen = account.ledger_account in h.books.unseen
    return AccountRow(
        id=account.id,
        name=account.name,
        kind=_kind(account, positions, h.books),
        owners=tuple(h.ref(m) for m in h.members if m.id in account.owners),
        value=Worth(
            sum((p.value for p in positions if p.value is not None), Decimal(0)),
            tuple(p.holding.commodity for p in positions if p.freshness is Freshness.UNPRICED),
            1 if account.ledger_account in h.balance_unknown else 0,
            1 if unseen else 0,
            counted=not unseen,
        ),
        stale=any(p.freshness is Freshness.STALE for p in positions),
        since=Month.of(coverage.spans[0].earliest) if coverage and coverage.spans else None,
        spans=h.gate.disclose(
            Facet.TRANSACTIONS, account.owners, lambda: coverage.spans if coverage else ()
        ),
        updated=h.gate.disclose(
            Facet.TRANSACTIONS, account.owners, lambda: coverage.latest if coverage else None
        ),
        arriving=account.ledger_account in h.arriving,
        awaiting=account.ledger_account in h.books.awaiting,
        unseen=unseen,
        where=account.named.institution if unseen and account.named else None,
        mask=account.named.mask if unseen and account.named else None,
        invested=account.ledger_account in h.books.invested,
        priced=max((p.quote.as_of for p in positions if p.quote), default=None),
    )


def _kind(account: Account, positions: Sequence[Valued], books: Books) -> str:
    if root(account.ledger_account) == "Liabilities":
        return "Credit"
    # A brokerage account is one whatever it holds: its holdings may not be in yet, or be cash.
    if account.ledger_account in books.invested or any(
        p.holding.commodity != books.currency for p in positions
    ):
        return "Investments"
    return "Cash"


def other_side(h: Household, line: Line) -> Account | None:
    """The account a line on an account hearth does not see moved money from or to, when the
    viewer may be told which it is (``access.may_name_other_side``): an owner of it. Anyone
    else, such as the other owner of a joint account one member moved their own money into,
    reads only that it came from, or went to, another account."""
    if len(line.other_side) != 1:
        return None
    found = next((a for a in h.accounts if a.ledger_account == line.other_side[0]), None)
    if found is None or not h.gate.may_name_other_side(found.owners):
        return None
    return found


def named_side(h: Household, line: Line) -> Line:
    """`line`, naming the other side of a move on an account hearth does not see where the
    viewer may be told it (`other_side`)."""
    found = other_side(h, line) if line.other_side else None
    if found is None:
        return line
    payee = f"Transfer {'from' if line.amount > 0 else 'to'} {found.name}"
    return replace(line, payee=payee)


def days(h: Household, accounts: Sequence[Account], month: Month) -> tuple[Day, ...]:
    """This month's lines on `accounts`, grouped by day, newest first, with each day's spending.

    A day's figure counts spending only: income and transfers between the accounts are left
    out, which the page says.
    """
    start = date(month.year, month.month, 1)
    end = min(h.books.as_of, start.replace(day=calendar.monthrange(month.year, month.month)[1]))
    grouped: defaultdict[date, list[Line]] = defaultdict(list)
    for line in figures.lines(h.books, accounts, start, end):
        grouped[line.date].append(named_side(h, line))
    return tuple(
        Day(
            day=day,
            spent=sum(
                (-line.amount for line in found if line.category and h.books.spends(line.category)),
                Decimal(0),
            ),
            lines=tuple(found),
        )
        for day, found in sorted(grouped.items(), reverse=True)
    )
