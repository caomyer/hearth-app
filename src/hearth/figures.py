"""Figures for one part of the household, read from the books.

A part is either one member's sole accounts or one group of joint owners' shared accounts.
Every household figure is a sum of parts, and each account belongs to exactly one part, so no
member figure contains any piece of a joint account (see ``docs/privacy-boundary.md``).

These functions compute; they never decide who may see the result. ``hearth.disclosure``
calls them only once ``hearth.access`` has said yes.
"""

import calendar
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from beancount.core.data import Transaction

from hearth.accounts import Account
from hearth.books import Books
from hearth.members import Member
from hearth.months import Month
from hearth.reports.common import is_balance_sheet, root, units
from hearth.reports.flows import (
    Flows,
    Owners,
    OwnersOf,
    income_by_source,
    payers,
    spending_by_category,
)
from hearth.valuation.networth import NetWorth

TYPICAL_MONTHS = 6
"""How many past months "typical" spending is the median of, for pace and categories."""

MIN_TYPICAL_MONTHS = 3
"""Fewer complete past months than this, and there is no typical to compare with."""

QUIET_START = timedelta(days=10)
"""How long an account may go without a transaction at the start of a month and still count as
covered: a quiet week is normal, a history that starts weeks late is not."""


@dataclass(frozen=True, slots=True)
class Part:
    """Accounts that belong to the same owners: one member alone, or one joint group."""

    owners: Owners
    accounts: tuple[Account, ...]

    @property
    def is_joint(self) -> bool:
        return len(self.owners) > 1

    @property
    def ledger_accounts(self) -> frozenset[str]:
        return frozenset(account.ledger_account for account in self.accounts)


def parts(household: Sequence[Member], accounts: Sequence[Account]) -> list[Part]:
    """Every member's sole part, in household order, then every joint group.

    A member with no accounts still has a part, an empty one, so they appear in the
    household; an account nobody owns belongs to no part.
    """
    grouped: defaultdict[Owners, list[Account]] = defaultdict(list)
    for account in accounts:
        if account.owners:
            grouped[account.owners].append(account)
    solo = [Part(frozenset({m.id}), tuple(grouped.get(frozenset({m.id}), ()))) for m in household]
    joint = [Part(owners, tuple(found)) for owners, found in grouped.items() if len(owners) > 1]
    return solo + sorted(joint, key=lambda part: sorted(part.owners))


def ownership(accounts: Sequence[Account]) -> OwnersOf:
    """Who owns each ledger account, from the accounts table. An unclaimed account has no
    owner, and nothing it pays for is anyone's."""
    owners = {account.ledger_account: account.owners for account in accounts if account.owners}
    return owners.get


def worth(valuation: NetWorth, part: Part) -> NetWorth:
    """The positions of a household valuation that sit on the part's accounts."""
    mine = part.ledger_accounts
    positions = tuple(p for p in valuation.positions if p.holding.account in mine)
    return NetWorth(valuation.as_of, valuation.currency, positions)


@dataclass(frozen=True, slots=True)
class MonthFlows:
    """One month of money in and out, for every part at once.

    `to_day` is set when only the month's first days count: the month in progress, or an
    earlier month cut to the same day so the two compare like with like.
    """

    month: Month
    to_day: int | None
    spending: Flows
    income: Flows
    unreviewed: Mapping[Owners, int]

    def spent_by_category(self, owners: Owners) -> dict[str, Decimal]:
        return dict(self.spending.by_owners.get(owners, {}))

    def spent(self, owners: Owners) -> Decimal:
        return sum(self.spending.by_owners.get(owners, {}).values(), Decimal(0))

    def earned(self, owners: Owners) -> Decimal:
        return sum(self.income.by_owners.get(owners, {}).values(), Decimal(0))


