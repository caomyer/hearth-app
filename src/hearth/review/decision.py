"""What the rulings on a row make of it: still unreviewed, reviewed against an account, or
rejected.

Precedence, as beancount_nextgen's: a ruling on the row itself wins over a ruling on its group,
and among rulings of the same kind the later one wins. An accept is bound to the proposal its
author saw: a row whose proposal has changed since is unreviewed again until someone looks.
A row ruling that leaves the row unreviewed, a reopen or an accept of a since-changed proposal,
wins only over earlier group rulings: a later group ruling decides the row.

Every ruling is bound to what its row said when it was made: which account, which day, how
much (``hearth.review.ids.says``). A bank revises a transaction it sent, and a member's decision
about the row as it was is not a decision about the row as it is: booking a charge against
Groceries says nothing about the same charge at three times the amount, and "this is not a
transaction" says nothing about one that has since moved a month. Keeping the ruling would book
the revised row as checked when nobody checked it, and dropping it would lose the decision
without a word, so neither is done: the ruling stops deciding the row, which is unreviewed again
and says why, and the member decides it afresh. The ruling stays on record, so the history of
what was decided is kept.

This module knows nothing of who may rule, so the books can apply decisions without reaching
the access policy; ``hearth.review.desk`` and ``hearth.disclosure`` decide whose rulings count.
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from enum import StrEnum

from hearth.review.pile import PileItem
from hearth.review.rulings import Action, Covered, Ruling, Scope


class State(StrEnum):
    UNREVIEWED = "unreviewed"
    """In the books at its proposed account, flagged `!`: nobody has checked it."""
    REVIEWED = "reviewed"
    """In the books at the account a member chose or accepted, flagged `*`."""
    REJECTED = "rejected"
    """Not a transaction: a member ruled that it books nothing."""


@dataclass(frozen=True, slots=True)
class Decision:
    state: State
    account: str | None
    """The account the row is reviewed against; `None` unless it is reviewed."""
    ruling: Ruling | None
    reason: str | None
    """Why an unreviewed row is unreviewed, in a sentence."""


UNRULED = Decision(State.UNREVIEWED, None, None, "nobody has ruled on it")
PROPOSAL_CHANGED = "the import's proposal has changed since it was accepted"
REOPENED = "a decision on it was taken back"
REVISED = "its bank has changed it since it was reviewed"


KEPT_OFF = "it was moved onto an account whose balance its bank decides now"
"""Why a row a ruling would move onto a carried account is unreviewed (`guarded`)."""


def guarded(item: PileItem, decision: Decision, carried: Collection[str]) -> Decision:
    """`decision`, unless it would put on an account carried on from the imported base a
    posting its bank never sent, or take one of its bank's rows away: then the row is
    unreviewed, at its proposal, and says why.

    A carried account's balance is asserted against exactly the rows its bank sent and its
    difference with the base, so no ruling may move another row onto it or reject one of its
    own. Review refuses both as they are asked for; this keeps it so for every ruling on
    record however it comes to decide, as one superseded that decides again once its bank
    revises the row, so no recompute fails because of carrying (``docs/books.md``)."""
    moved = decision.account in carried and decision.account != item.account
    if decision.state is State.REVIEWED and moved:
        return Decision(State.UNREVIEWED, None, decision.ruling, KEPT_OFF)
    if decision.state is State.REJECTED and item.account in carried:
        return Decision(State.UNREVIEWED, None, decision.ruling, KEPT_OFF)
    return decision


def decide(item: PileItem, rulings: Iterable[Covered], formerly: Iterable[str] = ()) -> Decision:
    """The decision `rulings` (oldest first, all covering the row) make for `item`.

    `formerly` are the accounts the row's account was called before hearth carried it on from
    the base (``RulingStore.formerly``): a ruling made on the row as it was booked there is
    bound to the same row of the same bank account, so it holds as long as nothing else it
    said has changed."""
    said = item.said(formerly)
    latest: dict[Scope, Covered] = {}
    for covered in rulings:
        latest[covered.ruling.scope] = covered
    row, group = latest.get(Scope.ROW), latest.get(Scope.GROUP)
    if row is not None:
        decision = _apply(item, row, said)
        if (
            decision.state is not State.UNREVIEWED
            or group is None
            or group.ruling.id < row.ruling.id
        ):
            return decision
    if group is None:
        return UNRULED
    return _apply(item, group, said)


def _apply(item: PileItem, covered: Covered, said: frozenset[str]) -> Decision:
    ruling, accepted = covered.ruling, covered.accepted
    if ruling.action is Action.REOPEN:
        return Decision(State.UNREVIEWED, None, ruling, REOPENED)
    if covered.says is not None and covered.says not in said:
        return Decision(State.UNREVIEWED, None, ruling, REVISED)
    if ruling.action is Action.REJECT:
        return Decision(State.REJECTED, None, ruling, None)
    if ruling.action is Action.ACCOUNT:
        return Decision(State.REVIEWED, ruling.account, ruling, None)
    if item.proposed != accepted:
        return Decision(State.UNREVIEWED, None, ruling, PROPOSAL_CHANGED)
    return Decision(State.REVIEWED, accepted, ruling, None)
