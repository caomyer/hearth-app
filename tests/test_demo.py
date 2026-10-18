"""The demo household, built by the same path `hearth demo` takes."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from beancount.core.data import Transaction

from hearth import accounts, books, db, demo, members
from hearth.books import Books
from hearth.config import Settings
from hearth.disclosure import rulings_in
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
