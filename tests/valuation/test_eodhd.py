import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hearth.valuation.eodhd import EodhdSource
from hearth.valuation.quotes import (
    PriceSourceError,
    PriceSourceUnavailableError,
    UnknownSymbolError,
)

KEY = "synthetic-key-0000"
NOW = datetime(2026, 10, 16, 23, 0, tzinfo=UTC)
START, END = date(2026, 10, 14), date(2026, 10, 15)
ROWS = [
    {
        "date": "2026-10-14",
        "open": 100.5,
        "high": 100.5,
        "low": 100.5,
        "close": 100.5,
        "adjusted_close": 97.1234,
        "volume": 0,
    },
    {
        "date": "2026-10-15",
        "open": 101.25,
        "high": 101.25,
        "low": 101.25,
        "close": 101.25,
        "adjusted_close": 97.8481,
        "volume": 0,
    },
]


class FakeTransport:
    def __init__(self, status: int = 200, body: object = ROWS) -> None:
        self.status = status
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.urls: list[str] = []

    def __call__(self, url: str) -> tuple[int, bytes]:
        self.urls.append(url)
        return self.status, self.body


def source(transport: FakeTransport) -> EodhdSource:
    return EodhdSource(KEY, transport=transport, clock=lambda: NOW)


def test_without_a_key_the_source_is_unavailable_and_never_called() -> None:
    transport = FakeTransport()
    unconfigured = EodhdSource(None, transport=transport)

    assert unconfigured.unavailable_reason() == "no EODHD API key is configured"
    with pytest.raises(PriceSourceUnavailableError):
        unconfigured.quotes("FUNDA", START, END)
    assert transport.urls == []


def test_an_empty_key_counts_as_no_key() -> None:
    assert EodhdSource("").unavailable_reason() is not None


def test_the_request_names_the_us_symbol_window_and_daily_json() -> None:
    transport = FakeTransport()

    source(transport).quotes("FUNDA", START, END)

    (url,) = transport.urls
    assert url.startswith("https://eodhd.com/api/eod/FUNDA.US?")
    for part in ("fmt=json", "period=d", "from=2026-10-14", "to=2026-10-15", f"api_token={KEY}"):
        assert part in url


def test_quotes_carry_the_raw_close_never_the_adjusted_one() -> None:
    quotes = source(FakeTransport()).quotes("FUNDA", START, END)

    assert [(q.as_of, q.price) for q in quotes] == [
        (date(2026, 10, 14), Decimal("100.5")),
        (date(2026, 10, 15), Decimal("101.25")),
    ]


def test_quotes_carry_their_provenance() -> None:
    first = source(FakeTransport()).quotes("FUNDA", START, END)[0]

    assert first.symbol == "FUNDA"
    assert first.source == "eodhd"
    assert first.source_ref == "FUNDA.US"
    assert first.currency == "USD"
    assert first.fetched_at == NOW


def test_prices_never_pass_through_a_float() -> None:
    body = b'[{"date": "2026-10-15", "close": 181.0519, "adjusted_close": 180.1}]'

    (only,) = source(FakeTransport(body=body)).quotes("FUNDA", START, END)

    assert only.price == Decimal("181.0519")


def test_an_unknown_symbol_is_reported_as_unknown() -> None:
    with pytest.raises(UnknownSymbolError, match=r"FUNDA\.US"):
        source(FakeTransport(404, b"Ticker Not Found.")).quotes("FUNDA", START, END)


@pytest.mark.parametrize("status", [401, 402, 429, 500])
def test_an_http_failure_is_an_error_that_never_reveals_the_key(status: int) -> None:
    with pytest.raises(PriceSourceError) as raised:
        source(FakeTransport(status, b"Unauthenticated")).quotes("FUNDA", START, END)

    assert str(status) in str(raised.value)
    assert KEY not in str(raised.value)


def test_an_unreachable_source_is_an_error_that_never_reveals_the_key() -> None:
    def unreachable(url: str) -> tuple[int, bytes]:
        raise TimeoutError("timed out")

    with pytest.raises(PriceSourceError, match="could not be reached") as raised:
        EodhdSource(KEY, transport=unreachable).quotes("FUNDA", START, END)

    assert KEY not in str(raised.value)


@pytest.mark.parametrize(
    "body",
    [
        b"<html>",
        b'{"error": "x"}',
        b'[{"date": "2026-10-15"}]',
        b'[{"date": "2026-10-15", "close": null}]',
        b'[{"date": "2026-10-15", "close": 0}]',
        b'[{"date": "2026-10-15", "close": "101.25"}]',
        b'[{"date": "15/10/2026", "close": 101.25}]',
        b'[{"date": "2026-10-13", "close": 101.25}]',
        b'[{"date": "2026-10-15", "close": 1}, {"date": "2026-10-15", "close": 2}]',
        b"[1]",
    ],
)
def test_a_response_with_any_unusable_row_is_rejected_whole(body: bytes) -> None:
    with pytest.raises(PriceSourceError):
        source(FakeTransport(body=body)).quotes("FUNDA", START, END)


def test_a_window_that_ends_before_it_starts_is_refused() -> None:
    with pytest.raises(ValueError, match="after end"):
        source(FakeTransport()).quotes("FUNDA", END, START)


def test_the_repr_does_not_reveal_the_key() -> None:
    assert KEY not in repr(source(FakeTransport()))


def test_the_default_transport_only_fetches_from_the_eodhd_api() -> None:
    from hearth.valuation.eodhd import _urlopen

    with pytest.raises(ValueError, match="outside the EODHD API"):
        _urlopen("http://eodhd.com.example/api/eod/FUNDA.US")
