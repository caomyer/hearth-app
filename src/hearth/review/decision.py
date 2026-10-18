"""What the rulings on a row make of it: still unreviewed, reviewed against an account, or
rejected.

Precedence, as beancount_nextgen's: a ruling on the row itself wins over a ruling on its group,
and among rulings of the same kind the later one wins. An accept is bound to the proposal its
author saw: a row whose proposal has changed since is unreviewed again until someone looks.
A row ruling that leaves the row unreviewed, a reopen or an accept of a since-changed proposal,
wins only over earlier group rulings: a later group ruling decides the row.

This module knows nothing of who may rule, so the books can apply decisions without reaching
the access policy; ``hearth.review.desk`` and ``hearth.disclosure`` decide whose rulings count.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from hearth.review.pile import PileItem
from hearth.review.rulings import Action, Ruling, Scope


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


def decide(item: PileItem, rulings: Iterable[tuple[Ruling, str | None]]) -> Decision:
    """The decision `rulings` (oldest first, all applicable to the row) make for `item`.

    Each ruling comes with the account the row was accepted as, for an accept.
    """
    latest: dict[Scope, tuple[Ruling, str | None]] = {}
    for ruling, accepted in rulings:
        latest[ruling.scope] = (ruling, accepted)
    row, group = latest.get(Scope.ROW), latest.get(Scope.GROUP)
    if row is not None:
        decision = _apply(item, *row)
        if decision.state is not State.UNREVIEWED or group is None or group[0].id < row[0].id:
            return decision
    if group is None:
        return UNRULED
    return _apply(item, *group)


def _apply(item: PileItem, ruling: Ruling, accepted: str | None) -> Decision:
    if ruling.action is Action.REOPEN:
        return Decision(State.UNREVIEWED, None, ruling, REOPENED)
    if ruling.action is Action.REJECT:
        return Decision(State.REJECTED, None, ruling, None)
    if ruling.action is Action.ACCOUNT:
        return Decision(State.REVIEWED, ruling.account, ruling, None)
    if item.proposed != accepted:
        return Decision(State.UNREVIEWED, None, ruling, PROPOSAL_CHANGED)
    return Decision(State.REVIEWED, accepted, ruling, None)
