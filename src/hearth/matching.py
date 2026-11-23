"""Which of the base's accounts each connected bank account is, and what carrying it on will
meet.

A household that imported its ledger (``hearth.base``) and connects its banks holds each bank
account twice over: in the base, as the ledger kept it, and in the feed, as Plaid sends it.
Before hearth can carry an account on from where the base stops, it must know which of the
base's accounts each connected one is. Nobody should have to say what the ledger already says,
so hearth proposes it from the ledger itself (`propose`):

- By id. A ledger fed from hearth carries each bank transaction's Plaid transaction id: as
  hearth's own `source` (`connections.plaid_source`), or as a bare `plaid-id` that an importer
  of the household's own writes, with no Item. A base entry holding one of a connected
  account's feed ids is that row, and the bank's own side of it is the balance-sheet posting
  of the row's amount in the sign the bank means it (``hearth.plaid.booking``: Plaid's sign is
  money leaving the account). A card payment's entry holds the card's posting and the paying
  account's, of opposite signs, so the paying account's row finds the paying account's
  posting and never the card's. A posting that carries the id itself, as each side of a
  transfer hearth booked does, is that side. The account a strict majority of the placed ids
  land on is proposed, saying how many agree and how many land elsewhere.
- By content, when none of its ids is in the base: a bank connected again, which Plaid gives
  new ids, or a base kept from statements. Each account the base opens on the connected
  account's side (a depository account under Assets, a card or a loan under Liabilities) is
  held against the feed's rows, counting the most rows the two hold one to one, for the same
  amount at most a week apart (`most_pairs`). The best is proposed only when it clearly beats
  the rest (`clearly`).
- Otherwise nothing is proposed, and the household says which account it is, or files it as a
  new one.

Ownership is never decided here, and a match never changes it: an account claimed by members
the connection's holder is not among is never proposed to that connection, as the gate
answers for the holder (``hearth.disclosure.gate``), nor one another connected account is
matched to already, as one ledger account is fed by one connection at most.

``hearth match-accounts`` prints every connected account with its proposal and, given
``--confirm``, records the match as the mapping a member confirms on the Connect page
(`Registry.confirm`), which books nothing into an account the base opens
(``hearth.plaid.booking``): nothing is booked differently for a match. `facing` says, for each
matched account, what carrying it on will meet, the evidence the cut-over rule is to be built
on.

This reads the whole household's base and feed for whoever runs hearth on its machine, as
``hearth plaid items`` does; it is not a page. What it says is counts, days and the names of
accounts, never a payee or an amount.
"""

import sqlite3
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from beancount.core.data import Balance, Directive, Open, Pad, Transaction

from hearth import books, db
from hearth.accounts import Account, all_accounts
from hearth.base import Base
from hearth.comparison import APART, posted_on
from hearth.config import Settings
from hearth.connections import transaction_of
from hearth.disclosure.gate import Gate
from hearth.members import Member, get
from hearth.plaid import feed
from hearth.plaid.client import Transaction as Row
from hearth.plaid.feed import Feed
from hearth.plaid.registry import LinkedAccount, Registry
from hearth.reports.common import is_balance_sheet, root

PLAID_ID = "plaid-id"
"""Where an importer of the household's own writes a bank transaction's Plaid id on the entry
it books, with no Item: ``plaid-id: "<transaction id>"``."""

PADDED = "P"
"""The flag of the entry beancount makes of a `pad`: a balance the ledger filled in, no row."""

MINIMUM = 5
"""The fewest rows a base account must hold one to one with a connected account's for content
alone to propose it: below that, a few same-amount charges a week apart are a coincidence."""

MARGIN = 2
"""How many times as many rows as the runner-up's the best account must hold."""

SIDES = {"depository": "Assets", "credit": "Liabilities", "loan": "Liabilities"}
"""Where an account of each Plaid type sits in a ledger. Content matches only these: Plaid
sends no transactions for a brokerage account, and an account of another type has no side."""


class How(StrEnum):
    ID = "by id"
    CONTENT = "by content"


