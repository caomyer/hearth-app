"""Ledger accounts the household has claimed, and who owns each one.

Ownership is many-to-many: a joint account lists every one of its real owners.
An account with no owners is visible to nobody.

Most accounts are fed by a connection to their bank. An account a member names instead
(`name_account`) is held by name alone: savings at a bank nobody connected, a car loan, money
lent to a sibling. Hearth sees only the money that moves between it and the household's connected
accounts, never the account's own records, so its balance is never known: not from its bank,
which tells hearth nothing, and never from the moves, whose sum is what moved, not what it
holds (``docs/books.md``).
"""

import secrets
import sqlite3
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from hearth import audit
from hearth.audit import EventKind
from hearth.db import transaction
from hearth.members import MemberId


class AccountRejectedError(ValueError):
    """An account claim hearth refuses, with a reason that can be shown as it is."""


class Kind(StrEnum):
    """What a named account is, in the member's words, which decides where it sits."""

    BANK = "bank"
    CARD = "card"
    LOAN = "loan"
    OWED = "owed"
    CASH = "cash"

    @property
    def root(self) -> str:
        """What is owed sits under Liabilities, everything else under Assets."""
        return "Liabilities" if self in (Kind.CARD, Kind.LOAN) else "Assets"

    @property
    def label(self) -> str:
        return KINDS[self][0]

    @property
    def hint(self) -> str:
        return KINDS[self][1]


KINDS = {
    Kind.BANK: ("Bank account", "Checking or savings"),
    Kind.CARD: ("Card", "A credit card you pay off"),
    Kind.LOAN: ("Loan", "Money you owe: a car, a mortgage, family"),
    Kind.OWED: ("Money owed to you", "You lent it, and expect it back"),
    Kind.CASH: ("Cash", "Only if you keep track of a cash float"),
}


@dataclass(frozen=True, slots=True)
class Named:
    """What makes an account one held by name: no connection feeds it."""

    kind: Kind
    institution: str | None
    """Where it is: a bank's name, or a person's for a private loan."""
    mask: str | None
    """Its last digits, as transfers to it name it, when the member gave them."""
    named_at: datetime


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    ledger_account: str
    name: str
    owners: frozenset[MemberId]
    named: Named | None = None
    """How it is held when a member named it rather than connected it; None otherwise."""


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
    named = {
        row["account_id"]: Named(
            Kind(row["kind"]),
            row["institution"],
            row["mask"],
            datetime.fromisoformat(row["named_at"]),
        )
        for row in conn.execute(
            "SELECT account_id, kind, institution, mask, named_at FROM named_accounts"
        )
    }
    return [
        Account(
            row["id"],
            row["ledger_account"],
            row["name"],
            frozenset(owners.get(row["id"], ())),
            named.get(row["id"]),
        )
        for row in conn.execute("SELECT id, ledger_account, name FROM accounts ORDER BY name, id")
    ]


def claim(
    conn: sqlite3.Connection,
    *,
    ledger_account: str,
    name: str,
    owners: Collection[MemberId],
    now: datetime,
) -> Account:
    """Register a ledger account as owned by `owners`, recording it in the audit log.

    Ownership decides who sees an account, so a claim is audited per owner and never
    replaces one: an account already claimed is refused, not reassigned.
    """
    owners = sorted(set(owners))
    if not owners:
        raise AccountRejectedError("An account needs at least one owner.")
    if not name.strip():
        raise AccountRejectedError("An account needs a name the household will recognise.")
    with transaction(conn):
        taken = conn.execute(
            "SELECT 1 FROM accounts WHERE ledger_account = ?", (ledger_account,)
        ).fetchone()
        if taken:
            raise AccountRejectedError(f"{ledger_account} is already claimed.")
        account = register(conn, ledger_account=ledger_account, name=name.strip(), owners=owners)
        for owner in owners:
            audit.record(conn, EventKind.ACCOUNT_CLAIMED, at=now, actor=None, subject=owner)
    return account


def name_account(
    conn: sqlite3.Connection,
    *,
    ledger_account: str,
    name: str,
    kind: Kind,
    institution: str | None,
    mask: str | None,
    owners: Collection[MemberId],
    by: MemberId,
    now: datetime,
) -> Account:
    """Register an account `by` names, held by name alone, owned by `owners`.

    Who may own it, and whether the ledger name is free, is decided before this is called
    (``hearth.disclosure``); this refuses only what no form should send. It is shared on the
    same terms as any other account. Like an account a member files a connection into, it is
    not in the audit log, which admins read whole: a line there would tell an admin its owners
    share nothing with that it exists.
    """
    owners = sorted(set(owners))
    if not owners:
        raise AccountRejectedError("An account needs at least one owner.")
    if not name.strip():
        raise AccountRejectedError("An account needs a name the household will recognise.")
    if not ledger_account.startswith(f"{kind.root}:"):
        raise AccountRejectedError(f"A {kind.label.lower()} goes under {kind.root}.")
    with transaction(conn):
        account = register(conn, ledger_account=ledger_account, name=name.strip(), owners=owners)
        conn.execute(
            "INSERT INTO named_accounts (account_id, kind, institution, mask, named_by, named_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (account.id, kind.value, institution or None, mask or None, by, now.isoformat()),
        )
    return Account(
        account.id,
        account.ledger_account,
        account.name,
        account.owners,
        Named(kind, institution or None, mask or None, now),
    )
