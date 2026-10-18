from datetime import date
from decimal import Decimal

import pytest

from hearth.months import Month
from hearth.web.templating import category, money, signed_money

MINUS = "\N{MINUS SIGN}"


@pytest.mark.parametrize(
    ("amount", "text"),
    [
        ("0", "$0.00"),
        ("1234.5", "$1,234.50"),
        ("-1234.5", f"{MINUS}$1,234.50"),
        ("0.005", "$0.00"),
        ("0.015", "$0.02"),
        ("-0.001", "$0.00"),
    ],
)
def test_money_is_shown_to_the_cent(amount: str, text: str) -> None:
    assert money(Decimal(amount)) == text


def test_gains_carry_a_plus_and_losses_a_minus() -> None:
    assert signed_money(Decimal("12")) == "+$12.00"
    assert signed_money(Decimal("-12")) == f"{MINUS}$12.00"
    assert signed_money(Decimal("0")) == "$0.00"


def test_categories_drop_their_ledger_root() -> None:
    assert (
        category("Expenses:Food:Dining")
        == "Food \N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} Dining"
    )
    assert category("Income:Salary") == "Salary"
    assert (
        category("Assets:Savings")
        == "Assets \N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} Savings"
    )


def test_months_step_across_year_ends() -> None:
    assert Month(2026, 12).following() == Month(2027, 1)
    assert Month(2026, 1).previous() == Month(2025, 12)
    assert str(Month.parse("2026-03")) == "2026-03"
    assert Month(2026, 10).label == "October 2026"
    assert Month.of(date(2026, 10, 31)).contains(date(2026, 10, 1))


@pytest.mark.parametrize(
    "text", ["2026-00", "2026-13", "26-10", "2026-1", "2026-10-01", "0000-01", ""]
)
def test_malformed_months_are_refused(text: str) -> None:
    with pytest.raises(ValueError, match="not a month"):
        Month.parse(text)
