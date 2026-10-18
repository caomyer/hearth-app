"""A member's own sharing choices. Nobody can change anyone else's."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from hearth import sharing
from hearth.disclosure import Concealed
from hearth.sharing import DESCRIPTIONS, Facet
from hearth.web.deps import Acting, App, Conn, Session, SignedIn
from hearth.web.templating import render

router = APIRouter()


def _rows(conn: Conn, session: Session) -> list[tuple[Facet, bool]]:
    shared = sharing.load(conn)[session.member.id]
    return [(facet, facet in shared) for facet in Facet]


@router.get("/settings/sharing")
def sharing_page(request: Request, conn: Conn, session: SignedIn) -> HTMLResponse:
    return render(
        request,
        "sharing.html",
        session=session,
        rows=_rows(conn, session),
        descriptions=DESCRIPTIONS,
    )


@router.post("/settings/sharing/{facet}")
def choose(
    request: Request,
    app: App,
    conn: Conn,
    session: Acting,
    facet: str,
    shared: Annotated[bool, Form()],
) -> Response:
    try:
        chosen = Facet(facet)
    except ValueError:
        raise Concealed from None
    sharing.choose(conn, session.member.id, chosen, shared=shared, now=app.clock())
    if request.headers.get("HX-Request") != "true":
        return RedirectResponse("/settings/sharing", status_code=303)
    return render(
        request,
        "_sharing_row.html",
        session=session,
        facet=chosen,
        shared=shared,
        description=DESCRIPTIONS[chosen],
    )
