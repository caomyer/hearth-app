"""The household's own ledger, as one member may bring it in and match their accounts to it.

Two things the command line does, for a member, through the gate (``docs/real-ledger.md``):

- Bringing in a ledger the household kept before hearth: reading a file to see what it holds
  (`LedgerDesk.check`), and importing it once (`LedgerDesk.bring_in`), each exactly as
  ``hearth import-ledger`` does it (``hearth.ledger_import``). Both are an admin's
  (``access.may_bring_in_ledger``); anyone else is told what something missing is told.
- Matching each of the member's own connected accounts to the imported ledger's account it is
  (`LedgerDesk.matching`, `LedgerDesk.match`), exactly as ``hearth match-accounts`` does it
  (``hearth.matching``), but for their own connections alone (``access.may_use_connection``),
  onto accounts they own (``access.may_feed``). The one account a member may take that nobody
  has claimed is the one the ledger itself records their connection's ids on, for an account
  hearth keeps for them (``access.may_carry_on``).
- Saying whose each of the ledger's accounts is, where nobody owns it yet (`LedgerDesk.owners`,
  `LedgerDesk.assign`), exactly as ``hearth add-account`` does it (``accounts.claim_all``), all
  at once. An admin's (``access.may_assign_owners``), who is told those accounts' names alone.

What a member is told names no account they could not otherwise see: an account the evidence
points at that someone else owns, or that nobody owns and only content points at, is said not
to be theirs, or nobody's yet, and never named.
"""

import itertools
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from beancount.core.data import Close, Open

from hearth import base, db, ledger_child, ledger_import
from hearth import matching as matches
from hearth.accounts import Account, AccountRejectedError, Claim, claim_all
from hearth.books import Books
from hearth.clock import Clock
from hearth.config import Settings
from hearth.disclosure.common import IDENTITY_HUES, MemberRef
from hearth.disclosure.gate import Concealed, Gate
from hearth.members import Member, MemberId
from hearth.reports.common import is_balance_sheet

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Said:
    """Why something cannot be done: the engine's word for it, and a plain sentence for the
    app, which names no path, no command and no setting (``ledger_import.ImportRefused``)."""

    why: str
    plain: str

    @classmethod
    def of(cls, refused: ledger_import.ImportRefused) -> Said:
        return cls(refused.why.value, refused.plain)


@dataclass(frozen=True, slots=True)
class LedgerStanding:
    """Where the household's ledger stands, for one member."""

    imported_on: date | None
    """The day its ledger was brought in, on the household's own calendar; None for none."""
    files: int | None
    """How many files the base holds."""
    unclaimed: int | None
    """How many of the books' Assets and Liabilities accounts nobody has claimed, so nobody
    sees: told to whoever may bring a ledger in, None to anyone else."""
    inherited: bool
    """Whether a ledger is inherited where it is (``HEARTH_LEDGER``)."""
    may_bring_in: bool
    """Whether this member may bring a ledger in now."""
    why_not: Said | None
    """Why not, to a member who may bring one in but for where the household stands."""


@dataclass(frozen=True, slots=True)
class Worth:
    """Net worth today at the ledger's own prices, as the import's check values it."""

    value: Decimal
    currency: str
    as_of: date
    is_floor: bool
    unpriced: tuple[tuple[str, str], ...]
    """Each holding it has no price for: its commodity and its account."""


@dataclass(frozen=True, slots=True)
class LedgerFound:
    """What a ledger file holds, as hearth reads it (``ledger_import.Check``)."""

    files: int
    entry_count: int
    """How many entries it holds, as hearth uses them."""
    first: date | None
    last: date | None
    accounts: tuple[ledger_import.Opened, ...]
    kinds: tuple[tuple[str, int], ...]
    """How many accounts it opens of each kind, in the order a balance sheet reads them."""
    assertions: int
    failing: int
    currencies: tuple[str, ...]
    commodities: tuple[str, ...]
    worth: Worth
    runs: tuple[str, ...]
    unused: tuple[ledger_import.Unused, ...]
    plugins: int
    """Of `unused`, the plugins hearth does not run, and the `documents` option."""
    custom: int
    documents: int


