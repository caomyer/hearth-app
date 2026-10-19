"""Card-payment pairing: a bank's payment to a card the household connects is one transfer.

The accounts have the shape of a real bank: one checking and one savings account, and five cards
it calls the same thing, told apart by their last digits. Every value is invented.
"""

import itertools
import random
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hearth import accounts as accounts_
from hearth.plaid import pairing
from hearth.plaid.client import BankAccount, Institution, Transaction
from hearth.plaid.fake import FakeAccount, FakeInstitution, FakeTransaction
from hearth.plaid.feed import Feed
from hearth.plaid.pairing import Leg, Pair, Pairing, Side, pair
from hearth.plaid.registry import Registry
from tests.conftest import FakeClock
from tests.firstday import CARD, PATHS, first_day
from tests.vault.conftest import MEMBER_A, open_database


@dataclass(frozen=True, slots=True)
class Account:
    account_id: str
    type: str
    mask: str | None


CHECKING = Account("chk", "depository", "6616")
SAVINGS = Account("sav", "depository", "7717")
CARDS = tuple(Account(f"c{n}", "credit", f"{n}{n}1{n}") for n in range(1, 6))
WREN, FINCH, LARK = CARDS[:3]
ACCOUNTS = (*CARDS, CHECKING, SAVINGS)

DAY = date(2026, 3, 2)
PAYMENT = "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"

_ids = itertools.count()


def row(
    account: Account,
    day: date,
    amount: str,
    name: str,
    *,
    category: str | None = None,
    pending: bool = False,
) -> Transaction:
    """A posted row as Plaid sends it: positive is money leaving the account."""
    return Transaction(
        transaction_id=f"t{next(_ids)}",
        account_id=account.account_id,
        day=day,
        authorized_date=day,
        amount=Decimal(amount),
        currency="USD",
        name=name,
        merchant_name=None,
        original_description=name.upper(),
        category=category,
        pending=pending,
        pending_transaction_id=None,
    )


def paid_to(card: Account, day: date, amount: str) -> Transaction:
    """The checking account's row for a payment that names its card, as a real bank's does."""
    return row(
        CHECKING, day, amount, f"Payment to Willowmere card ending in {card.mask}", category=PAYMENT
    )


def autopay(day: date, amount: str) -> Transaction:
    """The checking account's row for a payment that does not say which card it pays."""
    return row(CHECKING, day, amount, "WILLOWMERE CARD AUTOPAY", category=PAYMENT)


def received(card: Account, day: date, amount: str) -> Transaction:
    """A card's row for a payment arriving. Plaid has given these a contractor-income category."""
    return row(card, day, amount, "Payment Thank You-Mobile", category="INCOME_CONTRACTOR")


def paired(found: Pairing) -> set[tuple[str, str]]:
    return {(p.bank.transaction_id, p.card.transaction_id) for p in found.pairs}


def unpaired(found: Pairing) -> set[str]:
    return {leg.row.transaction_id for leg in found.unpaired}


# One payment, both sides connected.


def test_a_payment_to_a_connected_card_is_one_transfer_not_two_transactions() -> None:
    bank = paid_to(WREN, DAY, "120.00")
    card = received(WREN, DAY - timedelta(days=1), "-120.00")
    spending = row(WREN, DAY, "18.65", "Bramble Pharmacy", category="MEDICAL_PHARMACIES")

    found = pair(ACCOUNTS, [bank, card, spending])

    assert found.pairs == (Pair(bank, card),)
    assert found.unpaired == ()
    assert found.of(bank.transaction_id) is found.of(card.transaction_id) is found.pairs[0]
    assert found.of(spending.transaction_id) is None


def test_a_payment_that_does_not_name_its_card_pairs_when_only_one_card_matches() -> None:
    bank = autopay(DAY, "120.00")
    card = received(FINCH, DAY, "-120.00")

    assert paired(pair(ACCOUNTS, [bank, card])) == {(bank.transaction_id, card.transaction_id)}


