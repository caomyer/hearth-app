"""The demo household, built by the same path `hearth demo` takes."""

import os
import time as clock_time
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from beancount.core.data import Transaction

from hearth import accounts, books, db, demo, members
from hearth.books import Books, Status
from hearth.config import Settings
from hearth.disclosure import Disclosure, may_learn, rulings_in
from hearth.months import Month
from hearth.plaid import feed
from hearth.plaid.fake import DEMO_INSTITUTIONS
from hearth.plaid.feed import Feed
from hearth.plaid.registry import Registry
from tests.books_file import holds_the_books


def test_the_demos_connected_banks_carry_the_history_and_balance_the_ledger_holds(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    ledger = tmp_path / "household.beancount"
    settings = Settings.in_directory(tmp_path, ledger=ledger)
    db.initialise(settings.database)
    conn = db.connect(settings.database)
    try:
        demo.seed(conn, ledger, now=now)
    finally:
        conn.close()
    feed.initialise(settings.feed_database)
    demo.connect(settings, demo.plaid(lambda: now.astimezone().date(), ledger), now=now)

    held_in_ledger: dict[str, Decimal] = {}
    for entry in books.load_entries(ledger):
        if isinstance(entry, Transaction):
            for posting in entry.postings:
                if posting.units is not None and posting.units.number is not None:
                    held_in_ledger.setdefault(posting.account, Decimal(0))
                    held_in_ledger[posting.account] += posting.units.number
    conn = db.connect(settings.database)
    try:
        ledger_of = {a.id: a.ledger_account for a in accounts.all_accounts(conn)}
        registry = Registry(conn)
        connected = [members.find(conn, username) for username in ("avery", "blake")]
        linked = [
            account
            for member in connected
            if member is not None
            for item in registry.items_of(member.id)
            for account in registry.accounts_of(item.item_id)
        ]
    finally:
        conn.close()

    fed = {}
    with Feed.open(settings.feed_database) as store:
        for account in linked:
            assert account.hearth_account is not None
            fed[ledger_of[account.hearth_account]] = (
                store.held(account.account_id),
                store.balance(account.account_id),
            )
    assert set(fed) == {"Assets:Avery:Checking", "Liabilities:Avery:Card", "Assets:Blake:Checking"}
    for ledger_account, (held, balance) in fed.items():
        assert held.earliest is not None
        assert held.earliest <= now.astimezone().date() - timedelta(days=demo.HISTORY_DAYS - 7)
        assert balance is not None
        owed = ledger_account.startswith("Liabilities:")
        expected = -held_in_ledger[ledger_account] if owed else held_in_ledger[ledger_account]
        assert balance.current == expected
        assert balance.current != 0


def test_the_demos_books_file_reads_back_as_exactly_its_books(tmp_path: Path) -> None:
    """The demo's ledger, held where it is, and its connected banks over it: the file holds
    both, and every account holds in it what it holds in the books."""
    now = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    ledger = tmp_path / "household.beancount"
    settings = Settings.in_directory(tmp_path, ledger=ledger)
    db.initialise(settings.database)
    conn = db.connect(settings.database)
    try:
        demo.seed(conn, ledger, now=now)
    finally:
        conn.close()
    feed.initialise(settings.feed_database)
    demo.connect(settings, demo.plaid(lambda: now.astimezone().date(), ledger), now=now)

    found = books.from_household(
        settings, rulings=rulings_in, learns=may_learn, sources=(demo.PRICE_SOURCE,)
    )(now)

    entries = holds_the_books(settings.books_file, found)
    assert len(entries) == len(found.entries) > 900
    assert "include " not in settings.books_file.read_text()


@pytest.mark.parametrize("hour", [0, 5, 6, 15, 23])
def test_the_demos_prices_were_fetched_this_morning_and_never_after_now(
    tmp_path: Path, hour: int
) -> None:
    """Whatever the zone hearth runs in: the net worth page says when its prices were
    fetched, which must be today where hearth runs, and never a moment still to come."""
    now = datetime.combine(date(2026, 10, 18), time(hour, 30)).astimezone(UTC)
    ledger = tmp_path / "household.beancount"
    settings = Settings.in_directory(tmp_path, ledger=ledger)
    db.initialise(settings.database)
    conn = db.connect(settings.database)
    try:
        demo.seed(conn, ledger, now=now)
        fetched = {
            datetime.fromisoformat(row[0]) for row in conn.execute("SELECT fetched_at FROM price")
        }
    finally:
        conn.close()

    assert fetched
    assert all(at <= now for at in fetched)
    assert {at.astimezone().date() for at in fetched} == {date(2026, 10, 18)}


def test_the_first_review_demo_is_a_backfill_at_full_size(tmp_path: Path) -> None:
    ledger = tmp_path / "household.beancount"
    settings = Settings.in_directory(tmp_path / "data", ledger=ledger)
    db.initialise(settings.database)
    conn = db.connect(settings.database)
    now = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    try:
        demo.seed(conn, ledger, now=now, first_review=True)
    finally:
        conn.close()

    found: Books = books.from_household(
        settings, rulings=rulings_in, learns=may_learn, sources=(demo.PRICE_SOURCE,)
    )(now)

    assert len(found.pile) > 1000
    assert len({item.group for item in found.pile}) > 15
    assert sum(1 for e in found.entries if getattr(e, "flag", "") == "!") == len(found.pile)
    assert any(item.confidence is not None for item in found.pile)


DEMO_CLOCKS = (
    *(datetime(2026, 10, 1, 9, 0, tzinfo=UTC) + timedelta(days=n) for n in range(31)),
    datetime(2026, 10, 19, 0, 30, tzinfo=UTC),
    datetime(2027, 3, 20, 7, 30, tzinfo=UTC),
)
"""Every day of a whole month, so the days since Avery's card stopped both take in and miss
the 25th, when its payment arrives through the checking account's feed; a moment just after
midnight UTC, when the local day is still the one before; and a moment just after local
midnight within the days after the clocks went forward, when the card's stop twelve days
before falls on the other side of the change."""


@pytest.fixture
def pacific_time() -> Iterator[None]:
    """The machine's zone is America/Los_Angeles, as the demo's clocks above assume."""
    before = os.environ.get("TZ")
    os.environ["TZ"] = "America/Los_Angeles"
    clock_time.tzset()
    try:
        yield
    finally:
        if before is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = before
        clock_time.tzset()


@pytest.mark.usefixtures("pacific_time")
@pytest.mark.parametrize("now", DEMO_CLOCKS, ids=lambda now: now.isoformat())
def test_the_demos_connections_vouch_for_what_they_booked_and_its_stopped_card_shows(
    tmp_path: Path, now: datetime
) -> None:
    """The demo books its feed-era rows as the import will, from each connection, so
    `hearth demo` runs the active path of coverage: every connection vouches for its accounts,
    and Avery's expired card is known only through the day it stopped, which Avery reads on
    their own page while Blake, who sees only Avery's totals, reads the month alone. The card
    payment the checking account's feed books after the stop never carries the card on."""
    ledger = tmp_path / "household.beancount"
    settings = Settings.in_directory(tmp_path, ledger=ledger)
    db.initialise(settings.database)
    conn = db.connect(settings.database)
    try:
        demo.seed(conn, ledger, now=now)
    finally:
        conn.close()
    feed.initialise(settings.feed_database)
    demo.connect(settings, demo.plaid(lambda: now.astimezone().date(), ledger), now=now)

    found = books.from_household(
        settings, rulings=rulings_in, learns=may_learn, sources=(demo.PRICE_SOURCE,)
    )(now)
    today = now.astimezone().date()
    stopped = now.astimezone().date() - timedelta(days=demo.CARD_EXPIRED_DAYS_AGO)

    assert found.synced == {
        "Assets:Avery:Checking": today,
        "Liabilities:Avery:Card": stopped,
        "Assets:Blake:Checking": today,
    }
    status = Status(found, None, recomputing=False)
    conn = db.connect(settings.database)
    try:
        avery, blake = (members.find(conn, name) for name in ("avery", "blake"))
        assert avery is not None
        assert blake is not None
        own = Disclosure.load(conn, avery, status).you().ends
        theirs = Disclosure.load(conn, blake, status).person("avery").ends
    finally:
        conn.close()
    assert own is not None
    assert own.day == stopped
    assert theirs is not None
    assert (theirs.month, theirs.day, theirs.stalest) == (Month.of(stopped), None, None)


def test_the_from_zero_demo_is_a_household_with_members_and_nothing_else(tmp_path: Path) -> None:
    now = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    settings = Settings.in_directory(tmp_path)
    db.initialise(settings.database)
    feed.initialise(settings.feed_database)
    conn = db.connect(settings.database)
    try:
        passwords = demo.seed_from_zero(conn, now=now)
        assert accounts.all_accounts(conn) == []
        assert Registry(conn).all_items() == []
    finally:
        conn.close()

    found = books.from_household(
        settings, rulings=rulings_in, learns=may_learn, sources=(demo.PRICE_SOURCE,)
    )(now)

    assert set(passwords) == {"avery", "blake"}
    assert found.entries == ()
    assert holds_the_books(settings.books_file, found) == []
    stand_in = demo.plaid_from_zero(lambda: now.astimezone().date()).stand_in
    assert stand_in is not None
    assert set(stand_in.institutions) == {i.id for i in DEMO_INSTITUTIONS}


@pytest.mark.parametrize(
    "opened", [datetime(2026, 11, 26, 18, 0, tzinfo=UTC), datetime(2027, 2, 3, 6, 0, tzinfo=UTC)]
)
def test_the_demo_as_of_a_day_waits_on_the_same_rows_whichever_day_it_is_opened(
    monkeypatch: pytest.MonkeyPatch, opened: datetime
) -> None:
    """`hearth demo --as-of` is what the Mac app's tests of Review open: drawn back from the
    day it is given, never the day it runs."""
    from hearth import cli

    def waiting(household_clock: datetime) -> list[tuple[date, str | None]]:
        monkeypatch.setattr(cli, "system_clock", lambda: household_clock)
        with cli._demo_household(first_review=False, as_of=date(2026, 11, 22)) as household:
            assert household.settings.ledger is not None
            return sorted(
                (entry.date, entry.payee)
                for entry in books.load_entries(household.settings.ledger)
                if isinstance(entry, Transaction) and entry.flag == "!"
            )

    rows = waiting(opened)

    assert rows == waiting(datetime(2026, 11, 22, 18, 0, tzinfo=UTC))
    assert (date(2026, 11, 21), "Kibble Co") in rows
    assert max(day for day, _ in rows) <= date(2026, 11, 22)


@pytest.mark.parametrize(
    "opened", [datetime(2026, 11, 26, 18, 0, tzinfo=UTC), datetime(2027, 2, 3, 6, 0, tzinfo=UTC)]
)
def test_the_demo_as_of_a_day_has_books_as_of_that_day(
    monkeypatch: pytest.MonkeyPatch, opened: datetime
) -> None:
    """Opened on any day, the demo as of Nov 22 runs on Nov 22's clock: its books, and every
    screen of the Mac app's sample, are as of the day it stands on, never the day it runs."""
    from hearth import cli

    monkeypatch.setattr(cli, "system_clock", lambda: opened)
    with cli._demo_household(first_review=False, as_of=date(2026, 11, 22)) as household:
        assert household.clock().astimezone().date() == date(2026, 11, 22)
        found = household.establish(household.clock())
    assert found.as_of == date(2026, 11, 22)


@pytest.mark.parametrize(
    "argv", [["demo", "--as-of", "2026-11-22", "--from-zero"], ["demo", "--as-of", "22/11/2026"]]
)
def test_a_demo_as_of_a_day_is_a_day_of_the_invented_household(argv: list[str]) -> None:
    from hearth import cli

    with pytest.raises(SystemExit) as refused:
        cli.main(argv)

    assert refused.value.code == 2
