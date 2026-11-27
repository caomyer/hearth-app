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

Every screen but ``banks`` is of the books, so it waits for them. ``banks`` says how fresh the
viewer's own connections are and how this engine's syncs stand, which needs no books.

What an ask is given is read here, strictly: a key it does not take, a value of another kind,
or one out of range is malformed, never guessed at. Whether the viewer may see what it names
is for ``hearth.disclosure`` to decide, which answers what they may not see as missing.

Money is written as a decimal string, never a float, so nothing is rounded on the way to the
screen. Days are ISO dates, and moments are UTC to the second (``2026-10-15T12:00:00Z``).
"""

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

from hearth import db, members
from hearth.books import Bookkeeper
from hearth.clock import Clock, system_clock
from hearth.config import Settings
from hearth.connections import RegisteredConnections
from hearth.disclosure import (
    AccountGroup,
    AccountStanding,
    Bank,
    Concealed,
    Disclosure,
    Filter,
    Withheld,
)
from hearth.disclosure.balances import Standing
from hearth.disclosure.holdings import Held, HeldAccount, Price, Totals
from hearth.disclosure.journal import Entry, Show, Side
from hearth.names import readable
from hearth.native import review, statements
from hearth.native.common import (
    CATEGORY,
    DAY,
    LONGEST_ID,
    LONGEST_TEXT,
    MISSING,
    Acted,
    Answer,
    Around,
    Decided,
    Done,
    Given,
    Malformed,
    Screen,
    count_in,
    text_in,
)
from hearth.native.common import day as _day
from hearth.native.common import moment as _moment
from hearth.native.common import money as _money
from hearth.refresh import NOT_SYNCING, Syncing
from hearth.review.rulings import RulingStore
from hearth.valuation.pricestore import PriceStore

__all__ = ["SCREENS", "Acted", "Answer", "Decided", "Done", "Doorway", "Given"]

LOADING: Mapping[str, object] = {"state": "loading"}

PAGE = 100
"""How many entries of the journal an ask is answered with, unless it asks for fewer."""
MOST = 200
"""The most entries one ask is answered with: a window onto the journal, never all of it."""
FURTHEST = 1_000_000
"""The furthest into a journal an ask may start."""


@dataclass(frozen=True, slots=True)
class Doorway:
    """Answers one viewer's asks from the latest books. `viewer` is a username."""

    settings: Settings
    bookkeeper: Bookkeeper
    viewer: str
    syncing: Callable[[], Syncing] = lambda: NOT_SYNCING
    """Where this engine's syncs stand (`hearth.refresh.Refresher.status`)."""
    clock: Clock = system_clock

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
        around = Around(
            RegisteredConnections(self.settings.database),
            self.syncing(),
            RulingStore(conn),
            self.clock,
            PriceStore(conn),
        )
        if not screen.of_books:
            return {"state": "ready", **screen.write(view, asked, around)}
        if view.books is None:
            notice = view.notice()
            if notice is None:
                return dict(LOADING)
            return {"state": "failed", "summary": notice.summary}
        try:
            return {"state": "ready", **_books(view), **screen.write(view, asked, around)}
        except Concealed:
            return dict(MISSING)

    def act(self, action: str, given: Given | None = None) -> Done:
        """Make one of the viewer's review decisions (``hearth.native.review``), through their
        own desk and nothing else, then recompute the books behind it, as a page's decision
        does. Anything it names that is not on the viewer's desk is missing, like an action
        that is not there."""
        decision = review.DECISIONS.get(action)
        if decision is None:
            return Acted.MISSING
        try:
            asked = decision.read(given or {})
        except Malformed:
            return Acted.MISSING
        conn = db.connect(self.settings.database)
        try:
            member = members.find(conn, self.viewer)
            if member is None:
                return Acted.MISSING
            view = Disclosure.load(conn, member, self.bookkeeper.status)
            try:
                done = decision.make(view.review_desk(RulingStore(conn), self.clock), asked)
            except Concealed:
                return Acted.MISSING
        finally:
            conn.close()
        if done.rows:
            # A ruling changes the books, so they are recomputed, behind the app.
            self.bookkeeper.recompute_in_background()
        return done


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


def overview(view: Disclosure, _asked: object, around: Around) -> Answer:
    """The household's net worth and its history, as the viewer may see them, and what waits
    for the viewer in Review."""
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
        "review": review.waiting(view, around),
        # Whether the viewer's statements hold any tax, so counting it as spending changes
        # anything: judged on what their statements read, and nothing else.
        "taxed": view.statements_taxed(),
    }


def accounts(view: Disclosure, _asked: object, around: Around) -> Answer:
    """Every account the viewer may see, grouped, each with its balance and what it rests on."""
    return {"groups": [_group(group) for group in view.accounts(around.connections)]}


