"""The sharing rule itself, case by case."""

import pytest

from hearth import access
from hearth.members import Member, MemberId
from hearth.sharing import DEFAULT_SHARED, Facet

AVERY, BLAKE, CASEY = MemberId(1), MemberId(2), MemberId(3)


def member(member_id: MemberId, *, is_admin: bool = False) -> Member:
    return Member(member_id, f"m{member_id}", f"M{member_id}", is_admin, False)


EVERYTHING = frozenset(Facet)


def test_new_members_share_their_two_totals_and_nothing_more() -> None:
    assert frozenset({Facet.NET_WORTH_TOTAL, Facet.MONTHLY_SPENDING_TOTAL}) == DEFAULT_SHARED


@pytest.mark.parametrize("facet", list(Facet))
def test_an_owner_always_sees_their_own(facet: Facet) -> None:
    nothing_shared = {AVERY: frozenset[Facet]()}

    assert access.may_see(member(AVERY), facet, {AVERY}, nothing_shared)


@pytest.mark.parametrize("facet", list(Facet))
def test_every_owner_of_a_joint_account_sees_it(facet: Facet) -> None:
    nothing_shared = {AVERY: frozenset[Facet](), BLAKE: frozenset[Facet]()}

    assert access.may_see(member(AVERY), facet, {AVERY, BLAKE}, nothing_shared)
    assert access.may_see(member(BLAKE), facet, {AVERY, BLAKE}, nothing_shared)


def test_anyone_else_sees_what_the_owner_shares_and_nothing_else() -> None:
    choices = {BLAKE: DEFAULT_SHARED}

    for facet in Facet:
        assert access.may_see(member(CASEY), facet, {BLAKE}, choices) == (facet in DEFAULT_SHARED)


def test_one_joint_owner_cannot_share_on_the_others_behalf() -> None:
    joint = {AVERY, BLAKE}

    assert not access.may_see(
        member(CASEY), Facet.TRANSACTIONS, joint, {AVERY: EVERYTHING, BLAKE: DEFAULT_SHARED}
    )
    assert access.may_see(
        member(CASEY), Facet.TRANSACTIONS, joint, {AVERY: EVERYTHING, BLAKE: EVERYTHING}
    )


def test_being_an_admin_grants_no_view_of_anyone_elses_money() -> None:
    admin = member(AVERY, is_admin=True)

    assert not access.may_see(admin, Facet.TRANSACTIONS, {BLAKE}, {BLAKE: DEFAULT_SHARED})


def test_what_nobody_owns_nobody_sees() -> None:
    assert not access.may_see(member(AVERY), Facet.NET_WORTH_TOTAL, set(), {})


def test_a_member_with_no_recorded_choices_shares_nothing() -> None:
    assert not access.may_see(member(CASEY), Facet.NET_WORTH_TOTAL, {BLAKE}, {})
