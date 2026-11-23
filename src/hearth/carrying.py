"""Carrying a connected account on from the base: where the base's own record of it stops, and
which of the bank's rows the base holds already.

A household that imported its ledger (``hearth.base``) and matched a connected account to the
base's account it is (``hearth.matching``) holds that account twice over: in the base, as the
ledger kept it, and in the feed, as Plaid sends it. Hearth carries it on: its history before
its cut-over is the base's, and everything from the cut-over on is booked from the feed each
recompute, as hearth books an account it keeps itself (``hearth.plaid.booking``). Nothing is
stored twice, and the base is never written.

The cut-over (`cut_over`) is derived from the base alone, every recompute, never stored: the
later of the day after the base's last transaction touching the account and the day of its
last `balance` or `pad` on it. A balance assertion holds at the start of its day, so that
day's own rows are not in it, and are new.

Which of the bank's rows the base holds (`carry`), whatever day each is dated:

- by id, as matching reads ids (`placed`): hearth's own ``source: "plaid:<item>:<txn>"`` and
  an importer's bare ``plaid-id``;
- of the rest, by content: the most rows held one to one with the base's postings on the
  account that no feed row's id claims, for the same amount at most `APART` apart, on the day
  each posted (`most_pairs`). A card row the ledger dated on the day the charge was made and
  the bank dates on the day it posted is the one row, though they fall on either side of the
  cut-over. Matching is over the whole account, so a row before the cut-over keeps the base
  entry that is its own, and a row after it is never held by another row's entry.

A row on or after the cut-over that the base does not hold is new, and is booked. A row
before it the base does not hold is one the ledger never kept: it is counted, so a person can
see it, and never booked, since the ledger's balances already stand without it.

Only an account whose rows hearth books and whose bank reports money held or owed is carried
(`carries`): a brokerage, whose connection sends no rows hearth books, keeps its history in
the base alone, and every figure that includes it says where its data ends.

This module reads the base's entries and the feed's rows it is handed; it reads no database,
decides nothing about who may see what, and books nothing itself.
"""

from collections import defaultdict
from collections.abc import Collection, Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from beancount.core.data import Balance, Close, Directive, Open, Pad, Transaction

from hearth.connections import transaction_of
from hearth.plaid.client import Transaction as Row
from hearth.reports.common import is_balance_sheet

PLAID_ID = "plaid-id"
"""Where an importer of the household's own writes a bank transaction's Plaid id on the entry
it books, with no Item: ``plaid-id: "<transaction id>"``."""

PADDED = "P"
"""The flag of the entry beancount makes of a `pad`: a balance the ledger filled in, no row."""

POST_DATE = "post-date"
"""Where a ledger built from statements records the day the bank posted a row."""

APART = timedelta(days=7)
"""How far apart two books may date one row and still be read as the same row: a card's
charge and its posting, or a payment and its arrival, are a few days apart at most."""

QUIET = timedelta(days=10)
"""How long after the cut-over the bank's history may begin and the account still be read as
quiet, not missing: as ``figures.QUIET_START`` reads the start of a month."""

SHORT = timedelta(days=14)
"""How much later than asked for a bank's history may begin and still count as all of it, as
``feed.SHORT_GRACE_DAYS`` reads it: a later first row says the bank sent less."""

CARRIED_KINDS = frozenset({"depository", "credit", "loan"})
"""The kinds of connected account hearth carries on from the base: those whose rows it books
and whose bank reports money held or owed, which their rows move (``plaid.opening.BALANCED``).

A brokerage is not carried yet: Plaid sends it no rows hearth books, and what it holds is
reported, not moved, so carrying it on would need its holdings anchored after the base's own
positions, which is a later change's. Until then a matched brokerage is the base's alone and
its history ends at the base's last entry on it (`carries`)."""


def posted_on(entry: Transaction) -> date:
    """The day `entry` counts on: the day the bank posted it, where the ledger says."""
    posted = (entry.meta or {}).get(POST_DATE)
    if isinstance(posted, str):
        posted = date.fromisoformat(posted)
    return posted if isinstance(posted, date) else entry.date