def _one(given: Given) -> str:
    """An account, by its id."""
    match dict(given):
        # A mapping pattern takes keys besides those it names, so they are refused apart.
        case {"id": str(identity)} if given.keys() == {"id"} and 0 < len(identity) <= LONGEST_ID:
            return identity
    raise Malformed


def account(view: Disclosure, asked: object, around: Around) -> Answer:
    """One account the viewer may see, as it is listed, with how far its history reaches."""
    assert isinstance(asked, str)  # noqa: S101 - read by `_one`
    found = view.standing(asked, around.connections)
    spans = () if isinstance(found.spans, Withheld) else found.spans
    unseen = None if isinstance(found.unseen_between, Withheld) else found.unseen_between
    held = None if isinstance(found.held_back, Withheld) else found.held_back
    return {
        "account": _account(found),
        "history": [
            {"origin": s.origin, "from": _day(s.earliest), "to": _day(s.latest)} for s in spans
        ],
        # Days of a carried account's history neither its imported ledger nor its bank holds.
        "unseen": None if unseen is None else {"from": _day(unseen[0]), "to": _day(unseen[1])},
        # An account its imported ledger holds back: its data ends on `through`, and its
        # bank's transactions are brought in only from `from`, the ledger's entry ahead.
        "held_back": None if held is None else {"through": _day(held[0]), "from": _day(held[1])},
    }


_TRANSACTIONS = frozenset(
    {
        "from",
        "to",
        "account",
        "category",
        "categories",
        "text",
        "show",
        "within",
        "offset",
        "limit",
    }
)

MOST_CATEGORIES = 500
"""The most categories one ask of the journal may name: more than any household books to."""

WITHIN = "statements"
"""What a journal may be narrowed to the rows of: the viewer's statements."""


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
    category = text_in(given, "category", LONGEST_TEXT)
    if category is not None and not CATEGORY.fullmatch(category):
        raise Malformed
    show = text_in(given, "show", LONGEST_TEXT)
    if show is not None and show not in {s.value for s in Show}:
        raise Malformed
    text = text_in(given, "text", LONGEST_TEXT)
    within = text_in(given, "within", LONGEST_TEXT)
    if within is not None and within != WITHIN:
        raise Malformed
    return Window(
        Filter(
            start=start,
            end=end,
            account=text_in(given, "account", LONGEST_ID),
            category=category,
            text=(text.strip() or None) if text is not None else None,
            show=Show(show) if show is not None else Show.EVERYTHING,
            categories=_categories(given),
            statement=within is not None,
        ),
        offset=count_in(given, "offset", 0, FURTHEST, default=0),
        limit=count_in(given, "limit", 1, MOST, default=PAGE),
    )


def _categories(given: Given) -> tuple[str, ...]:
    """A list of categories by their ledger names, never empty when given."""
    if "categories" not in given:
        return ()
    value = given["categories"]
    if not isinstance(value, list) or not 0 < len(value) <= MOST_CATEGORIES:
        raise Malformed
    for name in value:
        if not isinstance(name, str) or len(name) > LONGEST_TEXT or not CATEGORY.fullmatch(name):
            raise Malformed
    return tuple(value)


def _day_in(given: Given, key: str) -> date | None:
    if key not in given:
        return None
    value = given[key]
    if not isinstance(value, str) or not DAY.fullmatch(value):
        raise Malformed
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise Malformed from None


def transactions(view: Disclosure, asked: object, around: Around) -> Answer:
    """A window onto the viewer's journal, newest first, as `window` read it.

    An entry waiting on the viewer's own review says why it is suggested as it is, in the
    words Review gives it (``review.reasons``): only those, which the journal marks with their
    review id, as a reason says what their owners decided before."""
    assert isinstance(asked, Window)  # noqa: S101 - read by `window`
    found = view.journal(asked.narrowed, offset=asked.offset, limit=asked.limit)
    why = review.reasons(view, around, {e.review for e in found.window if e.review is not None})
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
        "entries": [
            _entry(found.offset + i, e, why.get(e.review) if e.review is not None else None)
            for i, e in enumerate(found.window)
        ],
    }


def banks(view: Disclosure, _asked: object, around: Around) -> Answer:
    """How fresh the viewer's own connections are, and where this engine's syncs stand: what
    it may ask, whether it is asking now, and how its last sync went for each of them."""
    syncing = around.syncing
    stopped = syncing.last.stopped if syncing.last is not None else None
    return {
        "asking": sorted(operation.value for operation in syncing.asking),
        "running": syncing.running,
        "finished_at": _moment(syncing.finished_at) if syncing.finished_at else None,
        "stopped": stopped.value if stopped is not None else None,
        "banks": [_bank(bank) for bank in view.banks(around.connections, syncing.answered)],
    }


