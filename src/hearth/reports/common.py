"""Small helpers shared by the ledger queries."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from beancount.core.amount import Amount
from beancount.core.data import Directive, Posting, Transaction

BALANCE_SHEET_ROOTS = ("Assets", "Liabilities")


def root(account: str) -> str:
    return account.split(":", 1)[0]


def is_balance_sheet(account: str) -> bool:
    return root(account) in BALANCE_SHEET_ROOTS


def transactions(entries: Iterable[Directive], start: date, end: date) -> Iterator[Transaction]:
    """Transactions dated from `start` to `end`, inclusive."""
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    for entry in entries:
        if isinstance(entry, Transaction) and start <= entry.date <= end:
            yield entry


@dataclass(frozen=True, slots=True)
class Units:
    number: Decimal
    currency: str


def units(posting: Posting, entry: Transaction) -> Units:
    """The posting's amount, refusing one the loader has not filled in."""
    amount = posting.units
    if not isinstance(amount, Amount) or not isinstance(amount.number, Decimal):
        raise ValueError(
            f"posting to {posting.account} on {entry.date} has no number;"
            " load the ledger with beancount's loader so amounts are interpolated"
        )
    return Units(amount.number, amount.currency)


@dataclass(frozen=True, slots=True)
class Excluded:
    """An amount left out of a figure because it is not in the figure's currency."""

    date: date
    account: str
    number: Decimal
    currency: str

    def __str__(self) -> str:
        return f"{self.number} {self.currency} in {self.account} on {self.date}"