@pytest.mark.parametrize(
    "text",
    ["CHASE CREDIT CRD AUTOPAY", "AMERICAN EXPRESS ACH PMT", "DISCOVER DES:E-PAYMENT"],
)
def test_a_bank_row_names_a_card_payment_in_the_issuers_words(text: str) -> None:
    bank = row(CHECKING, DAY, "75.00", text)  # no category: the wording decides
    card = received(LARK, DAY, "-75.00")

    assert paired(pair(ACCOUNTS, [bank, card])) == {(bank.transaction_id, card.transaction_id)}


@pytest.mark.parametrize("gap", [-2, 0, 5])
def test_the_bank_row_may_be_dated_from_two_days_before_the_card_row_to_five_after(
    gap: int,
) -> None:
    bank = paid_to(WREN, DAY + timedelta(days=gap), "40.00")
    card = received(WREN, DAY, "-40.00")

    assert pair(ACCOUNTS, [bank, card]).pairs == (Pair(bank, card),)


# A leg nothing pairs with is kept, flagged, never dropped.


@pytest.mark.parametrize(
    ("bank", "card"),
    [
        # Outside the window, on either side.
        (paid_to(WREN, DAY + timedelta(days=6), "40.00"), received(WREN, DAY, "-40.00")),
        (paid_to(WREN, DAY - timedelta(days=3), "40.00"), received(WREN, DAY, "-40.00")),
        # A cent apart.
        (paid_to(WREN, DAY, "40.00"), received(WREN, DAY, "-40.01")),
        # The bank names one card; the payment arrived at another.
        (paid_to(WREN, DAY, "40.00"), received(FINCH, DAY, "-40.00")),
        # Money leaving both: a payment, and a card's payment returned. Never one payment.
        (paid_to(WREN, DAY, "40.00"), row(WREN, DAY, "40.00", "PAYMENT RETURNED")),
    ],
    ids=["six-days-late", "three-days-early", "a-cent-apart", "other-card", "same-direction"],
)
def test_legs_that_do_not_match_both_stay_legs(bank: Transaction, card: Transaction) -> None:
    found = pair(ACCOUNTS, [bank, card])

    assert found.pairs == ()
    assert unpaired(found) == {bank.transaction_id, card.transaction_id}
    assert {leg.side for leg in found.unpaired} == {Side.BANK, Side.CARD}
    assert found.tied == 0


def test_a_card_paid_from_a_bank_nobody_connected_is_a_leg_on_its_own() -> None:
    card = row(WREN, DAY, "-310.00", "AUTOMATIC PAYMENT - THANK", category="OTHER_OTHER")

    found = pair(ACCOUNTS, [card])

    assert found.unpaired == (Leg(card, Side.CARD),)
    assert isinstance(found.of(card.transaction_id), Leg)


def test_a_payment_to_a_card_nobody_connected_is_a_leg_naming_that_card() -> None:
    bank = row(
        CHECKING, DAY, "90.00", "Payment to Willowmere card ending in 9999", category=PAYMENT
    )

    assert pair(ACCOUNTS, [bank]).unpaired == (Leg(bank, Side.BANK, "9999"),)


def test_a_payment_to_a_connected_but_unmapped_card_is_a_leg_too() -> None:
    bank = paid_to(LARK, DAY, "90.00")
    card = received(LARK, DAY, "-90.00")

    # Pairing is given only the accounts the books hold; the card is not among them.
    found = pair([a for a in ACCOUNTS if a is not LARK], [bank])

    assert found.unpaired == (Leg(bank, Side.BANK, LARK.mask),)
    with pytest.raises(ValueError, match="not given"):
        pair([a for a in ACCOUNTS if a is not LARK], [bank, card])


@pytest.mark.parametrize(
    "card",
    [
        row(WREN, DAY, "-40.00", "Larkspur Grocer", category="GENERAL_MERCHANDISE"),
        row(WREN, DAY, "-40.00", "ANNUAL STATEMENT CREDIT", category="OTHER_OTHER"),
        row(WREN, DAY, "-40.00", "Tinderbox Coffee", category="INCOME_CONTRACTOR"),
    ],
    ids=["refund", "statement-credit", "refund-plaid-calls-income"],
)
def test_money_arriving_at_a_card_that_is_not_a_payment_is_no_leg(card: Transaction) -> None:
    bank = paid_to(WREN, DAY, "40.00")

    found = pair(ACCOUNTS, [bank, card])

    assert found.of(card.transaction_id) is None
    assert found.unpaired == (Leg(bank, Side.BANK, WREN.mask),)


