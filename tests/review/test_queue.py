"""The queue at the size of a first import: ordered, filtered and counted honestly."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from hearth.accounts import Account
from hearth.review.decision import PROPOSAL_CHANGED, UNRULED, Decision, State
from hearth.review.desk import ItemView
from hearth.review.pile import PileItem
from hearth.review.queue import (
    PAGE_SIZE,
    Confidence,
    Filter,
    Order,
    Tally,
    category_label,
    confidence,
    queue,
)
from tests.review.conftest import ALEX

CHECKING = Account("a1", "Assets:Alex:Checking", "Alex checking", frozenset({ALEX}))
CARD = Account("a2", "Liabilities:Alex:Card", "Alex card", frozenset({ALEX}))
REVIEWED = Decision(State.REVIEWED, "Expenses:Food", None, None)


def row(
    n: int,
    group: str,
    amount: str = "-10",
    proposed: str | None = "Expenses:Food",
    score: str | None = "0.9",
    account: Account = CHECKING,
    decision: Decision = UNRULED,
    transfer: bool = False,
) -> ItemView:
    item = PileItem(
        review_id=f"r{n:04d}",
        group=group,
        date=date(2026, 1, 1) + timedelta(days=n % 300),
        account=account.ledger_account,
        amount=Decimal(amount),
        currency="USD",
        payee=f"Payee {n}",
        narration="",
        proposed=proposed,
        confidence=Decimal(score) if score else None,
        source=None,
        location="?",
        transfer=transfer,
    )
    return ItemView(item, account.owners, decision, account)


def first_import() -> list[ItemView]:
    """A thousand rows, most of them two kinds of transaction, as a real backfill has."""
    rows = [row(n, "payroll", "2500", "Income:Salary", None) for n in range(300)]
    rows += [row(n, "p2p", "-40", "Expenses:Gifts", "0.7") for n in range(300, 700)]
    rows += [row(n, f"merchant-{n % 30}", "-25", "Expenses:Food", "0.95") for n in range(700, 1100)]
    rows += [
        row(1100, "unsure", "-900", None, None),
        row(1101, "unsure", "-5", "Expenses:X", "0.2"),
    ]
    rows += [
        row(n, "payroll", "2500", "Income:Salary", None, decision=REVIEWED)
        for n in range(2000, 2010)
    ]
    return rows


def test_grouping_collapses_the_bulk_into_a_few_decisions() -> None:
    found = queue(first_import(), Order.SIZE, Filter())

    assert [g.name for g in found.groups[:2]] == ["p2p", "payroll"]
    assert (found.groups[0].size, found.groups[1].size) == (400, 300)
    assert found.group_count == 33


def test_progress_counts_what_is_left_and_the_money_it_moves() -> None:
    found = queue(first_import(), Order.SIZE, Filter())

    assert (found.reviewed, found.remaining.rows, found.total) == (10, 1102, 1112)
    assert found.remaining.moved == 300 * 2500 + 400 * 40 + 400 * 25 + 900 + 5


def test_lowest_confidence_first_puts_nothing_suggested_then_the_least_sure_first() -> None:
    rows = queue(first_import(), Order.CONFIDENCE, Filter()).rows

    assert [v.item.review_id for v in rows[:2]] == ["r1100", "r1101"]
    assert confidence(rows[0]) is Confidence.NONE
    assert confidence(rows[1]) is Confidence.LOW


def test_largest_amount_first_counts_money_either_way() -> None:
    rows = queue(first_import(), Order.AMOUNT, Filter()).rows

    assert rows[0].item.amount == Decimal(2500)
    assert rows[300].item.review_id == "r1100"


def test_a_filter_says_what_it_leaves_and_the_progress_does_not_move() -> None:
    found = queue(first_import(), Order.SIZE, Filter(category="-"))

    assert (found.shown.rows, found.shown.moved) == (1, Decimal(900))
    assert found.remaining.rows == 1102


def test_an_account_filter_matches_only_the_members_own_accounts() -> None:
    rows = [row(1, "a"), row(2, "a", account=CARD)]

    assert [v.item.review_id for v in queue(rows, Order.SIZE, Filter(account="a2")).rows] == [
        "r0002"
    ]
    assert queue(rows, Order.SIZE, Filter(account="someone-elses")).rows == ()


def test_pages_cover_every_row_once() -> None:
    found = queue(first_import(), Order.DATE, Filter())
    first, last = found.page(1), found.page(10_000)

    assert len(first.rows) == PAGE_SIZE
    assert last.number == last.pages == -(-1102 // PAGE_SIZE)
    seen = [v for n in range(1, last.pages + 1) for v in found.page(n).rows]
    assert len({v.item.review_id for v in seen}) == 1102


def test_a_row_whose_suggestion_changed_since_it_was_accepted_is_counted_and_first() -> None:
    changed = Decision(State.UNREVIEWED, None, None, PROPOSAL_CHANGED)
    rows = [row(1, "a", score="0.1"), replace(row(2, "a"), decision=changed)]

    found = queue(rows, Order.CONFIDENCE, Filter())

    assert found.changed == 1
    assert found.rows[0].item.review_id == "r0002"


def test_a_search_finds_rows_by_merchant_or_description_in_any_case() -> None:
    rows = first_import()
    described = replace(rows[0], item=replace(rows[0].item, payee=None, narration="Kite STRING"))
    found = queue([*rows, described], Order.SIZE, Filter(text="kite string"))
    assert [v.item.review_id for v in found.rows] == [described.item.review_id]
    assert [v.item.review_id for v in queue(rows, Order.SIZE, Filter(text="PAYEE 1100")).rows] == [
        "r1100"
    ]
    assert found.filter.active
    assert found.remaining.rows == len(rows) - 10 + 1  # the search narrows what is shown only


def test_a_row_whose_other_side_hearth_does_not_see_is_counted_apart_from_what_is_left() -> None:
    """One side of a card payment hearth could not match is asked where its money went, on a
    page of its own, so it is never in what is left, nor in any group or page of the queue,
    and always in its own count, which no filter narrows."""
    legs = [row(n, "Card payment", "300", None, None, transfer=True) for n in range(5000, 5003)]
    rows = first_import()
    found = queue([*rows, *legs], Order.SIZE, Filter())
    assert found.remaining == queue(rows, Order.SIZE, Filter()).remaining
    assert found.total == len(rows)
    assert {v.item.review_id for v in found.rows}.isdisjoint(v.item.review_id for v in legs)
    assert "Card payment" not in {g.name for g in found.groups}
    assert found.one_sided == Tally(3, Decimal(900))

    narrowed = queue([*rows, *legs], Order.SIZE, Filter(text="payee 5001"))
    assert narrowed.one_sided == Tally(3, Decimal(900))


def test_review_names_a_category_by_its_most_specific_part_so_kinds_of_income_read_apart() -> None:
    assert category_label("Income:Wages") == "Wages"
    assert category_label("Income:InterestEarned") == "Interest earned"
    assert category_label("Expenses:BankFees:ATMFees") == "ATM fees"
    assert category_label(None) == "Nothing suggested"
