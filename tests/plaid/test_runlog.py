"""The runtime log: what hearth asked of Plaid and how it went, and nothing that is anyone's.

A connection is made, synced, refused and abandoned through the pages, against the stand-in
posing as Plaid's production, and the log hearth wrote is read back whole.
"""

import logging
import logging.handlers
import os
import re
import stat
from collections.abc import Collection, Iterable, Iterator
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


def connect_refused_and_abandoned(world: World) -> list[str]:
    """Dana links Casey's bank, whose accounts fail to load, then closes Link at another bank.
    Returns what must never be logged: every key and token she was handed or hearth holds, and
    every balance, account, mask and payee there is."""
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
    return [*never, "42.00", "Dana"]


def leaked(written: str, never: Iterable[str], issued: Collection[str]) -> list[str]:
    """Each of `never` that the log holds outside the identifiers the stand-in made up.

    Its request ids and Item ids are random hex, and a card's mask is four digits: about one
    log in a hundred has a mask somewhere inside one of them, which is the stand-in's dice and
    not anybody's figure (``household.Seen``)."""
    seen = household.Seen(written, issued)
    return [value for value in never if value in seen]


def test_the_log_holds_every_plaid_operation_and_its_outcome_and_nothing_of_anyones(
    world: World, log: Path
) -> None:
    relay = as_production(world)
    never = connect_refused_and_abandoned(world)
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
    assert not (found := leaked(written, never, world.plaid.issued)), found


@pytest.mark.parametrize("shape", ["mask={value}", "{made_up}{value}", "{value}{made_up}"])
def test_anything_of_anyones_in_the_log_is_found_even_run_into_an_identifier(
    world: World, log: Path, shape: str
) -> None:
    as_production(world)
    never = connect_refused_and_abandoned(world)
    issued = world.plaid.issued
    assert any(re.fullmatch(r"demo[0-9a-f]{12}", i) for i in issued)
    assert any(re.fullmatch(r"item-demo-[0-9a-f]{12}", i) for i in issued)

    plaid = logging.getLogger("hearth.plaid.client")
    for value in never:
        for made_up in issued:
            plaid.info("planted %s", shape.format(value=value, made_up=made_up))

    assert leaked(log.read_text(), never, issued) == never


def test_an_identifier_holding_a_secrets_text_is_not_taken_for_it() -> None:
    blake = next(p for p in household.PLAID if p.username == "blake")
    lucky = f"demo00{blake.checking_mask}aa11"
    line = f"production /item/get: ok request_id={lucky}\n"

    assert leaked(line, [blake.checking_mask], issued=[lucky]) == []
    assert leaked(line, [blake.checking_mask], issued=[]) == [blake.checking_mask]
    # The same for a ticker inside an account id on a page; the id itself is still found.
    account_id = "q2_ZQAA-x7Lm0pRt"
    page = household.Seen(f'<a href="/accounts/{account_id}">Badger Basic</a>', [account_id])
    assert "ZQAA" not in page
    assert account_id in page
    assert "ZQAA" in household.Seen(f"{account_id}ZQAA", [account_id])
