"""One member's review desk: their unreviewed rows, grouped, and the rulings they make on them.

Who may review a row is not decided here. The desk is handed the household's access policy as
`may_review(member, owners)` and asks it about every row, for the member at the desk and for
the author of every ruling, so a ruling made by someone who has since stopped owning an account
no longer decides that account's rows. A row on an account nobody owns is reviewed by nobody.

A row the member may not review behaves exactly like a row that does not exist: both raise
`Concealed`, so the desk never reveals what is on anyone else's pile, not even a count.

A row may be moved to any spending or income category, but to a balance-sheet account only
when the books hold it open on the row's day and the policy, handed over as
`may_move_to(owners)` for the member at the desk, lets them feed it: one of their own. Anything
else, an equity account included, is refused with `NotTheirs`, which says the same whether the
account is someone else's or nobody's, so the desk cannot be used to learn another member's
account names.

An account a member named that hearth does not see (``hearth.accounts.name_account``) is open
on every row's day by construction (``hearth.ownbooks``), so it takes a moved row as soon as it
is named, before the books are next recomputed.

An account whose balance its bank reports is the bank's to fill, not a ruling's: the books
assert that balance (``hearth.plaid.opening``), so a row moved into it, or one of its own rows
ruled not a transaction, would leave the books disagreeing with the bank, and every member's
figures would wait on it. That holds as much while the assertion waits on a sync that can
clear it (``opening.PASSING``) as once it is in the books, since a ruling made meanwhile would
be there when it is. So such an account, one of `banks`, is never a move target
(`BanksAccount`), and its rows are never rejected (`BanksRow`); either refusal is about an
account of the member's own, so it names nothing they could not see. A transfer between two
connected accounts needs no ruling: pairing books it once. A brokerage account is one of
them, its cash what its brokerage reports. An account a household keeps by hand, or one whose
balance is never its bank's to assert, still takes a moved row.

Every accept is bound to what its author was shown. A page sends back each row it showed with
the account it proposed then, and the desk accepts a row only while it is still waiting and
still proposes that account, so a proposal that changed between the page and the click is never
accepted unseen. Accepting a group records, row by row, the account each row proposed then
(``hearth.review.decision``): a row whose proposal changes later is unreviewed again, and a row
the import had nothing to propose for is never covered.

A row whose other side hearth does not see (`ItemView.one_sided`) is settled by saying where
the money went, not by a category, and is answered a group at a time (``hearth.review.where``):
`move_group` moves every row a page showed onto one account, each bound to what it said then,
as a group accept is, and a row unlike the rest of its group is answered alone (`set_account`).
Where rows like one were said to have gone into an account of the member's own, the desk
proposes it for the next (`learned`).
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from hearth.accounts import Account
from hearth.clock import Clock
from hearth.disclosure import Concealed
from hearth.members import MemberId
from hearth.review.decision import Decision, State, decide
from hearth.review.pile import Moved, PileItem
from hearth.review.rulings import Action, Covered, Ruling, RulingStore, Scope, check_account

__all__ = [
    "Acceptance",
    "BanksAccount",
    "BanksRow",
    "Decision",
    "GroupView",
    "ItemView",
    "Learned",
    "Moved",
    "NotOneDecision",
    "NotTheirs",
    "ReviewDesk",
    "State",
    "one_decision",
    "resolve",
]

type MayReview = Callable[[MemberId, Collection[MemberId]], bool]
"""The access policy's answer to: may this member review rows on an account these own?"""

type MayMoveTo = Callable[[Collection[MemberId]], bool]
"""The access policy's answer to: may the member at the desk move a row into an account these
own?"""

FREE = frozenset({"Expenses", "Income"})
"""Where any member may put a row: a category says what it was, not whose money it is."""

HELD = frozenset({"Assets", "Liabilities"})
"""Where a row may go only into an account of the member's own."""

type Opened = Mapping[str, tuple[date, date | None]]
"""Each account the books open, with the day it opens and the day it closes, if it does."""

