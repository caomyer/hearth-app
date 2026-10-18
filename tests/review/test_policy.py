"""The review desk as pages will reach it: through disclosure, deciding by access.may_review."""

import pytest

from hearth import access
from hearth.disclosure import Concealed, Disclosure, review_decisions
from hearth.ledger import InMemoryLedger
from hearth.members import Member, MemberId
from hearth.review.desk import State
from hearth.review.pile import PileItem
from hearth.review.rulings import RulingStore
from hearth.sharing import Facet
from tests.conftest import FakeClock
from tests.review.conftest import ACCOUNTS, ALEX, BLAIR

EVERYTHING = frozenset(Facet)


def member(member_id: MemberId, *, admin: bool = False) -> Member:
    return Member(member_id, f"m{member_id}", f"Member {member_id}", admin, False)


def disclosure_for(viewer: Member) -> Disclosure:
    return Disclosure(
        viewer,
        household=[member(ALEX), member(BLAIR)],
        accounts=ACCOUNTS,
        # Everyone shares everything: reviewing must still be for owners only.
        choices={ALEX: EVERYTHING, BLAIR: EVERYTHING},
        ledger=InMemoryLedger({}, {}),
    )


def test_only_an_owner_may_review() -> None:
    assert access.may_review(ALEX, {ALEX})
    assert access.may_review(ALEX, {ALEX, BLAIR})
    assert not access.may_review(ALEX, {BLAIR})
    assert not access.may_review(ALEX, set())


def test_sharing_everything_does_not_open_another_members_pile(
    pile: list[PileItem], rulings: RulingStore, clock: FakeClock
) -> None:
    desk = disclosure_for(member(ALEX)).review_desk(pile, rulings, clock)

    seen = {view.item.review_id for group in desk.groups() for view in group.items}
    assert seen == {"pay0000001", "pay0000002", "p2p0000001", "p2p0000002"}
    with pytest.raises(Concealed):
        desk.reject("p2p0000003")


def test_being_an_admin_opens_no_pile(
    pile: list[PileItem], rulings: RulingStore, clock: FakeClock
) -> None:
    admin = member(MemberId(3), admin=True)

    assert disclosure_for(admin).review_desk(pile, rulings, clock).groups() == []


def test_the_import_counts_a_ruling_only_while_its_author_may_review(
    pile: list[PileItem], rulings: RulingStore, clock: FakeClock
) -> None:
    disclosure_for(member(ALEX)).review_desk(pile, rulings, clock).accept_group("payroll")

    decisions = review_decisions(pile, ACCOUNTS, rulings)

    assert decisions["pay0000001"].state is State.BOOKED
    assert decisions["p2p0000003"].state is State.PENDING
