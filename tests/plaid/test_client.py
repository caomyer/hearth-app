"""The client refuses every unapproved call before sending anything, and reads Plaid exactly."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from hearth.plaid.approval import NOTHING_APPROVED, Approval, ApprovalRequired, Operation
from hearth.plaid.client import (
    Credentials,
    Environment,
    PlaidClient,
    PlaidMalformedError,
    PlaidRejectedError,
    PlaidUnreachableError,
    held,
    https_transport,
)
from hearth.plaid.fake import FakePlaid
from hearth.vault.secret import Secret
from tests.plaid.conftest import CLIENT_ID, SECRET


class Recorder:
    """A transport that records what it is asked and answers with what it is told."""

    def __init__(self, status: int = 200, body: object = None) -> None:
        self.sent: list[tuple[str, dict[str, object]]] = []
        self.status = status
        self.body = body if body is not None else {}

    def __call__(self, url: str, body: bytes) -> tuple[int, bytes]:
        self.sent.append((url, json.loads(body)))
        return self.status, json.dumps(self.body).encode()


def production(transport: Recorder, approval: Approval) -> PlaidClient:
    credentials = Credentials(Secret(CLIENT_ID), Secret(SECRET), Environment.PRODUCTION)
    return PlaidClient(credentials, approval, transport)


TOKEN = Secret("access-production-synthetic-0000")

CALLS: dict[str, tuple[Operation, Callable[[PlaidClient], object]]] = {
    "link token": (Operation.LINK, lambda c: c.create_link_token(user="u", days_requested=730)),
    "update link token": (
        Operation.LINK,
        lambda c: c.create_link_token(user="u", days_requested=730, access_token=TOKEN),
    ),
    "exchange": (Operation.LINK, lambda c: c.exchange(Secret("public-synthetic"))),
    "item": (Operation.READ, lambda c: c.institution(TOKEN)),
    "accounts": (Operation.READ, lambda c: c.accounts(TOKEN)),
    "transactions": (Operation.READ, lambda c: c.sync_page(TOKEN, None)),
    "remove": (Operation.REMOVE, lambda c: c.remove(TOKEN)),
    "holdings": (Operation.HOLDINGS, lambda c: c.holdings(TOKEN)),
}


@pytest.mark.parametrize("call", CALLS)
def test_no_call_is_sent_without_approval(call: str) -> None:
    transport = Recorder()
    _, make = CALLS[call]

    with pytest.raises(ApprovalRequired, match="nothing was sent"):
        make(production(transport, NOTHING_APPROVED))

    assert transport.sent == []


@pytest.mark.parametrize("call", CALLS)
def test_approving_one_operation_approves_no_other(call: str) -> None:
    operation, make = CALLS[call]
    for other in Operation:
        if other is operation:
            continue
        transport = Recorder()
        with pytest.raises(ApprovalRequired):
            make(production(transport, Approval.of([other])))
        assert transport.sent == []


def test_a_new_connection_asks_for_two_years_of_transactions_and_room_for_investments() -> None:
    transport = Recorder(body={"link_token": "link-production-synthetic"})

    production(transport, Approval.of([Operation.LINK])).create_link_token(
        user="hearth-member-7", days_requested=730
    )

    ((url, sent),) = transport.sent
    assert url == "https://production.plaid.com/link/token/create"
    assert sent["transactions"] == {"days_requested": 730}
    assert sent["products"] == ["transactions"]
    assert sent["optional_products"] == ["investments"]
    assert sent["user"] == {"client_user_id": "hearth-member-7"}
    assert (sent["client_id"], sent["secret"]) == (CLIENT_ID, SECRET)


def test_signing_in_again_uses_the_existing_item_and_asks_for_no_products() -> None:
    transport = Recorder(body={"link_token": "link-production-synthetic"})

    production(transport, Approval.of([Operation.LINK])).create_link_token(
        user="u", days_requested=730, access_token=TOKEN
    )

    ((_, sent),) = transport.sent
    assert sent["access_token"] == TOKEN.reveal()
    assert "products" not in sent
    assert "transactions" not in sent


def test_plaids_errors_are_named_by_its_own_code() -> None:
    transport = Recorder(
        status=400,
        body={
            "error_type": "ITEM_ERROR",
            "error_code": "ITEM_LOGIN_REQUIRED",
            "error_message": "x",
        },
    )

    with pytest.raises(PlaidRejectedError) as raised:
        production(transport, Approval.of([Operation.READ])).accounts(TOKEN)

    assert raised.value.error_code == "ITEM_LOGIN_REQUIRED"
    assert raised.value.status == 400


def test_an_unreachable_plaid_and_a_malformed_answer_are_told_apart() -> None:
    def down(url: str, body: bytes) -> tuple[int, bytes]:
        raise ConnectionRefusedError("no route")

    credentials = Credentials(Secret(CLIENT_ID), Secret(SECRET), Environment.PRODUCTION)
    with pytest.raises(PlaidUnreachableError, match="ConnectionRefusedError"):
        PlaidClient(credentials, Approval.of(Operation), down).accounts(TOKEN)

    def garbled(url: str, body: bytes) -> tuple[int, bytes]:
        return 200, b"<html>not json</html>"

    with pytest.raises(PlaidMalformedError):
        PlaidClient(credentials, Approval.of(Operation), garbled).accounts(TOKEN)


def test_amounts_are_decimals_with_plaids_sign(client: PlaidClient, fake: FakePlaid) -> None:
    link = client.create_link_token(user="u", days_requested=730)
    exchanged = client.exchange(Secret(fake.public_token(link.token, "ins_pinecone")))

    page = client.sync_page(exchanged.access_token, None)
    (card,) = client.accounts(exchanged.access_token)

    assert card.current == Decimal("684.12")
    assert all(isinstance(t.amount, Decimal) and t.amount > 0 for t in page.added)


def test_holdings_are_read_with_every_number_a_decimal_and_no_cusip(
    client: PlaidClient, fake: FakePlaid
) -> None:
    link = client.create_link_token(user="u", days_requested=730)
    exchanged = client.exchange(Secret(fake.public_token(link.token, "ins_juniper")))

    held = client.holdings(exchanged.access_token)

    assert held["holdings"]
    assert all(isinstance(h["institution_price"], Decimal) for h in held["holdings"])
    assert all(isinstance(h["quantity"], Decimal) for h in held["holdings"])
    assert all(s["cusip"] is None for s in held["securities"])


HOLDINGS = """{
  "accounts": [
    {"account_id": "acct-brk", "type": "investment"},
    {"account_id": "acct-empty", "type": "investment"},
    {"account_id": "acct-chk", "type": "depository"}
  ],
  "holdings": [
    {"account_id": "acct-brk", "security_id": "sec-fund", "quantity": 10.125,
     "institution_value": 1012.50, "iso_currency_code": "USD"},
    {"account_id": "acct-brk", "security_id": "sec-fund", "quantity": 0.5,
     "institution_value": 50.00, "iso_currency_code": "USD"},
    {"account_id": "acct-brk", "security_id": "sec-cash", "quantity": 75.25,
     "institution_value": 75.25, "iso_currency_code": "USD"},
    {"account_id": "acct-brk", "security_id": "sec-mmf", "quantity": 300,
     "institution_value": 300.00, "iso_currency_code": "USD"}
  ],
  "securities": [
    {"security_id": "sec-fund", "ticker_symbol": "ZQFND", "name": "Zq Fund", "type": "etf"},
    {"security_id": "sec-cash", "ticker_symbol": "CUR:USD", "type": "cash"},
    {"security_id": "sec-mmf", "ticker_symbol": "ZQMMF", "type": "mutual fund",
     "is_cash_equivalent": true}
  ]
}"""
"""An investment account holding a fund twice over, cash and a money market fund; another
holding nothing; and a checking account the answer lists beside them. Every value invented."""


def test_what_each_investment_account_holds_is_read_as_its_brokerage_said_it() -> None:
    found = held(json.loads(HOLDINGS, parse_float=Decimal))

    # One that holds nothing is listed, as that is what its brokerage said of it.
    assert found.accounts == ("acct-brk", "acct-empty")
    fund, cash, mmf = found.positions
    # Two holdings of one fund are one position.
    assert (fund.ticker, fund.name, fund.quantity, fund.value, fund.cash) == (
        "ZQFND",
        "Zq Fund",
        Decimal("10.625"),
        Decimal("1062.50"),
        False,
    )
    assert (cash.quantity, cash.value, cash.cash) == (Decimal("75.25"), Decimal("75.25"), True)
    assert (mmf.ticker, mmf.cash) == ("ZQMMF", True)
    assert all(isinstance(p.quantity, Decimal) for p in found.positions)


@pytest.mark.parametrize(
    "change",
    [
        lambda raw: raw["holdings"][0].update(account_id="acct-chk"),
        lambda raw: raw["holdings"][0].update(security_id="sec-unlisted"),
        lambda raw: raw["holdings"][0].update(quantity=None),
        lambda raw: raw["holdings"][0].update(quantity=10.125),
    ],
    ids=["not an investment account", "unlisted security", "no quantity", "a float"],
)
def test_a_holding_that_cannot_be_read_as_sent_is_refused_never_read_as_nothing(
    change: Callable[[dict[str, Any]], None],
) -> None:
    raw: dict[str, Any] = json.loads(HOLDINGS, parse_float=Decimal)
    change(raw)

    with pytest.raises(PlaidMalformedError):
        held(raw)


def test_an_item_that_offers_investments_is_read_with_no_new_sign_in(
    client: PlaidClient, fake: FakePlaid
) -> None:
    link = client.create_link_token(user="u", days_requested=730)
    exchanged = client.exchange(Secret(fake.public_token(link.token, "ins_juniper")))
    assert "investments" in client.item(exchanged.access_token).products

    held = client.holdings(exchanged.access_token)

    assert held["holdings"]
    assert fake.calls.count("/link/token/create") == 1


def test_the_https_transport_sends_nothing_outside_plaid() -> None:
    with pytest.raises(ValueError, match="outside the Plaid API"):
        https_transport("https://example.invalid/item/remove", b"{}")
    with pytest.raises(ValueError, match="outside the Plaid API"):
        https_transport("https://plaid.demo.invalid/item/remove", b"{}")


def test_a_link_token_is_good_for_four_hours_from_when_it_is_made(client: PlaidClient) -> None:
    before = datetime.now(UTC)
    link = client.create_link_token(user="hearth-member-1", days_requested=730)

    assert link.expires is not None
    assert before + timedelta(hours=4) <= link.expires <= datetime.now(UTC) + timedelta(hours=4)


def test_plaids_request_id_is_kept_with_its_refusal(fake: FakePlaid, client: PlaidClient) -> None:
    fake.fail_next("/link/token/create", "INVALID_FIELD")

    with pytest.raises(PlaidRejectedError) as refused:
        client.create_link_token(user="hearth-member-1", days_requested=730)

    assert (refused.value.error_code, refused.value.request_id) == ("INVALID_FIELD", "demo")