def test_rows_that_are_not_card_payments_are_left_alone() -> None:
    rows = [
        row(CHECKING, DAY, "40.00", "Online Transfer to SAV ...7717", category="TRANSFER_OUT"),
        row(SAVINGS, DAY, "-40.00", "Online Transfer from CHK ...6616", category="TRANSFER_IN"),
        row(CHECKING, DAY, "-2140.00", "Payroll Wren & Co", category="INCOME_WAGES"),
        row(WREN, DAY, "64.20", "Larkspur Grocer", category="FOOD_AND_DRINK_GROCERIES"),
    ]

    assert pair(ACCOUNTS, rows) == Pairing((), (), 0)


# Where a naive matcher breaks.


def test_two_cards_paid_the_same_amount_the_same_day_are_not_paired_by_guesswork() -> None:
    banks = [autopay(DAY, "250.00"), autopay(DAY, "250.00")]
    cards = [received(WREN, DAY, "-250.00"), received(FINCH, DAY, "-250.00")]

    found = pair(ACCOUNTS, [*banks, *cards])

    assert found.pairs == ()
    assert unpaired(found) == {t.transaction_id for t in (*banks, *cards)}
    assert found.tied == 4


def test_two_cards_paid_the_same_amount_the_same_day_pair_by_the_card_each_names() -> None:
    to_wren, to_finch = paid_to(WREN, DAY, "250.00"), paid_to(FINCH, DAY, "250.00")
    at_wren, at_finch = received(WREN, DAY, "-250.00"), received(FINCH, DAY, "-250.00")

    found = pair(ACCOUNTS, [to_wren, to_finch, at_finch, at_wren])

    assert paired(found) == {
        (to_wren.transaction_id, at_wren.transaction_id),
        (to_finch.transaction_id, at_finch.transaction_id),
    }


def test_a_row_tied_at_its_closest_does_not_pair_with_a_partner_further_away() -> None:
    # The bank row is a day from two cards' payments, and three from a third card's. Which
    # card it paid is ambiguous, and a further partner does not make it any less so.
    bank = autopay(DAY, "80.00")
    near = [
        received(WREN, DAY - timedelta(days=1), "-80.00"),
        received(FINCH, DAY + timedelta(days=1), "-80.00"),
    ]
    far = received(LARK, DAY - timedelta(days=3), "-80.00")

    found = pair(ACCOUNTS, [bank, *near, far])

    assert found.pairs == ()
    assert len(found.unpaired) == 4
    assert found.tied == 3


def test_the_closest_partner_pairs_first() -> None:
    bank = autopay(DAY, "80.00")
    closer = received(WREN, DAY - timedelta(days=1), "-80.00")
    further = received(FINCH, DAY - timedelta(days=2), "-80.00")

    found = pair(ACCOUNTS, [bank, further, closer])

    assert found.pairs == (Pair(bank, closer),)
    assert found.unpaired == (Leg(further, Side.CARD),)
    assert found.tied == 0


def test_a_payment_and_a_payment_returned_pair_each_with_its_own_kind() -> None:
    # Money goes to the card, and the same sum comes back to the bank, inside one window. Each
    # pairs with the row moving the same money the same way; a match on size alone would
    # cross them, and the card's refund in the same window is not a payment at all.
    pays = paid_to(WREN, DAY, "55.00")
    arrives = received(WREN, DAY, "-55.00")
    returned_bank = row(
        CHECKING, DAY + timedelta(days=2), "-55.00", "Returned: payment to card ending in 1111"
    )
    returned_card = row(WREN, DAY + timedelta(days=1), "55.00", "PAYMENT RETURNED")
    refund = row(WREN, DAY + timedelta(days=1), "-55.00", "Quillfeather Books")

    found = pair(ACCOUNTS, [returned_card, refund, arrives, returned_bank, pays])

    assert paired(found) == {
        (pays.transaction_id, arrives.transaction_id),
        (returned_bank.transaction_id, returned_card.transaction_id),
    }
    assert found.of(refund.transaction_id) is None


