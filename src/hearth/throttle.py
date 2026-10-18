"""Rate limiting of failed sign-ins, per username and per client.

The username limit applies whether or not the username exists, so throttling
never reveals which usernames are real. Attempted usernames are stored hashed,
because people do type their password into the username field.
"""

import hashlib
import sqlite3
from datetime import datetime, timedelta

WINDOW = timedelta(minutes=15)
FAILURES_PER_USERNAME = 5
FAILURES_PER_CLIENT = 20


def _key(username: str) -> str:
    return hashlib.sha256(username.encode()).hexdigest()


def is_throttled(conn: sqlite3.Connection, *, username: str, client: str, now: datetime) -> bool:
    since = (now - WINDOW).isoformat()
    (by_username,) = conn.execute(
        "SELECT count(*) FROM failed_sign_ins WHERE username_hash = ? AND at > ?",
        (_key(username), since),
    ).fetchone()
    (by_client,) = conn.execute(
        "SELECT count(*) FROM failed_sign_ins WHERE client = ? AND at > ?", (client, since)
    ).fetchone()
    return bool(by_username >= FAILURES_PER_USERNAME or by_client >= FAILURES_PER_CLIENT)


def record_failure(conn: sqlite3.Connection, *, username: str, client: str, now: datetime) -> None:
    conn.execute("DELETE FROM failed_sign_ins WHERE at <= ?", ((now - WINDOW).isoformat(),))
    conn.execute(
        "INSERT INTO failed_sign_ins (username_hash, client, at) VALUES (?, ?, ?)",
        (_key(username), client, now.isoformat()),
    )


def forgive(conn: sqlite3.Connection, *, username: str) -> None:
    """Clear a username's failures after it signs in successfully."""
    conn.execute("DELETE FROM failed_sign_ins WHERE username_hash = ?", (_key(username),))
