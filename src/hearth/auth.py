"""Signing in and out, and the password operations that change who can sign in as whom.

Every operation here that touches credentials is recorded in the audit log in the
same transaction as the change itself.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from hearth import audit, members, passwords, sessions, throttle
from hearth.audit import EventKind
from hearth.db import transaction
from hearth.members import Member


@dataclass(frozen=True, slots=True)
class SignedIn:
    member: Member
    token: str


class Rejected:
    """Wrong username or password. Which of the two is never said."""


class Throttled:
    """Too many recent failures for this username or this client."""


type SignInOutcome = SignedIn | Rejected | Throttled


def sign_in(
    conn: sqlite3.Connection, raw_username: str, password: str, *, client: str, now: datetime
) -> SignInOutcome:
    username = members.normalise_username(raw_username)
    if throttle.is_throttled(conn, username=username, client=client, now=now):
        return Throttled()
    member = members.find(conn, username)
    if member is None:
        passwords.verify_nothing(password)
        with transaction(conn):
            throttle.record_failure(conn, username=username, client=client, now=now)
        return Rejected()
    stored = members.password_hash(conn, member.id)
    if not passwords.verify(stored, password):
        with transaction(conn):
            throttle.record_failure(conn, username=username, client=client, now=now)
            audit.record(conn, EventKind.SIGN_IN_FAILED, at=now, actor=None, subject=member.id)
        return Rejected()
    with transaction(conn):
        if passwords.needs_rehash(stored):
            members.replace_password_hash(conn, member.id, passwords.hash_password(password))
        throttle.forgive(conn, username=username)
        token = sessions.start(conn, member.id, now=now)
        audit.record(conn, EventKind.SIGNED_IN, at=now, actor=member.id, subject=member.id)
    return SignedIn(member, token)


def sign_out(conn: sqlite3.Connection, member: Member, token: str, *, now: datetime) -> None:
    with transaction(conn):
        sessions.revoke(conn, token, now=now)
        audit.record(conn, EventKind.SIGNED_OUT, at=now, actor=member.id, subject=member.id)


def change_password(
    conn: sqlite3.Connection,
    member: Member,
    *,
    current: str,
    new: str,
    session_token: str,
    now: datetime,
) -> None:
    """Change one's own password, signing out every other session.

    Raises ``PasswordRejectedError`` if the current password is wrong or the new
    one is not acceptable.
    """
    if not passwords.verify(members.password_hash(conn, member.id), current):
        raise passwords.PasswordRejectedError("Your current password is not right.")
    if new == current:
        raise passwords.PasswordRejectedError("Choose a password you have not just used.")
    with transaction(conn):
        members.set_password(conn, member.id, new, must_change=False)
        sessions.revoke_all(conn, member.id, now=now, keep=session_token)
        audit.record(conn, EventKind.PASSWORD_CHANGED, at=now, actor=member.id, subject=member.id)


def reset_password(
    conn: sqlite3.Connection, admin: Member, subject: Member, *, now: datetime
) -> str:
    """Give ``subject`` a one-time password, signing them out everywhere.

    The admin learns the password, and so could sign in as the member until they
    change it; that is why every reset is audited and visible to the member.
    """
    temporary = passwords.temporary_password()
    with transaction(conn):
        members.set_password(conn, subject.id, temporary, must_change=True)
        sessions.revoke_all(conn, subject.id, now=now)
        audit.record(conn, EventKind.PASSWORD_RESET, at=now, actor=admin.id, subject=subject.id)
    return temporary


def create_member(
    conn: sqlite3.Connection,
    *,
    actor: Member | None,
    username: str,
    display_name: str,
    is_admin: bool,
    password: str | None = None,
    now: datetime,
) -> tuple[Member, str]:
    """Add a member, returning them with the password they first sign in with.

    Without ``password`` the member gets a one-time password they must change.
    Raises ``MemberRejectedError`` or ``PasswordRejectedError``.
    """
    initial = password if password is not None else passwords.temporary_password()
    with transaction(conn):
        member = members.insert(
            conn,
            username=members.normalise_username(username),
            display_name=display_name.strip(),
            password=initial,
            is_admin=is_admin,
            must_change_password=password is None,
            now=now,
        )
        audit.record(
            conn,
            EventKind.MEMBER_CREATED,
            at=now,
            actor=actor.id if actor else None,
            subject=member.id,
            detail="as an admin" if is_admin else "",
        )
    return member, initial
