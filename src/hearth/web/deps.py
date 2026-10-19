"""Request dependencies: the database, the signed-in member, and what they may see."""

import hmac
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from hearth import db, members, sessions
from hearth.books import Bookkeeper
from hearth.clock import Clock
from hearth.config import Settings
from hearth.connections import Connections
from hearth.disclosure import Concealed, Disclosure, may_administer
from hearth.members import Member
from hearth.months import Month
from hearth.plaid.connector import Runner
from hearth.plaid.setup import PlaidSetup
from hearth.valuation.quotes import PriceSource
from hearth.web.errors import FormExpired, PasswordChangeRequired, SignInRequired

SESSION_COOKIE = "hearth_session"
CSRF_FIELD = "csrf"
CSRF_HEADER = "X-CSRF-Token"


@dataclass(frozen=True, slots=True)
class Hearth:
    """Everything a running application is built from."""

    settings: Settings
    bookkeeper: Bookkeeper
    clock: Clock
    price_source: PriceSource
    connections: Connections
    plaid: PlaidSetup
    runner: Runner

    @property
    def signer(self) -> sessions.CookieSigner:
        return sessions.CookieSigner(self.settings.signing_key)


@dataclass(frozen=True, slots=True)
class Session:
    member: Member
    token: str
    csrf: str


def hearth(request: Request) -> Hearth:
    app: Hearth = request.app.state.hearth
    return app


App = Annotated[Hearth, Depends(hearth)]


def database(app: App) -> Iterator[sqlite3.Connection]:
    conn = db.connect(app.settings.database)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(database)]


def optional_session(request: Request, app: App, conn: Conn) -> Session | None:
    cookie = request.cookies.get(SESSION_COOKIE)
    token = app.signer.unsign(cookie) if cookie else None
    member_id = sessions.resolve(conn, token, now=app.clock()) if token else None
    member = members.get(conn, member_id) if member_id is not None else None
    if token is None or member is None:
        return None
    return Session(member, token, app.signer.csrf_token(token))


def session_outside_routing(request: Request) -> Session | None:
    """The viewer's session, for pages rendered where dependencies do not run, such as errors."""
    app = hearth(request)
    conn = db.connect(app.settings.database)
    try:
        return optional_session(request, app, conn)
    finally:
        conn.close()


def any_session(session: Annotated[Session | None, Depends(optional_session)]) -> Session:
    """A signed-in member, even one who has yet to replace a one-time password."""
    if session is None:
        raise SignInRequired
    return session


def signed_in(session: Annotated[Session, Depends(any_session)]) -> Session:
    if session.member.must_change_password:
        raise PasswordChangeRequired
    return session


async def form_token_checked(
    request: Request, session: Annotated[Session, Depends(any_session)]
) -> Session:
    """Reject a state-changing request that does not carry this session's form token."""
    sent = request.headers.get(CSRF_HEADER)
    if sent is None:
        sent = str((await request.form()).get(CSRF_FIELD, ""))
    if not hmac.compare_digest(sent, session.csrf):
        raise FormExpired
    return session


def acting(session: Annotated[Session, Depends(form_token_checked)]) -> Session:
    """A signed-in member making a change through a form they were given."""
    return signed_in(session)


def administering(session: Annotated[Session, Depends(signed_in)]) -> Session:
    if not may_administer(session.member):
        raise Concealed
    return session


def administering_change(session: Annotated[Session, Depends(acting)]) -> Session:
    return administering(session)


def disclosure(app: App, conn: Conn, session: Annotated[Session, Depends(signed_in)]) -> Disclosure:
    return Disclosure.load(conn, session.member, app.bookkeeper.status)


def selected_month(app: App, month: str | None = None) -> Month:
    """The month asked for, or the current one. A malformed month is simply not there."""
    if month is None:
        return Month.of(app.clock().astimezone().date())
    try:
        return Month.parse(month)
    except ValueError:
        raise Concealed from None


AnySession = Annotated[Session, Depends(any_session)]
SignedIn = Annotated[Session, Depends(signed_in)]
Acting = Annotated[Session, Depends(acting)]
ActingAnySession = Annotated[Session, Depends(form_token_checked)]
Admin = Annotated[Session, Depends(administering)]
AdminActing = Annotated[Session, Depends(administering_change)]
Viewing = Annotated[Disclosure, Depends(disclosure)]
SelectedMonth = Annotated[Month, Depends(selected_month)]
