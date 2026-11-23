"""What a row is suggested as, and why: the merchant rule, the order the suggestion is taken
in, its thresholds, and who it may learn from (``hearth.merchants``, ``hearth.proposals``).

These hold the rules on their own, with every row and ruling written out here; how a household
meets them, through Link, Review and an imported ledger, is in ``tests/test_learning.py``.
Every merchant, category and figure is invented.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from hearth.carrying import BaseEntry
from hearth.disclosure import may_learn
from hearth.members import MemberId
from hearth.merchants import merchant
from hearth.plaid.booking import bank_says
from hearth.proposals import (
    LATEST,
    MAP_AT_LEAST,
    NO_CATEGORY,
    Ledger,
    Proposal,
    Proposer,
    Ruled,
    Target,
    decided_as,
    group_of,
)
from hearth.review.reasons import Reason, Why
from hearth.review.rulings import Action, Covered, Ruling, Scope

AVERY, BLAKE = MemberId(1), MemberId(2)
ALONE, JOINT, HERS = frozenset({AVERY}), frozenset({AVERY, BLAKE}), frozenset({BLAKE})
DAY = date(2026, 10, 1)
ARRIVED = datetime(2026, 10, 2, 12, tzinfo=UTC)
BEFORE = ARRIVED - timedelta(days=1)
AFTER = ARRIVED + timedelta(days=1)


# The same merchant.


MERCHANTS = [
    # Plaid's own merchant name comes first; a description only without one.
    (("Blue Bottle Coffee", "SQ *BLUE BOTTLE 0042", None), "blue bottle coffee"),
    ((None, "SQ *BLUE BOTTLE 0042", None), "blue bottle"),
    (("  ", None, "Zq Fallback Words"), "zq fallback words"),
    # Casing.
    (("BLUE BOTTLE COFFEE",), "blue bottle coffee"),
    (("blue bottle coffee",), "blue bottle coffee"),
    # A store number, and the town after it.
    (("STARBUCKS #1234 SEATTLE WA",), "starbucks"),
    (("STARBUCKS STORE 01234",), "starbucks"),
    (("Shell Oil 57444123 Springfield IL",), "shell oil"),
    (("Target T-1234",), "target"),
    # A payment processor's prefix, with or without a space.
    (("SQ *ZQ BEAN COUNTER",), "zq bean counter"),
    (("TST* Zq Bean Counter",), "zq bean counter"),
    (("PAYPAL *ZQBEANS",), "zqbeans"),
    (("IN *ACME CO",), "acme co"),
    (("DD *DOORDASH SUSHI",), "doordash sushi"),
    # Not a processor: the merchant, and a reference after its asterisk.
    (("AMZN Mktp US*2K4AB1",), "amzn mktp us"),
    # The bank's own words for how money moved, with the dates and numbers among them.
    ((None, "PURCHASE AUTHORIZED ON 09/12 STARBUCKS STORE 0042"), "starbucks"),
    ((None, "PURCHASE AUTHORIZED ON 09/13 SHELL OIL 57444"), "shell oil"),
    ((None, "ACH DEBIT 0012 GEICO"), "geico"),
    ((None, "ACH DEBIT 0013 PG&E"), "pg&e"),
    ((None, "POS DEBIT 1234 TARGET T-1234"), "target"),
    # Only a description is read for them: a merchant's own name may start with one.
    (("Card Factory",), "card factory"),
    # A cheque says no merchant at all, whatever its number.
    ((None, "CHECK 1001"), None),
    ((None, "CHECK 1002"), None),
    # A name is never cut down to a generic word: the first meaningful one is kept, digits
    # and all.
    (("The 1849 Club",), "the 1849 club"),
    (("The 9 Muses",), "the 9 muses"),
    (("A 1 AUTO",), "a 1 auto"),
    (("7-Eleven 33012",), "7 eleven"),
    (("24 Hour Fitness 0099",), "24 hour fitness"),
    # Letters of every script, accents and all.
    (("寿司 BAR",), "寿司 bar"),
    (("拉麺 BAR",), "拉麺 bar"),
    (("CAFÉ",), "café"),
    (("Café",), "café"),
    # Punctuation is a space, but for an apostrophe or ampersand inside a word.
    (("Joe's Pizza",), "joe's pizza"),
    (("H&M 0418",), "h&m"),
    (("Zq. Bean -- Counter!",), "zq bean counter"),
    # Nothing to go on is no merchant, nor only generic words.
    ((None, None, None), None),
    (("", "   "), None),
    (("***",), None),
    (("SQ *",), None),
    ((None, "PURCHASE AUTHORIZED ON 09/12"), None),
    (("The",), None),
    ((None, "ONLINE PAYMENT"), None),
]
"""Each row's words, as `merchant` takes them, and the merchant they are from."""