def month_flows(
    books: Books, ownership: OwnersOf, month: Month, *, to_day: int | None = None
) -> MonthFlows:
    """Spending by category, income and unreviewed counts for a month, by owners.

    With `to_day`, only the month's first `to_day` days count.
    """
    start = date(month.year, month.month, 1)
    last = calendar.monthrange(month.year, month.month)[1]
    end = start.replace(day=min(to_day or last, last))
    unreviewed: defaultdict[Owners, int] = defaultdict(int)
    for entry in _transactions(books, start, end):
        paid_by = payers(entry, ownership)
        if entry.flag == "!" and not isinstance(paid_by, str):
            unreviewed[paid_by] += 1
    return MonthFlows(
        month=month,
        to_day=to_day,
        spending=spending_by_category(
            books.entries, ownership, start=start, end=end, currency=books.currency
        ),
        income=income_by_source(
            books.entries, ownership, start=start, end=end, currency=books.currency
        ),
        unreviewed=dict(unreviewed),
    )


def _transactions(books: Books, start: date, end: date) -> Iterable[Transaction]:
    for entry in books.entries:
        if isinstance(entry, Transaction) and start <= entry.date <= end:
            yield entry


def past_months(month: Month, count: int) -> list[Month]:
    """The `count` months before `month`, oldest first."""
    months = []
    current = month
    for _ in range(count):
        current = current.previous()
        months.append(current)
    return months[::-1]


def typical(values: Sequence[Decimal]) -> Decimal | None:
    """The median, or None when there are too few months to call anything typical."""
    if len(values) < MIN_TYPICAL_MONTHS:
        return None
    return Decimal(statistics.median(values))


@dataclass(frozen=True, slots=True)
class Pace:
    """Cumulative spending by day: this month so far, and the usual shape of a month."""

    days_in_month: int
    today: int
    actual: tuple[Decimal, ...]
    typical: tuple[Decimal, ...]
    low: tuple[Decimal, ...]
    high: tuple[Decimal, ...]
    months_compared: int


def pace(books: Books, ownership: OwnersOf, owners: Owners, months: Sequence[Month]) -> Pace:
    """This month's cumulative spending against the median and middle half of `months`.

    The typical series are empty when there are too few complete months to compare with.
    """
    month = books.month
    days = calendar.monthrange(month.year, month.month)[1]
    actual = _cumulative(books, ownership, owners, month, books.as_of.day)
    history = [_cumulative(books, ownership, owners, m, days) for m in months]
    if len(history) < MIN_TYPICAL_MONTHS:
        return Pace(days, books.as_of.day, actual, (), (), (), len(history))
    by_day = list(zip(*history, strict=True))
    return Pace(
        days_in_month=days,
        today=books.as_of.day,
        actual=actual,
        typical=tuple(Decimal(statistics.median(day)) for day in by_day),
        low=tuple(_quartiles(day)[0] for day in by_day),
        high=tuple(_quartiles(day)[1] for day in by_day),
        months_compared=len(history),
    )


def _quartiles(values: Sequence[Decimal]) -> tuple[Decimal, Decimal]:
    first, _, third = statistics.quantiles(values, n=4, method="inclusive")
    return Decimal(first), Decimal(third)


def _cumulative(
    books: Books, ownership: OwnersOf, owners: Owners, month: Month, days: int
) -> tuple[Decimal, ...]:
    """Cumulative spending through each of the first `days` days of `month`.

    A month shorter than `days` holds its final total for the days it does not have.
    """
    start = date(month.year, month.month, 1)
    last = calendar.monthrange(month.year, month.month)[1]
    daily: defaultdict[int, Decimal] = defaultdict(Decimal)
    for entry in _transactions(books, start, start.replace(day=last)):
        if payers(entry, ownership) != owners:
            continue
        for posting in entry.postings:
            if root(posting.account) == "Expenses":
                amount = units(posting, entry)
                if amount.currency == books.currency:
                    daily[entry.date.day] += amount.number
    running = Decimal(0)
    series = []
    for day in range(1, days + 1):
        running += daily.get(day, Decimal(0))
        series.append(running)
    return tuple(series)


