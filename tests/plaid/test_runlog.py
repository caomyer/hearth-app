"""The runtime log: what hearth asked of Plaid and how it went, and nothing that is anyone's.

A connection is made, synced, refused and abandoned through the pages, against the stand-in
posing as Plaid's production, and the log hearth wrote is read back whole.
"""

import logging
import logging.handlers
import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from hearth import runlog
from tests import household
from tests.conftest import World
from tests.plaid.linking import SERVED_AT, as_production, dana_at, opened_token


@pytest.fixture
def log(world: World) -> Iterator[Path]:
    path = runlog.configure(world.settings.data_dir)
    try:
        yield path
    finally:
        runlog.stop(world.settings.data_dir)


def test_the_log_is_in_the_data_directory_and_only_its_owner_may_read_it(
    world: World, log: Path
) -> None:
    logging.getLogger("hearth.plaid.client").info("written")

    assert log == world.settings.data_dir / "hearth.log"
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    assert "INFO hearth.plaid.client: written" in log.read_text()
    handlers = len(logging.getLogger("hearth").handlers)
    assert runlog.configure(world.settings.data_dir) == log
    assert len(logging.getLogger("hearth").handlers) == handlers


def test_every_file_the_log_rotates_into_stays_only_its_owners(world: World, log: Path) -> None:
    (handler,) = (
        h
        for h in logging.getLogger("hearth").handlers
        if isinstance(h, logging.handlers.RotatingFileHandler) and h.baseFilename == str(log)
    )
    handler.maxBytes = 200
    umask = os.umask(0o022)
    try:
        for n in range(40):
            logging.getLogger("hearth.plaid.client").info("line %d %s", n, "x" * 60)
    finally:
        os.umask(umask)

    files = [log, *(log.with_name(f"{log.name}.{n}") for n in (1, 2, 3))]
    assert {f.name: stat.S_IMODE(f.stat().st_mode) for f in files} == {f.name: 0o600 for f in files}


def test_stopping_writes_no_more(world: World, log: Path) -> None:
    runlog.stop(world.settings.data_dir)
    logging.getLogger("hearth").warning("after")

    assert "after" not in log.read_text()


def test_the_log_holds_every_plaid_operation_and_its_outcome_and_nothing_of_anyones(
    world: World, log: Path
) -> None:
    relay = as_production(world)
    dana = dana_at(world, SERVED_AT)

    link_token = opened_token(dana.post("/connect/link", data={"csrf": world.csrf(dana)}).text)
    public = world.plaid.public_token(link_token, "ins_casey")
    world.plaid.fail_next("/accounts/get", "INSTITUTION_NOT_RESPONDING")
    dana.post(
        "/connect/exchange",
        data={"csrf": world.csrf(dana), "public_token": public, "institution": "ignored"},
    )
    dana.post(
        "/connect/exited",
        data={
            "csrf": world.csrf(dana),
            "status": "requires_oauth",
            "last_event": "OPEN_OAUTH",
            "institution_id": "ins_casey",
            "institution_name": "Kestrel Trust",
            "request_id": "Zq7RequestSynthetic01",
        },
    )
    written = log.read_text()

    # Which operation, which institution, and how each went, with Plaid's request ids.
    for path in ("/link/token/create", "/item/public_token/exchange", "/item/get"):
        assert f"production {path}: ok request_id=demo" in written, path
    assert (
        "production /accounts/get: refused API_ERROR INSTITUTION_NOT_RESPONDING (HTTP 400)"
        " request_id=demo\n"
    ) in written
    casey = next(p for p in household.PLAID if p.username == "casey")
    assert f"connected {casey.institution} as item item-demo-" in written
    assert "Link closed without connecting" in written
    assert "status=requires_oauth last_event=OPEN_OAUTH" in written
    assert "request_id=Zq7RequestSynthetic01" in written
    assert relay.sent

    # Never a key, a token, a balance, an account or a payee.
    never = [
        "client-dana",
        "secret-dana",
        link_token,
        public,
        *(item.access_token for item in world.plaid._items.values()),
    ]
    for p in household.PLAID:
        never += [p.checking, p.card, p.checking_mask, p.card_mask, p.payee]
        for balance in (p.checking_balance, p.card_balance):
            never += [balance, balance.replace(",", "")]
    never += ["42.00", "Dana"]
    leaked = [value for value in never if value in written]
    assert not leaked, leaked
