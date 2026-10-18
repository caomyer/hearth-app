"""The boundary on what the dashboards added: failures, connections, prices, review and floors.

Each test states one contract and attacks it as a member who should not get through.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from hearth import books, db
from hearth.app import create_app
from hearth.connections import Connection, KnownConnections, State
from hearth.sharing import DEFAULT_SHARED, Facet
from hearth.valuation.pricestore import PriceStore
from tests import household
from tests.conftest import SourceNotSetUp, World

PAGES = ("/", "/you", "/members/avery", "/members/blake", "/members/casey", "/net-worth", "/review")


def everything(world: World, username: str) -> str:
    client = world.signed_in(username)
    return "\n".join(client.get(path).text for path in PAGES)


def test_a_failed_balance_check_is_explained_only_to_its_accounts_owner(world: World) -> None:
    """Casey's statement disagrees with the ledger. Casey sees the account and the amounts;
    everyone else sees one sentence that names no one, and yesterday's figures stay."""
    world.ledger.write_text(
        world.ledger.read_text() + "\n2026-10-10 balance Assets:Zq:CaseyChecking  9999.99 USD\n"
    )
    world.recompute()
    assert world.bookkeeper.status.failure is not None
    assert world.bookkeeper.status.books is not None

    casey = world.signed_in("casey").get("/net-worth").text
    assert "Assets:Zq:CaseyChecking" in casey
    assert "$9,999.99" in casey

    for username in ("avery", "blake"):
        seen = everything(world, username)
        assert "waiting on a fix to one member" in seen
        assert "Assets:Zq:CaseyChecking" not in seen
        assert "9,999.99" not in seen
        assert "Caracal Checking" not in seen


def test_an_expired_connection_is_named_only_to_the_member_who_holds_it(
    world: World, tmp_path: Path
) -> None:
    known = KnownConnections(
        {
            world.member_ids["blake"]: [
                Connection(
                    world.member_ids["blake"],
                    "Institution Zebrafinch",
                    ("Assets:Zq:BlakeSolo",),
                    State.EXPIRED,
                    date(2026, 10, 9),
                )
            ]
        }
    )
    world.app = create_app(
        world.settings, world.bookkeeper, world.clock, SourceNotSetUp(), known, plaid=world.setup
    )

    blake = world.signed_in("blake")
    assert "Institution Zebrafinch stopped updating" in blake.get("/").text
    assert "Institution Zebrafinch" in blake.get("/settings/connections").text

    for username in ("avery", "casey"):
        client = world.signed_in(username)
        seen = everything(world, username) + client.get("/settings/connections").text
        assert "Zebrafinch" not in seen
        assert "1 account not updated since Oct 9" in client.get("/").text


def test_only_someone_who_holds_a_security_may_price_it_by_hand(world: World) -> None:
    casey = world.signed_in("casey")
    form = {"csrf": world.csrf(casey), "symbol": "ZQAA", "day": "2026-10-14", "price": "1.00"}

    refused = casey.post("/net-worth/prices", data=form)
    missing = casey.post("/net-worth/prices", data={**form, "symbol": "NOPE"})

    assert refused.status_code == 404
    assert refused.text == missing.text
    assert _hand_prices(world) == []

    avery = world.signed_in("avery")
    accepted = avery.post("/net-worth/prices", data={**form, "csrf": world.csrf(avery)})
    assert accepted.headers["location"] == "/net-worth?outcome=price-recorded"
    assert _hand_prices(world) == [("ZQAA", Decimal("1.00"))]


def _hand_prices(world: World) -> list[tuple[str, Decimal]]:
    conn = db.connect(world.settings.database)
    try:
        store = PriceStore(conn)
        found = store.latest("ZQAA", date(2026, 10, 15), "hand")
        return [(found.symbol, found.price)] if found else []
    finally:
        conn.close()


@pytest.mark.parametrize("action", ["/review/rows/book", "/review/rows/reject"])
def test_a_row_on_someone_elses_pile_answers_like_one_that_does_not_exist(
    world: World, action: str
) -> None:
    avery = world.signed_in("avery")
    form = {"csrf": world.csrf(avery), "account": "Expenses:Avocados"}

    theirs = avery.post(action, data={**form, "review_id": "casey-row-1"})
    nobodys = avery.post(action, data={**form, "review_id": "no-such-row"})

    assert theirs.status_code == 404
    assert theirs.text == nobodys.text
    assert "Payee Shrike Pending" in world.signed_in("casey").get("/review").text


def test_sharing_everything_does_not_let_anyone_else_review_a_members_rows(world: World) -> None:
    world.choose("casey", frozenset(Facet))
    review = world.signed_in("avery").get("/review").text

    assert "Payee Shrike Pending" not in review
    assert "All caught up" in review


