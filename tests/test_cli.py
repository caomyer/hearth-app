import io
import json
from pathlib import Path
from typing import Any

import pytest

from hearth import accounts, audit, db, members
from hearth.audit import EventKind
from hearth.cli import main
from hearth.config import DATA_DIR_VARIABLE, LEDGER_VARIABLE
from tests.conftest import World, password_of


def test_the_first_admin_is_added_from_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(tmp_path))

    main(["add-member", "Avery", "--name", "Avery", "--admin"])

    assert "One-time password for avery: " in capsys.readouterr().out
    conn = db.connect(tmp_path / "hearth.sqlite3")
    try:
        member = members.find(conn, "avery")
    finally:
        conn.close()
    assert member is not None
    assert member.is_admin
    assert member.must_change_password
    assert (tmp_path / "signing.key").stat().st_mode & 0o777 == 0o600


def test_a_rejected_member_is_reported_plainly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(tmp_path))

    with pytest.raises(SystemExit, match="hearth: Usernames are"):
        main(["add-member", "x", "--name", "X"])


@pytest.mark.parametrize(
    "command",
    [
        ["plaid", "sync"],
        ["plaid", "remove", "item-synthetic-0001"],
        ["plaid", "adopt", "--member", "avery", "--token-file", "absent"],
        ["plaid", "adopt", "--member", "avery", "--token-file", "-", "--confirm", "item-0001"],
    ],
)
def test_a_live_plaid_command_refuses_without_approval_and_sends_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: list[str],
) -> None:
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(tmp_path))

    def network(*args: object, **kwargs: object) -> object:
        raise AssertionError("nothing may be sent without --approved")

    monkeypatch.setattr("urllib.request.urlopen", network)

    with pytest.raises(SystemExit) as raised:
        main(command)

    assert raised.value.code == 2
    assert "pass --approved" in capsys.readouterr().out


def test_every_connection_is_listed_with_its_last_successful_sync(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(world.settings.data_dir))

    with pytest.raises(SystemExit) as raised:
        main(["plaid", "items"])

    out = capsys.readouterr().out
    assert raised.value.code == 0
    for username, item_id in world.item_ids.items():
        line = next(line for line in out.splitlines() if line.startswith(item_id))
        assert f"member={username}" in line
        assert "accounts=2" in line
        assert "last-sync=2026-10-15" in line
    assert out.count("5 of 10 connections remaining (1 in hearth, 4 elsewhere)") == 3


LEDGER = """
2026-01-01 open Assets:Zq:Joint USD
2026-01-01 open Liabilities:Zq:Card USD
2026-01-01 open Expenses:Zq:Food USD
"""


@pytest.fixture
def household(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ledger = tmp_path / "ledger" / "household.beancount"
    ledger.parent.mkdir()
    ledger.write_text(LEDGER)
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(tmp_path / "data"))
    monkeypatch.setenv(LEDGER_VARIABLE, str(ledger))
    main(["add-member", "avery", "--name", "Avery", "--admin"])
    main(["add-member", "blake", "--name", "Blake"])
    return tmp_path


