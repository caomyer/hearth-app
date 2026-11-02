"""What Plaid sent: every transaction, balance and holding, kept as fetched, and a record of
each sync.

This is a staging store, deliberately apart from both the ledger and the app database. The
books stay the one record: nothing here is booked as it sits, and every recompute turns these
rows into entries for the review queue (``hearth.plaid.booking``), so a row Plaid revises or
withdraws here is booked as it now stands, or not at all.
The app database holds no money at all, so the amounts and payees Plaid sends are kept in
this file of their own, ``feed.sqlite3`` beside it.

Every write a sync makes for one page of Plaid's answer, including the cursor that page moves
to, is one transaction. A sync that stops part way therefore leaves the store exactly at the
last page it finished, and the next one carries on from there. When Plaid says its data
changed mid-loop, every page the run stored is undone before the loop starts again.

Amounts are stored as decimal text with Plaid's sign: positive is money leaving the account.
So are a holding's units: a share count read through a float is wrong at the first fractional
share.

What a brokerage says each investment account holds is today's and no other, like its prices
(``hearth.plaid.holdings``): Plaid keeps no history of it, so every answer is kept, each with
when it came, as a day not kept can never be had again. The books take only the latest
(``hearth.plaid.opening``).
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from hearth import db
from hearth.plaid import owner
from hearth.plaid.client import BankAccount, Holdings, Position, SyncPage, Transaction

MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE feed_cursor (
        item_id TEXT PRIMARY KEY,
        cursor TEXT NOT NULL,
        updated_at TEXT NOT NULL
    ) STRICT;

    CREATE TABLE feed_run (
        id INTEGER PRIMARY KEY,
        item_id TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        outcome TEXT NOT NULL
            CHECK (outcome IN ('running', 'complete', 'interrupted', 'failed', 'login_required')),
        from_start INTEGER NOT NULL CHECK (from_start IN (0, 1)),
        pages INTEGER NOT NULL DEFAULT 0,
        added INTEGER NOT NULL DEFAULT 0,
        modified INTEGER NOT NULL DEFAULT 0,
        removed INTEGER NOT NULL DEFAULT 0,
        history TEXT,
        problem TEXT,
        -- The lease a running run holds on its Item, so two syncs never race one Item: when
        -- it last showed it was alive, and which process it is (``hearth.plaid.owner``).
        heartbeat_at TEXT,
        owner TEXT
    ) STRICT;
    CREATE INDEX feed_run_by_item ON feed_run (item_id, id);

    CREATE TABLE feed_transaction (
        transaction_id TEXT PRIMARY KEY,
        item_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        date TEXT NOT NULL,
        authorized_date TEXT,
        amount TEXT NOT NULL,
        currency TEXT,
        name TEXT,
        merchant_name TEXT,
        original_description TEXT,
        category TEXT,
        pending INTEGER NOT NULL CHECK (pending IN (0, 1)),
        pending_transaction_id TEXT,
        first_run INTEGER NOT NULL REFERENCES feed_run (id),
        last_run INTEGER NOT NULL REFERENCES feed_run (id),
        removed_run INTEGER REFERENCES feed_run (id)
    ) STRICT;
    CREATE INDEX feed_transaction_by_account ON feed_transaction (account_id, date);

    -- Each transaction as it was before the running run first changed it, so a pagination
    -- loop Plaid aborts can be undone. Emptied when the run ends. Its columns after `run` are
    -- feed_transaction's, in the same order.
    CREATE TABLE feed_undo (
        run INTEGER NOT NULL REFERENCES feed_run (id),
        transaction_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        date TEXT NOT NULL,
        authorized_date TEXT,
        amount TEXT NOT NULL,
        currency TEXT,
        name TEXT,
        merchant_name TEXT,
        original_description TEXT,
        category TEXT,
        pending INTEGER NOT NULL,
        pending_transaction_id TEXT,
        first_run INTEGER NOT NULL,
        last_run INTEGER NOT NULL,
        removed_run INTEGER,
        PRIMARY KEY (run, transaction_id)
    ) STRICT;

    -- The latest balances Plaid reported for each account, and when.
    CREATE TABLE feed_balance (
        account_id TEXT PRIMARY KEY,
        item_id TEXT NOT NULL,
        current TEXT,
        available TEXT,
        currency TEXT,
        recorded_at TEXT NOT NULL
    ) STRICT;
    """,
    """
    -- Each time a brokerage said what an investment account holds, including that it holds
    -- nothing, and each holding it listed then.
    CREATE TABLE feed_holdings_report (
        id INTEGER PRIMARY KEY,
        item_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        reported_at TEXT NOT NULL
    ) STRICT;
    CREATE INDEX feed_holdings_report_by_account ON feed_holdings_report (account_id, id);

    CREATE TABLE feed_holding (
        report INTEGER NOT NULL REFERENCES feed_holdings_report (id),
        security_id TEXT NOT NULL,
        ticker TEXT,
        name TEXT,
        cash INTEGER NOT NULL CHECK (cash IN (0, 1)),
        quantity TEXT NOT NULL,
        value TEXT,
        currency TEXT,
        PRIMARY KEY (report, security_id)
    ) STRICT;
    """,
)


