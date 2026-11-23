"""Suggestions as a household meets them: a bank connected through Link, rows reviewed on the
Review page, more rows arriving with later syncs, and a ledger imported as the base
(``hearth.proposals``). The rules themselves are held in ``tests/test_proposals.py``.

Every institution, merchant, category and figure is invented.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from beancount.core.data import Transaction
from fastapi.testclient import TestClient

from hearth import accounts, books, matching
from hearth.disclosure import Disclosure
from hearth.ledger_import import import_ledger
from hearth.members import find
from hearth.plaid.fake import FakeAccount, FakeInstitution, FakeTransaction
from hearth.plaid.feed import Feed
from hearth.plaid.registry import Registry
from hearth.review.decision import PROPOSAL_CHANGED, State
from hearth.review.ids import plaid_review_id, says
from hearth.review.pile import PileItem
from hearth.review.reasons import Reason, Why
from hearth.review.rulings import Action, RulingStore, Scope
from tests.conftest import FakeClock
from tests.firstday import FirstDay, first_day
from tests.household import noon

TODAY = date(2026, 10, 15)
NOW = noon(TODAY)
COFFEE = "FOOD_AND_DRINK_COFFEE"
CHECKING = "Assets:Avery:Zq:Checking"
PLAID_COFFEE = "Expenses:FoodAndDrink:Coffee"

BEANS = (
    FakeTransaction("SQ *ZQ BEAN COUNTER", Decimal("4.50"), date(2026, 9, 3), COFFEE),
    FakeTransaction("Zq Bean Counter", Decimal("5.25"), date(2026, 9, 10), COFFEE),
    FakeTransaction("ZQ BEAN COUNTER #0042", Decimal("3.75"), date(2026, 9, 17), COFFEE),
)
"""One merchant, written three ways."""

BANK = FakeInstitution(
    "ins_zq_learn",
    "Zq Learning Bank",
    (
        FakeAccount(
            "chk",
            "ZQ CHECKING",
            "0707",
            "depository",
            "checking",
            Decimal("2000.00"),
            transactions=(
                *BEANS,
                FakeTransaction(
                    "Zq Noodle Hut", Decimal("20.00"), date(2026, 9, 5), "FOOD_AND_DRINK_RESTAURANT"
                ),
            ),
        ),
    ),
)


@dataclass
class Household:
    fresh: FirstDay
    item: str
    clock: FakeClock

    def books(self) -> books.Books:
        self.fresh.bookkeeper.recompute(wait=True)
        return self.fresh.books()

    def row(self, name: str, amount: str) -> PileItem:
        found = [
            item
            for item in self.books().pile
            if (item.payee or "") == name and item.amount == -Decimal(amount)
        ]
        assert len(found) == 1, (name, amount, [(i.payee, i.amount) for i in self.books().pile])
        return found[0]

    def arrive(self, row: FakeTransaction) -> None:
        """The bank posts `row`, and Avery syncs, a while later."""
        self.clock.advance(timedelta(hours=1))
        self.fresh.plaid.change(self.item, "added", row, "chk")
        avery = self.fresh.signed_in("avery")
        response = avery.post(
            f"/connect/items/{self.item}/sync", data={"csrf": self.fresh.csrf(avery)}
        )
        assert response.status_code == 303

    def decide(self, path: str, **form: str | list[str]) -> None:
        """Avery decides on the Review page, a while later."""
        self.clock.advance(timedelta(hours=1))
        avery = self.fresh.signed_in("avery")
        response = avery.post(path, data={"csrf": self.fresh.csrf(avery), **form})
        assert response.status_code == 303, response.text
        assert "outcome=changed" not in response.headers["location"]

    def state(self, item: PileItem) -> State:
        conn = self.fresh.connect()
        try:
            viewer = find(conn, "avery")
            assert viewer is not None
            desk = Disclosure.load(conn, viewer, self.fresh.bookkeeper.status).review_desk(
                RulingStore(conn), self.clock
            )
            view = desk.item(item.review_id)
        finally:
            conn.close()
        assert view.decision.reason != PROPOSAL_CHANGED, item
        return view.decision.state

    def client(self) -> TestClient:
        return self.fresh.signed_in("avery")


def suggested(item: PileItem) -> tuple[str | None, str, Reason | None]:
    return item.proposed, item.group, item.reason


def booked_to(household: Household, item: PileItem) -> str:
    """Where the books hold `item` now: the account its second posting is booked to."""
    (entry,) = [
        e
        for e in household.books().entries
        if isinstance(e, Transaction) and e.meta.get("review-id") == item.review_id
    ]
    return entry.postings[1].account


def connected(tmp_path: Path, clock: FakeClock) -> Household:
    clock.now = NOW
    fresh = first_day(tmp_path, clock, institutions=(BANK,))
    item = fresh.link("avery", BANK.id)
    fresh.map(fresh.signed_in("avery"), {"0707": CHECKING})
    household = Household(fresh, item, clock)
    household.books()
    return household


def test_one_merchant_written_three_ways_is_one_group_suggesting_the_banks_category(
    tmp_path: Path, clock: FakeClock
) -> None:
    household = connected(tmp_path, clock)
    rows = [household.row(r.name, str(r.amount)) for r in BEANS]

    assert {r.proposed for r in rows} == {PLAID_COFFEE}
    assert {r.group for r in rows} == {"Food and drink: coffee"}
    assert {r.reason for r in rows} == {Reason(Why.BANK, "Bank says Coffee")}
    page = household.client().get("/review?view=rows").text
    assert "Bank says Coffee" in page
    assert "Bank says Restaurant" in page


def test_a_remembered_merchant_is_suggested_but_never_accepted_and_an_accept_never_moves(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Avery accepts every row of the merchant at once, and a fourth that arrives after is
    suggested from that decision; Avery accepts it too, then books one of the first three
    elsewhere. Nothing accepted comes back to Review, and a fifth row, arriving after the
    decisions disagree, is suggested from the bank again."""
    household = connected(tmp_path, clock)
    first = [household.row(r.name, str(r.amount)) for r in BEANS]
    household.decide(
        "/review/groups/accept",
        group="Food and drink: coffee",
        rows=[f"{r.review_id} {PLAID_COFFEE}" for r in first],
    )

    fourth = FakeTransaction("Zq Bean Counter", Decimal("6.10"), date(2026, 9, 24), COFFEE)
    household.arrive(fourth)
    arrived = household.row("Zq Bean Counter", "6.10")

    # Remembered, but still a row to review: no accepting on anyone's behalf.
    assert arrived.proposed == PLAID_COFFEE
    assert arrived.reason == Reason(Why.REMEMBERED, "Coffee 3 times before")
    assert household.state(arrived) is State.UNREVIEWED
    entry = next(
        e
        for e in household.books().entries
        if isinstance(e, Transaction) and e.meta.get("review-id") == arrived.review_id
    )
    assert entry.flag == "!"
    assert "Coffee 3 times before" in household.client().get("/review?view=rows").text

    household.decide(
        "/review/rows/book", review_id=arrived.review_id, account=PLAID_COFFEE, shown=PLAID_COFFEE
    )
    household.decide("/review/rows/book", review_id=first[0].review_id, account="Expenses:Zq:Gifts")

    found = household.books()
    by_id = {item.review_id: item for item in found.pile}
    # What each accepted row suggests is what it suggested when it was accepted.
    assert suggested(by_id[arrived.review_id]) == suggested(arrived)
    for row in (*first[1:], arrived):
        assert household.state(row) is State.REVIEWED
    assert household.state(first[0]) is State.REVIEWED

    fifth = FakeTransaction("Zq Bean Counter", Decimal("7.20"), date(2026, 10, 1), COFFEE)
    household.arrive(fifth)
    after = household.row("Zq Bean Counter", "7.20")
    assert after.reason == Reason(Why.BANK, "Bank says Coffee")
    for row in (*first, arrived):
        assert household.state(row) is State.REVIEWED


