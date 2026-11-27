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
(`Registry.confirm`). From then on hearth carries the account on from the base
(``hearth.carrying``): its history before its cut-over is the base's, and hearth books the
bank's rows from then on that the base does not hold. `facing` says, for each account, what
carrying it on meets.

A match may be changed until carrying begins (``books.begun``): until then every row carried
on is derived afresh each recompute, so a wrong match put right moves its rows with it and
leaves nothing behind. Once a member has ruled on a row carried on the account, or on its
reconciling entry, the ruling rests on the match, so it is fixed, and so is the base
(``hearth.ledger_import``).

A connected account filed as an account hearth keeps itself, whose rows the base also holds,
would be counted twice once the base's account is matched as well. It is moved onto the base's
account (`Moving`) only when carrying would hold every row hearth has booked on it, and the
rulings made on those rows keep deciding them (``RulingStore.formerly``); otherwise it is
refused, saying why.

This reads the whole household's base and feed for whoever runs hearth on its machine, as
``hearth plaid items`` does; it is not a page. What it says is counts, days and the names of
accounts, never a payee or an amount.
"""

import sqlite3
import threading
from collections import Counter, defaultdict
from collections.abc import Collection, Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from beancount.core.data import Transaction

from hearth import books, carrying, db
from hearth.accounts import Account, all_accounts
from hearth.base import Base
from hearth.carrying import APART, Held, Status
from hearth.carrying import PADDED as PADDED
from hearth.carrying import PLAID_ID as PLAID_ID
from hearth.carrying import most_pairs as most_pairs
from hearth.carrying import placed as placed
from hearth.carrying import read as read
from hearth.config import Settings
from hearth.db import transaction
from hearth.disclosure import review_decisions
from hearth.disclosure.gate import Gate
from hearth.members import Member, MemberId, get
from hearth.plaid import booking, feed
from hearth.plaid.client import Transaction as Row
from hearth.plaid.feed import Feed
from hearth.plaid.registry import LinkedAccount, Registry
from hearth.reports.common import is_balance_sheet, root
from hearth.review.decision import State
from hearth.review.pile import PileItem, read_pile
from hearth.review.rulings import RulingStore

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
class ById:
    """What the base's Plaid ids say of a connected account: each base account its rows' ids
    put their bank's side on, with those rows, and how many rows' ids are in the base without
    saying which side is the bank's, or saying more than one account."""

    on: Mapping[str, Stretch]
    unplaced: int = 0
    entries: Mapping[str, frozenset[int]] = field(default_factory=dict)
    """Each base account's entries the ids are carried on (``carrying.Held.by_entry``)."""

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
    entries: dict[str, set[int]] = defaultdict(set)
    unplaced = 0
    for row in _posted(rows):
        if row.transaction_id not in held.ids:
            continue
        postings = placed(held, row)
        accounts = {p.account for p in postings}
        if len(accounts) == 1:
            account = accounts.pop()
            days[account].append(row.day)
            entries[account] |= {p.entry for p in postings}
        else:
            unplaced += 1
    return ById(
        {account: Stretch.of(found) for account, found in days.items()},
        unplaced,
        {account: frozenset(found) for account, found in entries.items()},
    )


@dataclass(frozen=True, slots=True)
class Candidate:
    """A base account held against a connected account's rows by content: how many of the
    rows it holds one to one (`most_pairs`), and how many of them fall within a week of its
    own first and last rows, the stretch over which it could hold them."""

    account: str
    matched: int
    within: int