def carries(kind: str) -> bool:
    """Whether a connected account of Plaid type `kind` is carried on from the base it is
    matched to (`CARRIED_KINDS`). The seam for carrying a brokerage on, later."""
    return kind in CARRIED_KINDS


@dataclass(frozen=True, slots=True)
class BasePosting:
    """A balance-sheet posting of a base transaction: its entry, its place in it, its account,
    the day it posted (`posted_on`) and its units."""

    entry: int
    index: int
    account: str
    day: date
    number: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class Held:
    """The base as carrying and matching read it (`read`)."""

    opened: frozenset[str]
    postings: tuple[BasePosting, ...]
    by_entry: Mapping[int, tuple[BasePosting, ...]]
    ids: Mapping[str, tuple[tuple[int, int | None], ...]]
    """Each Plaid transaction id a base entry carries, with the entry, and the posting that
    carries it itself, or None when the entry does."""
    last_entry: Mapping[str, date]
    last_balance: Mapping[str, date]
    last_pad: Mapping[str, date]
    opened_on: Mapping[str, date] = field(default_factory=dict)
    closed_on: Mapping[str, date] = field(default_factory=dict)
    moved: Mapping[str, tuple[tuple[date, str, Decimal], ...]] = field(default_factory=dict)
    """Every posting's units on each balance-sheet account, a `pad`'s included: what the base
    says the account holds, by day and currency."""
    balanced: Mapping[str, tuple[date, ...]] = field(default_factory=dict)
    """The day of every balance assertion on each account."""


def read(entries: Sequence[Directive]) -> Held:
    """What carrying needs of the base's entries: the accounts it opens and closes, every
    balance-sheet posting of its transactions, the Plaid ids they carry, and each account's
    last entry, balance assertion and pad. An entry beancount makes of a `pad` is no row of the
    bank's, though what it moves is part of what the account holds."""
    opened: dict[str, date] = {}
    closed: dict[str, date] = {}
    postings: list[BasePosting] = []
    ids: dict[str, list[tuple[int, int | None]]] = defaultdict(list)
    last_entry: dict[str, date] = {}
    last_balance: dict[str, date] = {}
    last_pad: dict[str, date] = {}
    moved: dict[str, list[tuple[date, str, Decimal]]] = defaultdict(list)
    balanced: dict[str, list[date]] = defaultdict(list)
    for number, entry in enumerate(entries):
        if isinstance(entry, Open):
            opened[entry.account] = min(entry.date, opened.get(entry.account, entry.date))
        elif isinstance(entry, Close):
            closed[entry.account] = entry.date
        elif isinstance(entry, Balance):
            last_balance[entry.account] = max(
                entry.date, last_balance.get(entry.account, entry.date)
            )
            balanced[entry.account].append(entry.date)
        elif isinstance(entry, Pad):
            last_pad[entry.account] = max(entry.date, last_pad.get(entry.account, entry.date))
        if not isinstance(entry, Transaction):
            continue
        for posting in entry.postings:
            units = posting.units
            if (
                is_balance_sheet(posting.account)
                and units is not None
                and isinstance(units.number, Decimal)
            ):
                moved[posting.account].append((entry.date, units.currency, units.number))
        if entry.flag == PADDED:
            continue
        for found in ids_of(entry.meta):
            ids[found].append((number, None))
        day = posted_on(entry)
        for index, posting in enumerate(entry.postings):
            for found in ids_of(posting.meta):
                ids[found].append((number, index))
            account = posting.account
            last_entry[account] = max(entry.date, last_entry.get(account, entry.date))
            units = posting.units
            if not is_balance_sheet(account) or units is None:
                continue
            if isinstance(units.number, Decimal):
                postings.append(
                    BasePosting(number, index, account, day, units.number, units.currency)
                )
    by_entry: dict[int, list[BasePosting]] = defaultdict(list)
    for kept in postings:
        by_entry[kept.entry].append(kept)
    return Held(
        frozenset(opened),
        tuple(postings),
        {entry: tuple(found) for entry, found in by_entry.items()},
        {found: tuple(where) for found, where in ids.items()},
        last_entry,
        last_balance,
        last_pad,
        opened,
        closed,
        {account: tuple(found) for account, found in moved.items()},
        {account: tuple(found) for account, found in balanced.items()},
    )


