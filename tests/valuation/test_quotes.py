from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import cast

import pytest

from tests.valuation.conftest import quote


def test_a_quote_refuses_a_float_price() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        replace(quote(), price=cast("Decimal", 101.25))


@pytest.mark.parametrize("price", ["0", "-1", "NaN", "Infinity"])
def test_a_quote_refuses_a_price_that_cannot_be_a_close(price: str) -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        quote(price=price)


def test_a_quote_refuses_a_naive_fetch_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(quote(), fetched_at=datetime(2026, 10, 16, 23, 0))


def test_a_quote_keeps_the_price_exactly() -> None:
    assert quote(price="181.0519").price == Decimal("181.0519")