def by_content(held: Held, connected: Connected) -> list[Candidate]:
    """Every account the base opens on the connected account's side, held against its rows,
    the most rows held first; none for an account of a type with no side."""
    side = connected.side
    if side is None:
        return []
    rows = _posted(connected.rows)
    ours = [(row.day, -row.amount, row.currency) for row in rows]
    on: dict[str, list[carrying.BasePosting]] = defaultdict(list)
    for posting in held.postings:
        if root(posting.account) == side:
            on[posting.account].append(posting)
    found = []
    for account in sorted(a for a in held.opened if root(a) == side):
        theirs = [(p.day, p.number, p.currency) for p in on.get(account, [])]
        matched = len(carrying.by_money(ours, theirs))
        if theirs:
            first = min(d for d, _, _ in theirs) - APART
            last = max(d for d, _, _ in theirs) + APART
            within = sum(1 for day, _, _ in ours if first <= day <= last)
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
    """What carrying a connected account on from the base meets, on the base account it is
    matched to, or would be (``hearth.carrying``).

    Of the feed's posted rows, how many the base holds already, by their Plaid ids and, of the
    rest, by content; of the rows it does not hold, those dated before the cut-over, which
    carrying never brings in, and those from it on, which hearth books. And the base's last
    entry, balance assertion and pad on the account, which the cut-over is worked out from,
    and whether an account of the connected account's kind is carried at all
    (``carrying.carries``): a brokerage is not, yet, so its history ends where the base's does.
    """

    account: str
    rows: Stretch
    by_id: int
    by_content: int
    missing: Stretch
    """Rows dated before the cut-over that the base does not hold: never booked."""
    after: Stretch
    """Rows from the cut-over on that the base does not hold: what hearth books."""
    last_entry: date | None
    last_balance: date | None
    last_pad: date | None
    cut_over: date | None = None
    carried: bool = True
    postponed: bool = False
    """Whether the cut-over is after today, so the account is not carried on yet
    (``carrying.postponed``)."""


def facing(
    held: Held,
    account: str,
    rows: Sequence[Row],
    kind: str | None = None,
    today: date = date.max,
) -> Facing:
    """What carrying `account` on from the base meets in `rows`, the bank's rows for a
    connected account of Plaid type `kind`, by the one rule the books carry it on by
    (``carrying.carry``)."""
    posted = _posted(rows)
    plan = carrying.carry(held, account, posted)
    return Facing(
        account,
        Stretch.of([r.day for r in posted]),
        len(plan.by_id),
        len(plan.by_content),
        Stretch.of([r.day for r in posted if plan.status(r) is Status.BEFORE]),
        Stretch.of([r.day for r in posted if plan.status(r) is Status.NEW]),
        held.last_entry.get(account),
        held.last_balance.get(account),
        held.last_pad.get(account),
        plan.cut_over,
        kind is None or carrying.carries(kind),
        plan.cut_over > today,
    )


def _posted(rows: Sequence[Row]) -> list[Row]:
    """Rows the bank has posted: a pending row may still change or vanish, and its id with it."""
    return [row for row in rows if not row.pending]


# The household's connected accounts, for ``hearth match-accounts``.


class Refusal(StrEnum):
    """Why a match is refused, for a screen that says it in its own words; the sentence a
    `MatchRefused` carries says it for a terminal."""

    NO_BASE = "no_base"
    """No ledger has been imported, so there is nothing of its to match to."""
    UNREADABLE = "unreadable"
    """The imported ledger cannot be read."""
    TWICE = "twice"
    """One connected account, or one ledger account, is named twice in one confirmation."""
    NOT_CONNECTED = "not_connected"
    """No working connection reaches the connected account named."""
    NOTHING_PROPOSED = "nothing_proposed"
    """Nothing is proposed for it, and no account was named."""
    NOT_OPENED = "not_opened"
    """The imported ledger opens no such account."""
    WRONG_SIDE = "wrong_side"
    """The account is not on the side of the ledger a connected account of its kind goes."""
    CLOSED = "closed"
    """The imported ledger closes the account, so nothing can be carried on into it."""
    MOVED_INTO = "moved_into"
    """Rows are moved into the account in Review."""
    UNSURE = "unsure"
    """Hearth cannot tell whether carrying the match it has on has begun."""
    FIXED = "fixed"
    """A decision has been made in Review on its transactions carried on, so its match is
    fixed (``books.begun``)."""
    UNCLAIMED = "unclaimed"
    """Nobody has claimed the account yet, and matching never says who owns one."""
    NAMED = "named"
    """The account is one a member named, which no connection feeds."""
    NOT_YOURS = "not_yours"
    """The account is not the connection's holder's."""
    TAKEN = "taken"
    """Another connected account is matched to the account already."""
    NOT_CARRIED = "not_carried"
    """It is of a kind hearth does not carry on from the imported ledger yet."""
    RULED_ONTO = "ruled_onto"
    """Rows were moved into the account hearth keeps it as, in Review, and those rulings name
    it."""
    OTHER_OWNERS = "other_owners"
    """The account is claimed with other owners than the one hearth keeps it as."""
    WOULD_LOSE = "would_lose"
    """Moving it would take `count` of the rows hearth booked on it, dated before `day`, out
    of the books."""
    UNRECORDED = "unrecorded"
    """Nobody has claimed the account, and the ledger does not record that it is this
    connection's firmly enough to hand its history over (`recorded`)."""
    BOOKING = "booking"
    """The bank's rows cannot be taken once, so hearth cannot tell what moving it would meet."""