class Unproposed(StrEnum):
    """Why nothing is proposed for a connected account."""

    NOTHING_SENT = "nothing-sent"
    """The bank has sent no posted transaction for it, as for a brokerage account."""
    IDS_DISAGREE = "ids-disagree"
    """Its ids are in the base, on more than one account, and none holds a strict majority."""
    UNPLACED = "unplaced"
    """Its ids are in the base, but no entry holding one says which side of it is the bank's."""
    NO_SIDE = "no-side"
    """None of its ids is in the base, and an account of its type is not matched by content."""
    UNCLEAR = "unclear"
    """None of its ids is in the base, and no account clearly holds its rows (`clearly`)."""
    WRONG_SIDE = "wrong-side"
    """Its ids are on an account of the base on the other side from its type's."""
    NOT_THEIRS = "not-theirs"
    """The evidence points to an account claimed by members the holder is not among."""
    TAKEN = "taken"
    """The evidence points to an account another connected account is matched to already."""


@dataclass(frozen=True, slots=True)
class Stretch:
    """How many rows, and the first and last day among them: what is said of rows."""

    count: int = 0
    first: date | None = None
    last: date | None = None

    @classmethod
    def of(cls, days: Sequence[date]) -> Stretch:
        return cls(len(days), min(days, default=None), max(days, default=None))


@dataclass(frozen=True, slots=True)
class Connected:
    """One account a working connection reaches, whose connection `holder` holds, with every
    posted row the feed holds for it."""

    account: LinkedAccount
    holder: Member
    institution: str
    rows: tuple[Row, ...]

    @property
    def side(self) -> str | None:
        return SIDES.get(self.account.type)


@dataclass(frozen=True, slots=True)
class _Posting:
    """A balance-sheet posting of a base transaction: its entry, its place in it, its account,
    the day it posted (`comparison.posted_on`) and its units."""

    entry: int
    index: int
    account: str
    day: date
    number: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class Held:
    """The base as matching reads it (`read`)."""

    opened: frozenset[str]
    postings: tuple[_Posting, ...]
    by_entry: Mapping[int, tuple[_Posting, ...]]
    ids: Mapping[str, tuple[tuple[int, int | None], ...]]
    """Each Plaid transaction id a base entry carries, with the entry, and the posting that
    carries it itself, or None when the entry does."""
    last_entry: Mapping[str, date]
    last_balance: Mapping[str, date]
    last_pad: Mapping[str, date]


def read(entries: Sequence[Directive]) -> Held:
    """What matching needs of the base's entries: the accounts it opens, every balance-sheet
    posting of its transactions, the Plaid ids they carry, and each account's last entry,
    balance assertion and pad. An entry beancount makes of a `pad` is no row of the bank's."""
    opened = frozenset(e.account for e in entries if isinstance(e, Open))
    postings: list[_Posting] = []
    ids: dict[str, list[tuple[int, int | None]]] = defaultdict(list)
    last_entry: dict[str, date] = {}
    last_balance: dict[str, date] = {}
    last_pad: dict[str, date] = {}
    for number, entry in enumerate(entries):
        if isinstance(entry, Balance):
            last_balance[entry.account] = max(
                entry.date, last_balance.get(entry.account, entry.date)
            )
        elif isinstance(entry, Pad):
            last_pad[entry.account] = max(entry.date, last_pad.get(entry.account, entry.date))
        if not isinstance(entry, Transaction) or entry.flag == PADDED:
            continue
        for found in _ids(entry.meta):
            ids[found].append((number, None))
        day = posted_on(entry)
        for index, posting in enumerate(entry.postings):
            for found in _ids(posting.meta):
                ids[found].append((number, index))
            account = posting.account
            last_entry[account] = max(entry.date, last_entry.get(account, entry.date))
            units = posting.units
            if not is_balance_sheet(account) or units is None:
                continue
            if isinstance(units.number, Decimal):
                postings.append(_Posting(number, index, account, day, units.number, units.currency))
    by_entry: dict[int, list[_Posting]] = defaultdict(list)
    for kept in postings:
        by_entry[kept.entry].append(kept)
    return Held(
        opened,
        tuple(postings),
        {entry: tuple(found) for entry, found in by_entry.items()},
        {found: tuple(where) for found, where in ids.items()},
        last_entry,
        last_balance,
        last_pad,
    )


