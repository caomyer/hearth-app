"""What each connected account held before its history: worked out from what its bank says it
holds now, and proved against that figure (``hearth.plaid.opening``).

The bank is invented in the shape of a real one: seven accounts at one institution, five of
them cards it calls the same. One card has statements, whose closing balances the books must
reproduce to the cent; it has a pending charge, which its bank's balance leaves out. Every
value is invented.
"""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from beancount import loader
from beancount.core.data import Balance, Directive, Posting, Transaction
from beancount.ops.balance import get_balance_tolerance

from hearth import accounts, sharing
from hearth.books import Books
from hearth.plaid.client import BankAccount
from hearth.plaid.fake import FakeAccount, FakeInstitution, FakeTransaction
from hearth.plaid.feed import Feed
from hearth.plaid.opening import EQUITY, Unknown, anchors
from hearth.plaid.registry import Registry
from hearth.review.ids import plaid_review_id
from hearth.review.rulings import Action, RulingStore, Scope
from hearth.sharing import Facet
from tests import household
from tests.conftest import FakeClock
from tests.firstday import CARD, FirstDay, first_day, shown, until_booked, written

STATEMENTED = (
    FakeTransaction(
        "Larkspur Grocer", Decimal("40.10"), date(2026, 8, 3), "FOOD_AND_DRINK_GROCERIES"
    ),
    FakeTransaction(
        "Tinderbox Coffee", Decimal("12.35"), date(2026, 8, 20), "FOOD_AND_DRINK_COFFEE"
    ),
    FakeTransaction(
        "Payment Thank You",
        Decimal("-52.45"),
        date(2026, 9, 5),
        "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
    ),
    FakeTransaction(
        "Quill Stationers",
        Decimal("0.10"),
        date(2026, 9, 18),
        "GENERAL_MERCHANDISE_OFFICE_SUPPLIES",
    ),
    FakeTransaction(
        "Quill Stationers",
        Decimal("0.20"),
        date(2026, 9, 18),
        "GENERAL_MERCHANDISE_OFFICE_SUPPLIES",
    ),
    FakeTransaction(
        "Tinderbox Coffee",
        Decimal("6.25"),
        date(2026, 10, 14),
        "FOOD_AND_DRINK_COFFEE",
        pending=True,
    ),
)
"""A card that owed $230.00 before Aug 3; its bank reports $230.30 owed, leaving the pending
coffee out."""

STATEMENTS = {
    date(2026, 8, 2): Decimal("230.00"),
    date(2026, 8, 31): Decimal("282.45"),
    date(2026, 9, 30): Decimal("230.30"),
}
"""What the card's statements say it owed at the end of each day: the opening, two closes."""

CHECKING = (
    FakeTransaction("Payroll Wren & Co", Decimal("-2140.00"), date(2026, 9, 1), "INCOME_WAGES"),
    FakeTransaction(
        "Hollins Lettings", Decimal("1800.00"), date(2026, 9, 3), "RENT_AND_UTILITIES_RENT"
    ),
    FakeTransaction("Payroll Wren & Co", Decimal("-2140.00"), date(2026, 10, 1), "INCOME_WAGES"),
    FakeTransaction(
        "Hollins Lettings", Decimal("1800.00"), date(2026, 10, 3), "RENT_AND_UTILITIES_RENT"
    ),
)