def test_a_category_a_member_chose_is_suggested_back_and_holds_once_accepted(
    tmp_path: Path, clock: FakeClock
) -> None:
    household = connected(tmp_path, clock)
    for row in BEANS:
        item = household.row(row.name, str(row.amount))
        household.decide("/review/rows/book", review_id=item.review_id, account="Expenses:Beans")

    household.arrive(
        FakeTransaction("SQ *Zq Bean Counter", Decimal("6.10"), date(2026, 9, 24), COFFEE)
    )
    arrived = household.row("SQ *Zq Bean Counter", "6.10")
    assert (arrived.proposed, arrived.group, arrived.reason) == (
        "Expenses:Beans",
        "Beans",
        Reason(Why.REMEMBERED, "Beans 3 times before"),
    )

    household.decide(
        "/review/groups/accept", group="Beans", rows=[f"{arrived.review_id} Expenses:Beans"]
    )
    # Every one of the three re-decided as something else, a while after: the accept holds.
    for row in BEANS:
        item = household.row(row.name, str(row.amount))
        household.decide("/review/rows/book", review_id=item.review_id, account="Expenses:Treats")
    assert suggested(household.row("SQ *Zq Bean Counter", "6.10")) == suggested(arrived)
    assert household.state(arrived) is State.REVIEWED
    assert household.books().pile  # the books recompute without failing


