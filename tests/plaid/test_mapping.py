"""Two ledger accounts a word apart are named before anyone confirms a mapping between them."""

import pytest

from hearth.plaid.mapping import differ_by_one_word, near_pairs, proposed, unused


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Assets:Bank:Checking", "Assets:Bank:Joint:Checking"),
        ("Assets:Bank:Checking", "Assets:Bank:JointChecking"),
        ("Assets:Jordan:Lakeside:Checking", "Assets:Jordan:Lakeside:Savings"),
        ("Assets:Joint:Bank:Checking", "Assets:Bank:Checking"),
    ],
)
def test_names_a_word_apart_are_near(a: str, b: str) -> None:
    assert differ_by_one_word(a, b)
    assert differ_by_one_word(b, a)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Assets:Bank:Checking", "Assets:Bank:Checking"),
        ("Assets:Bank:Checking", "Liabilities:Card:Rewards"),
        ("Assets:Alex:Bank:Checking", "Assets:Sam:Other:Checking"),
    ],
)
def test_names_further_apart_are_not(a: str, b: str) -> None:
    assert not differ_by_one_word(a, b)


def test_only_pairs_involving_a_chosen_target_are_named() -> None:
    chosen = ["Assets:Bank:Joint:Checking"]
    others = ["Assets:Bank:Checking", "Assets:Bank:Savings"]

    assert near_pairs(chosen, others) == [("Assets:Bank:Joint:Checking", "Assets:Bank:Checking")]


def test_a_proposal_is_a_valid_new_name_and_never_one_already_taken() -> None:
    card = proposed(
        credit=True,
        member="Jordan Lee",
        institution="Pinecone Card",
        name="Card",
        subtype="credit card",
    )
    assert card == "Liabilities:JordanLee:Pinecone:Card"
    assert unused(card, {card}, "9904") == f"{card}9904"
    assert unused(card, {card, f"{card}9904"}, "9904") == f"{card}99042"