THORNBURY = FakeInstitution(
    "ins_thornbury",
    "Thornbury Bank",
    (
        FakeAccount(
            "c1", CARD, "1101", "credit", "credit card", Decimal("230.30"), transactions=STATEMENTED
        ),
        FakeAccount("c2", CARD, "2202", "credit", "credit card", Decimal(0), transactions=()),
        FakeAccount(
            "c3",
            CARD,
            "3303",
            "credit",
            "credit card",
            Decimal("12.00"),
            transactions=(
                FakeTransaction(
                    "Marrowbone Transit",
                    Decimal("12.00"),
                    date(2026, 9, 9),
                    "TRANSPORTATION_PUBLIC_TRANSIT",
                ),
            ),
        ),
        FakeAccount(
            "c4",
            CARD,
            "4404",
            "credit",
            "credit card",
            Decimal("-15.00"),
            transactions=(
                FakeTransaction(
                    "Bramble Pharmacy",
                    Decimal("-15.00"),
                    date(2026, 9, 12),
                    "MEDICAL_PHARMACIES_AND_SUPPLEMENTS",
                ),
            ),
        ),
        FakeAccount(
            "c5",
            CARD,
            "5505",
            "credit",
            "credit card",
            Decimal("75.50"),
            transactions=(
                FakeTransaction(
                    "Cobblestone Utilities",
                    Decimal("25.50"),
                    date(2026, 10, 2),
                    "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY",
                ),
            ),
        ),
        FakeAccount(
            "chk",
            "EVERYDAY CHECKING",
            "6606",
            "depository",
            "checking",
            Decimal("4130.00"),
            transactions=CHECKING,
        ),
        FakeAccount(
            "sav",
            "SAVINGS",
            "7707",
            "depository",
            "savings",
            Decimal("8800.42"),
            transactions=(
                FakeTransaction(
                    "Interest", Decimal("-0.42"), date(2026, 9, 30), "INCOME_INTEREST_EARNED"
                ),
            ),
        ),
    ),
)
"""Seven accounts from one invented bank: five cards it calls the same, told apart by mask."""

PATHS = {
    "1101": "Liabilities:Avery:Thornbury:Heron",
    "2202": "Liabilities:Avery:Thornbury:Plover",
    "3303": "Liabilities:Avery:Thornbury:Tern",
    "4404": "Liabilities:Avery:Thornbury:Curlew",
    "5505": "Liabilities:Avery:Thornbury:Dunlin",
    "6606": "Assets:Avery:Thornbury:Checking",
    "7707": "Assets:Avery:Thornbury:Savings",
}

REPORTED = {
    "1101": Decimal("-230.30"),
    "2202": Decimal(0),
    "3303": Decimal("-12.00"),
    "4404": Decimal("15.00"),
    "5505": Decimal("-75.50"),
    "6606": Decimal("4130.00"),
    "7707": Decimal("8800.42"),
}
"""What the bank says each account holds, in the books' sign: owed on a card is negative, and
a card in credit, which Plaid reports as a negative balance, holds money."""

AS_OF = household.NOW.astimezone().date()
"""The day the bank reported its balances, as the household names it."""


@pytest.fixture
def filed(tmp_path: Path, clock: FakeClock) -> FirstDay:
    fresh = first_day(tmp_path, clock, institutions=(THORNBURY,))
    fresh.link("avery", THORNBURY.id)
    fresh.map(fresh.signed_in("avery"), PATHS)
    until_booked(fresh, PATHS["7707"])
    return fresh


def _held(found: Books | list[Directive], account: str, through: date) -> Decimal:
    """What the books say `account` held at the end of `through`."""
    entries = found.entries if isinstance(found, Books) else found
    return sum(
        (
            p.units.number
            for e in entries
            if isinstance(e, Transaction) and e.date <= through
            for p in e.postings
            if p.account == account and p.units is not None and p.units.number is not None
        ),
        Decimal(0),
    )


def _number(posting: Posting) -> Decimal:
    assert posting.units is not None
    assert isinstance(posting.units.number, Decimal)
    return posting.units.number


def _anchor(
    entries: tuple[Directive, ...] | list[Directive], account: str
) -> tuple[Transaction, Balance]:
    found = [e for e in entries if anchors(e) and getattr(e, "account", None) in (None, account)]
    (opening,) = [
        e for e in found if isinstance(e, Transaction) and e.postings[0].account == account
    ]
    (asserted,) = [e for e in found if isinstance(e, Balance) and e.account == account]
    return opening, asserted


# What each account holds.