@pytest.mark.parametrize(("texts", "found"), MERCHANTS)
def test_one_rule_says_which_merchant_a_row_is_from(
    texts: tuple[str | None, ...], found: str | None
) -> None:
    assert merchant(*texts) == found


def test_different_merchants_written_alike_are_never_one_merchant() -> None:
    """The keys of the table are distinct wherever its merchants are, so a wrong merchant can
    never agree with itself five times."""
    pairs = [
        (
            (None, "PURCHASE AUTHORIZED ON 09/12 STARBUCKS"),
            (None, "PURCHASE AUTHORIZED ON 09/12 SHELL"),
        ),
        ((None, "ACH DEBIT 0012 GEICO"), (None, "ACH DEBIT 0012 PG&E")),
        (("The 1849 Club",), ("The 9 Muses",)),
        (("寿司 BAR",), ("拉麺 BAR",)),
        (("A 1 AUTO",), ("A 2 Z TOYS",)),
    ]
    for one, other in pairs:
        assert merchant(*one) != merchant(*other), (one, other)
        assert merchant(*one) is not None


def test_rows_of_one_merchant_written_differently_are_one_merchant() -> None:
    assert (
        merchant("SQ *ZQ BEAN COUNTER #12 PORTLAND OR")
        == merchant("Zq Bean Counter")
        == merchant(None, "ZQ BEAN COUNTER STORE 0042")
    )
    assert merchant("Zq Bean Counter") != merchant("Zq Bean Roaster")


# The order a suggestion is taken in, and why.


def ruling(
    number: int,
    action: Action,
    *,
    account: str | None = None,
    by: MemberId = AVERY,
    at: datetime = BEFORE,
    scope: Scope = Scope.ROW,
) -> Ruling:
    group = "Some group" if scope is Scope.GROUP else None
    return Ruling(number, by, scope, group, action, account, None, at)


def covered(review_id: str, found: Ruling, accepted: str | None = None) -> Covered:
    return Covered(review_id, found, accepted, None)


def booked_as(
    review_id: str,
    number: int,
    account: str,
    *,
    by: MemberId = AVERY,
    at: datetime = BEFORE,
    on: frozenset[MemberId] = ALONE,
    name: str = "zq bean counter",
    day: date = DAY,
) -> Ruled:
    """A row of `name` someone booked against `account`, by a ruling of their own."""
    found = ruling(number, Action.ACCOUNT, account=account, by=by, at=at)
    return Ruled(review_id, name, day, on, (covered(review_id, found),))


def target(**changes: object) -> Target:
    row = Target(
        review_id="target",
        merchant="zq bean counter",
        day=DAY,
        currency="USD",
        owners=ALONE,
        category="FOOD_AND_DRINK_COFFEE",
        default="Expenses:FoodAndDrink:Coffee",
        bank_says="Coffee",
        arrived=ARRIVED,
    )
    return replace(row, **changes)  # type: ignore[arg-type]


def proposer(ruled: Sequence[Ruled] = (), ledger: Ledger | None = None, **kw: object) -> Proposer:
    return Proposer(may_learn, ruled, ledger, **kw)  # type: ignore[arg-type]


def entry(
    number: int,
    category: str | None,
    *,
    payee: str = "Zq Bean Counter",
    account: str = "Assets:Zq:Avery",
    day: date = DAY,
    checked: bool = True,
) -> BaseEntry:
    return BaseEntry(number, day, payee, "", frozenset({account}), category, checked)


OWNERS = {"Assets:Zq:Avery": ALONE, "Assets:Zq:Joint": JOINT, "Assets:Zq:Blake": HERS}


