from collections import Counter
from datetime import date

from hearth.valuation.pricestore import PriceStore, Recorded
from hearth.valuation.quotes import (
    PriceSourceError,
    PriceSourceUnavailableError,
    Quote,
    UnknownSymbolError,
)
from hearth.valuation.refresh import refresh_prices
from tests.valuation.conftest import quote

DAY = date(2026, 10, 15)


class FakeSource:
    name = "test"

    def __init__(self, reason: str | None = None) -> None:
        self.reason = reason
        self.asked: list[str] = []

    def unavailable_reason(self) -> str | None:
        return self.reason

    def quotes(self, symbol: str, start: date, end: date) -> list[Quote]:
        self.asked.append(symbol)
        if self.reason is not None:
            raise PriceSourceUnavailableError(self.reason)
        if symbol == "NOSUCH":
            raise UnknownSymbolError(symbol)
        if symbol == "BROKEN":
            raise PriceSourceError("HTTP 500 for BROKEN")
        return [quote(symbol, DAY)]


def test_an_unavailable_source_is_never_asked_and_nothing_is_recorded(store: PriceStore) -> None:
    source = FakeSource(reason="no key")

    report = refresh_prices(source, store, ["FUNDA"], DAY, DAY)

    assert report.unavailable == "no key"
    assert source.asked == []
    assert store.latest("FUNDA", DAY, "test") is None


def test_each_symbol_is_recorded_or_reported(store: PriceStore) -> None:
    report = refresh_prices(FakeSource(), store, ["FUNDA", "NOSUCH", "BROKEN", "FUNDB"], DAY, DAY)

    assert report.unavailable is None
    assert report.recorded == Counter({Recorded.NEW: 2})
    assert report.unknown == ("NOSUCH",)
    assert report.failed == (("BROKEN", "HTTP 500 for BROKEN"),)
    assert store.latest("FUNDB", DAY, "test") == quote("FUNDB", DAY)


def test_refreshing_again_reports_nothing_new(store: PriceStore) -> None:
    refresh_prices(FakeSource(), store, ["FUNDA"], DAY, DAY)

    report = refresh_prices(FakeSource(), store, ["FUNDA"], DAY, DAY)

    assert report.recorded == Counter({Recorded.UNCHANGED: 1})