def test_every_account_holds_what_its_bank_says(filed: FirstDay) -> None:
    found = filed.books()

    assert found.unanchored == frozenset()
    assert found.unknown == {}
    assert {mask: _held(found, PATHS[mask], found.as_of) for mask in PATHS} == REPORTED


def test_the_card_owed_what_each_of_its_statements_says_to_the_cent(filed: FirstDay) -> None:
    found = filed.books()

    assert {day: -_held(found, PATHS["1101"], day) for day in STATEMENTS} == STATEMENTS


def test_a_pending_charge_is_left_out_as_the_banks_balance_leaves_it_out(filed: FirstDay) -> None:
    """Had the bank's balance counted the pending coffee, the opening would be $6.25 less owed,
    and every statement would miss by exactly that: the measurement that decided it."""
    found = filed.books()
    opening, _ = _anchor(found.entries, PATHS["1101"])

    assert _number(opening.postings[0]) == Decimal("-230.00")
    assert all(
        e.payee != "Tinderbox Coffee" or e.date != date(2026, 10, 14)
        for e in found.entries
        if isinstance(e, Transaction)
    )


def test_an_opening_is_the_day_before_the_first_row_and_says_where_it_came_from(
    filed: FirstDay,
) -> None:
    found = filed.books()

    opening, asserted = _anchor(found.entries, PATHS["6606"])

    assert opening.date == date(2026, 8, 31)
    assert [(p.account, _number(p)) for p in opening.postings] == [
        (PATHS["6606"], Decimal("3450.00")),
        (EQUITY, Decimal("-3450.00")),
    ]
    assert opening.flag == "*"
    assert opening.narration == "Opening balance"
    # The day after the bank reported it: a balance is checked at the start of its day.
    assert (asserted.date, asserted.amount.number) == (
        AS_OF + timedelta(days=1),
        Decimal("4130.00"),
    )
    for entry in (opening, asserted):
        assert entry.meta["balance"] == "current"
        assert entry.meta["reported-by"] == "Thornbury Bank"
        assert entry.meta["reported-at"] == household.NOW.isoformat()


def test_a_cards_balance_is_asserted_as_owed(filed: FirstDay) -> None:
    _, owing = _anchor(filed.books().entries, PATHS["1101"])
    _, in_credit = _anchor(filed.books().entries, PATHS["4404"])

    assert owing.amount.number == Decimal("-230.30")
    assert in_credit.amount.number == Decimal("15.00")


def test_beancount_itself_agrees_with_every_balance_the_bank_reported(filed: FirstDay) -> None:
    entries, errors = written(filed.settings)

    assert errors == []
    asserted = {e.account: e.amount.number for e in entries if isinstance(e, Balance)}
    assert asserted == {PATHS[mask]: held for mask, held in REPORTED.items()}


def test_beancount_checks_every_balance_the_bank_reported_to_the_cent(filed: FirstDay) -> None:
    """A whole-dollar balance reaches hearth as `4130.0`; left to infer a tolerance from that,
    beancount would pass books that are four cents out."""
    entries, errors, options = loader.load_file(str(filed.settings.books_file))

    assert errors == []
    tolerances = {
        e.account: get_balance_tolerance(e, options)  # type: ignore[no-untyped-call]
        for e in entries
        if isinstance(e, Balance)
    }
    assert tolerances == dict.fromkeys(PATHS.values(), Decimal(0))


def test_an_account_with_no_history_holds_its_balance_from_the_day_it_was_reported(
    filed: FirstDay,
) -> None:
    """The quiet card has sent nothing: what it holds now is known, not what it held before or
    how it moved, so its history still reads as not brought in."""
    found = filed.books()
    opening, asserted = _anchor(found.entries, PATHS["2202"])

    assert opening.date == AS_OF
    assert asserted.date == AS_OF + timedelta(days=1)
    assert PATHS["2202"] in found.awaiting
    assert "Not brought in yet" in shown(filed.signed_in("avery").get("/you").text)


