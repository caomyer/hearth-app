"""What a row hearth books is suggested as, and why.

Every row hearth books from a connection that is not a move to or from an account it does not
see is flagged for review against a suggested category, or against nothing. The suggestion is
the first of these that gives one (`Proposer.propose`):

1. **What the row's owners decided before for the same merchant** (``hearth.merchants``): the
   latest decisions, up to `LATEST` of them, on rows of the merchant, when every one of them
   booked the same category. A decision is a ruling of one of the row's owners, on a row of an
   account with exactly them as its owners (``access.may_learn``), made before the row first
   arrived. "Dining twice before".
2. **How the imported ledger books the same merchant**: the latest of the ledger's own
   entries for it, up to `LATEST`, on accounts with exactly the row's owners, when every one books
   the same one category. "As your ledger books it".
3. **The bank's category, as the ledger names it**: what the rows the ledger holds of the
   bank's own, on accounts with exactly the row's owners, say each of the bank's categories is, when
   at least `MAP_AT_LEAST` rows of the category are held and a strict majority of them book the
   same one category. "Bank says Restaurant".
4. **The bank's category, as hearth names it** (``booking.proposal``); for a household with an
   imported ledger only when the ledger already books that category on an account all the
   row's owners own. Hearth never invents a category beside a ledger that has its own.

A suggested account must be one the books can post the row to on its day: a category open
then and taking the row's currency (`usable`). Otherwise the next step is tried, and with
none, nothing is suggested and the row is asked plainly: "New merchant" when nothing steps 1
and 2 may learn from decided or booked anything for its merchant, "Booked more than one way
before" when their latest decisions or entries disagree.

A suggestion is never a decision. The row still arrives flagged and waits for a member, however
often its merchant was decided before; there is no accepting on anyone's behalf.

None of this ever changes a row somebody ruled on. A ruling makes its row immune to what is
learned: an accept keeps booking the row where it was accepted for as long as the bank's own
inputs to it are what they were (``review.decision``), whatever is learned, claimed, matched,
connected or imported since. What is learned changes only what an unruled row suggests, and
even then not because of a ruling made after the row arrived (`Target.arrived`): so a row a
member is looking at does not change its suggestion under them as they rule on its
neighbours.
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

from hearth.carrying import BaseEntry
from hearth.members import MemberId
from hearth.names import readable
from hearth.review.reasons import Reason, Why
from hearth.review.rulings import ACCOUNT, Action, Covered, Scope

LATEST = 5
"""How many of a merchant's latest decisions, or of the ledger's latest entries for it, must
all agree for it to be suggested: a member who books one of them elsewhere is asked again,
rather than told the exception is now the rule or that the rule still holds."""

MAP_AT_LEAST = 3
"""How many rows of one bank category the ledger must hold before what most of them book is
the category's suggestion; of those, strictly more than half must book the same one."""

CATEGORIES = ("Expenses", "Income")

type Owners = frozenset[MemberId]

type MayLearn = Callable[[MemberId | None, Owners, Owners], bool]
"""The access policy's answer to: may a decision by this author (None for the household's own
ledger) on a row of an account these own suggest a category for a row of an account those
own? (``access.may_learn``)"""


def learn_nothing(author: MemberId | None, owners: Owners, source: Owners) -> bool:
    """Learn from nothing at all, for whatever reads the books without suggesting anything."""
    return False


type Opens = Mapping[str, tuple[date, date | None, frozenset[str] | None]]
"""Each account a ledger hearth reads opens: the day it opens, the day it closes, if it does,
and the currencies it takes, if it names them."""


@dataclass(frozen=True, slots=True)
class Proposal:
    account: str | None
    reason: Reason | None


NOTHING = Proposal(None, None)


@dataclass(frozen=True, slots=True)
class Target:
    """A row to suggest a category for."""

    review_id: str
    merchant: str | None
    day: date
    currency: str
    owners: Owners
    category: str | None
    """The bank's category, as Plaid names it."""
    default: str | None
    """The category hearth names the bank's category as (``booking.proposal``), if any."""
    bank_says: str | None
    """The bank's category in words, as the reason says it."""
    arrived: datetime | None
    """When the row first arrived; None when that is not known, which learns from no ruling."""


@dataclass(frozen=True, slots=True)
class Ruled:
    """A row a ruling covers, as a source of what its owners decided."""

    review_id: str
    merchant: str | None
    day: date
    owners: Owners
    covering: tuple[Covered, ...]


@dataclass(frozen=True, slots=True)
class Ledger:
    """What the imported ledger teaches (`Proposer`)."""

    entries: Sequence[BaseEntry]
    merchants: Mapping[int, frozenset[str]]
    """Each entry's merchants: its own words', and those of the bank's row it holds, if any."""
    held: Sequence[tuple[str, int]]
    """Each row of the bank's the ledger holds, as the bank's category and the entry holding it."""
    owners: Callable[[str], Owners]
    """Who owns each of its balance-sheet accounts."""


