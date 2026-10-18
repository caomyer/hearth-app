"""The ledger as reviewed: every row stays in it, and rulings decide only flag and account."""

from datetime import UTC, datetime

from beancount import loader
from beancount.core.amount import Amount
from beancount.core.data import Transaction
from beancount.core.number import D

from hearth.review.desk import resolve
from hearth.review.overlay import reviewed
from hearth.review.pile import PileItem, read_pile
from hearth.review.rulings import Action, RulingStore, Scope
from tests.review.conftest import ACCOUNTS, ALEX, BLAIR, PILE, DeskFactory, owners_only


def by_id(pile: list[PileItem], rulings: RulingStore) -> dict[str, Transaction]:
    entries, errors, _ = loader.load_string(PILE)
    assert errors == []
    decisions = resolve(read_pile(entries), ACCOUNTS, rulings, owners_only)
    return {
        e.meta["review-id"]: e for e in reviewed(entries, decisions) if isinstance(e, Transaction)
    }


def test_an_unreviewed_row_is_in_the_books_as_imported(
    pile: list[PileItem], rulings: RulingStore
) -> None:
    entry = by_id(pile, rulings)["p2p0000001"]

    assert entry.flag == "!"
    assert [p.account for p in entry.postings] == ["Assets:Alex:Checking", "Expenses:Gifts"]


def test_a_reviewed_row_is_flagged_checked_against_the_account_it_was_reviewed_as(
    desk_for: DeskFactory, pile: list[PileItem], rulings: RulingStore
) -> None:
    desk_for(ALEX).accept("pay0000001", "Income:Salary")
    desk_for(BLAIR).set_account("unk0000001", "Expenses:Shopping")

    entries = by_id(pile, rulings)

    assert entries["pay0000001"].flag == "*"
    assert entries["pay0000001"].postings[1].account == "Income:Salary"
    assert entries["unk0000001"].flag == "*"
    assert [p.account for p in entries["unk0000001"].postings] == [
        "Liabilities:Blair:Card",
        "Expenses:Shopping",
    ]
    bank, booked = entries["unk0000001"].postings
    assert (bank.units, booked.units) == (-Amount(D("9.99"), "USD"), Amount(D("9.99"), "USD"))


def test_a_rejected_row_books_nothing(
    desk_for: DeskFactory, pile: list[PileItem], rulings: RulingStore
) -> None:
    desk_for(ALEX).reject("p2p0000001")

    assert "p2p0000001" not in by_id(pile, rulings)


def test_a_ruling_by_someone_who_may_not_review_the_row_changes_nothing(
    desk_for: DeskFactory, pile: list[PileItem], rulings: RulingStore
) -> None:
    # Recorded straight into the store, as no desk would let Alex rule on Blair's card.
    rulings.record(
        member_id=ALEX,
        scope=Scope.ROW,
        group=None,
        action=Action.REJECT,
        account=None,
        note=None,
        rows={"p2p0000003": None},
        at=datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert by_id(pile, rulings)["p2p0000003"].flag == "!"
