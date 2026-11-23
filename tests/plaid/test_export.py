"""The file a ledger kept outside hearth imports from: every posted row of one member's accounts,
as the bank sent it, with what hearth's pairing knows that a row alone cannot. Every institution,
payee and figure is invented."""

import json
import stat
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from hearth import config
from hearth.cli import main
from hearth.members import find
from hearth.plaid import export
from hearth.plaid.activity import fetch_activity
from hearth.plaid.feed import Feed
from hearth.plaid.registry import Registry
from hearth.valuation.pricestore import PriceStore
from hearth.valuation.quotes import Quote
from tests.conftest import FakeClock
from tests.firstday import PATHS, FirstDay, first_day
from tests.plaid.test_activity import BROKERAGE
from tests.test_booking import filed  # noqa: F401 - the fixture

CHECKING = PATHS["6616"]
WREN = PATHS["1111"]


def _export(fresh: FirstDay, username: str = "avery") -> dict[str, Any]:
    conn = fresh.connect()
    try:
        member = find(conn, username)
        assert member is not None
        with Feed.open(fresh.settings.feed_database) as feed:
            return export.export(conn, feed, member.id, now=fresh.clock())
    finally:
        conn.close()


def _account(document: dict[str, Any], ledger_account: str) -> dict[str, Any]:
    return next(a for a in document["accounts"] if a["ledger_account"] == ledger_account)


def _row(account: dict[str, Any], name: str) -> dict[str, Any]:
    return next(r for r in account["transactions"] if r["name"] == name)


def test_every_posted_row_is_there_once_as_the_bank_sent_it(filed: FirstDay) -> None:  # noqa: F811
    document = _export(filed)
    assert document["schema"] == "buzz.plaid.v4"
    assert document["prices"] == [], "no brokerage, no prices"

    wren = _account(document, WREN)
    assert wren["account_type"] == "credit"
    assert wren["mask"] == "1111"
    assert wren["institution_name"] == "Willowmere Trust"
    assert wren["connection"]["state"] == "working"
    assert wren["transaction_count"] == len(wren["transactions"])

    names = [r["name"] for r in wren["transactions"]]
    assert names.count("Payment Thank You") == 1
    coffee = [r for r in wren["transactions"] if r["name"] == "Tinderbox Coffee"]
    assert [r["date"] for r in coffee] == ["2026-09-03"], "the pending row is not exported"
    assert coffee[0]["amount"] == "5.75", "Plaid's own sign, as a string, never a float"
    assert coffee[0]["category"] == "FOOD_AND_DRINK_COFFEE"
    assert coffee[0]["pair"] is None


def test_each_side_of_a_payment_names_the_other_though_the_card_has_no_payment_category(
    filed: FirstDay,  # noqa: F811
) -> None:
    document = _export(filed)
    card = _row(_account(document, WREN), "Payment Thank You")
    bank = _row(_account(document, CHECKING), "Payment to Willowmere card ending in 1111")

    assert card["category"] != "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"
    assert card["pair"]["side"] == "card"
    assert card["pair"]["other_account"] == CHECKING
    assert card["pair"]["other_date"] == bank["date"]
    assert bank["pair"]["side"] == "bank"
    assert bank["pair"]["other_account"] == WREN
    assert bank["pair"]["other_mask"] == "1111"


def test_a_payment_to_a_card_nobody_connected_is_a_leg_with_no_other_side(
    filed: FirstDay,  # noqa: F811
) -> None:
    row = _row(_account(_export(filed), CHECKING), "Payment to Fenwick card ending in 9090")
    assert row["pair"]["side"] == "bank"
    assert row["pair"]["other_account"] is None


def test_the_file_holds_one_members_accounts_and_says_nothing_of_anyone_elses(
    filed: FirstDay,  # noqa: F811
) -> None:
    conn = filed.connect()
    try:
        blake = find(conn, "blake")
        assert blake is not None
        conn.execute(
            "UPDATE account_owners SET member_id = ? WHERE account_id ="
            " (SELECT id FROM accounts WHERE ledger_account = ?)",
            (blake.id, WREN),
        )
        conn.commit()
    finally:
        conn.close()

    avery = _export(filed)
    assert WREN not in {a["ledger_account"] for a in avery["accounts"]}
    bank = _row(_account(avery, CHECKING), "Payment to Willowmere card ending in 1111")
    assert bank["pair"]["side"] == "bank"
    assert bank["pair"]["other_account"] is None, "paired, but the card is not Avery's to name"
    assert bank["pair"]["other_date"] is None
    assert bank["pair"]["other_mask"] is None
    assert WREN not in json.dumps(avery)

    assert [a["ledger_account"] for a in _export(filed, "blake")["accounts"]] == [WREN]


