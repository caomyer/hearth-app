"""The journal: every entry the viewer may see, newest first, each with both its sides.

An entry is in the viewer's journal when it moves money in an account whose transactions they
may see. What they are shown of it is what the pages show of its lines (``figures.lines``),
put back together into the one entry they came from:

- Its sides on accounts the viewer may see the transactions of are named, with its categories.
  A transfer between two of them is one entry, shown once, never as money out and money in.
- Its sides on any other account are one side, "another account", whose amount is what the
  named sides already add up to, so it tells nothing they do not.
- A side on an account hearth does not see (`books.unseen`) says only which way money moved,
  as its line does, unless the viewer owns every other side of the entry
  (``access.may_name_other_side``): one member's word for where their money went, and the
  bank's words for it, are their own account's transaction detail.

An opening balance is what an account held before its history, never something that
happened, so it is never an entry (``hearth.plaid.opening``). A balance check is, on the day
it covers, for whoever may see both the account's balance and its transactions: a balance on a
past day is the one less what moved since. Every check the books hold passed, as books that
fail one are not books (``books.compute``).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from beancount.core.data import Balance, Posting, Transaction

from hearth.accounts import Account
from hearth.books import REVIEW_EQUITY
from hearth.disclosure.common import Household
from hearth.figures import MOVED_IN, MOVED_OUT
from hearth.plaid.opening import anchors, held
from hearth.reports.common import is_balance_sheet, root, units
from hearth.review import reasons
from hearth.review.pile import TRANSFER_PENDING
from hearth.sharing import Facet

CATEGORY_ROOTS = ("Expenses", "Income")


class Kind(StrEnum):
    TRANSACTION = "transaction"
    TRANSFER = "transfer"
    """Money moved between accounts and nowhere else: no category, nothing spent or earned."""
    BALANCE = "balance"
    """A balance check, on the last day it covers."""


class Show(StrEnum):
    """Which entries the journal is narrowed to, beside its other filters."""

    EVERYTHING = "everything"
    UNREVIEWED = "unreviewed"
    TRANSFERS = "transfers"
    CHECKS = "balance_checks"


class Waiting(StrEnum):
    """What an unreviewed entry that has no category yet is waiting to be told."""

    CATEGORY = "category"
    """Where the money went: nothing could be proposed for it."""
    TRANSFER = "transfer"
    """The other side of a card payment, which has not been found yet."""


class Checked(StrEnum):
    BANK = "bank"
    """The balance its bank reported, which hearth checks the books against."""
    BROKERAGE = "brokerage"
    """What its brokerage reported the account holds."""
    BOOKS = "books"
    """A check the books hold, such as a statement's closing balance."""


@dataclass(frozen=True, slots=True)
class AccountRef:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class Side:
    """One side of an entry: an account the viewer may see, a category, money still waiting
    to be told where it went, or another account."""

    amount: Decimal
    account: AccountRef | None = None
    category: str | None = None
    waiting: bool = False
    elsewhere: bool = False


@dataclass(frozen=True, slots=True)
class Entry:
    kind: Kind
    date: date
    payee: str
    narration: str
    accounts: tuple[AccountRef, ...]
    """The viewer's accounts it moved, money out first: a transfer's from and to."""
    category: str | None
    """Its one category, when it has exactly one."""
    unreviewed: bool
    amount: Decimal
    """What it did to the accounts it names: for a transfer between two of them, what moved."""
    sides: tuple[Side, ...]
    waiting: Waiting | None = None
    checked: Checked | None = None
    reconciling: bool = False
    """A check of its bank's balance that the books meet only by an entry reconciling the
    imported ledger with the bank, which nobody has confirmed yet (``books.reconciling``): it
    is not shown as matching its bank."""
    reason: str | None = None
    """Why an unreviewed entry is suggested as it is, or why nothing is, in words
    (``hearth.proposals``): only for a viewer who reviews it, as it says what its owners
    decided before, which sharing its transactions does not share."""

    @property
    def suggested(self) -> bool:
        """Whether its category is one hearth suggested, which nobody has checked yet."""
        return self.unreviewed and self.category is not None

    def categories(self) -> tuple[str, ...]:
        return tuple(s.category for s in self.sides if s.category is not None)


