from datetime import timedelta

import pytest
from beancount import loader

from hearth.accounts import Account
from hearth.disclosure import Concealed
from hearth.members import MemberId
from hearth.review.desk import GroupView, State, resolve
from hearth.review.pile import PileItem, read_pile
from hearth.review.rulings import Action, RulingStore, Scope
from tests.conftest import FakeClock
from tests.review.conftest import ACCOUNTS, ALEX, BLAIR, PILE, DeskFactory, owners_only

PILE_P2P3_UNRESOLVED = PILE.replace(
    "  Liabilities:Blair:Card  -15.00 USD\n  Expenses:Gifts",
    "  Liabilities:Blair:Card  -15.00 USD\n  Equity:Review:Unresolved",
)


def reimport(text: str) -> list[PileItem]:
    entries, errors, _ = loader.load_string(text)
    assert errors == []
    return read_pile(entries)


def ids(groups: list[GroupView]) -> dict[str, list[str]]:
    return {group.name: [view.item.review_id for view in group.items] for group in groups}


def state(desk_for: DeskFactory, member: MemberId, review_id: str) -> State:
    return desk_for(member).item(review_id).decision.state


# What each member sees


def test_a_member_sees_only_rows_on_accounts_they_own(desk_for: DeskFactory) -> None:
    assert ids(desk_for(ALEX).groups()) == {
        "p2p": ["p2p0000001", "p2p0000002"],
        "payroll": ["pay0000001", "pay0000002"],
    }
    assert ids(desk_for(BLAIR).groups()) == {
        "p2p": ["p2p0000002", "p2p0000003"],
        "unknown-type": ["unk0000001"],
    }


def test_rows_on_an_account_nobody_owns_are_on_nobodys_pile(desk_for: DeskFactory) -> None:
    for member in (ALEX, BLAIR):
        seen = {v.item.review_id for g in desk_for(member).groups() for v in g.items}
        assert "orp0000001" not in seen


def test_another_members_row_is_indistinguishable_from_no_row(desk_for: DeskFactory) -> None:
    alex = desk_for(ALEX)

    for review_id in ("unk0000001", "p2p0000003", "orp0000001", "nosuchrow0"):
        with pytest.raises(Concealed):
            alex.item(review_id)
        with pytest.raises(Concealed):
            alex.reject(review_id)
        with pytest.raises(Concealed):
            alex.set_account(review_id, "Expenses:Gifts")


def test_a_group_with_none_of_the_members_rows_is_indistinguishable_from_no_group(
    desk_for: DeskFactory, rulings: RulingStore
) -> None:
    for group in ("unknown-type", "no-such-group"):
        with pytest.raises(Concealed):
            desk_for(ALEX).accept_group(group)

    assert list(rulings.covering({"unk0000001"})) == []


# Rulings


def test_accepting_a_group_books_each_row_as_proposed(desk_for: DeskFactory) -> None:
    ruling = desk_for(ALEX).accept_group("payroll", note="salary, every month")

    payroll = {g.name: g for g in desk_for(ALEX).groups()}["payroll"]
    assert payroll.pending == 0
    for view in payroll.items:
        assert view.decision.state is State.BOOKED
        assert view.decision.account == "Income:Salary"
        assert view.decision.ruling == ruling
    assert (ruling.scope, ruling.action, ruling.member_id) == (Scope.GROUP, Action.ACCEPT, ALEX)
    assert ruling.note == "salary, every month"


def test_a_group_ruling_covers_only_the_rows_on_its_authors_pile(desk_for: DeskFactory) -> None:
    desk_for(ALEX).accept_group("p2p")

    assert state(desk_for, ALEX, "p2p0000001") is State.BOOKED
    assert state(desk_for, BLAIR, "p2p0000002") is State.BOOKED  # joint: Alex owns it too
    assert state(desk_for, BLAIR, "p2p0000003") is State.PENDING  # Blair's alone


