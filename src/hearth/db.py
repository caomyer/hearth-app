"""The app-state database: members, sessions, sharing, the audit log, prices, institution
tokens and credentials, the connections they open, and review rulings.

This database is deliberately separate from the ledger. The ledger is the source of
truth for the household's money, so no table here ever stores a balance, an amount or
any other figure about it. Market prices are kept here, each with its provenance,
because a price is a fact about the market on a day, not about the household.

Schema changes are append-only migrations keyed by SQLite's ``user_version``.
"""

import sqlite3
import time
from collections.abc import Iterator, Sequence
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
    """
    -- A ruling can now reopen rows, so a decision made at keyboard speed can be taken back.
    -- SQLite cannot change a CHECK, so both tables are rebuilt, child first, ids kept.
    CREATE TABLE review_rulings_v2 (
        id INTEGER PRIMARY KEY,
        member_id INTEGER NOT NULL REFERENCES members (id),
        scope TEXT NOT NULL CHECK (scope IN ('row', 'group')),
        group_name TEXT,
        action TEXT NOT NULL CHECK (action IN ('accept', 'account', 'reject', 'reopen')),
        account TEXT,
        note TEXT,
        at TEXT NOT NULL,
        CHECK ((scope = 'group') = (group_name IS NOT NULL)),
        CHECK ((action = 'account') = (account IS NOT NULL))
    ) STRICT;
    CREATE TABLE review_ruling_rows_v2 (
        ruling_id INTEGER NOT NULL REFERENCES review_rulings_v2 (id),
        review_id TEXT NOT NULL,
        account TEXT,
        PRIMARY KEY (ruling_id, review_id)
    ) STRICT;
    INSERT INTO review_rulings_v2 SELECT * FROM review_rulings;
    INSERT INTO review_ruling_rows_v2 SELECT * FROM review_ruling_rows;
    DROP TABLE review_ruling_rows;
    DROP TABLE review_rulings;
    ALTER TABLE review_rulings_v2 RENAME TO review_rulings;
    ALTER TABLE review_ruling_rows_v2 RENAME TO review_ruling_rows;
    CREATE INDEX review_ruling_rows_by_row ON review_ruling_rows (review_id);

    -- How often each member wants to be reminded to review; no row means the default
    -- in hearth.review.cadence. When they last reviewed is read off their rulings.
    CREATE TABLE review_cadence (
        member_id INTEGER PRIMARY KEY REFERENCES members (id),
        cadence TEXT NOT NULL CHECK (cadence IN ('off', 'daily', 'weekly', 'monthly')),
        updated_at TEXT NOT NULL
    ) STRICT;
    """,
    """
    -- Plaid credentials (client id and secret), sealed like tokens. See hearth.vault.
    CREATE TABLE vault_credential (
        scope TEXT NOT NULL,
        name TEXT NOT NULL,
        key_version INTEGER NOT NULL REFERENCES vault_master_key (version),
        wrapped_key BLOB NOT NULL,
        ciphertext BLOB NOT NULL,
        stored_at TEXT NOT NULL,
        PRIMARY KEY (scope, name)
    ) STRICT;

    -- A Plaid account hearth links through: a member's own, or the household's one.
    -- Its keys are in vault_credential; this is what may be shown about it. See hearth.plaid.
    CREATE TABLE plaid_credentials (
        scope TEXT PRIMARY KEY,
        member_id INTEGER REFERENCES members (id),
        registered_by INTEGER NOT NULL REFERENCES members (id),
        environment TEXT NOT NULL CHECK (environment IN ('production', 'sandbox', 'demo')),
        item_limit INTEGER NOT NULL CHECK (item_limit > 0),
        used_elsewhere INTEGER NOT NULL CHECK (used_elsewhere >= 0),
        saved_at TEXT NOT NULL,
        CHECK ((scope = 'household') = (member_id IS NULL))
    ) STRICT;

    -- Members without a Plaid account of their own who chose to link through the household's.
    CREATE TABLE plaid_household_users (
        member_id INTEGER PRIMARY KEY REFERENCES members (id),
        chosen_at TEXT NOT NULL
    ) STRICT;

    -- One login at one institution. A removed Item stays as the record that it existed.
    CREATE TABLE plaid_items (
        item_id TEXT PRIMARY KEY,
        member_id INTEGER NOT NULL REFERENCES members (id),
        credentials TEXT NOT NULL,
        institution_id TEXT,
        institution_name TEXT NOT NULL,
        days_requested INTEGER NOT NULL,
        linked_at TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('working', 'login_required', 'removed')),
        history TEXT,
        last_synced_at TEXT,
        problem TEXT,
        removed_at TEXT,
        -- When hearth next asks Plaid for history still arriving, and which process will ask
        -- (``hearth.plaid.owner``); NULL when nothing is waiting.
        next_check_at TEXT,
        next_check_by TEXT
    ) STRICT;
    CREATE INDEX plaid_items_by_member ON plaid_items (member_id);

    -- Each account an Item reaches, and the ledger account its member confirmed it feeds.
    CREATE TABLE plaid_accounts (
        account_id TEXT PRIMARY KEY,
        item_id TEXT NOT NULL REFERENCES plaid_items (item_id),
        name TEXT NOT NULL,
        official_name TEXT,
        mask TEXT,
        type TEXT NOT NULL,
        subtype TEXT,
        hearth_account TEXT REFERENCES accounts (id),
        confirmed_at TEXT,
        confirmed_by INTEGER REFERENCES members (id),
        CHECK ((hearth_account IS NULL) = (confirmed_at IS NULL))
    ) STRICT;
    CREATE INDEX plaid_accounts_by_item ON plaid_accounts (item_id);

    -- Statement files a member uploaded to fill history a bank did not send. The file is in
    -- the data directory under `stored_as`; the import reads it from there.
    CREATE TABLE statement_uploads (
        id INTEGER PRIMARY KEY,
        member_id INTEGER NOT NULL REFERENCES members (id),
        account_id TEXT NOT NULL REFERENCES plaid_accounts (account_id),
        original_name TEXT NOT NULL,
        stored_as TEXT NOT NULL UNIQUE,
        size_bytes INTEGER NOT NULL,
        uploaded_at TEXT NOT NULL
    ) STRICT;
    """,
)


BUSY_FOR = 5.0
"""How long a statement waits for another connection to let go, as `busy_timeout` says."""


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, autocommit=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(BUSY_FOR * 1000)}")
    return conn


def initialise(path: Path) -> None:
    """Create the database if needed and bring its schema up to date."""
    conn = connect(path)
    try:
        use_wal(conn)
        migrate(conn)
    finally:
        conn.close()


def use_wal(conn: sqlite3.Connection) -> None:
    """Put the database in write-ahead logging, waiting while another connection holds it.

    Switching needs the database to itself, and SQLite answers "locked" at once rather than
    wait as it does for other statements, so two processes starting on a new database would
    otherwise fail one of them.
    """
    deadline = time.monotonic() + BUSY_FOR
    while True:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError as error:
            if "locked" not in str(error) or time.monotonic() > deadline:
                raise
            time.sleep(0.01)
        else:
            return


def migrate(conn: sqlite3.Connection, migrations: Sequence[str] = MIGRATIONS) -> None:
    """Apply each migration the database has not had, one transaction apiece.

    The version is read inside each migration's transaction, so a process starting up beside
    another, the server and a command run at the same moment, never applies one twice.
    """
    for number, script in enumerate(migrations, start=1):
        with transaction(conn):
            (version,) = conn.execute("PRAGMA user_version").fetchone()
            if version >= number:
                continue
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
