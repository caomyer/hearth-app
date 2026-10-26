"""The import: what a connected bank sent, in the books, the way a new household meets it.

A member with no ledger connects a bank and files its accounts; every posted row the bank sent
is then in the books, flagged until reviewed, a payment from the bank account to a connected
card once as a transfer, and every entry names the connection it came from, which turns on
coverage's rule that a connection vouches only for what the books hold of it. The bank has the
shape of a real one: seven accounts at one institution, five of them called the same thing.
Every institution, payee and figure is invented.
"""

import re
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from html import unescape
from pathlib import Path

import pytest
from beancount.core.amount import Amount
from beancount.core.data import Directive, Open, Posting, Transaction
from fastapi.testclient import TestClient

from hearth import accounts, books, figures, ownbooks
from hearth.books import Books
from hearth.connections import Connection, RegisteredConnections, item_of
from hearth.disclosure import rulings_in
from hearth.members import MemberId
from hearth.months import Month
from hearth.plaid import booking, opening, pairing
from hearth.plaid.booking import moved
from hearth.plaid.client import SyncPage
from hearth.plaid.client import Transaction as PlaidTransaction
from hearth.plaid.connector import NEW
from hearth.plaid.fake import FakeAccount, FakeInstitution, FakeTransaction
from hearth.plaid.feed import Feed, Outcome
from hearth.plaid.registry import ItemState, Registry
from hearth.review.ids import plaid_review_id, says
from hearth.review.pile import TRANSFER_PENDING, UNRESOLVED
from hearth.review.rulings import Action, RulingStore, Scope
from tests import household
from tests.conftest import FakeClock
from tests.firstday import CARD, ZERO, FirstDay, first_day, shown, written

PAID = date(2026, 9, 10)
"""The day the checking account paid the first card, which received it the day after."""

CHECKING = (
    FakeTransaction("Payroll Wren & Co", Decimal("-2140.00"), date(2026, 9, 1), "INCOME_WAGES"),
    FakeTransaction(
        "Payment to Willowmere card ending in 1111",
        Decimal("300.00"),
        PAID,
        "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
    ),
    FakeTransaction(
        "Payment to Fenwick card ending in 9090",
        Decimal("120.00"),
        date(2026, 9, 12),
        "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
    ),
    FakeTransaction(
        "Transfer to Hollins Savings",
        Decimal("500.00"),
        date(2026, 9, 14),
        "TRANSFER_OUT_ACCOUNT_TRANSFER",
    ),
    FakeTransaction(
        "Larkspur Grocer", Decimal("64.20"), date(2026, 9, 20), "FOOD_AND_DRINK_GROCERIES"
    ),
)
WREN = (
    FakeTransaction("Tinderbox Coffee", Decimal("5.75"), date(2026, 9, 3), "FOOD_AND_DRINK_COFFEE"),
    FakeTransaction(
        "Payment Thank You", Decimal("-300.00"), PAID + timedelta(days=1), "LOAN_PAYMENTS_OTHER"
    ),
    FakeTransaction(
        "Tinderbox Coffee", Decimal("6.25"), date(2026, 10, 14), "FOOD_AND_DRINK_COFFEE", True
    ),
)
QUIET = (
    FakeTransaction(
        "Bramble Pharmacy",
        Decimal("18.65"),
        date(2024, 11, 2),
        "MEDICAL_PHARMACIES_AND_SUPPLEMENTS",
    ),
)
"""The second card's only row, nearly two years ago: a card nobody uses."""


def _monthly(name: str, amount: str, category: str) -> tuple[FakeTransaction, ...]:
    """A row on the 5th of every month of the history, so the account is current."""
    day, rows = date(2024, 10, 20), []
    while day <= date(2026, 10, 15):
        rows.append(FakeTransaction(name, Decimal(amount), day.replace(day=5), category))
        day = (day.replace(day=1) + timedelta(days=32)).replace(day=1)
    return tuple(rows)


SHAPE = FakeInstitution(
    "ins_willowmere",
    "Willowmere Trust",
    (
        FakeAccount(
            "c1",
            CARD,
            "1111",
            "credit",
            "credit card",
            Decimal("0.50"),
            transactions=(
                *WREN,
                *_monthly(
                    "Cobblestone Utilities", "92.10", "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY"
                ),
            ),
        ),
        FakeAccount("c2", CARD, "2212", "credit", "credit card", Decimal(18), transactions=QUIET),
        *(
            FakeAccount(
                f"c{n}",
                CARD,
                f"{n}{n}1{n}",
                "credit",
                "credit card",
                Decimal(n),
                transactions=_monthly(
                    "Marrowbone Transit", f"{n}.90", "TRANSPORTATION_PUBLIC_TRANSIT"
                ),
            )
            for n in range(3, 6)
        ),
        FakeAccount(
            "chk",
            "TOTAL CHECKING",
            "6616",
            "depository",
            "checking",
            Decimal(4130),
            transactions=(*CHECKING, *_monthly("Payroll Wren & Co", "-2140.00", "INCOME_WAGES")),
        ),
        FakeAccount(
            "sav",
            "SAVINGS",
            "7717",
            "depository",
            "savings",
            Decimal(8800),
            transactions=_monthly("Interest", "-0.42", "INCOME_INTEREST_EARNED"),
        ),
    ),
)
"""Seven accounts from one invented bank, five of them cards it calls the same."""

PATHS = {
    "1111": "Liabilities:Avery:Willowmere:Wren",
    "2212": "Liabilities:Avery:Willowmere:Finch",
    "3313": "Liabilities:Avery:Willowmere:Lark",
    "4414": "Liabilities:Avery:Willowmere:Robin",
    "5515": "Liabilities:Avery:Willowmere:Swift",
    "6616": "Assets:Avery:Willowmere:Checking",
    "7717": "Assets:Avery:Willowmere:Savings",
}

CARD_BANK = FakeInstitution(
    "ins_fenwick",
    "Fenwick Card",
    (
        FakeAccount(
            "card",
            "Fenwick Rewards",
            "9090",
            "credit",
            "credit card",
            Decimal(40),
            transactions=tuple(
                row
                for row in _monthly("Hilltop Cafe", "40.00", "FOOD_AND_DRINK_RESTAURANT")
                if row.day < date(2026, 6, 1)
            ),
        ),
    ),
)
"""A card at a bank of its own, which stops sending anything after May."""

FENWICK = "Liabilities:Avery:Fenwick:Card"

PAID_CARD_BANK = FakeInstitution(
    "ins_fenwick_paid",
    "Fenwick Card",
    (
        replace(
            CARD_BANK.accounts[0],
            transactions=(
                FakeTransaction(
                    "AUTOMATIC PAYMENT - THANK YOU",
                    Decimal("-120.00"),
                    date(2026, 9, 13),
                    "INCOME_OTHER_INCOME",
                ),
            ),
        ),
    ),
)
"""The same card at its own bank, which did send its side of the payment from checking, under
a category that would read as income on its own."""


@pytest.fixture
def filed(tmp_path: Path, clock: FakeClock) -> FirstDay:
    """Avery's household once their bank is connected and its accounts filed."""
    fresh = first_day(tmp_path, clock, institutions=(SHAPE, CARD_BANK))
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), PATHS)
    until(fresh, lambda b: _on(b, PATHS["6616"]))
    return fresh


