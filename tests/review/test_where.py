"""Money hearth sees one side of: grouped one answer each, answered a group at a time onto an
account a member named, bound to what the page showed, and proposed again for rows like them."""

from datetime import UTC, datetime

import pytest
from beancount import loader

from hearth.accounts import Account, Kind, Named
from hearth.disclosure import Concealed
from hearth.members import MemberId
from hearth.plaid.booking import moved
from hearth.review.desk import NotTheirs, ReviewDesk, State
from hearth.review.pile import PileItem, read_pile
from hearth.review.rulings import Action, RulingStore, Scope
from hearth.review.where import CARD_GROUP, Question, alike, mask, where
from tests.conftest import FakeClock
from tests.review.conftest import ALEX, BLAIR, owners_only

NAMED_AT = datetime(2026, 1, 2, tzinfo=UTC)

CHECKING = Account("a1", "Assets:Alex:Checking", "Alex checking", frozenset({ALEX}))
CARD = Account("a2", "Liabilities:Alex:Card", "Alex card", frozenset({ALEX}))
STORE_CARD = Account("a3", "Liabilities:Alex:StoreCard", "Alex store card", frozenset({ALEX}))
JOINT = Account("j1", "Assets:Joint:Checking", "Joint checking", frozenset({ALEX, BLAIR}))
SAVINGS = Account(
    "n1",
    "Assets:Alex:Fernbrook:Savings",
    "Fernbrook savings",
    frozenset({ALEX}),
    Named(Kind.BANK, "Fernbrook Credit Union", "0417", NAMED_AT),
)
LOAN = Account(
    "n2",
    "Liabilities:Alex:CarLoan",
    "Car loan",
    frozenset({ALEX}),
    Named(Kind.LOAN, None, None, NAMED_AT),
)
BLAIRS = Account(
    "n3",
    "Assets:Blair:Thornbury:Savings",
    "Thornbury savings",
    frozenset({BLAIR}),
    Named(Kind.BANK, "Thornbury", None, NAMED_AT),
)
ACCOUNTS = [CHECKING, CARD, STORE_CARD, JOINT, SAVINGS, LOAN, BLAIRS]

OPENS = """
2026-01-01 open Assets:Alex:Checking
2026-01-01 open Liabilities:Alex:Card
2026-01-01 open Liabilities:Alex:StoreCard
2026-01-01 open Assets:Joint:Checking
2026-01-01 open Equity:Review:Unresolved
2026-01-01 open Equity:Review:TransferPending
"""


def row(review_id: str, day: str, account: str, amount: str, words: str, group: str) -> str:
    against = "Equity:Review:TransferPending" if group == CARD_GROUP else "Equity:Review:Unresolved"
    return f"""
{day} ! "{words}"
  review-id: "{review_id}"
  review-group: "{group}"
  {account}  {amount} USD
  {against}
"""


OUT = "Transfer out: account transfer"
IN = "Transfer in: account transfer"
CAR = "Loan payments: car payment"

ROWS = [
    row("sav0000001", "2026-01-05", "Assets:Alex:Checking", "-500.00",
        "Online Transfer to SAV ...0417 transaction#: 1111111 01/05", OUT),
    row("sav0000002", "2026-02-05", "Assets:Alex:Checking", "-500.00",
        "Online Transfer to SAV ...0417 transaction#: 2222222 02/05", OUT),
    row("sav0000003", "2026-03-05", "Assets:Alex:Checking", "-250.00",
        "Online Transfer to SAV ...0417 transaction#: 3333333 03/05", OUT),
    row("oth0000001", "2026-01-09", "Assets:Alex:Checking", "-75.00",
        "Online Transfer to CHK ...9921 transaction#: 4444444 01/09", OUT),
    row("zel0000001", "2026-01-12", "Assets:Alex:Checking", "40.00", "Zelle from a friend", IN),
    row("zel0000002", "2026-02-12", "Assets:Alex:Checking", "60.00", "Zelle from a friend", IN),
    row("car0000001", "2026-01-20", "Assets:Alex:Checking", "-385.00", "Ashgrove Auto Loan", CAR),
    row("leg0000001", "2026-01-25", "Liabilities:Alex:Card", "900.00",
        "AUTOMATIC PAYMENT - THANK YOU", CARD_GROUP),
    row("leg0000002", "2026-02-25", "Liabilities:Alex:Card", "700.00",
        "AUTOMATIC PAYMENT - THANK YOU", CARD_GROUP),
    row("jnt0000001", "2026-01-30", "Assets:Joint:Checking", "-200.00",
        "Online Transfer to SAV ...0417 transaction#: 5555555 01/30", OUT),
]  # fmt: skip


