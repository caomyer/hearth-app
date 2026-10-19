"""The demo household, built by the same path `hearth demo` takes."""

import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from beancount.core.data import Transaction

from hearth import accounts, books, db, demo, members
from hearth.books import Books, Status
from hearth.config import Settings
from hearth.disclosure import Disclosure, rulings_in
from hearth.months import Month
from hearth.plaid import feed
from hearth.plaid.feed import Feed
from hearth.plaid.registry import Registry


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
    demo.connect(settings, demo.plaid(lambda: now.date(), ledger), now=now)

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
        assert held.earliest <= now.date() - timedelta(days=demo.HISTORY_DAYS - 7)
        assert balance is not None
        owed = ledger_account.startswith("Liabilities:")
        expected = -held_in_ledger[ledger_account] if owed else held_in_ledger[ledger_account]
        assert balance.current == expected
        assert balance.current != 0


def test_the_first_review_demo_is_a_backfill_at_full_size(tmp_path: Path) -> None:
    ledger, database = tmp_path / "household.beancount", tmp_path / "hearth.sqlite3"
    db.initialise(database)
    conn = db.connect(database)
    now = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    try:
        demo.seed(conn, ledger, now=now, first_review=True)
    finally:
        conn.close()

    found: Books = books.from_ledger(
        ledger, database, rulings=rulings_in, sources=(demo.PRICE_SOURCE,)
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
    time.tzset()
    try:
        yield
    finally:
        if before is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = before
        time.tzset()


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
    demo.connect(settings, demo.plaid(lambda: now.date(), ledger), now=now)

    found = books.from_ledger(
        ledger, settings.database, rulings=rulings_in, sources=(demo.PRICE_SOURCE,)
    )(now)
    today = min(now.astimezone().date(), now.date())
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