LEASE = timedelta(minutes=15)
"""How long a running run holds its Item after its last sign of life, while the process that
owns it is alive; a live one renews it with every page it stores. A run whose owner has gone
holds nothing, however recent its last page."""


class Outcome(StrEnum):
    RUNNING = "running"
    COMPLETE = "complete"
    INTERRUPTED = "interrupted"
    """The run stopped without finishing, so it is resumed from the last page it stored."""
    FAILED = "failed"
    LOGIN_REQUIRED = "login_required"


@dataclass(frozen=True, slots=True)
class Run:
    id: int
    item_id: str
    started_at: datetime
    finished_at: datetime | None
    outcome: Outcome
    from_start: bool
    pages: int
    added: int
    modified: int
    removed: int
    history: str | None
    problem: str | None


@dataclass(frozen=True, slots=True)
class Balance:
    current: Decimal | None
    available: Decimal | None
    currency: str | None
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class Report:
    """What a brokerage last said one investment account holds, and when."""

    item_id: str
    account_id: str
    reported_at: datetime
    positions: tuple[Position, ...]


@dataclass(frozen=True, slots=True)
class Held:
    """What the store holds for one account: how many transactions, over which days."""

    account_id: str
    transactions: int
    earliest: date | None
    latest: date | None


SHORT_GRACE_DAYS = 14
"""History that starts within this of the day asked for counts as everything asked for."""


@dataclass(frozen=True, slots=True)
class Coverage:
    """How much history one account actually came with, against what was asked for."""

    account_id: str
    requested_from: date
    held: Held
    complete: bool
    """Whether Plaid says the institution has sent all it will; until then it may still grow."""

    @property
    def is_short(self) -> bool:
        """The account came with materially less than asked for (warn, never block)."""
        earliest = self.held.earliest
        if earliest is None:
            return self.complete
        return earliest > self.requested_from + timedelta(days=SHORT_GRACE_DAYS)

    @property
    def days(self) -> int | None:
        if self.held.earliest is None or self.held.latest is None:
            return None
        return (self.held.latest - self.held.earliest).days + 1


def initialise(path: Path) -> None:
    conn = connect(path)
    try:
        db.use_wal(conn)
        db.migrate(conn, MIGRATIONS)
    finally:
        conn.close()


def connect(path: Path) -> sqlite3.Connection:
    return db.connect(path)


def _holds(row: sqlite3.Row, now: datetime) -> bool:
    """Whether a running run still holds its Item: its lease is fresh and its owner alive."""
    return (
        row["outcome"] == Outcome.RUNNING.value
        and row["heartbeat_at"] is not None
        and datetime.fromisoformat(row["heartbeat_at"]) > now - LEASE
        and owner.is_alive(row["owner"])
    )


def _run(row: sqlite3.Row, now: datetime | None = None) -> Run:
    """A run as stored. Given `now`, a running run that no longer holds its Item is shown as
    the interrupted run it is: nothing will finish it, and the next sync closes it."""
    outcome = Outcome(row["outcome"])
    if outcome is Outcome.RUNNING and now is not None and not _holds(row, now):
        outcome = Outcome.INTERRUPTED
    return Run(
        id=row["id"],
        item_id=row["item_id"],
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
        outcome=outcome,
        from_start=bool(row["from_start"]),
        pages=row["pages"],
        added=row["added"],
        modified=row["modified"],
        removed=row["removed"],
        history=row["history"],
        problem=row["problem"],
    )


