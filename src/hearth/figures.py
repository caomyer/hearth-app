"""Figures derived from the ledger for a set of accounts.

A member's figures cover only the accounts they own alone. Joint accounts are
figured on their own, one line per set of co-owners, so no member's figure ever
contains part of an account someone else also owns.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from hearth.accounts import Account
from hearth.ledger import Ledger, Transaction
from hearth.members import MemberId
from hearth.months import Month

_EXPENSES = "Expenses:"


@dataclass(frozen=True, slots=True)
class CategoryAmount:
    category: str
    amount: Decimal


def solo(member: MemberId, accounts: Iterable[Account]) -> list[Account]:
    """The accounts ``member`` owns alone."""
    return [account for account in accounts if account.owners == {member}]


def joint(accounts: Iterable[Account]) -> dict[frozenset[MemberId], list[Account]]:
    """Accounts with several owners, grouped by who owns them."""
    groups: defaultdict[frozenset[MemberId], list[Account]] = defaultdict(list)
    for account in accounts:
        if len(account.owners) > 1:
            groups[account.owners].append(account)
    return dict(groups)


def _spent(transaction: Transaction) -> Decimal:
    return -transaction.amount if transaction.category.startswith(_EXPENSES) else Decimal(0)


def _top_category(transaction: Transaction) -> str:
    return transaction.category.removeprefix(_EXPENSES).split(":", 1)[0]


def _in_month(accounts: Iterable[Account], ledger: Ledger, month: Month) -> list[Transaction]:
    return [
        t
        for account in accounts
        for t in ledger.transactions(account.ledger_account)
        if month.contains(t.date)
    ]


def net_worth(accounts: Iterable[Account], ledger: Ledger) -> Decimal:
    return sum((ledger.balance(a.ledger_account) for a in accounts), Decimal(0))


def spending(accounts: Iterable[Account], ledger: Ledger, month: Month) -> Decimal:
    return sum((_spent(t) for t in _in_month(accounts, ledger, month)), Decimal(0))


def breakdown(accounts: Iterable[Account], ledger: Ledger, month: Month) -> list[CategoryAmount]:
    """Spending by top-level expense category, largest first."""
    totals: defaultdict[str, Decimal] = defaultdict(Decimal)
    for transaction in _in_month(accounts, ledger, month):
        if transaction.category.startswith(_EXPENSES):
            totals[_top_category(transaction)] += _spent(transaction)
    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return [CategoryAmount(category, amount) for category, amount in ranked]
