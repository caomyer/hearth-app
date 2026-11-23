"""An accept holds through everything hearth allows but the bank changing the row.

Hearth learns what to suggest from the household's decisions, its imported ledger and who owns
what, and every one of those may change after a member accepts a row. None of it may undo the
accept: a ruling makes its row immune to what is learned (``review.decision``). Only the bank's
own inputs to the row, what it says and the bank's category, bring it back to Review.

The household is ``tests/test_learning.py``'s with a ledger, with more beside it: an account of
the ledger nobody has claimed yet, whose newer entries book the bean counter as treats, and a
bank of Blake's. Every name and figure is invented.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hearth import accounts, auth, categories, figures, matching
from hearth.categories import Kind
from hearth.figures import ownership
from hearth.ledger_import import ImportRefused, import_ledger
from hearth.months import Month
from hearth.plaid.fake import FakeAccount, FakeInstitution, FakeTransaction
from hearth.plaid.feed import Feed
from hearth.plaid.registry import Registry
from hearth.review.decision import PROPOSAL_CHANGED, REVISED, State
from hearth.review.ids import plaid_review_id, says
from hearth.review.pile import PileItem
from hearth.review.rulings import Action, RulingStore, Scope
from tests.conftest import FakeClock, password_of
from tests.test_learning import (
    COFFEE,
    LEDGER_CHECKING,
    PLAID_COFFEE,
    Household,
    based,
    booked_to,
    connected,
)

OTHER = "Assets:Zq:Other"
TREATS = "Expenses:Zq:Treats"
LEDGER_COFFEE = "Expenses:Zq:Coffee"

EXTRA = f"""
2026-08-01 open {OTHER} USD
2026-08-01 open {TREATS} USD
2026-08-01 * "Opening"
  {OTHER}  100.00 USD
  Equity:Zq:Opening
2026-08-15 * "Zq Bean Counter" ""
  {OTHER}  -2.00 USD
  {TREATS}
2026-08-16 * "Zq Bean Counter" ""
  {OTHER}  -2.50 USD
  {TREATS}
