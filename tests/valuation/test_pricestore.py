import sqlite3
from contextlib import closing
from datetime import date
from decimal import Decimal

import pytest

from hearth.valuation.pricestore import PriceStore, Recorded
from tests.valuation.conftest import FETCHED_AT, quote


def test_a_recorded_quote_comes_back_with_all_its_provenance(store: PriceStore) -> None:
    original = quote(price="181.0519")

    assert store.record(original) is Recorded.NEW
    assert store.latest("FUNDA", date(2026, 10, 15), "test") == original


def test_prices_are_stored_as_exact_text(connection: sqlite3.Connection) -> None:
    PriceStore(connection).record(quote(price="0.10"))

    assert tuple(connection.execute("SELECT price, typeof(price) FROM price").fetchone()) == (
        "0.10",
        "text",
    )


def test_recording_the_same_price_again_changes_nothing(store: PriceStore) -> None:
    store.record(quote())

    assert store.record(quote(price="101.250")) is Recorded.UNCHANGED
    assert len(store.revisions("FUNDA", date(2026, 10, 15), "test")) == 1


def test_a_corrected_price_becomes_current_and_the_old_one_is_kept(store: PriceStore) -> None:
    store.record(quote(price="101.25"))

    assert store.record(quote(price="101.52")) is Recorded.CORRECTED

    day = date(2026, 10, 15)
    assert store.latest("FUNDA", day, "test") == quote(price="101.52")
    assert [q.price for q in store.revisions("FUNDA", day, "test")] == [
        Decimal("101.25"),
        Decimal("101.52"),
    ]


def test_a_correction_back_to_an_earlier_price_is_still_recorded(store: PriceStore) -> None:
    for price in ("101.25", "101.52", "101.25"):
        store.record(quote(price=price))

    assert store.latest("FUNDA", date(2026, 10, 15), "test") == quote(price="101.25")
    assert len(store.revisions("FUNDA", date(2026, 10, 15), "test")) == 3


def test_latest_takes_the_newest_day_not_after_the_date(store: PriceStore) -> None:
    for day, price in ((13, "99"), (14, "100"), (16, "102")):
        store.record(quote(as_of=date(2026, 10, day), price=price))

    found = store.latest("FUNDA", date(2026, 10, 15), "test")

    assert found is not None
    assert (found.as_of, found.price) == (date(2026, 10, 14), Decimal("100"))


def test_latest_is_none_before_the_first_price(store: PriceStore) -> None:
    store.record(quote(as_of=date(2026, 10, 15)))

    assert store.latest("FUNDA", date(2026, 10, 14), "test") is None


def test_sources_and_symbols_never_mix(store: PriceStore) -> None:
    store.record(quote(symbol="FUNDA", source="one", price="1"))
    store.record(quote(symbol="FUNDB", source="test", price="2"))

    day = date(2026, 10, 15)
    assert store.latest("FUNDA", day, "test") is None
    assert store.latest("FUNDB", day, "one") is None


def test_the_fetch_time_round_trips_with_its_timezone(store: PriceStore) -> None:
    store.record(quote())

    found = store.latest("FUNDA", date(2026, 10, 15), "test")

    assert found is not None
    assert found.fetched_at == FETCHED_AT
    assert found.fetched_at.utcoffset() is not None


def test_opening_the_store_twice_keeps_what_was_recorded(
    connection: sqlite3.Connection,
) -> None:
    PriceStore(connection).record(quote())

    assert PriceStore(connection).latest("FUNDA", date(2026, 10, 15), "test") == quote()


def test_a_connection_outside_autocommit_mode_is_refused() -> None:
    with (
        closing(sqlite3.connect(":memory:")) as legacy,
        pytest.raises(ValueError, match="autocommit"),
    ):
        PriceStore(legacy)