@dataclass
class Proposer:
    """Suggests a category for each row hearth books (the module's rules)."""

    may_learn: MayLearn
    ruled: Sequence[Ruled] = ()
    ledger: Ledger | None = None
    opens: Opens = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._ruled: defaultdict[str, list[Ruled]] = defaultdict(list)
        for row in self.ruled:
            if row.merchant is not None:
                self._ruled[row.merchant].append(row)
        self._entries: defaultdict[str, list[BaseEntry]] = defaultdict(list)
        self._by_number: dict[int, BaseEntry] = {}
        if self.ledger is not None:
            for entry in self.ledger.entries:
                self._by_number[entry.entry] = entry
                for name in self.ledger.merchants.get(entry.entry, ()):
                    self._entries[name].append(entry)
        self._maps: dict[Owners, dict[str, str]] = {}
        self._booked: dict[Owners, frozenset[str]] = {}

    def propose(self, row: Target) -> Proposal:
        """The first of the module's four steps that suggests a category for `row`, and why;
        or nothing, saying why where there is something to say."""
        found: set[Found] = set()
        for learned in (self._remembered(row), self._as_booked(row)):
            if isinstance(learned, Proposal):
                return learned
            if learned is not None:
                found.add(learned)
        if self._uses_ledger(row):
            mapped = self._map(row.owners).get(row.category or "")
            if mapped is not None and self.usable(mapped, row):
                return Proposal(mapped, self._bank(row))
        default = row.default
        if (
            default is not None
            and (not self._uses_ledger(row) or default in self._categories(row.owners))
            and self.usable(default, row)
        ):
            return Proposal(default, self._bank(row))
        if Found.DISAGREE in found:
            return Proposal(None, Reason(Why.MIXED, "Booked more than one way before"))
        if Found.ELSEWHERE in found:
            return Proposal(None, Reason(Why.ELSEWHERE, "Not booked to a category before"))
        if row.merchant is not None and not found:
            return Proposal(None, Reason(Why.NEW, "New merchant"))
        return NOTHING

    def usable(self, account: str, row: Target) -> bool:
        """Whether the books can post `row` to `account`: a category, open on the row's day
        in a ledger hearth reads if that ledger opens it, and taking the row's currency."""
        if not ACCOUNT.fullmatch(account) or account.split(":", 1)[0] not in CATEGORIES:
            return False
        found = self.opens.get(account)
        if found is None:
            return True
        opens, closes, currencies = found
        return (
            opens <= row.day
            and (closes is None or row.day <= closes)
            and (currencies is None or row.currency in currencies)
        )

    def _uses_ledger(self, row: Target) -> bool:
        return self.ledger is not None

    def _remembered(self, row: Target) -> Proposal | Found | None:
        """The category the row's owners booked its merchant as in their latest decisions
        before it arrived, when they agree on one it may be booked to; otherwise what they
        found instead, or None when they decided nothing for it."""
        if row.merchant is None or row.arrived is None:
            return None
        arrived = row.arrived
        decided: list[tuple[int, date, str, str | NoCategory]] = []
        for source in self._ruled.get(row.merchant, ()):
            if source.review_id == row.review_id:
                continue
            before = [
                c
                for c in source.covering
                if c.ruling.at < arrived
                and self.may_learn(c.ruling.member_id, row.owners, source.owners)
            ]
            found = decided_as(before)
            if found is not None:
                ruling_id, account = found
                decided.append((ruling_id, source.day, source.review_id, account))
        if not decided:
            return None
        decided.sort(key=lambda d: (d[0], d[1], d[2]), reverse=True)
        agreed = _agreed([account for *_, account in decided[:LATEST]])
        if not isinstance(agreed, str):
            return agreed
        if not self.usable(agreed, row):
            return Found.UNUSABLE
        times = sum(1 for *_, account in decided if account == agreed)
        return Proposal(agreed, Reason(Why.REMEMBERED, f"{label(agreed)} {_times(times)} before"))

    def _as_booked(self, row: Target) -> Proposal | Found | None:
        """The category the ledger's latest entries for the row's merchant book, on accounts
        with exactly its owners, when they agree on one it may be booked to; otherwise what they
        found instead, or None when the ledger has no such entry, or teaches the row nothing."""
        if row.merchant is None or not self._uses_ledger(row):
            return None
        found = [e for e in self._entries.get(row.merchant, ()) if self._theirs(e, row.owners)]
        if not found:
            return None
        found.sort(key=lambda e: (e.day, e.entry), reverse=True)
        agreed = _agreed([e.category or NO_CATEGORY for e in found[:LATEST]])
        if not isinstance(agreed, str):
            return agreed
        if not self.usable(agreed, row):
            return Found.UNUSABLE
        return Proposal(agreed, Reason(Why.LEDGER, "As your ledger books it"))

    def _theirs(self, entry: BaseEntry, owners: Owners) -> bool:
        """Whether the ledger's `entry` may teach a row `owners` own: it is complete, moves
        money, and every account it moves has exactly them as its owners."""
        ledger = self.ledger
        return (
            ledger is not None
            and entry.checked
            and bool(entry.accounts)
            and all(self.may_learn(None, owners, ledger.owners(a)) for a in entry.accounts)
        )

    def _map(self, owners: Owners) -> dict[str, str]:
        """Each bank category with the category most of the rows the ledger holds of it book,
        on accounts with exactly `owners`, where `MAP_AT_LEAST` rows of it are held and a strict
        majority agree."""
        if owners in self._maps:
            return self._maps[owners]
        counted: defaultdict[str, Counter[str | None]] = defaultdict(Counter)
        if self.ledger is not None:
            for category, number in self.ledger.held:
                entry = self._by_number.get(number)
                if entry is not None and self._theirs(entry, owners):
                    counted[category][entry.category] += 1
        found: dict[str, str] = {}
        for category, booked in counted.items():
            held = sum(booked.values())
            most, count = booked.most_common(1)[0]
            if held >= MAP_AT_LEAST and most is not None and 2 * count > held:
                found[category] = most
        self._maps[owners] = found
        return found

    def _categories(self, owners: Owners) -> frozenset[str]:
        """The categories the ledger books on accounts with exactly `owners`."""
        if owners not in self._booked:
            self._booked[owners] = frozenset(
                e.category
                for e in self._by_number.values()
                if e.category is not None and self._theirs(e, owners)
            )
        return self._booked[owners]

    @staticmethod
    def _bank(row: Target) -> Reason | None:
        return Reason(Why.BANK, f"Bank says {row.bank_says}") if row.bank_says else None