type Moves = Callable[[str], Moved | None]
"""What kind of move a review group's rows are, if they move money whose other side hearth
does not see (``hearth.plaid.booking.moved``)."""


def _nothing_moves(group: str) -> Moved | None:
    return None


@dataclass(frozen=True, slots=True)
class ItemView:
    item: PileItem
    owners: frozenset[MemberId]
    decision: Decision
    account: Account | None
    """The household account the row is on; only ever one the member may review."""
    bank_counts: bool = False
    """Whether the row's bank counts it in the balance it reports, so it cannot be excluded
    (`BanksRow`)."""
    moved: Moved | None = None
    """What kind of move it is, when it moves money whose other side hearth does not see."""

    @property
    def unreviewed(self) -> bool:
        return self.decision.state is State.UNREVIEWED

    @property
    def one_sided(self) -> bool:
        """Whether it is waiting to be told where its money went: one side of a card payment
        hearth could not match, or a transfer or loan payment whose other account the bank
        does not name, and nothing proposed for it. A category is no answer to "is this
        right?" for these, so they are asked where the money went instead, a group at a time,
        and counted apart from what is left (``hearth.review.where``)."""
        return self.unreviewed and (
            self.item.transfer or (self.moved is not None and self.item.proposed is None)
        )

    @property
    def actionable(self) -> bool:
        """Whether it is left for the member in the queue: unreviewed, and not one waiting to
        be told where its money went (`one_sided`)."""
        return self.unreviewed and not self.one_sided


@dataclass(frozen=True, slots=True)
class Learned:
    """What the member has said rows like one went into: an account of theirs hearth does not
    see, and how many such rows they have moved there. A proposal, never a decision."""

    account: Account
    like: int


@dataclass(frozen=True, slots=True)
class GroupView:
    name: str
    items: tuple[ItemView, ...]

    @property
    def pending(self) -> int:
        """Rows waiting on the member; a row that needs its other side is counted apart."""
        return sum(1 for view in self.items if view.actionable)


UNCERTAIN_GROUPS = frozenset(
    {"unsure", "unknown", "unknown-type", "uncategorized", "uncategorised", "other", "misc"}
)
"""Group names an import gives rows it could not place: groups defined by being unsure."""


class NotTheirs(ValueError):  # noqa: N818 - it names a refusal, as Concealed does
    """An account a member may not move a row to: not theirs, not open, or not a category.

    One refusal for all of these, naming nobody."""

    def __init__(self) -> None:
        super().__init__("a row moves only to a category or one of the member's own accounts")


class BanksAccount(ValueError):  # noqa: N818 - it names a refusal, as Concealed does
    """An account of the member's own whose balance its bank reports: a row moved into it would
    count money there the bank says is not."""

    def __init__(self) -> None:
        super().__init__("an account whose balance its bank reports takes only its bank's rows")


class BanksRow(ValueError):  # noqa: N818 - it names a refusal, as Concealed does
    """A row on an account whose balance its bank reports: the bank counts it, so leaving it
    out would make the books disagree with the bank."""

    def __init__(self) -> None:
        super().__init__("its bank counts this row in the balance it reports")


class NotOneDecision(ValueError):  # noqa: N818 - it names a refusal, as Concealed does
    """A group that is no one kind of transaction, so no single accept may settle it."""

    def __init__(self, group: str) -> None:
        super().__init__(f"{group!r} is not one kind of transaction; review it row by row")


def one_decision(group: str, waiting: Collection[PileItem]) -> bool:
    """Whether accepting `group` is one decision about one kind of transaction.

    Only when every row waiting in it has the same suggestion, and the group is not one an
    import made of what it could not place. A group of guesses, of mixed suggestions, or with
    a row nothing was suggested for, is reviewed row by row, however the accept is asked for.
    """
    if group.strip().casefold() in UNCERTAIN_GROUPS:
        return False
    suggestions = {item.proposed for item in waiting}
    return len(suggestions) == 1 and None not in suggestions


@dataclass(frozen=True, slots=True)
class Acceptance:
    """What an accept recorded, and how many of the rows it was shown it left as they are."""

    ruling: Ruling | None
    accepted: int
    changed: int
    """Rows shown with a proposal they no longer have, or no longer waiting for this member."""