def ledger(entries: Sequence[BaseEntry], held: Sequence[tuple[str, int]] = ()) -> Ledger:
    named = {e.entry: frozenset({m}) for e in entries if (m := merchant(e.payee)) is not None}
    return Ledger(entries, named, held, lambda account: OWNERS.get(account, frozenset()))


def test_with_nothing_learned_the_banks_category_as_hearth_names_it_is_suggested() -> None:
    assert proposer().propose(target()) == Proposal(
        "Expenses:FoodAndDrink:Coffee", Reason(Why.BANK, "Bank says Coffee")
    )


def test_a_members_own_decisions_come_first_and_say_how_often() -> None:
    ruled = [booked_as("a", 1, "Expenses:Zq:Beans"), booked_as("b", 2, "Expenses:Zq:Beans")]

    assert proposer(ruled).propose(target()) == Proposal(
        "Expenses:Zq:Beans", Reason(Why.REMEMBERED, "Beans twice before")
    )
    assert proposer(ruled[:1]).propose(target()).reason == Reason(
        Why.REMEMBERED, "Beans once before"
    )
    many = [booked_as(str(n), n, "Expenses:Zq:Beans") for n in range(1, 8)]
    assert proposer(many).propose(target()).reason == Reason(Why.REMEMBERED, "Beans 7 times before")


def test_an_accept_teaches_what_was_accepted_and_a_group_accept_teaches_each_row() -> None:
    accept = ruling(4, Action.ACCEPT, scope=Scope.GROUP)
    ruled = [
        Ruled(rid, "zq bean counter", DAY, ALONE, (covered(rid, accept, "Expenses:Zq:Beans"),))
        for rid in ("a", "b", "c")
    ]

    assert proposer(ruled).propose(target()) == Proposal(
        "Expenses:Zq:Beans", Reason(Why.REMEMBERED, "Beans 3 times before")
    )


def test_decisions_teach_only_when_the_latest_of_them_all_agree() -> None:
    bulk = [
        booked_as(str(n), 1, "Expenses:Zq:Beans", day=DAY - timedelta(days=n)) for n in range(9)
    ]
    redecided = booked_as("odd", 2, "Expenses:Zq:Gifts")

    disagreeing = proposer([*bulk, redecided]).propose(target())

    # The member booked one elsewhere since: hearth asks again, at the bank's category.
    assert disagreeing == Proposal(
        "Expenses:FoodAndDrink:Coffee", Reason(Why.BANK, "Bank says Coffee")
    )
    # Once the latest LATEST agree again, they teach again, counting every one.
    since = [booked_as(f"s{n}", 10 + n, "Expenses:Zq:Beans") for n in range(LATEST)]
    assert proposer([*bulk, redecided, *since]).propose(target()) == Proposal(
        "Expenses:Zq:Beans", Reason(Why.REMEMBERED, f"Beans {9 + LATEST} times before")
    )
    # With nothing else to suggest, a row whose merchant was decided both ways says so.
    plain = target(default=None, bank_says=None, category=None)
    assert proposer([*bulk, redecided]).propose(plain) == Proposal(
        None, Reason(Why.MIXED, "Booked more than one way before")
    )


def test_a_decision_made_after_the_row_arrived_teaches_it_nothing() -> None:
    later = [booked_as("a", 1, "Expenses:Zq:Beans", at=AFTER)]
    exactly = [booked_as("a", 1, "Expenses:Zq:Beans", at=ARRIVED)]

    assert proposer(later).propose(target()).reason == Reason(Why.BANK, "Bank says Coffee")
    assert proposer(exactly).propose(target()).reason == Reason(Why.BANK, "Bank says Coffee")
    # A row whose arrival is not known learns from no ruling at all.
    early = [booked_as("a", 1, "Expenses:Zq:Beans")]
    assert proposer(early).propose(target(arrived=None)).reason == Reason(
        Why.BANK, "Bank says Coffee"
    )


def test_a_ruling_made_later_never_changes_what_the_earlier_one_taught() -> None:
    """The row's own decision is still the one before it arrived, though a later ruling on the
    same earlier row booked it elsewhere and another took that back."""
    rows = (
        covered("a", ruling(1, Action.ACCOUNT, account="Expenses:Zq:Beans")),
        covered("a", ruling(5, Action.ACCOUNT, account="Expenses:Zq:Gifts", at=AFTER)),
        covered("a", ruling(6, Action.REOPEN, at=AFTER)),
    )
    found = proposer([Ruled("a", "zq bean counter", DAY, ALONE, rows)]).propose(target())

    assert found == Proposal("Expenses:Zq:Beans", Reason(Why.REMEMBERED, "Beans once before"))