def test_a_holding_with_no_price_makes_a_floor_and_is_named_only_to_those_who_see_it(
    world: World,
) -> None:
    conn = db.connect(world.settings.database)
    try:
        conn.execute("DELETE FROM price WHERE symbol = 'ZQBB'")
    finally:
        conn.close()
    world.recompute()

    blake = world.signed_in("blake").get("/net-worth").text
    assert "ZQBB has no price at all" in blake
    assert "at least" in blake

    casey = world.signed_in("casey").get("/").text
    assert "1 holding with no price" in casey
    assert "ZQBB" not in casey
    assert household.HOUSEHOLD_NET_WORTH not in casey


def test_the_app_serves_the_design_system_unchanged(world: World) -> None:
    """The stylesheet and script are the design's own, byte for byte; app.css only adds."""
    design = Path(__file__).parent.parent / "design"
    client = world.signed_in("casey")
    for served, original in (("design.css", "hearth.css"), ("design.js", "design.js")):
        response = client.get(f"/static/{served}")
        assert response.status_code == 200
        assert response.content == (design / original).read_bytes()


def test_before_the_first_recompute_pages_show_their_shape_and_no_figures(world: World) -> None:
    waiting = books.Bookkeeper(world.bookkeeper_establish, world.clock)
    world.app = create_app(
        world.settings, waiting, world.clock, SourceNotSetUp(), plaid=world.setup
    )

    page = world.signed_in("casey").get("/").text

    assert 'aria-busy="true"' in page
    assert "Loading household figures." in page
    assert 'hx-trigger="every 2s"' in page
    assert household.NET_WORTH["casey"] not in page


def test_a_first_recompute_that_fails_shows_why_and_never_a_page_of_zeros(world: World) -> None:
    world.ledger.write_text("this is not a ledger\n")
    failing = books.Bookkeeper(world.bookkeeper_establish, world.clock)
    failing.recompute()
    world.app = create_app(
        world.settings, failing, world.clock, SourceNotSetUp(), plaid=world.setup
    )

    page = world.signed_in("casey").get("/").text

    assert "No figures yet" in page
    assert "The ledger has" in page
    assert "$0" not in page


def test_with_no_price_feed_every_total_is_a_labelled_floor(world: World) -> None:
    """The household's default until a price feed is bought: nothing is priced. No page may show
    a total the valuation declined to produce; each one that leaves holdings out says so."""
    conn = db.connect(world.settings.database)
    try:
        conn.execute("DELETE FROM price")
    finally:
        conn.close()
    world.recompute()

    avery = world.signed_in("avery")
    pages = {
        path: avery.get(path).text for path in ("/", "/you", "/net-worth", "/settings/sharing")
    }

    assert all("no price" in page for page in pages.values())
    assert "at least" in pages["/net-worth"]
    assert "A floor" in pages["/settings/sharing"]
    for page in pages.values():
        assert household.HOUSEHOLD_NET_WORTH not in page
        assert household.NET_WORTH["avery"] not in page
    blake = world.signed_in("casey").get("/members/blake").text
    assert "A floor, not a total" in blake
    assert household.NET_WORTH["blake"] not in blake


BILL = "Expenses:Zither"


def _blakes_history(world: World, *october_bills: str) -> None:
    """Six months of books before October: Blake pays the same bill on the 2nd and buys bonsai
    on the 3rd every month, so the bill alone is fixed and every category has a typical by now."""
    text = world.ledger.read_text().replace(str(household.OPENED), "2026-04-01")
    text += f"\n2026-04-01 open {BILL}\n"
    for month, bonsai in zip(
        range(5, 10), ("100.00", "140.00", "90.00", "120.00", "110.00"), strict=True
    ):
        text += _payment(f"2026-{month:02}-02", BILL, "777.77")
        text += _payment(f"2026-{month:02}-03", "Expenses:Bonsai", bonsai)
    for amount in october_bills:
        text += _payment("2026-10-02", BILL, amount)
    world.ledger.write_text(text)
    world.recompute()


def _payment(day: str, category: str, amount: str) -> str:
    return f'\n{day} * "Payee Zither Hall" ""\n  Assets:Zq:BlakeSolo  -{amount} USD\n  {category}\n'


def test_paid_days_and_typical_by_date_need_the_transactions_facet(world: World) -> None:
    """A fixed bill's paid day is one transaction, and typical by this date reads off when money
    went out. Casey, who sees Blake's categories but not his transactions, gets each category's
    amount so far and nothing finer; Blake, and anyone he shares transactions with, get both."""
    _blakes_history(world, "777.77")
    world.choose("blake", DEFAULT_SHARED | {Facet.CATEGORY_BREAKDOWN})

    for page in (world.signed_in("casey").get(p).text for p in ("/", "/members/blake")):
        assert "Zither" in page
        assert "777.77" in page
        assert "Fixed:" not in page
        assert "paid Oct" not in page
        assert 'class="typ"' not in page
        assert "over typical" not in page
        assert "Typical by the" not in page

    for username, shares in (
        ("blake", DEFAULT_SHARED | {Facet.CATEGORY_BREAKDOWN}),
        ("casey", DEFAULT_SHARED | {Facet.CATEGORY_BREAKDOWN, Facet.TRANSACTIONS}),
    ):
        world.choose("blake", shares)
        page = world.signed_in(username).get("/").text
        assert "Fixed:</span> Zither, paid Oct 2" in page
        assert 'class="typ"' in page
        assert "Typical by the" in page