def test_a_monthly_payment_of_the_same_amount_pairs_with_its_own_month() -> None:
    months = [date(2026, m, 3) for m in range(1, 7)]
    cards = [received(WREN, d, "-100.00") for d in months]
    banks = [paid_to(WREN, d + timedelta(days=1), "100.00") for d in months]
    # March's payment left the bank four days late: still March's. May's left nine days
    # late, too late to be anyone's, and must not be taken for June's.
    banks[2] = paid_to(WREN, months[2] + timedelta(days=4), "100.00")
    banks[4] = paid_to(WREN, months[4] + timedelta(days=9), "100.00")

    found = pair(ACCOUNTS, [*banks, *cards])

    expected = {(b.transaction_id, c.transaction_id) for b, c in zip(banks, cards, strict=True)}
    expected.discard((banks[4].transaction_id, cards[4].transaction_id))
    assert paired(found) == expected
    assert unpaired(found) == {banks[4].transaction_id, cards[4].transaction_id}


def test_a_leg_whose_connection_stopped_stays_a_leg_until_its_other_side_arrives() -> None:
    # The card's connection stopped before its side of the payment arrived; another card
    # happens to have a payment of the same size the same day. The bank's row names its card,
    # so it waits rather than settling on the wrong one.
    bank = paid_to(WREN, DAY, "64.00")
    coincidence = received(FINCH, DAY, "-64.00")

    before = pair(ACCOUNTS, [bank, coincidence])

    assert before.pairs == ()
    assert unpaired(before) == {bank.transaction_id, coincidence.transaction_id}

    arrived = received(WREN, DAY - timedelta(days=1), "-64.00")
    after = pair(ACCOUNTS, [bank, coincidence, arrived])

    assert after.pairs == (Pair(bank, arrived),)
    assert after.unpaired == (Leg(coincidence, Side.CARD),)


# Money, exactly.


def test_amounts_pair_as_decimals_however_plaid_wrote_them() -> None:
    bank = paid_to(WREN, DAY, "100.1")
    card = received(WREN, DAY, "-100.10")

    assert pair(ACCOUNTS, [bank, card]).pairs == (Pair(bank, card),)


def test_amounts_a_tolerance_would_call_equal_do_not_pair() -> None:
    # 0.1 + 0.2 is 0.30000000000000004 as a float. A matcher that rounds or compares floats
    # within a tolerance pairs these; the bank and the card disagree by a fraction of a cent.
    bank = paid_to(WREN, DAY, str(Decimal("0.1") + Decimal("0.2")))
    card = received(WREN, DAY, str(-Decimal(0.1 + 0.2)))

    assert pair(ACCOUNTS, [bank, card]).pairs == ()


def test_amounts_a_float_cannot_tell_apart_do_not_pair() -> None:
    # Past fifteen digits a float has no cents left: these two are the same float.
    bank = paid_to(WREN, DAY, "1234567890123456.78")
    card = received(WREN, DAY, "-1234567890123456.79")

    assert pair(ACCOUNTS, [bank, card]).pairs == ()


def test_a_float_amount_is_refused_rather_than_compared() -> None:
    bank = paid_to(WREN, DAY, "0.30")
    floated = replace(bank, amount=0.3)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="float"):
        pair(ACCOUNTS, [floated])


def test_amounts_in_different_currencies_do_not_pair() -> None:
    bank = paid_to(WREN, DAY, "40.00")
    card = replace(received(WREN, DAY, "-40.00"), currency="CAD")

    assert pair(ACCOUNTS, [bank, card]).pairs == ()


# What pairing is given, and what it does not depend on.


def test_a_pending_row_is_refused() -> None:
    with pytest.raises(ValueError, match="pending"):
        pair(ACCOUNTS, [row(WREN, DAY, "-40.00", "Payment Thank You", pending=True)])