"""
"""An account of the ledger nobody has claimed, whose two newest entries for the bean counter
book it as treats: once claimed, the ledger no longer agrees on the bean counter."""

OTHER_BANK = FakeInstitution(
    "ins_zq_other",
    "Zq Other Bank",
    (
        FakeAccount(
            "oth",
            "ZQ OTHER",
            "0909",
            "depository",
            "checking",
            Decimal("95.50"),
            transactions=(
                FakeTransaction("Zq Bean Counter", Decimal("2.00"), date(2026, 8, 15), COFFEE),
                FakeTransaction("Zq Bean Counter", Decimal("2.50"), date(2026, 8, 16), COFFEE),
            ),
        ),
    ),
)
BLAKE_BANK = FakeInstitution(
    "ins_zq_blakes",
    "Zq Blake Bank",
    (
        FakeAccount(
            "chk",
            "ZQ BLAKE",
            "0606",
            "depository",
            "checking",
            Decimal("500.00"),
            transactions=(
                FakeTransaction("Zq Bean Counter", Decimal("8.00"), date(2026, 9, 9), COFFEE),
            ),
        ),
    ),
)


@dataclass
class Accepted:
    household: Household
    item: PileItem
    account: str
    spent: Decimal

    def holds(self, after: str) -> None:
        """The row is still reviewed, booked where it was accepted, and September's spending
        is what it was."""
        found = self.household
        assert found.state(self.row()) is State.REVIEWED, after
        assert booked_to(found, self.item) == self.account, after
        assert september(found) == self.spent, after

    def row(self) -> PileItem:
        (item,) = [i for i in self.household.books().pile if i.review_id == self.item.review_id]
        return item


def september(household: Household) -> Decimal:
    conn = household.fresh.connect()
    try:
        owners = frozenset({household.fresh.member_ids["avery"]})
        found = figures.month_flows(
            household.books(), ownership(accounts.all_accounts(conn)), Month(2026, 9)
        )
    finally:
        conn.close()
    return found.spent(owners)


@pytest.fixture
def accepted(tmp_path: Path, clock: FakeClock) -> Accepted:
    household = based(tmp_path, clock, others=(OTHER_BANK, BLAKE_BANK), extra=EXTRA)
    item = household.row("ZQ BEAN COUNTER #0042", "3.75")
    assert (item.proposed, item.reason and item.reason.words) == (
        LEDGER_COFFEE,
        "As your ledger books it",
    )
    household.decide(
        "/review/rows/book", review_id=item.review_id, account=LEDGER_COFFEE, shown=LEDGER_COFFEE
    )
    found = Accepted(household, item, LEDGER_COFFEE, september(household))
    found.holds("the accept")
    return found


def test_nothing_hearth_allows_but_the_bank_moves_an_accepted_row(accepted: Accepted) -> None:
    household, fresh, clock = accepted.household, accepted.household.fresh, accepted.household.clock

    # Claiming the account whose newer entries book the merchant otherwise: the ledger now
    # disagrees with itself about the bean counter, which the accepted row does not see.
    conn = fresh.connect()
    try:
        accounts.claim(
            conn,
            ledger_account=OTHER,
            name="Other",
            owners=[fresh.member_ids["avery"]],
            now=clock(),
        )
    finally:
        conn.close()
    assert accepted.row().proposed is None
    accepted.holds("claiming an account")

    # Connecting another bank, and matching its account to the one just claimed.
    clock.advance(timedelta(hours=1))
    other = fresh.link("avery", OTHER_BANK.id)
    conn = fresh.connect()
    try:
        (linked,) = Registry(conn).accounts_of(other)
    finally:
        conn.close()
    matching.confirm(fresh.settings, [(linked.account_id, OTHER)], now=clock())
    accepted.holds("connecting and matching another account")

    # Blake connects a bank of his own, rules on its row of the same merchant, removes the
    # connection and connects it again.
    clock.advance(timedelta(hours=1))
    blakes = fresh.link("blake", BLAKE_BANK.id)
    fresh.map(fresh.signed_in("blake"), {"0606": "Assets:Blake:Zq:Checking"}, owner="blake")
    (theirs,) = [i for i in household.books().pile if i.account == "Assets:Blake:Zq:Checking"]
    blake = fresh.signed_in("blake")
    response = blake.post(
        "/review/rows/book",
        data={"csrf": fresh.csrf(blake), "review_id": theirs.review_id, "account": TREATS},
    )
    assert response.status_code == 303
    accepted.holds("another member's ruling")
    clock.advance(timedelta(hours=1))
    response = blake.post(f"/connect/items/{blakes}/remove", data={"csrf": fresh.csrf(blake)})
    assert response.status_code == 303
    fresh.link("blake", BLAKE_BANK.id)
    accepted.holds("replacing a connection")

    # Importing the ledger again is refused once rulings rest on it, and moves nothing.
    folder = fresh.settings.data_dir.parent / "kept"
    with pytest.raises(ImportRefused):
        import_ledger(fresh.settings, folder / "main.beancount", now=clock())
    accepted.holds("trying to import the ledger again")

    # Saying what kind another category is, and adding a member.
    conn = fresh.connect()
    try:
        categories.set_kind(conn, "Expenses:Zq:Dining", Kind.TAX, now=clock())
        auth.create_member(
            conn,
            actor=None,
            username="casey",
            display_name="Casey",
            is_admin=False,
            password=password_of("casey"),
            now=clock(),
        )
    finally:
        conn.close()
    accepted.holds("setting a kind and adding a member")

    # Another row of the same merchant arrives, and is decided one way and then another.
    later = FakeTransaction("Zq Bean Counter", Decimal("4.25"), date(2026, 9, 20), COFFEE)
    household.arrive(later)
    again = household.row("Zq Bean Counter", "4.25")
    household.decide("/review/rows/book", review_id=again.review_id, account="Expenses:Gifts")
    household.decide("/review/rows/book", review_id=again.review_id, account=TREATS)
    # The new row is September's spending too, whatever it was booked as; nothing else moved.
    accepted.spent += Decimal("4.25")
    accepted.holds("re-deciding another row of the same merchant")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            FakeTransaction("ZQ BEAN COUNTER #0042", Decimal("9.75"), date(2026, 9, 2), COFFEE),
            REVISED,
        ),
        (
            FakeTransaction("ZQ BEAN COUNTER #0042", Decimal("3.75"), date(2026, 9, 3), COFFEE),
            REVISED,
        ),
        (
            FakeTransaction(
                "ZQ BEAN COUNTER #0042",
                Decimal("3.75"),
                date(2026, 9, 2),
                "FOOD_AND_DRINK_FAST_FOOD",
            ),
            PROPOSAL_CHANGED,
        ),
    ],
    ids=["amount", "date", "category"],
)
def test_the_bank_changing_the_row_brings_it_back(
    accepted: Accepted, change: FakeTransaction, reason: str
) -> None:
    household = accepted.household
    with Feed.open(household.fresh.settings.feed_database) as feed:
        (linked,) = [
            r
            for a in ("chk",)
            for r in feed.transactions(f"{household.item}-{a}")
            if plaid_review_id(r.transaction_id) == accepted.item.review_id
        ]
    household.clock.advance(timedelta(hours=1))
    household.fresh.plaid.change(
        household.item, "modified", change, "chk", transaction_id=linked.transaction_id
    )
    avery = household.fresh.signed_in("avery")
    response = avery.post(
        f"/connect/items/{household.item}/sync", data={"csrf": household.fresh.csrf(avery)}
    )
    assert response.status_code == 303
    household.books()

    conn = household.fresh.connect()
    try:
        from hearth.disclosure import Disclosure
        from hearth.members import find

        viewer = find(conn, "avery")
        assert viewer is not None
        desk = Disclosure.load(conn, viewer, household.fresh.bookkeeper.status).review_desk(
            RulingStore(conn), household.clock
        )
        view = desk.item(accepted.item.review_id)
    finally:
        conn.close()
    assert view.decision.state is State.UNREVIEWED
    assert view.decision.reason == reason


# Upgrading from rulings made before suggestions were learned.


def _legacy_accept(household: Household, item: PileItem, account: str) -> None:
    """An accept as hearth recorded one before it kept the bank's category with a ruling."""
    conn = household.fresh.connect()
    try:
        RulingStore(conn).record(
            member_id=household.fresh.member_ids["avery"],
            scope=Scope.ROW,
            group=None,
            action=Action.ACCEPT,
            account=None,
            note=None,
            rows={item.review_id: account},
            says={item.review_id: says(item.account, item.date, item.amount, item.currency)},
            at=household.clock(),
        )
    finally:
        conn.close()