type Likeness = Callable[[PileItem], str]
"""What makes two rows alike enough that the answer for one is a proposal for the other: the
same account, the same direction, and the same description, as ``hearth.review.where`` keys
them."""


def resolve(
    pile: Sequence[PileItem],
    accounts: Sequence[Account],
    rulings: RulingStore,
    may_review: MayReview,
) -> dict[str, Decision]:
    """Every row's decision, for the books to lay over the ledger. Not for display to anyone."""
    owners = _owners(accounts)
    applicable: defaultdict[str, list[Covered]] = defaultdict(list)
    by_id = {item.review_id: item for item in pile}
    for covered in rulings.covering(by_id):
        if may_review(covered.ruling.member_id, owners(by_id[covered.review_id])):
            applicable[covered.review_id].append(covered)
    return {item.review_id: decide(item, applicable[item.review_id]) for item in pile}


def _owners(accounts: Sequence[Account]) -> Callable[[PileItem], frozenset[MemberId]]:
    by_ledger_account = {account.ledger_account: account.owners for account in accounts}
    return lambda item: by_ledger_account.get(item.account, frozenset())


def _account(accounts: Sequence[Account]) -> Callable[[PileItem], Account | None]:
    by_ledger_account = {account.ledger_account: account for account in accounts}
    return lambda item: by_ledger_account.get(item.account)