def until(fresh: FirstDay, holds: Callable[[Books], object]) -> Books:
    """The books once a recompute started in the background makes `holds` true."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        found = fresh.bookkeeper.status.books
        if found is not None and holds(found):
            return found
        time.sleep(0.01)
    raise AssertionError(f"the books never came to hold it: {fresh.bookkeeper.status.failure}")


def _on(found: Books, account: str) -> list[Transaction]:
    return [
        e for e in _transactions(found.entries) if any(p.account == account for p in e.postings)
    ]


def _number(posting: Posting) -> Decimal:
    assert posting.units is not None
    assert isinstance(posting.units.number, Decimal)
    return posting.units.number


def _currency(posting: Posting) -> str:
    assert posting.units is not None
    return posting.units.currency


def _source(posting: Posting) -> str:
    assert posting.meta is not None
    source = posting.meta["source"]
    assert isinstance(source, str)
    return source


def _transactions(entries: tuple[Directive, ...] | list[Directive]) -> list[Transaction]:
    """The entries the import booked from the bank's rows: not the opening balances derived
    beside them."""
    return [e for e in entries if isinstance(e, Transaction) and not opening.anchors(e)]


def _sources(entry: Transaction) -> set[str]:
    """Every source an entry was booked from: its own, and each posting's."""
    found = {entry.meta["source"]}
    found |= {p.meta["source"] for p in entry.postings if p.meta and "source" in p.meta}
    return found


def _feed_rows(fresh: FirstDay) -> list[PlaidTransaction]:
    conn = fresh.connect()
    try:
        account_ids = [a.account_id for a in Registry(conn).confirmed()]
    finally:
        conn.close()
    with Feed.open(fresh.settings.feed_database) as feed:
        return [row for a in account_ids for row in feed.transactions(a)]


def _row(fresh: FirstDay, name: str, day: date) -> PlaidTransaction:
    return next(r for r in _feed_rows(fresh) if r.name == name and r.day == day)


def _item(fresh: FirstDay, institution: str = "Willowmere Trust") -> str:
    conn = fresh.connect()
    try:
        return next(i.item_id for i in Registry(conn).all_items() if i.institution == institution)
    finally:
        conn.close()


def _sync(fresh: FirstDay, item_id: str) -> None:
    """Press "Sync now", as Avery would."""
    avery = fresh.signed_in("avery")
    response = avery.post(f"/connect/items/{item_id}/sync", data={"csrf": fresh.csrf(avery)})
    assert response.status_code == 303


# Every posted row, once.


def test_every_posted_row_is_in_the_books_once_and_a_pending_one_not_at_all(
    filed: FirstDay,
) -> None:
    found = filed.books()
    rows = _feed_rows(filed)
    booked = Counter(
        source.rsplit(":", 1)[1] for e in _transactions(found.entries) for source in _sources(e)
    )

    posted = {r.transaction_id for r in rows if not r.pending}
    assert set(booked) == posted
    assert set(booked.values()) == {1}
    pending = next(r for r in rows if r.pending)
    assert pending.transaction_id not in booked
    assert all(e.flag in {"!", "*"} for e in _transactions(found.entries))


def test_the_import_books_exactly_the_same_entries_every_time_it_runs(filed: FirstDay) -> None:
    before = filed.books()

    filed.bookkeeper.recompute(wait=True)
    _sync(filed, _item(filed))
    filed.bookkeeper.recompute(wait=True)

    assert filed.books().entries == before.entries


def test_amounts_are_the_banks_to_the_cent_and_signed_as_the_account_moved(
    filed: FirstDay,
) -> None:
    found = filed.books()
    (grocer,) = [e for e in _on(found, PATHS["6616"]) if e.payee == "Larkspur Grocer"]
    (payroll,) = [
        e
        for e in _on(found, PATHS["6616"])
        if e.payee == "Payroll Wren & Co"
        and e.date.day == 1
        and e.date.month == 9
        and e.date.year == 2026
    ]
    coffee = next(e for e in _on(found, PATHS["1111"]) if e.payee == "Tinderbox Coffee")

    assert _number(grocer.postings[0]) == Decimal("-64.20")
    assert _number(payroll.postings[0]) == Decimal("2140.00")
    # A purchase on a card adds to what is owed, which a Liabilities account holds as negative.
    assert _number(coffee.postings[0]) == Decimal("-5.75")
    for entry in _transactions(found.entries):
        for posting in entry.postings:
            assert isinstance(_number(posting), Decimal)
            assert _currency(posting) == "USD"


# What each row is proposed as.


def test_each_row_is_flagged_against_what_its_category_proposes(filed: FirstDay) -> None:
    found = filed.books()
    by_payee = {e.payee: e for e in _on(found, PATHS["6616"]) if e.flag == "!"}

    assert by_payee["Larkspur Grocer"].postings[1].account == "Expenses:FoodAndDrink:Groceries"
    assert by_payee["Larkspur Grocer"].meta["review-group"] == "Food and drink: groceries"
    assert by_payee["Payroll Wren & Co"].postings[1].account == "Income:Wages"
    # A transfer says where the money went, not what for: a member says.
    assert by_payee["Transfer to Hollins Savings"].postings[1].account == UNRESOLVED
    pile = {item.review_id: item for item in found.pile}
    transfer = pile[by_payee["Transfer to Hollins Savings"].meta["review-id"]]
    assert transfer.proposed is None
    assert transfer.group == "Transfer out: account transfer"


@pytest.mark.parametrize(
    ("category", "proposed", "group"),
    [
        ("FOOD_AND_DRINK_COFFEE", "Expenses:FoodAndDrink:Coffee", "Food and drink: coffee"),
        ("INCOME_INTEREST_EARNED", "Income:InterestEarned", "Income: interest earned"),
        ("RENT_AND_UTILITIES_RENT", "Expenses:RentAndUtilities:Rent", "Rent and utilities: rent"),
        ("TRANSFER_IN_DEPOSIT", UNRESOLVED, "Transfer in: deposit"),
        ("LOAN_PAYMENTS_CAR_PAYMENT", UNRESOLVED, "Loan payments: car payment"),
        (
            "LOAN_DISBURSEMENTS_OTHER_DISBURSEMENT",
            UNRESOLVED,
            "Loan disbursements: other disbursement",
        ),
        ("OTHER_OTHER", UNRESOLVED, "Other"),
        ("BANK_FEES_ATM_FEES", "Expenses:BankFees:ATMFees", "Bank fees: ATM fees"),
        (
            "ENTERTAINMENT_TV_AND_MOVIES",
            "Expenses:Entertainment:TVAndMovies",
            "Entertainment: TV and movies",
        ),
        ("SOMETHING_PLAID_ADDS_LATER", UNRESOLVED, "Uncategorized"),
        ("FOOD_AND_DRINK_", UNRESOLVED, "Uncategorized"),
        (None, UNRESOLVED, "Uncategorized"),
    ],
)
def test_a_category_proposes_an_account_or_honestly_nothing(
    category: str | None, proposed: str, group: str
) -> None:
    assert booking.proposal(category) == proposed
    assert booking.group(category) == group


# A payment from the bank account to a card.


def test_a_payment_from_the_bank_account_to_a_connected_card_is_one_transfer(
    filed: FirstDay,
) -> None:
    found = filed.books()
    bank = _row(filed, "Payment to Willowmere card ending in 1111", PAID)
    card = _row(filed, "Payment Thank You", PAID + timedelta(days=1))

    (transfer,) = [
        e for e in _transactions(found.entries) if bank.transaction_id in str(_sources(e))
    ]
    assert transfer.flag == "*"
    assert transfer.date == card.day
    # The entry is on both accounts, which may be two members': neither bank's words cross.
    assert (transfer.payee, transfer.narration) == (None, booking.CARD_PAYMENT)
    assert [(p.account, _number(p)) for p in transfer.postings] == [
        (PATHS["6616"], Decimal("-300.00")),
        (PATHS["1111"], Decimal("300.00")),
    ]
    assert [item_of(_source(p)) for p in transfer.postings] == [_item(filed)] * 2
    assert {_source(p).rsplit(":", 1)[1] for p in transfer.postings} == {
        bank.transaction_id,
        card.transaction_id,
    }
    # Neither side waits for review, and neither is spending or income.
    assert "review-id" not in transfer.meta
    ids = {plaid_review_id(bank.transaction_id), plaid_review_id(card.transaction_id)}
    assert not ids & {item.review_id for item in found.pile}
    assert not [
        e
        for e in _transactions(found.entries)
        if card.transaction_id in str(_sources(e)) and e is not transfer
    ]


