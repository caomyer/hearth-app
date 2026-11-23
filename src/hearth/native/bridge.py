"""Who the native app is asking as, decided by hearth and nobody else.

A bridge starts signed out. Until the app signs in with a member's username and password, every
ask is answered ``signed_out``, whatever it names, so nothing about the household (not even
which screens there are) is told to an app that has not said who is asking. Signing in goes
through ``hearth.auth.sign_in``, as the pages' sign-in does, with the same throttling: a wrong
password and an unknown username are one outcome, and neither says which it was.

A signed-in bridge holds a session like a page's (``hearth.sessions``) and checks it on every
ask, so a password reset or change that signs a member out everywhere signs the app out too.
The session is revoked when the app signs out or closes its end.

The password is used once, to sign in, and never kept, logged or written anywhere.
"""

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from hearth import auth, db, members, sessions
from hearth.books import Bookkeeper
from hearth.config import Settings
from hearth.native.doorway import Answer, Doorway

CLIENT = "native app"
"""What the throttle counts the app's failed sign-ins against, as a page's are counted against
the address they came from."""

SIGNED_OUT: Mapping[str, object] = {"state": "signed_out"}


class Outcome(StrEnum):
    """What the app is told when it signs in or out."""

    SIGNED_IN = "signed_in"
    REJECTED = "rejected"
    """Wrong username or password. Which of the two is never said."""
    THROTTLED = "throttled"
    """Too many recent failures for this username, or from the app."""
    MUST_CHANGE_PASSWORD = "must_change_password"  # noqa: S105 - an outcome, not a password
    """The password is right, but it is a one-time password, which is changed in hearth's own
    pages. The bridge stays signed out."""
    SIGNED_OUT = "signed_out"


@dataclass(frozen=True, slots=True)
class _Session:
    member_id: members.MemberId
    token: str


@dataclass(slots=True)
class Bridge:
    """The app's way in: signed out, or one member's doorway."""

    settings: Settings
    bookkeeper: Bookkeeper
    clock: Callable[[], datetime]
    _session: _Session | None = field(default=None, init=False)

    def sign_in(self, username: str, password: str) -> Outcome:
        """Sign in as a member. Whoever was signed in before is not any more, whatever the
        outcome."""
        self.sign_out()
        conn = db.connect(self.settings.database)
        try:
            now = self.clock()
            match auth.sign_in(conn, username, password, client=CLIENT, now=now):
                case auth.SignedIn(member=member, token=token):
                    if member.must_change_password:
                        auth.sign_out(conn, member, token, now=now)
                        return Outcome.MUST_CHANGE_PASSWORD
                    self._session = _Session(member.id, token)
                    return Outcome.SIGNED_IN
                case auth.Throttled():
                    return Outcome.THROTTLED
                case auth.Rejected():
                    return Outcome.REJECTED
        finally:
            conn.close()

    def sign_out(self) -> Outcome:
        session, self._session = self._session, None
        if session is not None:
            conn = db.connect(self.settings.database)
            try:
                member = members.get(conn, session.member_id)
                if member is not None:
                    auth.sign_out(conn, member, session.token, now=self.clock())
            finally:
                conn.close()
        return Outcome.SIGNED_OUT

    def answer(self, ask: str) -> Answer:
        viewer = self._viewer()
        if viewer is None:
            return dict(SIGNED_OUT)
        return Doorway(self.settings, self.bookkeeper, viewer).answer(ask)

    def _viewer(self) -> str | None:
        """The username of the member signed in, while their session lasts."""
        if self._session is None:
            return None
        conn = db.connect(self.settings.database)
        try:
            member = _live(conn, self._session, self.clock())
        finally:
            conn.close()
        if member is None:
            # Revoked or expired elsewhere: the app is signed out, as a page would be.
            self._session = None
            return None
        return member.username


def _live(conn: sqlite3.Connection, session: _Session, now: datetime) -> members.Member | None:
    if sessions.resolve(conn, session.token, now=now) != session.member_id:
        return None
    return members.get(conn, session.member_id)