def holdings(view: Disclosure, _asked: object, _around: Around) -> Answer:
    """What the investment accounts the viewer may see hold, each holding with its price,
    value, cost and gain, and whose positions are withheld from them."""
    found = view.holdings()
    return {
        "priced_on": _day(found.priced_on),
        "accounts": [_held_account(account) for account in found.accounts],
        "totals": _totals(found.totals),
        "withheld": [
            {
                "name": withheld.part.name,
                "owners": [owner.display_name for owner in withheld.part.owners],
                "joint": withheld.part.is_joint,
                # Their value is inside the household's total only where their total is shared.
                "in_total": withheld.total_shared,
            }
            for withheld in found.withheld
        ],
    }


def _symbol(given: Given) -> str:
    """A security, by the symbol the books hold it as."""
    match dict(given):
        case {"symbol": str(symbol)} if (
            given.keys() == {"symbol"} and 0 < len(symbol) <= LONGEST_ID
        ):
            return symbol
    raise Malformed


def price_history(view: Disclosure, asked: object, around: Around) -> Answer:
    """Where a security's price comes from and every price hearth holds of it, for a security
    the viewer may see a holding of."""
    assert isinstance(asked, str)  # noqa: S101 - read by `_symbol`
    found = view.price_history(asked, around.prices)
    return {
        "symbol": found.commodity,
        "ticker": found.ticker,
        "name": found.name,
        "freshness": found.freshness.value,
        "price": _price(found.price),
        "closes": [
            {"day": _day(close.day), "price": _money(close.price), "source": close.source}
            for close in found.closes
        ],
    }


SCREENS: Mapping[str, Screen] = {
    "overview": Screen(_nothing, overview),
    "accounts": Screen(_nothing, accounts),
    "account": Screen(_one, account),
    "transactions": Screen(window, transactions),
    "banks": Screen(_nothing, banks, of_books=False),
    "review": Screen(review.read_queue, review.queue_screen),
    "review_row": Screen(_one, review.row_screen),
    "where": Screen(_nothing, review.where_screen),
    "statement_summary": Screen(statements.read_valued, statements.summary_screen),
    "income_statement": Screen(statements.read_columns, statements.income_statement_screen),
    "balance_sheet": Screen(statements.read_valued, statements.balance_sheet_screen),
    "year_by_year": Screen(statements.read_years, statements.years_screen),
    "holdings": Screen(_nothing, holdings),
    "price_history": Screen(_symbol, price_history),
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
        # Its bank's balance meets the books only by a difference nobody has confirmed yet.
        "reconciling": found.reconciling,
    }


def _held_account(account: HeldAccount) -> Answer:
    return {
        "id": account.id,
        "name": account.name,
        "owners": [owner.display_name for owner in account.owners],
        "yours": account.part.is_viewers,
        "known": account.known,
        "why": account.why,
        "held": [_held(held) for held in account.held],
        "totals": _totals(account.totals),
    }


def _held(held: Held) -> Answer:
    return {
        "symbol": held.commodity,
        "ticker": held.ticker,
        "name": held.name,
        "is_cash": held.is_cash,
        "units": _money(held.units),
        "freshness": held.freshness.value,
        "price": _price(held.price),
        # Not known is never zero: a value, a cost or a gain the books do not hold is null.
        "value": _money(held.value),
        "cost": _money(held.cost),
        "gain": _money(held.gain),
    }


def _price(price: Price | None) -> Answer | None:
    if price is None:
        return None
    return {
        "value": _money(price.value),
        "day": _day(price.day),
        "source": price.source,
        "fetched_at": _moment(price.fetched_at),
        "age": price.age,
    }


def _totals(totals: Totals) -> Answer:
    return {
        "value": _money(totals.value),
        "unpriced": totals.unpriced,
        "cost": _money(totals.cost),
        "uncosted": totals.uncosted,
        "gain": _money(totals.gain),
        "ungained": totals.ungained,
        "waiting": totals.waiting,
    }


def _bank(bank: Bank) -> Answer:
    answered = bank.answered
    return {
        "institution": bank.institution,
        "synced_at": _moment(bank.synced_at) if bank.synced_at else None,
        "needs_sign_in": bank.needs_sign_in,
        "added": answered.added if answered is not None else None,
        "trouble": answered.trouble.value if answered and answered.trouble else None,
    }


def _entry(position: int, entry: Entry, reason: str | None) -> Answer:
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
        "reason": reason,
        "amount": _money(entry.amount),
        "sides": [_side(side) for side in entry.sides],
        "review": entry.review,
        # Narrowed to categories, the part of it that is theirs: what a statement's line adds.
        "part": _money(entry.part),
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