def test_a_joint_account_is_claimed_for_each_of_its_owners(
    household: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    main(
        [
            "add-account",
            "Assets:Zq:Joint",
            "--name",
            "Joint",
            "--owner",
            "avery",
            "--owner",
            "Blake",
        ]
    )

    assert capsys.readouterr().out == "Joint is Avery and Blake's.\n"
    conn = db.connect(household / "data" / "hearth.sqlite3")
    try:
        (claimed,) = accounts.all_accounts(conn)
        claims = [e for e in audit.all_events(conn) if e.kind is EventKind.ACCOUNT_CLAIMED]
        avery, blake = members.find(conn, "avery"), members.find(conn, "blake")
    finally:
        conn.close()
    assert avery is not None
    assert blake is not None
    assert (claimed.ledger_account, claimed.name) == ("Assets:Zq:Joint", "Joint")
    assert claimed.owners == {avery.id, blake.id}
    assert sorted(e.subject_id for e in claims if e.subject_id) == sorted([avery.id, blake.id])


def test_an_owner_named_twice_owns_the_account_once(
    household: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    main(
        [
            "add-account",
            "Liabilities:Zq:Card",
            "--name",
            "Card",
            "--owner",
            "avery",
            "--owner",
            "Avery",
        ]
    )

    assert capsys.readouterr().out == "Card is Avery's.\n"
    conn = db.connect(household / "data" / "hearth.sqlite3")
    try:
        (claimed,) = accounts.all_accounts(conn)
        claims = [e for e in audit.all_events(conn) if e.kind is EventKind.ACCOUNT_CLAIMED]
        avery = members.find(conn, "avery")
    finally:
        conn.close()
    assert avery is not None
    assert claimed.owners == {avery.id}
    assert [e.subject_id for e in claims] == [avery.id]


def test_an_account_is_claimed_once_and_never_reassigned(household: Path) -> None:
    main(["add-account", "Liabilities:Zq:Card", "--name", "Card", "--owner", "avery"])

    with pytest.raises(SystemExit, match="hearth: Liabilities:Zq:Card is already claimed"):
        main(["add-account", "Liabilities:Zq:Card", "--name", "Card", "--owner", "blake"])


@pytest.mark.parametrize(
    ("arguments", "refusal"),
    [
        (["Assets:Zq:Typo", "--owner", "avery"], "the books open no Assets or Liabilities"),
        (["Expenses:Zq:Food", "--owner", "avery"], "the books open no Assets or Liabilities"),
        (["Assets:Zq:Joint", "--owner", "nobody"], "there is no member nobody"),
    ],
)
def test_a_claim_hearth_cannot_honour_is_refused_plainly(
    household: Path, arguments: list[str], refusal: str
) -> None:
    with pytest.raises(SystemExit, match=f"hearth: {refusal}"):
        main(["add-account", *arguments[:1], "--name", "X", *arguments[1:]])


def test_the_ledgers_accounts_are_listed_with_their_owners(
    household: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(
        [
            "add-account",
            "Assets:Zq:Joint",
            "--name",
            "Joint",
            "--owner",
            "avery",
            "--owner",
            "blake",
        ]
    )
    capsys.readouterr()

    main(["accounts"])

    assert capsys.readouterr().out.splitlines() == [
        "Assets:Zq:Joint  Joint  avery, blake",
        "Liabilities:Zq:Card  -  unclaimed",
    ]


def test_claiming_reads_the_ledger_and_writes_nothing_beside_it(household: Path) -> None:
    folder = household / "ledger"
    before = sorted(path.name for path in folder.iterdir())

    main(["accounts"])
    main(["add-account", "Assets:Zq:Joint", "--name", "Joint", "--owner", "avery"])

    assert sorted(path.name for path in folder.iterdir()) == before


def test_without_a_ledger_there_is_nothing_to_claim_and_no_file_to_ask_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(tmp_path))
    monkeypatch.delenv(LEDGER_VARIABLE, raising=False)
    main(["add-member", "avery", "--name", "Avery", "--admin"])
    capsys.readouterr()

    main(["accounts"])
    assert capsys.readouterr().out == ""
    with pytest.raises(SystemExit, match="the books open no Assets or Liabilities account"):
        main(["add-account", "Assets:Zq:Joint", "--name", "Joint", "--owner", "avery"])


def test_serving_over_https_registers_the_way_back_plaid_production_accepts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(tmp_path))
    served: list[tuple[Any, ...]] = []
    monkeypatch.setattr("hearth.cli._run", lambda *arguments: served.append(arguments))
    cert, key = tmp_path / "localhost.pem", tmp_path / "localhost-key.pem"

    everywhere = "0.0.0.0"  # noqa: S104 - never bound: serving is replaced
    main(
        [
            "serve",
            "--host",
            everywhere,
            "--port",
            "8443",
            "--tls-cert",
            str(cert),
            "--tls-key",
            str(key),
        ]
    )

    ((settings, _, host, port, setup, tls),) = served
    assert (host, port, tls) == (everywhere, 8443, (cert, key))
    # Always localhost, the address to register, however hearth is reached.
    assert setup.redirect_uri == "https://localhost:8443/connect/oauth"
    assert settings.secure_cookies
    assert "https://localhost:8443/connect/oauth, which must be one of" in capsys.readouterr().out


def test_serving_https_on_its_default_port_registers_the_address_a_browser_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(tmp_path))
    served: list[tuple[Any, ...]] = []
    monkeypatch.setattr("hearth.cli._run", lambda *arguments: served.append(arguments))
    cert, key = tmp_path / "localhost.pem", tmp_path / "localhost-key.pem"

    main(["serve", "--port", "443", "--tls-cert", str(cert), "--tls-key", str(key)])

    ((_, _, _, port, setup, _),) = served
    assert port == 443
    assert setup.redirect_uri == "https://localhost/connect/oauth"


