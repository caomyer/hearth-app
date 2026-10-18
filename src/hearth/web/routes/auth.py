"""Signing in and out, and choosing one's own password."""

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from hearth import auth
from hearth.passwords import PasswordRejectedError
from hearth.web.deps import (
    SESSION_COOKIE,
    ActingAnySession,
    AnySession,
    App,
    Conn,
    Session,
    optional_session,
)
from hearth.web.templating import render

router = APIRouter()

_HOME = "/"


def safe_destination(raw: str | None) -> str:
    """A path on this site to continue to after signing in; anything else goes home."""
    if not raw or not raw.startswith("/") or raw.startswith(("//", "/\\")):
        return _HOME
    if any(not character.isprintable() for character in raw):
        return _HOME
    parts = urlsplit(raw)
    return _HOME if parts.scheme or parts.netloc else raw


def _see_other(location: str) -> RedirectResponse:
    return RedirectResponse(location, status_code=303)


@router.get("/sign-in")
def sign_in_form(
    request: Request,
    app: App,
    conn: Conn,
    requested: Annotated[str | None, Query(alias="next")] = None,
) -> Response:
    if optional_session(request, app, conn) is not None:
        return _see_other(safe_destination(requested))
    return render(
        request, "sign_in.html", destination=safe_destination(requested), username="", problem=None
    )


@router.post("/sign-in")
def sign_in(
    request: Request,
    app: App,
    conn: Conn,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    destination: Annotated[str, Form()] = _HOME,
) -> Response:
    client = request.client.host if request.client else "unknown"
    outcome = auth.sign_in(conn, username, password, client=client, now=app.clock())
    match outcome:
        case auth.SignedIn(member=member, token=token):
            response = _see_other(
                "/settings/password"
                if member.must_change_password
                else safe_destination(destination)
            )
            response.set_cookie(
                SESSION_COOKIE,
                app.signer.sign(token),
                httponly=True,
                samesite="lax",
                secure=app.settings.secure_cookies,
                path="/",
            )
            return response
        case auth.Throttled():
            problem, status = "Too many attempts. Wait a few minutes, then try again.", 429
        case auth.Rejected():
            problem, status = "That username and password do not match.", 400
    return render(
        request,
        "sign_in.html",
        status_code=status,
        destination=safe_destination(destination),
        username=username,
        problem=problem,
    )


@router.post("/sign-out")
def sign_out(app: App, conn: Conn, session: ActingAnySession) -> Response:
    auth.sign_out(conn, session.member, session.token, now=app.clock())
    response = _see_other("/sign-in")
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/settings/password")
def password_form(request: Request, session: AnySession) -> HTMLResponse:
    return _password_page(request, session, problem=None)


@router.post("/settings/password")
def change_password(
    request: Request,
    app: App,
    conn: Conn,
    session: ActingAnySession,
    current: Annotated[str, Form()],
    new: Annotated[str, Form()],
    confirm: Annotated[str, Form()],
) -> Response:
    if new != confirm:
        return _password_page(request, session, problem="The two new passwords differ.")
    try:
        auth.change_password(
            conn,
            session.member,
            current=current,
            new=new,
            session_token=session.token,
            now=app.clock(),
        )
    except PasswordRejectedError as error:
        return _password_page(request, session, problem=str(error))
    return _see_other("/")


def _password_page(request: Request, session: Session, *, problem: str | None) -> HTMLResponse:
    return render(
        request,
        "password.html",
        status_code=200 if problem is None else 400,
        session=session,
        problem=problem,
    )