def ids_of(meta: Mapping[str, object] | None) -> set[str]:
    """The Plaid transaction ids `meta` carries: hearth's own `source`, and a bare `plaid-id`."""
    if not meta:
        return set()
    found = {transaction_of(meta.get("source"))}
    bare = meta.get(PLAID_ID)
    if isinstance(bare, str) and bare.strip():
        found.add(bare.strip())
    return {i for i in found if i}


def placed(held: Held, row: Row) -> tuple[BasePosting, ...]:
    """The base postings that are `row`'s bank's own side, in every entry holding its id: the
    posting that carries the id itself, or else the entry's balance-sheet postings of the
    row's amount in the books' sign. Empty when the base holds the id nowhere."""
    by_entry: dict[int, set[int | None]] = defaultdict(set)
    for entry, posting in held.ids.get(row.transaction_id, ()):
        by_entry[entry].add(posting)
    found: list[BasePosting] = []
    units = -row.amount
    for entry, carriers in by_entry.items():
        postings = held.by_entry.get(entry, ())
        pinned = {c for c in carriers if c is not None}
        if pinned:
            found += [p for p in postings if p.index in pinned]
        else:
            found += [
                p
                for p in postings
                if p.number == units and (row.currency is None or p.currency == row.currency)
            ]
    return tuple(found)


def most_pairs(
    ours: Sequence[tuple[date, Hashable]], theirs: Sequence[tuple[date, Hashable]]
) -> list[tuple[int, int]]:
    """The most pairs of `ours` and `theirs`, by index, each used once, of the same amount and
    at most `APART` apart: a maximum matching. What is paired is everything after the day,
    an amount or an amount with its currency (`money`), so 12.00 USD never pairs 12.00 EUR.

    ``hearth.comparison`` pairs rows to explain a difference, and so pairs only rows whose
    words agree and drops a row with two candidates equally near; ``hearth.plaid.pairing``
    pairs a bank's payment with a card's. Neither counts how much two accounts hold in common,
    which is what this is for. For one amount, rows on a line paired within a fixed distance,
    taking the earliest two that fit is a maximum matching: were the earliest of either side
    left out of every maximum one, pairing it with the earliest it fits frees nothing a pair
    needs, as everything later is no further from what it was paired with.
    """
    by_amount: dict[Hashable, tuple[list[tuple[date, int]], list[tuple[date, int]]]] = defaultdict(
        lambda: ([], [])
    )
    for index, (day, amount) in enumerate(ours):
        by_amount[amount][0].append((day, index))
    for index, (day, amount) in enumerate(theirs):
        by_amount[amount][1].append((day, index))
    pairs = []
    for left, right in by_amount.values():
        left.sort()
        right.sort()
        i = j = 0
        while i < len(left) and j < len(right):
            (a, x), (b, y) = left[i], right[j]
            if abs(a - b) <= APART:
                pairs.append((x, y))
                i, j = i + 1, j + 1
            elif a < b:
                i += 1
            else:
                j += 1
    return sorted(pairs)


def by_money(
    ours: Sequence[tuple[date, Decimal, str | None]],
    theirs: Sequence[tuple[date, Decimal, str]],
) -> list[tuple[int, int]]:
    """The most pairs of `ours`, the bank's rows, and `theirs`, the base's postings, each a
    day, an amount and its currency (`most_pairs`): in the same currency where a row says
    its own, and then, for a row that names none, as Plaid sometimes sends one, by amount
    alone among the postings left over, as `placed` reads such a row."""
    named = [i for i, (_, _, currency) in enumerate(ours) if currency is not None]
    pairs = [
        (named[i], j)
        for i, j in most_pairs(
            [(ours[i][0], (ours[i][1], ours[i][2])) for i in named],
            [(day, (number, currency)) for day, number, currency in theirs],
        )
    ]
    unnamed = [i for i, (_, _, currency) in enumerate(ours) if currency is None]
    left = [j for j in range(len(theirs)) if j not in {j for _, j in pairs}]
    pairs += [
        (unnamed[i], left[j])
        for i, j in most_pairs(
            [(ours[i][0], ours[i][1]) for i in unnamed],
            [(theirs[j][0], theirs[j][1]) for j in left],
        )
    ]
    return sorted(pairs)


