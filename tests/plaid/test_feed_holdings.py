"""What a brokerage says each holding cost is kept with the holding, as it said it."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from hearth import db
from hearth.plaid import feed
from hearth.plaid.client import Holdings, Position
from hearth.plaid.feed import Feed

NOW = datetime(2026, 3, 2, 14, 30, tzinfo=UTC)


def _position(security_id: str, cost: Decimal | None) -> Position:
    return Position(
        account_id="acct-brk",
        security_id=security_id,
        ticker=security_id.upper(),
        name=None,
        cash=False,
        quantity=Decimal("10.125"),
        value=Decimal("1012.50"),
        currency="USD",
        cost_basis=cost,
    )


def test_each_holdings_cost_is_kept_to_the_cent_or_as_not_known(tmp_path: Path) -> None:
    path = tmp_path / "feed.sqlite3"
    feed.initialise(path)
    found = Holdings(("acct-brk",), (_position("zqa", Decimal("900.10")), _position("zqb", None)))

    with Feed.open(path) as store:
        store.record_holdings("item-1", found, now=NOW)
        report = store.holdings("acct-brk")

    assert report is not None
    assert {p.security_id: p.cost_basis for p in report.positions} == {
        "zqa": Decimal("900.10"),
        "zqb": None,
    }


def test_a_holding_reported_before_hearth_kept_its_cost_reads_as_not_known(
    tmp_path: Path,
) -> None:
    path = tmp_path / "feed.sqlite3"
    conn = db.connect(path)
    try:
        db.migrate(conn, feed.MIGRATIONS[:-1])
        with db.transaction(conn):
            report = conn.execute(
                "INSERT INTO feed_holdings_report (item_id, account_id, reported_at)"
                " VALUES ('item-1', 'acct-brk', ?)",
                (NOW.isoformat(),),
            ).lastrowid
            conn.execute(
                "INSERT INTO feed_holding (report, security_id, ticker, name, cash, quantity,"
                " value, currency) VALUES (?, 'zqa', 'ZQA', NULL, 0, '10.125', '1012.50', 'USD')",
                (report,),
            )
    finally:
        conn.close()

    feed.initialise(path)

    with Feed.open(path) as store:
        found = store.holdings("acct-brk")
    assert found is not None
    ((held),) = found.positions
    assert (held.quantity, held.cost_basis) == (Decimal("10.125"), None)
    raw = db.connect(path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == len(feed.MIGRATIONS)
    finally:
        raw.close()
