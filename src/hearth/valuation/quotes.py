"""The price source interface and the quote it produces."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Quote:
    """The raw closing price, or NAV, of one security on one trading day, and where it came from.

    `price` is always the figure as traded or as published by the fund: never adjusted for
    dividends or splits, which would misstate what a holding was worth on `as_of`.
    """

    symbol: str
    as_of: date
    price: Decimal
    currency: str
    source: str
    source_ref: str
    fetched_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.price, Decimal):
            raise TypeError(f"price must be a Decimal, not {type(self.price).__name__}")
        if not self.price.is_finite() or self.price <= 0:
            raise ValueError(f"price must be positive and finite, got {self.price}")
        if self.fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")


class PriceSourceUnavailableError(Exception):
    """The source cannot be asked at all, for example because no API key is configured."""


class PriceSourceError(Exception):
    """The source was asked and did not give a usable answer."""


class UnknownSymbolError(PriceSourceError):
    """The source does not know the symbol."""


class PriceSource(Protocol):
    """Somewhere raw daily closes come from. Implementations must be swappable."""

    @property
    def name(self) -> str:
        """A stable identifier, stored with every quote this source produces."""
        ...

    def unavailable_reason(self) -> str | None:
        """Why the source cannot be asked right now, or None when it can."""
        ...

    def quotes(self, symbol: str, start: date, end: date) -> list[Quote]:
        """Every raw close for `symbol` on trading days from `start` to `end`, inclusive.

        Days the source has no price for are absent, never filled in. Raises
        `PriceSourceUnavailableError` when `unavailable_reason()` is not None.
        """
        ...