@dataclass(frozen=True, slots=True)
class Filter:
    start: date | None = None
    """The first day; None for the first of the books' year."""
    end: date | None = None
    """The last day; None for the books' own day."""
    account: str | None = None
    """An account's id; one the viewer may not see the transactions of is not there."""
    category: str | None = None
    """A category's ledger name, which takes in the categories beneath it."""
    text: str | None = None
    """Words to find in a payee or a description, in any case."""
    show: Show = Show.EVERYTHING


@dataclass(frozen=True, slots=True)
class Counts:
    """How many entries each of `Show` would leave, under the journal's other filters."""

    everything: int
    unreviewed: int
    transfers: int
    balance_checks: int


@dataclass(frozen=True, slots=True)
class Journal:
    start: date
    end: date
    counts: Counts
    total: int
    """How many entries the whole filter leaves; `window` is the part of them asked for."""
    offset: int
    window: tuple[Entry, ...]


def journal(
    h: Household,
    accounts: Sequence[Account],
    narrowed: Filter,
    *,
    offset: int,
    limit: int,
    account: Account | None,
) -> Journal:
    """The entries `narrowed` leaves, newest first, `limit` of them from `offset`. `account`
    is the one `narrowed` names, which the caller has made sure the viewer may see into."""
    books = h.books
    start = narrowed.start or date(books.as_of.year, 1, 1)
    end = narrowed.end or books.as_of
    reader = _Reader(h, accounts)
    found: list[tuple[tuple[date, int, int], Entry]] = []
    for index, entry in enumerate(books.entries):
        if isinstance(entry, Transaction) and start <= entry.date <= end and not _opening(entry):
            found.extend(((entry.date, 0, index), e) for e in reader.entries(entry))
        elif isinstance(entry, Balance):
            check = reader.check(entry)
            if check is not None and start <= check.date <= end:
                found.append(((check.date, 1, index), check))
    found.sort(key=lambda pair: pair[0], reverse=True)
    kept = [e for _, e in found if _keeps(e, narrowed, account)]
    counts = Counts(
        everything=len(kept),
        unreviewed=sum(1 for e in kept if e.unreviewed),
        transfers=sum(1 for e in kept if e.kind is Kind.TRANSFER),
        balance_checks=sum(1 for e in kept if e.kind is Kind.BALANCE),
    )
    shown = [e for e in kept if _shows(e, narrowed.show)]
    return Journal(
        start=start,
        end=end,
        counts=counts,
        total=len(shown),
        offset=offset,
        window=tuple(shown[offset : offset + limit]),
    )


def _opening(entry: Transaction) -> bool:
    """An opening balance: hearth's, or any entry that balances an account against equity
    outside review, as `books.unanchored` reads one."""
    return anchors(entry) or any(
        root(p.account) == "Equity" and not p.account.startswith(REVIEW_EQUITY)
        for p in entry.postings
    )


def _keeps(entry: Entry, narrowed: Filter, account: Account | None) -> bool:
    if account is not None and account.id not in {a.id for a in entry.accounts}:
        return False
    if narrowed.category is not None:
        wanted = narrowed.category
        if not any(c == wanted or c.startswith(f"{wanted}:") for c in entry.categories()):
            return False
    if narrowed.text is not None:
        words = narrowed.text.casefold()
        if words not in entry.payee.casefold() and words not in entry.narration.casefold():
            return False
    return True


def _shows(entry: Entry, show: Show) -> bool:
    match show:
        case Show.EVERYTHING:
            return True
        case Show.UNREVIEWED:
            return entry.unreviewed
        case Show.TRANSFERS:
            return entry.kind is Kind.TRANSFER
        case Show.CHECKS:
            return entry.kind is Kind.BALANCE


