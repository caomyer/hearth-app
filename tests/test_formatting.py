"""Numbers and dates as the design system writes them (design/system.html, Numbers)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hearth.months import Month
from hearth.web.templating import amount, category, close, compact, dollars, hero, money, when

MINUS = "\N{MINUS SIGN}"


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("0", "$0.00"),
        ("1234.5", "$1,234.50"),
        ("-1234.5", f"{MINUS}$1,234.50"),
        ("0.005", "$0.00"),
        ("0.015", "$0.02"),
        ("-0.001", "$0.00"),
    ],
)
def test_line_items_keep_cents(value: str, text: str) -> None:
    assert money(Decimal(value)) == text


@pytest.mark.parametrize(
    ("value", "text"),
    [("611295.59", "$611,296"), ("-3140.22", f"{MINUS}$3,140"), ("0.4", "$0")],
)
def test_aggregates_are_whole_dollars(value: str, text: str) -> None:
    assert dollars(Decimal(value)) == text


def test_a_hero_sets_the_dollar_sign_small() -> None:
    assert hero(Decimal("1284898.2")) == '<span class="cur">$</span>1,284,898'
    assert hero(Decimal("-12")) == f'{MINUS}<span class="cur">$</span>12'


def test_transactions_carry_their_sign_without_a_currency() -> None:
    assert amount(Decimal("-64.18")) == f"{MINUS}64.18"
    assert amount(Decimal("3299.6")) == "+3,299.60"
    assert amount(Decimal("0")) == "0.00"


def test_compact_labels() -> None:
    assert compact(Decimal("8214")) == "$8.2K"
    assert compact(Decimal("1284898")) == "$1.3M"
    assert compact(Decimal("-950")) == f"{MINUS}$950"


def test_categories_read_as_people_say_them() -> None:
    assert category("Expenses:Food:Dining") == "Dining"
    assert category("Income:Salary:Avery") == "Income"
    assert category(None) == "Transfer"


def test_a_price_says_which_close_it_is() -> None:
    today = date(2026, 10, 18)
    assert close(date(2026, 10, 16), today) == "Fri close"
    assert close(date(2026, 10, 5), today) == "Oct 5 close"


def test_times_read_relative_to_today() -> None:
    computed = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    local = computed.astimezone().date()
    assert when(computed, local).startswith("today, ")
    assert when(computed, local.replace(day=local.day + 1)).startswith("yesterday, ")


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
