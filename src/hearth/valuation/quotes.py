"""The price source interface and the quote it produces."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

MOST_DIGITS = 15
"""The most digits a price typed by hand may have: more than any close or NAV is quoted to."""
LEAST_PRICE = Decimal("0.000000000001")
MOST_PRICE = Decimal("1000000000")
"""The range a price typed by hand must be in: wider than anything a household holds trades at,
narrow enough that a slip of the keyboard is not a price."""


def enterable(price: Decimal) -> bool:
    """Whether a price typed by hand is one to keep: finite, in range, and not too long."""
    if not price.is_finite() or not LEAST_PRICE <= price <= MOST_PRICE:
        return False
    return len(as_entered(price).as_tuple().digits) <= MOST_DIGITS


def as_entered(price: Decimal) -> Decimal:
    """A price typed by hand as it is checked and kept: without the zeros that say nothing,
    and never with a positive exponent, so "1.000000000000000000000000000000" is kept as 1 and
    "1E+2" as 100. A very small price is still written in exponent form, as Decimal writes it
    ("0.000000000001" is kept as 1E-12); it packs all the same."""
    found = price.normalize()
    exponent = found.as_tuple().exponent
    return found.quantize(Decimal(1)) if isinstance(exponent, int) and exponent > 0 else found


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