class MatchRefused(Exception):  # noqa: N818 - it names a refusal, as `booking.Refused` does
    """Nothing was matched, for the reason given, which names no amount and no payee: in a
    sentence for a terminal, and as `why`, with the `count` and `day` it needs, for a screen."""

    def __init__(
        self, said: str, why: Refusal, *, count: int | None = None, day: date | None = None
    ) -> None:
        super().__init__(said)
        self.why = why
        self.count = count
        self.day = day


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
    begun: bool = False
    """Whether carrying its match on has begun (``books.begun``), which fixes the match."""


@dataclass(frozen=True, slots=True)
class Survey:
    base: Base
    accounts: tuple[Surveyed, ...]
    moved_into: Mapping[str, tuple[PileItem, ...]] = field(default_factory=dict)
    """Each account a ruling now decides a row onto, with those rows, which no connection may
    be matched to while they are (`_target`). Empty rows for an account when the books could
    not be derived, so any account a ruling ever named counts."""
    unsure: str | None = None
    """Why hearth cannot tell whether carrying has begun on any account, when it cannot: the
    books could not be derived. No match may be changed meanwhile."""

    def find(self, account_id: str) -> Surveyed | None:
        return next(
            (s for s in self.accounts if s.proposal.connected.account.account_id == account_id),
            None,
        )


def survey(settings: Settings, today: date | None = None) -> Survey:
    """Every account a working connection reaches, with what the base says it is. Raises
    `MatchRefused` when there is no base to match to, or it cannot be read."""
    return _survey(settings, today)[0]


def _survey(settings: Settings, today: date | None = None) -> tuple[Survey, Held]:
    today = today or datetime.now().astimezone().date()
    frozen = _base(settings)
    try:
        held = read(books.load_ledger(frozen).entries)
    except books.LedgerError as error:
        raise MatchRefused(error.summary, Refusal.UNREADABLE) from error
    db.initialise(settings.database)
    feed.initialise(settings.feed_database)
    unsure = None
    try:
        begun = books.begun_in(settings, today)
    except books.LedgerError as error:
        begun, unsure = frozenset(), error.summary
    conn = db.connect(settings.database)
    try:
        with Feed.open(settings.feed_database) as fed:
            found = tuple(_surveyed(conn, fed, held, begun, today))
    finally:
        conn.close()
    return Survey(frozen, found, _moved_onto(settings, today), unsure), held


def _moved_onto(settings: Settings, today: date) -> dict[str, tuple[PileItem, ...]]:
    """Each balance-sheet account a ruling now decides a row onto, other than the row's own,
    with those rows: the latest decision on each row of the books as they stand, as the
    recompute lays it (``review.decision``). A ruling since superseded decides nothing, so it
    holds nothing back; were it ever to decide again, the books leave a row it would move onto
    a carried account unreviewed (``review.decision.guarded``)."""
    try:
        household = books.household_entries(settings, today)
        pile = read_pile(
            e
            for e in household.entries
            if isinstance(e, Transaction) and e.flag == "!" and "review-id" in (e.meta or {})
        )
    except books.LedgerError, ValueError:
        conn = db.connect(settings.database)
        try:
            return {a: () for a in RulingStore(conn).ruled_onto() if is_balance_sheet(a)}
        finally:
            conn.close()
    conn = db.connect(settings.database)
    try:
        decided = review_decisions(pile, all_accounts(conn), RulingStore(conn))
    finally:
        conn.close()
    found: dict[str, list[PileItem]] = defaultdict(list)
    for item in pile:
        decision = decided[item.review_id]
        account = decision.account
        if (
            decision.state is State.REVIEWED
            and account is not None
            and account != item.account
            and is_balance_sheet(account)
        ):
            found[account].append(item)
    return {account: tuple(items) for account, items in found.items()}


