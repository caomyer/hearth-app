"""Each member's connections to their institutions, and whether they are still updating.

Bank linking lands with the Plaid work; until then hearth has no connection to report, and
`NoConnections` says exactly that. The interface is what the pages are built against, so the
expired-connection state works the day connections exist. A connection is its member's alone:
nobody else can see or use it, joint accounts included.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol

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
    """Before bank linking exists there are no connections, so nothing has expired."""

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
