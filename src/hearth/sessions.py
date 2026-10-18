"""Server-side sessions behind signed cookies.

The cookie carries a random token and an HMAC of it. The database stores only a
hash of the token, so a session can be revoked at any time and a stolen copy of
the database cannot be replayed as a cookie.
"""

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta

from hearth.members import MemberId

LIFETIME = timedelta(days=14)


class CookieSigner:
    def __init__(self, secret_key: bytes) -> None:
        self._key = secret_key

    def sign(self, token: str) -> str:
        return f"{token}.{self._mac('session', token)}"

    def unsign(self, cookie: str) -> str | None:
        token, _, mac = cookie.rpartition(".")
        if token and hmac.compare_digest(mac, self._mac("session", token)):
            return token
        return None

    def csrf_token(self, token: str) -> str:
        """A per-session value that forms must echo back, bound to the session it came from."""
        return self._mac("csrf", token)

    def _mac(self, purpose: str, token: str) -> str:
        message = f"{purpose}:{token}".encode()
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start(conn: sqlite3.Connection, member_id: MemberId, *, now: datetime) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (token_hash, member_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (_hash(token), member_id, now.isoformat(), (now + LIFETIME).isoformat()),
    )
    return token


def resolve(conn: sqlite3.Connection, token: str, *, now: datetime) -> MemberId | None:
    """The member a live session belongs to, or None if it is unknown, expired or revoked."""
    row = conn.execute(
        "SELECT member_id FROM sessions"
        " WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?",
        (_hash(token), now.isoformat()),
    ).fetchone()
    return MemberId(row["member_id"]) if row else None


def revoke(conn: sqlite3.Connection, token: str, *, now: datetime) -> None:
    conn.execute(
        "UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
        (now.isoformat(), _hash(token)),
    )


def revoke_all(
    conn: sqlite3.Connection, member_id: MemberId, *, now: datetime, keep: str | None = None
) -> None:
    """Revoke every session of a member, optionally sparing the one in use."""
    conn.execute(
        "UPDATE sessions SET revoked_at = ?"
        " WHERE member_id = ? AND revoked_at IS NULL AND token_hash IS NOT ?",
        (now.isoformat(), member_id, _hash(keep) if keep else None),
    )