def _base(settings: Settings) -> Base:
    try:
        found = books.inherited(settings)
    except books.LedgerError as error:
        raise MatchRefused(error.summary, Refusal.UNREADABLE) from error
    if not isinstance(found, Base):
        raise MatchRefused(
            "No ledger has been imported, so there are no accounts of its to match to. Import"
            " it first with hearth import-ledger.",
            Refusal.NO_BASE,
        )
    return found


def _surveyed(
    conn: sqlite3.Connection, fed: Feed, held: Held, begun: Collection[str], today: date
) -> list[Surveyed]:
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
                facing(held, target, one.rows, one.account.type, today) if target else None,
                proposal.proposed if matched is None and proposal.proposed not in claimed else None,
                matched in begun,
            )
        )
    return found


@dataclass(frozen=True, slots=True)
class Moving:
    """A connected account filed as an account hearth keeps itself, `kept`, to be moved onto
    the base's account `target`, which hearth then carries on: every connected account feeding
    `kept` with it. `into` is the household's claim of `target`, when it has one, which has
    exactly `kept`'s owners; without one, `kept` itself becomes the claim of `target`."""

    kept: Account
    target: str
    into: Account | None
    feeding: tuple[LinkedAccount, ...]


@dataclass(frozen=True, slots=True)
class Weighing:
    """The survey with what it was worked out from, to weigh any match against without
    writing anything (`refusal`), by the very checks `confirm` makes before it writes."""

    survey: Survey
    held: Held
    today: date

    def facing(self, one: Surveyed, account: str) -> Facing:
        """What carrying `one` on from `account` would meet."""
        connected = one.proposal.connected
        return facing(self.held, account, connected.rows, connected.account.type, self.today)


_weighed: dict[Path, tuple[Hashable, Weighing]] = {}
_weighing = threading.Lock()


def weigh(settings: Settings, today: date | None = None) -> Weighing:
    """The household's connected accounts, as `survey` finds them, ready to be weighed. Raises
    `MatchRefused` as `survey` does.

    A survey reads the whole base and works the household's books out twice over, so one is
    kept for each data directory and handed out again while everything a recompute reads
    stands where it stood (``books.standing``), on the same day: anything that could change
    what it says changes that, so a survey handed out again is never stale."""
    today = today or datetime.now().astimezone().date()
    key = (books.standing(settings), today, settings.ledger)
    with _weighing:
        kept = _weighed.get(settings.data_dir)
        if kept is not None and kept[0] == key:
            return kept[1]
    found, held = _survey(settings, today)
    weighed = Weighing(found, held, today)
    with _weighing:
        _weighed[settings.data_dir] = (key, weighed)
    return weighed


def refusal(
    settings: Settings,
    weighing: Weighing,
    account_id: str,
    chosen: str | None,
    *,
    claim: bool = False,
) -> MatchRefused | None:
    """Why matching `account_id` to `chosen`, or to what is proposed for it, would be refused
    as things stand, or None when `confirm` would record it."""
    conn = db.connect(settings.database)
    try:
        _planned(conn, settings, weighing, [(account_id, chosen)], claim=claim)
    except MatchRefused as refused:
        return refused
    finally:
        conn.close()
    return None


RECENT = timedelta(days=31)
"""How near the end of the base's history on an account its Plaid ids must reach for the
ledger to vouch that it is still that connection's account."""

WINDOW = timedelta(days=730)
"""How far back a bank sends an account's history when it is connected, at most: Plaid's two
years, over which the bank's own rows must be most of what the base holds on it."""