class NoCategory(Enum):
    """A decision that booked a row to no category: ruled not a transaction, or moved onto an
    account; or a ledger entry that books none, as a transfer or a split does."""

    NONE = "none"


NO_CATEGORY = NoCategory.NONE


class Found(Enum):
    """What a merchant's history gave instead of a suggestion."""

    DISAGREE = "disagree"
    """Its latest decisions, or entries, name more than one thing."""
    ELSEWHERE = "elsewhere"
    """Every one of them booked it to no category (`NO_CATEGORY`)."""
    UNUSABLE = "unusable"
    """They agree on a category the row cannot be booked to on its day."""


def decided_as(covering: Iterable[Covered]) -> tuple[int, str | NoCategory] | None:
    """What `covering`, the rulings on one row oldest first, decided it as, by the precedence
    a decision has (``review.decision``): the ruling that decides it, and the category it
    booked, or `NO_CATEGORY` for a decision that booked none, such as a row ruled not a
    transaction or moved onto an account. None when they leave it undecided.

    An accept decided what its author was shown, whatever the row suggests now, and a ruling
    decided its merchant's category even if the bank has since revised the row's amount."""
    latest: dict[Scope, Covered] = {}
    for covered in covering:
        latest[covered.ruling.scope] = covered
    row, group = latest.get(Scope.ROW), latest.get(Scope.GROUP)
    chosen = row
    if row is None or (
        row.ruling.action is Action.REOPEN and group is not None and group.ruling.id > row.ruling.id
    ):
        chosen = group
    if chosen is None or chosen.ruling.action is Action.REOPEN:
        return None
    ruling = chosen.ruling
    account = chosen.accepted if ruling.action is Action.ACCEPT else ruling.account
    if account is None or account.split(":", 1)[0] not in CATEGORIES:
        return ruling.id, NO_CATEGORY
    return ruling.id, account


def _agreed(accounts: Sequence[str | NoCategory]) -> str | Found:
    """The one category `accounts` all name; else that they all name none, or disagree."""
    found = set(accounts)
    if len(found) > 1:
        return Found.DISAGREE
    only = found.pop()
    return Found.ELSEWHERE if only is NO_CATEGORY else only


def label(account: str) -> str:
    """A category as a reason names it: its most specific part, in words."""
    return readable(account.rsplit(":", 1)[-1])


def _times(count: int) -> str:
    return {1: "once", 2: "twice"}.get(count, f"{count} times")


def group_of(account: str) -> str:
    """The review group of rows suggested as `account`: the category in words, after
    "Expenses", so every row in a group suggests the same category and accepting the group is
    one decision. "Expenses:FoodAndDrink:Coffee" is "Food and drink: coffee", the name of
    Plaid's category for it; "Income:Wages" is "Income: wages"."""
    parts = account.split(":")
    if parts[0] == "Expenses" and len(parts) > 1:
        parts = parts[1:]
    words = [readable(part) for part in parts]
    return ": ".join([words[0], *(_lower(w) for w in words[1:])])


def _lower(words: str) -> str:
    """`words` as they read after a colon: lowercase, but for an abbreviation ("ATM fees")."""
    first = words.split(" ", 1)[0]
    return words if len(first) > 1 and first.isupper() else words[:1].lower() + words[1:]
