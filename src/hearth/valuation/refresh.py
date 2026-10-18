"""Fetching prices from a source into the store, and saying plainly what did not arrive."""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from hearth.valuation.pricestore import PriceStore, Recorded
from hearth.valuation.quotes import PriceSource, PriceSourceError, UnknownSymbolError


@dataclass(frozen=True, slots=True)
class RefreshReport:
    """The outcome of one refresh. A source that could not be asked records nothing."""

    source: str
    unavailable: str | None = None
    recorded: Counter[Recorded] = field(default_factory=Counter)
    unknown: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()


def refresh_prices(
    source: PriceSource, store: PriceStore, symbols: Iterable[str], start: date, end: date
) -> RefreshReport:
    """Record every close `source` has for `symbols` from `start` to `end`."""
    reason = source.unavailable_reason()
    if reason is not None:
        return RefreshReport(source=source.name, unavailable=reason)
    recorded: Counter[Recorded] = Counter()
    unknown: list[str] = []
    failed: list[tuple[str, str]] = []
    for symbol in symbols:
        try:
            quotes = source.quotes(symbol, start, end)
        except UnknownSymbolError:
            unknown.append(symbol)
            continue
        except PriceSourceError as error:
            failed.append((symbol, str(error)))
            continue
        recorded.update(store.record(quote) for quote in quotes)
    return RefreshReport(
        source=source.name, recorded=recorded, unknown=tuple(unknown), failed=tuple(failed)
    )
