"""The Plaid accounts hearth links through, and how many connections each has left.

A Plaid account's free production tier allows a fixed number of Items, and an Item is one
login at one institution however many accounts it reaches. So the scarce thing is banks, not
accounts, and each Plaid account carries its own allowance. That is why a member's own Plaid
account is the default: every member who brings one doubles nobody's cost and adds a whole
allowance, and their data never passes through an account registered to someone else. The
household may also keep one Plaid account, entered by an admin, for members who choose not
to register their own; a member links through it only after choosing to.

The client id and secret are sealed in the vault (``hearth.vault``), bound to their set.
Only what may be shown about a set is kept in the clear: its environment, its allowance, how
many of its Items are used outside hearth (which hearth cannot see and so is told), and who
registered it.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from hearth.db import transaction
from hearth.members import MemberId
from hearth.plaid.client import Credentials, Environment
from hearth.vault.secret import Secret
from hearth.vault.tokens import TokenVault

HOUSEHOLD = "household"
DEFAULT_ITEM_LIMIT = 10
"""Plaid's free production tier allows about ten Items per Plaid account."""

_CLIENT_ID = "client_id"
_SECRET = "secret"  # noqa: S105 - the name a credential is stored under, not a credential


def personal(member: MemberId) -> str:
    """The scope of a member's own Plaid account."""
    return f"member:{member}"


class CredentialsInUseError(ValueError):
    """The set still has connections, which only its own keys can sync or remove."""


@dataclass(frozen=True, slots=True)
class CredentialSet:
    scope: str
    owner: MemberId | None
    """The member whose own Plaid account this is; None for the household's."""
    registered_by: MemberId
    environment: Environment
    item_limit: int
    used_elsewhere: int
    saved_at: datetime

    @property
    def is_household(self) -> bool:
        return self.owner is None


@dataclass(frozen=True, slots=True)
class Capacity:
    """How many connections a Plaid account has left: its allowance, less what is in use."""

    limit: int
    used_elsewhere: int
    in_hearth: int

    @property
    def used(self) -> int:
        return self.used_elsewhere + self.in_hearth

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)


