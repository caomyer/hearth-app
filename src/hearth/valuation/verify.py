"""Checking a source's prices against prices the fund company published itself.

Run once a month's fund NAVs have been collected from each fund company's own site into a CSV
with the header `symbol,date,nav`:

    HEARTH_EODHD_API_KEY=... uv run python -m hearth.valuation.verify published.csv

It asks EODHD for the same symbols and days and fails if any day is missing on either side or
differs by more than half a cent. The procedure, and what a failure decides, is in
`docs/valuation.md`. The same comparison serves as the daily cross-check against the prices
an institution reports through Plaid.
"""

import argparse
import csv
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from hearth.valuation.eodhd import EodhdSource
from hearth.valuation.quotes import PriceSource, PriceSourceError, Quote

TOLERANCE = Decimal("0.005")
"""Half a cent. Fund companies publish NAVs to the cent, so a faithful source is within this."""

API_KEY_VARIABLE = "HEARTH_EODHD_API_KEY"

type Reference = Mapping[tuple[str, date], Decimal]


@dataclass(frozen=True, slots=True)
class Mismatch:
    symbol: str
    as_of: date
    quoted: Decimal
    reference: Decimal

    @property
    def difference(self) -> Decimal:
        return abs(self.quoted - self.reference)


@dataclass(frozen=True, slots=True)
class Comparison:
    matched: int
    mismatched: tuple[Mismatch, ...]
    missing_from_source: tuple[tuple[str, date], ...]
    missing_from_reference: tuple[tuple[str, date], ...]

    @property
    def passed(self) -> bool:
        return not (self.mismatched or self.missing_from_source or self.missing_from_reference)


def compare(
    quotes: Iterable[Quote], reference: Reference, tolerance: Decimal = TOLERANCE
) -> Comparison:
    """Compare quotes with reference prices day by day. A difference above `tolerance` fails."""
    quoted = {(q.symbol, q.as_of): q.price for q in quotes}
    mismatched = tuple(
        Mismatch(symbol, as_of, quoted[symbol, as_of], price)
        for (symbol, as_of), price in sorted(reference.items())
        if (symbol, as_of) in quoted and abs(quoted[symbol, as_of] - price) > tolerance
    )
    return Comparison(
        matched=len(quoted.keys() & reference.keys()) - len(mismatched),
        mismatched=mismatched,
        missing_from_source=tuple(sorted(reference.keys() - quoted.keys())),
        missing_from_reference=tuple(sorted(quoted.keys() - reference.keys())),
    )


def read_reference(path: Path) -> dict[tuple[str, date], Decimal]:
    """Read published prices from a CSV with the header `symbol,date,nav`."""
    reference: dict[tuple[str, date], Decimal] = {}
    with path.open(newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != ["symbol", "date", "nav"]:
            raise ValueError(f"{path}: the header must be exactly symbol,date,nav")
        for line, row in enumerate(reader, start=2):
            try:
                key = (row["symbol"].strip(), date.fromisoformat(row["date"].strip()))
                nav = Decimal(row["nav"].strip())
            except AttributeError, ValueError, InvalidOperation:
                message = f"{path}:{line}: expected a symbol, an ISO date and a NAV"
                raise ValueError(message) from None
            if not key[0] or not nav.is_finite() or nav <= 0:
                raise ValueError(f"{path}:{line}: expected a symbol and a positive NAV")
            if key in reference:
                raise ValueError(f"{path}:{line}: {key[0]} on {key[1]} appears twice")
            reference[key] = nav
    return reference


def run(source: PriceSource, reference: Reference) -> Comparison:
    """Fetch every symbol in `reference` over the days it covers, then compare."""
    quotes: list[Quote] = []
    for symbol in sorted({symbol for symbol, _ in reference}):
        days = [as_of for s, as_of in reference if s == symbol]
        quotes.extend(source.quotes(symbol, min(days), max(days)))
    return compare(quotes, reference)


def _print(comparison: Comparison) -> None:
    for m in comparison.mismatched:
        print(
            f"MISMATCH {m.symbol} {m.as_of}: source {m.quoted}, published {m.reference},"
            f" off by {m.difference}"
        )
    for symbol, as_of in comparison.missing_from_source:
        print(f"MISSING  {symbol} {as_of}: published, but the source has no price")
    for symbol, as_of in comparison.missing_from_reference:
        print(f"EXTRA    {symbol} {as_of}: the source has a price, but none was published")
    verdict = "PASS" if comparison.passed else "FAIL"
    print(f"{verdict}: {comparison.matched} days within {TOLERANCE} of the published NAV")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hearth.valuation.verify",
        description="Compare EODHD closes with NAVs published by the fund companies.",
    )
    parser.add_argument("published", type=Path, help="CSV with the header symbol,date,nav")
    args = parser.parse_args(argv)
    source = EodhdSource(os.environ.get(API_KEY_VARIABLE))
    if (reason := source.unavailable_reason()) is not None:
        print(f"cannot verify: {reason}; set {API_KEY_VARIABLE}", file=sys.stderr)
        return 2
    try:
        comparison = run(source, read_reference(args.published))
    except (ValueError, PriceSourceError) as error:
        print(f"cannot verify: {error}", file=sys.stderr)
        return 2
    _print(comparison)
    return 0 if comparison.passed else 1


if __name__ == "__main__":
    sys.exit(main())