def recorded(held: Held, proposal: Proposal) -> bool:
    """Whether the ledger itself records that `proposal`'s connected account is the base
    account proposed, firmly enough to hand that account's whole history to the connection's
    owners when nobody has claimed it (``access.may_carry_on``).

    Proposed by its Plaid ids, which only that bank account's rows carry; at least `MINIMUM` of
    them agreeing on the account, as many as content alone needs to propose anything; and
    reaching the base's latest month on it (`RECENT`), so an account that was this bank's
    once, years ago, and is something else since, does not pass on everything after; and on a
    strict majority of the base's entries on the account over the stretch a bank sends of an
    account's history (`WINDOW`), up to its last entry there, or since its first if that is
    later: so a few rows of this bank's at the end of an account that holds years of something
    else's do not hand all of it over, while an account older than anything the bank still
    sends, every recent entry of which is the bank's, does."""
    account = proposal.proposed
    if proposal.how is not How.ID or account is None:
        return False
    on = proposal.ids.on.get(account)
    last = held.last_entry.get(account)
    if on is None or on.count < MINIMUM or on.last is None or last is None:
        return False
    if on.last < last - RECENT:
        return False
    days = [posting.day for posting in held.postings if posting.account == account]
    start = max(min(days, default=last), last - WINDOW)
    spanned = {
        posting.entry
        for posting in held.postings
        if posting.account == account and start <= posting.day <= last
    }
    ours = proposal.ids.entries.get(account, frozenset()) & spanned
    return 2 * len(ours) > len(spanned)


def confirm(
    settings: Settings,
    wanted: Sequence[tuple[str, str | None]],
    *,
    now: datetime,
    by: MemberId | None = None,
    claim: bool = False,
    weighing: Weighing | None = None,
) -> None:
    """Record each match in `wanted`, a connected account's Plaid id with the base account it
    is, or None for the one proposed, as the mapping a member confirms on the Connect page, and
    return nothing: whoever wants the survey as it stands after asks for it (`survey`). Every
    one is checked against the survey it was weighed from before any is written, and one
    hearth refuses raises `MatchRefused` having written nothing.

    `by` is the member who confirms it, as the Connect page records one, or None for whoever
    runs hearth on its machine (``hearth match-accounts``). `weighing` is the survey the
    matches were chosen from, when the caller has one (`weigh`), which is weighed again only
    if it is no longer as things stand.

    A match changes nothing about who owns the base account: it must be claimed already, and
    owned by the connection's holder among others. A match may be changed until carrying it
    on begins (``books.begun``); one confirmed again as it stands keeps the day it was first
    confirmed. A connected account filed as an account hearth keeps itself is moved onto the
    base's account only as `_moving` allows: onto one nobody has claimed only where the ledger
    records it is that account (`recorded`), unless `claim`, the operator's word on the
    command line that it is."""
    today = now.astimezone().date()
    weighing = weigh(settings, today) if weighing is None else weighing
    conn = db.connect(settings.database)
    try:
        taking, moving = _planned(conn, settings, weighing, wanted, claim=claim)
        registry = Registry(conn)
        for account_id, account in taking.items():
            one = weighing.survey.find(account_id)
            if one is not None and one.matched != account.ledger_account:
                registry.confirm(account_id, account.id, by=by, now=now)
        for move in moving.values():
            _move(conn, move, now=now, by=by)
    finally:
        conn.close()


