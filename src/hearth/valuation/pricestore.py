"""Stored prices, each with its provenance, and corrections that never erase what came before."""

import sqlite3
from collections.abc import Iterator, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from hearth.db import transaction
from hearth.valuation.quotes import Quote

_COLUMNS = "symbol, as_of, price, currency, source, source_ref, fetched_at"
_IS_CURRENT = (
    "id = (SELECT max(id) FROM price AS newer"
    " WHERE newer.symbol = price.symbol AND newer.source = price.source"
    " AND newer.as_of = price.as_of)"
)


class Recorded(StrEnum):
    NEW = "new"
    UNCHANGED = "unchanged"
    CORRECTED = "corrected"


def _row_to_quote(row: tuple[str, str, str, str, str, str, str]) -> Quote:
    symbol, as_of, price, currency, source, source_ref, fetched_at = row
    return Quote(
        symbol=symbol,
        as_of=date.fromisoformat(as_of),
        price=Decimal(price),
        currency=currency,
        source=source,
        source_ref=source_ref,
        fetched_at=datetime.fromisoformat(fetched_at),
    )


class PriceStore:
    """Prices in the app database's `price` table, with every revision a source ever gave kept.

    The table is append-only: a row is never updated or deleted. When a source gives a different
    price for a day it already priced, the newer row is current and the older one stays as the
    record of what was believed before. Prices are text, so no Decimal passes through a float.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection
        if connection.autocommit is not True:
            raise ValueError("open the connection with hearth.db.connect, in autocommit mode")

    def record(self, quote: Quote) -> Recorded:
        """Store `quote` unless it repeats the current price for its day and source."""
        with transaction(self._db):
            revisions = self.revisions(quote.symbol, quote.as_of, quote.source)
            current = revisions[-1] if revisions else None
            if (
                current is not None
                and current.price == quote.price
                and current.currency == quote.currency
            ):
                return Recorded.UNCHANGED
            self._db.execute(
                f"INSERT INTO price ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",  # noqa: S608 - constant columns
                (
                    quote.symbol,
                    quote.as_of.isoformat(),
                    str(quote.price),
                    quote.currency,
                    quote.source,
                    quote.source_ref,
                    quote.fetched_at.isoformat(),
                ),
            )
        return Recorded.NEW if current is None else Recorded.CORRECTED

    def latest(self, symbol: str, on_or_before: date, source: str) -> Quote | None:
        """The current price from `source` for the most recent day not after `on_or_before`."""
        row = self._db.execute(
            f"SELECT {_COLUMNS} FROM price"  # noqa: S608 - constant fragments
            f" WHERE symbol = ? AND source = ? AND as_of <= ? AND {_IS_CURRENT}"
            " ORDER BY as_of DESC LIMIT 1",
            (symbol, source, on_or_before.isoformat()),
        ).fetchone()
        return None if row is None else _row_to_quote(row)

    def history(self, symbol: str, source: str) -> list[Quote]:
        """The current price from `source` for every day it priced `symbol`, oldest first."""
        rows = self._db.execute(
            f"SELECT {_COLUMNS} FROM price"  # noqa: S608 - constant fragments
            f" WHERE symbol = ? AND source = ? AND {_IS_CURRENT} ORDER BY as_of",
            (symbol, source),
        ).fetchall()
        return [_row_to_quote(row) for row in rows]

    def current(self, sources: Sequence[str]) -> Iterator[tuple[str, str, str, str, str, str, str]]:
        """The current price from each of `sources` for every day it priced any symbol, as
        the columns of `latest` and `history`, read in one pass: by symbol, source and day, the
        last revision of a day standing for it, as in `history`."""
        if not sources:
            return
        marks = ", ".join("?" for _ in sources)
        rows = self._db.execute(
            f"SELECT {_COLUMNS} FROM price"  # noqa: S608 - constant fragments and placeholders
            f" WHERE source IN ({marks}) ORDER BY symbol, source, as_of, id",
            tuple(sources),
        )
        held = None
        for row in rows:
            if held is not None and (row[0], row[4], row[1]) != (held[0], held[4], held[1]):
                yield held
            held = row
        if held is not None:
            yield held

    def revisions(self, symbol: str, as_of: date, source: str) -> list[Quote]:
        """Every price `source` has given for the day, oldest first; the last one is current."""
        rows = self._db.execute(
            f"SELECT {_COLUMNS} FROM price"  # noqa: S608 - constant columns
            " WHERE symbol = ? AND source = ? AND as_of = ? ORDER BY id",
            (symbol, source, as_of.isoformat()),
        ).fetchall()
        return [_row_to_quote(row) for row in rows]