@dataclass(frozen=True, slots=True)
class LedgerCheck:
    """What a ledger file holds, if it loads, and why it would not be brought in, if not."""

    found: LedgerFound | None
    refused: Said | None


@dataclass(frozen=True, slots=True)
class BroughtIn:
    """What bringing a ledger in did: `imported`, `unchanged` (the base was this ledger
    already), `refused`, with the sentence saying why, or `failed`, on an unexpected error."""

    outcome: str
    refused: Said | None = None


@dataclass(frozen=True, slots=True)
class Facing:
    """What carrying a connected account on from one of the ledger's accounts meets
    (``matching.Facing``)."""

    account: str
    cut_over: date | None
    carried: bool
    postponed: bool
    rows: int
    by_id: int
    by_content: int
    before: matches.Stretch
    """The bank's rows before the cut-over the ledger does not hold: never brought in."""
    booked: matches.Stretch
    """Its rows from the cut-over on the ledger does not hold: what hearth books."""
    last_entry: date | None
    last_balance: date | None
    last_pad: date | None


@dataclass(frozen=True, slots=True)
class Refused:
    """Why a match would be refused (``matching.Refusal``), with what saying it needs."""

    why: str
    count: int | None = None
    day: date | None = None


@dataclass(frozen=True, slots=True)
class Choice:
    """One account of the ledger a connected account may be matched to, and what that
    would meet, or why it would be refused."""

    account: str
    proposed: bool
    facing: Facing | None
    refused: Refused | None


@dataclass(frozen=True, slots=True)
class Proposed:
    """What the ledger's evidence says a connected account is."""

    account: str
    how: str
    """`id`, by the Plaid ids the ledger's entries carry, or `content`, by its rows alone."""
    agreeing: int
    """How many of its ids are on the account, or how many of its rows it holds."""
    elsewhere: int
    """How many of its ids are on other accounts."""
    of: int
    """How many posted rows its bank has sent."""


@dataclass(frozen=True, slots=True)
class ConnectedAccount:
    """One of the member's own connected accounts, and which of the ledger's accounts it is."""

    id: str
    institution: str
    name: str
    mask: str | None
    kind: str
    state: str
    """`matched`, `kept` (filed as an account hearth keeps itself) or `unmatched`."""
    matched: str | None
    matched_on: date | None
    fixed: bool
    """Carrying its match on has begun (``books.begun``), so the match is fixed."""
    kept: str | None
    proposed: Proposed | None
    why_not: str | None
    """Why nothing is proposed for it, or why what is proposed cannot be had
    (``matching.Unproposed``, and `unclaimed` for an account nobody has claimed yet)."""
    choices: tuple[Choice, ...]
    facing: Facing | None
    """What carrying it on meets where it is matched now."""
    reconciling: bool
    """Its bank's balance meets the books only by a difference that waits in Review."""


@dataclass(frozen=True, slots=True)
class Matching:
    """The member's own connected accounts, matched to the imported ledger's."""

    imported: bool
    unsure: str | None
    accounts: tuple[ConnectedAccount, ...]

    @property
    def matched(self) -> int:
        return sum(1 for a in self.accounts if a.state == "matched")

    @property
    def in_ledger(self) -> int:
        """How many of the bank's rows the ledger holds already, on every account matched."""
        return sum(f.by_id + f.by_content for f in self._facings)

    @property
    def booked(self) -> int:
        """How many rows hearth books from each matched account's cut-over on."""
        return sum(f.booked.count for f in self._facings)

    @property
    def _facings(self) -> list[Facing]:
        return [a.facing for a in self.accounts if a.state == "matched" and a.facing is not None]


NOTHING = Matching(imported=False, unsure=None, accounts=())


@dataclass(frozen=True, slots=True)
class Unowned:
    """One of the imported ledger's accounts that nobody owns yet, as an admin is told it: its
    name in the ledger, its kind (its root), the days the ledger opens and closes it, as the
    ledger's check told them, and the name the household will see it by once it is owned."""

    account: str
    kind: str
    opened: date
    closed: date | None
    name: str


