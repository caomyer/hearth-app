from pathlib import Path

import pytest

from hearth import db, members
from hearth.cli import main
from hearth.config import DATA_DIR_VARIABLE
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