def test_an_opening_is_never_listed_as_a_transaction(filed: FirstDay) -> None:
    """The quiet card's opening is dated today, the checking account's in August: neither is
    something that happened, so neither month lists it."""
    avery = filed.signed_in("avery")
    for mask, month in (("2202", "2026-10"), ("6606", "2026-08")):
        page = shown(avery.get(f"/accounts/{_account_id(filed, PATHS[mask])}?month={month}").text)
        assert "Opening balance" not in page, mask


# What cannot be worked out is said, never guessed.


def _account_id(fresh: FirstDay, ledger_account: str) -> str:
    conn = fresh.connect()
    try:
        return next(a.id for a in accounts.all_accounts(conn) if a.ledger_account == ledger_account)
    finally:
        conn.close()


def _item(fresh: FirstDay) -> str:
    conn = fresh.connect()
    try:
        return Registry(conn).all_items()[-1].item_id
    finally:
        conn.close()


def _sync(fresh: FirstDay, item_id: str) -> None:
    avery = fresh.signed_in("avery")
    response = avery.post(f"/connect/items/{item_id}/sync", data={"csrf": fresh.csrf(avery)})
    assert response.status_code == 303


def _not_known(fresh: FirstDay, ledger_account: str, why: str) -> None:
    """The account's balance is not known, its owner is told why, and nothing reads as zero."""
    found = fresh.books()
    assert ledger_account in found.unanchored
    avery = fresh.signed_in("avery")
    page = shown(avery.get(f"/accounts/{_account_id(fresh, ledger_account)}").text)
    assert "Not known yet." in page
    assert why in page
    assert "no balance in hearth yet" in shown(avery.get("/").text)


def test_a_sync_that_did_not_finish_leaves_the_balance_it_reported_unused(filed: FirstDay) -> None:
    """The sync recorded the bank's new balances and then stopped: rows the balance counts may
    be missing, so the opening is not worked out from it until a sync finishes."""
    item_id = _item(filed)
    filed.plaid.change(
        item_id, "added", FakeTransaction("Tinderbox Coffee", Decimal("4.10"), AS_OF), "c3"
    )
    filed.plaid.fail_next("/transactions/sync", "INTERNAL_SERVER_ERROR")
    _sync(filed, item_id)
    filed.bookkeeper.recompute(wait=True)

    assert set(filed.books().unknown.values()) == {Unknown.UNFINISHED}
    _not_known(filed, PATHS["1101"], "Its bank's latest update did not finish")

    _sync(filed, item_id)
    filed.bookkeeper.recompute(wait=True)
    assert filed.books().unanchored == frozenset()


def test_a_balance_in_another_currency_is_not_used(filed: FirstDay) -> None:
    item_id = _item(filed)
    with Feed.open(filed.settings.feed_database) as feed:
        conn = filed.connect()
        try:
            (savings,) = [a for a in Registry(conn).accounts_of(item_id) if a.mask == "7707"]
        finally:
            conn.close()
        reported = BankAccount(
            savings.account_id,
            "SAVINGS",
            None,
            "7707",
            "depository",
            "savings",
            Decimal("8800.42"),
            None,
            "CAD",
        )
        feed.record_balances(item_id, [reported], now=household.NOW + timedelta(minutes=1))
    filed.bookkeeper.recompute(wait=True)

    assert filed.books().unknown == {PATHS["7707"]: Unknown.CURRENCY}
    _not_known(filed, PATHS["7707"], "in a different currency from its transactions")


