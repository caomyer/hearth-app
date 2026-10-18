"""Ledger accounts the household has claimed, and who owns each one.

Ownership is many-to-many: a joint account lists every one of its real owners.
An account with no owners is visible to nobody.
"""

import secrets
import sqlite3
from collections.abc import Collection
from dataclasses import dataclass

from hearth.members import MemberId


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    ledger_account: str
    name: str
    owners: frozenset[MemberId]


def register(
    conn: sqlite3.Connection, *, ledger_account: str, name: str, owners: Collection[MemberId]
) -> Account:
    # Opaque and unguessable, so an id in a URL says nothing about other accounts.
    account_id = secrets.token_urlsafe(12)
    conn.execute(
        "INSERT INTO accounts (id, ledger_account, name) VALUES (?, ?, ?)",
        (account_id, ledger_account, name),
    )
    conn.executemany(
        "INSERT INTO account_owners (account_id, member_id) VALUES (?, ?)",
        [(account_id, owner) for owner in owners],
    )
    return Account(account_id, ledger_account, name, frozenset(owners))


def all_accounts(conn: sqlite3.Connection) -> list[Account]:
    owners: dict[str, set[MemberId]] = {}
    for row in conn.execute("SELECT account_id, member_id FROM account_owners"):
        owners.setdefault(row["account_id"], set()).add(MemberId(row["member_id"]))
    return [
        Account(row["id"], row["ledger_account"], row["name"], frozenset(owners.get(row["id"], ())))
        for row in conn.execute("SELECT id, ledger_account, name FROM accounts ORDER BY name, id")
    ]