# With a ledger imported as the base.

SPENT = (
    ("Zq Bean Counter", "SQ *ZQ BEAN COUNTER", "4.50", date(2026, 8, 3), "Coffee", COFFEE),
    (
        "Zq Noodle Hut",
        "ZQ NOODLE HUT",
        "20.00",
        date(2026, 8, 5),
        "Dining",
        "FOOD_AND_DRINK_RESTAURANT",
    ),
    (
        "Zq Taco Stand",
        "ZQ TACO STAND",
        "12.00",
        date(2026, 8, 7),
        "Dining",
        "FOOD_AND_DRINK_RESTAURANT",
    ),
    (
        "Zq Pho Place",
        "ZQ PHO PLACE",
        "15.00",
        date(2026, 8, 9),
        "Dining",
        "FOOD_AND_DRINK_FAST_FOOD",
    ),
    (
        "Zq Curry Corner",
        "ZQ CURRY CORNER",
        "18.00",
        date(2026, 8, 11),
        "Dining",
        "FOOD_AND_DRINK_RESTAURANT",
    ),
    ("Zq Grocer", "ZQ GROCER", "60.00", date(2026, 8, 13), "Groceries", "FOOD_AND_DRINK_GROCERIES"),
)
"""What the ledger booked from Aug 3 to Aug 13, with the bank's own words and category for
each: the ledger's payee, the bank's name, the amount, the day, the ledger's category and the
bank's."""

LATER = (
    FakeTransaction("ZQ BEAN COUNTER #0042", Decimal("3.75"), date(2026, 9, 2), COFFEE),
    FakeTransaction(
        "Zq Dumpling Den", Decimal("22.00"), date(2026, 9, 4), "FOOD_AND_DRINK_RESTAURANT"
    ),
    FakeTransaction(
        "Zq Burger Barn", Decimal("9.00"), date(2026, 9, 6), "FOOD_AND_DRINK_FAST_FOOD"
    ),
    FakeTransaction("Zq Grocer", Decimal("40.00"), date(2026, 9, 8), "FOOD_AND_DRINK_GROCERIES"),
    FakeTransaction(
        "Zq Hardware Hall", Decimal("30.00"), date(2026, 9, 10), "HOME_IMPROVEMENT_HARDWARE"
    ),
)
"""What the bank sent after the ledger stops, on Aug 20."""

