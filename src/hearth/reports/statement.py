"""The household income statement, month by month."""

import calendar
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from beancount.core.data import Directive

from hearth.reports.common import Excluded, root, transactions, units


@dataclass(frozen=True, slots=True)
class MonthStatement:
    """One calendar month, or the part of it inside the requested period.

    Income is positive money earned, expenses positive money spent, by expense account; a
    refund larger than the month's spending in a category shows as a negative expense.
    """

    month: date
    partial: bool
    income: Decimal
    expenses: dict[str, Decimal]
    unreviewed: int
    excluded: tuple[Excluded, ...]

    @property
    def total_expenses(self) -> Decimal:
        return sum(self.expenses.values(), Decimal(0))

    @property
    def net(self) -> Decimal:
        return self.income - self.total_expenses

    @property
    def savings_rate(self) -> Decimal | None:
        """Net over income; None when there was no income, since any rate would be invented."""
        return self.net / self.income if self.income > 0 else None

    @property
    def complete(self) -> bool:
        """False when amounts in another currency were left out of the month's figures."""
        return not self.excluded


@dataclass(frozen=True, slots=True)
class IncomeStatement:
    start: date
    end: date
    currency: str
    months: tuple[MonthStatement, ...]

    @property
    def complete(self) -> bool:
        return all(month.complete for month in self.months)


def _months(start: date, end: date) -> list[date]:
    months = []
    month = start.replace(day=1)
    while month <= end:
        months.append(month)
        month = (
            month.replace(year=month.year + 1, month=1)
            if month.month == 12
            else month.replace(month=month.month + 1)
        )
    return months


def income_statement(
    entries: Iterable[Directive], *, start: date, end: date, currency: str
) -> IncomeStatement:
    """Income, expenses by account, net and savings rate for each month from start to end.

    Every month in the period appears, including months with no activity. A month cut by the
    period's start or end is marked partial. Transactions flagged `!` are included and counted
    as unreviewed, so a figure that rests on unchecked entries says so. Income and expense
    amounts in any currency other than `currency` are left out and listed, never converted.
    """
    income: defaultdict[date, Decimal] = defaultdict(Decimal)
    expenses: defaultdict[date, defaultdict[str, Decimal]] = defaultdict(
        lambda: defaultdict(Decimal)
    )
    unreviewed: defaultdict[date, int] = defaultdict(int)
    excluded: defaultdict[date, list[Excluded]] = defaultdict(list)
    for entry in transactions(entries, start, end):
        month = entry.date.replace(day=1)
        if entry.flag == "!":
            unreviewed[month] += 1
        for posting in entry.postings:
            kind = root(posting.account)
            if kind not in ("Income", "Expenses"):
                continue
            amount = units(posting, entry)
            if amount.currency != currency:
                excluded[month].append(
                    Excluded(entry.date, posting.account, amount.number, amount.currency)
                )
            elif kind == "Income":
                income[month] -= amount.number
            else:
                expenses[month][posting.account] += amount.number
    return IncomeStatement(
        start=start,
        end=end,
        currency=currency,
        months=tuple(
            MonthStatement(
                month=month,
                partial=month < start
                or end < month.replace(day=calendar.monthrange(month.year, month.month)[1]),
                income=income[month],
                expenses=dict(sorted(expenses[month].items())),
                unreviewed=unreviewed[month],
                excluded=tuple(excluded[month]),
            )
            for month in _months(start, end)
        ),
    )