def test_what_decides_a_row_is_what_decides_it_in_review() -> None:
    beans, gifts = "Expenses:Zq:Beans", "Expenses:Zq:Gifts"
    group = ruling(1, Action.ACCEPT, scope=Scope.GROUP)
    row = ruling(2, Action.ACCOUNT, account=gifts)
    reopen = ruling(3, Action.REOPEN)
    later_group = ruling(4, Action.ACCOUNT, account=beans, scope=Scope.GROUP)

    assert decided_as([covered("x", group, beans)]) == (1, beans)
    assert decided_as([covered("x", group, beans), covered("x", row)]) == (2, gifts)
    # A row ruling wins over an earlier group's, a reopen of it only over earlier ones.
    assert decided_as([covered("x", row), covered("x", group, beans)]) == (2, gifts)
    assert decided_as([covered("x", group, beans), covered("x", reopen)]) is None
    assert decided_as([covered("x", reopen), covered("x", later_group)]) == (4, beans)
    # Not a transaction, or moved onto an account, books no category: it disagrees.
    assert decided_as([covered("x", ruling(5, Action.REJECT))]) == (5, NO_CATEGORY)
    moved = ruling(6, Action.ACCOUNT, account="Assets:Zq:Savings")
    assert decided_as([covered("x", moved)]) == (6, NO_CATEGORY)


def test_a_merchant_only_ever_booked_to_no_category_says_so() -> None:
    """Moved onto an account each time, or ruled not a transaction: that is not booking it
    more than one way, and the row is told what is true."""
    moved = [
        Ruled(
            f"m{n}",
            "zq bean counter",
            DAY,
            ALONE,
            (covered(f"m{n}", ruling(n, Action.ACCOUNT, account="Assets:Zq:Savings")),),
        )
        for n in range(1, 4)
    ]
    plain = target(default=None, bank_says=None, category=None)

    assert proposer(moved).propose(plain) == Proposal(
        None, Reason(Why.ELSEWHERE, "Not booked to a category before")
    )
    transfers = ledger([entry(n, None) for n in range(1, 3)])
    assert proposer(ledger=transfers).propose(plain) == Proposal(
        None, Reason(Why.ELSEWHERE, "Not booked to a category before")
    )
    assert decided_as([]) is None


def test_the_ledgers_booking_of_the_merchant_comes_after_a_members_own() -> None:
    books = ledger([entry(1, "Expenses:Zq:Cafe"), entry(2, "Expenses:Zq:Cafe")])
    ruled = [booked_as("a", 1, "Expenses:Zq:Beans")]

    assert proposer(ledger=books).propose(target()) == Proposal(
        "Expenses:Zq:Cafe", Reason(Why.LEDGER, "As your ledger books it")
    )
    assert proposer(ruled, books).propose(target()).account == "Expenses:Zq:Beans"


def test_the_ledger_teaches_only_when_its_latest_entries_agree() -> None:
    old = [entry(n, "Expenses:Zq:Cafe", day=DAY - timedelta(days=30 + n)) for n in range(1, 4)]
    split = entry(9, None, day=DAY - timedelta(days=10))
    newer = [entry(20 + n, "Expenses:Zq:Cafe", day=DAY - timedelta(days=n)) for n in range(LATEST)]

    plain = target(default=None, bank_says=None)
    assert proposer(ledger=ledger([*old, split])).propose(plain) == Proposal(
        None, Reason(Why.MIXED, "Booked more than one way before")
    )
    assert proposer(ledger=ledger([*old, split, *newer])).propose(plain).account == (
        "Expenses:Zq:Cafe"
    )
    # An entry the ledger's importer set aside is a guess, not how the ledger books it.
    unchecked = ledger([entry(1, "Expenses:Zq:Cafe", checked=False)])
    assert proposer(ledger=unchecked).propose(plain) == Proposal(
        None, Reason(Why.NEW, "New merchant")
    )