def test_serving_plain_http_offers_plaid_no_way_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(tmp_path))
    served: list[tuple[Any, ...]] = []
    monkeypatch.setattr("hearth.cli._run", lambda *arguments: served.append(arguments))

    main(["serve", "--host", "0.0.0.0", "--port", "8000"])  # noqa: S104 - never bound here

    ((settings, _, _, _, setup, tls),) = served
    assert setup.redirect_uri is None
    assert tls is None
    assert not settings.secure_cookies
    assert "offers no way back to a bank that takes over the tab" in capsys.readouterr().out


def test_a_certificate_without_its_key_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(tmp_path))
    monkeypatch.setattr("hearth.cli._run", lambda *arguments: pytest.fail("served"))

    with pytest.raises(SystemExit) as refused:
        main(["serve", "--tls-cert", str(tmp_path / "localhost.pem")])

    assert refused.value.code == 2


def test_the_bridge_answers_the_app_on_standard_output_and_nothing_else(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(world.settings.data_dir))
    monkeypatch.setenv(LEDGER_VARIABLE, str(world.ledger))
    sign_in = {"id": 2, "sign_in": {"username": "Avery", "password": password_of("avery")}}
    requests = [{"id": 1, "ask": "overview"}, sign_in, {"id": 3, "ask": "overview"}]
    monkeypatch.setattr("sys.stdin", io.StringIO("".join(json.dumps(r) + "\n" for r in requests)))

    main(["bridge"])

    before, signed, after = (json.loads(line) for line in capsys.readouterr().out.splitlines())
    assert before == {"id": 1, "answer": {"state": "signed_out"}}
    assert signed == {"id": 2, "outcome": "signed_in"}
    assert after["id"] == 3
    # The first recompute runs beside the first ask, so either answer is a true one.
    assert after["answer"]["state"] in {"loading", "ready"}


def test_the_bridge_ends_the_session_it_signed_in_with_when_the_app_closes_its_end(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(world.settings.data_dir))
    monkeypatch.setenv(LEDGER_VARIABLE, str(world.ledger))
    sign_in = {"id": 1, "sign_in": {"username": "blake", "password": password_of("blake")}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(sign_in) + "\n"))

    main(["bridge"])

    conn = world.connect()
    try:
        (live,) = conn.execute("SELECT count(*) FROM sessions WHERE revoked_at IS NULL").fetchone()
    finally:
        conn.close()
    assert live == 0


def test_the_bridge_makes_no_household_where_there_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    nowhere = tmp_path / "nothing-here"
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(nowhere))
    monkeypatch.setattr("sys.stdin", io.StringIO('{"id": 1, "ask": "overview"}\n'))

    with pytest.raises(SystemExit, match="hearth: there is no household in"):
        main(["bridge"])

    assert capsys.readouterr().out == ""
    assert not nowhere.exists()