@dataclass(frozen=True, slots=True)
class Owners:
    """Whose the imported ledger's accounts are to be: every member, and each of the ledger's
    Assets and Liabilities accounts nobody owns yet."""

    imported: bool
    members: tuple[MemberRef, ...]
    """Every member, the viewer first."""
    choices: tuple[tuple[str, ...], ...]
    """Whose an account may be said to be, by usernames: each member alone, the viewer first,
    then every two or more together for a joint account, as ``hearth add-account`` takes them;
    in a household of more than `MOST_TOGETHER`, every member together alone among those."""
    accounts: tuple[Unowned, ...]


MOST_TOGETHER = 4
"""The most members a household may have for every group of them to be offered as joint owners:
past it, only all of them together, as the choices would be too many to read."""


def household_name(account: str) -> str:
    """The name the household sees one of the ledger's accounts by, once it is owned: its name
    in the ledger without its kind, each part a word, so `Assets:Harbor:Joint-Checking` is
    `Harbor Joint Checking`. The command line takes the name a person gives."""
    parts = account.split(":")[1:] or [account]
    return " ".join(part.replace("-", " ").replace("_", " ") for part in parts)


class LedgerDesk:
    """The household's ledger, as one member may bring it in and match their accounts to it."""

    def __init__(
        self,
        gate: Gate,
        *,
        accounts: Sequence[Account],
        settings: Settings,
        clock: Clock,
        books: Books | None,
        household: Sequence[Member] = (),
    ) -> None:
        self._gate = gate
        self._viewer: Member = gate.viewer
        self._accounts = list(accounts)
        self._members = tuple(household)
        self._settings = settings
        self._clock = clock
        self._books = books

    def __repr__(self) -> str:
        return "LedgerDesk()"

    # Bringing a ledger in.

    def standing(self) -> LedgerStanding:
        may = self._gate.may_bring_in_ledger()
        inherited = self._settings.ledger is not None
        try:
            kept = base.current(self._settings.ledger_base)
        except base.BaseError:
            kept = None
        why_not = None
        if may:
            try:
                ledger_import.household_allows(self._settings, self._clock(), once=True)
            except ledger_import.ImportRefused as refused:
                why_not = Said.of(refused)
        return LedgerStanding(
            imported_on=kept.imported_at.astimezone().date() if kept else None,
            files=len(kept.base.files) if kept else None,
            unclaimed=self._unclaimed(kept) if may else None,
            inherited=inherited,
            may_bring_in=may and why_not is None,
            why_not=why_not,
        )

    @property
    def may_bring_in(self) -> bool:
        return self._gate.may_bring_in_ledger()

    def check(self, path: Path) -> LedgerCheck:
        """What the ledger at `path` holds, and whether it would be brought in, writing
        nothing. Concealed from anyone who may not bring a ledger in."""
        if not self._gate.may_bring_in_ledger():
            raise Concealed
        # Read in a process of its own, which beancount's parser cannot hold the bridge up from.
        checked = ledger_child.check(self._settings, path, now=self._clock())
        refused = checked.refused
        return LedgerCheck(_found(checked.found), Said.of(refused) if refused is not None else None)

    def bring_in(self, path: Path) -> BroughtIn:
        """Bring the ledger at `path` in as the household's base, once, exactly as
        ``hearth import-ledger`` does. Concealed from anyone who may not."""
        if not self._gate.may_bring_in_ledger():
            raise Concealed
        try:
            done = ledger_child.bring_in(self._settings, path, now=self._clock())
        except Exception:
            # Nothing is written unless the import commits whole (``hearth.base``).
            _log.exception("bringing a ledger in stopped on an unexpected error")
            return BroughtIn("failed")
        if done.refused is not None:
            failed = done.refused.why is ledger_import.Refusal.FAILED
            return BroughtIn("failed" if failed else "refused", Said.of(done.refused))
        return BroughtIn(done.outcome)

    def _unclaimed(self, kept: base.Imported | None) -> int | None:
        """How many Assets and Liabilities accounts the imported ledger opens that nobody has
        claimed (`_unowned`)."""
        if self._books is None or kept is None:
            return None
        return len(self._unowned(kept))

    def _unowned(self, kept: base.Imported) -> list[Open]:
        """Each Assets and Liabilities account the imported ledger opens that nobody has
        claimed, in the order of their names: the base's own, by the file each is opened in,
        never an account hearth keeps itself, which books computed before a match moved its
        claim would still open. Income, Expenses and Equity are never anyone's: a row of them is
        seen by whoever owns the account on its other side."""
        claimed = {a.ledger_account for a in self._accounts}
        opened = {e.account: e for e in self._opened(kept) if e.account not in claimed}
        return [opened[name] for name in sorted(opened)]

    # Whose the ledger's accounts are.

    def owners(self) -> Owners:
        """Every member, and each of the imported ledger's Assets and Liabilities accounts
        that nobody owns yet, by name. Concealed from anyone who may not say whose they are."""
        if not self._gate.may_assign_owners():
            raise Concealed
        hues = {m.id: n % IDENTITY_HUES + 1 for n, m in enumerate(self._members)}
        ordered = sorted(self._members, key=lambda m: m.id != self._viewer.id)
        refs = tuple(MemberRef(m.username, m.display_name, hues[m.id]) for m in ordered)
        names = [m.username for m in ordered]
        sizes = range(2, len(names) + 1) if len(names) <= MOST_TOGETHER else [len(names)]
        choices = (
            *((name,) for name in names),
            *(group for size in sizes for group in itertools.combinations(names, size)),
        )
        kept = self._kept()
        if kept is None:
            return Owners(imported=False, members=refs, choices=choices, accounts=())
        closed = self._closed()
        return Owners(
            imported=True,
            members=refs,
            choices=choices,
            accounts=tuple(
                Unowned(
                    e.account,
                    e.account.split(":", 1)[0],
                    e.date,
                    closed.get(e.account),
                    household_name(e.account),
                )
                for e in self._unowned(kept)
            ),
        )

    def assign(self, chosen: Mapping[str, Sequence[str]]) -> bool:
        """Say whose each account `chosen` names is, by the usernames of its owners: one member,
        or several for a joint account, all at once through ``accounts.claim_all``, the one way
        an account comes to be owned. True when they were recorded; False when one of them was
        claimed since it was offered, so nothing was written. Concealed from anyone who may
        not, and for an account the ledger does not open, one that is not of the balance sheet,
        or a member who is not there, as for something that does not exist."""
        if not self._gate.may_assign_owners():
            raise Concealed
        kept = self._kept()
        if kept is None or self._books is None:
            raise Concealed
        offered = {e.account for e in self._opened(kept)}
        by_username = {m.username: m.id for m in self._members}
        claims: list[Claim] = []
        for account, usernames in chosen.items():
            if account not in offered or not usernames:
                raise Concealed
            owners: set[MemberId] = set()
            for username in usernames:
                member = by_username.get(username)
                if member is None:
                    raise Concealed
                owners.add(member)
            claims.append(Claim(account, household_name(account), frozenset(owners)))
        conn = db.connect(self._settings.database)
        try:
            claim_all(conn, claims, now=self._clock(), by=self._viewer.id)
        except AccountRejectedError:
            return False
        finally:
            conn.close()
        return True

    def _kept(self) -> base.Imported | None:
        try:
            return base.current(self._settings.ledger_base)
        except base.BaseError:
            return None

    def _opened(self, kept: base.Imported) -> list[Open]:
        """Each Assets and Liabilities account the imported ledger opens, owned or not."""
        if self._books is None:
            return []
        folder = str(kept.base.directory)
        return [
            e
            for e in self._books.entries
            if isinstance(e, Open)
            and is_balance_sheet(e.account)
            and str((e.meta or {}).get("filename", "")).startswith(folder)
        ]

    def _closed(self) -> dict[str, date]:
        if self._books is None:
            return {}
        return {e.account: e.date for e in self._books.entries if isinstance(e, Close)}

    # Matching.

    def matching(self) -> Matching:
        """The viewer's own connected accounts, each with what the imported ledger says it is,
        and every account of the ledger they may match it to, with what each would meet."""
        try:
            weighing = matches.weigh(self._settings, self._today())
        except matches.MatchRefused as refused:
            if refused.why is matches.Refusal.NO_BASE:
                return NOTHING
            return Matching(imported=True, unsure=str(refused), accounts=())
        claimed = {a.ledger_account: a for a in self._accounts}
        found = tuple(
            self._connected(weighing, one, claimed)
            for one in weighing.survey.accounts
            if self._gate.may_use_connection(one.proposal.connected.holder.id)
        )
        return Matching(imported=True, unsure=weighing.survey.unsure, accounts=found)

    def match(self, chosen: Mapping[str, str]) -> bool:
        """Match each of the viewer's connected accounts `chosen` names, by its Plaid id, to the
        account it names, one of those `matching` offers it, all at once through
        ``matching.confirm``: every one is checked before any is written. True when they were
        recorded, False when the check refused one since they were offered, and nothing was
        written; Concealed for a connected account that is not the viewer's, or an account not
        offered, as there is nothing to match without a ledger imported that hearth can read."""
        try:
            weighing = matches.weigh(self._settings, self._today())
        except matches.MatchRefused as refused:
            raise Concealed from refused
        claimed = {a.ledger_account: a for a in self._accounts}
        for account_id, to in chosen.items():
            one = weighing.survey.find(account_id)
            if one is None or not self._gate.may_use_connection(one.proposal.connected.holder.id):
                raise Concealed
            if to not in {account for account, _ in self._offered(weighing, one, claimed)}:
                raise Concealed
        try:
            matches.confirm(
                self._settings,
                list(chosen.items()),
                now=self._clock(),
                by=self._viewer.id,
                weighing=weighing,
            )
        except matches.MatchRefused:
            return False
        return True

    def _connected(
        self, weighing: matches.Weighing, one: matches.Surveyed, claimed: dict[str, Account]
    ) -> ConnectedAccount:
        proposal = one.proposal
        linked = proposal.connected.account
        offered = self._offered(weighing, one, claimed)
        named = {account for account, _ in offered}
        proposed = None
        if proposal.account is not None and self._nameable(proposal.account, claimed, named):
            ids = proposal.ids
            on = ids.on.get(proposal.account)
            content = next(
                (c.matched for c in proposal.content if c.account == proposal.account), 0
            )
            by_id = proposal.how is matches.How.ID
            proposed = Proposed(
                proposal.account,
                "id" if by_id else "content",
                on.count if on is not None and by_id else content,
                # Ids on other accounts are counted only on accounts the viewer may name.
                sum(
                    stretch.count
                    for account, stretch in ids.on.items()
                    if account != proposal.account and self._nameable(account, claimed, named)
                )
                if by_id
                else 0,
                len([r for r in proposal.connected.rows if not r.pending]),
            )
        choices = tuple(
            Choice(
                account,
                is_proposed,
                _facing(weighing.facing(one, account)),
                _refused(matches.refusal(self._settings, weighing, linked.account_id, account)),
            )
            for account, is_proposed in offered
        )
        matched = one.matched
        books = self._books
        return ConnectedAccount(
            id=linked.account_id,
            institution=proposal.connected.institution,
            name=linked.name,
            mask=linked.mask,
            kind=linked.subtype or linked.type,
            state="matched" if matched else "kept" if one.filed else "unmatched",
            matched=matched,
            matched_on=one.confirmed_at.astimezone().date() if one.confirmed_at else None,
            fixed=one.begun,
            kept=one.filed,
            proposed=proposed,
            why_not=self._why_not(proposal, claimed, named, proposed),
            choices=choices,
            facing=_facing(one.facing) if matched and one.facing else None,
            reconciling=bool(matched and books is not None and matched in books.reconciling),
        )

    def _nameable(self, account: str, claimed: dict[str, Account], offered: set[str]) -> bool:
        """Whether an account of the ledger may be named to the viewer: one they own, or one it
        is offered to them to move onto, which nobody owns, only where ``access.may_carry_on``
        holds. Nobody sees an account the ledger opens until it is claimed, so no other one
        nobody owns is ever named, nor anyone else's."""
        held = claimed.get(account)
        if held is None:
            return account in offered
        return self._gate.may_feed(held.owners)

    def _why_not(
        self,
        proposal: matches.Proposal,
        claimed: dict[str, Account],
        offered: set[str],
        proposed: Proposed | None,
    ) -> str | None:
        """Why nothing is proposed for one of the viewer's connected accounts, in the words of
        ``matching.Unproposed``, but only from what the viewer may see: evidence that points at
        an account they may not name, someone else's or one nobody owns, reads exactly as no
        evidence at all, `unclear`, so nothing changes when somebody else claims it."""
        why = proposal.unproposed
        if why is matches.Unproposed.IDS_DISAGREE:
            seen = all(self._nameable(a, claimed, offered) for a in proposal.ids.on)
            return "ids_disagree" if seen else "unclear"
        if why is matches.Unproposed.NOT_THEIRS:
            return "unclear"
        if why in (matches.Unproposed.WRONG_SIDE, matches.Unproposed.TAKEN):
            account = proposal.account
            if account is None or not self._nameable(account, claimed, offered):
                return "unclear"
        if why is not None:
            return why.value.replace("-", "_")
        if proposed is None:
            return "unclear"
        return None

    def _offered(
        self, weighing: matches.Weighing, one: matches.Surveyed, claimed: dict[str, Account]
    ) -> list[tuple[str, bool]]:
        """Every account of the ledger the viewer may match `one` to, and whether it is the one
        proposed: where it is matched now; accounts the ledger opens, and does not close, on
        the side its kind goes, that the viewer owns and no member named; and the one proposed,
        which may be one nobody has claimed only as `access.may_carry_on` allows."""
        held = weighing.held
        side = one.proposal.connected.side
        proposal = one.proposal.proposed
        kept = claimed.get(one.filed) if one.filed else None

        def opens(account: str) -> bool:
            return (
                account in held.opened
                and account not in held.closed_on
                and is_balance_sheet(account)
                and (side is None or account.split(":", 1)[0] == side)
            )

        mine = sorted(
            account
            for account, a in claimed.items()
            if opens(account) and a.named is None and self._gate.may_feed(a.owners)
        )
        if (
            proposal is not None
            and proposal not in claimed
            and kept is not None
            and opens(proposal)
            and self._gate.may_carry_on(
                kept.owners, recorded=matches.recorded(weighing.held, one.proposal)
            )
        ):
            mine.append(proposal)
        first = [a for a in (one.matched, proposal) if a is not None and a in mine]
        ordered = list(dict.fromkeys([*first, *mine]))
        return [(account, account == proposal) for account in ordered]

    def _today(self) -> date:
        return self._clock().astimezone().date()


