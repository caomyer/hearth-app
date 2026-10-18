"""No Plaid key and no access token ever reaches a page, a log, a database file in the clear, an
exception, a repr or a traceback.

The same standard as ``tests/vault/test_no_leak.py``: every failure is rendered the way the most
indiscreet error reporter would, with the whole chain and every frame's local variables.
"""

import json
import logging
from collections.abc import Callable

import pytest

from hearth.plaid.approval import Approval, Operation
from hearth.plaid.client import (
    Credentials,
    Environment,
    PlaidClient,
    PlaidError,
)
from hearth.plaid.fake import FakePlaid
from hearth.vault.secret import Secret
from tests.conftest import World
from tests.test_privacy_boundary import everything_seen
from tests.vault.test_no_leak import render

CLIENT_ID = "client-production-synthetic-7a7a7a"
SECRET = "secret-production-synthetic-5e5e5e"
ACCESS = "access-production-synthetic-0b0b0b0b"


def credentials() -> Credentials:
    return Credentials(Secret(CLIENT_ID), Secret(SECRET), Environment.PRODUCTION)


def answering(status: int, body: bytes) -> Callable[[str, bytes], tuple[int, bytes]]:
    def transport(url: str, sent: bytes) -> tuple[int, bytes]:
        return status, body

    return transport


def failing(url: str, sent: bytes) -> tuple[int, bytes]:
    raise ConnectionResetError("the line dropped")


FAILURES: dict[
    str, tuple[Callable[[str, bytes], tuple[int, bytes]], Callable[[PlaidClient], object]]
] = {
    "unreachable with a token in flight": (failing, lambda c: c.sync_page(Secret(ACCESS), None)),
    "rejected": (
        answering(400, json.dumps({"error_type": "X", "error_code": "Y"}).encode()),
        lambda c: c.accounts(Secret(ACCESS)),
    ),
    "garbled answer": (answering(200, b"\x00not json"), lambda c: c.remove(Secret(ACCESS))),
    # Plaid's answer carries the access token, but not the item id hearth needs with it.
    "an exchange missing its item": (
        answering(200, json.dumps({"access_token": ACCESS}).encode()),
        lambda c: c.exchange(Secret("public-synthetic")),
    ),
    "an exchange that is not an object": (
        answering(200, json.dumps([ACCESS]).encode()),
        lambda c: c.exchange(Secret("public-synthetic")),
    ),
    "malformed transactions beside a token": (
        answering(
            200,
            json.dumps(
                {
                    "added": [{"access_token": ACCESS}],
                    "modified": [],
                    "removed": [],
                    "has_more": False,
                }
            ).encode(),
        ),
        lambda c: c.sync_page(Secret(ACCESS), None),
    ),
}


@pytest.mark.parametrize("failure", FAILURES)
def test_no_client_failure_reveals_a_key_or_a_token(
    failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    transport, call = FAILURES[failure]
    client = PlaidClient(credentials(), Approval.of(Operation), transport)

    with caplog.at_level(logging.DEBUG), pytest.raises(PlaidError) as raised:
        call(client)

    seen = render(raised.value) + caplog.text + repr(client) + repr(credentials())
    for secret in (CLIENT_ID, SECRET, ACCESS):
        assert secret not in seen


def test_no_page_log_or_database_file_holds_a_key_or_a_token_in_the_clear(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        avery = world.signed_in("avery")
        avery.post(
            "/connect/credentials",
            data={
                "csrf": world.csrf(avery),
                "scope": "household",
                "client_id": CLIENT_ID,
                "secret": SECRET,
                "environment": "demo",
                "item_limit": "10",
                "used_elsewhere": "0",
            },
        )
        seen = [everything_seen(world, world.signed_in(u)) for u in ("avery", "blake", "casey")]
    tokens = [item.access_token for item in world.plaid._items.values()]
    keys = [
        CLIENT_ID,
        SECRET,
        *(f"{kind}-{u}" for kind in ("client", "secret") for u in ("avery", "blake", "casey")),
    ]
    assert tokens

    files = b"".join(
        path.read_bytes()
        for path in world.settings.data_dir.iterdir()
        if path.suffix in {".sqlite3", ".sqlite3-wal"} or path.name.endswith("-wal")
    )
    for secret in [*tokens, *keys]:
        assert secret.encode() not in files, secret
        assert secret not in caplog.text
        assert all(secret not in page for page in seen), secret


def test_a_refused_step_carries_nothing_of_the_call_that_failed(world: World) -> None:
    from hearth.plaid.connector import Refused

    world.plaid.fail_next("/item/public_token/exchange", "INVALID_PUBLIC_TOKEN")
    with world.connector("casey") as connector:
        start = connector.start_link()
        # Held as a Secret here too: this frame is part of what is rendered.
        token = Secret(world.plaid.public_token(start.link_token, "ins_casey"))
        with pytest.raises(Refused) as raised:
            connector.finish_link(token, "")

    assert raised.value.plaid_code == "INVALID_PUBLIC_TOKEN"
    shown = render(raised.value)
    assert token.reveal() not in shown
    for item in world.plaid._items.values():
        assert item.access_token not in shown


def test_the_stand_in_reaches_nothing_but_itself(fake: FakePlaid) -> None:
    with pytest.raises(ValueError, match="only requests addressed to it"):
        fake("https://production.plaid.com/item/get", b"{}")
