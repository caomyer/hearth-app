"""What the native app may ask, and what it is told.

Each ask names one screen, and may be given what narrows it: which account, which days. Its
answer is that screen's view from ``hearth.disclosure``, as a JSON object whose ``state`` says
what kind of answer it is:

- ``ready``: the screen's figures follow.
- ``loading``: the books have not been computed yet.
- ``failed``: the last recompute failed and there are no earlier books to show; ``summary`` is
  the one sentence anyone may read.
- ``missing``: there is no such screen, none this viewer may see, or the ask was given
  something it does not take. All three are one answer.

What an ask is given is read here, strictly: a key it does not take, a value of another kind,
or one out of range is malformed, never guessed at. Whether the viewer may see what it names
is for ``hearth.disclosure`` to decide, which answers what they may not see as missing.

Money is written as a decimal string, never a float, so nothing is rounded on the way to the
screen. Days are ISO dates, and moments are UTC to the second (``2026-10-15T12:00:00Z``).
"""

import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from hearth import db, members
from hearth.books import Bookkeeper
from hearth.config import Settings
from hearth.connections import Connections, RegisteredConnections
from hearth.disclosure import (
    AccountGroup,
    AccountStanding,
    Concealed,
    Disclosure,
    Filter,
    Withheld,
)
from hearth.disclosure.balances import Standing
from hearth.disclosure.journal import Entry, Show, Side
from hearth.names import readable

type Answer = dict[str, object]
type Given = Mapping[str, object]
"""What an ask was given to narrow it: the ``with`` of its request, as the wire carried it."""

MISSING: Mapping[str, object] = {"state": "missing"}
LOADING: Mapping[str, object] = {"state": "loading"}

PAGE = 100
"""How many entries of the journal an ask is answered with, unless it asks for fewer."""
MOST = 200
"""The most entries one ask is answered with: a window onto the journal, never all of it."""
FURTHEST = 1_000_000
"""The furthest into a journal an ask may start."""
LONGEST_TEXT = 200
LONGEST_ID = 64


class Malformed(Exception):  # noqa: N818 - it names an outcome, not a fault
    """What an ask was given is not something it takes. Answered as missing."""


@dataclass(frozen=True, slots=True)
class Doorway:
    """Answers one viewer's asks from the latest books. `viewer` is a username."""

    settings: Settings
    bookkeeper: Bookkeeper
    viewer: str

    def answer(self, ask: str, given: Given | None = None) -> Answer:
        screen = SCREENS.get(ask)
        if screen is None:
            return dict(MISSING)
        try:
            asked = screen.read(given or {})
        except Malformed:
            return dict(MISSING)
        conn = db.connect(self.settings.database)
        try:
            return self._answer(conn, screen, asked)
        finally:
            conn.close()

    def _answer(self, conn: sqlite3.Connection, screen: Screen, asked: object) -> Answer:
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
        known = RegisteredConnections(self.settings.database)
        try:
            return {"state": "ready", **_books(view), **screen.write(view, asked, known)}
        except Concealed:
            return dict(MISSING)


@dataclass(frozen=True, slots=True)
class Screen:
    """One screen: how to read what an ask of it was given, and how to write its answer."""

    read: Callable[[Given], object]
    write: Callable[[Disclosure, object, Connections], Answer]


def _books(view: Disclosure) -> Answer:
    """What every answer says of the books it was worked out from."""
    books = view.books
    assert books is not None  # noqa: S101 - the doorway asks a screen only once there are books
    return {
        "computed_at": _moment(books.computed_at),
        "as_of": _day(books.as_of),
        "currency": books.currency,
        "recomputing": view.status.recomputing,
    }


def _nothing(given: Given) -> None:
    """A screen that takes nothing to narrow it."""
    if given:
        raise Malformed