def _planned(
    conn: sqlite3.Connection,
    settings: Settings,
    weighing: Weighing,
    wanted: Sequence[tuple[str, str | None]],
    *,
    claim: bool = False,
) -> tuple[dict[str, Account], dict[str, Moving]]:
    """What `confirm` would write for `wanted`: each match to record, and each account hearth
    keeps itself to move onto the base's, every one checked; or raise `MatchRefused`."""
    found, held = weighing.survey, weighing.held
    claimed = {a.ledger_account: a for a in all_accounts(conn)}
    taking: dict[str, Account] = {}
    moving: dict[str, Moving] = {}
    for account_id, chosen in wanted:
        if account_id in taking or account_id in moving:
            raise MatchRefused(f"{account_id} is named twice.", Refusal.TWICE)
        one = found.find(account_id)
        if one is not None and one.filed is not None:
            with Feed.open(settings.feed_database) as fed:
                moving[account_id] = _moving(
                    conn, fed, found, held, claimed, one, chosen, claim=claim
                )
        else:
            taking[account_id] = _checked(found, held, claimed, account_id, chosen)
    targets = Counter(
        [
            *(account.ledger_account for account in taking.values()),
            *(move.target for move in moving.values()),
        ]
    )
    twice = sorted(a for a, n in targets.items() if n > 1)
    if twice:
        raise MatchRefused(
            f"{twice[0]} is named for two connected accounts; one ledger account is fed by"
            " one connection at most.",
            Refusal.TWICE,
        )
    return taking, moving


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
        raise MatchRefused(
            f"No working connection reaches an account {account_id}.", Refusal.NOT_CONNECTED
        )
    connected = one.proposal.connected
    target = _target(found, one, held, account_id, chosen)
    if one.matched is not None and one.matched != target:
        if found.unsure is not None:
            raise MatchRefused(
                f"Hearth cannot tell whether carrying {one.matched} on has begun, so its match"
                f" is not changed: {found.unsure}",
                Refusal.UNSURE,
            )
        if one.begun:
            raise MatchRefused(
                f"{account_id} is carried on from the imported ledger's {one.matched}, and a"
                " decision has been made in Review on its transactions there. Its match is"
                " fixed from then on: changing it would leave that decision deciding nothing.",
                Refusal.FIXED,
            )
    account = claimed.get(target)
    holder = connected.holder
    if account is None:
        raise MatchRefused(
            f"Nobody has claimed {target} yet, and matching never says who owns an account."
            f" Claim it first: hearth add-account {target} --name <name> --owner"
            f" {holder.username}.",
            Refusal.UNCLAIMED,
        )
    if account.named is not None:
        raise MatchRefused(
            f"{target} is an account a member named, which no connection feeds"
            ' (docs/books.md, "Accounts hearth does not see").',
            Refusal.NAMED,
        )
    if not may_match(holder, account):
        raise MatchRefused(
            f"{target} is not {holder.username}'s, so their connection is not matched to it;"
            " matching never changes who owns an account.",
            Refusal.NOT_YOURS,
        )
    _not_taken(found, target, account_id)
    return account


def _target(found: Survey, one: Surveyed, held: Held, account_id: str, chosen: str | None) -> str:
    """The base account `chosen`, or proposed, for `one`, if a connected account of its kind
    may go there at all."""
    connected = one.proposal.connected
    target = chosen or one.proposal.proposed
    if target is None:
        raise MatchRefused(
            f"Nothing is proposed for {account_id}; name the base account it is:"
            f" --confirm {account_id}=<ledger account>.",
            Refusal.NOTHING_PROPOSED,
        )
    if target not in held.opened:
        raise MatchRefused(f"The imported ledger opens no account {target}.", Refusal.NOT_OPENED)
    side = connected.side
    if not is_balance_sheet(target) or (side is not None and root(target) != side):
        where = side or "Assets or Liabilities"
        raise MatchRefused(
            f"{target} is not under {where}, where {account_id} goes.", Refusal.WRONG_SIDE
        )
    closed = held.closed_on.get(target)
    if closed is not None:
        raise MatchRefused(
            f"The imported ledger closes {target} on {_day(closed)}, so nothing can be carried"
            " on into it.",
            Refusal.CLOSED,
        )
    moved = found.moved_into.get(target)
    if moved is not None and target != one.matched:
        # Carried on, its bank's balance would be asserted beside rows its bank never sent:
        # what carrying holds is the bank's alone.
        rows = "; ".join(
            f"{item.review_id}, on {item.account}, {_day(item.date)}"
            for item in sorted(moved, key=lambda i: (i.date, i.review_id))
        )
        raise MatchRefused(
            f"Rows are moved into {target} in Review, so no connection is matched to it:"
            " carried on, it would hold them beside its bank's own transactions"
            + (f". They are {rows}" if rows else "")
            + ". Decide them again in Review, onto another account or a category, and"
            " match it then.",
            Refusal.MOVED_INTO,
        )
    return target