def test_the_banks_category_is_mapped_by_a_strict_majority_of_what_the_ledger_holds() -> None:
    """MAP_AT_LEAST held rows of a bank category, and strictly more than half of them booking
    one category, make it the suggestion for that bank category."""
    assert MAP_AT_LEAST == 3

    def held(*categories: str | None) -> Ledger:
        rows = [entry(n, c, payee=f"Zq Elsewhere {chr(65 + n)}") for n, c in enumerate(categories)]
        return ledger(rows, [("FOOD_AND_DRINK_COFFEE", e.entry) for e in rows])

    row = target(merchant="zq never seen")
    dining = "Expenses:Zq:Dining"
    bank = Reason(Why.BANK, "Bank says Coffee")

    assert proposer(ledger=held(dining, dining, "Expenses:Zq:X")).propose(row) == Proposal(
        dining, bank
    )
    # Two of four is no majority, and two rows are too few however they agree.
    assert proposer(ledger=held(dining, dining, "Expenses:Zq:X", "Expenses:Zq:Y")).propose(
        row
    ) == Proposal(None, Reason(Why.NEW, "New merchant"))
    assert proposer(ledger=held(dining, dining)).propose(row).account is None
    # A held row the ledger booked as no one category counts against every category.
    assert proposer(ledger=held(dining, None, None)).propose(row).account is None


def test_beside_a_ledger_hearths_own_name_for_a_category_is_used_only_if_the_ledger_has_it() -> (
    None
):
    row = target(merchant="zq never seen")
    without = ledger([entry(1, "Expenses:Zq:Other", payee="Zq Something")])
    having = ledger([entry(1, "Expenses:FoodAndDrink:Coffee", payee="Zq Something")])

    assert proposer(ledger=without).propose(row) == Proposal(None, Reason(Why.NEW, "New merchant"))
    assert proposer(ledger=having).propose(row) == Proposal(
        "Expenses:FoodAndDrink:Coffee", Reason(Why.BANK, "Bank says Coffee")
    )


def test_a_row_with_no_merchant_learns_nothing_and_is_not_called_new() -> None:
    ruled = [booked_as("a", 1, "Expenses:Zq:Beans", name="zq bean counter")]
    row = target(merchant=None, default=None, bank_says=None, category=None)

    assert proposer(ruled).propose(row) == Proposal(None, None)


# Who a suggestion may learn from.


def test_another_members_decisions_never_reach_a_members_row() -> None:
    """Blake booked the merchant on Blake's own account, and on the joint account, both as a
    category of Blake's choosing: Avery's own row learns neither."""
    ruled = [
        booked_as("b1", 1, "Expenses:Zq:Falconry", by=BLAKE, on=HERS),
        booked_as("b2", 2, "Expenses:Zq:Falconry", by=BLAKE, on=JOINT),
    ]

    assert proposer(ruled).propose(target()).account == "Expenses:FoodAndDrink:Coffee"
    assert proposer(ruled).propose(target(default=None, bank_says=None)) == Proposal(
        None, Reason(Why.NEW, "New merchant")
    )


def test_a_row_learns_only_from_accounts_with_exactly_its_owners() -> None:
    """A suggested category is shown to everyone the row is shared with, so it comes only from
    accounts shared on the same terms: Avery's decision on Avery's own account teaches no joint
    row, and a decision on the joint account, by either owner, teaches no one's own row."""
    mine = [booked_as("a", 1, "Expenses:Zq:Marzipan", on=ALONE)]
    joint = target(owners=JOINT)

    assert proposer(mine).propose(joint).account == "Expenses:FoodAndDrink:Coffee"
    assert proposer(mine).propose(target()).account == "Expenses:Zq:Marzipan"
    for by in (AVERY, BLAKE):
        theirs = [booked_as("j", 1, "Expenses:Zq:Hearth", by=by, on=JOINT)]
        assert proposer(theirs).propose(joint).account == "Expenses:Zq:Hearth"
        assert proposer(theirs).propose(target()).account == "Expenses:FoodAndDrink:Coffee"
    # Nor does an account three own teach one two of them own.
    three = frozenset({AVERY, BLAKE, MemberId(3)})
    assert proposer([booked_as("t", 1, "Expenses:Zq:All", on=three)]).propose(joint).account == (
        "Expenses:FoodAndDrink:Coffee"
    )