def cut_over(held: Held, account: str) -> date | None:
    """The first day hearth books the bank's rows onto `account`, which the base opens: the
    later of the day after the base's last transaction touching it and the day of its last
    `balance` or `pad` on it, and never before the day the base opens it. None for an account
    the base does not open.

    It is the one definition of where the base's own record of an account stops: the day
    before it is the last day the base knows of (``books.frozen_through``)."""
    if account not in held.opened:
        return None
    days = [held.opened_on.get(account, date.min)]
    if account in held.last_entry:
        days.append(held.last_entry[account] + timedelta(days=1))
    days += [d for d in (held.last_balance.get(account), held.last_pad.get(account)) if d]
    return max(days)


def known_through(held: Held, account: str, today: date) -> date:
    """The last day the base knows of `account`, which it opens, on the household's own day
    `today`: the day before its cut-over, unless that is after today, as a balance the ledger
    forecast makes it. Then only what the base holds up to today counts: its last transaction
    or `pad` on the account on or before today, or the day before its last balance assertion
    on or before it, and never before the day before it opens. A forecast says nothing of the
    days before it."""
    day = cut_over(held, account)
    if day is None:
        raise ValueError(f"the base does not open {account}")
    if day - timedelta(days=1) <= today:
        return day - timedelta(days=1)
    days = [held.opened_on.get(account, date.min + timedelta(days=1)) - timedelta(days=1)]
    days += [moved_on for moved_on, _, _ in held.moved.get(account, ()) if moved_on <= today]
    days += [b - timedelta(days=1) for b in held.balanced.get(account, ()) if b <= today]
    return max(days)


def ahead(held: Held, account: str) -> date | None:
    """The latest day of anything the base holds on `account`: its last transaction,
    balance assertion or `pad`."""
    days = [
        d
        for d in (
            held.last_entry.get(account),
            held.last_balance.get(account),
            held.last_pad.get(account),
        )
        if d is not None
    ]
    return max(days, default=None)


class Status(StrEnum):
    """What carrying makes of one of the bank's rows on a carried account."""

    NEW = "new"
    """On or after the cut-over, and not in the base: booked."""
    HELD = "held"
    """In the base already, by id or by content: never booked again."""
    BEFORE = "before"
    """Before the cut-over and not in the base: a row the ledger never kept, counted and never
    booked."""


@dataclass(frozen=True, slots=True)
class Carry:
    """One carried account against the bank's rows on it (`carry`)."""

    account: str
    cut_over: date
    by_id: frozenset[str]
    """The Plaid ids of the rows the base holds by their own id."""
    by_content: frozenset[str]
    """The Plaid ids of the rows the base holds by content alone."""
    before: frozenset[str]
    """The Plaid ids of the rows dated before the cut-over that the base does not hold."""
    opening: Mapping[str, Decimal]
    """What the base says the account held at the start of the cut-over, by currency."""
    held_through: date | None
    """The newest day among the bank's rows the base holds: the base's entries for them are
    the connection's own rows, as far as coverage is concerned (``books.vouched``)."""
    reaches: date | None = None
    """The first day of the bank's history for the account (`window`): what hearth asked
    for, or, where the bank evidently sent less, its earliest posted row."""

    @property
    def held(self) -> frozenset[str]:
        return self.by_id | self.by_content

    @property
    def unseen(self) -> tuple[date, date] | None:
        """The days from the cut-over that neither the base nor the bank's history holds:
        from the cut-over to the day before the bank's history begins (`reaches`), when that
        is more than a quiet stretch later (`QUIET`). None when the bank's history reaches back
        to the cut-over: an account quiet for a few days after it is quiet, not missing. A
        month among them is never whole (``figures.covers``), and what moved in them is in the
        account's difference with its bank (``opening.Carrying``)."""
        if self.reaches is None or self.reaches <= self.cut_over + QUIET:
            return None
        return (self.cut_over, self.reaches - timedelta(days=1))

    def status(self, row: Row) -> Status:
        if row.transaction_id in self.held:
            return Status.HELD
        if row.day < self.cut_over:
            return Status.BEFORE
        return Status.NEW