def _not_taken(found: Survey, target: str, account_id: str) -> None:
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
            " fed by one connection at most.",
            Refusal.TAKEN,
        )


def _moving(
    conn: sqlite3.Connection,
    fed: Feed,
    found: Survey,
    held: Held,
    claimed: Mapping[str, Account],
    one: Surveyed,
    chosen: str | None,
    *,
    claim: bool = False,
) -> Moving:
    """How `one`, filed as an account hearth keeps itself whose rows the base also holds, is
    moved onto the base's account, or raise `MatchRefused` saying why it is not.

    Once the base's account is matched too, the one bank account would be counted twice, in
    the base and in hearth's own account. Moving it is carrying it on from the base instead,
    which is allowed only when carrying would hold every row hearth has booked on its own
    account: each is in the base, or dated from the cut-over on, so hearth books it again on
    the base's account. A row before the cut-over that the base does not hold would leave the
    books, so then nothing is moved. Rulings on the rows booked again keep deciding them, as
    a ruling is bound to the row as it was booked (``RulingStore.formerly``); a ruling on a row
    the base holds decides nothing more, since the base's own entry for it stands.

    Who sees the account does not change: the base's account must be unclaimed, so the claim
    of hearth's own account becomes its claim, or claimed by exactly the same members.
    """
    account_id = one.proposal.connected.account.account_id
    assert one.filed is not None  # noqa: S101 - only a filed account is moved
    kept = claimed[one.filed]
    target = _target(found, one, held, account_id, chosen)
    registry = Registry(conn)
    feeding = tuple(a for a in registry.confirmed() if a.hearth_account == kept.id)
    if not all(carrying.carries(a.type) for a in feeding):
        raise MatchRefused(
            f"{account_id} is not of a kind hearth carries on from the imported ledger yet:"
            " a brokerage's history stays where the ledger left it.",
            Refusal.NOT_CARRIED,
        )
    # Only a row moved onto it is named by a ruling as its own; an accept names a category.
    if kept.ledger_account in RulingStore(conn).ruled_onto():
        raise MatchRefused(
            f"Rows were moved into {kept.ledger_account} in Review, so it is not moved onto"
            f" {target}: those rulings name it.",
            Refusal.RULED_ONTO,
        )
    into = claimed.get(target)
    if (
        into is None
        and not claim
        and not (one.proposal.proposed == target and recorded(held, one.proposal))
    ):
        raise MatchRefused(
            f"Nobody has claimed {target}, and the imported ledger does not record that it is"
            f" {account_id}: at least {MINIMUM} of its Plaid ids agreeing on it, reaching the"
            " base's last month there. Moving onto it would give its whole history to"
            f" {kept.ledger_account}'s owners. Claim it with hearth add-account, or move it"
            " anyway with --claim.",
            Refusal.UNRECORDED,
        )
    if into is not None and into.owners != kept.owners:
        raise MatchRefused(
            f"{target} is claimed with other owners than {kept.ledger_account}, so moving it"
            " would change who sees the account, which matching never does.",
            Refusal.OTHER_OWNERS,
        )
    if into is not None and into.named is not None:
        raise MatchRefused(
            f"{target} is an account a member named, which no connection feeds"
            ' (docs/books.md, "Accounts hearth does not see").',
            Refusal.NAMED,
        )
    _not_taken(found, target, account_id)
    try:
        feeds, rows = booking.taken_once(conn, fed, opened=held.opened)
    except booking.Refused as refused:
        raise MatchRefused(str(refused), Refusal.BOOKING) from refused
    mine = [r for r in rows if feeds[r.account_id][1] == kept.ledger_account]
    plan = carrying.carry(held, target, mine)
    lost = [r for r in mine if plan.status(r) is Status.BEFORE]
    if lost:
        count = len(lost)
        raise MatchRefused(
            f"{count} of the transactions hearth has booked on {kept.ledger_account}"
            f" {'is' if count == 1 else 'are'} dated before {_day(plan.cut_over)}, where"
            f" carrying {target} on begins, and the imported ledger does not hold"
            f" {'it' if count == 1 else 'them'}, so moving it would take"
            f" {'it' if count == 1 else 'them'} out of the books. It stays where it is.",
            Refusal.WOULD_LOSE,
            count=count,
            day=plan.cut_over,
        )
    return Moving(kept, target, into, feeding)