LEDGER_CHECKING = "Assets:Zq:Checking"
HELD = Decimal("1000.00") - sum((Decimal(s[2]) for s in SPENT), Decimal(0))
CURRENT = HELD - sum((r.amount for r in LATER), Decimal(0))

IMPORTED_BANK = FakeInstitution(
    "ins_zq_based",
    "Zq Based Bank",
    (
        FakeAccount(
            "chk",
            "ZQ CHECKING",
            "0808",
            "depository",
            "checking",
            CURRENT,
            transactions=(
                *(
                    FakeTransaction(bank, Decimal(amount), day, plaid)
                    for _, bank, amount, day, _, plaid in SPENT
                ),
                *LATER,
            ),
        ),
    ),
)


def _base() -> str:
    text = 'option "operating_currency" "USD"\n\n'
    for account in (
        LEDGER_CHECKING,
        "Equity:Zq:Opening",
        "Expenses:Zq:Coffee",
        "Expenses:Zq:Dining",
        "Expenses:Zq:Groceries",
        "Expenses:HomeImprovement:Hardware",
    ):
        text += f"2026-08-01 open {account} USD\n"
    text += f'\n2026-08-01 * "Opening"\n  {LEDGER_CHECKING}  1000.00 USD\n  Equity:Zq:Opening\n\n'
    for payee, _, amount, day, category, _ in SPENT:
        text += f'{day} * "{payee}" ""\n  {LEDGER_CHECKING}  -{amount} USD\n'
        text += f"  Expenses:Zq:{category}\n\n"
    # Hardware, bought with cash the ledger kept apart, once: the ledger has the category.
    text += (
        '2026-08-14 * "Zq Hardware Elsewhere" ""\n  Expenses:HomeImprovement:Hardware  8.00 USD\n'
    )
    text += f"  {LEDGER_CHECKING}  -8.00 USD\n\n"
    text += '2026-08-14 * "Zq Hardware Elsewhere" "refund"\n'
    text += "  Expenses:HomeImprovement:Hardware  -8.00 USD\n"
    text += f"  {LEDGER_CHECKING}  8.00 USD\n\n"
    text += f"2026-08-20 balance {LEDGER_CHECKING}  {HELD} USD\n"
    return text


def based(
    tmp_path: Path,
    clock: FakeClock,
    *,
    ruled_first: bool = False,
    others: tuple[FakeInstitution, ...] = (),
    extra: str = "",
) -> Household:
    """Avery's bank connected, the ledger imported and the checking account matched to it.
    With `ruled_first`, Avery accepted the bean counter's September row as the bank said
    before the ledger was imported."""
    clock.now = NOW
    fresh = first_day(tmp_path, clock, institutions=(IMPORTED_BANK, *others))
    item = fresh.link("avery", IMPORTED_BANK.id)
    household = Household(fresh, item, clock)
    conn = fresh.connect()
    try:
        (linked,) = Registry(conn).accounts_of(item)
        if ruled_first:
            with Feed.open(fresh.settings.feed_database) as feed:
                (row,) = [
                    r for r in feed.transactions(linked.account_id) if r.day == date(2026, 9, 2)
                ]
            review_id = plaid_review_id(row.transaction_id)
            RulingStore(conn).record(
                member_id=fresh.member_ids["avery"],
                scope=Scope.ROW,
                group=None,
                action=Action.ACCEPT,
                account=None,
                note=None,
                rows={review_id: PLAID_COFFEE},
                says={review_id: says(LEDGER_CHECKING, row.day, -row.amount, "USD")},
                at=clock(),
            )
    finally:
        conn.close()
    folder = tmp_path / "kept"
    folder.mkdir()
    (folder / "main.beancount").write_text(_base() + extra)
    clock.advance(timedelta(minutes=5))
    import_ledger(fresh.settings, folder / "main.beancount", now=clock())
    conn = fresh.connect()
    try:
        accounts.claim(
            conn,
            ledger_account=LEDGER_CHECKING,
            name="Checking",
            owners=[fresh.member_ids["avery"]],
            now=clock(),
        )
    finally:
        conn.close()
    matching.confirm(fresh.settings, [(linked.account_id, LEDGER_CHECKING)], now=clock())
    household.books()
    return household


