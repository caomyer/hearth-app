"""Asking each connected brokerage for its investment activity, over the stand-in: ``hearth
plaid activity`` and the morning's sync.

Activity is a separate subscription at Plaid from holdings, so it is its own approval; what it
brings is kept in the feed by Plaid's id, for a ledger outside hearth, and never booked.
"""

import argparse
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from hearth import refresh
from hearth.config import Settings
from hearth.plaid import activity as activity_module
from hearth.plaid import cli as plaid_cli
from hearth.plaid import client as client_module
from hearth.plaid.activity import HISTORY_DAYS, REFRESH_DAYS, fetch_activity
from hearth.plaid.approval import Approval, Operation
from hearth.plaid.client import (
    LOGIN_REQUIRED,
    PRODUCT_NOT_READY,
    PlaidMalformedError,
    activity_page,
)
from hearth.plaid.fake import FakeAccount, FakeHolding, FakeInstitution
from hearth.plaid.feed import Feed
from hearth.plaid.registry import ItemState, Registry
from tests.conftest import World, link

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
"""One holding with a ticker, bought and paid four dividends; one without, bought only; and a
contribution each month: 19 activities in all, each well within the history asked for."""

EVERY = 1 + 4 + 1 + 13


@pytest.fixture
def brokerage(world: World) -> tuple[World, str]:
    world.plaid.institutions[BROKERAGE.id] = BROKERAGE
    return world, link(world, "avery", BROKERAGE.id)


def _ask(world: World, setup: Any = None) -> activity_module.Fetched:
    conn = world.connect()
    try:
        return fetch_activity(conn, world.settings.feed_database, setup or world.setup, world.clock)
    finally:
        conn.close()


def _account(world: World, item_id: str) -> str:
    conn = world.connect()
    try:
        (account,) = Registry(conn).accounts_of(item_id)
    finally:
        conn.close()
    return account.account_id


def test_the_first_ask_reaches_back_two_years_and_keeps_every_activity(
    brokerage: tuple[World, str],
) -> None:
    world, item_id = brokerage

    fetched = _ask(world)

    today = world.clock().date()
    (asked,) = fetched.asked
    assert (asked.start, asked.end, asked.activity, asked.problem) == (
        today - timedelta(days=HISTORY_DAYS),
        today,
        EVERY,
        None,
    )
    with Feed.open(world.settings.feed_database) as feed:
        kept = feed.activity(_account(world, item_id))
        securities = feed.securities()
        through = feed.activity_through(item_id)
    assert len(kept) == EVERY
    assert [a.day for a in kept] == sorted(a.day for a in kept)
    (bought,) = [a for a in kept if a.type == "buy" and a.quantity == Decimal(10)]
    assert (bought.price, bought.amount) == (Decimal("1251.07"), Decimal("12510.70"))
    assert all(isinstance(a.amount, Decimal) for a in kept)
    assert {s.ticker for s in securities.values()} == {"ZQAA", None}
    assert through == today


def test_a_later_ask_starts_a_month_before_the_last_and_keeps_each_activity_once(
    brokerage: tuple[World, str],
) -> None:
    world, item_id = brokerage
    _ask(world)

    (asked,) = _ask(world).asked

    assert asked.start == world.clock().date() - timedelta(days=REFRESH_DAYS)
    with Feed.open(world.settings.feed_database) as feed:
        assert len(feed.activity(_account(world, item_id))) == EVERY


