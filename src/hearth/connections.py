"""Each member's connections to their institutions, and whether they are still updating.

`RegisteredConnections` reports the Plaid connections hearth holds (``hearth.plaid``); the
demo and the tests can also describe connections in memory. A connection is its member's
alone: nobody else can see or use it, joint accounts included. The one exception: for a
ledger account you co-own, you may see that it is already fed by another connection. The
feeding member is never named and neither is the institution. On an account with exactly
two owners, a co-owner who knows they did not connect it can infer that the other owner did.
That inference is accepted: both members own the account, and hiding the fed state would
make the second owner re-link the same bank and spend one of the household's few
connections to create the duplicate feed the refusal exists to prevent.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from hearth import db
from hearth.members import MemberId


class State(StrEnum):
    WORKING = "working"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class Connection:
    """One member's link to one institution, and the ledger accounts it feeds."""

    member: MemberId
    institution: str
    ledger_accounts: tuple[str, ...]
    state: State
    data_until: date | None
    """The last day the connection brought in; for an expired one, where its data stops."""


class Connections(Protocol):
    def of(self, member: MemberId) -> Sequence[Connection]: ...

    def all(self) -> Sequence[Connection]: ...


class NoConnections:
    """No connections at all, so nothing has expired."""

    def of(self, member: MemberId) -> Sequence[Connection]:
        return ()

    def all(self) -> Sequence[Connection]:
        return ()


class KnownConnections:
    """Connections held in memory, as the demo household and the tests describe them."""

    def __init__(self, by_member: Mapping[MemberId, Sequence[Connection]]) -> None:
        self._by_member = {member: tuple(found) for member, found in by_member.items()}

    def of(self, member: MemberId) -> Sequence[Connection]:
        return self._by_member.get(member, ())

    def all(self) -> Sequence[Connection]:
        return tuple(c for found in self._by_member.values() for c in found)


class RegisteredConnections:
    """The Plaid connections in the app database, read afresh on every question."""

    def __init__(self, database: Path) -> None:
        self._database = database

    def of(self, member: MemberId) -> Sequence[Connection]:
        return tuple(c for c in self.all() if c.member == member)

    def all(self) -> Sequence[Connection]:
        conn = db.connect(self._database)
        try:
            feeds: dict[str, list[str]] = {}
            for row in conn.execute(
                "SELECT p.item_id, a.ledger_account FROM plaid_accounts p"
                " JOIN accounts a ON a.id = p.hearth_account ORDER BY a.ledger_account"
            ):
                feeds.setdefault(row["item_id"], []).append(row["ledger_account"])
            rows = conn.execute(
                "SELECT item_id, member_id, institution_name, state, last_synced_at"
                " FROM plaid_items WHERE state != 'removed' ORDER BY linked_at, item_id"
            ).fetchall()
        finally:
            conn.close()
        return tuple(
            Connection(
                member=MemberId(row["member_id"]),
                institution=row["institution_name"],
                ledger_accounts=tuple(feeds.get(row["item_id"], ())),
                state=State.EXPIRED if row["state"] == "login_required" else State.WORKING,
                data_until=(
                    datetime.fromisoformat(row["last_synced_at"]).astimezone().date()
                    if row["last_synced_at"]
                    else None
                ),
            )
            for row in rows
        )