def test_a_payment_to_a_card_nobody_connected_waits_as_a_transfer_pending(
    filed: FirstDay,
) -> None:
    found = filed.books()
    leg = _row(filed, "Payment to Fenwick card ending in 9090", date(2026, 9, 12))

    (entry,) = [
        e for e in _transactions(found.entries) if e.meta["source"].endswith(leg.transaction_id)
    ]
    assert entry.flag == "!"
    assert entry.meta["review-group"] == pairing.GROUP
    assert [(p.account, _number(p)) for p in entry.postings] == [
        (PATHS["6616"], Decimal("-120.00")),
        (TRANSFER_PENDING, Decimal("120.00")),
    ]
    (item,) = [i for i in found.pile if i.review_id == entry.meta["review-id"]]
    assert item.proposed is None
    assert item.transfer


def test_review_counts_a_leg_whose_other_side_hearth_does_not_see_apart_from_what_is_left(
    filed: FirstDay,
) -> None:
    """One side of a payment whose other side hearth has not found is not a guess to say yes
    to: it is asked where its money went, on a page of its own, with every row like it. What
    is left in the queue is only the rest; the count on every page is every row waiting."""
    avery = filed.signed_in("avery")
    found = filed.books()
    (leg,) = [i for i in found.pile if i.payee == "Payment to Fenwick card ending in 9090"]
    one_sided = [i for i in found.pile if i.transfer or (moved(i.group) and not i.proposed)]
    actionable = len(found.pile) - len(one_sided)

    page = avery.get("/review").text
    assert f'<p class="hero-value">{actionable:,}</p>' in page
    assert f'aria-label="{len(found.pile):,} to review"' in page
    assert (
        f'<a href="/review/where">{len(one_sided)} more need to know where the money went</a>'
        in page
    )
    assert f'id="r-{leg.review_id}"' not in page
    assert "Needs the other side" not in page
    where = avery.get("/review/where").text
    assert "Card payments hearth could not match" in where
    assert leg.payee is not None
    assert leg.payee in where
    assert "other side not connected" not in where  # nothing says why it was not found
    for path in ("/", "/you"):
        assert f'aria-label="{len(found.pile):,} to review"' in avery.get(path).text


def test_a_leg_that_needs_its_other_side_can_be_booked_and_leaves_the_count(
    filed: FirstDay,
) -> None:
    avery = filed.signed_in("avery")
    (leg,) = [i for i in filed.books().pile if i.payee == "Payment to Fenwick card ending in 9090"]
    booked = avery.post(
        "/review/rows/book",
        data={"csrf": filed.csrf(avery), "review_id": leg.review_id, "account": "Expenses:Misc"},
    )
    assert booked.status_code == 303
    after = avery.get("/review/where").text
    assert leg.payee is not None
    assert leg.payee not in after
    assert f'id="r-{leg.review_id}"' not in avery.get("/review").text


def test_an_account_page_sends_a_leg_that_needs_its_other_side_to_where_it_is_counted(
    filed: FirstDay,
) -> None:
    avery = filed.signed_in("avery")
    found = filed.books()
    (leg,) = [i for i in found.pile if i.payee == "Payment to Fenwick card ending in 9090"]
    conn = filed.connect()
    try:
        (on,) = [a for a in accounts.all_accounts(conn) if a.ledger_account == leg.account]
    finally:
        conn.close()
    page = avery.get(f"/accounts/{on.id}?month={leg.date:%Y-%m}").text
    tags = re.findall(
        r'<span class="primary">([^<]+)</span> <a class="tag tag-review" href="([^"]+)">', page
    )
    assert (leg.payee, "/review/where") in tags
    others = {payee: href for payee, href in tags if payee != leg.payee}
    assert others == {
        payee: "/review/where" if payee.startswith("Transfer to") else "/review" for payee in others
    }


def test_you_names_the_biggest_few_groups_waiting_and_counts_the_rest(filed: FirstDay) -> None:
    found = filed.books()
    sizes = Counter(
        i.group for i in found.pile if not (i.transfer or (moved(i.group) and not i.proposed))
    )
    assert len(sizes) > 4
    card = re.search(
        r'<section class="panel review-card".*?</section>',
        filed.signed_in("avery").get("/you").text,
        re.S,
    )
    assert card is not None
    listed = re.findall(
        r'<li><span class="grow">([^<]+)</span><span class="end tnum">([\d,]+)</span>', card[0]
    )
    # Money hearth sees one side of is its own line, which leads to where it is answered.
    assert '<a href="/review/where">Where did it go?</a>' in card[0]
    assert [(unescape(name), int(n.replace(",", ""))) for name, n in listed] == [
        (name, n) for name, n in sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0]))[:4]
    ]
    assert pairing.GROUP not in card[0]
    assert f"And {len(sizes) - 4} more group" in card[0]


def test_review_offers_no_exclude_on_a_row_its_bank_counts(filed: FirstDay) -> None:
    """Every account here is the bank's balance, so the desk refuses to leave any row out; the
    queue offers what it would refuse on none of them."""
    avery = filed.signed_in("avery")
    page = avery.get("/review?view=rows").text
    rows = re.findall(r'<tr id="r-[^"]+" data-row.*?</tr>', page, flags=re.DOTALL)
    assert rows
    assert not [row for row in rows if "data-exclude" in row]
    assert [row for row in rows if ">Accept<" in row]  # what it does offer is still there


def test_review_finds_a_row_by_its_merchant_in_any_case(filed: FirstDay) -> None:
    avery = filed.signed_in("avery")
    page = avery.get("/review?view=rows&q=++LARKSPUR++grocer").text
    rows = re.findall(r'<tr id="r-[^"]+" data-row.*?</tr>', page, flags=re.DOTALL)
    assert len(rows) == 1
    assert "Larkspur Grocer" in rows[0]
    assert 'value="LARKSPUR grocer"' in page  # the search stays in its box, tidied
    assert "matching &ldquo;LARKSPUR grocer&rdquo;" in page


def _review_row(page: str, review_id: str) -> str:
    found = re.search(rf'<tr id="r-{review_id}" data-row.*?</tr>', page, flags=re.DOTALL)
    assert found is not None, review_id
    return found[0]


def test_a_card_connected_without_its_side_of_a_payment_leaves_the_payment_waiting(
    filed: FirstDay,
) -> None:
    filed.link("avery", "ins_fenwick")
    filed.map(filed.signed_in("avery"), {"9090": FENWICK})

    found = until(filed, lambda b: _on(b, FENWICK))

    leg = _row(filed, "Payment to Fenwick card ending in 9090", date(2026, 9, 12))
    (entry,) = [e for e in _transactions(found.entries) if leg.transaction_id in str(_sources(e))]
    # The card's own feed sent no payment row for it, so its other side is still not in the
    # books: still a leg, never booked as spending.
    assert entry.postings[1].account == TRANSFER_PENDING
    # Both sides' banks are connected now, so where it waits claims nothing about either.
    (waiting,) = [i for i in found.pile if i.payee == leg.name]
    assert leg.name is not None
    page = filed.signed_in("avery").get("/review/where").text
    group = re.search(
        r'<section class="panel rgroup[^>]*>(?:(?!</section>).)*?'
        + re.escape(leg.name)
        + ".*?</section>",
        page,
        re.S,
    )
    assert group is not None, waiting.review_id
    asked = group[0].split("<fieldset")[0]
    assert "not connected" not in asked
    assert "does not see" not in asked