def test_a_file_is_written_whole_and_only_its_owner_can_read_it(
    filed: FirstDay,  # noqa: F811
    tmp_path: Path,
) -> None:
    path = tmp_path / "held" / "export.json"
    export.write(path, _export(filed))
    assert json.loads(path.read_text())["schema"] == "buzz.plaid.v4"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["export.json"]


def test_the_command_writes_the_file_and_says_where(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(config.DATA_DIR_VARIABLE, str(tmp_path / "data"))
    main(["add-member", "avery", "--name", "Avery", "--admin"])
    capsys.readouterr()

    main(["export", "--member", "avery"])

    path = tmp_path / "data" / "export.json"
    assert capsys.readouterr().out.strip() == str(path)
    assert json.loads(path.read_text())["accounts"] == []

    with pytest.raises(SystemExit, match="no member called nobody"):
        main(["export", "--member", "nobody"])


SORREL = "Assets:Avery:Sorrel:Individual"


def test_a_brokerage_carries_its_activity_holdings_and_the_price_hearth_kept_each_day(
    tmp_path: Path, clock: FakeClock
) -> None:
    fresh = first_day(tmp_path, clock, institutions=(BROKERAGE,))
    item_id = fresh.link("avery", BROKERAGE.id)
    fresh.map(fresh.signed_in("avery"), {"6021": SORREL})
    conn = fresh.connect()
    try:
        fetch_activity(conn, fresh.settings.feed_database, fresh.setup, fresh.clock)
        (linked,) = Registry(conn).accounts_of(item_id)
        with Feed.open(fresh.settings.feed_database) as feed:
            (bought,) = [
                a
                for a in feed.activity(linked.account_id)
                if a.type == "buy" and a.quantity == Decimal(10)
            ]
        PriceStore(conn).record(
            Quote("ZQAA", bought.day, Decimal("1250.00"), "USD", "plaid", "sec", fresh.clock())
        )
    finally:
        conn.close()

    brokerage = _account(_export(fresh), SORREL)

    assert brokerage["account_type"] == "investment"
    assert brokerage["transactions"] == []
    assert brokerage["activity_count"] == len(brokerage["activity"]) == 19
    assert brokerage["activity_through"] == fresh.clock().astimezone().date().isoformat()
    row = next(a for a in brokerage["activity"] if a["activity_id"] == bought.activity_id)
    assert row["ticker"] == "ZQAA"
    assert [Decimal(row[k]) for k in ("quantity", "price", "amount")] == [
        Decimal(10),
        Decimal("1251.07"),
        Decimal("12510.70"),
    ]
    assert all(isinstance(row[k], str) for k in ("quantity", "price", "amount"))
    assert row["market_price"] == "1250.00", "hearth's own price for the day"
    others = [a for a in brokerage["activity"] if a["ticker"] == "ZQAA" and a is not row]
    assert all(a["market_price"] is None for a in others), "a day hearth did not price"
    tickers = {p["ticker"] for p in brokerage["holdings"]["positions"]}
    assert "ZQAA" in tickers
    assert [a["date"] for a in brokerage["activity"]] == sorted(
        a["date"] for a in brokerage["activity"]
    )


def test_the_file_carries_each_days_price_for_what_the_member_holds_and_nothing_else(
    tmp_path: Path, clock: FakeClock
) -> None:
    fresh = first_day(tmp_path, clock, institutions=(BROKERAGE,))
    fresh.link("avery", BROKERAGE.id)
    fresh.map(fresh.signed_in("avery"), {"6021": SORREL})
    conn = fresh.connect()
    try:
        fetch_activity(conn, fresh.settings.feed_database, fresh.setup, fresh.clock)
        store = PriceStore(conn)
        today = fresh.clock().astimezone().date()
        earlier = today.replace(day=1)
        for symbol, day, price in (
            ("ZQAA", earlier, "1200.00"),
            ("ZQAA", today, "1240.00"),
            ("ZQAA", today, "1250.00"),
            ("ZQZZ", today, "77.00"),
        ):
            store.record(Quote(symbol, day, Decimal(price), "USD", "plaid", "sec", fresh.clock()))
    finally:
        conn.close()

    prices = _export(fresh)["prices"]

    zqaa = {p["date"]: p for p in prices if p["symbol"] == "ZQAA"}
    assert len(zqaa) == len([p for p in prices if p["symbol"] == "ZQAA"]), "one price a day"
    assert zqaa[earlier.isoformat()]["price"] == "1200.00"
    assert zqaa[today.isoformat()] == {
        "symbol": "ZQAA",
        "date": today.isoformat(),
        "price": "1250.00",
        "currency": "USD",
    }, "the current price, never the one it corrected"
    order = [(p["symbol"], p["date"]) for p in prices]
    assert order == sorted(order)
    assert all(p["symbol"] != "ZQZZ" for p in prices), "a price for what nobody here holds"
