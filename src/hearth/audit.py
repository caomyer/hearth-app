"""The audit log: who did what to whose account, and when.

Event details come only from the fixed phrases below and never carry a monetary
figure, a payee or anything else from the ledger.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from hearth.members import MemberId


class EventKind(StrEnum):
    MEMBER_CREATED = "member_created"
    PASSWORD_RESET = "password_reset"  # noqa: S105 - an event name
    PASSWORD_CHANGED = "password_changed"  # noqa: S105 - an event name
    SIGNED_IN = "signed_in"
    SIGN_IN_FAILED = "sign_in_failed"
    SIGNED_OUT = "signed_out"
    SHARING_CHANGED = "sharing_changed"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    at: datetime
    kind: EventKind
    actor_id: MemberId | None
    subject_id: MemberId | None
    detail: str


def record(
    conn: sqlite3.Connection,
    kind: EventKind,
    *,
    at: datetime,
    actor: MemberId | None,
    subject: MemberId | None,
    detail: str = "",
) -> None:
    conn.execute(
        "INSERT INTO audit_events (at, kind, actor_id, subject_id, detail) VALUES (?, ?, ?, ?, ?)",
        (at.isoformat(), kind.value, actor, subject, detail),
    )


def all_events(conn: sqlite3.Connection) -> list[AuditEvent]:
    rows = conn.execute(
        "SELECT at, kind, actor_id, subject_id, detail FROM audit_events ORDER BY id DESC"
    )
    return [
        AuditEvent(
            at=datetime.fromisoformat(row["at"]),
            kind=EventKind(row["kind"]),
            actor_id=row["actor_id"],
            subject_id=row["subject_id"],
            detail=row["detail"],
        )
        for row in rows
    ]
