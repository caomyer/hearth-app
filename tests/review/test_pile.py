from datetime import date
from decimal import Decimal

import pytest
from beancount import loader

from hearth.review.pile import PileItem, read_pile


def test_each_row_carries_its_proposal_confidence_source_and_group(
    pile: list[PileItem],
) -> None:
    p2p = next(i for i in pile if i.review_id == "p2p0000001")

    assert (p2p.group, p2p.date, p2p.account) == ("p2p", date(2026, 1, 20), "Assets:Alex:Checking")
    assert (p2p.amount, p2p.currency) == (Decimal("-40.00"), "USD")
    assert p2p.narration == "P2P TRANSFER"
    assert p2p.proposed == "Expenses:Gifts"
    assert p2p.confidence == Decimal("0.412")
    assert p2p.source == "statement-a:6"
    assert p2p.location.startswith("<string>:")


def test_a_row_with_nothing_proposed_has_no_proposal(pile: list[PileItem]) -> None:
    unknown = next(i for i in pile if i.group == "unknown-type")

    assert unknown.proposed is None
    assert unknown.confidence is None
    assert not unknown.transfer


def test_one_side_of_a_transfer_waiting_for_the_other_has_no_proposal() -> None:
    entries, _, _ = loader.load_string(
        "2026-01-01 open Assets:Bank\n2026-01-01 open Equity:Review:TransferPending\n"
        '2026-01-02 ! "Payment to card ending in 1111"\n  review-id: "x000000001"\n'
        '  review-group: "card-payment-unmatched"\n'
        "  Assets:Bank  -50.00 USD\n  Equity:Review:TransferPending\n"
    )

    [leg] = read_pile(entries)

    # Accepting it as proposed must not settle it: only naming the other account does.
    assert leg.proposed is None
    # And it is not a guess: what it waits on is its other side.
    assert leg.transfer


def test_booked_entries_are_not_on_the_pile() -> None:
    entries, _, _ = loader.load_string(
        "2026-01-01 open Assets:Bank\n2026-01-01 open Expenses:Food\n"
        '2026-01-02 * "Lunch"\n  review-id: "x000000001"\n  review-group: "g"\n'
        "  Assets:Bank  -5.00 USD\n  Expenses:Food\n"
    )

    assert read_pile(entries) == []


@pytest.mark.parametrize(
    ("meta", "postings", "complaint"),
    [
        ('  review-group: "g"\n', 2, "no review-id"),
        ('  review-id: "x000000001"\n', 2, "no review-group"),
        ('  review-id: "x000000001"\n  review-group: "g"\n', 3, "exactly two postings"),
    ],
)
def test_a_row_that_cannot_be_ruled_on_durably_is_refused(
    meta: str, postings: int, complaint: str
) -> None:
    legs = ["  Assets:Bank  -5.00 USD", "  Expenses:Food  3.00 USD", "  Expenses:Fees  2.00 USD"]
    if postings == 2:
        legs = ["  Assets:Bank  -5.00 USD", "  Expenses:Food  5.00 USD"]
    text = '2026-01-02 ! "Lunch"\n' + meta + "\n".join(legs) + "\n"
    entries, _, _ = loader.load_string(text)

    with pytest.raises(ValueError, match=complaint):
        read_pile(entries)


def test_a_review_id_twice_is_refused() -> None:
    row = '2026-01-02 ! "Lunch"\n  review-id: "x000000001"\n  review-group: "g"\n'
    row += "  Assets:Bank  -5.00 USD\n  Expenses:Food  5.00 USD\n"
    entries, _, _ = loader.load_string(row + "\n" + row)

    with pytest.raises(ValueError, match="appears twice"):
        read_pile(entries)
