"""Both databases come up whole however many processes start on them at once."""

import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from hearth import db
from hearth.plaid import feed

STARTING = 8
"""Processes starting on one new database at the same moment: the server and its first
recompute, and a command run beside them."""

ROUNDS = 5
"""New databases raced on per run, so a race that is lost only some of the time is caught."""


@pytest.mark.parametrize(
    ("initialise", "migrations"),
    [(db.initialise, db.MIGRATIONS), (feed.initialise, feed.MIGRATIONS)],
    ids=["app", "feed"],
)
def test_starting_at_once_migrates_a_new_database_exactly_once(
    tmp_path: Path, initialise: Callable[[Path], None], migrations: tuple[str, ...]
) -> None:
    for round_ in range(ROUNDS):
        path = tmp_path / f"new-{round_}.sqlite3"
        assert _started_at_once(initialise, path) == []
        conn = db.connect(path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == len(migrations)
        finally:
            conn.close()


def _started_at_once(initialise: Callable[[Path], None], path: Path) -> list[BaseException]:
    ready = threading.Barrier(STARTING)
    failures: list[BaseException] = []

    def start() -> None:
        ready.wait()
        try:
            initialise(path)
        except BaseException as error:  # collected for the caller to assert on
            failures.append(error)

    threads = [threading.Thread(target=start) for _ in range(STARTING)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return failures


def test_each_migration_takes_a_database_from_the_one_before_it(tmp_path: Path) -> None:
    """A database migrated to any earlier number, as a hearth of that day left it, is brought
    up to date by the migrations after it alone: the same tables and columns as a new one.
    An edit to a migration already numbered would be missed by every database past it, which
    this finds, as each is compared with a database built from scratch."""
    whole = tmp_path / "whole.sqlite3"
    db.initialise(whole)
    expected = _schema(whole)
    for upto in range(1, len(db.MIGRATIONS)):
        path = tmp_path / f"at-{upto}.sqlite3"
        conn = db.connect(path)
        try:
            db.migrate(conn, db.MIGRATIONS[:upto])
            assert conn.execute("PRAGMA user_version").fetchone()[0] == upto
            db.migrate(conn)
        finally:
            conn.close()
        assert _schema(path) == expected, f"from migration {upto}"


def test_rulings_keep_what_the_bank_said_in_a_database_one_migration_behind(
    tmp_path: Path,
) -> None:
    path = tmp_path / "behind.sqlite3"
    conn = db.connect(path)
    try:
        db.migrate(conn, db.MIGRATIONS[:-1])
        columns = {row[1] for row in conn.execute("PRAGMA table_info(review_ruling_rows)")}
        assert "bank" not in columns
        db.migrate(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(review_ruling_rows)")}
    finally:
        conn.close()
    assert "bank" in columns


def _schema(path: Path) -> dict[str, list[tuple[object, ...]]]:
    """Every table's columns, as SQLite describes them."""
    conn = db.connect(path)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        return {
            table: sorted(tuple(row[1:]) for row in conn.execute(f"PRAGMA table_info({table})"))
            for table in tables
        }
    finally:
        conn.close()
