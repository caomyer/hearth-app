"""An invented household for trying hearth out. Every name and figure is made up."""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from hearth import accounts, auth, passwords
from hearth.ledger import InMemoryLedger, Transaction
from hearth.members import MemberId


@dataclass(frozen=True, slots=True)
class DemoMember:
    username: str
    display_name: str
    is_admin: bool


MEMBERS = (
    DemoMember("avery", "Avery", is_admin=True),
    DemoMember("blake", "Blake", is_admin=False),
    DemoMember("casey", "Casey", is_admin=False),
)

# ledger account, display name, owners, balance
_ACCOUNTS = (
    ("Assets:Avery:Checking", "Everyday checking", ("avery",), "8420.17"),
    ("Liabilities:Avery:Card", "Rewards card", ("avery",), "-1312.40"),
    ("Assets:Joint:Checking", "Joint checking", ("avery", "blake"), "12604.88"),
    ("Assets:Joint:Home", "Home", ("avery", "blake"), "385000.00"),
    ("Liabilities:Joint:Mortgage", "Mortgage", ("avery", "blake"), "-211450.00"),
    ("Assets:Blake:Brokerage", "Brokerage", ("blake",), "48210.55"),
    ("Assets:Casey:Checking", "Student checking", ("casey",), "1875.32"),
)

# ledger account, days ago, payee, category, amount
_TRANSACTIONS = (
    ("Assets:Avery:Checking", 1, "Corner Grocer", "Expenses:Food:Groceries", "-86.42"),
    ("Assets:Avery:Checking", 3, "Northside Transit", "Expenses:Transport:Transit", "-42.00"),
    ("Assets:Avery:Checking", 9, "Example Employer", "Income:Salary", "3410.00"),
    ("Liabilities:Avery:Card", 2, "Lantern Books", "Expenses:Leisure:Books", "-31.90"),
    ("Liabilities:Avery:Card", 6, "Hilltop Cafe", "Expenses:Food:Dining", "-24.75"),
    ("Assets:Joint:Checking", 2, "City Power & Light", "Expenses:Housing:Utilities", "-148.21"),
    ("Assets:Joint:Checking", 4, "Valley Mortgage Co", "Expenses:Housing:Mortgage", "-1890.00"),
    ("Assets:Joint:Checking", 8, "Corner Grocer", "Expenses:Food:Groceries", "-132.18"),
    ("Assets:Blake:Brokerage", 12, "Example Studio", "Income:Freelance", "1200.00"),
    ("Assets:Casey:Checking", 1, "Campus Bookstore", "Expenses:Education:Books", "-64.10"),
    ("Assets:Casey:Checking", 7, "Hilltop Cafe", "Expenses:Food:Dining", "-12.40"),
)


def seed(conn: sqlite3.Connection, *, now: datetime) -> tuple[InMemoryLedger, dict[str, str]]:
    """Create the demo household, returning its ledger and each member's password."""
    ids: dict[str, MemberId] = {}
    chosen: dict[str, str] = {}
    for demo in MEMBERS:
        member, password = auth.create_member(
            conn,
            actor=None,
            username=demo.username,
            display_name=demo.display_name,
            is_admin=demo.is_admin,
            password=passwords.temporary_password(),
            now=now,
        )
        ids[demo.username] = member.id
        chosen[demo.username] = password
    for ledger_account, name, owners, _ in _ACCOUNTS:
        accounts.register(
            conn, ledger_account=ledger_account, name=name, owners=[ids[o] for o in owners]
        )
    return _ledger(now.date()), chosen


def _ledger(today: date) -> InMemoryLedger:
    transactions: dict[str, list[Transaction]] = {}
    for ledger_account, days_ago, payee, category, amount in _TRANSACTIONS:
        transactions.setdefault(ledger_account, []).append(
            Transaction(today - timedelta(days=days_ago), payee, "", category, Decimal(amount))
        )
    balances = {ledger_account: Decimal(balance) for ledger_account, *_, balance in _ACCOUNTS}
    return InMemoryLedger(balances, transactions)