def test_once_the_card_and_its_side_are_connected_the_payment_is_one_transfer(
    tmp_path: Path, clock: FakeClock
) -> None:
    fresh = first_day(tmp_path, clock, institutions=(SHAPE, PAID_CARD_BANK))
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), PATHS)
    fresh.link("avery", "ins_fenwick_paid")
    fresh.map(fresh.signed_in("avery"), {"9090": FENWICK})

    found = until(fresh, lambda b: _on(b, FENWICK))

    (transfer,) = _on(found, FENWICK)
    assert transfer.flag == "*"
    assert [(p.account, _number(p)) for p in transfer.postings] == [
        (PATHS["6616"], Decimal("-120.00")),
        (FENWICK, Decimal("120.00")),
    ]
    # Each side is its own connection's row, so the card's connection vouches for the card.
    fenwick = _item(fresh, "Fenwick Card")
    assert [item_of(_source(p)) for p in transfer.postings] == [_item(fresh), fenwick]
    assert found.synced[FENWICK] == found.as_of


# Every entry names its connection, which turns coverage's rule on.


def test_every_entry_names_the_connection_it_was_booked_from(filed: FirstDay) -> None:
    found = filed.books()
    item_id = _item(filed)

    for entry in _transactions(found.entries):
        assert entry.meta["source"].startswith("plaid:")
        assert {item_of(source) for source in _sources(entry)} == {item_id}
    assert set(found.synced) == set(PATHS.values())


def test_a_quiet_card_on_a_connection_that_still_syncs_holds_its_months(filed: FirstDay) -> None:
    found = filed.books()
    conn = filed.connect()
    try:
        held = accounts.all_accounts(conn)
    finally:
        conn.close()

    assert PATHS["2212"] in found.synced
    for month in (Month(2026, 6), Month(2026, 9)):
        assert figures.flows_cover(found, held, month), month


def test_a_card_whose_connection_stopped_no_longer_claims_months_it_does_not_hold(
    filed: FirstDay,
) -> None:
    """The defect coverage was built against: one current account made every account in its
    part look current. Fenwick's card stopped sending after May; the checking account is
    current; spending after May must read as incomplete, which it does only because the import
    names the connection each entry came from."""
    filed.clock.now = datetime(2026, 6, 2, 12, tzinfo=UTC)
    filed.link("avery", "ins_fenwick")
    filed.map(filed.signed_in("avery"), {"9090": FENWICK})
    until(filed, lambda b: _on(b, FENWICK))
    fenwick = _item(filed, "Fenwick Card")
    filed.clock.now = household.NOW
    filed.plaid.require_login(fenwick)
    _sync(filed, fenwick)
    conn = filed.connect()
    try:
        assert Registry(conn).item(fenwick).state is ItemState.LOGIN_REQUIRED  # type: ignore[union-attr]
        held = accounts.all_accounts(conn)
    finally:
        conn.close()
    filed.bookkeeper.recompute(wait=True)
    found = filed.books()

    assert found.synced[FENWICK] == date(2026, 6, 2)
    assert figures.flows_cover(found, held, Month(2026, 5))
    assert not figures.flows_cover(found, held, Month(2026, 6))
    assert not figures.flows_cover(found, held, Month(2026, 9))


# Plaid revises and withdraws what it sent.


def test_a_revised_row_is_booked_as_it_now_stands_and_a_withdrawn_one_not_at_all(
    filed: FirstDay,
) -> None:
    item_id = _item(filed)
    grocer = _row(filed, "Larkspur Grocer", date(2026, 9, 20))
    coffee = _row(filed, "Tinderbox Coffee", date(2026, 9, 3))
    revised = FakeTransaction(
        "Larkspur Grocer", Decimal("71.80"), date(2026, 9, 21), "FOOD_AND_DRINK_GROCERIES"
    )
    filed.plaid.change(item_id, "modified", revised, "chk", transaction_id=grocer.transaction_id)
    gone = FakeTransaction("Tinderbox Coffee", Decimal("5.75"), coffee.day)
    filed.plaid.change(item_id, "removed", gone, "c1", transaction_id=coffee.transaction_id)

    _sync(filed, item_id)
    found = until(
        filed,
        lambda b: (
            not [e for e in _transactions(b.entries) if coffee.transaction_id in str(_sources(e))]
        ),
    )

    (entry,) = [
        e for e in _transactions(found.entries) if grocer.transaction_id in str(_sources(e))
    ]
    assert (entry.date, _number(entry.postings[0])) == (date(2026, 9, 21), Decimal("-71.80"))
    assert entry.meta["review-id"] == plaid_review_id(grocer.transaction_id)


def _rule(fresh: FirstDay, review_id: str, account: str, *, shown: str = "") -> None:
    avery = fresh.signed_in("avery")
    response = avery.post(
        "/review/rows/book",
        data={
            "csrf": fresh.csrf(avery),
            "review_id": review_id,
            "account": account,
            "shown": shown,
        },
    )
    assert response.status_code == 303
    assert "changed" not in response.headers["location"]


def _reject(fresh: FirstDay, review_id: str) -> None:
    avery = fresh.signed_in("avery")
    response = avery.post(
        "/review/rows/reject", data={"csrf": fresh.csrf(avery), "review_id": review_id}
    )
    assert response.status_code == 303


def _held(found: Books, account: str) -> Decimal:
    """What the books say `account` holds today."""
    return sum(
        (
            _number(p)
            for e in found.entries
            if isinstance(e, Transaction)
            for p in e.postings
            if p.account == account and e.date <= found.as_of
        ),
        Decimal(0),
    )


def _opened(found: Books) -> set[str]:
    return {e.account for e in found.entries if isinstance(e, Open)}


def test_a_row_moves_to_nobody_elses_account_and_the_refusal_names_nobody(
    filed: FirstDay,
) -> None:
    """Avery types Blake's real card, then an account nobody connected: each is refused the
    same way, so typing names cannot tell whose accounts exist, and the books stay whole."""
    filed.link("blake", "ins_fenwick")
    blakes = "Liabilities:Blake:Fenwick:Rewards"
    filed.map(filed.signed_in("blake"), {"9090": blakes}, owner="blake")
    before = until(filed, lambda b: blakes in _opened(b))
    transfer = _row(filed, "Transfer to Hollins Savings", date(2026, 9, 14))
    review_id = plaid_review_id(transfer.transaction_id)

    avery = filed.signed_in("avery")
    answers = {}
    for account in (blakes, "Liabilities:Avery:Mortgage"):
        response = avery.post(
            "/review/rows/book",
            data={"csrf": filed.csrf(avery), "review_id": review_id, "account": account},
        )
        assert response.status_code == 303
        location = response.headers["location"]
        answers[account] = (location, shown(avery.get(location).text))

    assert answers[blakes] == answers["Liabilities:Avery:Mortgage"]
    location, page = answers[blakes]
    assert "outcome=not-yours" in location
    assert "one of your own accounts in hearth" in page
    assert "Blake" not in page
    conn = filed.connect()
    try:
        recorded = conn.execute(
            "SELECT count(*) FROM review_ruling_rows WHERE review_id = ?", (review_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert recorded == 0
    filed.bookkeeper.recompute(wait=True)
    assert filed.books().entries == before.entries
    entries, errors = written(filed.settings)
    assert errors == []
    assert UNRESOLVED in {p.account for e in _transactions(entries) for p in e.postings}


HAND_KEPT = "Liabilities:Avery:Mortgage"
"""A mortgage nobody connects, which the household keeps in a ledger of its own."""


def test_a_row_moves_to_an_account_of_the_members_own(tmp_path: Path, clock: FakeClock) -> None:
    """A transfer out of checking is the mortgage payment, and the mortgage is kept by hand:
    nothing asserts its balance, so the row moves there and the books still agree with the
    bank about checking."""
    ledger = tmp_path / "household.beancount"
    ledger.write_text(f'option "operating_currency" "USD"\n2024-01-01 open {HAND_KEPT}\n')
    fresh = first_day(tmp_path, clock, institutions=(SHAPE,), inherits=ledger)
    conn = fresh.connect()
    try:
        accounts.register(
            conn, ledger_account=HAND_KEPT, name="Mortgage", owners=[fresh.member_ids["avery"]]
        )
    finally:
        conn.close()
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), PATHS)
    until(fresh, lambda b: _on(b, PATHS["6616"]))
    transfer = _row(fresh, "Transfer to Hollins Savings", date(2026, 9, 14))
    review_id = plaid_review_id(transfer.transaction_id)

    _rule(fresh, review_id, HAND_KEPT)
    found = until(
        fresh,
        lambda b: any(
            e.flag == "*" for e in _on(b, PATHS["6616"]) if e.meta.get("review-id") == review_id
        ),
    )

    assert fresh.bookkeeper.status.failure is None
    (entry,) = [e for e in _on(found, PATHS["6616"]) if e.meta.get("review-id") == review_id]
    assert entry.postings[1].account == HAND_KEPT
    assert _held(found, HAND_KEPT) == Decimal(500)
    assert _held(found, PATHS["6616"]) == Decimal(4130)
    _, errors = written(fresh.settings)
    assert errors == []


