"""What holdings are worth, and exactly how far that figure can be trusted.

The rules this module exists to enforce:

- A position with no usable price is UNPRICED: its value is None, never zero, and any total
  that leaves it out says so.
- A price older than the sessions a source can reasonably lag is STALE: it is still used, but
  it is reported with its date and age, never as current.
- `NetWorth.total` refuses to answer while anything is unpriced; the partial figure is only
  reachable as `known_total`, next to the list of what it leaves out.

Whose holdings are valued is the caller's choice, and the household's model for it lives in
`hearth.figures`: value a member's sole accounts (`figures.solo`) or one joint group's
accounts (`figures.joint`), never a share of an account.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from hearth.valuation.holdings import Holding
from hearth.valuation.quotes import Quote

STALE_AFTER_MISSED_SESSIONS = 2
"""Weekday sessions a price may lag before it is stale.

Fund NAVs publish in the evening and sources ingest them overnight, so yesterday's close is
normally the newest one available. Two further sessions of slack absorb market holidays, which
this module does not model, without letting a feed that stopped updating pass as current.
"""

type PriceLookup = Callable[[str, date], Quote | None]
"""Returns the current stored quote for a commodity on or before a date, or None."""


class Freshness(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    UNPRICED = "unpriced"


def missed_sessions(price_date: date, as_of: date) -> int:
    """Weekdays strictly between the price's day and the valuation day."""
    missed = 0
    day = price_date + timedelta(days=1)
    while day < as_of:
        if day.weekday() < 5:
            missed += 1
        day += timedelta(days=1)
    return missed


@dataclass(frozen=True, slots=True)
class Valued:
    """One holding with its value and the evidence for it.

    `reason` says why the position is unpriced or stale and is None when it is current.
    `price_age_days` counts calendar days from the price's day to the valuation day, and is
    None when no price was involved: cash, or a commodity with no price at all.
    """

    holding: Holding
    freshness: Freshness
    value: Decimal | None
    quote: Quote | None
    reason: str | None
    price_age_days: int | None


@dataclass(frozen=True, slots=True)
class NetWorth:
    as_of: date
    currency: str
    positions: tuple[Valued, ...]

    @property
    def unpriced(self) -> tuple[Valued, ...]:
        return tuple(p for p in self.positions if p.freshness is Freshness.UNPRICED)

    @property
    def stale(self) -> tuple[Valued, ...]:
        return tuple(p for p in self.positions if p.freshness is Freshness.STALE)

    @property
    def complete(self) -> bool:
        """True when every position has a value. Stale prices are reported separately."""
        return not self.unpriced

    @property
    def missing(self) -> tuple[str, ...]:
        """What the total leaves out, one line per unpriced position, for display."""
        return tuple(
            f"{p.holding.units} {p.holding.commodity} in {p.holding.account}: {p.reason}"
            for p in self.unpriced
        )

    @property
    def known_total(self) -> Decimal:
        """The sum of every position that has a value: a floor, not the net worth, if incomplete."""
        return sum((p.value for p in self.positions if p.value is not None), Decimal(0))

    @property
    def total(self) -> Decimal:
        """The net worth. Raises `IncompleteValuationError` unless every position has a value."""
        if not self.complete:
            raise IncompleteValuationError(self)
        return self.known_total


class IncompleteValuationError(Exception):
    """A total was asked for while positions are unpriced."""

    def __init__(self, net_worth: NetWorth) -> None:
        self.net_worth = net_worth
        super().__init__(
            f"net worth on {net_worth.as_of} is incomplete; unpriced: "
            + "; ".join(net_worth.missing)
        )


def _value(holding: Holding, lookup: PriceLookup, as_of: date, currency: str) -> Valued:
    if holding.commodity == currency:
        return Valued(holding, Freshness.CURRENT, holding.units, None, None, None)
    quote = lookup(holding.commodity, as_of)
    if quote is None:
        return Valued(holding, Freshness.UNPRICED, None, None, "no price recorded", None)
    if quote.as_of > as_of:
        raise ValueError(f"lookup returned a {quote.as_of} price for a {as_of} valuation")
    age = (as_of - quote.as_of).days
    if quote.currency != currency:
        reason = f"priced in {quote.currency}, and currency conversion is not supported"
        return Valued(holding, Freshness.UNPRICED, None, quote, reason, age)
    lag = missed_sessions(quote.as_of, as_of)
    if lag > STALE_AFTER_MISSED_SESSIONS:
        reason = f"latest price is from {quote.as_of}, {age} days old"
        return Valued(holding, Freshness.STALE, holding.units * quote.price, quote, reason, age)
    return Valued(holding, Freshness.CURRENT, holding.units * quote.price, quote, None, age)


def value_holdings(
    holdings: Iterable[Holding], lookup: PriceLookup, *, as_of: date, currency: str
) -> NetWorth:
    """Value every holding at `as_of` in `currency`, recording the evidence for each figure."""
    return NetWorth(
        as_of=as_of,
        currency=currency,
        positions=tuple(_value(h, lookup, as_of, currency) for h in holdings),
    )
