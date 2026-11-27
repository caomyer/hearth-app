from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import cast

import pytest

from hearth.valuation.pricetable import WIDE, PriceTable
from hearth.valuation.quotes import LEAST_PRICE, MOST_DIGITS, MOST_PRICE, enterable
from tests.valuation.conftest import quote


def test_a_quote_refuses_a_float_price() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        replace(quote(), price=cast("Decimal", 101.25))


@pytest.mark.parametrize("price", ["0", "-1", "NaN", "Infinity"])
def test_a_quote_refuses_a_price_that_cannot_be_a_close(price: str) -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        quote(price=price)


@pytest.mark.parametrize(
    "price",
    [
        str(LEAST_PRICE),
        "0.00000002",
        "0." + "0" * 11 + "1" * MOST_DIGITS,
        "1" * 9 + "." + "1" * (MOST_DIGITS - 9),
        "9" * 9 + "." + "9" * (MOST_DIGITS - 10),
        str(MOST_PRICE),
    ],
)
def test_every_price_a_hand_may_type_is_packed_and_kept_exactly(price: str) -> None:
    """Down to the floor and up to the ceiling, at its most digits: kept, and packed in the
    price table's arrays exactly, never held whole as a price too wide to pack."""
    assert enterable(Decimal(price))
    table = PriceTable.of([quote(price=price)])
    (series,) = table._series.values()

    assert series.exponents[0] != WIDE
    assert table(quote().symbol, quote().as_of).price == Decimal(price)  # type: ignore[union-attr]


@pytest.mark.parametrize("price", ["0.0000000000009", "1000000000.1", "0.1234567890123456"])
def test_a_price_outside_the_bounds_or_too_long_is_not_one_a_hand_may_type(price: str) -> None:
    assert not enterable(Decimal(price))


def test_a_quote_refuses_a_naive_fetch_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(quote(), fetched_at=datetime(2026, 10, 16, 23, 0))


def test_a_quote_keeps_the_price_exactly() -> None:
    assert quote(price="181.0519").price == Decimal("181.0519")