def test_a_row_never_moves_onto_an_account_whose_balance_its_bank_reports(
    filed: FirstDay,
) -> None:
    """Savings is Avery's own and open, but its bank reports its balance: what is in it is the
    bank's to say. Moving checking's transfer there would count $500 the bank says savings
    never received, and stall every member's figures, so the desk refuses it and says why."""
    transfer = _row(filed, "Transfer to Hollins Savings", date(2026, 9, 14))
    before = filed.books()
    avery = filed.signed_in("avery")

    response = avery.post(
        "/review/rows/book",
        data={
            "csrf": filed.csrf(avery),
            "review_id": plaid_review_id(transfer.transaction_id),
            "account": PATHS["7717"],
        },
    )

    assert response.status_code == 303
    assert "outcome=banks-account" in response.headers["location"]
    assert "only the bank's own transactions go in it" in shown(
        avery.get(response.headers["location"]).text
    )
    filed.bookkeeper.recompute(wait=True)
    assert filed.bookkeeper.status.failure is None
    assert filed.books().entries == before.entries
    conn = filed.connect()
    try:
        assert RulingStore(conn).accounts() == set()
    finally:
        conn.close()


def _on_record(fresh: FirstDay, review_id: str, action: Action, account: str | None) -> None:
    """A ruling already on record, as one made before the desk refused it would be."""
    (item,) = [i for i in fresh.books().pile if i.review_id == review_id]
    conn = fresh.connect()
    try:
        RulingStore(conn).record(
            member_id=fresh.member_ids["avery"],
            scope=Scope.ROW,
            group=None,
            action=action,
            account=account,
            note=None,
            rows={review_id: None},
            says={review_id: item.says},
            at=fresh.clock(),
        )
    finally:
        conn.close()


def test_a_ruling_on_record_that_moves_a_row_onto_a_banks_account_fails_the_balance_check(
    filed: FirstDay,
) -> None:
    """Were such a ruling on record, the books would show savings holding $500 more than its
    bank says: they are wrong, and the recompute says so rather than serve the figure."""
    transfer = _row(filed, "Transfer to Hollins Savings", date(2026, 9, 14))
    before = filed.books()

    _on_record(filed, plaid_review_id(transfer.transaction_id), Action.ACCOUNT, PATHS["7717"])
    filed.bookkeeper.recompute(wait=True)

    status = filed.bookkeeper.status
    assert status.books == before
    assert status.failure is not None
    check = status.failure.disagreement
    assert check is not None
    assert (check.account, check.asserted, check.computed) == (
        PATHS["7717"],
        Decimal(8800),
        Decimal(9300),
    )
    page = shown(filed.signed_in("avery").get("/net-worth").text)
    assert "add up to more than Willowmere Trust says this account held" in page
    assert re.search(r"Willowmere Trust says\s*\$8,800\.00 at the end of Oct 15", page)
    assert re.search(r"Hearth adds up to\s*\$9,300\.00", page)
    assert "Today's figures are waiting on a fix" in shown(filed.signed_in("blake").get("/").text)


@pytest.mark.parametrize(
    ("account", "says"),
    [
        ("Expenses:Household", "Saved under the category you chose."),
        (PATHS["7717"], "only the bank's own transactions go in it"),
    ],
)
def test_the_page_says_where_a_row_went(filed: FirstDay, account: str, says: str) -> None:
    transfer = _row(filed, "Transfer to Hollins Savings", date(2026, 9, 14))
    avery = filed.signed_in("avery")
    response = avery.post(
        "/review/rows/book",
        data={
            "csrf": filed.csrf(avery),
            "review_id": plaid_review_id(transfer.transaction_id),
            "account": account,
        },
    )

    assert response.status_code == 303
    page = shown(avery.get(response.headers["location"]).text)
    assert says in page


def test_a_row_moves_to_no_account_of_the_members_own_before_the_books_open_it(
    tmp_path: Path, clock: FakeClock
) -> None:
    """A card connected later opens later: a checking row from before that day cannot move
    to it, is refused like any account that is not the member's, and the books stay whole."""
    fresh = first_day(tmp_path, clock, institutions=(SHAPE, CARD_BANK))
    clock.now = household.NOW - timedelta(days=60)
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), PATHS)
    until(fresh, lambda b: _on(b, PATHS["6616"]))
    clock.now = household.NOW
    card = "Liabilities:Avery:Fenwick:Rewards"
    fresh.link("avery", "ins_fenwick")
    fresh.map(fresh.signed_in("avery"), {"9090": card})
    before = until(fresh, lambda b: card in _opened(b))
    (opens,) = [e.date for e in before.entries if isinstance(e, Open) and e.account == card]
    early = next(
        e
        for e in sorted(_on(before, PATHS["6616"]), key=lambda e: e.date)
        if e.flag == "!" and e.date < opens
    )

    avery = fresh.signed_in("avery")
    response = avery.post(
        "/review/rows/book",
        data={"csrf": fresh.csrf(avery), "review_id": early.meta["review-id"], "account": card},
    )

    assert response.status_code == 303
    assert "outcome=not-yours" in response.headers["location"]
    fresh.bookkeeper.recompute(wait=True)
    assert fresh.books().entries == before.entries
    _, errors = written(fresh.settings)
    assert errors == []


