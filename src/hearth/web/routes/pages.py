"""The pages that show household data. Each one renders a view from ``Disclosure``.

Every figure page goes through ``_figures``, which renders the books' state instead of the
page while there are no books to show: a skeleton while the first recompute runs, and what
went wrong if it failed.
"""

import sqlite3
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from hearth import audit
from hearth.disclosure import Disclosure
from hearth.review.desk import State
from hearth.review.rulings import RulingStore
from hearth.web import presenters
from hearth.web.deps import Acting, App, Conn, SelectedMonth, Session, SignedIn, Viewing
from hearth.web.templating import render

router = APIRouter()


def chrome(
    app: App, conn: sqlite3.Connection, session: Session, view: Disclosure
) -> dict[str, Any]:
    """What every signed-in page shows around its content: the review badge and the
    viewer's own colour, and why today's figures are missing when they are."""
    books = view.books
    waiting = 0
    if books is not None:
        desk = view.review_desk(RulingStore(conn), app.clock)
        waiting = sum(group.pending for group in desk.groups())
    return {
        "session": session,
        "review_count": waiting,
        "viewer_hue": presenters.hue_of(view, session.member),
        "notice": view.notice(),
        "expired": [c for c in view.connections(app.connections) if c.state == "expired"],
        "lag": view.lag(app.connections),
        "status": view.status,
        "today": app.clock().astimezone().date(),
    }


def _figures(
    request: Request,
    app: App,
    conn: sqlite3.Connection,
    session: Session,
    view: Disclosure,
    section: str,
    template: str,
    build: Callable[[], dict[str, Any]],
) -> HTMLResponse:
    page = chrome(app, conn, session, view) | {"section": section}
    if view.books is None:
        state = "loading.html" if view.status.failure is None else "no_books.html"
        return render(request, state, **page)
    return render(request, template, **page, **build())


@router.get("/")
def household(
    request: Request, app: App, conn: Conn, session: SignedIn, view: Viewing
) -> HTMLResponse:
    def build() -> dict[str, Any]:
        household = view.household()
        return {"household": household, **presenters.household_page(household)}

    return _figures(request, app, conn, session, view, "household", "household.html", build)


@router.get("/you")
def you(request: Request, app: App, conn: Conn, session: SignedIn, view: Viewing) -> HTMLResponse:
    def build() -> dict[str, Any]:
        me = view.you()
        books = view.books
        assert books is not None  # noqa: S101 - _figures calls build only when there are books
        desk = view.review_desk(RulingStore(conn), app.clock)
        return {
            "view": me,
            "groups": [g for g in desk.groups() if g.pending],
            **presenters.person_page(me),
        }

    return _figures(request, app, conn, session, view, "you", "you.html", build)


@router.get("/members/{username}")
def member(
    request: Request, username: str, app: App, conn: Conn, session: SignedIn, view: Viewing
) -> Response:
    if username == session.member.username:
        return RedirectResponse("/you", status_code=303)

    def build() -> dict[str, Any]:
        person = view.person(username)
        return {"view": person, **presenters.person_page(person)}

    return _figures(request, app, conn, session, view, "household", "member.html", build)


@router.get("/accounts/{account_id}")
def account(
    request: Request,
    account_id: str,
    app: App,
    conn: Conn,
    session: SignedIn,
    view: Viewing,
    month: SelectedMonth,
) -> HTMLResponse:
    def build() -> dict[str, Any]:
        return {"view": view.account(account_id, month)}

    return _figures(request, app, conn, session, view, "household", "account.html", build)


OUTCOMES = {
    "price-recorded": "Price recorded as entered by you. Net worth is recomputing with it.",
    "price-not-understood": "That price could not be used. Enter a positive number for a past day.",
    "source-unavailable": "The price source is not set up yet, so it could not be asked again.",
    "source-asked": "The price source was asked again. Net worth is recomputing with what it sent.",
}
"""Fixed messages for what a price action did; the query names one, it never carries text."""


@router.get("/net-worth")
def net_worth(
    request: Request,
    app: App,
    conn: Conn,
    session: SignedIn,
    view: Viewing,
    outcome: str | None = None,
) -> HTMLResponse:
    def build() -> dict[str, Any]:
        worth = view.net_worth()
        today = app.clock().astimezone().date()
        message = OUTCOMES.get(outcome or "")
        return {"view": worth, "message": message, **presenters.net_worth_page(worth, today)}

    return _figures(request, app, conn, session, view, "net-worth", "net_worth.html", build)


@router.post("/net-worth/recompute")
def recompute(app: App, session: Acting) -> RedirectResponse:
    """Recompute on request, in the background; the page shows the old figures meanwhile."""
    app.bookkeeper.recompute_in_background()
    return RedirectResponse("/net-worth", status_code=303)


@router.get("/activity")
def activity(
    request: Request, app: App, conn: Conn, session: SignedIn, view: Viewing
) -> HTMLResponse:
    lines = view.activity(audit.all_events(conn))
    return render(
        request,
        "activity.html",
        **chrome(app, conn, session, view),
        section="settings",
        lines=lines,
    )


REVIEW_OUTCOMES = {
    "not-an-account": "That row was not booked. Enter a full account name, like"
    " Expenses:Groceries.",
}
"""Fixed messages for what a review action did; the query names one, it never carries text."""


@router.get("/review")
def review(
    request: Request,
    app: App,
    conn: Conn,
    session: SignedIn,
    view: Viewing,
    outcome: str | None = None,
) -> HTMLResponse:
    def build() -> dict[str, Any]:
        books = view.books
        assert books is not None  # noqa: S101 - _figures calls build only when there are books
        desk = view.review_desk(RulingStore(conn), app.clock)
        groups = desk.groups()
        mine = [item for g in groups for item in g.items]
        return {
            "message": REVIEW_OUTCOMES.get(outcome or ""),
            # Suggest only categories from the viewer's own pile: nobody else's are named.
            "categories": sorted({i.item.proposed for i in mine if i.item.proposed}),
            "groups": [g for g in groups if g.pending],
            "decided": [
                i for g in groups for i in g.items if i.decision.state is not State.PENDING
            ],
        }

    return _figures(request, app, conn, session, view, "review", "review.html", build)


@router.post("/review/groups/accept")
def accept_group(
    app: App,
    conn: Conn,
    session: Acting,
    view: Viewing,
    group: Annotated[str, Form()],
) -> RedirectResponse:
    books = view.books
    if books is not None:
        view.review_desk(RulingStore(conn), app.clock).accept_group(group)
    return RedirectResponse("/review", status_code=303)


@router.post("/review/rows/book")
def book_row(
    app: App,
    conn: Conn,
    session: Acting,
    view: Viewing,
    review_id: Annotated[str, Form()],
    account: Annotated[str, Form()],
) -> RedirectResponse:
    desk = view.review_desk(RulingStore(conn), app.clock)
    try:
        desk.set_account(review_id, account.strip())
    except ValueError:
        return RedirectResponse("/review?outcome=not-an-account", status_code=303)
    return RedirectResponse("/review", status_code=303)


@router.post("/review/rows/reject")
def reject_row(
    app: App, conn: Conn, session: Acting, view: Viewing, review_id: Annotated[str, Form()]
) -> RedirectResponse:
    view.review_desk(RulingStore(conn), app.clock).reject(review_id)
    return RedirectResponse("/review", status_code=303)