def _ids(meta: Mapping[str, object] | None) -> set[str]:
    """The Plaid transaction ids `meta` carries: hearth's own `source`, and a bare `plaid-id`."""
    if not meta:
        return set()
    found = {transaction_of(meta.get("source"))}
    bare = meta.get(PLAID_ID)
    if isinstance(bare, str) and bare.strip():
        found.add(bare.strip())
    return {i for i in found if i}


def placed(held: Held, row: Row) -> tuple[_Posting, ...]:
    """The base postings that are `row`'s bank's own side, in every entry holding its id: the
    posting that carries the id itself, or else the entry's balance-sheet postings of the
    row's amount in the books' sign. Empty when the base holds the id nowhere."""
    by_entry: dict[int, set[int | None]] = defaultdict(set)
    for entry, posting in held.ids.get(row.transaction_id, ()):
        by_entry[entry].add(posting)
    found: list[_Posting] = []
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


@dataclass(frozen=True, slots=True)
class ById:
    """What the base's Plaid ids say of a connected account: each base account its rows' ids
    put their bank's side on, with those rows, and how many rows' ids are in the base without
    saying which side is the bank's, or saying more than one account."""

    on: Mapping[str, Stretch]
    unplaced: int = 0

    @property
    def found(self) -> int:
        return sum(s.count for s in self.on.values()) + self.unplaced

    def ranked(self) -> list[tuple[str, Stretch]]:
        return sorted(self.on.items(), key=lambda item: (-item[1].count, item[0]))

    @property
    def agreeing(self) -> tuple[str, Stretch] | None:
        """The account a strict majority of the placed ids land on, if one does."""
        ranked = self.ranked()
        if not ranked:
            return None
        account, stretch = ranked[0]
        placed_count = sum(s.count for s in self.on.values())
        return (account, stretch) if 2 * stretch.count > placed_count else None

    @property
    def dissent(self) -> int:
        """How many placed ids land on an account other than the most-voted one."""
        ranked = self.ranked()
        return sum(s.count for _, s in ranked[1:])


def by_id(held: Held, rows: Sequence[Row]) -> ById:
    days: dict[str, list[date]] = defaultdict(list)
    unplaced = 0
    for row in _posted(rows):
        if row.transaction_id not in held.ids:
            continue
        accounts = {p.account for p in placed(held, row)}
        if len(accounts) == 1:
            days[accounts.pop()].append(row.day)
        else:
            unplaced += 1
    return ById({account: Stretch.of(found) for account, found in days.items()}, unplaced)


@dataclass(frozen=True, slots=True)
class Candidate:
    """A base account held against a connected account's rows by content: how many of the
    rows it holds one to one (`most_pairs`), and how many of them fall within a week of its
    own first and last rows, the stretch over which it could hold them."""

    account: str
    matched: int
    within: int


