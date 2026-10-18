"""The app-state database: members, sessions, sharing, the audit log, prices, institution
tokens and review rulings.

This database is deliberately separate from the ledger. The ledger is the source of
truth for the household's money, so no table here ever stores a balance, an amount or
any other figure about it. Market prices are kept here, each with its provenance,
because a price is a fact about the market on a day, not about the household.

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
    """
    -- Raw closing prices, append-only: a source's correction for a day is a newer row,
    -- and the older one stays as the record of what was believed. See hearth.valuation.
    CREATE TABLE price (
        id INTEGER PRIMARY KEY,
        symbol TEXT NOT NULL,
        as_of TEXT NOT NULL,
        price TEXT NOT NULL,
        currency TEXT NOT NULL,
        source TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        fetched_at TEXT NOT NULL
    ) STRICT;
    CREATE INDEX price_by_day ON price (symbol, source, as_of, id);

    -- Institution tokens, envelope-encrypted under a master key held in the Keychain.
    -- No plaintext column exists. See hearth.vault.
    -- Names this database's keys in the Keychain, so two hearth databases on one Mac
    -- (the demo and the real one, say) never share or rotate away each other's keys.
    CREATE TABLE vault_identity (
        id TEXT PRIMARY KEY
    ) STRICT;
    CREATE TABLE vault_master_key (
        version INTEGER PRIMARY KEY,
        created_at TEXT NOT NULL,
        retired_at TEXT
    ) STRICT;
    CREATE TABLE vault_token (
        member_id INTEGER NOT NULL REFERENCES members (id),
        item_id TEXT NOT NULL,
        key_version INTEGER NOT NULL REFERENCES vault_master_key (version),
        wrapped_key BLOB NOT NULL,
        ciphertext BLOB NOT NULL,
        stored_at TEXT NOT NULL,
        PRIMARY KEY (member_id, item_id)
    ) STRICT;

    -- Rulings on set-aside rows, append-only and keyed by content-derived review id.
    -- A group ruling lists the rows it covered, so it never reaches a later row.
    -- See hearth.review.
    CREATE TABLE review_rulings (
        id INTEGER PRIMARY KEY,
        member_id INTEGER NOT NULL REFERENCES members (id),
        scope TEXT NOT NULL CHECK (scope IN ('row', 'group')),
        group_name TEXT,
        action TEXT NOT NULL CHECK (action IN ('accept', 'account', 'reject')),
        account TEXT,
        note TEXT,
        at TEXT NOT NULL,
        CHECK ((scope = 'group') = (group_name IS NOT NULL)),
        CHECK ((action = 'account') = (account IS NOT NULL))
    ) STRICT;
    CREATE TABLE review_ruling_rows (
        ruling_id INTEGER NOT NULL REFERENCES review_rulings (id),
        review_id TEXT NOT NULL,
        account TEXT,
        PRIMARY KEY (ruling_id, review_id)
    ) STRICT;
    CREATE INDEX review_ruling_rows_by_row ON review_ruling_rows (review_id);
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