class ReviewDesk:
    def __init__(
        self,
        member: MemberId,
        *,
        pile: Sequence[PileItem],
        accounts: Sequence[Account],
        rulings: RulingStore,
        may_review: MayReview,
        opened: Opened,
        may_move_to: MayMoveTo,
        clock: Clock,
        banks: Collection[str] = (),
        moves: Moves = _nothing_moves,
    ) -> None:
        self._member = member
        self._banks = frozenset(banks)
        self._moves = moves
        # An account a member named opens on every row's day (``hearth.ownbooks``), so it is
        # open here from the moment it is named, before the books are next recomputed.
        self._movable = {
            account.ledger_account: (
                (date.min, None) if account.named else opened[account.ledger_account]
            )
            for account in accounts
            if account.ledger_account.split(":")[0] in HELD
            and (account.named is not None or account.ledger_account in opened)
            and may_move_to(account.owners)
        }
        self._owners = _owners(accounts)
        self._accounts = list(accounts)
        self._rulings = rulings
        self._may_review = may_review
        self._clock = clock
        self._mine = {
            item.review_id: item for item in pile if may_review(member, self._owners(item))
        }

    def items(self) -> list[ItemView]:
        """Every row on the member's pile, decided or not, in a stable order."""
        views = self._views(self._mine.values())
        return sorted(views, key=lambda v: (v.item.group, v.item.date, v.item.review_id))

    def groups(self) -> list[GroupView]:
        """The member's rows by group, groups and rows in a stable order."""
        by_group: defaultdict[str, list[ItemView]] = defaultdict(list)
        for view in self.items():
            by_group[view.item.group].append(view)
        return [GroupView(name, tuple(rows)) for name, rows in sorted(by_group.items())]

    def item(self, review_id: str) -> ItemView:
        (view,) = self._views([self._row(review_id)])
        return view

    def last_reviewed(self) -> datetime | None:
        """When the member last reviewed, or `None` if they never have; an undo does not count."""
        return self._rulings.latest_by(self._member)

    def accept(self, review_id: str, shown: str, note: str | None = None) -> Acceptance:
        """Accept one row's proposal, if it is still waiting and still proposes `shown`."""
        item = self._row(review_id)
        return self._accept(Scope.ROW, None, [item], {review_id: shown}, note)

    def accept_group(
        self, group: str, shown: Mapping[str, str], note: str | None = None
    ) -> Acceptance:
        """Accept every row of `group` in `shown`, each bound to the account it was shown with.

        Rows of the group that were not shown are left alone, and so is a row that is no longer
        waiting or now proposes something else: it is counted as changed and stays as it is.
        """
        rows = [item for item in self._mine.values() if item.group == group]
        if not rows:
            raise Concealed
        waiting = [view.item for view in self._views(rows) if view.unreviewed]
        if not one_decision(group, waiting):
            raise NotOneDecision(group)
        return self._accept(Scope.GROUP, group, rows, shown, note)

    def ruling(self, ruling_id: int) -> tuple[Ruling, int] | None:
        """One of the member's own rulings and how many rows of their pile it covers, for
        saying what an undo would take back; `None` for anyone else's, or none."""
        found = self._rulings.get(ruling_id)
        if found is None or found[0].member_id != self._member:
            return None
        ruling, covered = found
        return ruling, sum(1 for review_id in covered if review_id in self._mine)

    def undo(self, ruling_id: int) -> Ruling:
        """Take back one of the member's own rulings: the rows it covered on their pile are
        unreviewed again. Anyone else's ruling, or none, is concealed like a missing one."""
        found = self._rulings.get(ruling_id)
        if found is None:
            raise Concealed
        ruling, covered = found
        rows = [review_id for review_id in covered if review_id in self._mine]
        if ruling.member_id != self._member or ruling.action is Action.REOPEN or not rows:
            raise Concealed
        return self._record(
            ruling.scope, ruling.group, Action.REOPEN, None, None, dict.fromkeys(rows)
        )

    def set_account(self, review_id: str, account: str, note: str | None = None) -> Ruling:
        """Review one row against `account`, whatever its group was given: a category, or one
        of the member's own accounts the books hold open on the row's day (`NotTheirs`
        otherwise) and whose balance no bank reports (`BanksAccount` otherwise)."""
        item = self._row(review_id)
        self._check_target(account, [item])
        return self._record(Scope.ROW, None, Action.ACCOUNT, account, note, {review_id: None})

    def move_group(
        self, group: str, account: str, shown: Mapping[str, str], note: str | None = None
    ) -> Acceptance:
        """Review every row in `shown` against `account`, as one decision called `group`: the
        rows a page showed together, each with what it said then (``PileItem.says``).

        The account is checked as `set_account` checks it, for every row. A row that is no
        longer waiting, or says something else now, is left as it is and counted as changed,
        so the decision covers exactly the rows the member saw as they saw them. A row that is
        not on the member's desk at all is concealed, like one that does not exist.
        """
        rows = [self._row(review_id) for review_id in shown]
        if not rows:
            raise Concealed
        self._check_target(account, rows)
        waiting = {v.item.review_id for v in self._views(rows) if v.unreviewed}
        bound = {
            item.review_id: None
            for item in rows
            if item.review_id in waiting and shown[item.review_id] == item.says
        }
        changed = len(rows) - len(bound)
        if not bound:
            return Acceptance(None, 0, changed)
        ruling = self._record(Scope.GROUP, group, Action.ACCOUNT, account, note, bound)
        return Acceptance(ruling, len(bound), changed)

    def book_shown(self, review_id: str, says: str, account: str) -> Ruling | None:
        """`set_account`, bound to what the row said when it was shown (``PileItem.says``):
        `None`, leaving the row as it is, once it is no longer waiting or says something else
        now, so a decision about a row as it was is never recorded about it as it is."""
        view = self.item(review_id)
        if not view.unreviewed or view.item.says != says:
            return None
        return self.set_account(review_id, account)

    def answer_shown(self, review_id: str, says: str, account: str) -> Ruling | None:
        """`book_shown` for a row waiting to be told where its money went, answered alone
        (``hearth.review.where``): `None` once it is not one of those, or says something else."""
        view = self.item(review_id)
        if not view.one_sided or view.item.says != says:
            return None
        return self.set_account(review_id, account)

    def reject_shown(self, review_id: str, says: str) -> Ruling | None:
        """`reject`, bound to what the row said when it was shown, as `book_shown` is."""
        view = self.item(review_id)
        if not view.unreviewed or view.item.says != says:
            return None
        return self.reject(review_id)

    def movable(self) -> list[Account]:
        """The member's own accounts a row may be moved onto: open in the books, or named, and
        not one whose balance its bank reports."""
        return [
            account
            for account in self._accounts
            if account.ledger_account in self._movable and account.ledger_account not in self._banks
        ]

    def own(self, account_id: str, *, unseen: bool = False) -> Account | None:
        """One of `movable`, by its id, or with `unseen` only one the member named, which hearth
        does not see; `None` for anything else, whoever's it is."""
        return next(
            (
                account
                for account in self.movable()
                if account.id == account_id and (account.named is not None or not unseen)
            ),
            None,
        )

    def learned(self, alike: Likeness) -> dict[str, Learned]:
        """For each row waiting to be told where its money went, what the rows like it on the
        member's desk (`alike`) were said to have gone into, when that is one of the member's
        own accounts hearth does not see: the account most of them went to, and how many did.

        Only an account the member may move a row to counts, so a proposal never names an
        account that is not theirs: a co-owner's answer on a joint account's row onto their
        own private account proposes nothing to the other owner.
        """
        views = self._views(self._mine.values())
        targets = {a.ledger_account: a for a in self.movable() if a.named is not None}
        said: defaultdict[str, Counter[str]] = defaultdict(Counter)
        for view in views:
            decision = view.decision
            if decision.state is State.REVIEWED and decision.account in targets:
                said[alike(view.item)][decision.account] += 1
        found: dict[str, Learned] = {}
        for view in views:
            if not view.one_sided or (counts := said.get(alike(view.item))) is None:
                continue
            account, like = counts.most_common(1)[0]
            found[view.item.review_id] = Learned(targets[account], like)
        return found

    def _check_target(self, account: str, rows: Sequence[PileItem]) -> None:
        check_account(account)
        if account.split(":")[0] in FREE:
            return
        opens, closes = self._movable.get(account, (date.max, None))
        if not all(opens <= item.date <= (closes or date.max) for item in rows):
            raise NotTheirs
        if account in self._banks:
            raise BanksAccount

    def reject(self, review_id: str, note: str | None = None) -> Ruling:
        """One row is not a transaction: book nothing for it; never a row its bank counts in
        the balance it reports (`BanksRow`)."""
        if self._row(review_id).account in self._banks:
            raise BanksRow
        return self._record(Scope.ROW, None, Action.REJECT, None, note, {review_id: None})

    def _accept(
        self,
        scope: Scope,
        group: str | None,
        rows: Sequence[PileItem],
        shown: Mapping[str, str],
        note: str | None,
    ) -> Acceptance:
        waiting = {
            view.item.review_id: view.item.proposed
            for view in self._views(rows)
            if view.unreviewed and view.item.proposed is not None
        }
        bound = {rid: account for rid, account in shown.items() if waiting.get(rid) == account}
        changed = len(set(shown) - set(bound))
        if not bound:
            return Acceptance(None, 0, changed)
        ruling = self._record(scope, group, Action.ACCEPT, None, note, dict(bound))
        return Acceptance(ruling, len(bound), changed)

    def _row(self, review_id: str) -> PileItem:
        item = self._mine.get(review_id)
        if item is None:
            raise Concealed
        return item

    def _record(
        self,
        scope: Scope,
        group: str | None,
        action: Action,
        account: str | None,
        note: str | None,
        rows: Mapping[str, str | None],
    ) -> Ruling:
        return self._rulings.record(
            member_id=self._member,
            scope=scope,
            group=group,
            action=action,
            account=account,
            note=note,
            rows=rows,
            says={review_id: self._mine[review_id].says for review_id in rows},
            at=self._clock(),
        )

    def _views(self, items: Iterable[PileItem]) -> list[ItemView]:
        rows = list(items)
        decisions = resolve(rows, self._accounts, self._rulings, self._may_review)
        account = _account(self._accounts)
        return [
            ItemView(
                item,
                self._owners(item),
                decisions[item.review_id],
                account(item),
                bank_counts=item.account in self._banks,
                moved=Moved.CARD if item.transfer else self._moves(item.group),
            )
            for item in rows
        ]