class CredentialStore:
    def __init__(self, connection: sqlite3.Connection, vault: TokenVault | None) -> None:
        """`vault` may be None to read only what may be shown, which never needs a key."""
        self._db = connection
        self._vault_or_none = vault

    @property
    def _vault(self) -> TokenVault:
        if self._vault_or_none is None:
            raise RuntimeError("this credential store was opened without the vault")
        return self._vault_or_none

    def __repr__(self) -> str:
        return "CredentialStore()"

    def get(self, scope: str) -> CredentialSet | None:
        row = self._db.execute(
            "SELECT scope, member_id, registered_by, environment, item_limit, used_elsewhere,"
            " saved_at FROM plaid_credentials WHERE scope = ?",
            (scope,),
        ).fetchone()
        if row is None:
            return None
        return CredentialSet(
            scope=row["scope"],
            owner=None if row["member_id"] is None else MemberId(row["member_id"]),
            registered_by=MemberId(row["registered_by"]),
            environment=Environment(row["environment"]),
            item_limit=row["item_limit"],
            used_elsewhere=row["used_elsewhere"],
            saved_at=datetime.fromisoformat(row["saved_at"]),
        )

    def save(
        self,
        scope: str,
        *,
        owner: MemberId | None,
        registered_by: MemberId,
        client_id: Secret,
        secret: Secret,
        environment: Environment,
        item_limit: int,
        used_elsewhere: int,
        now: datetime,
    ) -> None:
        """Store a set's keys and what may be shown about it.

        A set with connections may have its secret replaced, but not its client id or its
        environment: an Item's access token works only with the client and the environment
        that created it, so changing either would strand every connection on the old one,
        still using its slots.
        """
        if (scope == HOUSEHOLD) != (owner is None):
            raise ValueError("the household's set has no owner, and every other set has one")
        if item_limit < 1 or used_elsewhere < 0:
            raise ValueError("an allowance is at least one, and nothing is used below zero")
        stored = self.get(scope)
        if (
            self.in_use(scope)
            and stored is not None
            and (
                stored.environment is not environment
                or self._vault.get_credential(scope, _CLIENT_ID) != client_id
            )
        ):
            raise CredentialsInUseError(
                "this Plaid account still has connections; remove them before switching"
                " to a different Plaid account or environment"
            )
        self._vault.put_credential(scope, _CLIENT_ID, client_id)
        self._vault.put_credential(scope, _SECRET, secret)
        with transaction(self._db):
            self._db.execute(
                "INSERT INTO plaid_credentials (scope, member_id, registered_by, environment,"
                " item_limit, used_elsewhere, saved_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (scope) DO UPDATE SET registered_by = excluded.registered_by,"
                " environment = excluded.environment, item_limit = excluded.item_limit,"
                " used_elsewhere = excluded.used_elsewhere, saved_at = excluded.saved_at",
                (
                    scope,
                    owner,
                    registered_by,
                    environment.value,
                    item_limit,
                    used_elsewhere,
                    now.isoformat(),
                ),
            )

    def remove(self, scope: str) -> None:
        """Forget a set. Refused while it has connections, which only its keys can remove."""
        if self.in_use(scope):
            raise CredentialsInUseError("remove this Plaid account's connections first")
        self._vault.delete_credentials(scope)
        with transaction(self._db):
            self._db.execute("DELETE FROM plaid_credentials WHERE scope = ?", (scope,))

    def credentials(self, scope: str) -> Credentials:
        """The set's keys, for a client. Never for a page."""
        stored = self.get(scope)
        if stored is None:
            raise LookupError(f"no Plaid account is saved as {scope}")
        return Credentials(
            client_id=self._vault.get_credential(scope, _CLIENT_ID),
            secret=self._vault.get_credential(scope, _SECRET),
            environment=stored.environment,
        )

    def in_use(self, scope: str) -> int:
        """Connections in hearth that still hold one of the set's slots."""
        (count,) = self._db.execute(
            "SELECT count(*) FROM plaid_items WHERE credentials = ? AND state != 'removed'",
            (scope,),
        ).fetchone()
        return int(count)

    def capacity(self, scope: str) -> Capacity | None:
        stored = self.get(scope)
        if stored is None:
            return None
        return Capacity(stored.item_limit, stored.used_elsewhere, self.in_use(scope))

    def brought_in(self, scope: str) -> None:
        """One of the set's Items in use outside hearth is now held by hearth: it counts in
        hearth from here, and no longer elsewhere, so the allowance left is unchanged."""
        with transaction(self._db):
            self._db.execute(
                "UPDATE plaid_credentials SET used_elsewhere = max(used_elsewhere - 1, 0)"
                " WHERE scope = ?",
                (scope,),
            )

    def chose_household(self, member: MemberId) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM plaid_household_users WHERE member_id = ?", (member,)
        ).fetchone()
        return row is not None

    def choose_household(self, member: MemberId, *, chosen: bool, now: datetime) -> None:
        with transaction(self._db):
            if chosen:
                self._db.execute(
                    "INSERT OR IGNORE INTO plaid_household_users (member_id, chosen_at)"
                    " VALUES (?, ?)",
                    (member, now.isoformat()),
                )
            else:
                self._db.execute("DELETE FROM plaid_household_users WHERE member_id = ?", (member,))

    def scope_for(self, member: MemberId) -> str | None:
        """The set a member's next connection would use: their own, or the household's if
        they chose it and it exists. Never someone else's own."""
        if self.get(personal(member)) is not None:
            return personal(member)
        if self.chose_household(member) and self.get(HOUSEHOLD) is not None:
            return HOUSEHOLD
        return None