def _waiting(household: Household) -> set[str]:
    conn = household.fresh.connect()
    try:
        from hearth.disclosure import Disclosure
        from hearth.members import find

        viewer = find(conn, "avery")
        assert viewer is not None
        desk = Disclosure.load(conn, viewer, household.fresh.bookkeeper.status).review_desk(
            RulingStore(conn), household.clock
        )
        return {v.item.review_id for v in desk.items() if v.unreviewed}
    finally:
        conn.close()


def test_rows_accepted_before_suggestions_were_learned_stay_accepted(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Every row of a household's books accepted as the bank's category alone named it, as
    hearth suggested before it learned, after an import, and a merchant decided both ways
    before: opened by this hearth, nothing comes back to Review."""
    household = based(tmp_path, clock)
    before = _waiting(household)
    suggested = [i for i in household.books().pile if i.plain is not None]
    assert suggested
    for item in suggested:
        clock.advance(timedelta(minutes=1))
        _legacy_accept(household, item, item.plain or "")
    household.books()

    assert _waiting(household) == before - {i.review_id for i in suggested}
    for item in suggested:
        assert household.state(item) is State.REVIEWED
        assert booked_to(household, item) == item.plain
    assert any(item.proposed != item.plain for item in suggested)


def test_rows_accepted_before_learning_in_a_household_with_no_ledger_stay_accepted(
    tmp_path: Path, clock: FakeClock
) -> None:
    """The bean counter was booked as beans, then a row of it arrived and was accepted at the
    bank's category, as hearth suggested then. Hearth now suggests beans for that row, from
    what was decided before it arrived; the accept holds all the same."""
    household = connected(tmp_path, clock)
    rows = [i for i in household.books().pile if i.group == "Food and drink: coffee"]
    for item in rows:
        household.decide("/review/rows/book", review_id=item.review_id, account="Expenses:Beans")
    household.arrive(FakeTransaction("Zq Bean Counter", Decimal("6.10"), date(2026, 9, 24), COFFEE))
    arrived = household.row("Zq Bean Counter", "6.10")
    assert arrived.proposed == "Expenses:Beans"
    clock.advance(timedelta(hours=1))
    _legacy_accept(household, arrived, PLAID_COFFEE)

    assert household.state(arrived) is State.REVIEWED
    assert booked_to(household, arrived) == PLAID_COFFEE
    assert arrived.review_id not in _waiting(household)


def test_an_old_accept_the_ledger_has_closed_the_account_of_is_asked_again(
    tmp_path: Path, clock: FakeClock
) -> None:
    """An accept made before a ledger was imported that closes the account it booked: laying
    it there would fail the books, so the row is asked again, and the books still compute."""
    closes = f"2026-08-02 open {PLAID_COFFEE} EUR\n"
    household = based(tmp_path, clock, ruled_first=True, extra=closes)
    item = household.row("ZQ BEAN COUNTER #0042", "3.75")

    assert household.fresh.bookkeeper.status.failure is None
    conn = household.fresh.connect()
    try:
        from hearth.disclosure import Disclosure
        from hearth.members import find

        viewer = find(conn, "avery")
        assert viewer is not None
        desk = Disclosure.load(conn, viewer, household.fresh.bookkeeper.status).review_desk(
            RulingStore(conn), household.clock
        )
        assert desk.item(item.review_id).unreviewed
    finally:
        conn.close()
    assert LEDGER_CHECKING in {i.account for i in household.books().pile}
