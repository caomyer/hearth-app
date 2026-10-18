import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from hearth import db as hearth_db
from hearth.valuation.pricestore import PriceStore
from hearth.valuation.quotes import Quote

FETCHED_AT = datetime(2026, 10, 16, 23, 0, tzinfo=UTC)


def quote(
    symbol: str = "FUNDA",
    as_of: date = date(2026, 10, 15),
    price: str = "101.25",
    *,
    currency: str = "USD",
    source: str = "test",
) -> Quote:
    return Quote(
        symbol=symbol,
        as_of=as_of,
        price=Decimal(price),
        currency=currency,
        source=source,
        source_ref=f"{symbol}.TEST",
        fetched_at=FETCHED_AT,
    )


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    db = hearth_db.connect(Path(":memory:"))
    hearth_db.migrate(db)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def store(connection: sqlite3.Connection) -> PriceStore:
    return PriceStore(connection)
