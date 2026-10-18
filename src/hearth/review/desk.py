"""One member's review desk: their part of the pile, grouped, and the rulings they make on it.

Who may review a row is not decided here. The desk is handed the household's access policy as
`may_review(member, owners)` and asks it about every row, for the member at the desk and for
the author of every ruling, so a ruling made by someone who has since stopped owning an account
no longer decides that account's rows. A row on an account nobody owns is reviewed by nobody.

A row the member may not review behaves exactly like a row that does not exist: both raise
`Concealed`, so the desk never reveals what is on anyone else's pile, not even a count.

Precedence, as beancount_nextgen's: a ruling on the row itself wins over a ruling on its group,
and among rulings of the same kind the later one wins. Accepting a group binds each row to the
account it proposed then: a row the import had nothing to propose for is not covered, and a row
whose proposal has changed since goes back to pending until someone looks at it again.
"""

from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from hearth.accounts import Account
from hearth.clock import Clock
from hearth.disclosure import Concealed
from hearth.members import MemberId
from hearth.review.pile import PileItem
from hearth.review.rulings import Action, Ruling, RulingStore, Scope, check_account

type MayReview = Callable[[MemberId, Collection[MemberId]], bool]
"""The access policy's answer to: may this member review rows on an account these own?"""


class State(StrEnum):
    PENDING = "pending"
    BOOKED = "booked"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class Decision:
    """What happens to a row: still waiting, booked against `account`, or rejected."""

    state: State
    account: str | None
    ruling: Ruling | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class ItemView:
    item: PileItem
    owners: frozenset[MemberId]
    decision: Decision


@dataclass(frozen=True, slots=True)
class GroupView:
    name: str
    items: tuple[ItemView, ...]

    @property
    def pending(self) -> int:
        return sum(1 for view in self.items if view.decision.state is State.PENDING)


UNRULED = Decision(State.PENDING, None, None, "nobody has ruled on it")


def decide(item: PileItem, rulings: Iterable[tuple[Ruling, str | None]]) -> Decision:
    """The decision `rulings` (oldest first, all applicable to the row) make for `item`.

    Each ruling comes with the account the row was accepted as, for an accept.
    """
    latest: dict[Scope, tuple[Ruling, str | None]] = {}
    for ruling, accepted in rulings:
        latest[ruling.scope] = (ruling, accepted)
    deciding = latest.get(Scope.ROW) or latest.get(Scope.GROUP)
    if deciding is None:
        return UNRULED
    return _apply(item, *deciding)


def _apply(item: PileItem, ruling: Ruling, accepted: str | None) -> Decision:
    if ruling.action is Action.REJECT:
        return Decision(State.REJECTED, None, ruling, None)
    if ruling.action is Action.ACCOUNT:
        return Decision(State.BOOKED, ruling.account, ruling, None)
    if item.proposed != accepted:
        reason = "the import's proposal has changed since it was accepted"
        return Decision(State.PENDING, None, ruling, reason)
    return Decision(State.BOOKED, accepted, ruling, None)


def resolve(
    pile: Sequence[PileItem],
    accounts: Sequence[Account],
    rulings: RulingStore,
    may_review: MayReview,
) -> dict[str, Decision]:
    """Every row's decision, for the import that books them. Not for display to anyone."""
    owners = _owners(accounts)
    applicable: defaultdict[str, list[tuple[Ruling, str | None]]] = defaultdict(list)
    by_id = {item.review_id: item for item in pile}
    for review_id, ruling, accepted in rulings.covering(by_id):
        if may_review(ruling.member_id, owners(by_id[review_id])):
            applicable[review_id].append((ruling, accepted))
    return {item.review_id: decide(item, applicable[item.review_id]) for item in pile}


def _owners(accounts: Sequence[Account]) -> Callable[[PileItem], frozenset[MemberId]]:
    by_ledger_account = {account.ledger_account: account.owners for account in accounts}
    return lambda item: by_ledger_account.get(item.account, frozenset())


class ReviewDesk:
    def __init__(
        self,
        member: MemberId,
        *,
        pile: Sequence[PileItem],
        accounts: Sequence[Account],
        rulings: RulingStore,
        may_review: MayReview,
        clock: Clock,
    ) -> None:
        self._member = member
        self._owners = _owners(accounts)
        self._accounts = list(accounts)
        self._rulings = rulings
        self._may_review = may_review
        self._clock = clock
        self._mine = {
            item.review_id: item for item in pile if may_review(member, self._owners(item))
        }

    def groups(self) -> list[GroupView]:
        """The member's rows by group, groups and rows in a stable order."""
        views = self._views(self._mine.values())
        by_group: defaultdict[str, list[ItemView]] = defaultdict(list)
        for view in views:
            by_group[view.item.group].append(view)
        return [
            GroupView(name, tuple(sorted(rows, key=lambda v: (v.item.date, v.item.review_id))))
            for name, rows in sorted(by_group.items())
        ]

    def item(self, review_id: str) -> ItemView:
        (view,) = self._views([self._row(review_id)])
        return view

    def accept_group(self, group: str, note: str | None = None) -> Ruling:
        """Accept the proposal for every row of `group` on the member's pile, as it is now.

        A row the import proposed nothing for is left out, and if that is every row, nothing
        is recorded: there is nothing to accept.
        """
        rows = [item for item in self._mine.values() if item.group == group]
        if not rows:
            raise Concealed
        proposed = {item.review_id: item.proposed for item in rows if item.proposed is not None}
        if not proposed:
            raise ValueError(f"the import proposed no account for any row of {group!r}")
        return self._record(Scope.GROUP, group, Action.ACCEPT, None, note, proposed)

    def set_account(self, review_id: str, account: str, note: str | None = None) -> Ruling:
        """Book one row against `account`, whatever its group was given."""
        self._row(review_id)
        check_account(account)
        return self._record(Scope.ROW, None, Action.ACCOUNT, account, note, {review_id: None})

    def reject(self, review_id: str, note: str | None = None) -> Ruling:
        """One row is not a transaction: book nothing for it."""
        self._row(review_id)
        return self._record(Scope.ROW, None, Action.REJECT, None, note, {review_id: None})

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
            at=self._clock(),
        )

    def _views(self, items: Iterable[PileItem]) -> list[ItemView]:
        rows = list(items)
        decisions = resolve(rows, self._accounts, self._rulings, self._may_review)
        return [ItemView(item, self._owners(item), decisions[item.review_id]) for item in rows]