def test_accepting_a_group_with_nothing_proposed_records_nothing(
    desk_for: DeskFactory, rulings: RulingStore
) -> None:
    with pytest.raises(ValueError, match="proposed no account for any row"):
        desk_for(BLAIR).accept_group("unknown-type")

    assert list(rulings.covering({"unk0000001"})) == []
    assert desk_for(BLAIR).item("unk0000001").decision.state is State.PENDING


def test_accepting_a_group_leaves_out_a_row_with_nothing_proposed(
    desk_for: DeskFactory, rulings: RulingStore
) -> None:
    unproposed = reimport(PILE_P2P3_UNRESOLVED)
    desk_for(BLAIR, unproposed).accept_group("p2p")

    assert [review_id for review_id, _, _ in rulings.covering({"p2p0000003"})] == []
    assert desk_for(BLAIR, unproposed).item("p2p0000003").decision.state is State.PENDING


def test_one_row_can_be_booked_elsewhere_or_rejected(desk_for: DeskFactory) -> None:
    desk = desk_for(BLAIR)
    desk.set_account("unk0000001", "Expenses:Shopping")
    desk.reject("p2p0000003", note="a refund of a transfer, not spending")

    booked = desk_for(BLAIR).item("unk0000001").decision
    rejected = desk_for(BLAIR).item("p2p0000003").decision
    assert (booked.state, booked.account) == (State.BOOKED, "Expenses:Shopping")
    assert (rejected.state, rejected.account) == (State.REJECTED, None)


def test_a_ruling_on_the_row_wins_over_its_group_whichever_came_first(
    desk_for: DeskFactory, clock: FakeClock
) -> None:
    desk_for(ALEX).set_account("p2p0000001", "Expenses:Shopping")
    clock.advance(timedelta(minutes=1))
    desk_for(ALEX).accept_group("p2p")

    decision = desk_for(ALEX).item("p2p0000001").decision
    assert (decision.state, decision.account) == (State.BOOKED, "Expenses:Shopping")
    assert state(desk_for, ALEX, "p2p0000002") is State.BOOKED


def test_a_later_ruling_supersedes_an_earlier_one(desk_for: DeskFactory) -> None:
    desk = desk_for(ALEX)
    desk.reject("p2p0000001")
    assert state(desk_for, ALEX, "p2p0000001") is State.REJECTED

    desk.set_account("p2p0000001", "Expenses:Shopping")
    assert state(desk_for, ALEX, "p2p0000001") is State.BOOKED

    desk.reject("p2p0000001")
    assert state(desk_for, ALEX, "p2p0000001") is State.REJECTED


def test_either_owner_of_a_joint_account_can_rule_and_the_later_one_holds(
    desk_for: DeskFactory,
) -> None:
    desk_for(ALEX).reject("p2p0000002")
    desk_for(BLAIR).set_account("p2p0000002", "Expenses:Shopping")

    for member in (ALEX, BLAIR):
        decision = desk_for(member).item("p2p0000002").decision
        assert decision.account == "Expenses:Shopping"
        assert decision.ruling is not None
        assert decision.ruling.member_id == BLAIR


@pytest.mark.parametrize(
    ("account", "complaint"),
    [("expenses:gifts", "not a beancount account"), ("Equity:Review:Unresolved", "waits")],
)
def test_a_row_must_be_booked_against_a_real_account(
    desk_for: DeskFactory, rulings: RulingStore, account: str, complaint: str
) -> None:
    with pytest.raises(ValueError, match=complaint):
        desk_for(ALEX).set_account("p2p0000001", account)

    assert list(rulings.covering({"p2p0000001"})) == []


# Durability


