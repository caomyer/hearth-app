"""Member administration: adding members and resetting passwords. Nothing more."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from hearth import auth, members
from hearth.disclosure import Concealed
from hearth.members import MemberRejectedError
from hearth.web.deps import Admin, AdminActing, App, Conn, Session
from hearth.web.templating import render

router = APIRouter(prefix="/admin")


@router.get("/members")
def members_page(request: Request, conn: Conn, session: Admin) -> HTMLResponse:
    return _members_page(request, conn, session)


@router.post("/members")
def add_member(
    request: Request,
    app: App,
    conn: Conn,
    session: AdminActing,
    username: Annotated[str, Form()],
    display_name: Annotated[str, Form()],
    is_admin: Annotated[bool, Form()] = False,
) -> HTMLResponse:
    try:
        member, password = auth.create_member(
            conn,
            actor=session.member,
            username=username,
            display_name=display_name,
            is_admin=is_admin,
            now=app.clock(),
        )
    except MemberRejectedError as error:
        return _members_page(request, conn, session, problem=str(error), status_code=400)
    return render(
        request,
        "one_time_password.html",
        session=session,
        member=member,
        password=password,
        created=True,
    )


@router.post("/members/{username}/reset-password")
def reset_password(
    request: Request, app: App, conn: Conn, session: AdminActing, username: str
) -> HTMLResponse:
    subject = members.find(conn, username)
    if subject is None or subject.id == session.member.id:
        raise Concealed
    password = auth.reset_password(conn, session.member, subject, now=app.clock())
    return render(
        request,
        "one_time_password.html",
        session=session,
        member=subject,
        password=password,
        created=False,
    )


def _members_page(
    request: Request,
    conn: Conn,
    session: Session,
    *,
    problem: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return render(
        request,
        "admin_members.html",
        status_code=status_code,
        session=session,
        household=members.all_members(conn),
        problem=problem,
    )