def test_the_ledger_teaches_a_row_only_from_accounts_all_its_owners_own() -> None:
    hers = ledger(
        [entry(n, "Expenses:Zq:Falconry", account="Assets:Zq:Blake") for n in range(1, 4)],
        [("FOOD_AND_DRINK_COFFEE", n) for n in range(1, 4)],
    )
    mine = ledger([entry(n, "Expenses:Zq:Marzipan") for n in range(1, 4)])
    nobodys = ledger([entry(1, "Expenses:Zq:Orphanage", account="Assets:Zq:Nobody")])
    plain = target(default=None, bank_says=None)

    # Not by the merchant, not by the bank's category, and not to admit a category exists.
    assert proposer(ledger=hers).propose(plain) == Proposal(None, Reason(Why.NEW, "New merchant"))
    assert proposer(ledger=hers).propose(target()).account is None
    assert proposer(ledger=mine).propose(target(owners=JOINT)).account is None
    assert proposer(ledger=nobodys).propose(plain).account is None
    assert proposer(ledger=mine).propose(target()).account == "Expenses:Zq:Marzipan"
    # The joint account's entries teach no member's own row, nor the joint row any category
    # that only the joint account books, beside a ledger.
    joint = ledger(
        [entry(n, "Expenses:Zq:Hearthstone", account="Assets:Zq:Joint") for n in range(1, 4)],
        [("FOOD_AND_DRINK_COFFEE", n) for n in range(1, 4)],
    )
    assert proposer(ledger=joint).propose(plain) == Proposal(None, Reason(Why.NEW, "New merchant"))
    assert proposer(ledger=joint).propose(target()).account is None
    assert proposer(ledger=joint).propose(target(owners=JOINT)).account == (
        "Expenses:Zq:Hearthstone"
    )


# A suggestion the books can post.


def test_a_suggestion_is_only_ever_a_category_open_on_the_rows_day_in_its_currency() -> None:
    ruled = [booked_as("a", 1, "Expenses:Zq:Beans")]
    row = target()

    assert proposer(ruled, opens={"Expenses:Zq:Beans": (DAY, None, None)}).propose(row).account
    for opens in (
        (DAY + timedelta(days=1), None, None),
        (DAY - timedelta(days=9), DAY - timedelta(days=1), None),
        (DAY - timedelta(days=9), None, frozenset({"EUR"})),
    ):
        found = proposer(ruled, opens={"Expenses:Zq:Beans": opens}).propose(row)
        assert found.account == "Expenses:FoodAndDrink:Coffee", opens
    # Closed on the row's own day still takes it, as beancount closes at the end of the day.
    closing = proposer(ruled, opens={"Expenses:Zq:Beans": (DAY - timedelta(days=9), DAY, None)})
    assert closing.propose(row).account == "Expenses:Zq:Beans"


# Words.


@pytest.mark.parametrize(
    ("account", "group"),
    [
        ("Expenses:FoodAndDrink:Coffee", "Food and drink: coffee"),
        ("Expenses:BankFees:ATMFees", "Bank fees: ATM fees"),
        ("Expenses:Entertainment:TVAndMovies", "Entertainment: TV and movies"),
        ("Income:Wages", "Income: wages"),
        ("Expenses:Dining", "Dining"),
        ("Expenses:Food:Dining:Lunch", "Food: dining: lunch"),
    ],
)
def test_a_group_of_suggested_rows_is_named_for_its_category(account: str, group: str) -> None:
    assert group_of(account) == group


@pytest.mark.parametrize(
    ("category", "said"),
    [
        ("FOOD_AND_DRINK_RESTAURANT", "Restaurant"),
        ("BANK_FEES_ATM_FEES", "ATM fees"),
        ("INCOME_WAGES", "Wages"),
        ("GENERAL_MERCHANDISE_OTHER_GENERAL_MERCHANDISE", "Other general merchandise"),
        (None, None),
        ("NOT_A_PLAID_CATEGORY", None),
    ],
)
def test_the_bank_is_quoted_by_its_most_specific_words(category: str | None, said: str) -> None:
    assert bank_says(category) == said