def test_an_accept_does_not_follow_a_proposal_that_changed_after_it(
    desk_for: DeskFactory,
) -> None:
    desk_for(ALEX).accept_group("payroll")

    recategorised = reimport(PILE.replace("  Income:Salary", "  Expenses:Shopping", 1))

    desk = desk_for(ALEX, recategorised)
    decision = desk.item("pay0000001").decision
    assert (decision.state, decision.account) == (State.PENDING, None)
    assert decision.reason == "the import's proposal has changed since it was accepted"
    assert desk.item("pay0000002").decision.account == "Income:Salary"


def test_an_accept_never_books_a_row_that_gained_a_proposal_after_it(
    desk_for: DeskFactory, rulings: RulingStore, pile: list[PileItem]
) -> None:
    desk_for(BLAIR, reimport(PILE_P2P3_UNRESOLVED)).accept_group("p2p")

    decisions = resolve(pile, ACCOUNTS, rulings, owners_only)
    assert (decisions["p2p0000003"].state, decisions["p2p0000003"].ruling) == (State.PENDING, None)
    assert decisions["p2p0000002"].state is State.BOOKED


def test_rulings_follow_rows_through_a_redownload_that_reorders_and_adds_rows(
    desk_for: DeskFactory,
) -> None:
    desk_for(ALEX).accept_group("payroll")
    desk_for(ALEX).set_account("p2p0000001", "Expenses:Shopping")

    later = PILE.replace(
        '2026-01-15 ! "PAYROLL DEPOSIT"',
        '2026-02-15 ! "PAYROLL DEPOSIT"\n  review-id: "pay0000003"\n  review-group: "payroll"\n'
        "  Assets:Alex:Checking  2500.00 USD\n  Income:Salary\n\n"
        '2026-01-15 ! "PAYROLL DEPOSIT"',
    )
    entries, errors, _ = loader.load_string(later)
    assert errors == []
    redownloaded = list(reversed(read_pile(entries)))

    desk = desk_for(ALEX, redownloaded)
    assert desk.item("pay0000001").decision.state is State.BOOKED
    assert desk.item("pay0000002").decision.state is State.BOOKED
    assert desk.item("p2p0000001").decision.account == "Expenses:Shopping"
    # The group ruling covered what Alex saw; a row that arrived later waits to be seen.
    assert desk.item("pay0000003").decision.state is State.PENDING


def test_rulings_survive_a_new_store_on_the_same_database(
    desk_for: DeskFactory, rulings: RulingStore, pile: list[PileItem]
) -> None:
    desk_for(ALEX).reject("p2p0000001")

    reopened = RulingStore(rulings._db)
    decisions = resolve(pile, ACCOUNTS, reopened, owners_only)

    assert decisions["p2p0000001"].state is State.REJECTED


def test_a_row_nobody_rules_on_stays_pending_however_long_it_waits(
    desk_for: DeskFactory, clock: FakeClock
) -> None:
    clock.advance(timedelta(days=3650))

    assert all(
        view.decision.state is State.PENDING
        for group in desk_for(ALEX).groups()
        for view in group.items
    )


# The import's view


def test_resolve_decides_every_row_for_the_import(
    desk_for: DeskFactory, rulings: RulingStore, pile: list[PileItem]
) -> None:
    desk_for(ALEX).accept_group("payroll")

    decisions = resolve(pile, ACCOUNTS, rulings, owners_only)

    assert set(decisions) == {item.review_id for item in pile}
    assert decisions["pay0000001"].state is State.BOOKED
    assert decisions["orp0000001"].state is State.PENDING


def test_a_ruling_stops_counting_once_its_author_no_longer_owns_the_account(
    desk_for: DeskFactory, rulings: RulingStore, pile: list[PileItem]
) -> None:
    desk_for(ALEX).reject("p2p0000002")
    blair_alone = [
        Account(a.id, a.ledger_account, a.name, frozenset({BLAIR})) if a.id == "j1" else a
        for a in ACCOUNTS
    ]

    decisions = resolve(pile, blair_alone, rulings, owners_only)

    assert decisions["p2p0000002"].state is State.PENDING
