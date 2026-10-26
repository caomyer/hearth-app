"""A member's own sharing choices, with a preview of what everyone else then sees.

Nobody can change anyone else's choices: the route only ever writes the signed-in member's.
"""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from hearth import audit, sharing
from hearth.audit import EventKind
from hearth.disclosure import Concealed
from hearth.sharing import DESCRIPTIONS, Facet
from hearth.web.deps import Acting, App, Conn, SignedIn, Viewing
from hearth.web.routes import connect
from hearth.web.routes.pages import chrome
from hearth.web.templating import render

router = APIRouter(prefix="/settings/sharing")


@router.get("")
def sharing_page(
    request: Request,
    app: App,
    conn: Conn,
    session: SignedIn,
    view: Viewing,
    saved: str | None = None,
) -> HTMLResponse:
    shared = sharing.load(conn)[session.member.id]
    resets = [
        line
        for line in view.activity(audit.all_events(conn))
        if line.kind is EventKind.PASSWORD_RESET
        and line.subject is not None
        and line.subject.username == session.member.username
    ]
    try:
        just_saved = Facet(saved) if saved else None
    except ValueError:
        just_saved = None
    return render(
        request,
        "settings_sharing.html",
        **chrome(app, conn, session, view),
        section="settings",
        tab="sharing",
        rows=[(facet, DESCRIPTIONS[facet], facet in shared) for facet in Facet],
        preview=view.preview() if view.books is not None else None,
        resets=resets,
        just_saved=just_saved,
        descriptions=DESCRIPTIONS,
        month_name=f"{app.clock().astimezone():%B}",
    )


@router.post("/{facet}")
def choose(
    app: App,
    conn: Conn,
    session: Acting,
    facet: str,
    shared: Annotated[bool, Form()],
) -> RedirectResponse:
    try:
        chosen = Facet(facet)
    except ValueError:
        raise Concealed from None
    sharing.choose(conn, session.member.id, chosen, shared=shared, now=app.clock())
    return RedirectResponse(f"/settings/sharing?saved={chosen.value}", status_code=303)


connections_router = APIRouter(prefix="/settings/connections")


@connections_router.get("")
def connections_page(
    request: Request,
    app: App,
    conn: Conn,
    session: SignedIn,
    view: Viewing,
    outcome: str | None = None,
    code: str | None = None,
) -> HTMLResponse:
    message, plaid_code = connect.message_for(outcome, code)
    return render(
        request,
        "settings_connections.html",
        **chrome(app, conn, session, view),
        section="settings",
        tab="connections",
        connect=connect.connector(app, conn, view).overview(),
        message=message,
        plaid_code=plaid_code,
        plaid_request=connect.plaid_request(request, message),
        is_problem=connect.is_problem(outcome),
    )
