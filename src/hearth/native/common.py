"""What the doorway's screens and actions share: how what the app sends is read, how money,
days and moments are written back, and what an action is told.

What the app sends is read strictly: a key a request does not take, a value of another kind,
or one out of range is `Malformed`, never guessed at, and answered as missing.
"""

import re
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from hearth.clock import Clock
from hearth.config import Settings
from hearth.connections import Connections
from hearth.disclosure import Disclosure
from hearth.native.importer import Importer
from hearth.refresh import Syncing
from hearth.review.rulings import RulingStore
from hearth.valuation.pricestore import PriceStore

type Answer = dict[str, object]
type Given = Mapping[str, object]
"""What an ask or an action was given: the ``with`` of its request, as the wire carried it."""

MISSING: Mapping[str, object] = {"state": "missing"}

LONGEST_TEXT = 200
LONGEST_ID = 64
LONGEST_PATH = 1024
"""The longest path to a file the app may name: longer than any the Mac's own panels give."""
MOST_ROWS = 5000
"""The most rows one decision may name: more than any first import's largest group."""


class Malformed(Exception):  # noqa: N818 - it names an outcome, not a fault
    """What a request was given is not something it takes. Answered as missing."""


@dataclass(frozen=True, slots=True)
class Around:
    """What a screen is written from beside the viewer's view: the connections hearth holds,
    every member's, which only disclosure narrows to the viewer's, where this engine's syncs
    stand, the rulings the viewer's review desk is built over, the stored prices, which only
    disclosure reads a security's history from, the household's settings, which say where its
    ledger is kept, and where this engine's imports of a ledger stand."""

    connections: Connections
    syncing: Syncing
    rulings: RulingStore
    clock: Clock
    prices: PriceStore
    settings: Settings
    importer: Importer | None
    viewer: str
    """The username of the member signed in."""


@dataclass(frozen=True, slots=True)
class Screen:
    """One screen: how to read what an ask of it was given, and how to write its answer."""

    read: Callable[[Given], object]
    write: Callable[[Disclosure, object, Around], Answer]
    of_books: bool = True
    """Whether it is written from the books, so waits for them and says which they are."""
    slow: bool = False
    """Whether it may take seconds, so it is worked out off the wire (``hearth.native.worker``)
    and asked again until it is there."""
    stands: Callable[[Settings], Hashable] | None = None
    """For a slow screen, where what its answer is worked out from stands, when that is less
    than everything a recompute reads (``books.standing``, the default): an answer worked out
    under anything else is never handed out."""


class Acted(StrEnum):
    """What the app is told when it asks hearth to do something."""

    STARTED = "started"
    RUNNING = "running"
    """It was already being done: a sync was running, and what it brings is what was asked."""
    NOT_ALLOWED = "not_allowed"
    """This bridge was started approved for nothing the action asks of Plaid."""
    MISSING = "missing"
    """There is no such action, it was given something it does not take, or what it names is
    not there for this viewer. All three are one outcome."""
    SIGNED_OUT = "signed_out"
    """Nobody is signed in, so nothing is done and nothing is said of what could be."""
    ACCEPTED = "accepted"
    """Every row shown was accepted as suggested."""
    BOOKED = "booked"
    """Saved under the category chosen."""
    MOVED = "moved"
    """Moved to the account of the viewer's own they chose."""
    EXCLUDED = "excluded"
    """Not a transaction: it books nothing."""
    UNDONE = "undone"
    """The decision was taken back, and its rows wait again."""
    CHANGED = "changed"
    """Some of what was shown changed since, so it was left waiting; the rest, if any, was
    decided."""
    NOT_ONE_DECISION = "not_one_decision"
    """The group is not one kind of transaction, so it cannot be accepted all at once."""
    NOT_AN_ACCOUNT = "not_an_account"
    """The name given is not a full account name."""
    NOT_YOURS = "not_yours"
    """A row goes only to a category or one of the viewer's own accounts; which of the two
    rules the account broke is never said."""
    BANKS_ACCOUNT = "banks_account"
    """That account's balance is the one its bank reports, so only the bank's rows go in it."""
    BANKS_ROW = "banks_row"
    """Its bank counts the row in the balance it reports, so it cannot be left out."""
    NOT_OPEN = "not_open"
    """The books hold that category, but close it before a row's day or keep it for another
    currency, so no row may be booked there (``desk.NotOpen``)."""
    MATCHED = "matched"
    """The connected account is matched to the imported ledger's account chosen."""
    REFUSED = "refused"
    """The match, or whose an account is, was refused since it was offered, as things changed:
    ask again."""
    ASSIGNED = "assigned"
    """Each of the ledger's accounts named is now owned by the members said."""


@dataclass(frozen=True, slots=True)
class Decided:
    """What a decision did: its outcome, the ruling an Undo would take back, how many rows it
    decided, and how many of those shown it left waiting because they changed."""

    outcome: Acted
    ruling: int | None = None
    rows: int = 0
    changed: int = 0

    def wire(self) -> Answer:
        return {
            "outcome": self.outcome.value,
            "ruling": self.ruling,
            "rows": self.rows,
            "changed": self.changed,
        }


type Done = Acted | Decided
"""What an action did: an outcome alone, or, for a decision, what it decided."""


def text_in(given: Given, key: str, longest: int) -> str | None:
    if key not in given:
        return None
    value = given[key]
    if not isinstance(value, str) or not 0 < len(value) <= longest:
        raise Malformed
    return value


def count_in(given: Given, key: str, least: int, most: int, *, default: int) -> int:
    if key not in given:
        return default
    value = given[key]
    # bool is an int in Python, and no window is true entries long.
    if not isinstance(value, int) or isinstance(value, bool) or not least <= value <= most:
        raise Malformed
    return value


def table_in(given: Given, key: str) -> dict[str, str]:
    """An object of ids to text, as a decision names the rows it showed: never empty, never
    more than `MOST_ROWS`, every key an id and every value text."""
    value = given.get(key)
    if not isinstance(value, dict) or not 0 < len(value) <= MOST_ROWS:
        raise Malformed
    found: dict[str, str] = {}
    for name, said in value.items():
        if not (isinstance(name, str) and 0 < len(name) <= LONGEST_ID):
            raise Malformed
        if not (isinstance(said, str) and 0 < len(said) <= LONGEST_TEXT):
            raise Malformed
        found[name] = said
    return found


def path_in(given: Given, key: str) -> str:
    """An absolute path to a file, as the Mac's open panel gives one: never relative, which
    would be read against wherever the engine was started, and never with a NUL in it."""
    value = text_in(given, key, LONGEST_PATH)
    if value is None or not value.startswith("/") or "\0" in value:
        raise Malformed
    return value


def only(given: Given, keys: frozenset[str]) -> None:
    if not given.keys() <= keys:
        raise Malformed


DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
CATEGORY = re.compile(r"(Expenses|Income)(:[^\s:]+)*")


def money(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def day(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def moment(value: datetime) -> str:
    """To the second, in UTC, always in one shape: ``2026-10-15T12:00:00Z``. The app shows it in
    the Mac's own time zone."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