def test_every_page_is_asked_for_until_plaids_total(
    brokerage: tuple[World, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    world, _ = brokerage
    monkeypatch.setattr(client_module, "ACTIVITY_PAGE_SIZE", 4)
    world.plaid.calls.clear()

    (asked,) = _ask(world).asked

    assert asked.activity == EVERY
    assert world.plaid.calls.count("/investments/transactions/get") == 5


@pytest.mark.parametrize("code", [PRODUCT_NOT_READY, LOGIN_REQUIRED])
def test_an_ask_that_fails_keeps_nothing_and_the_next_asks_the_whole_history_again(
    brokerage: tuple[World, str], code: str
) -> None:
    world, item_id = brokerage
    world.plaid.fail_next("/investments/transactions/get", code)

    (asked,) = _ask(world).asked

    assert asked.problem == code
    with Feed.open(world.settings.feed_database) as feed:
        assert feed.activity(_account(world, item_id)) == []
        assert feed.activity_through(item_id) is None
    conn = world.connect()
    try:
        state = Registry(conn).item(item_id)
    finally:
        conn.close()
    assert state is not None
    assert (state.state is ItemState.LOGIN_REQUIRED) == (code == LOGIN_REQUIRED)


class Recorder:
    """A transport that records the paths it is asked for and answers each with an error."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    def __call__(self, url: str, body: bytes) -> tuple[int, bytes]:
        self.paths.append(url.removeprefix("https://production.plaid.com"))
        json.loads(body)
        return 400, json.dumps({"error_type": "API_ERROR", "error_code": "TEST"}).encode()


def test_a_real_connection_is_never_asked_unless_activity_is_approved(
    brokerage: tuple[World, str],
) -> None:
    """Holdings approved, as the morning usually is, starts no second subscription."""
    world, _ = brokerage
    conn = world.connect()
    try:
        conn.execute("UPDATE plaid_credentials SET environment = 'production'")
    finally:
        conn.close()
    recorder = Recorder()
    holdings = replace(world.setup, approval=Approval.of([Operation.HOLDINGS]), transport=recorder)

    not_asked = _ask(world, holdings)
    refresh.sync_then_recompute(world.settings, holdings, world.bookkeeper, world.clock)
    approved = replace(holdings, approval=Approval.of([Operation.ACTIVITY]))
    asked = _ask(world, approved)

    assert not_asked.asked == ()
    assert recorder.paths == ["/investments/holdings/get", "/investments/transactions/get"]
    assert [a.problem for a in asked.asked] == ["TEST"]


def test_the_morning_keeps_activity_and_the_books_stay_as_they_were(
    brokerage: tuple[World, str],
) -> None:
    world, _ = brokerage
    world.recompute()
    before = world.bookkeeper.status.books
    assert before is not None

    refreshed = refresh.sync_then_recompute(
        world.settings, world.setup, world.bookkeeper, world.clock
    )

    assert refreshed.activity is not None
    assert [a.activity for a in refreshed.activity.asked] == [EVERY]
    after = world.bookkeeper.status.books
    assert after is not None
    assert after.entries == before.entries


def test_the_command_refuses_without_approval_and_sends_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings.in_directory(tmp_path / "data")
    arguments = argparse.Namespace(plaid_command="activity", approved=False)

    assert plaid_cli.run(arguments, settings) == plaid_cli.REFUSED

    said = capsys.readouterr().out
    assert "Investments Transactions subscription" in said
    assert "Nothing was sent." in said


PAGE = """{
  "accounts": [
    {"account_id": "acct-brk", "type": "investment"},
    {"account_id": "acct-chk", "type": "depository"}
  ],
  "investment_transactions": [
    {"investment_transaction_id": "inv-1", "account_id": "acct-brk", "security_id": "sec-a",
     "date": "2026-03-02", "name": "BUY ZQA", "type": "buy", "subtype": "buy",
     "quantity": 1.5, "price": 20.25, "amount": 30.38, "fees": 0.01,
     "iso_currency_code": "USD", "cancel_transaction_id": null},
    {"investment_transaction_id": "inv-2", "account_id": "acct-brk", "security_id": null,
     "date": "2026-03-01", "name": "CONTRIBUTION", "type": "cash", "subtype": "contribution",
     "quantity": 0, "price": 0, "amount": -250.00, "fees": null,
     "iso_currency_code": "USD", "cancel_transaction_id": null}
  ],
  "securities": [{"security_id": "sec-a", "ticker_symbol": "ZQA", "name": "Zq A", "type": "etf"}],
  "total_investment_transactions": 2
}"""
"""A page with a purchase and a contribution, every value invented."""


def test_a_page_is_read_with_every_number_a_decimal() -> None:
    page = activity_page(json.loads(PAGE, parse_float=Decimal))

    bought, paid_in = page.activity
    assert (bought.quantity, bought.price, bought.amount, bought.fees) == (
        Decimal("1.5"),
        Decimal("20.25"),
        Decimal("30.38"),
        Decimal("0.01"),
    )
    assert (paid_in.security_id, paid_in.subtype, paid_in.amount, paid_in.fees) == (
        None,
        "contribution",
        Decimal("-250.00"),
        None,
    )
    assert page.total == 2
    assert [s.ticker for s in page.securities] == ["ZQA"]


@pytest.mark.parametrize(
    "change",
    [
        lambda raw: raw["investment_transactions"][0].update(account_id="acct-chk"),
        lambda raw: raw["investment_transactions"][0].update(security_id="sec-unlisted"),
        lambda raw: raw["investment_transactions"][0].update(date=None),
        lambda raw: raw.pop("total_investment_transactions"),
    ],
    ids=["not an investment account", "unlisted security", "no date", "no total"],
)
def test_a_page_that_cannot_be_read_as_sent_is_refused(change: Any) -> None:
    raw: dict[str, Any] = json.loads(PAGE, parse_float=Decimal)
    change(raw)

    with pytest.raises(PlaidMalformedError):
        activity_page(raw)
