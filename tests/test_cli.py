from pathlib import Path

import pytest

from hearth import db, members
from hearth.cli import main
from hearth.config import DATA_DIR_VARIABLE


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
