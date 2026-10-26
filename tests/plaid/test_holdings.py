"""Asking each connected brokerage for its holdings, over the stand-in: ``hearth plaid holdings``
and the morning's sync.

The household's funds have no price until a brokerage is asked: a brokerage's price is today's
only, so each ask stores it with its source, the Plaid security, its day and when it was
fetched, and the books value the fund at it from then on.
"""

import argparse
import json
import logging
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hearth import cli
from hearth.config import Settings
from hearth.plaid import cli as plaid_cli
from hearth.plaid.approval import Approval, Operation
from hearth.plaid.client import LOGIN_REQUIRED, PRODUCTS_NOT_SUPPORTED
from hearth.plaid.fake import FakeAccount, FakeHolding, FakeInstitution
from hearth.plaid.feed import Feed, Run
from hearth.plaid.holdings import price_holdings
from hearth.plaid.registry import ItemState, Registry
from hearth.valuation.institution import NO_TICKER
from hearth.valuation.networth import Freshness
from hearth.valuation.pricestore import PriceStore, Recorded
from tests import household
from tests.conftest import World, link

PRICED_ON = household.PRICED
"""The last session before the household's today: the day the brokerage's price is for."""

BROKERAGE = FakeInstitution(
    "ins_sorrel",
    "Sorrel Brokerage",
    (
        FakeAccount(
            "ind",
            "Individual",
            "6021",
            "investment",
            "brokerage",
            Decimal("13000.00"),
            holdings=(
                FakeHolding("ZQAA", "Zq Total Market", Decimal(10), Decimal("1251.07")),
                FakeHolding(None, "Sorrel Stable Value Trust", Decimal(40), Decimal("10.00")),
            ),
        ),
    ),
)
SECOND = FakeInstitution(
    "ins_tansy",
    "Tansy Invest",
    (
        FakeAccount(
            "ind",
            "Individual",
            "7733",
            "investment",
            "brokerage",
            Decimal("35000.00"),
            holdings=(FakeHolding("ZQBB", "Zq Bond", Decimal(50), Decimal("702.15")),),
        ),
    ),
)
UNHELD = FakeInstitution(
    "ins_vetch",
    "Vetch Invest",
    (
        FakeAccount(
            "ind",
            "Individual",
            "5150",
            "investment",
            "brokerage",
            Decimal("2000.00"),
            holdings=(FakeHolding("ZQCC", "Zq Small Cap", Decimal(20), Decimal("88.40")),),
        ),
    ),
)
BANK = FakeInstitution(
    "ins_yarrow",
    "Yarrow Bank",
    (FakeAccount("chk", "Checking", "1180", "depository", "checking", Decimal("900.00")),),
)


@pytest.fixture
def unpriced(world: World) -> World:
    """The household as no price source has answered for it: every fund has no price."""
    conn = world.connect()
    try:
        conn.execute("DELETE FROM price")
    finally:
        conn.close()
    world.recompute()
    for institution in (BROKERAGE, SECOND, BANK):
        world.plaid.institutions[institution.id] = institution
    return world


def connected(world: World, institution: FakeInstitution, username: str = "avery") -> str:
    """Link `institution` and sync it once, as Link does, so its accounts are recorded."""
    return link(world, username, institution.id)


def freshness(world: World, symbol: str) -> Freshness:
    books = world.bookkeeper.status.books
    assert books is not None
    (valued,) = [p for p in books.today.positions if p.holding.commodity == symbol]
    return valued.freshness


def test_a_fund_with_no_price_is_valued_at_its_brokerages_price_once_asked(
    unpriced: World, capsys: pytest.CaptureFixture[str]
) -> None:
    world = unpriced
    item_id = connected(world, BROKERAGE)
    assert freshness(world, "ZQAA") is Freshness.UNPRICED

    assert plaid_cli.holdings(world.settings, world.setup, world.clock) == 0
    world.recompute()

    books = world.bookkeeper.status.books
    assert books is not None
    (valued,) = [p for p in books.today.positions if p.holding.commodity == "ZQAA"]
    assert valued.freshness is Freshness.CURRENT
    assert valued.value == Decimal("12510.70")
    quote = valued.quote
    assert quote is not None
    assert (quote.source, quote.as_of, quote.fetched_at) == ("plaid", PRICED_ON, world.clock())
    assert quote.source_ref == "sec-ins_sorrel-ZQAA"
    said = capsys.readouterr().out
    assert f"{item_id} (Sorrel Brokerage): 2 holdings" in said
    assert "Prices stored: 1 new, 0 corrected, 0 already stored" in said
    assert f"Not priced: sec-ins_sorrel-Sorrel Stable Value Trust, {NO_TICKER}." in said


def test_the_morning_sync_asks_every_brokerage_for_its_prices(unpriced: World) -> None:
    world = unpriced
    connected(world, BROKERAGE)
    connected(world, SECOND, "blake")

    cli.sync_then_recompute(world.settings, world.setup, world.bookkeeper, world.clock)

    assert freshness(world, "ZQAA") is Freshness.CURRENT
    assert freshness(world, "ZQBB") is Freshness.CURRENT


def test_the_morning_says_why_it_kept_a_price_no_position_holds_yet(
    unpriced: World, caplog: pytest.LogCaptureFixture
) -> None:
    """Hearth's own books hold no brokerage's positions, so a price may value nothing today;
    it is kept, being today's only, and the log says so rather than paying for it silently."""
    world = unpriced
    world.plaid.institutions[UNHELD.id] = UNHELD
    connected(world, BROKERAGE)
    connected(world, UNHELD, "blake")

    with caplog.at_level(logging.INFO, logger="hearth.cli"):
        cli.sync_then_recompute(world.settings, world.setup, world.bookkeeper, world.clock)

    (said,) = [r.getMessage() for r in caplog.records if "no position" in r.getMessage()]
    assert said.startswith("kept today's price of ZQCC, which no position in the books holds")
    assert "ZQAA" not in said