def test_with_a_ledger_new_rows_are_suggested_in_its_own_categories_and_say_why(
    tmp_path: Path, clock: FakeClock
) -> None:
    household = based(tmp_path, clock)
    found = {(i.payee, i.amount): i for i in household.books().pile}

    def said(name: str, amount: str) -> tuple[str | None, str, Reason | None]:
        item = found[(name, -Decimal(amount))]
        return item.proposed, item.group, item.reason

    # The ledger books the merchant so, whatever the bank writes it as.
    assert said("ZQ BEAN COUNTER #0042", "3.75") == (
        "Expenses:Zq:Coffee",
        "Zq: coffee",
        Reason(Why.LEDGER, "As your ledger books it"),
    )
    assert said("Zq Grocer", "40.00")[0::2] == (
        "Expenses:Zq:Groceries",
        Reason(Why.LEDGER, "As your ledger books it"),
    )
    # Three restaurants the ledger holds of the bank's all book as Dining.
    assert said("Zq Dumpling Den", "22.00") == (
        "Expenses:Zq:Dining",
        "Zq: dining",
        Reason(Why.BANK, "Bank says Restaurant"),
    )
    # One fast-food row is too few to say what the bank's category is, and the ledger has
    # no category of hearth's name for it: asked plainly, never a category invented.
    assert said("Zq Burger Barn", "9.00") == (
        None,
        "Food and drink: fast food, nothing suggested",
        Reason(Why.NEW, "New merchant"),
    )
    # Hearth's own name for the bank's category, which the ledger has already.
    assert said("Zq Hardware Hall", "30.00") == (
        "Expenses:HomeImprovement:Hardware",
        "Home improvement: hardware",
        Reason(Why.BANK, "Bank says Hardware"),
    )
    page = household.client().get("/review?view=rows").text
    for words in ("As your ledger books it", "Bank says Restaurant", "New merchant"):
        assert words in page
    assert "FoodAndDrink" not in page


def test_a_row_accepted_before_the_ledger_came_stays_booked_where_it_was_accepted(
    tmp_path: Path, clock: FakeClock
) -> None:
    """The ledger now suggests its own category for the row, as for any other; the accept,
    made before the ledger came and before the bank's category was kept with a ruling, holds,
    as the bank's category still names what it accepted."""
    household = based(tmp_path, clock, ruled_first=True)
    item = household.row("ZQ BEAN COUNTER #0042", "3.75")

    assert item.proposed == "Expenses:Zq:Coffee"
    assert household.state(item) is State.REVIEWED
    assert booked_to(household, item) == PLAID_COFFEE
    # Every other row learns from the ledger as it would have.
    assert household.row("Zq Grocer", "40.00").proposed == "Expenses:Zq:Groceries"


def test_a_total_says_how_much_money_nobody_has_placed_yet(
    tmp_path: Path, clock: FakeClock
) -> None:
    """The burger barn's row suggests nothing, so it is in no spending total: September's
    says so to its owner, how many rows and how much, and says nothing of it to anyone else."""
    household = based(tmp_path, clock)
    conn = household.fresh.connect()
    try:
        avery = find(conn, "avery")
        assert avery is not None
        view = Disclosure.load(conn, avery, household.fresh.bookkeeper.status).household()
    finally:
        conn.close()
    spent = view.left_over.out_unreviewed
    assert view.left_over.last.month.month == 9
    assert (spent.unplaced.rows, spent.unplaced.amount) == (1, Decimal("9.00"))
    assert spent.leaves_out

    page = household.client().get("/").text
    assert "Leaves out 1 transaction of" in page
    assert "Leaves out" not in household.fresh.signed_in("blake").get("/").text
