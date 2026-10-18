"""How often a member wants to review, and whether a review is due.

Hearth runs on the household's own machine and sends no email, so a reminder is something a
page shows: the count waiting, when the member last reviewed, and that their review is due once
their chosen interval has passed since then with something waiting. Nothing is ever accepted
because a reminder went unanswered; there is no auto-accept, after a delay or otherwise.
"""

import calendar
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from hearth.members import MemberId


class Cadence(StrEnum):
    OFF = "off"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"

    @property
    def label(self) -> str:
        return "Never" if self is Cadence.OFF else self.value.title()

    def after(self, moment: datetime) -> datetime | None:
        """When a review is due again after one at `moment`; `None` when never."""
        match self:
            case Cadence.OFF:
                return None
            case Cadence.DAILY:
                return moment + timedelta(days=1)
            case Cadence.WEEKLY:
                return moment + timedelta(weeks=1)
            case Cadence.MONTHLY:
                return _next_month(moment)


DEFAULT = Cadence.WEEKLY
"""The design's default: weekly, and only if something is waiting."""


def _next_month(moment: datetime) -> datetime:
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


@dataclass(frozen=True, slots=True)
class Reminder:
    """What a page tells one member about their own review. Never about anyone else's."""

    cadence: Cadence
    waiting: int
    last_reviewed: datetime | None
    due_at: datetime | None
    """When the next review falls due; `None` when the member asked never to be reminded."""
    due: bool


def reminder(
    cadence: Cadence, waiting: int, last_reviewed: datetime | None, now: datetime
) -> Reminder:
    """A member who has never reviewed is due as soon as something waits for them."""
    due_at = (
        None if cadence is Cadence.OFF else cadence.after(last_reviewed) if last_reviewed else now
    )
    due = waiting > 0 and due_at is not None and due_at <= now
    return Reminder(cadence, waiting, last_reviewed, due_at, due)


def load(conn: sqlite3.Connection, member_id: MemberId) -> Cadence:
    row = conn.execute(
        "SELECT cadence FROM review_cadence WHERE member_id = ?", (member_id,)
    ).fetchone()
    return DEFAULT if row is None else Cadence(row["cadence"])


def save(conn: sqlite3.Connection, member_id: MemberId, cadence: Cadence, at: datetime) -> None:
    conn.execute(
        "INSERT INTO review_cadence (member_id, cadence, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT (member_id) DO UPDATE SET"
        " cadence = excluded.cadence, updated_at = excluded.updated_at",
        (member_id, cadence.value, at.isoformat()),
    )