def test_an_investment_account_is_not_known_never_its_balance_read_as_cash(
    tmp_path: Path, clock: FakeClock
) -> None:
    quarry = FakeInstitution(
        "ins_quarry",
        "Quarry Brokerage",
        (
            FakeAccount(
                "ira", "Individual account", "8818", "investment", "brokerage", Decimal("18452.30")
            ),
        ),
    )
    fresh = first_day(tmp_path, clock, institutions=(quarry,))
    fresh.link("avery", quarry.id)
    fresh.map(fresh.signed_in("avery"), {"8818": "Assets:Avery:Quarry:Brokerage"})
    until_booked(fresh, "Assets:Avery:Quarry:Brokerage")

    assert fresh.books().unknown == {"Assets:Avery:Quarry:Brokerage": Unknown.INVESTMENT}
    _not_known(fresh, "Assets:Avery:Quarry:Brokerage", "what its investments are worth")


def test_two_bank_accounts_going_to_one_have_no_one_balance(filed: FirstDay) -> None:
    """A card reissued under new digits at another bank, filed into the same account: neither
    bank's balance is the account's, so neither is used."""
    other = FakeInstitution(
        "ins_fenwick",
        "Fenwick Card",
        (
            FakeAccount(
                "card",
                "Fenwick Rewards",
                "9090",
                "credit",
                "credit card",
                Decimal("40.00"),
                transactions=(),
            ),
        ),
    )
    filed.plaid.institutions[other.id] = other
    filed.link("avery", other.id)
    conn = filed.connect()
    try:
        registry = Registry(conn)
        (card,) = registry.accounts_of(_item(filed))
        heron = _account_id(filed, PATHS["1101"])
        registry.confirm(card.account_id, heron, by=filed.member_ids["avery"], now=household.NOW)
    finally:
        conn.close()
    filed.bookkeeper.recompute(wait=True)

    assert filed.books().unknown == {PATHS["1101"]: Unknown.SHARED}
    _not_known(filed, PATHS["1101"], "no one balance a bank reports is its own")