def overview(view: Disclosure, _asked: object, _known: Connections) -> Answer:
    """The household's net worth and its history, as the viewer may see them."""
    household = view.household()
    headline = household.headline
    return {
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


def accounts(view: Disclosure, _asked: object, known: Connections) -> Answer:
    """Every account the viewer may see, grouped, each with its balance and what it rests on."""
    return {"groups": [_group(group) for group in view.accounts(known)]}


def _one(given: Given) -> str:
    """An account, by its id."""
    match dict(given):
        case {"id": str(identity)} if 0 < len(identity) <= LONGEST_ID:
            return identity
    raise Malformed


def account(view: Disclosure, asked: object, known: Connections) -> Answer:
    """One account the viewer may see, as it is listed, with how far its history reaches."""
    assert isinstance(asked, str)  # noqa: S101 - read by `_one`
    found = view.standing(asked, known)
    spans = () if isinstance(found.spans, Withheld) else found.spans
    return {
        "account": _account(found),
        "history": [
            {"origin": s.origin, "from": _day(s.earliest), "to": _day(s.latest)} for s in spans
        ],
    }


_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_CATEGORY = re.compile(r"(Expenses|Income)(:[^\s:]+)*")
_TRANSACTIONS = frozenset({"from", "to", "account", "category", "text", "show", "offset", "limit"})


@dataclass(frozen=True, slots=True)
class Window:
    """What the journal was asked for: what narrows it, and which entries of what is left."""

    narrowed: Filter
    offset: int
    limit: int


def window(given: Given) -> Window:
    """Read what the journal was asked for. Anything else, or anything of another shape, is
    malformed: the doorway takes what it knows and guesses at nothing."""
    if not given.keys() <= _TRANSACTIONS:
        raise Malformed
    start, end = _day_in(given, "from"), _day_in(given, "to")
    if start is not None and end is not None and start > end:
        raise Malformed
    category = _text_in(given, "category", LONGEST_TEXT)
    if category is not None and not _CATEGORY.fullmatch(category):
        raise Malformed
    show = _text_in(given, "show", LONGEST_TEXT)
    if show is not None and show not in {s.value for s in Show}:
        raise Malformed
    text = _text_in(given, "text", LONGEST_TEXT)
    return Window(
        Filter(
            start=start,
            end=end,
            account=_text_in(given, "account", LONGEST_ID),
            category=category,
            text=(text.strip() or None) if text is not None else None,
            show=Show(show) if show is not None else Show.EVERYTHING,
        ),
        offset=_count_in(given, "offset", 0, FURTHEST, default=0),
        limit=_count_in(given, "limit", 1, MOST, default=PAGE),
    )


def _day_in(given: Given, key: str) -> date | None:
    if key not in given:
        return None
    value = given[key]
    if not isinstance(value, str) or not _DAY.fullmatch(value):
        raise Malformed
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise Malformed from None


def _text_in(given: Given, key: str, longest: int) -> str | None:
    if key not in given:
        return None
    value = given[key]
    if not isinstance(value, str) or not 0 < len(value) <= longest:
        raise Malformed
    return value


def _count_in(given: Given, key: str, least: int, most: int, *, default: int) -> int:
    if key not in given:
        return default
    value = given[key]
    # bool is an int in Python, and no window is true entries long.
    if not isinstance(value, int) or isinstance(value, bool) or not least <= value <= most:
        raise Malformed
    return value


def transactions(view: Disclosure, asked: object, _known: Connections) -> Answer:
    """A window onto the viewer's journal, newest first, as `window` read it."""
    assert isinstance(asked, Window)  # noqa: S101 - read by `window`
    found = view.journal(asked.narrowed, offset=asked.offset, limit=asked.limit)
    counts = found.counts
    return {
        "from": _day(found.start),
        "to": _day(found.end),
        "total": found.total,
        "offset": found.offset,
        "counts": {
            "everything": counts.everything,
            "unreviewed": counts.unreviewed,
            "transfers": counts.transfers,
            "balance_checks": counts.balance_checks,
        },
        "entries": [_entry(found.offset + i, e) for i, e in enumerate(found.window)],
    }


SCREENS: Mapping[str, Screen] = {
    "overview": Screen(_nothing, overview),
    "accounts": Screen(_nothing, accounts),
    "account": Screen(_one, account),
    "transactions": Screen(window, transactions),
}
"""Every screen the app may ask for, by the name it asks with."""


def _group(group: AccountGroup) -> Answer:
    return {
        "name": group.name,
        "yours": group.yours,
        "accounts": [_account(found) for found in group.accounts],
    }


def _account(found: AccountStanding) -> Answer:
    balance = found.balance
    return {
        "id": found.id,
        "name": found.name,
        "owners": [owner.display_name for owner in found.owners],
        "yours": found.part.is_viewers,
        "kind": None if isinstance(found.kind, Withheld) else found.kind,
        "balance": (
            None
            if isinstance(balance, Withheld) or found.standing is Standing.UNSEEN
            else {
                # A balance not known is not one of zero, so none is written.
                "value": _money(balance.value) if balance.is_known else None,
                "is_known": balance.is_known,
                "is_floor": balance.is_floor,
                "unpriced": list(balance.unpriced),
            }
        ),
        "standing": found.standing.value,
        "why": found.why,
        "checked": None if isinstance(found.checked, Withheld) else _day(found.checked),
        "failed": (
            None
            if found.failed is None
            else {
                "asserted": _money(found.failed.asserted),
                "computed": _money(found.failed.computed),
                "as_of": _day(found.failed.as_of),
            }
        ),
        "updated": None if isinstance(found.updated, Withheld) else _day(found.updated),
        "behind": found.behind is True,
        "priced": _day(found.priced),
        "transactions": found.transactions,
        "institution": found.institution,
        "mask": found.mask,
        "attention": found.attention,
        "unseen_kind": found.unseen_kind,
    }


def _entry(position: int, entry: Entry) -> Answer:
    return {
        "position": position,
        "kind": entry.kind.value,
        "date": _day(entry.date),
        "payee": entry.payee,
        "narration": entry.narration,
        "accounts": [{"id": a.id, "name": a.name} for a in entry.accounts],
        "category": _category(entry.category),
        "unreviewed": entry.unreviewed,
        "suggested": entry.suggested,
        "waiting": entry.waiting.value if entry.waiting else None,
        "checked": entry.checked.value if entry.checked else None,
        "amount": _money(entry.amount),
        "sides": [_side(side) for side in entry.sides],
    }


def _side(side: Side) -> Answer:
    return {
        "amount": _money(side.amount),
        "account": {"id": side.account.id, "name": side.account.name} if side.account else None,
        "category": _category(side.category),
        "waiting": side.waiting,
        "elsewhere": side.elsewhere,
    }


def _category(name: str | None) -> Answer | None:
    """A category by its ledger name, which narrows the journal to it, and as it reads.

    Spending reads as its most specific part, in words ("Expenses:Food:Dining" reads
    "Dining"). Income reads as all of its parts, as its most specific part is often whose it
    is ("Income:Salary:Avery" reads "Salary: Avery")."""
    if name is None:
        return None
    root, _, rest = name.partition(":")
    if root == "Income" and rest:
        return {"name": name, "label": ": ".join(readable(part) for part in rest.split(":"))}
    return {"name": name, "label": readable(name.rsplit(":", 1)[-1])}


def _money(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _day(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _moment(value: datetime) -> str:
    """To the second, in UTC, always in one shape: ``2026-10-15T12:00:00Z``. The app shows it in
    the Mac's own time zone."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
