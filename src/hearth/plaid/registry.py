"""The connections hearth holds: each Item, the accounts it reaches, and where each one feeds.

This is app state, kept in the app database with no amount in it: which member holds which
Item, through which Plaid account, at which institution, whether it still works, and when it
last synced successfully. Balances and transactions are household money, and live in the feed
store (``hearth.plaid.feed``), not here.

An Item is its holder's alone, joint accounts included (``hearth.access``); this module
records holders and decides nothing about them.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from hearth.db import transaction
from hearth.members import MemberId
from hearth.plaid import owner
from hearth.plaid.client import BankAccount, Institution

UNNAMED_INSTITUTION = "Your bank"
"""What an Item is called when neither Plaid nor Link said which institution it is at."""


class ItemState(StrEnum):
    WORKING = "working"
    LOGIN_REQUIRED = "login_required"
    """The institution wants its member to sign in again; nothing syncs until they do."""
    REMOVED = "removed"


class History(StrEnum):
    """How much of the history asked for Plaid says it has sent (``transactions_update_status``)."""

    NOT_READY = "NOT_READY"
    INITIAL = "INITIAL_UPDATE_COMPLETE"
    """The last thirty days or so are in; older history is still arriving."""
    COMPLETE = "HISTORICAL_UPDATE_COMPLETE"
    """Everything the institution will give is in. What it gave is the coverage."""


@dataclass(frozen=True, slots=True)
class Item:
    item_id: str
    member: MemberId
    credentials: str
    institution_id: str | None
    institution: str
    days_requested: int
    linked_at: datetime
    state: ItemState
    history: History | None
    last_synced_at: datetime | None
    problem: str | None
    removed_at: datetime | None
    next_check_at: datetime | None
    """When hearth next asks for history Plaid has not finished sending, while it waits."""
    next_check_by: str | None
    """The process that will ask then (``hearth.plaid.owner``)."""

    @property
    def active(self) -> bool:
        return self.state is not ItemState.REMOVED

    @property
    def requested_from(self) -> date:
        """The first day of the history asked for when it was linked: its fetched window."""
        return self.linked_at.astimezone().date() - timedelta(days=self.days_requested - 1)


@dataclass(frozen=True, slots=True)
class LinkedAccount:
    """One account an Item reaches, and the hearth account its member confirmed it feeds."""

    account_id: str
    item_id: str
    name: str
    official_name: str | None
    mask: str | None
    type: str
    subtype: str | None
    hearth_account: str | None
    confirmed_at: datetime | None

    @property
    def is_investment(self) -> bool:
        return self.type == "investment"

    @property
    def is_credit(self) -> bool:
        return self.type in {"credit", "loan"}


def _one_institution(at: str | None, other_at: str | None) -> bool:
    """Whether Items with these Plaid institution ids may be at one institution: equal ids, or
    either has none, since a name may be a Link hint or the fallback."""
    return None in (at, other_at) or at == other_at


def _item(row: sqlite3.Row) -> Item:
    def when(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    return Item(
        item_id=row["item_id"],
        member=MemberId(row["member_id"]),
        credentials=row["credentials"],
        institution_id=row["institution_id"],
        institution=row["institution_name"],
        days_requested=row["days_requested"],
        linked_at=datetime.fromisoformat(row["linked_at"]),
        state=ItemState(row["state"]),
        history=History(row["history"]) if row["history"] in set(History) else None,
        last_synced_at=when(row["last_synced_at"]),
        problem=row["problem"],
        removed_at=when(row["removed_at"]),
        next_check_at=when(row["next_check_at"]),
        next_check_by=row["next_check_by"],
    )


def _account(row: sqlite3.Row) -> LinkedAccount:
    return LinkedAccount(
        account_id=row["account_id"],
        item_id=row["item_id"],
        name=row["name"],
        official_name=row["official_name"],
        mask=row["mask"],
        type=row["type"],
        subtype=row["subtype"],
        hearth_account=row["hearth_account"],
        confirmed_at=datetime.fromisoformat(row["confirmed_at"]) if row["confirmed_at"] else None,
    )


class Registry:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def add(
        self,
        item_id: str,
        *,
        member: MemberId,
        credentials: str,
        institution: Institution,
        fallback_name: str,
        days_requested: int,
        now: datetime,
    ) -> Item:
        with transaction(self._db):
            self._db.execute(
                "INSERT INTO plaid_items (item_id, member_id, credentials, institution_id,"
                " institution_name, days_requested, linked_at, state)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'working')",
                (
                    item_id,
                    member,
                    credentials,
                    institution.id,
                    institution.name or fallback_name or UNNAMED_INSTITUTION,
                    days_requested,
                    now.isoformat(),
                ),
            )
        found = self.item(item_id)
        assert found is not None  # noqa: S101 - inserted just above
        return found

    def forget(self, item_id: str) -> None:
        """Undo `add` and `record_accounts` for an Item nothing has synced or mapped yet."""
        with transaction(self._db):
            self._db.execute("DELETE FROM plaid_accounts WHERE item_id = ?", (item_id,))
            self._db.execute("DELETE FROM plaid_items WHERE item_id = ?", (item_id,))

    def item(self, item_id: str) -> Item | None:
        row = self._db.execute("SELECT * FROM plaid_items WHERE item_id = ?", (item_id,)).fetchone()
        return _item(row) if row else None

    def items_of(self, member: MemberId) -> list[Item]:
        rows = self._db.execute(
            "SELECT * FROM plaid_items WHERE member_id = ? ORDER BY linked_at, item_id", (member,)
        )
        return [_item(row) for row in rows]

    def all_items(self) -> list[Item]:
        return [_item(row) for row in self._db.execute("SELECT * FROM plaid_items ORDER BY rowid")]

    def record_accounts(self, item_id: str, found: list[BankAccount]) -> None:
        """Add accounts the Item reaches and refresh their names; a mapping is never touched."""
        with transaction(self._db):
            self._db.executemany(
                "INSERT INTO plaid_accounts (account_id, item_id, name, official_name, mask,"
                " type, subtype) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (account_id) DO UPDATE"
                " SET name = excluded.name, official_name = excluded.official_name,"
                " mask = excluded.mask, type = excluded.type, subtype = excluded.subtype"
                " WHERE plaid_accounts.item_id = excluded.item_id",
                [
                    (a.account_id, item_id, a.name, a.official_name, a.mask, a.type, a.subtype)
                    for a in found
                ],
            )

    def accounts_of(self, item_id: str) -> list[LinkedAccount]:
        rows = self._db.execute(
            "SELECT * FROM plaid_accounts WHERE item_id = ? ORDER BY type, name, account_id",
            (item_id,),
        )
        return [_account(row) for row in rows]

    def account(self, account_id: str) -> LinkedAccount | None:
        row = self._db.execute(
            "SELECT * FROM plaid_accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        return _account(row) if row else None

    def confirmed(self) -> list[LinkedAccount]:
        """Every account a member has confirmed a mapping for, removed connections' included:
        what a connection brought in stays in the books after it is removed."""
        rows = self._db.execute(
            "SELECT * FROM plaid_accounts WHERE confirmed_at IS NOT NULL ORDER BY account_id"
        )
        return [_account(row) for row in rows]

    def connected_twice(self) -> list[tuple[LinkedAccount, ...]]:
        """Confirmed accounts that may be one real account reached through two Items: accounts
        with the same last digits on two Items that may be at one institution. Two Items are
        when both have Plaid's institution id and the ids are equal, or when either has none,
        since a name may be a Link hint or the fallback; two different ids never are. A bank
        connected again without removing the old connection leaves this, and so does one
        removed and connected again, as `confirmed` keeps a removed connection's accounts.
        Each group is the accounts sharing one mask so; none means none."""
        rows = self._db.execute(
            "SELECT a.*, i.institution_id AS at_id"
            " FROM plaid_accounts a JOIN plaid_items i USING (item_id)"
            " WHERE a.confirmed_at IS NOT NULL AND a.mask IS NOT NULL ORDER BY a.account_id"
        )
        by_mask: dict[str, list[tuple[LinkedAccount, str | None]]] = {}
        for row in rows:
            by_mask.setdefault(row["mask"], []).append((_account(row), row["at_id"]))
        groups = []
        for held in by_mask.values():
            twice = tuple(
                account
                for account, at in held
                if any(
                    _one_institution(at, other_at)
                    for other, other_at in held
                    if other.item_id != account.item_id
                )
            )
            if twice:
                groups.append(twice)
        return groups

    def reaching(
        self, institution_id: str | None, mask: str
    ) -> list[tuple[LinkedAccount, MemberId]]:
        """Accounts of connections still active, confirmed or not, that may be the one real
        account ending `mask` at `institution_id`, as `connected_twice` tells, each with the
        member who holds its connection: what an Item brought in from outside would duplicate."""
        rows = self._db.execute(
            "SELECT a.*, i.institution_id AS at_id, i.member_id AS holder"
            " FROM plaid_accounts a JOIN plaid_items i USING (item_id)"
            " WHERE a.mask = ? AND i.state != 'removed' ORDER BY a.account_id",
            (mask,),
        )
        return [
            (_account(row), MemberId(row["holder"]))
            for row in rows
            if _one_institution(institution_id, row["at_id"])
        ]

    def fed_before(self, account_id: str) -> list[LinkedAccount]:
        """Confirmed accounts on its member's own removed connections that may be the same real
        account as `account_id`, as `connected_twice` tells, the most recently linked first:
        where it went before it was connected again. Nobody else's connections are looked at."""
        rows = self._db.execute(
            "SELECT a.*, i.institution_id AS at_id, o.institution_id AS other_at"
            " FROM plaid_accounts this JOIN plaid_items o ON o.item_id = this.item_id"
            " JOIN plaid_accounts a ON a.mask = this.mask AND a.item_id != this.item_id"
            " JOIN plaid_items i ON i.item_id = a.item_id"
            " WHERE this.account_id = ? AND a.confirmed_at IS NOT NULL"
            " AND i.state = 'removed' AND i.member_id = o.member_id"
            " ORDER BY i.linked_at DESC, a.account_id",
            (account_id,),
        )
        return [_account(row) for row in rows if _one_institution(row["at_id"], row["other_at"])]

    def feeding(self, hearth_account: str) -> list[LinkedAccount]:
        """Accounts of connections still active that feed `hearth_account`."""
        rows = self._db.execute(
            "SELECT a.* FROM plaid_accounts a JOIN plaid_items i USING (item_id)"
            " WHERE a.hearth_account = ? AND i.state != 'removed'",
            (hearth_account,),
        )
        return [_account(row) for row in rows]

    def confirm(self, account_id: str, hearth_account: str, *, by: MemberId, now: datetime) -> None:
        with transaction(self._db):
            self._db.execute(
                "UPDATE plaid_accounts SET hearth_account = ?, confirmed_at = ?, confirmed_by = ?"
                " WHERE account_id = ?",
                (hearth_account, now.isoformat(), by, account_id),
            )

    def set_state(self, item_id: str, state: ItemState, *, problem: str | None = None) -> None:
        with transaction(self._db):
            self._db.execute(
                "UPDATE plaid_items SET state = ?, problem = ? WHERE item_id = ?"
                " AND state != 'removed'",
                (state.value, problem, item_id),
            )

    def name_institution(self, item_id: str, institution: Institution) -> None:
        """Record the institution Plaid says the Item is at, keeping the name it had if Plaid
        gives none."""
        with transaction(self._db):
            self._db.execute(
                "UPDATE plaid_items SET institution_id = ?,"
                " institution_name = coalesce(?, institution_name) WHERE item_id = ?",
                (institution.id, institution.name or None, item_id),
            )

    def synced(self, item_id: str, *, at: datetime, history: str | None) -> None:
        """A sync finished: the Item works, and its history is as Plaid last said."""
        with transaction(self._db):
            self._db.execute(
                "UPDATE plaid_items SET state = 'working', problem = NULL, last_synced_at = ?,"
                " history = coalesce(?, history) WHERE item_id = ? AND state != 'removed'",
                (at.isoformat(), history, item_id),
            )

    def next_check(self, item_id: str, at: datetime | None) -> None:
        """Record when this process next asks for history still arriving, or that nothing is
        waiting."""
        with transaction(self._db):
            self._db.execute(
                "UPDATE plaid_items SET next_check_at = ?, next_check_by = ? WHERE item_id = ?",
                (
                    at.isoformat() if at else None,
                    owner.this_process() if at else None,
                    item_id,
                ),
            )

    def remove(self, item_id: str, *, at: datetime) -> None:
        with transaction(self._db):
            self._db.execute(
                "UPDATE plaid_items SET state = 'removed', removed_at = ?, next_check_at = NULL,"
                " next_check_by = NULL"
                " WHERE item_id = ?",
                (at.isoformat(), item_id),
            )