def _move(conn: sqlite3.Connection, move: Moving, *, now: datetime, by: MemberId | None) -> None:
    """Carry `move.kept`'s connected accounts on from the base's account (`Moving`)."""
    with transaction(conn):
        if move.into is None:
            conn.execute(
                "UPDATE accounts SET ledger_account = ? WHERE id = ?", (move.target, move.kept.id)
            )
        else:
            for linked in move.feeding:
                conn.execute(
                    "UPDATE plaid_accounts SET hearth_account = ?, confirmed_at = ?,"
                    " confirmed_by = ? WHERE account_id = ?",
                    (move.into.id, now.isoformat(), by, linked.account_id),
                )
            conn.execute("DELETE FROM account_owners WHERE account_id = ?", (move.kept.id,))
            conn.execute("DELETE FROM accounts WHERE id = ?", (move.kept.id,))
        RulingStore(conn).carry_over(move.kept.ledger_account, move.target, at=now)


# What ``hearth match-accounts`` prints.


def describe(found: Survey) -> list[str]:
    """The survey in words for a terminal: counts, days and the names of accounts, never a
    payee or an amount."""
    lines = [
        "Each connected account, and which of the imported ledger's accounts it is"
        f" (the base {found.base.digest[:12]}):"
    ]
    if found.unsure is not None:
        lines.append(
            "Hearth cannot tell whether carrying has begun on any account, so no match is"
            f" changed until it can: {found.unsure}"
        )
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
            if one.begun:
                lines.append(
                    "  The match is fixed: a decision has been made in Review on its transactions"
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
                lines.append("  Carrying it on meets:")
            elif one.filed is not None:
                lines.append(f"  Moving it onto {one.facing.account} would meet:")
            else:
                lines.append(f"  Carrying it on {one.facing.account} would meet:")
            lines += [f"    {line}" for line in _facing(one.facing)]
        if one.matched is not None:
            continue
        account_id = linked.account_id
        if one.filed is not None:
            if one.facing is not None and one.facing.carried and not one.facing.missing.count:
                lines.append(
                    f"  Every transaction hearth has booked on {one.filed} is in the base or"
                    " comes after it: move it onto the base's account with"
                    f" --confirm {account_id}={one.facing.account}"
                )
            continue
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
    balance = _day(found.last_balance) if found.last_balance else "none"
    pad = _day(found.last_pad) if found.last_pad else "none"
    lines = [
        f"From the bank: {found.rows.count} transactions" + _span(found.rows),
        f"In the base already: {found.by_id} by Plaid id, {found.by_content} by content",
        f"The base's last entry on it: {_day(found.last_entry) if found.last_entry else 'none'};"
        f" its last balance assertion: {balance}; its last pad: {pad}",
    ]
    if found.cut_over is None:
        return lines
    if found.carried and found.postponed:
        return [
            *lines,
            "Not carried on yet: the base has an entry or a balance assertion on it dated after"
            f" today, so carrying it on would begin on {_day(found.cut_over)}; until then its"
            " history ends where the base's does",
        ]
    if not found.carried:
        last = found.cut_over - timedelta(days=1)
        return [
            *lines,
            "Not carried on: hearth does not carry an account of its kind on from the base"
            f" yet, so its history ends where the base's does, on {_day(last)}",
        ]
    return [
        *lines,
        f"Carried on from {_day(found.cut_over)}: before then the base's, from then on the bank's",
        f"Before then and not in the base, so never brought in: {found.missing.count}"
        + _span(found.missing),
        f"From then on and not in the base, so booked by hearth: {found.after.count}"
        + _span(found.after),
    ]


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