def test_asking_again_the_same_day_stores_nothing_new(unpriced: World) -> None:
    world = unpriced
    connected(world, BROKERAGE)
    conn = world.connect()
    try:
        price_holdings(conn, world.setup, world.clock)
        world.clock.now += timedelta(hours=2)
        again = price_holdings(conn, world.setup, world.clock)
        revisions = PriceStore(conn).revisions("ZQAA", PRICED_ON, "plaid")
    finally:
        conn.close()

    assert sum(again.recorded.values()) == again.recorded[Recorded.UNCHANGED] == 1
    assert len(revisions) == 1


def test_a_bank_with_no_investment_account_is_never_asked(unpriced: World) -> None:
    world = unpriced
    connected(world, BANK)
    conn = world.connect()
    try:
        priced = price_holdings(conn, world.setup, world.clock)
    finally:
        conn.close()

    assert priced.asked == ()
    assert "/investments/holdings/get" not in world.plaid.calls


def test_one_brokerage_that_cannot_answer_stops_only_itself(unpriced: World) -> None:
    world = unpriced
    first = connected(world, BROKERAGE)
    connected(world, SECOND, "blake")
    world.plaid.fail_next("/investments/holdings/get", PRODUCTS_NOT_SUPPORTED)
    conn = world.connect()
    try:
        priced = price_holdings(conn, world.setup, world.clock)
    finally:
        conn.close()
    world.recompute()

    assert [(a.item_id, a.problem) for a in priced.asked if a.problem] == [
        (first, PRODUCTS_NOT_SUPPORTED)
    ]
    assert freshness(world, "ZQAA") is Freshness.UNPRICED
    assert freshness(world, "ZQBB") is Freshness.CURRENT


def test_a_brokerage_wanting_its_member_to_sign_in_again_is_marked_so(unpriced: World) -> None:
    world = unpriced
    item_id = connected(world, BROKERAGE)
    world.plaid.require_login(item_id)
    conn = world.connect()
    try:
        priced = price_holdings(conn, world.setup, world.clock)
        item = Registry(conn).item(item_id)
    finally:
        conn.close()

    assert [a.problem for a in priced.asked] == [LOGIN_REQUIRED]
    assert item is not None
    assert item.state is ItemState.LOGIN_REQUIRED


class Recorder:
    """Plaid as a real connection reaches it: records each request and answers nothing usable."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    def __call__(self, url: str, body: bytes) -> tuple[int, bytes]:
        self.paths.append(url.removeprefix("https://production.plaid.com"))
        json.loads(body)
        return 400, json.dumps({"error_type": "API_ERROR", "error_code": "TEST"}).encode()


def test_a_real_connection_is_never_asked_unless_holdings_are_approved(unpriced: World) -> None:
    """A run approved to sync, as most runs are, adds no product to anybody's Item."""
    world = unpriced
    connected(world, BROKERAGE)
    conn = world.connect()
    try:
        conn.execute("UPDATE plaid_credentials SET environment = 'production'")
        recorder = Recorder()
        reading = replace(world.setup, approval=Approval.of([Operation.READ]), transport=recorder)
        not_asked = price_holdings(conn, reading, world.clock)
        sent_unapproved = list(recorder.paths)
        approved = replace(reading, approval=Approval.of([Operation.HOLDINGS]))
        asked = price_holdings(conn, approved, world.clock)
    finally:
        conn.close()

    assert not_asked.asked == ()
    assert sent_unapproved == []
    assert recorder.paths == ["/investments/holdings/get"]
    assert [a.problem for a in asked.asked] == ["TEST"]


def _runs(world: World, item_id: str) -> list[Run]:
    with Feed.open(world.settings.feed_database) as feed:
        return feed.runs(item_id, now=world.clock())


def test_a_morning_approved_only_for_holdings_syncs_no_real_connection(unpriced: World) -> None:
    """Reading holdings is not reading transactions: the morning asks for prices alone, and
    records no sync it was never approved to make."""
    world = unpriced
    item_id = connected(world, BROKERAGE)
    runs = _runs(world, item_id)
    conn = world.connect()
    try:
        conn.execute("UPDATE plaid_credentials SET environment = 'production'")
    finally:
        conn.close()
    recorder = Recorder()
    holdings_only = replace(
        world.setup, approval=Approval.of([Operation.HOLDINGS]), transport=recorder
    )

    cli.sync_then_recompute(world.settings, holdings_only, world.bookkeeper, world.clock)

    assert recorder.paths == ["/investments/holdings/get"]
    assert _runs(world, item_id) == runs


def test_the_command_refuses_without_approval_and_sends_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings.in_directory(tmp_path / "data")
    arguments = argparse.Namespace(plaid_command="holdings", approved=False)

    assert plaid_cli.run(arguments, settings) == plaid_cli.REFUSED

    said = capsys.readouterr().out
    assert "adds Plaid's Investments product" in said
    assert "Nothing was sent." in said


def test_the_page_names_where_each_price_came_from(unpriced: World) -> None:
    world = unpriced
    connected(world, BROKERAGE)
    connected(world, SECOND, "blake")
    cli.sync_then_recompute(world.settings, world.setup, world.bookkeeper, world.clock)

    page = world.signed_in("avery").get("/net-worth").text

    assert "Source: each brokerage&#39;s own price, through Plaid. Fetched today," in page
