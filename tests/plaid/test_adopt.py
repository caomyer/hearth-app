"""Adopting an Item made outside hearth: ``hearth plaid adopt``, over the stand-in.

A household may already hold working Items an older tool linked through the same Plaid
account. Adopting one takes no slot and no Link flow, but the history it reaches back is the
one it was made with, for good. So the first run only says what it offers, and writes nothing;
the second, naming the Item, holds it as the member's own connection, as if they had linked it.
"""

import io
import logging
import sqlite3
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hearth import db, runlog
from hearth.plaid import cli as plaid_cli
from hearth.plaid import connector as connector_module
from hearth.plaid.credentials import CredentialStore
from hearth.plaid.fake import FakeAccount, FakeInstitution, FakeTransaction
from hearth.plaid.feed import Feed
from hearth.plaid.registry import Registry
from hearth.plaid.sync import SyncStoppedError
from hearth.vault.errors import VaultError
from hearth.vault.secret import Secret
from hearth.vault.tokens import TokenVault
from tests import household
from tests.conftest import World
from tests.vault.test_no_leak import render

ELSEWHERE = FakeInstitution(
    "ins_quarry",
    "Quarry Street Brokerage",
    (
        FakeAccount("brk", "Brokerage", "3141", "investment", "brokerage", Decimal("5000.00")),
        FakeAccount("chk", "Cash", "2718", "depository", "checking", Decimal("812.40")),
    ),
)
MADE = household.NOW.date() - timedelta(days=200)
"""When the older tool linked it, asking for 120 days: what it reaches back stops there."""


@pytest.fixture
def outside(world: World) -> tuple[str, str]:
    """An Item avery's Plaid account made outside hearth: its id and its access token."""
    world.plaid.institutions[ELSEWHERE.id] = ELSEWHERE
    return world.plaid.made_elsewhere("client-avery", ELSEWHERE.id, days=120, linked=MADE)


def token_file(tmp_path: Path, token: str) -> str:
    path = tmp_path / "token"
    path.write_text(f"{token}\n")
    return str(path)


def adopt(world: World, username: str, path: str, confirm: str | None = None) -> int:
    return plaid_cli.adopt(
        world.settings,
        world.setup,
        world.clock,
        username=username,
        token_file=path,
        confirm=confirm,
    )


def state(world: World) -> tuple[object, ...]:
    """Everything adopting could write: connections, accounts, tokens, allowances, the feed."""
    conn = world.connect()
    try:
        tables = tuple(
            tuple(tuple(row) for row in conn.execute(f"SELECT * FROM {table}"))  # noqa: S608
            for table in ("plaid_items", "plaid_accounts", "vault_token", "plaid_credentials")
        )
    finally:
        conn.close()
    feed = db.connect(world.settings.feed_database)
    try:
        held = feed.execute("SELECT count(*) FROM feed_transaction").fetchone()[0]
    finally:
        feed.close()
    return (*tables, held)