def pile(*extra: str) -> list[PileItem]:
    entries, errors, _ = loader.load_string(OPENS + "".join(ROWS) + "".join(extra))
    assert errors == []
    return read_pile(entries)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 4, 1, 9, 0, tzinfo=UTC))


def desk(
    member: MemberId, rulings: RulingStore, clock: FakeClock, items: list[PileItem] | None = None
) -> ReviewDesk:
    return ReviewDesk(
        member,
        pile=pile() if items is None else items,
        accounts=ACCOUNTS,
        rulings=rulings,
        may_review=owners_only,
        # The books open none of the named accounts: one is open from the moment it is named.
        opened={a: (datetime(2026, 1, 1).date(), None) for a in (
            CHECKING.ledger_account, CARD.ledger_account, STORE_CARD.ledger_account,
            JOINT.ledger_account,
        )},
        may_move_to=lambda owners: member in owners,
        clock=clock,
        moves=moved,
    )  # fmt: skip


def says(review_id: str) -> str:
    """What a row of the pile says, as a page sends it back."""
    return next(item.says for item in pile() if item.review_id == review_id)


def titles(d: ReviewDesk) -> dict[str, list[str]]:
    page = where(d.items(), d.learned(alike))
    found: dict[str, list[str]] = {}
    for g in page.groups:
        found.setdefault(g.title, []).extend(v.item.review_id for v in g.rows)
    return found


# Grouping


def test_rows_group_by_where_the_banks_words_say_the_money_went(
    rulings: RulingStore, clock: FakeClock
) -> None:
    page = where(desk(ALEX, rulings, clock).items(), {})

    found = {g.title: g for g in page.groups if JOINT not in g.accounts}
    assert [v.item.review_id for v in found["Online Transfer to SAV ...0417"].rows] == [
        "sav0000003",
        "sav0000002",
        "sav0000001",
    ]
    assert found["Online Transfer to SAV ...0417"].mask == "0417"
    assert found["Online Transfer to SAV ...0417"].question is Question.WENT
    assert [v.item.review_id for v in found["Online Transfer to CHK ...9921"].rows] == [
        "oth0000001"
    ]
    assert found["Zelle from a friend"].question is Question.CAME
    assert found["Ashgrove Auto Loan"].question is Question.LOAN
    assert found[CARD_GROUP].question is Question.PAID_FROM
    assert len(found[CARD_GROUP].rows) == 2
    # The same words on the joint account are another answer, the joint account's owners'.
    (joint,) = [g for g in page.groups if JOINT in g.accounts]
    assert [v.item.review_id for v in joint.rows] == ["jnt0000001"]
    assert page.rows == 10


STORE_LEG = row(
    "leg0000003",
    "2026-02-27",
    "Liabilities:Alex:StoreCard",
    "120.00",
    "PAYMENT RECEIVED",
    CARD_GROUP,
)


def test_each_card_asks_where_its_own_payments_came_from(
    rulings: RulingStore, clock: FakeClock
) -> None:
    """Two cards paid from accounts hearth does not see may have been paid from two different
    ones, so one answer never settles both."""
    page = where(desk(ALEX, rulings, clock, pile(STORE_LEG)).items(), {})

    cards = [g for g in page.groups if g.title == CARD_GROUP]
    assert {g.accounts: [v.item.review_id for v in g.rows] for g in cards} == {
        (CARD,): ["leg0000002", "leg0000001"],
        (STORE_CARD,): ["leg0000003"],
    }
    assert all(g.question is Question.PAID_FROM for g in cards)