@dataclass(frozen=True, slots=True)
class Line:
    """One transaction as it touched one of the part's accounts."""

    date: date
    payee: str
    narration: str
    category: str | None
    amount: Decimal
    account: Account
    unreviewed: bool


def lines(books: Books, accounts: Sequence[Account], start: date, end: date) -> list[Line]:
    """Every posting to `accounts` from start to end, newest first.

    The category is the transaction's single income or expense account, when it has one.
    """
    by_name = {account.ledger_account: account for account in accounts}
    found = []
    for entry in _transactions(books, start, end):
        others = [p.account for p in entry.postings if not is_balance_sheet(p.account)]
        category = others[0] if len(others) == 1 else None
        for posting in entry.postings:
            account = by_name.get(posting.account)
            if account is None:
                continue
            amount = units(posting, entry)
            if amount.currency != books.currency:
                continue
            found.append(
                Line(
                    date=entry.date,
                    payee=entry.payee or entry.narration or "",
                    narration=(entry.narration or "") if entry.payee else "",
                    category=category,
                    amount=amount.number,
                    account=account,
                    unreviewed=entry.flag == "!",
                )
            )
    return sorted(found, key=lambda line: line.date, reverse=True)


def covers(books: Books, accounts: Iterable[Account], month: Month) -> bool:
    """Whether the ledger holds the whole month for every one of `accounts` open in it.

    An account whose history starts after the month began leaves the month incomplete, and a
    figure for it must say so rather than look whole. Quiet stretches inside an account's
    history are not treated as missing: a brokerage account that trades once a quarter is
    quiet, not incomplete, and coverage reports such gaps per account instead.
    """
    first = date(month.year, month.month, 1)
    last = first.replace(day=calendar.monthrange(month.year, month.month)[1])
    by_account = {coverage.account: coverage for coverage in books.coverage}
    for account in accounts:
        coverage = by_account.get(account.ledger_account)
        if coverage is None or coverage.opened > last:
            continue
        expected_from = max(first, coverage.opened)
        if coverage.earliest is None or coverage.earliest > expected_from + QUIET_START:
            return False
    return True


FIXED_MONTHS = 3
FIXED_TOLERANCE = Decimal("0.05")


@dataclass(frozen=True, slots=True)
class Payment:
    day: date
    amount: Decimal


def category_payments(
    books: Books, ownership: OwnersOf, owners: Iterable[Owners], month: Month
) -> dict[str, list[Payment]]:
    """Each expense category's individual payments in `month` by the given owners."""
    groups = set(owners)
    start = date(month.year, month.month, 1)
    end = min(books.as_of, start.replace(day=calendar.monthrange(month.year, month.month)[1]))
    found: defaultdict[str, list[Payment]] = defaultdict(list)
    for entry in _transactions(books, start, end):
        if payers(entry, ownership) not in groups:
            continue
        for posting in entry.postings:
            if root(posting.account) == "Expenses":
                amount = units(posting, entry)
                if amount.currency == books.currency:
                    found[posting.account].append(Payment(entry.date, amount.number))
    return dict(found)


def fixed_categories(
    books: Books, ownership: OwnersOf, owners: Iterable[Owners], months: Sequence[Month]
) -> frozenset[str]:
    """Categories paid exactly once a month at a steady amount, like rent or a mortgage.

    They are shown as one line with the day they were paid rather than as a bar against a
    typical pace, because a bill paid on the 1st is not "over typical" on the 2nd.
    """
    recent = list(months)[-FIXED_MONTHS:]
    if len(recent) < FIXED_MONTHS:
        return frozenset()
    groups = list(owners)
    history = [category_payments(books, ownership, groups, month) for month in recent]
    fixed = set()
    for category in set().union(*history):
        payments = [month.get(category, []) for month in history]
        if any(len(found) != 1 for found in payments):
            continue
        amounts = [found[0].amount for found in payments]
        if max(amounts) - min(amounts) <= FIXED_TOLERANCE * max(amounts):
            fixed.add(category)
    return frozenset(fixed)