@pytest.mark.parametrize("action", ["accept", "book elsewhere"])
def test_a_ruling_on_a_row_its_bank_has_since_changed_comes_back_for_review(
    filed: FirstDay, action: str
) -> None:
    item_id = _item(filed)
    grocer = _row(filed, "Larkspur Grocer", date(2026, 9, 20))
    review_id = plaid_review_id(grocer.transaction_id)
    proposed = "Expenses:FoodAndDrink:Groceries"
    if action == "accept":
        _rule(filed, review_id, proposed, shown=proposed)
    else:
        _rule(filed, review_id, "Expenses:Household")
    until(
        filed,
        lambda b: any(
            e.flag == "*" for e in _on(b, PATHS["6616"]) if review_id == e.meta.get("review-id")
        ),
    )

    tripled = FakeTransaction(
        "Larkspur Grocer", Decimal("192.60"), grocer.day, "FOOD_AND_DRINK_GROCERIES"
    )
    filed.plaid.change(item_id, "modified", tripled, "chk", transaction_id=grocer.transaction_id)
    _sync(filed, item_id)
    found = until(
        filed,
        lambda b: any(
            _number(e.postings[0]) == Decimal("-192.60")
            for e in _on(b, PATHS["6616"])
            if e.meta.get("review-id") == review_id
        ),
    )

    (entry,) = [e for e in _on(found, PATHS["6616"]) if e.meta.get("review-id") == review_id]
    # Neither kept as checked nor dropped: back as proposed, unchecked, saying why.
    assert entry.flag == "!"
    assert entry.postings[1].account == proposed
    page = shown(filed.signed_in("avery").get("/review?view=rows&order=confidence").text)
    assert "came back because the bank changed it after it was reviewed" in page
    assert "Changed by the bank since it was reviewed" in page
    conn = filed.connect()
    try:
        recorded = conn.execute(
            "SELECT count(*) FROM review_ruling_rows WHERE review_id = ?", (review_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert recorded == 1


def test_a_ruling_holds_while_the_bank_only_tidies_how_a_row_is_described(
    filed: FirstDay,
) -> None:
    item_id = _item(filed)
    grocer = _row(filed, "Larkspur Grocer", date(2026, 9, 20))
    review_id = plaid_review_id(grocer.transaction_id)
    _rule(filed, review_id, "Expenses:Household")
    until(
        filed,
        lambda b: any(
            e.flag == "*" for e in _on(b, PATHS["6616"]) if review_id == e.meta.get("review-id")
        ),
    )

    tidied = FakeTransaction(
        "Larkspur Grocer #114", grocer.amount, grocer.day, "FOOD_AND_DRINK_SUPERMARKETS"
    )
    filed.plaid.change(item_id, "modified", tidied, "chk", transaction_id=grocer.transaction_id)
    _sync(filed, item_id)
    found = until(
        filed,
        lambda b: any(e.payee == "Larkspur Grocer #114" for e in _on(b, PATHS["6616"])),
    )

    (entry,) = [e for e in _on(found, PATHS["6616"]) if e.meta.get("review-id") == review_id]
    assert entry.flag == "*"
    assert entry.postings[1].account == "Expenses:Household"


# Taking each transaction once.


def test_a_bank_reached_through_two_connections_books_nothing_and_says_why(
    filed: FirstDay,
) -> None:
    before = filed.books()
    filed.link("avery", "ins_willowmere")
    conn = filed.connect()
    try:
        registry = Registry(conn)
        second = [i for i in registry.all_items() if i.institution == "Willowmere Trust"][-1]
        target = {a.mask: a.hearth_account for a in registry.confirmed()}
        for account in registry.accounts_of(second.item_id):
            registry.confirm(
                account.account_id,
                target[account.mask] or "",
                by=filed.member_ids["avery"],
                now=filed.clock(),
            )
    finally:
        conn.close()

    filed.bookkeeper.recompute(wait=True)

    status = filed.bookkeeper.status
    assert status.failure is not None
    assert status.failure.summary == booking.TWICE
    # The books established before stay, never a double count.
    assert status.books is before
    avery = filed.signed_in("avery")
    assert booking.TWICE in avery.get("/").text

    # What the page says to do works: removing either connection is the way out.
    removed = avery.post(
        f"/connect/items/{second.item_id}/remove", data={"csrf": filed.csrf(avery)}
    )
    assert removed.status_code == 303
    assert "outcome=removed" in removed.headers["location"]
    # Removing starts the recompute itself; nothing else is pressed.
    deadline = time.monotonic() + 10
    while filed.bookkeeper.status.failure is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert filed.bookkeeper.status.failure is None
    assert filed.books().entries == before.entries


def _reconnect(fresh: FirstDay, client: TestClient) -> None:
    """Map a bank connected again into the accounts it fed before, as a member picks them."""
    conn = fresh.connect()
    try:
        registry = Registry(conn)
        newest = registry.all_items()[-1]
        found = registry.accounts_of(newest.item_id)
    finally:
        conn.close()
    data: dict[str, str | list[str]] = {
        "csrf": fresh.csrf(client),
        "account": [a.account_id for a in found],
        "near": [],
    }
    for account in found:
        data |= {
            f"target-{account.account_id}": PATHS[account.mask or ""],
            f"owners-{account.account_id}": str(fresh.member_ids["avery"]),
            f"confirm-{account.account_id}": "1",
        }
    response = client.post("/connect/map", data=data)
    if response.status_code == 400:
        # Confirmed again with the page's warning about names a word apart in view.
        data["near"] = [
            unescape(v)
            for v in re.findall(r'<input type="hidden" name="near" value="([^"]+)">', response.text)
        ]
        response = client.post("/connect/map", data=data)
    assert response.status_code == 303, response.text


def test_a_bank_removed_and_connected_again_books_each_transaction_once(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Plaid sends the whole history again under new ids: the books take it from the new
    connection, and keep from the old one only what came before the new history starts."""
    fresh = first_day(tmp_path, clock, institutions=(SHAPE,))
    clock.now = household.NOW - timedelta(days=40)
    fresh.link("avery", "ins_willowmere")
    avery = fresh.signed_in("avery")
    fresh.map(avery, PATHS)
    until(fresh, lambda b: _on(b, PATHS["6616"]))
    old = _item(fresh)
    avery = fresh.signed_in("avery")
    assert (
        avery.post(f"/connect/items/{old}/remove", data={"csrf": fresh.csrf(avery)}).status_code
        == 303
    )

    clock.now = household.NOW
    fresh.link("avery", "ins_willowmere")
    avery = fresh.signed_in("avery")
    _reconnect(fresh, avery)
    fresh.bookkeeper.recompute(wait=True)

    assert fresh.bookkeeper.status.failure is None
    found = fresh.books()
    booked = Counter(
        (p.account, e.date, _number(p))
        for e in _transactions(found.entries)
        for p in e.postings
        if p.account in PATHS.values()
    )
    # The same history twice over would book every payroll twice; each is here once.
    payroll = [key for key in booked if key[0] == PATHS["6616"] and key[2] == Decimal("2140.00")]
    assert payroll
    assert all(booked[key] == 1 for key in payroll)
    # The weeks before the new connection's history are the old one's, and still here; from
    # the day it starts, every row is the new one's.
    conn = fresh.connect()
    try:
        (new,) = [i.item_id for i in Registry(conn).all_items() if i.active]
    finally:
        conn.close()
    starts = min(e.date for e in _transactions(found.entries) if item_of(e.meta["source"]) == new)
    for entry in _transactions(found.entries):
        assert {item_of(s) for s in _sources(entry)} == {old if entry.date < starts else new}
    assert min(e.date for e in _on(found, PATHS["6616"])) < starts


def test_a_bank_removed_and_connected_again_twice_books_each_transaction_once(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Each connection gives way to every one linked after it, not only to the working one:
    two removed connections hold the same weeks, and those are booked once."""
    fresh = first_day(tmp_path, clock, institutions=(SHAPE,))
    # A month apart each, so each history starts on a different one of the bank's months.
    clock.now = household.NOW - timedelta(days=30)
    fresh.link("avery", "ins_willowmere")
    avery = fresh.signed_in("avery")
    fresh.map(avery, PATHS)
    linked = [_item(fresh)]
    until(fresh, lambda b: _on(b, PATHS["6616"]))
    for at in (household.NOW + timedelta(days=10), household.NOW + timedelta(days=40)):
        avery = fresh.signed_in("avery")
        assert (
            avery.post(
                f"/connect/items/{linked[-1]}/remove", data={"csrf": fresh.csrf(avery)}
            ).status_code
            == 303
        )
        clock.now = at
        fresh.link("avery", "ins_willowmere")
        avery = fresh.signed_in("avery")
        _reconnect(fresh, avery)
        conn = fresh.connect()
        try:
            linked.append(Registry(conn).all_items()[-1].item_id)
        finally:
            conn.close()
        fresh.bookkeeper.recompute(wait=True)
    assert fresh.bookkeeper.status.failure is None
    found = fresh.books()

    booked = Counter(
        (p.account, e.date, _number(p))
        for e in _transactions(found.entries)
        for p in e.postings
        if p.account in PATHS.values()
    )
    assert all(count == 1 for count in booked.values())
    # Every connection stands for the weeks from its own history's start to the next one's.
    first, second, working = linked
    starts = {
        item: min(e.date for e in _transactions(found.entries) if item_of(e.meta["source"]) == item)
        for item in (second, working)
    }
    assert min(e.date for e in _transactions(found.entries)) < starts[second] < starts[working]
    for entry in _transactions(found.entries):
        expected = (
            first
            if entry.date < starts[second]
            else second
            if entry.date < starts[working]
            else working
        )
        assert {item_of(s) for s in _sources(entry)} == {expected}


def _remove(fresh: FirstDay, username: str, item_id: str) -> None:
    client = fresh.signed_in(username)
    removed = client.post(f"/connect/items/{item_id}/remove", data={"csrf": fresh.csrf(client)})
    assert removed.status_code == 303


def _newest(fresh: FirstDay) -> dict[str, str]:
    """The newest connection's accounts, by their last digits."""
    conn = fresh.connect()
    try:
        registry = Registry(conn)
        return {
            a.mask or "": a.account_id
            for a in registry.accounts_of(registry.all_items()[-1].item_id)
        }
    finally:
        conn.close()


def _defaults(page: str) -> dict[str, str]:
    """What the mapping page has chosen for each account before the member touches it."""
    found = {}
    for account_id, options in re.findall(
        r'<select [^>]*name="target-([^"]+)"[^>]*>(.*?)</select>', page, flags=re.DOTALL
    ):
        (chosen,) = re.findall(r'<option value="([^"]+)"[^>]* selected', options)
        found[account_id] = unescape(chosen)
    return found


def test_a_bank_connected_again_is_filed_where_it_went_before_by_default(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Removed and connected again, every account defaults to the one it fed before, so
    keeping the suggestions keeps each account's whole history in one place, booked once."""
    fresh = first_day(tmp_path, clock, institutions=(SHAPE,))
    clock.now = household.NOW - timedelta(days=40)
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), PATHS)
    until(fresh, lambda b: _on(b, PATHS["6616"]))
    _remove(fresh, "avery", _item(fresh))
    clock.now = household.NOW
    fresh.link("avery", "ins_willowmere")

    avery = fresh.signed_in("avery")
    page = avery.get("/connect/map").text
    defaults = _defaults(page)
    ids = _newest(fresh)
    assert {mask: defaults[account_id] for mask, account_id in ids.items()} == PATHS

    data: dict[str, str | list[str]] = {
        "csrf": fresh.csrf(avery),
        "account": list(ids.values()),
        "near": [],
    }
    for account_id in ids.values():
        data |= {f"target-{account_id}": defaults[account_id], f"confirm-{account_id}": "1"}
    response = avery.post("/connect/map", data=data)
    if response.status_code == 400:
        data["near"] = [
            unescape(v)
            for v in re.findall(r'<input type="hidden" name="near" value="([^"]+)">', response.text)
        ]
        response = avery.post("/connect/map", data=data)
    assert response.status_code == 303, response.text
    fresh.bookkeeper.recompute(wait=True)

    assert fresh.bookkeeper.status.failure is None
    found = fresh.books()
    held = {
        e.account for e in found.entries if isinstance(e, Open) and e.account.startswith(("A", "L"))
    }
    assert held == set(PATHS.values())
    booked = Counter(
        (p.account, e.date, _number(p))
        for e in _transactions(found.entries)
        for p in e.postings
        if p.account in PATHS.values()
    )
    assert booked
    assert set(booked.values()) == {1}


def test_a_bank_connected_again_never_defaults_to_another_members_account(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Blake filed the bank into Blake's own accounts and removed it; Avery connecting the same
    bank and cards is offered fresh accounts, and the page says nothing of Blake's."""
    fresh = first_day(tmp_path, clock, institutions=(SHAPE,))
    blakes = {mask: path.replace(":Avery:", ":Blake:") for mask, path in PATHS.items()}
    fresh.link("blake", "ins_willowmere")
    fresh.map(fresh.signed_in("blake"), blakes, owner="blake")
    until(fresh, lambda b: _on(b, blakes["6616"]))
    _remove(fresh, "blake", _item(fresh))
    fresh.link("avery", "ins_willowmere")

    page = fresh.signed_in("avery").get("/connect/map").text

    defaults = _defaults(page)
    assert set(defaults) == set(_newest(fresh).values())
    assert set(defaults.values()) == {NEW}
    assert ":Blake:" not in unescape(page)


def test_a_bank_connected_again_never_defaults_to_an_account_the_member_no_longer_owns(
    tmp_path: Path, clock: FakeClock
) -> None:
    fresh = first_day(tmp_path, clock, institutions=(SHAPE,))
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), PATHS)
    until(fresh, lambda b: _on(b, PATHS["6616"]))
    _remove(fresh, "avery", _item(fresh))
    conn = fresh.connect()
    try:
        (checking,) = [a for a in accounts.all_accounts(conn) if a.ledger_account == PATHS["6616"]]
        # Owned jointly with Blake once, and now Blake's alone.
        conn.execute(
            "INSERT INTO account_owners (account_id, member_id) VALUES (?, ?)",
            (checking.id, fresh.member_ids["blake"]),
        )
        conn.execute(
            "DELETE FROM account_owners WHERE account_id = ? AND member_id = ?",
            (checking.id, fresh.member_ids["avery"]),
        )
        conn.commit()
    finally:
        conn.close()
    fresh.link("avery", "ins_willowmere")

    page = fresh.signed_in("avery").get("/connect/map").text

    defaults = {mask: _defaults(page)[account_id] for mask, account_id in _newest(fresh).items()}
    assert defaults["6616"] == NEW
    assert {mask: d for mask, d in defaults.items() if mask != "6616"} == {
        mask: path for mask, path in PATHS.items() if mask != "6616"
    }
    offered = re.findall(r'<option value="([^"]+)"', unescape(page))
    assert PATHS["6616"] not in offered


# What hearth does not know yet.


REPORTED = {
    "1111": Decimal("-0.50"),
    "2212": Decimal(-18),
    "3313": Decimal(-3),
    "4414": Decimal(-4),
    "5515": Decimal(-5),
    "6616": Decimal(4130),
    "7717": Decimal(8800),
}
"""What the bank says each account holds, in the books' sign: a card's balance is owed."""


def test_each_account_holds_what_its_bank_says_once_its_opening_balance_is_in(
    filed: FirstDay,
) -> None:
    found = filed.books()
    avery, blake = filed.signed_in("avery"), filed.signed_in("blake")

    assert (found.awaiting, found.unanchored, found.unknown) == (frozenset(), frozenset(), {})
    assert {PATHS[mask]: _held(found, PATHS[mask]) for mask in PATHS} == {
        PATHS[mask]: held for mask, held in REPORTED.items()
    }
    for where, page in {
        "avery /": avery.get("/").text,
        "avery /you": avery.get("/you").text,
        "avery /net-worth": avery.get("/net-worth").text,
        "blake /net-worth": blake.get("/net-worth").text,
    }.items():
        text = shown(page)
        assert "Not known yet" not in text, where
        assert "no balance in hearth yet" not in text, where
        # The balance, not two years of rows added up.
        assert "$12,900" in text, where
    assert "$12,899.50" in shown(avery.get("/net-worth").text)
    # Spending is known: every row of the month is in, and this month is exactly typical.
    you = shown(avery.get("/you").text)
    assert "History incomplete" not in you
    assert "Not brought in yet" not in you
    assert re.search(r"Since\s+(Oct|Nov) 2024", you)
    assert "Gas and electricity" in you
    assert "GasAndElectricity" not in you
    assert "You are right where a typical October is at this point." in you


def test_an_opening_balance_is_what_makes_a_balance_known() -> None:
    opened = ownbooks.Opened(PATHS["6616"], date(2024, 10, 16), "Checking", "Willowmere", "6616")
    moved = Transaction(
        {"filename": "<test>", "lineno": 1},
        date(2026, 9, 1),
        "!",
        None,
        "Payroll",
        frozenset(),
        frozenset(),
        [],
    )

    def entry(other: str) -> Transaction:
        units = Amount(Decimal("10.00"), "USD")
        return moved._replace(
            postings=[
                Posting(PATHS["6616"], units, None, None, None, None),
                Posting(other, -units, None, None, None, None),
            ]
        )

    for other, anchors in (
        ("Income:Wages", False),
        (UNRESOLVED, False),
        (TRANSFER_PENDING, False),
        ("Equity:Opening-Balances", True),
    ):
        found = books.unanchored([opened.entry(1), entry(other)])
        assert (PATHS["6616"] not in found) is anchors, other


# The file a person opens.


def test_the_books_file_holds_every_transaction_and_loads_cleanly(filed: FirstDay) -> None:
    grocer = _row(filed, "Larkspur Grocer", date(2026, 9, 20))
    _rule(filed, plaid_review_id(grocer.transaction_id), "Expenses:Household")
    found = until(
        filed,
        lambda b: any(
            e.flag == "*" and e.payee == "Larkspur Grocer" for e in _on(b, PATHS["6616"])
        ),
    )

    entries, errors = written(filed.settings)

    assert errors == []
    assert len(_transactions(entries)) == len(_transactions(found.entries))
    opened = {e.account for e in entries if isinstance(e, Open)}
    assert "Expenses:Household" in opened
    assert {UNRESOLVED, TRANSFER_PENDING} <= opened


def test_an_account_the_households_own_ledger_holds_is_left_to_it(filed: FirstDay) -> None:
    conn = filed.connect()
    try:
        with Feed.open(filed.settings.feed_database) as feed:
            derived = ownbooks.derive(
                conn, feed, opened={PATHS["6616"]}, since=date(2026, 1, 1)
            ).entries
    finally:
        conn.close()

    touched = {p.account for e in _transactions(derived) for p in e.postings}
    assert PATHS["6616"] not in touched
    assert PATHS["1111"] in touched
    assert PATHS["6616"] not in {e.account for e in derived if isinstance(e, Open)}


# Review ids.


def test_a_plaid_rows_review_id_is_its_transaction_ids_whatever_it_says() -> None:
    assert plaid_review_id("tx-0f3a9c2e71") == plaid_review_id("tx-0f3a9c2e71")
    assert plaid_review_id("tx-0f3a9c2e71") != plaid_review_id("tx-5b1d0e8a44")
    assert re.fullmatch(r"[0-9a-f]{10}", plaid_review_id("tx-0f3a9c2e71"))
    with pytest.raises(ValueError, match="transaction id"):
        plaid_review_id("")


def test_what_a_row_says_is_its_account_day_and_amount_to_the_cent() -> None:
    day = date(2026, 9, 20)
    base = says(PATHS["6616"], day, Decimal("-64.20"), "USD")

    assert says(PATHS["6616"], day, Decimal("-64.2"), "USD") == base
    assert says(PATHS["6616"], day, Decimal("-64.21"), "USD") != base
    assert says(PATHS["6616"], day + timedelta(days=1), Decimal("-64.20"), "USD") != base
    assert says(PATHS["7717"], day, Decimal("-64.20"), "USD") != base


class SyncFinishingNow:
    """The household's connections, as a recompute finds them just as a sync finishes: the
    moment the recompute asks how current each is, the sync stores one more row."""

    def __init__(self, fresh: FirstDay, item_id: str, account_id: str) -> None:
        self._known = RegisteredConnections(fresh.settings.database)
        self._feed = fresh.settings.feed_database
        self._item_id, self._account_id = item_id, account_id
        self.stored: str | None = None

    def of(self, member: MemberId) -> Sequence[Connection]:
        return self._known.of(member)

    def all(self) -> Sequence[Connection]:
        if self.stored is None:
            self.stored = "tx-arrived-mid-recompute"
            row = PlaidTransaction(
                self.stored, self._account_id, date(2026, 10, 15), None, Decimal("3.10"), "USD",
                "Tinderbox Coffee", "Tinderbox Coffee", None, "FOOD_AND_DRINK_COFFEE",
                pending=False, pending_transaction_id=None,
            )  # fmt: skip
            with Feed.open(self._feed) as feed:
                run = feed.start_run(self._item_id, now=household.NOW)
                assert run is not None
                feed.apply(
                    run,
                    self._item_id,
                    SyncPage((row,), (), (), "c-next", False, None),
                    now=household.NOW,
                )
                feed.finish(run, Outcome.COMPLETE, now=household.NOW)
        return self._known.all()


def test_a_sync_finishing_during_a_recompute_never_vouches_for_rows_the_books_lack(
    filed: FirstDay,
) -> None:
    conn = filed.connect()
    try:
        card = next(a for a in Registry(conn).confirmed() if a.mask == "1111")
    finally:
        conn.close()
    racing = SyncFinishingNow(filed, card.item_id, card.account_id)

    found = books.from_household(filed.settings, rulings=rulings_in, connections=racing)(
        filed.clock()
    )

    # Whatever the connection vouches for, through whichever day, the books hold all of it.
    assert PATHS["1111"] in found.synced
    assert racing.stored is not None
    assert any(racing.stored in str(_sources(e)) for e in _on(found, PATHS["1111"]))


def test_the_import_page_says_so_while_what_arrived_is_still_being_added_up(
    tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Filing the accounts starts a recompute behind the page it lands on, which a member
    reaches before the recompute is done: the page must not say nothing is waiting."""
    fresh = first_day(tmp_path, clock, institutions=(SHAPE,))
    fresh.link("avery", "ins_willowmere")
    waiting: list[threading.Thread] = []

    class NotStartedYet(threading.Thread):
        """A recompute's thread, asked for and not yet running, as the next page finds it.
        Every other thread starts as it would."""

        def start(self) -> None:
            if self.name == "hearth-recompute":
                waiting.append(self)
            else:
                super().start()

    monkeypatch.setattr(threading, "Thread", NotStartedYet)
    avery = fresh.signed_in("avery")
    fresh.map(avery, PATHS)
    adding = avery.get("/connect/import").text
    monkeypatch.undo()
    assert waiting
    for thread in waiting:
        thread.run()
    done = avery.get("/connect/import").text

    assert "Bringing them into your figures" in shown(adding)
    assert "arrives here in a moment" in shown(adding)
    assert "Nothing waits in Review yet" not in shown(adding)
    assert 'hx-get="/connect/import"' in adding
    assert "In your figures and in Review" in shown(done)
    assert "waiting in Review" in shown(done)
    assert 'hx-get="/connect/import"' not in done


def test_an_account_the_household_names_that_nothing_brings_in_is_not_known_never_zero(
    filed: FirstDay,
) -> None:
    """A household that once inherited a ledger and now keeps its own books still names the
    accounts it claimed then; one no connection feeds holds nothing in the books, not $0."""
    conn = filed.connect()
    try:
        elsewhere = accounts.register(
            conn,
            ledger_account="Assets:Avery:Elsewhere:Checking",
            name="Elsewhere checking",
            owners=[filed.member_ids["avery"]],
        )
    finally:
        conn.close()
    filed.bookkeeper.recompute(wait=True)
    avery = filed.signed_in("avery")

    you = shown(avery.get("/you").text)
    page = shown(avery.get(f"/accounts/{elsewhere.id}").text)

    assert re.findall(ZERO, you) == []
    assert "Not connected, nothing brought in" in you
    assert "Nothing of this account is in hearth: connect its bank to bring it in." in page
    assert re.findall(ZERO, page) == []
    # Nor is a month with none of its transactions in hearth a month in which it had none.
    assert "No transactions" not in page
    assert "does not have all of this account's transactions for October 2026" in page


def test_where_did_it_go_counts_the_moves_hearth_matched_by_itself(filed: FirstDay) -> None:
    """Card payments both banks sent are matched and counted once: Where did it go? says how
    many, as nothing is left to do for them."""
    found = filed.books()
    pairs = [e for e in _transactions(found.entries) if e.narration == booking.CARD_PAYMENT]
    assert pairs

    page = filed.signed_in("avery").get("/review/where").text

    matched = f"{len(pairs)} move{'' if len(pairs) == 1 else 's'} between your connected accounts"
    assert f'<span class="tnum">{len(pairs)}</span> {matched.split(" ", 1)[1]} matched' in page
    assert (
        "between your connected accounts" not in filed.signed_in("blake").get("/review/where").text
    )