def test_a_row_given_twice_is_refused() -> None:
    bank = paid_to(WREN, DAY, "40.00")

    with pytest.raises(ValueError, match="twice"):
        pair(ACCOUNTS, [bank, bank])


def test_the_order_rows_and_accounts_arrive_in_changes_nothing() -> None:
    rows = [
        *(autopay(DAY, "250.00") for _ in range(2)),
        received(WREN, DAY, "-250.00"),
        received(FINCH, DAY, "-250.00"),
        paid_to(LARK, DAY, "31.00"),
        received(LARK, DAY - timedelta(days=1), "-31.00"),
        autopay(DAY + timedelta(days=10), "80.00"),
        received(WREN, DAY + timedelta(days=9), "-80.00"),
        received(FINCH, DAY + timedelta(days=10), "-80.00"),
        received(LARK, DAY + timedelta(days=20), "-12.00"),
    ]
    first = pair(ACCOUNTS, rows)
    shuffled = random.Random(7)  # noqa: S311 - shuffles test rows; nothing secret

    for _ in range(25):
        again = rows[:]
        shuffled.shuffle(again)
        accounts = list(ACCOUNTS)
        shuffled.shuffle(accounts)
        assert pair(accounts, again) == first


def test_an_unpaired_leg_says_nothing_about_any_other_account() -> None:
    # One member's payment, against what another member's cards might hold: nothing, a
    # payment outside the window, a payment a cent off, or two equally good matches. The leg
    # is the same fact in every case, so reviewing it reveals none of them.
    mine = autopay(DAY, "45.00")
    theirs: Sequence[Sequence[Transaction]] = [
        [],
        [received(WREN, DAY + timedelta(days=12), "-45.00")],
        [received(WREN, DAY, "-45.01")],
        [received(WREN, DAY, "-45.00"), received(FINCH, DAY, "-45.00")],
    ]

    legs = {pair(ACCOUNTS, [mine, *other]).of(mine.transaction_id) for other in theirs}

    assert legs == {Leg(mine, Side.BANK)}


# An account connected twice: a fact about connections, which pairing cannot infer.

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def _item(
    conn: sqlite3.Connection, item_id: str, institution: str | None, accounts: Sequence[Account]
) -> None:
    """An Item with every account in `accounts` confirmed into the books."""
    registry = Registry(conn)
    registry.add(
        item_id,
        member=MEMBER_A,
        credentials="member:1",
        institution=Institution(institution, "Willowmere Trust"),
        fallback_name="",
        days_requested=730,
        now=NOW,
    )
    registry.record_accounts(
        item_id,
        [
            BankAccount(a.account_id, a.account_id, None, a.mask, a.type, None, None, None, "USD")
            for a in accounts
        ],
    )
    for a in accounts:
        path = f"Assets:Avery:{a.account_id.title().replace('-', '')}"
        mapped = accounts_.register(conn, ledger_account=path, name=path, owners=[MEMBER_A])
        registry.confirm(a.account_id, mapped.id, by=MEMBER_A, now=NOW)


def _twice(registry: Registry) -> set[tuple[str, ...]]:
    return {tuple(a.account_id for a in group) for group in registry.connected_twice()}


def test_a_bank_removed_and_connected_again_is_caught_where_the_tie_count_cannot_see_it() -> None:
    # Avery removes the bank in March and connects it again: Plaid gives the accounts new ids
    # and pulls history again, so March and April arrive from both. Every month the card is
    # also paid from a bank nobody connected.
    old_chk, old_card = replace(CHECKING, account_id="old-chk"), replace(WREN, account_id="old-c1")
    new_chk, new_card = replace(CHECKING, account_id="new-chk"), replace(WREN, account_id="new-c1")
    conn = open_database()
    try:
        registry = Registry(conn)
        _item(conn, "old", "ins_willowmere", [old_chk, old_card])
        registry.remove("old", at=NOW)
        _item(conn, "new", "ins_willowmere", [new_chk, new_card])
        months = [date(2026, m, 3) for m in range(1, 7)]
        rows: list[Transaction] = []
        for n, day in enumerate(months, start=1):
            for chk, card in [(old_chk, old_card)] * (n <= 4) + [(new_chk, new_card)] * (n >= 3):
                pays = f"Payment to Willowmere card ending in {card.mask}"
                rows += [
                    row(chk, day + timedelta(days=1), "100.00", pays, category=PAYMENT),
                    received(card, day, "-100.00"),
                    row(card, day, "-310.00", "AUTOMATIC PAYMENT - THANK", category="OTHER"),
                ]

        found = pairing.staged(conn, rows)

        # January, February, May and June pair; March and April tie; the payments from the
        # unconnected bank are unpaired and untied, duplicated or not.
        assert len(found.pairs) == 4
        assert (found.tied, len(found.unpaired)) == (8, 16)
        assert _twice(registry) == {("new-c1", "old-c1"), ("new-chk", "old-chk")}
    finally:
        conn.close()