def test_the_first_run_says_what_the_item_offers_and_writes_nothing(
    world: World, outside: tuple[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    item_id, token = outside
    before = state(world)

    assert adopt(world, "avery", token_file(tmp_path, token)) == 0

    out = capsys.readouterr().out
    first = MADE - timedelta(days=119)
    assert f"Quarry Street Brokerage: Item {item_id}, made at Plaid {MADE:%Y-%m-%d}" in out
    assert "Brokerage (brokerage ending 3141): " in out
    assert "Cash (checking ending 2718): " in out
    days = (household.NOW.date() - first).days + 1
    assert f"Earliest transaction: {first:%Y-%m-%d}, {days} days back" in out
    assert "asks for 730" in out
    assert "read from Plaid, not what any tool kept from it" in out
    assert "starts on or before the earliest transaction" in out
    assert "Its holdings can be read without linking again" in out
    assert f"--confirm {item_id}" in out
    assert token not in out
    assert state(world) == before
    assert "/transactions/sync" in world.plaid.calls


def test_confirming_holds_it_as_the_members_own_connection_without_a_slot(
    world: World, outside: tuple[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    item_id, token = outside

    assert adopt(world, "avery", token_file(tmp_path, token), confirm=item_id) == 0

    out = capsys.readouterr().out
    assert f"{item_id} (Quarry Street Brokerage) is avery's connection now" in out
    assert f"hearth plaid remove {item_id} --approved" in out
    assert token not in out
    conn = world.connect()
    try:
        item = Registry(conn).item(item_id)
        assert item is not None
        assert item.member == world.member_ids["avery"]
        assert item.credentials == f"member:{world.member_ids['avery']}"
        assert item.institution_id == ELSEWHERE.id
        assert item.days_requested == 730
        masks = {a.mask for a in Registry(conn).accounts_of(item_id)}
        assert masks == {"3141", "2718"}
        # It is held exactly as a linked token is: sealed for avery and this Item.
        assert TokenVault(conn, world.setup.keys()).get(item.member, item_id) == Secret(token)
        # It was counted outside hearth, and is counted in it now: nothing more is used.
        capacity = CredentialStore(conn, None).capacity(item.credentials)
        assert capacity is not None
        assert (capacity.in_hearth, capacity.used_elsewhere, capacity.remaining) == (2, 3, 5)
    finally:
        conn.close()
    with Feed.open(world.settings.feed_database) as feed:
        assert feed.held(f"{item_id}-chk").earliest == MADE - timedelta(days=119)
    # Downstream it is one of avery's connections like any other, and nobody else's.
    with world.connector("avery") as connector:
        assert item_id in {i.item_id for i in connector.overview().items}
        assert f"{item_id}-chk" in {r.account.account_id for r in connector.mapping().rows}
    for other in ("blake", "casey"):
        with world.connector(other) as connector:
            assert item_id not in {i.item_id for i in connector.overview().items}


def test_the_token_may_come_on_standard_input(
    world: World,
    outside: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    item_id, token = outside
    monkeypatch.setattr("sys.stdin", io.StringIO(f"  {token}\n"))

    assert adopt(world, "avery", "-") == 0

    assert f"--confirm {item_id}" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("ours", "theirs", "said"),
    [
        (300, 0, "hearth already covers it better, so keep that one"),
        (0, 0, "hearth already covers it as well, so keep that one"),
        (0, 300, "To keep the longer history, remove hearth's"),
    ],
)
def test_an_item_reaching_an_account_hearth_reaches_is_refused_saying_which_reaches_further(
    world: World,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    ours: int,
    theirs: int,
    said: str,
) -> None:
    """A bank hearth already holds: adopting the old Item for it would bring every transaction
    twice, and may narrow what hearth knows. The report says which reaches further back."""
    held = world.item_ids["avery"]
    item_id, token = world.plaid.made_elsewhere(
        "client-avery", "ins_avery", days=730, linked=household.NOW.date()
    )
    old = FakeTransaction("Invented Grocer", Decimal("9.00"), household.NOW.date())
    for item, back in ((held, ours), (item_id, theirs)):
        if back:
            world.plaid.change(item, "added", replace(old, day=old.day - timedelta(back)), "chk")
    with world.connector("avery") as connector:
        connector.resync(held)
    before = state(world)

    assert adopt(world, "avery", token_file(tmp_path, token), confirm=item_id) == plaid_cli.REFUSED

    out = capsys.readouterr().out
    assert f"hearth already reaches it through {held}" in out
    assert "hearth will not adopt it" in out
    assert said in out
    assert state(world) == before


def test_reaching_further_is_weighed_account_by_account(
    world: World, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """hearth's own reaches the checking account furthest of all, yet the outside Item reaches
    the card further back: keeping either loses some history, and the report says so."""
    held = world.item_ids["avery"]
    item_id, token = world.plaid.made_elsewhere(
        "client-avery", "ins_avery", days=730, linked=household.NOW.date()
    )
    old = FakeTransaction("Invented Grocer", Decimal("9.00"), household.NOW.date())
    for item, account, back in (
        (held, "chk", 300),
        (held, "card", 30),
        (item_id, "chk", 100),
        (item_id, "card", 200),
    ):
        world.plaid.change(item, "added", replace(old, day=old.day - timedelta(back)), account)
    with world.connector("avery") as connector:
        connector.resync(held)
    before = state(world)

    assert adopt(world, "avery", token_file(tmp_path, token), confirm=item_id) == plaid_cli.REFUSED

    out = capsys.readouterr().out
    day = household.NOW.date()
    assert (
        f"This Item reaches further back on Axolotl Rewards ending 6623 (this Item from"
        f" {day - timedelta(200):%Y-%m-%d}, hearth's own from {day - timedelta(30):%Y-%m-%d})"
    ) in out
    assert (
        f"hearth's own reaches further back on Aardvark Everyday ending 4417 (this Item from"
        f" {day - timedelta(100):%Y-%m-%d}, hearth's own from {day - timedelta(300):%Y-%m-%d})"
    ) in out
    assert "Which to keep is the member's choice" in out
    assert "starts on or before it" in out
    assert "keep that one" not in out
    assert "hearth plaid remove" not in out
    assert state(world) == before


@pytest.mark.parametrize(
    ("outside_has", "ours", "theirs", "said"),
    [
        (("chk", "card", "sav"), 300, 0, "Only this Item reaches Savings ending 5538"),
        (("chk",), 0, 300, "Only hearth's own reaches Axolotl Rewards ending 6623"),
    ],
)
def test_an_account_only_one_side_reaches_leaves_the_choice_to_the_member(
    world: World,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    outside_has: tuple[str, ...],
    ours: int,
    theirs: int,
    said: str,
) -> None:
    """Keeping hearth's own would leave out an account only the outside Item reaches, and
    removing it would end one only it reaches: neither is advised, whichever reaches further."""
    held = world.item_ids["avery"]
    bank = world.plaid.institutions["ins_avery"]
    savings = FakeAccount("sav", "Savings", "5538", "depository", "savings", Decimal("90.00"))
    world.plaid.institutions["ins_avery"] = replace(
        bank,
        accounts=tuple(a for a in (*bank.accounts, savings) if a.key in outside_has),
    )
    item_id, token = world.plaid.made_elsewhere(
        "client-avery", "ins_avery", days=730, linked=household.NOW.date()
    )
    world.plaid.institutions["ins_avery"] = bank
    old = FakeTransaction("Invented Grocer", Decimal("9.00"), household.NOW.date())
    for item, back in ((held, ours), (item_id, theirs)):
        if back:
            world.plaid.change(item, "added", replace(old, day=old.day - timedelta(back)), "chk")
    with world.connector("avery") as connector:
        connector.resync(held)
    before = state(world)

    assert adopt(world, "avery", token_file(tmp_path, token), confirm=item_id) == plaid_cli.REFUSED

    out = capsys.readouterr().out
    assert said in out
    assert "Which to keep is the member's choice" in out
    assert "keep that one" not in out
    assert "hearth plaid remove" not in out
    assert state(world) == before


def test_another_members_account_is_refused_without_saying_whose_or_what_it_holds(
    world: World, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Blake holds a token reaching an account avery's connection reaches: refused, and told
    only that hearth reaches it, never through which connection nor since when."""
    _, token = world.plaid.made_elsewhere(
        "client-blake", "ins_avery", days=120, linked=household.NOW.date()
    )
    before = state(world)

    assert adopt(world, "blake", token_file(tmp_path, token)) == plaid_cli.REFUSED

    out = capsys.readouterr().out
    assert "hearth already reaches it through another connection" in out
    assert world.item_ids["avery"] not in out
    assert "since" not in out
    assert "reaches back to" not in out
    assert state(world) == before


def test_an_item_hearth_holds_is_refused_alike_whoever_holds_it(
    world: World, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = world.connect()
    try:
        token = TokenVault(conn, world.setup.keys()).get(
            world.member_ids["avery"], world.item_ids["avery"]
        )
    finally:
        conn.close()
    before, calls = state(world), len(world.plaid.calls)

    assert adopt(world, "avery", token_file(tmp_path, token.reveal())) == plaid_cli.REFUSED

    said = capsys.readouterr().out
    assert said == "hearth: hearth already holds that Item. Nothing was written.\n"
    assert state(world) == before
    # Refused on what /item/get says, before its accounts or history are asked for.
    assert world.plaid.calls[calls:] == ["/item/get"]


def test_a_token_from_another_environment_is_refused_before_anything_is_sent(
    world: World, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = len(world.plaid.calls)

    path = token_file(tmp_path, "access-production-00000000-0000-0000-0000-000000000000")
    assert adopt(world, "avery", path) == plaid_cli.REFUSED

    out = capsys.readouterr().out
    assert "made in Plaid's production environment" in out
    assert "is in demo" in out
    assert len(world.plaid.calls) == calls


def test_a_token_another_plaid_account_made_is_refused_with_why(
    world: World, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    world.plaid.institutions[ELSEWHERE.id] = ELSEWHERE
    _, token = world.plaid.made_elsewhere("client-blake", ELSEWHERE.id, days=120, linked=MADE)
    before = state(world)

    assert adopt(world, "avery", token_file(tmp_path, token)) == plaid_cli.REFUSED

    out = capsys.readouterr().out
    assert "Plaid does not know that token under the Plaid account avery connects through" in out
    assert "client id and environment that made it" in out
    assert state(world) == before


def test_confirming_an_item_the_token_is_not_for_writes_nothing(
    world: World, outside: tuple[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, token = outside
    before = state(world)

    path = token_file(tmp_path, token)
    assert adopt(world, "avery", path, confirm="item-demo-other") == plaid_cli.REFUSED

    assert "not for the Item named by --confirm" in capsys.readouterr().out
    assert state(world) == before


@pytest.mark.parametrize(
    ("contents", "said"),
    [("", "holds no token"), ("not a token", "not a Plaid access token")],
)
def test_a_token_file_without_a_token_is_refused(
    world: World, tmp_path: Path, capsys: pytest.CaptureFixture[str], contents: str, said: str
) -> None:
    assert adopt(world, "avery", token_file(tmp_path, contents)) == plaid_cli.REFUSED

    assert said in capsys.readouterr().out


def test_a_missing_token_file_is_refused(
    world: World, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert adopt(world, "avery", str(tmp_path / "absent")) == plaid_cli.REFUSED

    assert "the token file cannot be read" in capsys.readouterr().out


def test_an_adopted_token_is_sealed_and_never_shown_logged_or_kept_in_the_clear(
    world: World,
    outside: tuple[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    item_id, token = outside

    with caplog.at_level(logging.DEBUG):
        assert adopt(world, "avery", token_file(tmp_path, token)) == 0
        assert adopt(world, "avery", token_file(tmp_path, token), confirm=item_id) == 0
        assert adopt(world, "blake", token_file(tmp_path, token)) == plaid_cli.REFUSED

    files = b"".join(
        path.read_bytes() for path in world.settings.data_dir.iterdir() if ".sqlite3" in path.name
    )
    assert token.encode() not in files
    assert token not in capsys.readouterr().out + caplog.text


@pytest.fixture
def sealed(outside: tuple[str, str]) -> Secret:
    """The outside Item's token, held only as a `Secret`, so no frame holds it plainly."""
    return Secret(outside[1])


def test_a_refusal_carries_nothing_of_the_token(world: World, sealed: Secret) -> None:
    from hearth.plaid.connector import NotAdopted

    with world.connector("blake") as connector, pytest.raises(NotAdopted) as raised:
        connector.offer(sealed)

    assert raised.value.reason.value == "not-known"
    shown = render(raised.value)
    for item in world.plaid._items.values():
        assert item.access_token not in shown


def test_a_token_file_that_is_not_text_is_refused_without_showing_it(
    world: World, outside: tuple[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, token = outside
    path = tmp_path / "token"
    path.write_bytes(b"\xff" + token.encode())

    assert adopt(world, "avery", str(path)) == plaid_cli.REFUSED

    out = capsys.readouterr().out
    assert "holds no token" in out
    assert token not in out


def test_a_vault_that_cannot_seal_the_token_is_refused_and_leaves_nothing(
    world: World,
    outside: tuple[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    item_id, token = outside

    def locked(*_: object) -> None:
        raise VaultError("the macOS Keychain is locked")

    monkeypatch.setattr(TokenVault, "put", locked)
    before = state(world)

    assert adopt(world, "avery", token_file(tmp_path, token), confirm=item_id) == plaid_cli.REFUSED

    out = capsys.readouterr().out
    assert out.endswith("hearth: the macOS Keychain is locked\n")
    assert token not in out
    assert state(world) == before


def test_plaid_sending_too_many_pages_is_refused_plainly(
    world: World,
    outside: tuple[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, token = outside

    def endless(*_: object) -> None:
        raise SyncStoppedError("Plaid sent more than 50 pages; stopping here")

    monkeypatch.setattr(connector_module, "look", endless)
    before = state(world)

    assert adopt(world, "avery", token_file(tmp_path, token)) == plaid_cli.REFUSED

    out = capsys.readouterr().out
    assert out == "hearth: Plaid sent more than 50 pages; stopping here\n"
    assert state(world) == before


def test_a_failure_after_the_token_is_sealed_leaves_nothing_behind(
    world: World, outside: tuple[str, str], sealed: Secret, monkeypatch: pytest.MonkeyPatch
) -> None:
    item_id, _ = outside

    def failing(*_: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(CredentialStore, "brought_in", failing)
    before = state(world)

    with world.connector("avery") as connector, pytest.raises(sqlite3.OperationalError):
        connector.adopt(sealed, item_id)

    assert state(world) == before


def test_a_first_sync_the_vault_stops_still_leaves_the_item_adopted(
    world: World,
    outside: tuple[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    item_id, token = outside

    def locked(*_: object) -> None:
        raise VaultError("the macOS Keychain is locked")

    monkeypatch.setattr(TokenVault, "get", locked)

    assert adopt(world, "avery", token_file(tmp_path, token), confirm=item_id) == 0

    out = capsys.readouterr().out
    assert f"{item_id} (Quarry Street Brokerage) is avery's connection now" in out
    assert "Its first sync did not run: the macOS Keychain is locked." in out
    assert token not in out
    monkeypatch.undo()
    conn = world.connect()
    try:
        assert Registry(conn).item(item_id) is not None
        assert TokenVault(conn, world.setup.keys()).get(world.member_ids["avery"], item_id) == (
            Secret(token)
        )
    finally:
        conn.close()


def test_adopting_is_in_the_runtime_log_beside_links_connections(
    world: World, outside: tuple[str, str], tmp_path: Path
) -> None:
    item_id, token = outside
    log = runlog.configure(world.settings.data_dir)
    try:
        assert adopt(world, "avery", token_file(tmp_path, token)) == 0
        assert adopt(world, "avery", token_file(tmp_path, token), confirm=item_id) == 0
    finally:
        runlog.stop(world.settings.data_dir)
    written = log.read_text()

    member = world.member_ids["avery"]
    assert f"member {member} adopted Quarry Street Brokerage as item {item_id}" in written
    assert "demo /item/get: ok request_id=demo" in written
    assert token not in written