def _decimal(text: str | None) -> Decimal | None:
    return Decimal(text) if text is not None else None


class Feed:
    """The feed store, over one connection opened with `connect`."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    @classmethod
    @contextmanager
    def open(cls, path: Path) -> Iterator[Feed]:
        conn = connect(path)
        try:
            yield cls(conn)
        finally:
            conn.close()

    def standing(self) -> tuple[object, ...]:
        """Where the feed stands: changes with each sync that ends and each holdings report,
        whichever process made it, and says nothing of what any of them brought."""
        runs = self._db.execute(
            "SELECT max(id), count(finished_at), max(finished_at) FROM feed_run"
        ).fetchone()
        reports = self._db.execute("SELECT max(id) FROM feed_holdings_report").fetchone()
        balances = self._db.execute("SELECT max(recorded_at) FROM feed_balance").fetchone()
        return (*runs, *reports, *balances)

    def cursor(self, item_id: str) -> str | None:
        row = self._db.execute(
            "SELECT cursor FROM feed_cursor WHERE item_id = ?", (item_id,)
        ).fetchone()
        return row["cursor"] if row else None

    def start_run(self, item_id: str, *, now: datetime) -> Run | None:
        """Claim the Item and begin a run, or return None if another run holds it.

        A run holds its Item while it is running, its owner is alive, and it has shown it is
        alive within `LEASE`. Any other running run can never finish, and is closed as
        interrupted first."""
        with db.transaction(self._db):
            running = self._db.execute(
                "SELECT * FROM feed_run WHERE item_id = ? AND outcome = ?",
                (item_id, Outcome.RUNNING.value),
            ).fetchall()
            if any(_holds(row, now) for row in running):
                return None
            self._db.execute(
                "DELETE FROM feed_undo WHERE run IN"
                " (SELECT id FROM feed_run WHERE item_id = ? AND outcome = ?)",
                (item_id, Outcome.RUNNING.value),
            )
            self._db.execute(
                "UPDATE feed_run SET outcome = 'interrupted' WHERE item_id = ? AND outcome = ?",
                (item_id, Outcome.RUNNING.value),
            )
            cursor = self._db.execute(
                "INSERT INTO feed_run (item_id, started_at, outcome, from_start, heartbeat_at,"
                " owner) VALUES (?, ?, 'running', ?, ?, ?)",
                (
                    item_id,
                    now.isoformat(),
                    self.cursor(item_id) is None,
                    now.isoformat(),
                    owner.this_process(),
                ),
            )
        assert cursor.lastrowid is not None  # noqa: S101 - sqlite3 always sets it after INSERT
        return self.run(cursor.lastrowid)

    def run(self, run_id: int) -> Run:
        return _run(self._db.execute("SELECT * FROM feed_run WHERE id = ?", (run_id,)).fetchone())

    def runs(self, item_id: str, *, now: datetime) -> list[Run]:
        """The Item's runs, newest first, as they stand at `now`."""
        rows = self._db.execute(
            "SELECT * FROM feed_run WHERE item_id = ? ORDER BY id DESC", (item_id,)
        )
        return [_run(row, now) for row in rows]

    def apply(self, run: Run, item_id: str, page: SyncPage, *, now: datetime) -> None:
        """Store one page and move the Item's cursor past it, all or nothing."""
        with db.transaction(self._db):
            self._db.executemany(
                "INSERT OR IGNORE INTO feed_undo SELECT ?, * FROM feed_transaction"
                " WHERE transaction_id = ? AND first_run != ?",
                [
                    (run.id, transaction_id, run.id)
                    for transaction_id in (
                        *(t.transaction_id for t in (*page.added, *page.modified)),
                        *page.removed,
                    )
                ],
            )
            for found in (*page.added, *page.modified):
                self._upsert(found, item_id, run.id)
            self._db.executemany(
                "UPDATE feed_transaction SET removed_run = ? WHERE transaction_id = ?"
                " AND removed_run IS NULL",
                [(run.id, transaction_id) for transaction_id in page.removed],
            )
            self._db.execute(
                "INSERT INTO feed_cursor (item_id, cursor, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT (item_id) DO UPDATE SET cursor = excluded.cursor,"
                " updated_at = excluded.updated_at",
                (item_id, page.next_cursor, now.isoformat()),
            )
            self._db.execute(
                "UPDATE feed_run SET pages = pages + 1, added = added + ?,"
                " modified = modified + ?, removed = removed + ?, history = ?, heartbeat_at = ?"
                " WHERE id = ?",
                (
                    len(page.added),
                    len(page.modified),
                    len(page.removed),
                    page.update_status,
                    now.isoformat(),
                    run.id,
                ),
            )

    def _upsert(self, found: Transaction, item_id: str, run_id: int) -> None:
        self._db.execute(
            "INSERT INTO feed_transaction (transaction_id, item_id, account_id, date,"
            " authorized_date, amount, currency, name, merchant_name, original_description,"
            " category, pending, pending_transaction_id, first_run, last_run)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (transaction_id) DO UPDATE SET account_id = excluded.account_id,"
            " date = excluded.date, authorized_date = excluded.authorized_date,"
            " amount = excluded.amount, currency = excluded.currency, name = excluded.name,"
            " merchant_name = excluded.merchant_name,"
            " original_description = excluded.original_description,"
            " category = excluded.category, pending = excluded.pending,"
            " pending_transaction_id = excluded.pending_transaction_id,"
            " last_run = excluded.last_run, removed_run = NULL",
            (
                found.transaction_id,
                item_id,
                found.account_id,
                found.day.isoformat(),
                found.authorized_date.isoformat() if found.authorized_date else None,
                str(found.amount),
                found.currency,
                found.name,
                found.merchant_name,
                found.original_description,
                found.category,
                found.pending,
                found.pending_transaction_id,
                run_id,
                run_id,
            ),
        )

    def finish(
        self, run: Run, outcome: Outcome, *, now: datetime, problem: str | None = None
    ) -> Run:
        with db.transaction(self._db):
            self._db.execute("DELETE FROM feed_undo WHERE run = ?", (run.id,))
            self._db.execute(
                "UPDATE feed_run SET outcome = ?, finished_at = ?, problem = ? WHERE id = ?",
                (outcome.value, now.isoformat(), problem, run.id),
            )
        return self.run(run.id)

    def restart_from(self, run: Run, item_id: str, cursor: str | None, *, now: datetime) -> None:
        """Undo every page `run` stored and put the Item's cursor back to where it began, as
        Plaid asks when the data changed under a pagination loop: the pages sent again from
        there describe the data as it is now, so nothing of the aborted loop may remain."""
        with db.transaction(self._db):
            self._db.execute("DELETE FROM feed_transaction WHERE first_run = ?", (run.id,))
            self._db.execute(
                "INSERT OR REPLACE INTO feed_transaction"
                " SELECT transaction_id, item_id, account_id, date, authorized_date, amount,"
                " currency, name, merchant_name, original_description, category, pending,"
                " pending_transaction_id, first_run, last_run, removed_run"
                " FROM feed_undo WHERE run = ?",
                (run.id,),
            )
            self._db.execute("DELETE FROM feed_undo WHERE run = ?", (run.id,))
            self._db.execute(
                "UPDATE feed_run SET pages = 0, added = 0, modified = 0, removed = 0,"
                " history = NULL, heartbeat_at = ? WHERE id = ?",
                (now.isoformat(), run.id),
            )
            if cursor is None:
                self._db.execute("DELETE FROM feed_cursor WHERE item_id = ?", (item_id,))
            else:
                self._db.execute(
                    "UPDATE feed_cursor SET cursor = ?, updated_at = ? WHERE item_id = ?",
                    (cursor, now.isoformat(), item_id),
                )

    def record_balances(self, item_id: str, found: list[BankAccount], *, now: datetime) -> None:
        with db.transaction(self._db):
            self._db.executemany(
                "INSERT INTO feed_balance (account_id, item_id, current, available, currency,"
                " recorded_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (account_id) DO UPDATE SET"
                " current = excluded.current, available = excluded.available,"
                " currency = excluded.currency, recorded_at = excluded.recorded_at",
                [
                    (
                        a.account_id,
                        item_id,
                        None if a.current is None else str(a.current),
                        None if a.available is None else str(a.available),
                        a.currency,
                        now.isoformat(),
                    )
                    for a in found
                ],
            )

    def balance(self, account_id: str) -> Balance | None:
        row = self._db.execute(
            "SELECT current, available, currency, recorded_at FROM feed_balance"
            " WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            return None
        return Balance(
            _decimal(row["current"]),
            _decimal(row["available"]),
            row["currency"],
            datetime.fromisoformat(row["recorded_at"]),
        )

    def record_holdings(self, item_id: str, found: Holdings, *, now: datetime) -> None:
        """Keep what one answer says each investment account holds, as a report of its own:
        an account it listed holding nothing is kept as that."""
        with db.transaction(self._db):
            for account_id in found.accounts:
                report = self._db.execute(
                    "INSERT INTO feed_holdings_report (item_id, account_id, reported_at)"
                    " VALUES (?, ?, ?)",
                    (item_id, account_id, now.isoformat()),
                ).lastrowid
                self._db.executemany(
                    "INSERT INTO feed_holding (report, security_id, ticker, name, cash, quantity,"
                    " value, currency) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            report,
                            p.security_id,
                            p.ticker,
                            p.name,
                            int(p.cash),
                            str(p.quantity),
                            None if p.value is None else str(p.value),
                            p.currency,
                        )
                        for p in found.positions
                        if p.account_id == account_id
                    ],
                )

    def holdings(self, account_id: str) -> Report | None:
        """What a brokerage last said the account holds, or None if it never has."""
        report = self._db.execute(
            "SELECT * FROM feed_holdings_report WHERE account_id = ? ORDER BY id DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        if report is None:
            return None
        rows = self._db.execute(
            "SELECT * FROM feed_holding WHERE report = ? ORDER BY security_id", (report["id"],)
        )
        return Report(
            item_id=report["item_id"],
            account_id=account_id,
            reported_at=datetime.fromisoformat(report["reported_at"]),
            positions=tuple(
                Position(
                    account_id=account_id,
                    security_id=row["security_id"],
                    ticker=row["ticker"],
                    name=row["name"],
                    cash=bool(row["cash"]),
                    quantity=Decimal(row["quantity"]),
                    value=_decimal(row["value"]),
                    currency=row["currency"],
                )
                for row in rows
            ),
        )

    def recorded_by(self, balance: Balance, item_id: str) -> Run | None:
        """The run that recorded `balance`, an Item's: its latest run started by then. A run
        records the balances before it stores any page."""
        rows = self._db.execute("SELECT * FROM feed_run WHERE item_id = ?", (item_id,))
        started = [
            row for row in rows if datetime.fromisoformat(row["started_at"]) <= balance.recorded_at
        ]
        latest = max(
            started,
            key=lambda row: (datetime.fromisoformat(row["started_at"]), row["id"]),
            default=None,
        )
        return _run(latest) if latest else None

    def held(self, account_id: str) -> Held:
        """Posted transactions held for the account. Pending ones may still change or vanish."""
        row = self._db.execute(
            "SELECT count(*) AS n, min(date) AS earliest, max(date) AS latest"
            " FROM feed_transaction WHERE account_id = ? AND removed_run IS NULL AND pending = 0",
            (account_id,),
        ).fetchone()
        return Held(
            account_id,
            row["n"],
            date.fromisoformat(row["earliest"]) if row["earliest"] else None,
            date.fromisoformat(row["latest"]) if row["latest"] else None,
        )

    def transactions(self, account_id: str) -> list[Transaction]:
        """The account's transactions as last sent, oldest first; removed ones left out."""
        rows = self._db.execute(
            "SELECT * FROM feed_transaction WHERE account_id = ? AND removed_run IS NULL"
            " ORDER BY date, transaction_id",
            (account_id,),
        )
        return [
            Transaction(
                transaction_id=row["transaction_id"],
                account_id=row["account_id"],
                day=date.fromisoformat(row["date"]),
                authorized_date=(
                    date.fromisoformat(row["authorized_date"]) if row["authorized_date"] else None
                ),
                amount=Decimal(row["amount"]),
                currency=row["currency"],
                name=row["name"],
                merchant_name=row["merchant_name"],
                original_description=row["original_description"],
                category=row["category"],
                pending=bool(row["pending"]),
                pending_transaction_id=row["pending_transaction_id"],
            )
            for row in rows
        ]
