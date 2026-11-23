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

What a connection brings in reaches the books through the import, which marks every entry it
books from one with `plaid_source`. Only entries so marked are that connection's data in the
books (`books.compute`), so a connection vouches for nothing the books do not hold.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from hearth import db
from hearth.members import MemberId

PLAID = "plaid"
"""The origin of every entry booked from a connection: the first field of `plaid_source`,
which is also how `reports.coverage` reads origins.

The format `plaid:<item_id>:<transaction_id>` is a contract between the import that books
Plaid transactions, which writes it, and coverage's rule that a connection vouches only for
entries booked from it (`books.vouched`), which parses it back with `item_of`. The tests pin
it, malformed input included, so neither side drifts.
"""


def plaid_source(item_id: str, transaction_id: str) -> str:
    """The `source` metadata the import must write on an entry booked from Plaid Item
    `item_id`'s transaction `transaction_id`: `plaid:<item_id>:<transaction id>`.

    `item_of` parses it back, so one it could misread is refused: an empty id, or an Item id
    with a colon, which would move where the Item id ends.
    """
    if not item_id or ":" in item_id or not transaction_id:
        raise ValueError("a Plaid source needs an Item id without a colon and a transaction id")
    return f"{PLAID}:{item_id}:{transaction_id}"


BALANCE = "balance"


def plaid_balance_source(item_id: str) -> str:
    """The `source` of an opening balance derived from the balance Plaid Item `item_id`
    reported (``hearth.plaid.opening``): `plaid:<item_id>:balance`, in the same format, so
    `item_of` reads its Item as it reads a transaction's. No Plaid transaction id is ``balance``."""
    return plaid_source(item_id, BALANCE)


def item_of(source: object) -> str | None:
    """The Plaid Item an entry's `source` metadata says it was booked from, if any."""
    if not isinstance(source, str):
        return None
    origin, _, rest = source.partition(":")
    item_id, _, transaction_id = rest.partition(":")
    return item_id if origin == PLAID and item_id and transaction_id else None


def transaction_of(source: object) -> str | None:
    """The Plaid transaction an entry's `source` metadata says it was booked from, if any: the
    last field of `plaid_source`, read as `item_of` reads the first. An opening balance's
    source (`plaid_balance_source`) names no transaction."""
    if item_of(source) is None:
        return None
    assert isinstance(source, str)  # noqa: S101 - item_of reads only a string
    transaction_id = source.split(":", 2)[2]
    return transaction_id if transaction_id != BALANCE else None


class State(StrEnum):
    WORKING = "working"
    EXPIRED = "expired"


class Trouble(StrEnum):
    """What kept one ask of a connection from bringing anything in, in words a member can act
    on. The detail, Plaid's own codes among it, is in ``hearth.log``, never here."""

    SIGN_IN = "sign_in"
    """The bank wants its member to sign in to it again (Plaid's ``ITEM_LOGIN_REQUIRED``)."""
    UNREACHABLE = "unreachable"
    """Plaid could not be reached at all: no network, or Plaid is down."""
    KEYS = "keys"
    """The connection's access token or its Plaid account's keys could not be opened: the
    Keychain is locked or unreachable, or holds no key for this household's vault."""
    REFUSED = "refused"
    """Plaid answered with an error of its own, other than wanting a sign-in."""
    FAILED = "failed"
    """Anything else, such as an answer hearth could not read."""


@dataclass(frozen=True, slots=True)
class Answered:
    """How one connection answered the last time this hearth asked it, sync and brokerage
    reads together: how many new transactions it sent, and what went wrong, if anything."""

    added: int
    trouble: Trouble | None = None


@dataclass(frozen=True, slots=True)
class Connection:
    """One member's link to one institution, and the ledger accounts it feeds."""

    member: MemberId
    institution: str
    ledger_accounts: tuple[str, ...]
    state: State
    data_until: date | None
    """The last day the connection brought in; for an expired one, where its data stops."""
    item_id: str | None = None
    """The Plaid Item it is, which names the entries booked from it (`plaid_source`)."""
    synced_at: datetime | None = None
    """When it last synced to the end: when its bank was last asked and answered in full."""


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
                item_id=row["item_id"],
                synced_at=(
                    datetime.fromisoformat(row["last_synced_at"]) if row["last_synced_at"] else None
                ),
            )
            for row in rows
        )
