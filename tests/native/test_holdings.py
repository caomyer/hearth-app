"""The app's Holdings, and a security's price history, from disclosure's views.

The figures are worked out by hand: from ``tests/household.py``'s securities and stored
prices, and from the lots, prices and days this file adds to its ledger, each chosen so that
one holding is in each state a holding can be in. What each viewer may be told is checked
against the contract in ``tests/household.py`` by the leak crawl in ``test_doorway.py``.
"""

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from hearth.native.doorway import Doorway
from hearth.sharing import DEFAULT_SHARED, Facet
from hearth.valuation.pricestore import PriceStore
from hearth.valuation.quotes import Quote
from tests import household
from tests.conftest import FakeClock, World
from tests.native.test_doorway import differences
from tests.test_positions import HOLDINGS, TRUST, connected

FIXTURES = Path(__file__).parent / "fixtures"
BROKERAGE_NAME = "Card 4242"
"""What ``tests.firstday`` files the Fernhill brokerage as, by its last four."""
EVERYTHING = frozenset(Facet)

STALE_DAY = date(2026, 10, 5)
"""The last close stored for ZQDD: seven weekday sessions before the books' own day."""

ADDED = f"""
{household.OPENED} commodity ZQCC
  name: "Zq Copper Miners"
{household.OPENED} commodity ZQDD
  name: "Zq Dune Index"
{household.OPENED} commodity ZQEE
  name: "Zq Estuary Fund"

2026-10-01 * "Bought more ZQAA"
  Assets:Zq:AveryInvest  5 ZQAA {{1000.00 USD}}
  Equity:Opening

2026-10-01 * "Bought ZQCC, which nothing prices"
  Assets:Zq:AveryInvest  3 ZQCC {{50.00 USD}}
  Equity:Opening

2026-10-01 * "Bought ZQDD, whose price stopped"
  Assets:Zq:AveryInvest  4 ZQDD {{20.00 USD}}
  Equity:Opening

2026-10-01 * "Moved in ZQEE, at no cost the books hold"
  Assets:Zq:AveryInvest  2 ZQEE
  Equity:Opening
"""
"""Avery's Wombat Shares, besides its 10 ZQAA at 1,234.56: 5 more ZQAA at 1,000.00, a fund no
source prices, one whose last price is old, and one whose cost the books do not hold."""


def ask(world: World, viewer: str, screen: str, **given: object) -> dict[str, Any]:
    return Doorway(world.settings, world.bookkeeper, viewer).answer(screen, given)


def held(answer: dict[str, Any], account: str) -> dict[str, dict[str, Any]]:
    found = next(a for a in answer["accounts"] if a["name"] == account)
    return {h["symbol"]: h for h in found["held"]}


def record(world: World, symbol: str, day: date, price: str, source: str = "eodhd") -> None:
    conn = world.connect()
    try:
        PriceStore(conn).record(
            Quote(symbol, day, Decimal(price), "USD", source, "test", world.clock())
        )
    finally:
        conn.close()


@pytest.fixture
def mixed(world: World) -> World:
    """The household, with Avery's Wombat Shares holding one of every kind of holding."""
    world.ledger.write_text(household.ledger_text() + ADDED)
    record(world, "ZQDD", STALE_DAY, "25.00")
    record(world, "ZQEE", household.PRICED, "7.50")
    world.recompute()
    return world


def test_each_holding_has_its_units_price_value_cost_and_gain_to_the_cent(world: World) -> None:
    answer = ask(world, "avery", "holdings")

    assert answer["state"] == "ready"
    assert [a["name"] for a in answer["accounts"]] == ["Wombat Shares"]
    zqaa = held(answer, "Wombat Shares")["ZQAA"]
    assert zqaa["units"] == "10"
    assert zqaa["price"] == {
        "value": "1234.56",
        "day": household.PRICED.isoformat(),
        "source": "eodhd",
        # Stored when the household was, at noon of its day where hearth runs.
        "fetched_at": household.NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "age": 1,
    }
    # Ten at 1,234.56, bought at the price it closes at: worth what it cost.
    assert zqaa["value"] == "12345.60"
    assert zqaa["cost"] == "12345.60"
    assert zqaa["gain"] == "0.00"
    assert zqaa["freshness"] == "current"
    assert answer["priced_on"] == household.PRICED.isoformat()