class _Reader:
    """Reads the books' entries as the viewer may see them."""

    def __init__(self, h: Household, accounts: Sequence[Account]) -> None:
        gate = h.gate
        self._currency = h.books.currency
        self._unseen = h.books.unseen
        self._reconciling = h.books.reconciling
        self._by_ledger: Mapping[str, Account] = {a.ledger_account: a for a in accounts}
        self._sees = {a.ledger_account for a in accounts if gate.may(Facet.TRANSACTIONS, a.owners)}
        self._balances = {
            a.ledger_account for a in accounts if gate.may(Facet.ACCOUNT_BALANCES, a.owners)
        }
        self._owns = {a.ledger_account for a in accounts if gate.may_name_other_side(a.owners)}
        self._reviews = {a.ledger_account for a in accounts if gate.may_review(a.owners)}

    def _ref(self, ledger_account: str) -> AccountRef:
        account = self._by_ledger[ledger_account]
        return AccountRef(account.id, account.name)

    def entries(self, entry: Transaction) -> list[Entry]:
        legs = [
            p
            for p in entry.postings
            if is_balance_sheet(p.account) and units(p, entry).currency == self._currency
        ]
        mine = [p for p in legs if p.account in self._sees]
        if not mine:
            return []
        seen = [p for p in mine if p.account not in self._unseen]
        apart = [
            p
            for p in mine
            if p.account in self._unseen
            and not (seen and all(o.account in self._owns for o in legs if o is not p))
        ]
        named = [p for p in mine if not any(p is q for q in apart)]
        found = [self._entry(entry, legs, named)] if named else []
        return found + [self._moved(entry, p) for p in apart]

    def _entry(self, entry: Transaction, legs: list[Posting], named: list[Posting]) -> Entry:
        rest = [p for p in entry.postings if not is_balance_sheet(p.account)]
        sides = [Side(units(p, entry).number, account=self._ref(p.account)) for p in named]
        for p in rest:
            value = units(p, entry)
            if value.currency != self._currency:
                continue
            if root(p.account) in CATEGORY_ROOTS:
                sides.append(Side(value.number, category=p.account))
            else:
                sides.append(Side(value.number, waiting=True))
        others = [p for p in legs if not any(p is q for q in named)]
        if others:
            moved = sum((units(p, entry).number for p in others), Decimal(0))
            sides.append(Side(moved, elsewhere=True))
        categories = [p.account for p in rest if root(p.account) in CATEGORY_ROOTS]
        mine = sorted(named, key=lambda p: units(p, entry).number)
        transfer = not rest
        amount = sum((units(p, entry).number for p in named), Decimal(0))
        if transfer and len(named) > 1:
            amount = sum((n for p in named if (n := units(p, entry).number) > 0), Decimal(0))
        waiting = None
        if any(root(p.account) not in CATEGORY_ROOTS for p in rest):
            pending = any(p.account == TRANSFER_PENDING for p in rest)
            waiting = Waiting.TRANSFER if pending else Waiting.CATEGORY
        return Entry(
            kind=Kind.TRANSFER if transfer else Kind.TRANSACTION,
            date=entry.date,
            payee=entry.payee or entry.narration or "",
            # A bank that repeats its payee as the description says nothing more with it.
            narration=(
                (entry.narration or "") if entry.payee and entry.narration != entry.payee else ""
            ),
            accounts=tuple(self._ref(p.account) for p in mine),
            category=categories[0] if len(rest) == 1 and categories else None,
            unreviewed=entry.flag == "!",
            amount=amount,
            sides=tuple(sides),
            waiting=waiting,
            reason=self._reason(entry),
        )

    def _reason(self, entry: Transaction) -> str | None:
        """Why an unreviewed review row is suggested as it is, for a viewer who reviews it."""
        if (
            entry.flag != "!"
            or not entry.postings
            or entry.postings[0].account not in self._reviews
        ):
            return None
        found = reasons.read(entry.meta)
        return found.words if found is not None else None

    def _moved(self, entry: Transaction, posting: Posting) -> Entry:
        """A side on an account hearth does not see, on its own: which way money moved."""
        amount = units(posting, entry).number
        return Entry(
            kind=Kind.TRANSFER,
            date=entry.date,
            payee=MOVED_IN if amount > 0 else MOVED_OUT,
            narration="",
            accounts=(self._ref(posting.account),),
            category=None,
            unreviewed=entry.flag == "!",
            amount=amount,
            sides=(
                Side(amount, account=self._ref(posting.account)),
                Side(-amount, elsewhere=True),
            ),
        )

    def check(self, entry: Balance) -> Entry | None:
        """A balance check, for whoever may see the account's balance and its transactions."""
        account = entry.account
        if account not in self._sees or account not in self._balances:
            return None
        if entry.amount.currency != self._currency or entry.amount.number is None:
            return None
        checked = Checked.BOOKS
        if held(entry):
            checked = Checked.BROKERAGE
        elif anchors(entry) and account not in self._reconciling:
            checked = Checked.BANK
        return Entry(
            kind=Kind.BALANCE,
            date=entry.date - timedelta(days=1),
            payee="",
            narration="",
            accounts=(self._ref(account),),
            category=None,
            unreviewed=False,
            amount=entry.amount.number,
            sides=(),
            checked=checked,
            # Its bank's balance, met only by a difference nobody has confirmed yet.
            reconciling=anchors(entry) and account in self._reconciling,
        )