def _found(found: ledger_import.Summary | None) -> LedgerFound | None:
    if found is None:
        return None
    counted: dict[str, int] = {}
    for opened in found.accounts:
        counted[opened.kind] = counted.get(opened.kind, 0) + 1
    unused = found.unused
    return LedgerFound(
        files=found.files,
        entry_count=found.entries,
        first=found.first,
        last=found.last,
        accounts=found.accounts,
        kinds=tuple(counted.items()),
        assertions=found.assertions,
        failing=found.failing,
        currencies=found.currencies,
        commodities=found.commodities,
        worth=Worth(
            found.worth, found.worth_currency, found.worth_as_of, found.worth_floor, found.unpriced
        ),
        runs=found.runs,
        unused=unused,
        plugins=sum(1 for u in unused if u.kind in ("plugin", "documents")),
        custom=sum(1 for u in unused if u.kind == "custom"),
        documents=sum(1 for u in unused if u.kind == "document"),
    )


def _facing(found: matches.Facing | None) -> Facing | None:
    if found is None:
        return None
    return Facing(
        account=found.account,
        cut_over=found.cut_over,
        carried=found.carried,
        postponed=found.postponed,
        rows=found.rows.count,
        by_id=found.by_id,
        by_content=found.by_content,
        before=found.missing,
        booked=found.after,
        last_entry=found.last_entry,
        last_balance=found.last_balance,
        last_pad=found.last_pad,
    )


def _refused(refused: matches.MatchRefused | None) -> Refused | None:
    if refused is None:
        return None
    return Refused(refused.why.value, refused.count, refused.day)
