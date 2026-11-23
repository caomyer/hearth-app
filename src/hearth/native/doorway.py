"""What the native app may ask, and what it is told.

Each ask names one screen. Its answer is that screen's view from ``hearth.disclosure``, as a
JSON object whose ``state`` says what kind of answer it is:

- ``ready``: the screen's figures follow.
- ``loading``: the books have not been computed yet.
- ``failed``: the last recompute failed and there are no earlier books to show; ``summary`` is
  the one sentence anyone may read.
- ``missing``: there is no such screen, or none this viewer may see. The two are one answer.

Money is written as a decimal string, never a float, so nothing is rounded on the way to the
screen. Days are ISO dates, and moments are UTC to the second (``2026-10-15T12:00:00Z``).
"""

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from hearth import db, members
from hearth.books import Bookkeeper
from hearth.config import Settings
from hearth.disclosure import Concealed, Disclosure

type Answer = dict[str, object]

MISSING: Mapping[str, object] = {"state": "missing"}
LOADING: Mapping[str, object] = {"state": "loading"}


@dataclass(frozen=True, slots=True)
class Doorway:
    """Answers one viewer's asks from the latest books. `viewer` is a username."""

    settings: Settings
    bookkeeper: Bookkeeper
    viewer: str

    def answer(self, ask: str) -> Answer:
        screen = SCREENS.get(ask)
        if screen is None:
            return dict(MISSING)
        conn = db.connect(self.settings.database)
        try:
            return self._answer(conn, screen)
        finally:
            conn.close()

    def _answer(self, conn: sqlite3.Connection, screen: Screen) -> Answer:
        member = members.find(conn, self.viewer)
        if member is None:
            # A viewer who is no longer a member is told what anyone else would be: nothing.
            return dict(MISSING)
        view = Disclosure.load(conn, member, self.bookkeeper.status)
        if view.books is None:
            notice = view.notice()
            if notice is None:
                return dict(LOADING)
            return {"state": "failed", "summary": notice.summary}
        try:
            return {"state": "ready", **screen(view)}
        except Concealed:
            return dict(MISSING)


type Screen = Callable[[Disclosure], Answer]


def overview(view: Disclosure) -> Answer:
    """The household's net worth and its history, as the viewer may see them."""
    books = view.books
    assert books is not None  # noqa: S101 - the doorway asks a screen only once there are books
    household = view.household()
    headline = household.headline
    return {
        "computed_at": _moment(books.computed_at),
        "as_of": _day(books.as_of),
        "currency": books.currency,
        "recomputing": view.status.recomputing,
        "connected": household.connected,
        "net_worth": {
            "value": _money(headline.value),
            "is_known": headline.is_known,
            "is_partial": headline.is_partial,
            "is_floor": headline.is_floor,
            "without": [part.name for part in headline.without],
            "since_month_start": _money(headline.since_month_start),
            "over_year": _money(headline.over_year),
            "priced_on": _day(headline.priced_on),
            "stale_prices": headline.stale,
        },
        "history": [
            {"day": _day(point.day), "value": _money(point.value), "is_floor": point.is_floor}
            for point in household.history
        ],
    }


SCREENS: Mapping[str, Screen] = {"overview": overview}
"""Every screen the app may ask for, by the name it asks with."""


def _money(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _day(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _moment(value: datetime) -> str:
    """To the second, in UTC, always in one shape: ``2026-10-15T12:00:00Z``. The app shows it in
    the Mac's own time zone."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
