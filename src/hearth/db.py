"""The app-state database: members, sessions, sharing preferences and the audit log.

This database is deliberately separate from the ledger. The ledger is the source of
truth for money, so no table here ever stores a monetary figure.

Schema changes are append-only migrations keyed by SQLite's ``user_version``.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE members (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin INTEGER NOT NULL CHECK (is_admin IN (0, 1)),
        must_change_password INTEGER NOT NULL CHECK (must_change_password IN (0, 1)),
        created_at TEXT NOT NULL
    ) STRICT;

    -- Only a hash of the session token is stored, so a copy of this database
    -- cannot be replayed as a cookie.
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        member_id INTEGER NOT NULL REFERENCES members (id),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        revoked_at TEXT
    ) STRICT;
    CREATE INDEX sessions_by_member ON sessions (member_id);

    CREATE TABLE failed_sign_ins (
        id INTEGER PRIMARY KEY,
        username_hash TEXT NOT NULL,
        client TEXT NOT NULL,
        at TEXT NOT NULL
    ) STRICT;
    CREATE INDEX failed_sign_ins_by_username_hash ON failed_sign_ins (username_hash, at);
    CREATE INDEX failed_sign_ins_by_client ON failed_sign_ins (client, at);

    -- A row exists only once a member has made a choice; absent rows mean the
    -- default in hearth.sharing applies.
    CREATE TABLE sharing_preferences (
        member_id INTEGER NOT NULL REFERENCES members (id),
        facet TEXT NOT NULL,
        shared INTEGER NOT NULL CHECK (shared IN (0, 1)),
        updated_at TEXT NOT NULL,
        PRIMARY KEY (member_id, facet)
    ) STRICT;

    -- Ledger accounts the household has claimed. The opaque id is what appears
    -- in URLs; the ledger name never does.
    CREATE TABLE accounts (
        id TEXT PRIMARY KEY,
        ledger_account TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL
    ) STRICT;

    -- Many-to-many: a joint account has every one of its real owners here.
    CREATE TABLE account_owners (
        account_id TEXT NOT NULL REFERENCES accounts (id),
        member_id INTEGER NOT NULL REFERENCES members (id),
        PRIMARY KEY (account_id, member_id)
    ) STRICT;

    CREATE TABLE audit_events (
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        kind TEXT NOT NULL,
        actor_id INTEGER REFERENCES members (id),
        subject_id INTEGER REFERENCES members (id),
        detail TEXT NOT NULL
    ) STRICT;
    CREATE INDEX audit_events_by_subject ON audit_events (subject_id, at);
    """,
)


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, autocommit=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def initialise(path: Path) -> None:
    """Create the database if needed and bring its schema up to date."""
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        migrate(conn)
    finally:
        conn.close()


def migrate(conn: sqlite3.Connection) -> None:
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        with transaction(conn):
            conn.executescript(script)
            conn.execute(f"PRAGMA user_version = {number}")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Group statements atomically on an autocommit connection."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
