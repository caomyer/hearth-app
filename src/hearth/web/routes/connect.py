"""Connecting a member's institutions: their Plaid account, Link, mapping and the import.

Every page and action here works through the viewer's `Connector`, which ``Disclosure``
builds with the viewer's gate, so a member reaches only their own connections and an Item
held by anyone else answers exactly like one that does not exist.

No step makes a live Plaid call unless this run of hearth was started with that operation
approved (``hearth.plaid.approval``). A step that would need one says so instead, and a
member whose Plaid account has no connection left is told before Link ever opens.
"""

import re
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from hearth.plaid.client import Environment
from hearth.plaid.connector import (
    NEW,
    Connector,
    Held,
    Hold,
    LinkStart,
    Mapping,
    Refusal,
    Refused,
)
from hearth.plaid.mapping import Problem
from hearth.plaid.setup import DAYS_REQUESTED
from hearth.vault.secret import Secret
from hearth.web.deps import Acting, App, Conn, Session, SignedIn, Viewing
from hearth.web.routes.pages import chrome
from hearth.web.templating import render

router = APIRouter(prefix="/connect")

LINK_SCRIPT = "https://cdn.plaid.com/link/v2/stable/link-initialize.js"
LINK_POLICY = (
    # Plaid Link is Plaid's own script in Plaid's own frame; this page alone may load them.
    "default-src 'none'; script-src 'self' https://cdn.plaid.com/link/v2/stable/;"
    " frame-src https://cdn.plaid.com/; connect-src 'self' https://production.plaid.com"
    " https://sandbox.plaid.com; style-src 'self'; style-src-attr 'unsafe-inline';"
    " img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)

OUTCOMES = {
    "saved": "Plaid account saved. Its keys are encrypted on this machine and never shown again.",
    "removed-account": "Plaid account forgotten.",
    "household-chosen": "Your connections will go through the household's Plaid account.",
    "household-unchosen": "You will not link through the household's Plaid account.",
    "connected": "Connected. Its history is coming in now.",
    "reconnected": "Signed in again. What happened since is coming in now.",
    "syncing": "Bringing it up to date now.",
    "removed": "Connection removed at Plaid. Its slot is free again, and every transaction it"
    " brought in is kept. To connect that bank again, connect it now.",
    "statement": "Statement kept for the import. It fills the history the bank did not send.",
    "link-closed": "Plaid closed without connecting anything. Nothing was used.",
    "partly-confirmed": "Saved. Only confirmed accounts are imported; confirm the rest when"
    " you are sure where they go.",
    Refusal.NO_PLAID_ACCOUNT.value: "Choose a Plaid account to connect through first.",
    Refusal.NO_SLOT.value: "This Plaid account has no connections left, so nothing was opened.",
    Refusal.NOT_APPROVED.value: "That needs a live call to Plaid, which this hearth is not"
    " approved to make. Nothing was sent.",
    Refusal.PLAID_FAILED.value: "Plaid did not complete that.",
    Refusal.CREDENTIALS_INCOMPLETE.value: "Enter both the client ID and the secret.",
    Refusal.CREDENTIALS_IN_USE.value: "This Plaid account still has connections, so its client"
    " id and environment cannot change. Remove them first: only its own keys can remove them,"
    " and each one holds a slot until it is.",
    Refusal.NOT_UNDERSTOOD.value: "That could not be used. Check the form and try again.",
    Refusal.TARGET_TAKEN.value: "That ledger account already receives transactions from another"
    " connection. One account can be fed by one connection, or every transaction would be"
    " imported twice.",
    Refusal.STATEMENT_UNREADABLE.value: "That file could not be kept. Upload a CSV statement of"
    " up to 5 MB.",
    Hold.PROBLEMS.value: "Nothing was saved yet. Put the marked accounts right, then start the"
    " import again.",
    Hold.NEAR.value: "Nothing was saved yet. What you chose puts two targets one word apart."
    " Check them against the bank's balances, then confirm them again.",
}
"""Fixed messages for what a step did; the query names one, it never carries text."""

PROBLEMS = {
    Problem.MISSING: "Name the new ledger account.",
    Problem.MALFORMED: "Use names joined by colons, each starting with a capital letter or a"
    " digit, then only letters, digits and hyphens.",
    Problem.UNDER_ASSETS: "A bank or investment account goes under Assets.",
    Problem.UNDER_LIABILITIES: "A card or loan goes under Liabilities.",
    Problem.IN_USE: "That name is not available: the books already use it, or a name inside or"
    " above it. Choose another, or pick one of your accounts from the list.",
    Problem.TWICE: "Another account on this page goes here too, or inside or above it. Each"
    " needs its own.",
    Problem.NAME_TOO_LONG: "Keep the name to 80 characters or fewer.",
    Problem.NAME_NOT_NEW: "A name is given only to a new ledger account; one already in your"
    " books keeps its own. Clear it, or choose New ledger account.",
}
"""Fixed messages for what is wrong with one row of the mapping form."""


def is_problem(outcome: str | None) -> bool:
    return outcome in {r.value for r in Refusal} | {h.value for h in Hold}


_PLAID_CODE = re.compile(r"[A-Z][A-Z0-9_]{2,63}")


def connector(app: App, conn: Conn, view: Viewing) -> Connector:
    return view.connector(
        conn,
        database=app.settings.database,
        feed_path=app.settings.feed_database,
        data_dir=app.settings.data_dir,
        setup=app.plaid,
        clock=app.clock,
        runner=app.runner,
    )


def _back(to: str, outcome: str, code: str | None = None) -> RedirectResponse:
    query = f"outcome={outcome}" + (f"&code={code}" if code else "")
    return RedirectResponse(f"{to}?{query}", status_code=303)


def _refused(to: str, refused: Refused) -> RedirectResponse:
    return _back(to, refused.reason.value, refused.plaid_code)


def message_for(outcome: str | None, code: str | None) -> tuple[str | None, str | None]:
    message = OUTCOMES.get(outcome or "")
    plaid_code = code if message and code and _PLAID_CODE.fullmatch(code) else None
    return message, plaid_code


def _page(
    request: Request,
    app: App,
    conn: Conn,
    session: Session,
    view: Viewing,
    template: str,
    outcome: str | None,
    code: str | None,
    *,
    status_code: int = 200,
    **context: Any,
) -> HTMLResponse:
    message, plaid_code = message_for(outcome, code)
    return render(
        request,
        template,
        status_code=status_code,
        **chrome(app, conn, session, view),
        focus=True,
        message=message,
        plaid_code=plaid_code,
        is_problem=is_problem(outcome),
        **context,
    )


@router.get("")
def connect_page(
    request: Request,
    app: App,
    conn: Conn,
    session: SignedIn,
    view: Viewing,
    outcome: str | None = None,
    code: str | None = None,
) -> HTMLResponse:
    mine = connector(app, conn, view)
    return _page(
        request,
        app,
        conn,
        session,
        view,
        "connect.html",
        outcome,
        code,
        step=2,
        connect=mine.overview(),
        environments=app.plaid.environments,
    )


@router.post("/credentials")
async def save_credentials(
    request: Request, app: App, conn: Conn, session: Acting, view: Viewing
) -> RedirectResponse:
    """Save a Plaid account's keys. They are read from the form straight into `Secret`, never
    taken as parameters, so no frame of this request holds them in the clear afterwards."""
    keys = await _keys(request)
    to = _return_to(keys.back)
    try:
        connector(app, conn, view).save_credentials(
            household=keys.scope == "household",
            client_id=keys.client_id,
            secret=keys.secret,
            environment=keys.environment,
            item_limit=keys.item_limit,
            used_elsewhere=keys.used_elsewhere,
        )
    except Refused as refused:
        return _refused(to, refused)
    return _back(to, "saved")


@dataclass(frozen=True, slots=True)
class _Keys:
    client_id: Secret | None
    secret: Secret | None
    environment: str
    item_limit: str
    used_elsewhere: str
    scope: str
    back: str


async def _keys(request: Request) -> _Keys:
    form = await request.form()

    def secret(name: str) -> Secret | None:
        value = form.get(name)
        return Secret(value.strip()) if isinstance(value, str) and value.strip() else None

    def text(name: str, default: str) -> str:
        value = form.get(name)
        return value.strip() if isinstance(value, str) else default

    return _Keys(
        client_id=secret("client_id"),
        secret=secret("secret"),
        environment=text("environment", Environment.PRODUCTION.value),
        item_limit=text("item_limit", "10"),
        used_elsewhere=text("used_elsewhere", "0"),
        scope=text("scope", "own"),
        back=text("back", "connect"),
    )


@router.post("/credentials/remove")
def remove_credentials(
    app: App,
    conn: Conn,
    session: Acting,
    view: Viewing,
    scope: Annotated[str, Form()] = "own",
    back: Annotated[str, Form()] = "connect",
) -> RedirectResponse:
    to = _return_to(back)
    try:
        connector(app, conn, view).remove_credentials(household=scope == "household")
    except Refused as refused:
        return _refused(to, refused)
    return _back(to, "removed-account")


@router.post("/household")
def choose_household(
    app: App,
    conn: Conn,
    session: Acting,
    view: Viewing,
    chosen: Annotated[bool, Form()],
    back: Annotated[str, Form()] = "connect",
) -> RedirectResponse:
    connector(app, conn, view).choose_household(chosen)
    return _back(_return_to(back), "household-chosen" if chosen else "household-unchosen")


@router.post("/link")
def open_link(
    request: Request,
    app: App,
    conn: Conn,
    session: Acting,
    view: Viewing,
    item_id: Annotated[str, Form()] = "",
) -> Response:
    """Open Plaid Link: for a new connection, or to sign an existing one in again."""
    mine = connector(app, conn, view)
    try:
        start = mine.start_reconnect(item_id) if item_id else mine.start_link()
    except Refused as refused:
        return _refused("/connect", refused)
    return _link_page(request, app, conn, session, view, start)


def _link_page(
    request: Request, app: App, conn: Conn, session: Session, view: Viewing, start: LinkStart
) -> HTMLResponse:
    response = render(
        request,
        "connect_link.html",
        **chrome(app, conn, session, view),
        focus=True,
        step=2,
        start=start,
        link_script=LINK_SCRIPT,
        days=DAYS_REQUESTED,
    )
    if not start.stand_in:
        response.headers["Content-Security-Policy"] = LINK_POLICY
    return response


@router.post("/exchange")
async def exchange(
    request: Request, app: App, conn: Conn, session: Acting, view: Viewing
) -> RedirectResponse:
    """What Link returns on success. Exchanging it creates the Item that holds a slot."""
    public_token, institution = await _link_result(request)
    if public_token is None:
        return _back("/connect", "link-closed")
    try:
        connector(app, conn, view).finish_link(public_token, institution)
    except Refused as refused:
        return _refused("/connect", refused)
    return _back("/connect", "connected")


@router.post("/reconnected")
async def reconnected(
    request: Request, app: App, conn: Conn, session: Acting, view: Viewing
) -> RedirectResponse:
    public_token, _ = await _link_result(request)
    item_id = (await request.form()).get("item_id")
    connector(app, conn, view).finish_reconnect(
        item_id if isinstance(item_id, str) else "", public_token
    )
    return _back("/connect", "reconnected")


async def _link_result(request: Request) -> tuple[Secret | None, str]:
    """Link's one-time public token, read straight into a `Secret`, and the bank's name."""
    form = await request.form()

    def text(name: str) -> str:
        value = form.get(name)
        return value.strip() if isinstance(value, str) else ""

    return (Secret(text("public_token")) if text("public_token") else None), text("institution")


@router.post("/items/{item_id}/sync")
def sync(app: App, conn: Conn, session: Acting, view: Viewing, item_id: str) -> RedirectResponse:
    try:
        connector(app, conn, view).resync(item_id)
    except Refused as refused:
        return _refused("/settings/connections", refused)
    return _back("/settings/connections", "syncing")


@router.get("/items/{item_id}/remove")
def remove_page(
    request: Request,
    item_id: str,
    app: App,
    conn: Conn,
    session: SignedIn,
    view: Viewing,
) -> HTMLResponse:
    mine = connector(app, conn, view)
    return render(
        request,
        "connect_remove.html",
        **chrome(app, conn, session, view),
        section="settings",
        item=mine.item(item_id),
        connect=mine.overview(),
    )


@router.post("/items/{item_id}/remove")
def remove(app: App, conn: Conn, session: Acting, view: Viewing, item_id: str) -> RedirectResponse:
    try:
        connector(app, conn, view).remove(item_id)
    except Refused as refused:
        return _refused("/settings/connections", refused)
    # Which connection stands for an account reached twice may have changed (booking).
    app.bookkeeper.recompute_in_background()
    return _back("/settings/connections", "removed")


@router.post("/accounts/{account_id}/statements")
async def upload_statement(
    app: App,
    conn: Conn,
    session: Acting,
    view: Viewing,
    account_id: str,
    statement: Annotated[UploadFile, File()],
) -> RedirectResponse:
    data = await statement.read(5 * 1024 * 1024 + 1)
    try:
        connector(app, conn, view).upload_statement(account_id, statement.filename or "", data)
    except Refused as refused:
        return _refused("/connect", refused)
    return _back("/connect", "statement")


@router.get("/map")
def map_page(
    request: Request,
    app: App,
    conn: Conn,
    session: SignedIn,
    view: Viewing,
    outcome: str | None = None,
    code: str | None = None,
) -> HTMLResponse:
    mine = connector(app, conn, view)
    return _page(
        request,
        app,
        conn,
        session,
        view,
        "connect_map.html",
        outcome,
        code,
        step=3,
        mapping=mine.mapping(),
        new=NEW,
        problems=PROBLEMS,
    )


@router.post("/map")
async def confirm(
    request: Request, app: App, conn: Conn, session: Acting, view: Viewing
) -> Response:
    form = await request.form()
    ids = [str(v) for v in form.getlist("account")]
    submitted = [
        Mapping(
            account_id=account_id,
            target=str(form.get(f"target-{account_id}", "")),
            owners=str(form.get(f"owners-{account_id}", "")),
            confirm=form.get(f"confirm-{account_id}") == "1",
            path=str(form.get(f"path-{account_id}", "")),
            name=str(form.get(f"name-{account_id}", "")),
        )
        for account_id in ids
    ]
    # The pairs a word apart that the page named, each sent as "one other".
    near_seen = [
        (one, other) for v in form.getlist("near") for one, _, other in [str(v).partition(" ")]
    ]
    mine = connector(app, conn, view)
    try:
        mine.confirm(submitted, near_seen=near_seen)
    except Refused as refused:
        return _refused("/connect/map", refused)
    except Held as held:
        # Shown again as it was sent, so nothing the member typed is lost.
        return _page(
            request,
            app,
            conn,
            session,
            view,
            "connect_map.html",
            held.reason.value,
            None,
            status_code=400,
            step=3,
            mapping=held.view,
            new=NEW,
            problems=PROBLEMS,
        )
    # Each mapped account is an account in hearth's own books, so they are recomputed.
    app.bookkeeper.recompute_in_background()
    mapping = mine.mapping()
    if mapping.rows and mapping.confirmed == len(mapping.rows):
        return RedirectResponse("/connect/import", status_code=303)
    return _back("/connect/map", "partly-confirmed")


@router.get("/import")
def import_page(
    request: Request,
    app: App,
    conn: Conn,
    session: SignedIn,
    view: Viewing,
) -> HTMLResponse:
    mine = connector(app, conn, view)
    return _page(
        request,
        app,
        conn,
        session,
        view,
        "connect_import.html",
        None,
        None,
        step=4,
        imported=mine.import_view(),
    )


def _return_to(back: str) -> str:
    """Where a settings form returns: one of two fixed pages, never a URL from the form."""
    return "/settings/connections" if back == "settings" else "/connect"
