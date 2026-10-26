"""An institution's own price for its holdings, read from an invented Plaid holdings response."""

import json
import sqlite3
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from hearth import books
from hearth.valuation import institution
from hearth.valuation.institution import (
    CONFLICT,
    NO_CURRENCY,
    NO_DATE,
    NO_PRICE,
    NO_TICKER,
    HoldingsMalformedError,
    Unpriceable,
)
from hearth.valuation.pricestore import PriceStore, Recorded
from tests.valuation.conftest import FETCHED_AT

RESPONSE = """{
  "accounts": [{"account_id": "acct-zq-1"}],
  "holdings": [
    {"account_id": "acct-zq-1", "security_id": "sec-fund", "quantity": 12.5,
     "institution_price": 101.2345, "institution_price_as_of": "2026-10-16",
     "institution_value": 1265.43, "iso_currency_code": "USD"},
    {"account_id": "acct-zq-1", "security_id": "sec-cash", "quantity": 400.0,
     "institution_price": 1.0, "institution_price_as_of": "2026-10-16",
     "iso_currency_code": "USD"},
    {"account_id": "acct-zq-1", "security_id": "sec-trust", "quantity": 3.0,
     "institution_price": 55.10, "institution_price_as_of": "2026-10-16",
     "iso_currency_code": "USD"},
    {"account_id": "acct-zq-1", "security_id": "sec-undated", "quantity": 1.0,
     "institution_price": 9.99, "institution_price_as_of": null, "iso_currency_code": "USD"}
  ],
  "securities": [
    {"security_id": "sec-fund", "ticker_symbol": "ZQFND", "type": "mutual fund",
     "is_cash_equivalent": false},
    {"security_id": "sec-cash", "ticker_symbol": "CUR:USD", "type": "cash",
     "is_cash_equivalent": true},
    {"security_id": "sec-trust", "ticker_symbol": null, "type": "mutual fund",
     "is_cash_equivalent": false},
    {"security_id": "sec-undated", "ticker_symbol": "ZQUND", "type": "equity",
     "is_cash_equivalent": false}
  ]
}"""


def parsed(text: str = RESPONSE) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(text, parse_float=Decimal)
    return payload


def test_each_holding_is_priced_at_the_institutions_own_price_with_its_provenance() -> None:
    prices = institution.read_holdings(parsed(), fetched_at=FETCHED_AT)

    (quote,) = prices.quotes
    assert quote.symbol == "ZQFND"
    assert quote.price == Decimal("101.2345")
    assert quote.as_of == date(2026, 10, 16)
    assert (quote.source, quote.source_ref, quote.fetched_at) == ("plaid", "sec-fund", FETCHED_AT)


def test_a_holding_hearth_cannot_price_is_listed_with_the_reason() -> None:
    prices = institution.read_holdings(parsed(), fetched_at=FETCHED_AT)

    assert prices.unpriceable == (
        Unpriceable("sec-trust", NO_TICKER),
        Unpriceable("sec-undated", NO_DATE),
    )


def test_a_price_read_as_a_float_is_refused() -> None:
    with pytest.raises(HoldingsMalformedError, match="parse_float=Decimal"):
        institution.read_holdings(json.loads(RESPONSE), fetched_at=FETCHED_AT)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"institution_price": None}, NO_PRICE),
        ({"institution_price": Decimal(0)}, NO_PRICE),
        ({"iso_currency_code": None, "unofficial_currency_code": "ZQC"}, NO_CURRENCY),
    ],
)
def test_a_holding_without_a_usable_price_is_listed(change: dict[str, Any], reason: str) -> None:
    payload = parsed()
    payload["holdings"][0] |= change

    prices = institution.read_holdings(payload, fetched_at=FETCHED_AT)

    assert prices.quotes == ()
    assert Unpriceable("sec-fund", reason) in prices.unpriceable


def test_two_holdings_of_one_ticker_that_agree_store_one_price() -> None:
    payload = parsed()
    payload["holdings"].append(payload["holdings"][0] | {"account_id": "acct-zq-2"})

    assert len(institution.read_holdings(payload, fetched_at=FETCHED_AT).quotes) == 1


def test_two_holdings_of_one_ticker_that_disagree_store_neither() -> None:
    payload = parsed()
    other = payload["holdings"][0] | {"account_id": "acct-zq-2", "security_id": "sec-fund-2"}
    payload["holdings"].append(other | {"institution_price": Decimal("101.30")})
    payload["securities"].append(payload["securities"][0] | {"security_id": "sec-fund-2"})

    prices = institution.read_holdings(payload, fetched_at=FETCHED_AT)

    assert prices.quotes == ()
    assert Unpriceable("sec-fund", CONFLICT) in prices.unpriceable
    assert Unpriceable("sec-fund-2", CONFLICT) in prices.unpriceable


def test_a_holding_naming_no_listed_security_is_malformed() -> None:
    payload = parsed()
    payload["securities"] = []

    with pytest.raises(HoldingsMalformedError):
        institution.read_holdings(payload, fetched_at=FETCHED_AT)


def test_stored_prices_value_holdings_on_their_own_day_and_after(
    connection: sqlite3.Connection,
) -> None:
    store = PriceStore(connection)
    prices = institution.read_holdings(parsed(), fetched_at=FETCHED_AT)

    assert institution.record(store, prices) == [Recorded.NEW]
    assert institution.record(store, prices) == [Recorded.UNCHANGED]

    lookup = books.stored_prices(store)
    found = lookup("ZQFND", date(2026, 10, 17))
    assert found is not None
    assert (found.price, found.source) == (Decimal("101.2345"), "plaid")
    # Today-only: there is no price before the first day one was asked for.
    assert lookup("ZQFND", date(2026, 10, 15)) is None


def test_a_price_given_with_the_moment_it_was_current_is_read_on_that_moments_day() -> None:
    payload = parsed()
    payload["holdings"][3]["institution_price_datetime"] = "2026-10-16T16:00:00-04:00"

    prices = institution.read_holdings(payload, fetched_at=FETCHED_AT)

    assert {(q.symbol, q.as_of) for q in prices.quotes} == {
        ("ZQFND", date(2026, 10, 16)),
        ("ZQUND", date(2026, 10, 16)),
    }


def test_two_brokerages_pricing_one_fund_differently_on_a_day_store_neither() -> None:
    other = parsed()
    other["holdings"][0]["security_id"] = other["securities"][0]["security_id"] = "sec-other"
    other["holdings"][0]["institution_price"] = Decimal("101.2399")

    prices = institution.read_many([parsed(), other], fetched_at=FETCHED_AT)

    assert all(q.symbol != "ZQFND" for q in prices.quotes)
    assert Unpriceable("sec-fund", CONFLICT) in prices.unpriceable
    assert Unpriceable("sec-other", CONFLICT) in prices.unpriceable


def test_two_brokerages_agreeing_on_a_fund_store_it_once() -> None:
    other = parsed()
    other["holdings"][0]["security_id"] = other["securities"][0]["security_id"] = "sec-other"

    prices = institution.read_many([parsed(), other], fetched_at=FETCHED_AT)

    assert [q.symbol for q in prices.quotes] == ["ZQFND"]


def test_a_security_is_matched_by_its_ticker_never_its_cusip() -> None:
    payload = parsed()
    payload["securities"][2]["cusip"] = "000000000"

    prices = institution.read_holdings(payload, fetched_at=FETCHED_AT)

    assert Unpriceable("sec-trust", NO_TICKER) in prices.unpriceable