def test_a_fixed_bill_paid_twice_this_month_is_still_counted(world: World) -> None:
    _blakes_history(world, "777.77", "300.00")

    page = world.signed_in("blake").get("/you").text

    assert "Fixed:</span> Zither" not in page
    assert "Zither" in page
    assert "$1,077.77" in page


def test_an_account_whose_balance_is_withheld_does_not_say_what_it_holds(world: World) -> None:
    """Avery shares transactions but not balances: Casey may open Wombat Shares to read its
    transactions, and learns neither its balance nor that it holds securities."""
    world.choose("avery", DEFAULT_SHARED | {Facet.TRANSACTIONS})
    account = world.account_ids["avery_invest"]

    casey = world.signed_in("casey").get(f"/accounts/{account}").text
    assert "Wombat Shares" in casey
    assert "Investments" not in casey
    assert "12,346" not in casey

    avery = world.signed_in("avery").get(f"/accounts/{account}").text
    assert "Investments" in avery
    assert "12,346" in avery


def test_booking_a_row_as_something_that_is_not_an_account_says_why(world: World) -> None:
    casey = world.signed_in("casey")
    form = {"csrf": world.csrf(casey), "review_id": "casey-row-1", "account": "groceries"}

    response = casey.post("/review/rows/book", data=form)
    page = casey.get(response.headers["location"]).text

    assert "That row was not booked. Enter a full account name" in page
    assert "Payee Shrike Pending" in page
    assert "That row was not booked" not in casey.get("/review").text


def test_a_group_with_a_row_nothing_was_suggested_for_offers_no_accept(world: World) -> None:
    text = world.ledger.read_text()
    for review_id, other in (("casey-row-2", "Expenses:Kites"), ("casey-row-3", "")):
        text += (
            f'\n2026-10-04 ! "Payee Shrike Mixed" ""\n  review-id: "{review_id}"\n'
            f'  review-group: "Mixed"\n  Assets:Zq:CaseyChecking  -5.00 USD\n'
            f"  {other or household.UNRESOLVED}\n"
        )
    world.ledger.write_text(text)
    world.recompute()

    page = world.signed_in("casey").get("/review").text

    mixed = page[page.index(">Mixed</h2>") :].split("</section>")[0]
    assert "data-accept-group" not in mixed
    assert 'id="r-casey-row-2"' in mixed  # each row is offered on its own instead


def test_a_month_with_only_a_fixed_bill_so_far_is_not_an_empty_panel(world: World) -> None:
    """Only the fixed bill is paid so far. Finding it took three months of history, so the
    panel neither says there is too little history nor claims a comparison it did not make."""
    _blakes_history(world, "777.77")
    text = world.ledger.read_text()
    for spent in (
        '"Payee Bittern Nursery" ""\n  Assets:Zq:BlakeSolo  -1927.53 USD\n  Expenses:Bonsai\n',
        '"Payee Jacana Hall" ""\n  Assets:Zq:Joint  -1640.84 USD\n  Expenses:Jointery\n',
    ):
        assert spent in text
        text = text.replace(spent, "")
    world.ledger.write_text(text)
    world.recompute()
    blake = world.signed_in("blake")

    for page in (blake.get("/").text, blake.get("/you").text):
        assert "Fixed:</span> Zither, paid Oct 2" in page
        assert "Nobody shares spending by category yet" not in page
        assert "No spending yet this month" not in page
        assert "not enough history" not in page
        assert "close to typical" not in page


def test_a_savings_rate_withheld_by_others_never_reads_as_no_income(world: World) -> None:
    """At the defaults nobody shares income, so September's savings rate cannot be worked out
    for Casey: the tile says whose income is not shared, not that there was none."""
    page = world.signed_in("casey").get("/").text
    assert "No income that month" not in page
    assert "Not shared by Avery, Blake and Joint accounts" in page

    for username in household.USERNAMES:
        world.choose(username, DEFAULT_SHARED | {Facet.MONTHLY_INCOME_TOTAL})
    assert "No income that month" in world.signed_in("casey").get("/").text


def test_without_enough_history_no_category_claims_a_typical(world: World) -> None:
    """The accounts were open from April but their history starts in August, so only two
    months are whole: too few for any typical by this date."""
    world.ledger.write_text(world.ledger.read_text().replace("2026-08-01 open", "2026-04-01 open"))
    world.recompute()
    blake = world.signed_in("blake")
    for path in ("/", "/you"):
        page = blake.get(path).text
        assert "close to typical" not in page
        assert "Typical by the" not in page
        assert 'class="typ"' not in page
    assert "not enough history yet" in blake.get("/").text


def test_the_sharing_preview_lists_a_fixed_bill(world: World) -> None:
    _blakes_history(world, "777.77")
    world.choose("blake", frozenset(Facet))

    preview = world.signed_in("blake").get("/settings/sharing").text

    assert "Zither $778" in preview