def test_cash_in_a_brokerage_is_cash_and_gains_nothing(world: World) -> None:
    answer = ask(world, "blake", "holdings")

    bilby = held(answer, "Bilby Brokerage")
    assert list(bilby) == ["ZQBB", "USD"]
    assert bilby["ZQBB"]["value"] == "35000.00"
    cash = bilby["USD"]
    assert cash["is_cash"] is True
    assert cash["value"] == cash["cost"] == "38519.27"
    assert cash["price"] is None
    assert cash["gain"] is None
    totals = answer["accounts"][0]["totals"]
    assert Decimal(totals["value"]) == Decimal(household.BALANCE["blake_solo"].replace(",", ""))
    assert totals["ungained"] == 0


def test_every_absence_in_an_account_is_its_own(mixed: World) -> None:
    answer = ask(mixed, "avery", "holdings")
    wombat = held(answer, "Wombat Shares")

    # Fifteen ZQAA: ten at 1,234.56 and five at 1,000.00, all worth 1,234.56 now.
    assert wombat["ZQAA"]["units"] == "15"
    assert wombat["ZQAA"]["cost"] == "17345.60"
    assert wombat["ZQAA"]["value"] == "18518.40"
    assert wombat["ZQAA"]["gain"] == "1172.80"
    # No price at all: not worth zero, and no gain.
    copper = wombat["ZQCC"]
    assert copper["name"] == "Zq Copper Miners"
    assert (copper["freshness"], copper["price"], copper["value"]) == ("unpriced", None, None)
    assert copper["cost"] == "150.00"
    assert copper["gain"] is None
    # An old price is used, with its day and its age.
    dune = wombat["ZQDD"]
    assert dune["freshness"] == "stale"
    assert dune["price"]["day"] == STALE_DAY.isoformat()
    assert dune["price"]["age"] == 10
    assert (dune["value"], dune["cost"], dune["gain"]) == ("100.00", "80.00", "20.00")
    # A cost the books do not hold has no gain, never one against zero.
    estuary = wombat["ZQEE"]
    assert (estuary["value"], estuary["cost"], estuary["gain"]) == ("15.00", None, None)
    # Securities by what they are called, by their symbol when the books name them nothing,
    # then cash, as the design lists them.
    assert list(wombat) == ["ZQCC", "ZQDD", "ZQEE", "ZQAA"]

    totals = answer["accounts"][0]["totals"]
    assert totals == {
        "value": "18633.40",
        "unpriced": 1,
        "cost": "17575.60",
        "uncosted": 1,
        "gain": "1192.80",
        "ungained": 2,
        "waiting": 0,
    }
    assert answer["totals"] == totals


def test_anothers_positions_are_withheld_and_inside_their_total_only_where_it_is_shared(
    world: World,
) -> None:
    answer = ask(world, "casey", "holdings")

    assert answer["accounts"] == []
    assert answer["withheld"] == [
        {"name": "Avery", "owners": ["Avery"], "joint": False, "in_total": True},
        {"name": "Blake", "owners": ["Blake"], "joint": False, "in_total": True},
        {"name": "Joint accounts", "owners": ["Avery", "Blake"], "joint": True, "in_total": True},
    ]

    world.choose("avery", DEFAULT_SHARED - {Facet.NET_WORTH_TOTAL})
    withheld = ask(world, "casey", "holdings")["withheld"]

    assert [w["in_total"] for w in withheld] == [False, True, False]


def test_positions_shared_with_their_balances_are_listed_as_someone_elses(world: World) -> None:
    world.choose("avery", DEFAULT_SHARED | {Facet.ACCOUNT_BALANCES})

    answer = ask(world, "casey", "holdings")

    assert [(a["name"], a["owners"], a["yours"]) for a in answer["accounts"]] == [
        ("Wombat Shares", ["Avery"], False)
    ]
    assert held(answer, "Wombat Shares")["ZQAA"]["value"] == "12345.60"
    assert "Avery" not in [w["name"] for w in answer["withheld"]]


def test_a_securitys_price_history_is_every_close_hearth_holds_of_it(world: World) -> None:
    answer = ask(world, "avery", "price_history", symbol="ZQAA")

    assert answer["state"] == "ready"
    assert answer["symbol"] == answer["ticker"] == "ZQAA"
    assert answer["price"]["value"] == "1234.56"
    assert answer["closes"] == [
        {"day": "2026-09-30", "price": "1234.56", "source": "eodhd"},
        {"day": household.PRICED.isoformat(), "price": "1234.56", "source": "eodhd"},
    ]


def test_a_day_priced_twice_takes_the_price_fetched_last_as_a_valuation_does(
    world: World,
) -> None:
    world.clock.advance(timedelta(hours=1))
    record(world, "ZQAA", household.PRICED, "1240.00", source="hand")
    world.recompute()

    answer = ask(world, "avery", "price_history", symbol="ZQAA")

    assert answer["closes"][-1] == {
        "day": household.PRICED.isoformat(),
        "price": "1240.00",
        "source": "hand",
    }
    assert answer["price"]["value"] == "1240.00"
    assert answer["price"]["source"] == "hand"