def most_pairs(
    ours: Sequence[tuple[date, Decimal]], theirs: Sequence[tuple[date, Decimal]]
) -> list[tuple[int, int]]:
    """The most pairs of `ours` and `theirs`, by index, each used once, of the same amount and
    at most `comparison.APART` apart: a maximum matching.

    ``hearth.comparison`` pairs rows to explain a difference, and so pairs only rows whose
    words agree and drops a row with two candidates equally near; ``hearth.plaid.pairing``
    pairs a bank's payment with a card's. Neither counts how much two accounts hold in common,
    which is what this is for. For one amount, rows on a line paired within a fixed distance,
    taking the earliest two that fit is a maximum matching: were the earliest of either side
    left out of every maximum one, pairing it with the earliest it fits frees nothing a pair
    needs, as everything later is no further from what it was paired with.
    """
    by_amount: dict[Decimal, tuple[list[tuple[date, int]], list[tuple[date, int]]]] = defaultdict(
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


def by_content(held: Held, connected: Connected) -> list[Candidate]:
    """Every account the base opens on the connected account's side, held against its rows,
    the most rows held first; none for an account of a type with no side."""
    side = connected.side
    if side is None:
        return []
    rows = _posted(connected.rows)
    ours = [(row.day, -row.amount) for row in rows]
    on: dict[str, list[_Posting]] = defaultdict(list)
    for posting in held.postings:
        if root(posting.account) == side:
            on[posting.account].append(posting)
    found = []
    for account in sorted(a for a in held.opened if root(a) == side):
        theirs = [(p.day, p.number) for p in on.get(account, [])]
        matched = len(most_pairs(ours, theirs))
        if theirs:
            first, last = min(d for d, _ in theirs) - APART, max(d for d, _ in theirs) + APART
            within = sum(1 for day, _ in ours if first <= day <= last)
        else:
            within = 0
        found.append(Candidate(account, matched, within))
    return sorted(found, key=lambda c: (-c.matched, c.account))


def clearly(ranked: Sequence[Candidate]) -> Candidate | None:
    """The best of `ranked`, most rows held first, if it clearly beats the rest, else None.

    Clearly is three things at once: it holds at least `MINIMUM` of the rows one to one; at
    least `MARGIN` times as many as the runner-up holds, so two accounts of near-identical
    activity propose neither; and at least half of the rows that fall within a week of its
    own first and last rows, so an account that is not the bank's, holding a few rows of the
    same amounts by coincidence, is not proposed for want of a better one.
    """
    if not ranked:
        return None
    best = ranked[0]
    runner_up = ranked[1].matched if len(ranked) > 1 else 0
    if best.matched < MINIMUM or best.matched < MARGIN * runner_up:
        return None
    return best if 2 * best.matched >= best.within else None


@dataclass(frozen=True, slots=True)
class Proposal:
    """What the base says a connected account is.

    `account` is where the evidence points, `how` by which evidence; `unproposed` says why it
    is not proposed, which may be so though the evidence points somewhere (`NOT_THEIRS`,
    `TAKEN`, `WRONG_SIDE`)."""

    connected: Connected
    account: str | None
    how: How | None
    ids: ById
    content: tuple[Candidate, ...]
    unproposed: Unproposed | None

    @property
    def proposed(self) -> str | None:
        return self.account if self.unproposed is None else None


def propose(
    held: Held,
    connected: Connected,
    claimed: Mapping[str, Account],
    taken: Mapping[str, str],
) -> Proposal:
    """What the base says `connected` is (the module's rules). `claimed` is each account the
    household holds, by ledger name; `taken`, each ledger account a connected account is
    matched to, with that connected account's Plaid id."""
    ids = by_id(held, connected.rows)
    if not _posted(connected.rows):
        return Proposal(connected, None, None, ids, (), Unproposed.NOTHING_SENT)
    if ids.found:
        agreeing = ids.agreeing
        if agreeing is None:
            split = Unproposed.IDS_DISAGREE if ids.on else Unproposed.UNPLACED
            return Proposal(connected, None, None, ids, (), split)
        account = agreeing[0]
        side = connected.side
        why = Unproposed.WRONG_SIDE if side is not None and root(account) != side else None
        return _owned(Proposal(connected, account, How.ID, ids, (), why), claimed, taken)
    if connected.side is None:
        return Proposal(connected, None, None, ids, (), Unproposed.NO_SIDE)
    ranked = tuple(by_content(held, connected))
    best = clearly(ranked)
    if best is None:
        return Proposal(connected, None, None, ids, ranked, Unproposed.UNCLEAR)
    return _owned(Proposal(connected, best.account, How.CONTENT, ids, ranked, None), claimed, taken)


def _owned(
    proposal: Proposal, claimed: Mapping[str, Account], taken: Mapping[str, str]
) -> Proposal:
    """`proposal`, unless what it proposes is not its holder's to match to, or is matched to
    another connected account already."""
    if proposal.unproposed is not None or proposal.account is None:
        return proposal
    held_by = claimed.get(proposal.account)
    connected = proposal.connected
    if held_by is not None and not may_match(connected.holder, held_by):
        return _without(proposal, Unproposed.NOT_THEIRS)
    other = taken.get(proposal.account)
    if other is not None and other != connected.account.account_id:
        return _without(proposal, Unproposed.TAKEN)
    return proposal


def _without(proposal: Proposal, why: Unproposed) -> Proposal:
    return Proposal(
        proposal.connected, proposal.account, proposal.how, proposal.ids, proposal.content, why
    )


def may_match(holder: Member, account: Account) -> bool:
    """Whether a connection `holder` holds may be matched to `account`: only one they own, as
    the gate answers whether a member may file a connection into an account."""
    return Gate(holder, {}).may_feed(account.owners)


@dataclass(frozen=True, slots=True)
class Facing:
    """What carrying a connected account on from the base will meet, on the base account it
    is matched to: the evidence a cut-over is decided on, never acted on here.

    Of the feed's posted rows, how many the base holds already, by their Plaid ids and, of the
    rest, by content (`most_pairs`, against the base's rows on the account no id accounts for);
    of the rows it does not hold, those dated on or before the base's last entry on the
    account, which carrying on from that day would never bring in, and those after it. And the
    base's last entry, balance assertion and pad on the account, which are where its own
    record of the account stops."""

    account: str
    rows: Stretch
    by_id: int
    by_content: int
    missing: Stretch
    after: Stretch
    last_entry: date | None
    last_balance: date | None
    last_pad: date | None


def facing(held: Held, account: str, rows: Sequence[Row]) -> Facing:
    posted = _posted(rows)
    used: set[_Posting] = set()
    by_ids = []
    rest = []
    for row in posted:
        mine = [p for p in placed(held, row) if p.account == account]
        if mine:
            by_ids.append(row)
            used.update(mine)
        else:
            rest.append(row)
    pool = [p for p in held.postings if p.account == account and p not in used]
    pairs = most_pairs([(r.day, -r.amount) for r in rest], [(p.day, p.number) for p in pool])
    by_contents = {i for i, _ in pairs}
    unheld = [row for i, row in enumerate(rest) if i not in by_contents]
    last_entry = held.last_entry.get(account)
    return Facing(
        account,
        Stretch.of([r.day for r in posted]),
        len(by_ids),
        len(by_contents),
        Stretch.of([r.day for r in unheld if last_entry is not None and r.day <= last_entry]),
        Stretch.of([r.day for r in unheld if last_entry is None or r.day > last_entry]),
        last_entry,
        held.last_balance.get(account),
        held.last_pad.get(account),
    )


def _posted(rows: Sequence[Row]) -> list[Row]:
    """Rows the bank has posted: a pending row may still change or vanish, and its id with it."""
    return [row for row in rows if not row.pending]


# The household's connected accounts, for ``hearth match-accounts``.


class MatchRefused(Exception):  # noqa: N818 - it names a refusal, as `booking.Refused` does
    """Nothing was matched, for the reason given, which names no amount and no payee."""


@dataclass(frozen=True, slots=True)
class Surveyed:
    """One connected account: what the base says it is, where it is filed now, and what
    carrying it on will meet on the account it is matched to, or else proposed."""

    proposal: Proposal
    matched: str | None
    """The base account it is confirmed to feed, if it is matched."""
    filed: str | None
    """An account it feeds that the base does not open, which hearth keeps itself."""
    confirmed_at: datetime | None
    facing: Facing | None
    unclaimed: str | None = None
    """The base account proposed for it, when nobody has claimed it yet, so it cannot be
    matched until somebody does."""


@dataclass(frozen=True, slots=True)
class Survey:
    base: Base
    accounts: tuple[Surveyed, ...]

    def find(self, account_id: str) -> Surveyed | None:
        return next(
            (s for s in self.accounts if s.proposal.connected.account.account_id == account_id),
            None,
        )


def survey(settings: Settings) -> Survey:
    """Every account a working connection reaches, with what the base says it is. Raises
    `MatchRefused` when there is no base to match to, or it cannot be read."""
    return _survey(settings)[0]


def _survey(settings: Settings) -> tuple[Survey, Held]:
    frozen = _base(settings)
    try:
        held = read(books.load_ledger(frozen).entries)
    except books.LedgerError as error:
        raise MatchRefused(error.summary) from error
    db.initialise(settings.database)
    feed.initialise(settings.feed_database)
    conn = db.connect(settings.database)
    try:
        with Feed.open(settings.feed_database) as fed:
            return Survey(frozen, tuple(_surveyed(conn, fed, held))), held
    finally:
        conn.close()


def _base(settings: Settings) -> Base:
    try:
        found = books.inherited(settings)
    except books.LedgerError as error:
        raise MatchRefused(error.summary) from error
    if not isinstance(found, Base):
        raise MatchRefused(
            "No ledger has been imported, so there are no accounts of its to match to. Import"
            " it first with hearth import-ledger."
        )
    return found


def _surveyed(conn: sqlite3.Connection, fed: Feed, held: Held) -> list[Surveyed]:
    registry = Registry(conn)
    claimed = {a.ledger_account: a for a in all_accounts(conn)}
    by_id = {a.id: a for a in claimed.values()}
    connected = []
    for item in registry.all_items():
        holder = get(conn, item.member)
        if not item.active or holder is None:
            continue
        for linked in registry.accounts_of(item.item_id):
            rows = tuple(fed.transactions(linked.account_id))
            connected.append(Connected(linked, holder, item.institution, rows))
    connected.sort(
        key=lambda c: (c.holder.display_name, c.institution, c.account.name, c.account.account_id)
    )
    taken = {
        by_id[c.account.hearth_account].ledger_account: c.account.account_id
        for c in connected
        if c.account.hearth_account in by_id
    }
    found = []
    for one in connected:
        proposal = propose(held, one, claimed, taken)
        feeds = by_id.get(one.account.hearth_account or "")
        current = feeds.ledger_account if feeds is not None else None
        matched = current if current in held.opened else None
        target = matched or proposal.proposed
        found.append(
            Surveyed(
                proposal,
                matched,
                current if matched is None else None,
                one.account.confirmed_at if matched else None,
                facing(held, target, one.rows) if target else None,
                proposal.proposed if matched is None and proposal.proposed not in claimed else None,
            )
        )
    return found


def confirm(
    settings: Settings, wanted: Sequence[tuple[str, str | None]], *, now: datetime
) -> Survey:
    """Record each match in `wanted`, a connected account's Plaid id with the base account it
    is, or None for the one proposed, as the mapping a member confirms on the Connect page;
    then the survey as it stands. Every one is checked before any is written, and one hearth
    refuses raises `MatchRefused` having written nothing.

    A match changes nothing about who owns the base account: it must be claimed already, and
    owned by the connection's holder among others. A match may be changed until carrying
    begins, which is not built yet, so always for now; one confirmed again as it stands keeps
    the day it was first confirmed."""
    found, held = _survey(settings)
    conn = db.connect(settings.database)
    try:
        claimed = {a.ledger_account: a for a in all_accounts(conn)}
        taking: dict[str, Account] = {}
        for account_id, chosen in wanted:
            if account_id in taking:
                raise MatchRefused(f"{account_id} is named twice.")
            taking[account_id] = _checked(found, held, claimed, account_id, chosen)
        targets = Counter(account.ledger_account for account in taking.values())
        twice = sorted(a for a, n in targets.items() if n > 1)
        if twice:
            raise MatchRefused(
                f"{twice[0]} is named for two connected accounts; one ledger account is fed by"
                " one connection at most."
            )
        registry = Registry(conn)
        for account_id, account in taking.items():
            one = found.find(account_id)
            if one is not None and one.matched != account.ledger_account:
                registry.confirm(account_id, account.id, by=None, now=now)
    finally:
        conn.close()
    return survey(settings)


def _checked(
    found: Survey,
    held: Held,
    claimed: Mapping[str, Account],
    account_id: str,
    chosen: str | None,
) -> Account:
    """The account `account_id` may be matched to, `chosen` or its proposal, or raise
    `MatchRefused` saying why not."""
    one = found.find(account_id)
    if one is None:
        raise MatchRefused(f"No working connection reaches an account {account_id}.")
    connected = one.proposal.connected
    if one.filed is not None:
        raise MatchRefused(
            f"{account_id} feeds {one.filed}, an account hearth keeps itself, which the base does"
            " not open; matching it to the base would take what it brought in out of the books."
        )
    target = chosen or one.proposal.proposed
    if target is None:
        raise MatchRefused(
            f"Nothing is proposed for {account_id}; name the base account it is:"
            f" --confirm {account_id}=<ledger account>."
        )
    if target not in held.opened:
        raise MatchRefused(f"The imported ledger opens no account {target}.")
    side = connected.side
    if not is_balance_sheet(target) or (side is not None and root(target) != side):
        where = side or "Assets or Liabilities"
        raise MatchRefused(f"{target} is not under {where}, where {account_id} goes.")
    account = claimed.get(target)
    holder = connected.holder
    if account is None:
        raise MatchRefused(
            f"Nobody has claimed {target} yet, and matching never says who owns an account."
            f" Claim it first: hearth add-account {target} --name <name> --owner"
            f" {holder.username}."
        )
    if account.named is not None:
        raise MatchRefused(
            f"{target} is an account a member named, which no connection feeds"
            ' (docs/books.md, "Accounts hearth does not see").'
        )
    if not may_match(holder, account):
        raise MatchRefused(
            f"{target} is not {holder.username}'s, so their connection is not matched to it;"
            " matching never changes who owns an account."
        )
    other = next(
        (
            s
            for s in found.accounts
            if s.matched == target and s.proposal.connected.account.account_id != account_id
        ),
        None,
    )
    if other is not None:
        raise MatchRefused(
            f"{target} is matched to another connected account already; one ledger account is"
            " fed by one connection at most."
        )
    return account


# What ``hearth match-accounts`` prints.


def describe(found: Survey) -> list[str]:
    """The survey in words for a terminal: counts, days and the names of accounts, never a
    payee or an amount."""
    lines = [
        "Each connected account, and which of the imported ledger's accounts it is"
        f" (the base {found.base.digest[:12]}):"
    ]
    if not found.accounts:
        return [*lines, "No working connection reaches an account yet."]
    unconfirmed = []
    for one in found.accounts:
        connected = one.proposal.connected
        linked = connected.account
        ending = f" ending {linked.mask}" if linked.mask else ""
        kind = linked.subtype or linked.type
        lines += [
            "",
            f"{connected.holder.display_name}, {connected.institution}: {linked.name}{ending}"
            f" ({kind}), {linked.account_id}",
        ]
        if one.matched is not None:
            when = one.confirmed_at.astimezone().date() if one.confirmed_at else None
            lines.append(
                f"  Matched to {one.matched}" + (f", confirmed {_day(when)}" if when else "")
            )
        elif one.filed is not None:
            lines.append(
                f"  Feeds {one.filed}, an account the base does not open, which hearth keeps"
                " itself: not matched to the base"
            )
        else:
            lines.append("  Not matched")
        lines += [f"  {line}" for line in _proposal(one.proposal)]
        if one.facing is not None:
            if one.matched is not None:
                lines.append("  Carrying it on will meet:")
            else:
                lines.append(f"  Carrying it on {one.facing.account} would meet:")
            lines += [f"    {line}" for line in _facing(one.facing)]
        if one.matched is not None or one.filed is not None:
            continue
        account_id = linked.account_id
        if one.unclaimed is not None:
            lines.append(
                f"  Nobody has claimed {one.unclaimed} yet: claim it with hearth add-account"
                f" {one.unclaimed} --name <name> --owner {connected.holder.username}, then"
                f" confirm it with --confirm {account_id}"
            )
        elif one.proposal.proposed is not None:
            unconfirmed.append(account_id)
            lines.append(f"  Confirm it with --confirm {account_id}")
        else:
            lines.append(
                f"  Name its account with --confirm {account_id}=<ledger account>, or file it"
                " as a new account on the Connect page"
            )
    if unconfirmed:
        flags = " ".join(f"--confirm {account_id}" for account_id in unconfirmed)
        lines += ["", f"To confirm every proposal: hearth match-accounts {flags}"]
    return lines


def _proposal(proposal: Proposal) -> list[str]:
    connected = proposal.connected
    sent = len(_posted(connected.rows))
    ids = proposal.ids
    said: list[str] = []
    if ids.found:
        found = f"{ids.found} of the {sent} transactions the bank sent are in the base by their"
        ranked = ids.ranked()
        if len(ranked) == 1 and not ids.unplaced:
            account, stretch = ranked[0]
            said.append(f"{found} Plaid ids, all on {account}, {_stretch(stretch)}")
        else:
            said.append(f"{found} Plaid ids:")
            said += [f"  {s.count} on {account}, {_stretch(s)}" for account, s in ranked]
            if ids.unplaced:
                said.append(f"  {ids.unplaced} in entries that do not say which side is the bank's")
    elif sent and connected.side is not None:
        said.append(f"None of the {sent} transactions the bank sent is in the base by its Plaid id")
        holding = [c for c in proposal.content if c.matched][:2]
        said += [_candidate(c) for c in holding]
        if not holding:
            said.append(f"No account of the base under {connected.side} holds any of them")
    if proposal.proposed is not None:
        head = f"Proposed: {proposal.proposed}, {proposal.how}"
        if proposal.how is How.ID and ids.dissent:
            head += f", though {ids.dissent} of its ids are on other accounts"
        return [head, *(f"  {line}" for line in said)]
    return [f"Nothing proposed: {_why(proposal)}", *(f"  {line}" for line in said)]


def _why(proposal: Proposal) -> str:
    connected = proposal.connected
    match proposal.unproposed:
        case Unproposed.NOTHING_SENT:
            return "the bank has sent no transactions for it, so there is nothing to match"
        case Unproposed.IDS_DISAGREE:
            return "its Plaid ids are on more than one of the base's accounts, and none holds most"
        case Unproposed.UNPLACED:
            return (
                "its Plaid ids are in the base, in entries that do not say which side is the bank's"
            )
        case Unproposed.NO_SIDE:
            return (
                f"none of its Plaid ids is in the base, and an account of its type"
                f" ({connected.account.type}) is not matched by its transactions"
            )
        case Unproposed.UNCLEAR:
            return (
                "none of its Plaid ids is in the base, and no account of the base clearly holds"
                f" its transactions: at least {MINIMUM} of them, {MARGIN} times as many as the"
                " next best, and half of those within a week of its own first and last"
            )
        case Unproposed.WRONG_SIDE:
            return (
                f"its Plaid ids are on {proposal.account}, which is not where a"
                f" {connected.account.type} account goes"
            )
        case Unproposed.NOT_THEIRS:
            return (
                f"the evidence points to {proposal.account}, which {connected.holder.display_name}"
                " does not own; matching never changes who owns an account"
            )
        case Unproposed.TAKEN:
            return (
                f"the evidence points to {proposal.account}, which another connected account is"
                " matched to already"
            )
    return "nothing in the base says which account it is"


def _candidate(candidate: Candidate) -> str:
    return (
        f"{candidate.account} holds {candidate.matched} of them, by amount within a week, of the"
        f" {candidate.within} within a week of its own first and last"
    )


def _facing(found: Facing) -> list[str]:
    last = _day(found.last_entry) if found.last_entry else None
    lines = [
        f"From the bank: {found.rows.count} transactions" + _span(found.rows),
        f"In the base already: {found.by_id} by Plaid id, {found.by_content} by content",
    ]
    if last is not None:
        lines += [
            f"Not in the base, on or before its last entry there on {last}:"
            f" {found.missing.count}{_span(found.missing)}",
            f"After its last entry: {found.after.count}{_span(found.after)}",
        ]
    else:
        lines.append(f"The base has no entry on it; all {found.after.count} come after it")
    balance = _day(found.last_balance) if found.last_balance else "none"
    pad = _day(found.last_pad) if found.last_pad else "none"
    lines.append(f"The base's last balance assertion on it: {balance}; its last pad: {pad}")
    return lines


def _span(stretch: Stretch) -> str:
    return f", {_stretch(stretch)}" if stretch.count else ""


def _stretch(stretch: Stretch) -> str:
    if stretch.first is None or stretch.last is None:
        return "no days"
    if stretch.first == stretch.last:
        return _day(stretch.first)
    return f"{_day(stretch.first)} to {_day(stretch.last)}"


def _day(day: date) -> str:
    return f"{day:%b} {day.day}, {day.year}"
