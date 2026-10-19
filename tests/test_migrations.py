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
