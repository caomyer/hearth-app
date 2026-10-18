"""EODHD end-of-day prices: equities, ETFs and mutual fund NAVs on US venues.

API reference: https://eodhd.com/financial-apis/api-for-historical-data-and-volumes
Each row's `close` is the raw close (a fund's NAV); `adjusted_close` is never read.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

from hearth.valuation.quotes import (
    PriceSourceError,
    PriceSourceUnavailableError,
    Quote,
    UnknownSymbolError,
)

BASE_URL = "https://eodhd.com/api/eod/"
EXCHANGE = "US"
CURRENCY = "USD"
TIMEOUT_SECONDS = 30

type Transport = Callable[[str], tuple[int, bytes]]
"""Performs an HTTPS GET and returns the status code and body, whatever the status."""


def _urlopen(url: str) -> tuple[int, bytes]:
    if not url.startswith(BASE_URL):
        raise ValueError("refusing to fetch a URL outside the EODHD API")
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 - https only, checked above
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def _now() -> datetime:
    return datetime.now(UTC)


class EodhdSource:
    """A `PriceSource` backed by EODHD's end-of-day API.

    Without an API key the source reports itself unavailable and is never asked; that is the
    normal state until a plan is bought, not an error.
    """

    name = "eodhd"

    def __init__(
        self,
        api_key: str | None,
        *,
        transport: Transport = _urlopen,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._api_key = api_key or None
        self._transport = transport
        self._clock = clock

    def __repr__(self) -> str:
        return f"EodhdSource(configured={self._api_key is not None})"

    def unavailable_reason(self) -> str | None:
        if self._api_key is None:
            return "no EODHD API key is configured"
        return None

    def quotes(self, symbol: str, start: date, end: date) -> list[Quote]:
        if self._api_key is None:
            raise PriceSourceUnavailableError(self.unavailable_reason())
        if start > end:
            raise ValueError(f"start {start} is after end {end}")
        ref = f"{symbol}.{EXCHANGE}"
        query = urllib.parse.urlencode(
            {
                "api_token": self._api_key,
                "fmt": "json",
                "period": "d",
                "from": start.isoformat(),
                "to": end.isoformat(),
            }
        )
        # The URL carries the key, so no error raised below may include it.
        try:
            status, body = self._transport(f"{BASE_URL}{urllib.parse.quote(ref)}?{query}")
        except OSError as error:
            raise PriceSourceError(f"EODHD could not be reached for {ref}: {error}") from None
        if status == 404:
            raise UnknownSymbolError(f"EODHD does not know {ref}")
        if status == 401:
            raise PriceSourceError("EODHD rejected the API key (HTTP 401)")
        if status != 200:
            raise PriceSourceError(f"EODHD returned HTTP {status} for {ref}")
        return self._parse(symbol, ref, body, start, end)

    def _parse(self, symbol: str, ref: str, body: bytes, start: date, end: date) -> list[Quote]:
        try:
            rows = json.loads(body, parse_float=Decimal, parse_int=Decimal)
        except ValueError:
            raise PriceSourceError(f"EODHD sent a response for {ref} that is not JSON") from None
        if not isinstance(rows, list):
            raise PriceSourceError(f"EODHD sent an unexpected response for {ref}")
        fetched_at = self._clock()
        quotes: list[Quote] = []
        seen: set[date] = set()
        for row in rows:
            # A response with any unusable row is rejected whole: storing the rows around a
            # gap would make the gap look like a day the source simply had no price for.
            try:
                as_of = date.fromisoformat(row["date"])
                close = row["close"]
            except TypeError, KeyError, ValueError:
                raise PriceSourceError(f"EODHD sent a malformed row for {ref}") from None
            if not isinstance(close, Decimal) or not close.is_finite() or close <= 0:
                raise PriceSourceError(f"EODHD sent an unusable close for {ref} on {as_of}")
            if not start <= as_of <= end or as_of in seen:
                raise PriceSourceError(f"EODHD sent an unexpected date for {ref}: {as_of}")
            seen.add(as_of)
            quotes.append(
                Quote(
                    symbol=symbol,
                    as_of=as_of,
                    price=close,
                    currency=CURRENCY,
                    source=self.name,
                    source_ref=ref,
                    fetched_at=fetched_at,
                )
            )
        return quotes
