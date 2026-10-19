from pathlib import Path

import pytest

from hearth import accounts, audit, db, members
from hearth.audit import EventKind
from hearth.cli import main
from hearth.config import DATA_DIR_VARIABLE, LEDGER_VARIABLE
from tests.conftest import World


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
    [["plaid", "sync"], ["plaid", "remove", "item-synthetic-0001"]],
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
