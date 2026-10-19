"""Two ledger accounts a word apart are named before anyone confirms a mapping between them, and
a ledger account a member types for a new account is one the books can take."""

import pytest

from hearth.plaid.mapping import (
    MAX_LENGTH,
    Problem,
    check_new,
    differ_by_one_word,
    near_pairs,
    proposed,
    unused,
)


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


@pytest.mark.parametrize(
    ("taken", "expected"),
    [
        ({"Assets:Jordan:Lakeside"}, "Assets:Jordan:LakesideChecking"),
        ({"Assets:Jordan"}, "Assets:JordanLakesideChecking"),
        ({"Assets:Jordan", "Assets:JordanLakesideChecking"}, "Assets:JordanLakesideChecking6606"),
        ({"Assets:Jordan:Lakeside:Checking:Sub"}, "Assets:Jordan:Lakeside:Checking6606"),
    ],
)
def test_a_proposal_never_sits_inside_or_above_an_account_already_taken(
    taken: set[str], expected: str
) -> None:
    proposal = unused("Assets:Jordan:Lakeside:Checking", taken, "6606")

    assert proposal == expected
    assert check_new(proposal, credit=False, existing=taken) is None


BOOKS = {"Assets:Bank:Checking", "Liabilities:Bank:Card"}


@pytest.mark.parametrize(
    ("path", "credit"),
    [
        ("Assets:Bank:Savings", False),
        ("Assets:Bank:Checking2", False),
        ("Assets:2024:Deposit-Account", False),
        ("Liabilities:Bank:Travel", True),
        ("Liabilities:B", True),
    ],
)
def test_a_new_name_the_books_can_take_is_accepted(path: str, credit: bool) -> None:
    assert check_new(path, credit=credit, existing=BOOKS) is None


@pytest.mark.parametrize(
    ("path", "credit", "problem"),
    [
        ("", False, Problem.MISSING),
        ("Liabilities:Bank:Savings", False, Problem.UNDER_ASSETS),
        ("Expenses:Bank:Savings", False, Problem.UNDER_ASSETS),
        ("assets:bank:savings", False, Problem.UNDER_ASSETS),
        ("Assets:Bank:Travel", True, Problem.UNDER_LIABILITIES),
        ("Assets", False, Problem.MALFORMED),
        ("Assets:", False, Problem.MALFORMED),
        ("Assets::Savings", False, Problem.MALFORMED),
        ("Assets:Bank:savings", False, Problem.MALFORMED),
        ("Assets:Bank:Rainy Day", False, Problem.MALFORMED),
        ("Assets:Bank:Épargne", False, Problem.MALFORMED),
        # A Cyrillic capital Es, which reads as the C of "Card".
        ("Assets:Bank:\u0421ard", False, Problem.MALFORMED),
        ("Assets:Bank:Savings:", False, Problem.MALFORMED),
        ("Assets:" + "A" * MAX_LENGTH, False, Problem.MALFORMED),
        ("Assets:Bank:Checking", False, Problem.IN_USE),
        ("Liabilities:Bank:Card", True, Problem.IN_USE),
        ("Assets:Bank:Checking:Sub", False, Problem.IN_USE),
        ("Assets:Bank", False, Problem.IN_USE),
        ("Liabilities:Bank", True, Problem.IN_USE),
    ],
)
def test_a_new_name_the_books_cannot_take_says_why(
    path: str, credit: bool, problem: Problem
) -> None:
    assert check_new(path, credit=credit, existing=BOOKS) is problem


def test_a_name_sharing_only_a_prefix_of_letters_is_not_nested() -> None:
    assert check_new("Assets:Bank:CheckingTwo", credit=False, existing=BOOKS) is None
    assert check_new("Assets:Ban", credit=False, existing=BOOKS) is None
