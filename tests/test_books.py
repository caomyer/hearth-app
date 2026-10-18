"""The bookkeeper: one recompute at a time, and none asked for is ever dropped."""

import threading
from datetime import UTC, datetime

from hearth.books import Bookkeeper, Books
from hearth.valuation.networth import NetWorth
from tests.conftest import FakeClock

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)


def books_at(now: datetime) -> Books:
    return Books(now, now.date(), "USD", (), NetWorth(now.date(), "USD", ()), (), (), ())


def test_a_recompute_asked_for_mid_recompute_runs_once_more_after_it() -> None:
    """A ruling made while the books recompute must be in the books that follow."""
    started, release = threading.Event(), threading.Event()
    runs: list[datetime] = []

    def establish(now: datetime) -> Books:
        runs.append(now)
        if len(runs) == 1:
            started.set()
            release.wait(5)
        return books_at(now)

    keeper = Bookkeeper(establish, FakeClock(NOW))
    first = threading.Thread(target=keeper.recompute)
    first.start()
    assert started.wait(5)

    keeper.recompute()  # returns at once: the running one will go round again
    keeper.recompute()
    assert len(runs) == 1
    release.set()
    first.join(5)

    assert len(runs) == 2


def test_waiting_returns_only_once_books_after_the_call_are_in_place() -> None:
    started, release = threading.Event(), threading.Event()
    runs: list[int] = []

    def establish(now: datetime) -> Books:
        runs.append(1)
        if len(runs) == 1:
            started.set()
            release.wait(5)
        return books_at(now)

    keeper = Bookkeeper(establish, FakeClock(NOW))
    threading.Thread(target=keeper.recompute).start()
    assert started.wait(5)
    threading.Timer(0.05, release.set).start()

    keeper.recompute(wait=True)

    assert len(runs) == 2
    assert keeper.status.books is not None
    assert not keeper.status.recomputing
