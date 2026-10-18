"""From a ledger to a net worth, through the store, with and without a price source."""

from datetime import date
from decimal import Decimal

from beancount import loader

from hearth.valuation.eodhd import EodhdSource
from hearth.valuation.holdings import holdings_at
from hearth.valuation.networth import value_holdings
from hearth.valuation.pricestore import PriceStore
from hearth.valuation.refresh import refresh_prices
from tests.valuation.conftest import quote

LEDGER = """
option "operating_currency" "USD"
2026-01-01 open Assets:Bank:Checking USD
2026-01-01 open Assets:Broker:Taxable
2026-01-01 open Equity:Opening
2026-01-02 * "Opening"
  Assets:Bank:Checking  500.00 USD
  Assets:Broker:Taxable  4 FUNDA {25.00 USD}
  Equity:Opening
"""
DAY = date(2026, 10, 19)


def net_worth(store: PriceStore, source: str) -> tuple[bool, Decimal, tuple[str, ...]]:
    entries, errors, _ = loader.load_string(LEDGER)
    assert errors == []
    worth = value_holdings(
        holdings_at(entries, DAY),
        lambda symbol, on: store.latest(symbol, on, source),
        as_of=DAY,
        currency="USD",
    )
    return worth.complete, worth.known_total, worth.missing


def test_with_no_key_configured_the_fund_is_reported_unpriced(store: PriceStore) -> None:
    source = EodhdSource(None)

    report = refresh_prices(source, store, ["FUNDA"], DAY, DAY)

    assert report.unavailable == "no EODHD API key is configured"
    assert net_worth(store, source.name) == (
        False,
        Decimal("500.00"),
        ("4 FUNDA in Assets:Broker:Taxable: no price recorded",),
    )


def test_with_a_stored_price_the_net_worth_is_complete(store: PriceStore) -> None:
    store.record(quote("FUNDA", date(2026, 10, 16), "30.10", source="eodhd"))

    assert net_worth(store, "eodhd") == (True, Decimal("620.40"), ())