def test_a_bank_connected_again_without_removing_the_old_connection_is_caught() -> None:
    conn = open_database()
    try:
        _item(conn, "first", "ins_willowmere", [CHECKING])
        _item(conn, "second", "ins_willowmere", [replace(CHECKING, account_id="chk-again")])

        assert _twice(Registry(conn)) == {("chk", "chk-again")}
    finally:
        conn.close()


@pytest.mark.parametrize(
    "institutions",
    [("ins_willowmere", None), (None, "ins_willowmere"), (None, None)],
    ids=["second-without-an-id", "first-without-an-id", "neither-with-an-id"],
)
def test_an_item_without_an_institution_id_may_be_at_any_institution(
    institutions: tuple[str | None, str | None],
) -> None:
    # Plaid did not say which institution one connection is at, so it may be the other's.
    conn = open_database()
    try:
        _item(conn, "first", institutions[0], [CHECKING])
        _item(conn, "second", institutions[1], [replace(CHECKING, account_id="chk-again")])

        assert _twice(Registry(conn)) == {("chk", "chk-again")}
    finally:
        conn.close()


@pytest.mark.parametrize(
    "items",
    [
        [("only", "ins_willowmere", [CHECKING, WREN, FINCH])],
        [("only", "ins_willowmere", [CHECKING, replace(SAVINGS, mask=CHECKING.mask)])],
        [
            ("here", "ins_willowmere", [CHECKING]),
            ("there", "ins_pinecone", [replace(CHECKING, account_id="elsewhere")]),
        ],
        [("one", "ins_willowmere", [CHECKING]), ("two", "ins_willowmere", [WREN])],
    ],
    ids=[
        "one-item",
        "same-digits-in-one-item",
        "same-digits-at-two-institutions",
        "two-accounts-at-one-institution",
    ],
)
def test_accounts_that_are_not_one_account_twice_are_not_reported(
    items: Sequence[tuple[str, str, Sequence[Account]]],
) -> None:
    conn = open_database()
    try:
        for item_id, institution, accounts in items:
            _item(conn, item_id, institution, accounts)

        assert Registry(conn).connected_twice() == []
    finally:
        conn.close()


# Over a household's connected accounts, as a member connects and maps them.


def _connected(
    tmp_path: Path, clock: FakeClock, *, card_holder: str
) -> list[tuple[str, date, Decimal, str]]:
    """What pairing finds once Avery connects a bank and `card_holder` connects a card at
    another, both through Link and the mapping page: the legs, as (kind, day, amount, name)."""
    fresh = first_day(tmp_path / card_holder, clock, institutions=(WILLOWMERE, PINECONE))
    fresh.link("avery", WILLOWMERE.id)
    fresh.map(fresh.signed_in("avery"), PATHS)
    fresh.link(card_holder, PINECONE.id)
    fresh.map(
        fresh.signed_in(card_holder),
        {"9904": f"Liabilities:{card_holder.title()}:Pinecone:Card"},
        owner=card_holder,
    )

    conn = fresh.connect()
    try:
        with Feed.open(fresh.settings.feed_database) as feed:
            rows = [
                t
                for a in Registry(conn).confirmed()
                for t in feed.transactions(a.account_id)
                if not t.pending
            ]
        found = pairing.staged(conn, rows)
    finally:
        conn.close()
    legs: list[tuple[str, date, Decimal, str]] = []
    for p in found.pairs:
        legs.append(("pair", p.bank.day, p.bank.amount, f"{p.bank.name} / {p.card.name}"))
    for leg in found.unpaired:
        legs.append((leg.side.value, leg.row.day, leg.row.amount, leg.row.name or ""))
    return sorted(legs)