def test_a_member_is_asked_only_about_rows_on_accounts_they_review(
    rulings: RulingStore, clock: FakeClock
) -> None:
    seen = {
        v.item.review_id
        for g in where(desk(BLAIR, rulings, clock).items(), {}).groups
        for v in g.rows
    }

    assert seen == {"jnt0000001"}


def test_the_last_digits_a_bank_names_are_read_in_its_own_words() -> None:
    assert mask("Online Transfer to CHK ...1234 transaction#: 9876543 10/02") == "1234"
    assert mask("Online Realtime Transfer to Fernbrook CU 5678 conf# 12345") == "5678"
    assert mask("Payment to card ending in 4414") == "4414"
    assert mask("ACH x0417 deposit") == "0417"
    assert mask("Zelle from a friend") is None
    assert mask("Ashgrove Auto Loan") is None


# Answering a group


def test_one_answer_moves_every_row_the_page_showed_onto_the_named_account(
    rulings: RulingStore, clock: FakeClock
) -> None:
    alex = desk(ALEX, rulings, clock)
    group = next(
        g for g in where(alex.items(), {}).groups if g.accounts == (CHECKING,) and g.mask == "0417"
    )

    moved_rows = alex.move_group(group.title, SAVINGS.ledger_account, group.shown())

    assert (moved_rows.accepted, moved_rows.changed) == (3, 0)
    assert moved_rows.ruling is not None
    assert (moved_rows.ruling.scope, moved_rows.ruling.action) == (Scope.GROUP, Action.ACCOUNT)
    for review_id in ("sav0000001", "sav0000002", "sav0000003"):
        decision = desk(ALEX, rulings, clock).item(review_id).decision
        assert (decision.state, decision.account) == (State.REVIEWED, SAVINGS.ledger_account)
    assert titles(desk(ALEX, rulings, clock))["Online Transfer to SAV ...0417"] == ["jnt0000001"]


def test_a_row_that_changed_since_the_page_showed_it_is_left_waiting(
    rulings: RulingStore, clock: FakeClock
) -> None:
    alex = desk(ALEX, rulings, clock)
    group = next(
        g for g in where(alex.items(), {}).groups if g.accounts == (CHECKING,) and g.mask == "0417"
    )
    shown = dict(group.shown())
    shown["sav0000002"] = "something else"

    found = alex.move_group(group.title, SAVINGS.ledger_account, shown)

    assert (found.accepted, found.changed) == (2, 1)
    assert desk(ALEX, rulings, clock).item("sav0000002").unreviewed


def test_one_row_of_a_group_is_answered_on_its_own_and_the_rest_wait(
    rulings: RulingStore, clock: FakeClock
) -> None:
    alex = desk(ALEX, rulings, clock)

    ruling = alex.set_account("sav0000002", "Expenses:Gifts")

    assert (ruling.scope, ruling.action) == (Scope.ROW, Action.ACCOUNT)
    after = desk(ALEX, rulings, clock)
    assert after.item("sav0000002").decision.account == "Expenses:Gifts"
    assert titles(after)["Online Transfer to SAV ...0417"] == [
        "sav0000003",
        "sav0000001",
        "jnt0000001",
    ]
    assert all(after.item(r).one_sided for r in ("sav0000001", "sav0000003"))


def test_undo_takes_back_the_whole_group(rulings: RulingStore, clock: FakeClock) -> None:
    alex = desk(ALEX, rulings, clock)
    group = next(
        g for g in where(alex.items(), {}).groups if g.accounts == (CHECKING,) and g.mask == "0417"
    )
    ruling = alex.move_group(group.title, SAVINGS.ledger_account, group.shown()).ruling
    assert ruling is not None

    alex.undo(ruling.id)

    after = desk(ALEX, rulings, clock)
    assert all(after.item(r).one_sided for r in ("sav0000001", "sav0000002", "sav0000003"))


