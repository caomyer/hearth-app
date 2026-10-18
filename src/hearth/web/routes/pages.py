"""The pages that show household data. Each one renders a view from ``Disclosure``."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from hearth import audit
from hearth.web.deps import Conn, SelectedMonth, SignedIn, Viewing
from hearth.web.templating import render

router = APIRouter()


@router.get("/")
def household(
    request: Request, session: SignedIn, view: Viewing, month: SelectedMonth
) -> HTMLResponse:
    return render(request, "household.html", session=session, household=view.household(month))


@router.get("/members/{username}")
def member(
    request: Request, username: str, session: SignedIn, view: Viewing, month: SelectedMonth
) -> HTMLResponse:
    return render(request, "member.html", session=session, view=view.member(username, month))


@router.get("/accounts/{account_id}")
def account(
    request: Request, account_id: str, session: SignedIn, view: Viewing, month: SelectedMonth
) -> HTMLResponse:
    return render(request, "account.html", session=session, view=view.account(account_id, month))


@router.get("/activity")
def activity(request: Request, conn: Conn, session: SignedIn, view: Viewing) -> HTMLResponse:
    lines = view.activity(audit.all_events(conn))
    return render(request, "activity.html", session=session, lines=lines)
