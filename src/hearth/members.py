"""Household members."""

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import NewType

from hearth import passwords

MemberId = NewType("MemberId", int)

_USERNAME = re.compile(r"[a-z][a-z0-9_-]{1,31}")
_DISPLAY_NAME_MAX = 64


class MemberRejectedError(ValueError):
    """A proposed member does not meet the rules."""


@dataclass(frozen=True, slots=True)
class Member:
    id: MemberId
    username: str
    display_name: str
    is_admin: bool
    must_change_password: bool


def normalise_username(raw: str) -> str:
    return raw.strip().lower()


def check_username(username: str) -> None:
    if not _USERNAME.fullmatch(username):
        raise MemberRejectedError(
            "Usernames are 2 to 32 characters: lower-case letters, digits, '-' and '_',"
            " starting with a letter."
        )


def check_display_name(display_name: str) -> None:
    if not display_name or len(display_name) > _DISPLAY_NAME_MAX:
        raise MemberRejectedError(f"Display names are 1 to {_DISPLAY_NAME_MAX} characters.")


def insert(
    conn: sqlite3.Connection,
    *,
    username: str,
    display_name: str,
    password: str,
    is_admin: bool,
    must_change_password: bool,
    now: datetime,
) -> Member:
    check_username(username)
    check_display_name(display_name)
    passwords.check_acceptable(password)
    try:
        cursor = conn.execute(
            "INSERT INTO members"
            " (username, display_name, password_hash, is_admin, must_change_password, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                username,
                display_name,
                passwords.hash_password(password),
                is_admin,
                must_change_password,
                now.isoformat(),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise MemberRejectedError("That username is taken.") from error
    assert cursor.lastrowid is not None  # noqa: S101 - sqlite3 always sets it after INSERT
    return Member(
        id=MemberId(cursor.lastrowid),
        username=username,
        display_name=display_name,
        is_admin=is_admin,
        must_change_password=must_change_password,
    )


def _from_row(row: sqlite3.Row) -> Member:
    return Member(
        id=MemberId(row["id"]),
        username=row["username"],
        display_name=row["display_name"],
        is_admin=bool(row["is_admin"]),
        must_change_password=bool(row["must_change_password"]),
    )


def get(conn: sqlite3.Connection, member_id: MemberId) -> Member | None:
    row = conn.execute(
        "SELECT id, username, display_name, is_admin, must_change_password"
        " FROM members WHERE id = ?",
        (member_id,),
    ).fetchone()
    return _from_row(row) if row else None


def find(conn: sqlite3.Connection, username: str) -> Member | None:
    row = conn.execute(
        "SELECT id, username, display_name, is_admin, must_change_password"
        " FROM members WHERE username = ?",
        (username,),
    ).fetchone()
    return _from_row(row) if row else None


def all_members(conn: sqlite3.Connection) -> list[Member]:
    rows = conn.execute(
        "SELECT id, username, display_name, is_admin, must_change_password"
        " FROM members ORDER BY display_name, id"
    )
    return [_from_row(row) for row in rows]


def password_hash(conn: sqlite3.Connection, member_id: MemberId) -> str:
    (value,) = conn.execute(
        "SELECT password_hash FROM members WHERE id = ?", (member_id,)
    ).fetchone()
    return str(value)


def set_password(
    conn: sqlite3.Connection, member_id: MemberId, password: str, *, must_change: bool
) -> None:
    passwords.check_acceptable(password)
    conn.execute(
        "UPDATE members SET password_hash = ?, must_change_password = ? WHERE id = ?",
        (passwords.hash_password(password), must_change, member_id),
    )


def replace_password_hash(conn: sqlite3.Connection, member_id: MemberId, new_hash: str) -> None:
    conn.execute("UPDATE members SET password_hash = ? WHERE id = ?", (new_hash, member_id))
