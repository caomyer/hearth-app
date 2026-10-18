"""Read access to the ledger, which is the source of truth for every figure.

The application never stores a monetary figure of its own; it asks the ledger.
A beancount-backed implementation of ``Ledger`` arrives with ledger import;
``InMemoryLedger`` serves tests and the demo household.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Transaction:
    """One transaction as it touches one ledger account.

    ``amount`` is signed from that account's point of view, and ``category`` is
    the counterpart ledger account, such as ``Expenses:Food:Groceries``.
    """

    date: date
    payee: str
    narration: str
    category: str
    amount: Decimal


class Ledger(Protocol):
    def balance(self, ledger_account: str) -> Decimal: ...

    def transactions(self, ledger_account: str) -> Sequence[Transaction]: ...


class InMemoryLedger:
    def __init__(
        self,
        balances: Mapping[str, Decimal],
        transactions: Mapping[str, Sequence[Transaction]],
    ) -> None:
        self._balances = dict(balances)
        self._transactions = {account: tuple(rows) for account, rows in transactions.items()}

    def balance(self, ledger_account: str) -> Decimal:
        return self._balances.get(ledger_account, Decimal(0))

    def transactions(self, ledger_account: str) -> Sequence[Transaction]:
        return self._transactions.get(ledger_account, ())