def window(asked: date | None, rows: Sequence[Row]) -> date | None:
    """The first day of a bank's history, from `asked`, the first day hearth asked for, and
    the posted `rows` it sent: what was asked, unless the first row comes materially later,
    when the bank evidently sent less and its history begins at that row (`SHORT`). With
    nothing asked for, the first row."""
    earliest = min((r.day for r in rows if not r.pending), default=None)
    if asked is None:
        return earliest
    if earliest is not None and earliest > asked + SHORT:
        return earliest
    return asked


def carry(held: Held, account: str, rows: Sequence[Row], asked: date | None = None) -> Carry:
    """`account`, which the base opens, against the bank's posted `rows` on it, each
    transaction once (the module's rules), `asked` being the first day of the history hearth
    asked the bank for (`window`). A pending row is left out: it may still change or vanish,
    and its id with it."""
    day = cut_over(held, account)
    if day is None:
        raise ValueError(f"the base does not open {account}")
    posted = [row for row in rows if not row.pending]
    used: set[BasePosting] = set()
    by_ids: list[Row] = []
    rest: list[Row] = []
    for row in posted:
        mine = [p for p in placed(held, row) if p.account == account]
        if mine:
            by_ids.append(row)
            used.update(mine)
        else:
            rest.append(row)
    pool = [p for p in held.postings if p.account == account and p not in used]
    pairs = by_money(
        [(r.day, -r.amount, r.currency) for r in rest],
        [(p.day, p.number, p.currency) for p in pool],
    )
    by_contents = [rest[i] for i, _ in pairs]
    content_ids = {r.transaction_id for r in by_contents}
    opening: defaultdict[str, Decimal] = defaultdict(Decimal)
    for moved_on, currency, number in held.moved.get(account, ()):
        if moved_on < day:
            opening[currency] += number
    holding = [*by_ids, *by_contents]
    return Carry(
        account,
        day,
        frozenset(r.transaction_id for r in by_ids),
        frozenset(content_ids),
        frozenset(
            r.transaction_id for r in rest if r.transaction_id not in content_ids and r.day < day
        ),
        dict(opening),
        max((r.day for r in holding), default=None),
        window(asked, posted),
    )


def carried(held: Held, feeding: Mapping[str, Collection[str]], today: date) -> frozenset[str]:
    """The base's accounts hearth carries on: each in `feeding`, a ledger account with the
    Plaid types of every connected account confirmed to feed it, that the base opens and does
    not close, that every one of those connected accounts is of a kind carried (`carries`),
    and whose cut-over has come by `today`, the household's own day (`postponed`)."""
    return frozenset(
        account
        for account in _matched(held, feeding)
        if (cut_over(held, account) or date.max) <= today
    )


def postponed(held: Held, feeding: Mapping[str, Collection[str]], today: date) -> dict[str, date]:
    """Each account `carried` would carry on but for a cut-over after `today`, with it.

    A base with an entry dated in the future, a balance it forecast say, puts the cut-over
    there: carried on, every row before it would wait unbooked, and the account's difference
    with its bank would be dated where nothing shows it. Such an account is not carried until
    its cut-over comes; its history ends where the base's does, and every figure says so."""
    found = {account: cut_over(held, account) for account in _matched(held, feeding)}
    return {account: day for account, day in found.items() if day is not None and day > today}


def _matched(held: Held, feeding: Mapping[str, Collection[str]]) -> list[str]:
    return sorted(
        account
        for account, kinds in feeding.items()
        if account in held.opened
        and account not in held.closed_on
        and kinds
        and all(carries(kind) for kind in kinds)
    )