SEPT = date(2026, 9, 1)


def _card(n: int, rows: tuple[FakeTransaction, ...]) -> FakeAccount:
    return FakeAccount(
        f"c{n}", CARD, f"{n}{n}1{n}", "credit", "credit card", Decimal(1), None, rows
    )


WILLOWMERE = FakeInstitution(
    "ins_willowmere",
    "Willowmere Trust",
    (
        _card(
            1,
            (
                FakeTransaction("Larkspur Grocer", Decimal("64.20"), SEPT, "FOOD_AND_DRINK"),
                FakeTransaction("Payment Thank You-Mobile", Decimal("-120.00"), SEPT, "OTHER"),
            ),
        ),
        _card(2, (FakeTransaction("AUTOMATIC PAYMENT - THANK", Decimal("-75.00"), SEPT),)),
        *(_card(n, ()) for n in range(3, 6)),
        FakeAccount(
            "chk",
            "TOTAL CHECKING",
            "6616",
            "depository",
            "checking",
            Decimal("4130"),
            None,
            (
                FakeTransaction(
                    "Payment to Willowmere card ending in 1111",
                    Decimal("120.00"),
                    SEPT + timedelta(days=1),
                    PAYMENT,
                ),
                FakeTransaction(
                    "Payment to Pinecone card ending in 9904",
                    Decimal("60.00"),
                    SEPT + timedelta(days=10),
                ),
                FakeTransaction("Payroll Wren & Co", Decimal("-2140.00"), SEPT, "INCOME_WAGES"),
                # Still pending: it may change or vanish, so it pairs with nothing yet.
                FakeTransaction(
                    "Payment to Willowmere card ending in 2212",
                    Decimal("75.00"),
                    SEPT + timedelta(days=20),
                    PAYMENT,
                    pending=True,
                ),
            ),
        ),
        FakeAccount("sav", "SAVINGS", "7717", "depository", "savings", Decimal("8800"), None, ()),
    ),
)
"""The first-day bank, with a month of rows: Avery pays one card from checking, and another
from a bank nobody connected, and pays a card at another institution too."""

PINECONE = FakeInstitution(
    "ins_pinecone",
    "Pinecone Card",
    (
        FakeAccount(
            "card",
            "Credit card",
            "9904",
            "credit",
            "credit card",
            Decimal("684.12"),
            None,
            (
                FakeTransaction(
                    "PAYMENT RECEIVED - THANK YOU", Decimal("-60.00"), SEPT + timedelta(days=9)
                ),
                FakeTransaction("Tinderbox Coffee", Decimal("5.75"), SEPT, "FOOD_AND_DRINK"),
            ),
        ),
    ),
)


def test_a_household_pairs_payments_across_its_connections(
    tmp_path: Path, clock: FakeClock
) -> None:
    assert _connected(tmp_path, clock, card_holder="avery") == [
        ("card", SEPT, Decimal("-75.00"), "AUTOMATIC PAYMENT - THANK"),
        (
            "pair",
            SEPT + timedelta(days=1),
            Decimal("120.00"),
            "Payment to Willowmere card ending in 1111 / Payment Thank You-Mobile",
        ),
        (
            "pair",
            SEPT + timedelta(days=10),
            Decimal("60.00"),
            "Payment to Pinecone card ending in 9904 / PAYMENT RECEIVED - THANK YOU",
        ),
    ]


def test_who_holds_the_card_changes_nothing_about_how_its_payments_pair(
    tmp_path: Path, clock: FakeClock
) -> None:
    assert _connected(tmp_path, clock, card_holder="blake") == _connected(
        tmp_path, clock, card_holder="avery"
    )
