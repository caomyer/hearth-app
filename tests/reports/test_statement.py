from datetime import date
from decimal import Decimal

import pytest
from beancount.core.data import Directive

from hearth.reports.statement import income_statement


def test_every_month_in_the_period_appears(entries: list[Directive]) -> None:
    statement = income_statement(
        entries, start=date(2026, 1, 1), end=date(2026, 6, 30), currency="USD"
    )

    assert [m.month for m in statement.months] == [date(2026, n, 1) for n in range(1, 7)]
    assert statement.months[4].income == 0
    assert statement.months[4].expenses == {}


def test_a_month_has_income_expenses_by_category_net_and_savings_rate(
    entries: list[Directive],
) -> None:
    (january,) = income_statement(
        entries, start=date(2026, 1, 1), end=date(2026, 1, 31), currency="USD"
    ).months

    assert january.income == Decimal("3000.00")
    assert january.expenses == {
        "Expenses:Food": Decimal("120.35"),
        "Expenses:Rent": Decimal("1500.00"),
    }
    assert january.total_expenses == Decimal("1620.35")
    assert january.net == Decimal("1379.65")
    assert january.savings_rate == Decimal("1379.65") / Decimal("3000.00")
    assert not january.partial
    assert january.complete
    assert january.unreviewed == 0


def test_a_month_without_income_has_no_savings_rate_rather_than_an_invented_one(
    entries: list[Directive],
) -> None:
    (march,) = income_statement(
        entries, start=date(2026, 3, 1), end=date(2026, 3, 31), currency="USD"
    ).months

    assert march.income == 0
    assert march.net == Decimal("-1.00")
    assert march.savings_rate is None


def test_amounts_in_another_currency_are_listed_not_converted(entries: list[Directive]) -> None:
    statement = income_statement(
        entries, start=date(2026, 2, 1), end=date(2026, 2, 28), currency="USD"
    )
    (february,) = statement.months

    assert "Expenses:Travel" not in february.expenses
    assert [str(e) for e in february.excluded] == ["100.00 EUR in Expenses:Travel on 2026-02-12"]
    assert not february.complete
    assert not statement.complete


def test_unreviewed_entries_are_counted_in_their_month(entries: list[Directive]) -> None:
    (february,) = income_statement(
        entries, start=date(2026, 2, 1), end=date(2026, 2, 28), currency="USD"
    ).months

    assert february.unreviewed == 1
    assert february.expenses["Expenses:Food"] == Decimal("38.33")


def test_months_cut_by_the_period_are_partial_and_only_count_days_inside_it(
    entries: list[Directive],
) -> None:
    january, february = income_statement(
        entries, start=date(2026, 1, 21), end=date(2026, 2, 5), currency="USD"
    ).months

    assert january.partial
    assert february.partial
    assert january.income == 0
    assert january.expenses == {"Expenses:Food": Decimal("120.35")}


def test_a_period_that_ends_before_it_starts_is_refused(entries: list[Directive]) -> None:
    with pytest.raises(ValueError, match="after end"):
        income_statement(entries, start=date(2026, 2, 1), end=date(2026, 1, 1), currency="USD")
