"""The books: the ledger read without a trace beside it, how far each account reaches, and
one recompute at a time with none asked for ever dropped."""

import threading
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from beancount import loader
from beancount.core.data import Directive
from beancount.parser import parser

from hearth import books, db
from hearth.books import Bookkeeper, Books
from hearth.months import Month
from hearth.valuation.networth import NetWorth
from hearth.valuation.pricestore import PriceStore
from hearth.valuation.quotes import Quote
from tests import household
from tests.conftest import FakeClock

LEDGER = """
option "operating_currency" "USD"
2026-01-01 open Assets:Checking USD
2026-01-01 open Expenses:Food USD
2026-01-05 * "Grocer" "Groceries"
  Expenses:Food  12.50 USD
  Assets:Checking
"""


def _contents(directory: Path) -> dict[str, tuple[int, bytes]]:
    return {path.name: (path.stat().st_mtime_ns, path.read_bytes()) for path in directory.iterdir()}


def test_loading_leaves_a_stale_cache_beside_the_ledger_alone(tmp_path: Path) -> None:
    ledger = tmp_path / "household.beancount"
    ledger.write_text(LEDGER)
    # Beancount's cached loader deletes a cache it cannot trust; this one is not even a pickle.
    (tmp_path / ".household.beancount.picklecache").write_bytes(b"not a pickle")
    before = _contents(tmp_path)

    entries = books.load_entries(ledger)

    assert len(entries) == 3
    assert _contents(tmp_path) == before


def test_a_slow_load_writes_no_cache_beside_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "household.beancount"
    ledger.write_text(LEDGER)
    parse_file = parser.parse_file

    def slow_parse(*args: object, **kwargs: object) -> object:
        # Past the one-second threshold over which beancount's loader caches a result.
        time.sleep(loader.PICKLE_CACHE_THRESHOLD + 0.1)
        return parse_file(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(parser, "parse_file", slow_parse)

    books.load_entries(ledger)

    assert [path.name for path in tmp_path.iterdir()] == ["household.beancount"]


def _entries(text: str) -> tuple[Directive, ...]:
    entries, errors, _ = loader.load_string(LEDGER + text)
    assert not errors
    return tuple(entries)


def _computed(on: datetime) -> books.Books:
    return books.compute(_entries(""), lambda symbol, day: None, now=on, decide=lambda pile: {})


def test_figures_are_as_of_today_however_early_the_ledger_stops() -> None:
    computed = _computed(household.noon(date(2026, 3, 1)))

    assert computed.as_of == date(2026, 3, 1)
    assert computed.month_ends[-1].as_of == date(2026, 2, 28)
    assert computed.account_ends == {"Assets:Checking": date(2026, 1, 5)}


@pytest.fixture
def pacific(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Hearth's machine in a time zone hours behind UTC, as a household's evening is."""
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.usefixtures("pacific")
def test_the_books_are_as_of_the_households_own_day_not_greenwich() -> None:
    # 6:43 in the evening of Oct 18 in the household, already Oct 19 in UTC.
    evening = datetime(2026, 10, 19, 1, 43, tzinfo=UTC)

    computed = _computed(evening)

    assert computed.as_of == date(2026, 10, 18)
    assert computed.month_ends[-1].as_of == date(2026, 9, 30)
    assert not computed.behind(date(2026, 10, 11))


def test_an_account_reaches_the_months_of_its_newest_entries_by_their_own_dates() -> None:
    entries = _entries(
        "2026-01-01 open Liabilities:Card USD\n"
        '2026-01-03 * "Shop"\n  Liabilities:Card  -3.00 USD\n  Expenses:Food\n'
        "2026-02-01 balance Assets:Checking  -12.50 USD\n"
    )

    assert books.account_reaches(entries, date(2026, 3, 1)) == {
        "Assets:Checking": books.Reach(Month(2026, 1), Month(2026, 2)),
        "Liabilities:Card": books.Reach(Month(2026, 1), None),
    }
    assert books.account_reaches(entries, date(2026, 1, 20)) == {
        "Assets:Checking": books.Reach(Month(2026, 1), Month(2026, 1)),
        "Liabilities:Card": books.Reach(Month(2026, 1), None),
    }


def test_an_assertion_makes_the_months_before_it_whole_but_not_its_own() -> None:
    asserted = books.Reach(Month(2026, 9), Month(2026, 10))
    october = Month(2026, 10)

    assert asserted.holds(Month(2026, 9), current=october)
    assert not asserted.holds(october, current=october)
    assert books.Reach(october, None).holds(october, current=october)
    assert not books.Reach(Month(2026, 9), Month(2026, 9)).holds(Month(2026, 9), october)


def test_an_account_never_ends_after_today() -> None:
    assert books.account_ends(_entries(""), date(2026, 1, 2)) == {
        "Assets:Checking": date(2026, 1, 2)
    }


def test_each_account_ends_on_its_own_last_day() -> None:
    entries = _entries(
        "2026-01-01 open Liabilities:Card USD\n"
        '2026-01-03 * "Shop"\n  Liabilities:Card  -3.00 USD\n  Expenses:Food\n'
        "2026-02-01 balance Assets:Checking  -12.50 USD\n"
    )

    assert books.account_ends(entries, date(2026, 3, 1)) == {
        "Assets:Checking": date(2026, 1, 31),
        "Liabilities:Card": date(2026, 1, 3),
    }


def test_data_a_few_days_short_is_not_called_behind() -> None:
    computed = _computed(household.noon(date(2026, 1, 12)))

    assert not computed.behind(date(2026, 1, 5))
    assert computed.behind(date(2026, 1, 4))


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


def test_the_books_hold_the_prices_they_were_valued_at_by_the_rule_the_store_has(
    tmp_path: Path,
) -> None:
    """A statement values any day after the database is closed, so the books hold every price
    the recompute read, and look each up as the store would: the newest day wins, then the
    latest fetched, whichever source it came from, and a correction stands in for its day."""
    database = tmp_path / "hearth.sqlite3"
    db.initialise(database)
    conn = db.connect(database)
    try:
        store = PriceStore(conn)
        early, late = datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 2, tzinfo=UTC)
        for symbol, day, price, source, fetched in [
            ("ZQAA", date(2026, 9, 10), "10.00", "eodhd", early),
            ("ZQAA", date(2026, 9, 10), "11.00", "plaid", late),
            ("ZQAA", date(2026, 9, 12), "12.00", "hand", early),
            ("ZQAA", date(2026, 9, 12), "12.50", "hand", late),
            ("ZQBB", date(2026, 9, 11), "20.00", "eodhd", early),
        ]:
            store.record(Quote(symbol, day, Decimal(price), "USD", source, symbol, fetched))
        stored = books.stored_prices(store)
        remembered = books.remembered_prices(store, ["ZQAA", "ZQBB", "ZQCC"])
        for symbol in ("ZQAA", "ZQBB", "ZQCC"):
            for n in range(8, 15):
                asked = date(2026, 9, n)
                assert remembered(symbol, asked) == stored(symbol, asked), (symbol, asked)
    finally:
        conn.close()
    assert remembered("ZQAA", date(2026, 9, 12)).price == Decimal("12.50")  # type: ignore[union-attr]
    assert remembered("ZQAA", date(2026, 9, 11)).price == Decimal("11.00")  # type: ignore[union-attr]
