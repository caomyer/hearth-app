"""The demo's first review is a real backfill: big, grouped, every row keyed and flagged."""

from datetime import UTC, datetime
from pathlib import Path

from hearth import books, db, demo
from hearth.books import Books
from hearth.disclosure import rulings_in


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