def test_a_bank_connected_again_is_not_known_until_its_history_reaches_the_old_one(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Removed a month ago and connected again: until the new connection's history reaches back
    to where the old one stopped, the rows between them are missing, so its balance is not
    worked out from what is in; once it does, it is."""
    clock.now = household.NOW - timedelta(days=30)
    fresh = first_day(tmp_path, clock, institutions=(THORNBURY,))
    fresh.link("avery", THORNBURY.id)
    fresh.map(fresh.signed_in("avery"), PATHS)
    until_booked(fresh, PATHS["7707"])
    old = _item(fresh)
    avery = fresh.signed_in("avery")
    assert (
        avery.post(f"/connect/items/{old}/remove", data={"csrf": fresh.csrf(avery)}).status_code
        == 303
    )

    clock.now = household.NOW
    fresh.plaid.slow_history = True
    fresh.link("avery", THORNBURY.id)
    new = _item(fresh)
    _remap(fresh)
    fresh.bookkeeper.recompute(wait=True)

    # The old connection's rows stop where it last synced, and the new one has sent none yet;
    # an account the old one sent nothing for has no rows to be missing between them.
    assert fresh.books().unknown == dict.fromkeys(
        (PATHS[mask] for mask in ("1101", "3303", "4404", "6606")), Unknown.GAP
    )
    _not_known(fresh, PATHS["6606"], "the transactions from between its two connections")

    fresh.plaid.slow_history = False
    fresh.plaid.history_ready(new)
    _sync(fresh, new)
    fresh.bookkeeper.recompute(wait=True)
    assert fresh.books().unknown == {}
    assert _held(fresh.books(), PATHS["6606"], fresh.books().as_of) == Decimal("4130.00")


def _remap(fresh: FirstDay) -> None:
    """File a bank connected again into the accounts it fed before."""
    import re
    from html import unescape

    conn = fresh.connect()
    try:
        registry = Registry(conn)
        found = registry.accounts_of(registry.all_items()[-1].item_id)
    finally:
        conn.close()
    client = fresh.signed_in("avery")
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
        data["near"] = [
            unescape(v)
            for v in re.findall(r'<input type="hidden" name="near" value="([^"]+)">', response.text)
        ]
        response = client.post("/connect/map", data=data)
    assert response.status_code == 303, response.text


# What the books do with a balance once it is in them.


def test_a_row_its_bank_counts_is_never_marked_as_not_a_transaction(filed: FirstDay) -> None:
    """Its bank counted the rent in the balance it reports for checking: leaving it out is a
    dispute with the bank, not a review decision, so the desk refuses it and says so, and
    nobody's figures wait on it."""
    before = filed.books()
    avery = filed.signed_in("avery")

    response = avery.post(
        "/review/rows/reject",
        data={"csrf": filed.csrf(avery), "review_id": _rent(filed)},
    )

    assert response.status_code == 303
    assert "outcome=banks-row" in response.headers["location"]
    page = shown(avery.get(response.headers["location"]).text)
    assert "Its bank counts it in the balance it reports for this account" in page
    filed.bookkeeper.recompute(wait=True)
    assert filed.bookkeeper.status.failure is None
    assert filed.books().entries == before.entries


def test_a_row_is_its_banks_while_the_bank_has_not_reported_a_balance_yet(
    tmp_path: Path, clock: FakeClock
) -> None:
    """A bank that has reported no balance yet will once it does, and the books will then
    assert it: a row left out meanwhile would be missing from them, so the desk refuses it."""
    silent = FakeInstitution(
        THORNBURY.id,
        THORNBURY.name,
        tuple(replace(a, current=None) for a in THORNBURY.accounts),
    )
    fresh = first_day(tmp_path, clock, institutions=(silent,))
    fresh.link("avery", silent.id)
    fresh.map(fresh.signed_in("avery"), PATHS)
    until_booked(fresh, PATHS["7707"])
    assert fresh.books().unknown[PATHS["6606"]] is Unknown.NO_BALANCE
    avery = fresh.signed_in("avery")

    response = avery.post(
        "/review/rows/reject", data={"csrf": fresh.csrf(avery), "review_id": _rent(fresh)}
    )

    assert "outcome=banks-row" in response.headers["location"]


def test_no_ruling_made_while_a_sync_is_unfinished_stalls_the_books_after_it_finishes(
    filed: FirstDay,
) -> None:
    """While its last sync is unfinished, checking asserts nothing; the bank's balance will be
    asserted once a sync finishes. Leaving out its rent, or moving the rent into savings,
    would make those books disagree with the bank, so neither is taken meanwhile."""
    item_id = _item(filed)
    filed.plaid.change(
        item_id, "added", FakeTransaction("Tinderbox Coffee", Decimal("4.10"), AS_OF), "c3"
    )
    filed.plaid.fail_next("/transactions/sync", "INTERNAL_SERVER_ERROR")
    _sync(filed, item_id)
    filed.bookkeeper.recompute(wait=True)
    assert filed.books().unknown[PATHS["6606"]] is Unknown.UNFINISHED
    avery = filed.signed_in("avery")
    rent = _rent(filed)

    rejected = avery.post(
        "/review/rows/reject", data={"csrf": filed.csrf(avery), "review_id": rent}
    )
    moved = avery.post(
        "/review/rows/book",
        data={"csrf": filed.csrf(avery), "review_id": rent, "account": PATHS["7707"]},
    )

    assert "outcome=banks-row" in rejected.headers["location"]
    assert "outcome=banks-account" in moved.headers["location"]
    _sync(filed, item_id)
    filed.bookkeeper.recompute(wait=True)
    assert filed.bookkeeper.status.failure is None
    found = filed.books()
    assert found.unanchored == frozenset()
    assert _held(found, PATHS["6606"], AS_OF) == Decimal("4130.00")


def _rent(fresh: FirstDay) -> str:
    """The review id of checking's October rent."""
    with Feed.open(fresh.settings.feed_database) as feed:
        row = next(
            r
            for r in feed.transactions(_linked(fresh, "6606"))
            if (r.name, r.day) == ("Hollins Lettings", date(2026, 10, 3))
        )
    return plaid_review_id(row.transaction_id)


def _rejected_on_record(fresh: FirstDay) -> None:
    """The rent marked as not a transaction by a ruling already on record, as one made before
    the desk refused it would be; then a recompute."""
    review_id = _rent(fresh)
    (item,) = [i for i in fresh.books().pile if i.review_id == review_id]
    conn = fresh.connect()
    try:
        RulingStore(conn).record(
            member_id=fresh.member_ids["avery"],
            scope=Scope.ROW,
            group=None,
            action=Action.REJECT,
            account=None,
            note=None,
            rows={review_id: None},
            says={review_id: item.says},
            at=fresh.clock(),
        )
    finally:
        conn.close()
    fresh.bookkeeper.recompute(wait=True)


def test_books_that_leave_out_a_row_the_bank_counted_fail_the_balance_check(
    filed: FirstDay,
) -> None:
    """They would no longer add up to what the bank says checking holds: the figures stay as
    they were, and its owner is told why."""
    before = filed.books()

    _rejected_on_record(filed)

    status = filed.bookkeeper.status
    assert status.books == before
    assert status.failure is not None
    assert status.failure.disagreement is not None
    check = status.failure.disagreement
    assert (check.account, check.asserted, check.computed) == (
        PATHS["6606"],
        Decimal("4130.00"),
        Decimal("5930.00"),
    )
    assert status.failure.reported_by == "Thornbury Bank"


def test_only_its_owner_is_told_why_a_banks_balance_failed(filed: FirstDay) -> None:
    """Blake sees Avery's balances, not what Avery's connection or reviews did, nor the day
    the connection last synced: Blake reads only that the figures wait on one member."""
    conn = filed.connect()
    try:
        sharing.choose(
            conn, filed.member_ids["avery"], Facet.ACCOUNT_BALANCES, shared=True, now=filed.clock()
        )
    finally:
        conn.close()
    _rejected_on_record(filed)

    blake = shown(filed.signed_in("blake").get("/net-worth").text)
    avery = shown(filed.signed_in("avery").get("/net-worth").text)

    assert "Thornbury Bank says" in avery
    assert "Thornbury Bank" not in blake
    assert "Today's figures are waiting on a fix to one member's records." in blake


def _linked(fresh: FirstDay, mask: str) -> str:
    conn = fresh.connect()
    try:
        return next(a.account_id for a in Registry(conn).confirmed() if a.mask == mask)
    finally:
        conn.close()


def test_an_opening_is_derived_afresh_from_each_newer_balance(filed: FirstDay) -> None:
    """The bank posts a charge and reports the new balance: the opening is unchanged, as it
    should be, and the balance asserted is the new one."""
    first, _ = _anchor(filed.books().entries, PATHS["3303"])
    item_id = _item(filed)
    filed.clock.now = household.NOW + timedelta(days=1)
    filed.plaid.change(
        item_id,
        "added",
        FakeTransaction(
            "Marrowbone Transit", Decimal("2.90"), (household.NOW + timedelta(days=1)).date()
        ),
        "c3",
    )
    filed.plaid.report(item_id, "c3", Decimal("14.90"))
    _sync(filed, item_id)
    filed.bookkeeper.recompute(wait=True)

    found = filed.books()
    opening, asserted = _anchor(found.entries, PATHS["3303"])
    assert (opening.date, opening.postings) == (first.date, first.postings)
    assert opening.meta["reported-at"] == (household.NOW + timedelta(days=1)).isoformat()
    assert asserted.amount.number == Decimal("-14.90")
    assert _held(found, PATHS["3303"], found.as_of) == Decimal("-14.90")


def test_balances_are_reported_with_the_time_they_were(filed: FirstDay) -> None:
    _, asserted = _anchor(filed.books().entries, PATHS["7707"])

    reported = datetime.fromisoformat(str(asserted.meta["reported-at"]))
    assert reported == household.NOW
    assert reported.tzinfo is not None
    assert reported.astimezone(UTC) == household.NOW
