"""Every stored price a recompute values the books at, held compactly in memory.

The books keep it (``books.Books.prices``) so any day can be valued after the database is
closed: a statement's start and end, a year's close. It answers as ``books.stored_prices``
does, by the same rule: of every current price from the sources named, the latest day on or
before the one asked wins, then the one fetched last. Each symbol's prices are arrays of plain
numbers, looked up by bisection, so ten years of daily closes for forty securities is a few
megabytes, not a hundred and fifty thousand objects; a `Quote` is made only when asked for.
"""

from array import array
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from hearth.valuation.pricestore import PriceStore
from hearth.valuation.quotes import Quote

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_COLUMNS = ("days", "mantissas", "exponents", "currencies", "sources", "refs", "fetched")


@dataclass(slots=True)
class _Series:
    days: array[int] = field(default_factory=lambda: array("l"))
    """Each price's day, as an ordinal, ascending; within a day, the last fetched last."""
    mantissas: array[int] = field(default_factory=lambda: array("q"))
    exponents: array[int] = field(default_factory=lambda: array("b"))
    currencies: array[int] = field(default_factory=lambda: array("B"))
    sources: array[int] = field(default_factory=lambda: array("B"))
    refs: array[int] = field(default_factory=lambda: array("H"))
    fetched: array[int] = field(default_factory=lambda: array("q"))
    """When each was fetched, in microseconds since the epoch, in UTC."""
    ref_names: list[str] = field(default_factory=list)


class PriceTable:
    """Every current price of the symbols it was built for, from the sources named."""

    def __init__(self) -> None:
        self._series: dict[str, _Series] = {}
        self._currencies: list[str] = []
        self._sources: list[str] = []

    @classmethod
    def read(cls, store: PriceStore, symbols: Iterable[str], sources: Sequence[str]) -> PriceTable:
        """Every current price of `symbols` from `sources`, in one pass over the store."""
        table = cls()
        wanted = set(symbols)
        for symbol, as_of, price, currency, source, ref, fetched in store.current(sources):
            if symbol in wanted:
                table._add(
                    symbol,
                    date.fromisoformat(as_of),
                    price,
                    currency,
                    source,
                    ref,
                    datetime.fromisoformat(fetched),
                )
        table._order()
        return table

    @classmethod
    def of(cls, quotes: Iterable[Quote]) -> PriceTable:
        """A table of the quotes given, for tests and for prices held anywhere but a store."""
        table = cls()
        for quote in quotes:
            table.add(quote)
        table._order()
        return table

    def add(self, quote: Quote) -> None:
        self._add(
            quote.symbol,
            quote.as_of,
            str(quote.price),
            quote.currency,
            quote.source,
            quote.source_ref,
            quote.fetched_at,
        )

    def _add(
        self,
        symbol: str,
        as_of: date,
        price: str,
        currency: str,
        source: str,
        ref: str,
        fetched: datetime,
    ) -> None:
        series = self._series.setdefault(symbol, _Series())
        mantissa, exponent = _digits(price)
        series.days.append(as_of.toordinal())
        series.mantissas.append(mantissa)
        series.exponents.append(exponent)
        series.currencies.append(_index(self._currencies, currency))
        series.sources.append(_index(self._sources, source))
        series.refs.append(_index(series.ref_names, ref))
        series.fetched.append((fetched - _EPOCH) // timedelta(microseconds=1))

    def _order(self) -> None:
        for symbol, series in self._series.items():
            order = sorted(
                range(len(series.days)), key=lambda i: (series.days[i], series.fetched[i])
            )
            if order == list(range(len(order))):
                continue
            ordered = _Series(ref_names=series.ref_names)
            for name in (
                "days",
                "mantissas",
                "exponents",
                "currencies",
                "sources",
                "refs",
                "fetched",
            ):
                old, new = getattr(series, name), getattr(ordered, name)
                new.extend(old[i] for i in order)
            self._series[symbol] = ordered

    def _at(self, symbol: str, day: date) -> tuple[_Series, int] | None:
        series = self._series.get(symbol)
        if series is None:
            return None
        index = bisect_right(series.days, day.toordinal())
        return (series, index - 1) if index else None

    def __call__(self, symbol: str, on_or_before: date) -> Quote | None:
        """The price of `symbol` on or before the day, as `books.stored_prices` would give it."""
        found = self._at(symbol, on_or_before)
        if found is None:
            return None
        series, i = found
        return Quote(
            symbol=symbol,
            as_of=date.fromordinal(series.days[i]),
            price=_price(series, i),
            currency=self._currencies[series.currencies[i]],
            source=self._sources[series.sources[i]],
            source_ref=series.ref_names[series.refs[i]],
            fetched_at=_EPOCH + timedelta(microseconds=series.fetched[i]),
        )

    def price(self, symbol: str, on_or_before: date, currency: str) -> Decimal | None:
        """Just the price, in `currency`, or None: what valuing needs, with no `Quote` made."""
        found = self._at(symbol, on_or_before)
        if found is None:
            return None
        series, i = found
        if self._currencies[series.currencies[i]] != currency:
            return None
        return _price(series, i)

    def day(self, symbol: str, on_or_before: date) -> date | None:
        """The day of the price `price` would give."""
        found = self._at(symbol, on_or_before)
        return date.fromordinal(found[0].days[found[1]]) if found is not None else None

    def first(self, symbol: str) -> date | None:
        """The first day `symbol` has a price on: before it, nothing says what it was worth."""
        series = self._series.get(symbol)
        return date.fromordinal(series.days[0]) if series is not None and series.days else None

    def __len__(self) -> int:
        return sum(len(series.days) for series in self._series.values())


def _digits(price: str) -> tuple[int, int]:
    """A price, as stored text, as its digits and where its point is: exactly, never through a
    float. "101.2345" is (1012345, -4)."""
    whole, point, fraction = price.partition(".")
    if point and "E" not in price.upper():
        return int(whole + fraction), -len(fraction)
    sign, digits, exponent = Decimal(price).as_tuple()
    assert isinstance(exponent, int)  # noqa: S101 - a stored price is finite
    return int("".join(map(str, digits)) or "0") * (-1 if sign else 1), exponent


def _price(series: _Series, i: int) -> Decimal:
    return Decimal(series.mantissas[i]).scaleb(series.exponents[i])


def _index(names: list[str], name: str) -> int:
    try:
        return names.index(name)
    except ValueError:
        names.append(name)
        return len(names) - 1


def none() -> PriceTable:
    """A table with no prices at all."""
    return PriceTable()