@pytest.mark.parametrize("symbol", ["ZQBB", "USD", "ZQZZ"])
def test_the_history_of_a_security_the_viewer_holds_none_of_is_missing(
    world: World, symbol: str
) -> None:
    """ZQBB is Blake's alone: that it is held at all is Blake's, so Avery is told what a
    symbol nobody holds is told."""
    assert ask(world, "avery", "price_history", symbol=symbol) == {"state": "missing"}


def test_a_shared_position_shows_its_history_to_whoever_it_is_shared_with(world: World) -> None:
    assert ask(world, "casey", "price_history", symbol="ZQBB") == {"state": "missing"}

    world.choose("blake", DEFAULT_SHARED | {Facet.ACCOUNT_BALANCES})

    assert ask(world, "casey", "price_history", symbol="ZQBB")["closes"][0]["price"] == "700.00"


@pytest.mark.parametrize(
    "given",
    [{}, {"symbol": ""}, {"symbol": 7}, {"symbol": "x" * 65}, {"symbol": "ZQAA", "day": "x"}],
)
def test_a_price_history_asked_for_in_another_shape_is_missing(world: World, given: object) -> None:
    door = Doorway(world.settings, world.bookkeeper, "avery")

    assert door.answer("price_history", given) == {"state": "missing"}  # type: ignore[arg-type]


def test_holdings_take_nothing_to_narrow_them(world: World) -> None:
    door = Doorway(world.settings, world.bookkeeper, "avery")

    assert door.answer("holdings", {"symbol": "ZQAA"}) == {"state": "missing"}


# A brokerage in hearth's own books


def test_what_a_brokerage_reported_has_no_cost_the_books_hold(
    tmp_path: Path, clock: FakeClock
) -> None:
    fresh = connected(tmp_path, clock)
    door = Doorway(fresh.settings, fresh.bookkeeper, "avery")

    answer: dict[str, Any] = door.answer("holdings")
    found = next(a for a in answer["accounts"] if a["name"] == BROKERAGE_NAME)

    by_symbol = {h["symbol"]: h for h in found["held"]}
    growth = by_symbol["ZQGR"]
    assert growth["units"] == "37.0625"
    assert growth["name"] == "Fernhill Growth"
    assert growth["price"]["source"] == "plaid"
    assert growth["price"]["value"] == "212.48"
    assert growth["value"] == "7875.040000"
    assert (growth["cost"], growth["gain"]) == (None, None)
    assert by_symbol[TRUST]["ticker"] is None
    assert by_symbol["USD"]["value"] == "244.46"
    assert found["totals"]["uncosted"] == len(HOLDINGS)
    assert found["totals"]["ungained"] == len(HOLDINGS)
    assert found["totals"]["cost"] == "244.46"
    assert Decimal(found["totals"]["value"]) == Decimal("10000.00")


def test_a_brokerage_that_has_not_said_what_it_holds_says_so_to_its_owner_alone(
    tmp_path: Path, clock: FakeClock
) -> None:
    fresh = connected(tmp_path, clock, ready=False)
    conn = fresh.connect()
    try:
        conn.execute(
            "INSERT INTO sharing_preferences (member_id, facet, shared, updated_at)"
            " SELECT id, ?, 1, ? FROM members WHERE username = 'avery'"
            " ON CONFLICT (member_id, facet) DO UPDATE SET shared = 1",
            (Facet.ACCOUNT_BALANCES.value, clock().isoformat()),
        )
    finally:
        conn.close()

    mine: dict[str, Any] = Doorway(fresh.settings, fresh.bookkeeper, "avery").answer("holdings")
    theirs: dict[str, Any] = Doorway(fresh.settings, fresh.bookkeeper, "blake").answer("holdings")

    for answer, why in ((mine, "investment"), (theirs, None)):
        account = next(a for a in answer["accounts"] if a["name"] == BROKERAGE_NAME)
        assert (account["known"], account["why"], account["held"]) == (False, why, [])
        assert answer["totals"]["waiting"] == 1


# One shape, checked from both sides


@pytest.mark.parametrize("screen", ["holdings", "price_history"])
def test_each_screen_has_the_shape_the_app_decodes(mixed: World, screen: str) -> None:
    """Each example in ``fixtures`` is what the app's own tests decode (``apple/HearthKit``)."""
    example = json.loads((FIXTURES / f"{screen}.json").read_text())
    given = {"symbol": "ZQAA"} if screen == "price_history" else {}

    assert differences(ask(mixed, "avery", screen, **given), example["answer"]) == []