def test_a_group_answers_onto_a_category_too(rulings: RulingStore, clock: FakeClock) -> None:
    alex = desk(ALEX, rulings, clock)
    group = next(g for g in where(alex.items(), {}).groups if g.title == "Zelle from a friend")

    found = alex.move_group(group.title, "Income:Gifts", group.shown())

    assert found.accepted == 2
    assert desk(ALEX, rulings, clock).item("zel0000001").decision.account == "Income:Gifts"


@pytest.mark.parametrize(
    "target",
    [
        BLAIRS.ledger_account,
        "Assets:Nobody:Savings",
        "Equity:Household:Anything",
        "Equity:Review:Unresolved",
    ],
)
def test_an_account_not_the_members_own_takes_nothing(
    rulings: RulingStore, clock: FakeClock, target: str
) -> None:
    alex = desk(ALEX, rulings, clock)
    group = next(
        g for g in where(alex.items(), {}).groups if g.accounts == (CHECKING,) and g.mask == "0417"
    )

    with pytest.raises((NotTheirs, ValueError)):
        alex.move_group(group.title, target, group.shown())
    assert desk(ALEX, rulings, clock).item("sav0000001").unreviewed


def test_another_members_row_in_a_group_is_concealed_like_a_missing_one(
    rulings: RulingStore, clock: FakeClock
) -> None:
    blair = desk(BLAIR, rulings, clock)
    alexs = {"sav0000001": says("sav0000001")}

    for shown in (alexs, {"nosuchrow0": "x"}):
        with pytest.raises(Concealed):
            blair.move_group("Anything", BLAIRS.ledger_account, shown)


def test_only_the_members_own_named_accounts_are_offered(
    rulings: RulingStore, clock: FakeClock
) -> None:
    offered = {a.id for a in desk(ALEX, rulings, clock).movable() if a.named}

    assert offered == {SAVINGS.id, LOAN.id}
    assert {a.id for a in desk(BLAIR, rulings, clock).movable() if a.named} == {BLAIRS.id}


# Proposals that learn


SAV_APRIL = row(
    "sav0000004",
    "2026-04-01",
    "Assets:Alex:Checking",
    "-500.00",
    "Online Transfer to SAV ...0417 transaction#: 6666666 04/01",
    OUT,
)


def test_a_row_like_ones_moved_onto_a_named_account_is_proposed_onto_it(
    rulings: RulingStore, clock: FakeClock
) -> None:
    alex = desk(ALEX, rulings, clock)
    group = next(
        g for g in where(alex.items(), {}).groups if g.accounts == (CHECKING,) and g.mask == "0417"
    )
    alex.move_group(group.title, SAVINGS.ledger_account, group.shown())

    later = desk(ALEX, rulings, clock, pile(SAV_APRIL))
    proposed = later.learned(alike)

    assert set(proposed) == {"sav0000004"}
    assert (proposed["sav0000004"].account, proposed["sav0000004"].like) == (SAVINGS, 3)
    # A proposal, not a decision: it waits for the member's yes.
    assert later.item("sav0000004").one_sided
    page = where(later.items(), proposed)
    assert (
        next(g for g in page.groups if g.accounts == (CHECKING,) and g.mask == "0417").learned
        == proposed["sav0000004"]
    )


def test_a_proposal_never_names_another_members_account(
    rulings: RulingStore, clock: FakeClock
) -> None:
    """Blair moving a joint transfer onto Blair's own account proposes nothing to Alex, who
    reviews the joint account too but may not see Blair's."""
    blair = desk(BLAIR, rulings, clock)
    moved_rows = blair.move_group(
        "Joint", BLAIRS.ledger_account, {"jnt0000001": says("jnt0000001")}
    )
    assert moved_rows.accepted == 1
    joint_later = row(
        "jnt0000002",
        "2026-03-30",
        "Assets:Joint:Checking",
        "-200.00",
        "Online Transfer to SAV ...0417 transaction#: 7777777 03/30",
        OUT,
    )

    alex = desk(ALEX, rulings, clock, pile(joint_later))

    assert "jnt0000002" not in alex.learned(alike)
    assert "Thornbury" not in str(where(alex.items(), alex.learned(alike)))
