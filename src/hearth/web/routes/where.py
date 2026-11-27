"""Where did it go? The rows a member's banks sent whose other side hearth does not see.

A transfer to savings at a bank nobody connected, a loan payment, a card paid from an account
hearth has never seen: each is answered once for every row like it, by naming the account the
money went to or came from, or the category it was for (``hearth.review.where``). A group whose
rows are not all one answer is answered one by one, each row on its own. Naming a new account
takes its own page, which saves the account and the answer together.

Every row and account here comes from the viewer's own desk (``Disclosure.review_desk``), so
nothing on these pages describes anyone else's. A group, row or account that is not the
viewer's answers exactly like one that does not exist, and every answer is bound to the rows
the page showed, as each said then.
"""

from collections.abc import Mapping, Sequence
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from hearth.disclosure import Concealed, Disclosure
from hearth.disclosure.naming import Checked, Draft, NotTheirs, Wrong
from hearth.plaid.mapping import Problem
from hearth.review.desk import ItemView, ReviewDesk
from hearth.review.desk import NotTheirs as NotTheirsRow
from hearth.review.queue import category_label
from hearth.review.rulings import Action, Ruling, RulingStore
from hearth.review.where import (
    NEW,
    NEW_KIND,
    Answer,
    Question,
    WhereGroup,
    WherePage,
    alike,
    answers,
    settled,
    where,
)
from hearth.web.deps import Acting, App, Conn, SignedIn, Viewing
from hearth.web.routes.pages import chrome, figures_page
from hearth.web.templating import render

router = APIRouter(prefix="/review/where")

OUTCOMES = {
    "moved": "Saved. They moved between your accounts, so they are neither spending nor income.",
    "booked": "Saved under the category you chose.",
    "named": "Account added. It is listed with your accounts, and left out of your net worth.",
    "changed": "Some of those changed since the page showed them, so they were left waiting."
    " Here they are as they are now.",
    "not-yours": "That was not saved. Choose one of your own accounts, or a category.",
    "undone": "Taken back. Those transactions are waiting for an answer again.",
}
"""Fixed messages for what an answer did; the query names one, it never carries text."""

ONE_OUTCOMES = OUTCOMES | {
    "moved": "Saved. It moved between your accounts, so it is neither spending nor income.",
    "changed": "That one changed since the page showed it, so it was left waiting."
    " Here it is as it is now.",
    "undone": "Taken back. That transaction is waiting for an answer again.",
    "named-waiting": "Account added. That transaction changed since the page showed it, so it"
    " was left waiting.",
}
"""The same, said of the one row a member answered on its own."""

UNDOABLE = frozenset({"moved", "booked", "changed"})


def _desk(app: App, conn: Conn, view: Disclosure) -> ReviewDesk:
    return view.review_desk(RulingStore(conn), app.clock)


def _page(desk: ReviewDesk) -> tuple[WherePage, list[ItemView]]:
    items = desk.items()
    return where(items, desk.learned(alike)), items


@router.get("")
def where_page(
    request: Request,
    app: App,
    conn: Conn,
    session: SignedIn,
    view: Viewing,
    outcome: str | None = None,
    undo: str | None = None,
    focus: str | None = None,
    n: str | None = None,
) -> HTMLResponse:
    def build() -> dict[str, Any]:
        desk = _desk(app, conn, view)
        page, items = _page(desk)
        return {
            "page": page,
            "answers": answers(desk, page, items),
            "settled": settled(desk, items)[:5],
            "matched": view.matched(),
            **_told(desk, outcome, undo, n),
            "landing": focus if focus and focus.isalnum() and len(focus) <= 16 else None,
            "category_label": category_label,
            "new": NEW,
            "questions": Question,
        }

    return figures_page(request, app, conn, session, view, "review", "where.html", build)


def _told(
    desk: ReviewDesk, outcome: str | None, ruling_id: str | None, n: str | None
) -> dict[str, Any]:
    """What an answer did, worded for the rows it speaks of: one, or more. Those are the rows
    its ruling covers on the member's desk; where it saved nothing, or speaks of the rows left
    waiting, which its ruling does not count, they are the `n` the redirect carries."""
    found = None
    if ruling_id and ruling_id.isdigit() and len(ruling_id) <= 18:
        found = desk.ruling(int(ruling_id))
    counted = found is not None and found[1] and outcome != "changed"
    alone = found[1] == 1 if found is not None and counted else n == "1"
    undoable = found is not None and found[1] and found[0].action is not Action.REOPEN
    return {
        "message": (ONE_OUTCOMES if alone else OUTCOMES).get(outcome or ""),
        "undo": found if undoable and outcome in UNDOABLE else None,
    }


def _to(
    outcome: str, ruling: Ruling | None = None, focus: str = "", n: int = 0
) -> RedirectResponse:
    """To the page of groups; `n` is how many rows the message speaks of where no ruling says."""
    params = {"outcome": outcome}
    if ruling is not None:
        params["undo"] = str(ruling.id)
    if n:
        params["n"] = str(n)
    anchor = ""
    if focus.isalnum() and len(focus) <= 16:
        params["focus"] = focus
        anchor = f"#w-{focus}"
    return RedirectResponse(f"/review/where?{urlencode(params)}{anchor}", status_code=303)


def _shown(rows: Sequence[str]) -> dict[str, str]:
    """What the page showed: each row's review id with what it said then."""
    return dict(row.split(" ", 1) for row in rows[:2000] if " " in row)


@router.post("/move")
def move(
    app: App,
    conn: Conn,
    session: Acting,
    view: Viewing,
    group: Annotated[str, Form()],
    title: Annotated[str, Form()],
    rows: Annotated[list[str], Form()],
    answer: Annotated[str, Form()] = "account",
    account: Annotated[str, Form()] = "",
    category: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Answer one group: every row the page showed goes to one of the member's own accounts,
    or a category. Choosing to name a new account goes to the page that names it."""
    desk = _desk(app, conn, view)
    shown = _shown(rows)
    if answer == "account" and account == NEW:
        return RedirectResponse(f"/review/where/account?{urlencode({'group': group})}", 303)
    if answer == "account":
        found = desk.own(account, unseen=True)
        target = found.ledger_account if found else ""
    else:
        target = category.strip()
    if not target:
        return _to("not-yours", focus=group)
    try:
        moved = desk.move_group(title.strip()[:200] or "Where did it go?", target, shown)
    except NotTheirsRow, ValueError:
        return _to("not-yours", focus=group)
    if moved.accepted:
        app.bookkeeper.recompute_in_background()
    outcome = "changed" if moved.changed else "booked" if answer != "account" else "moved"
    return _to(outcome, moved.ruling, n=moved.changed)


@router.get("/one")
def one_page(
    request: Request,
    app: App,
    conn: Conn,
    session: SignedIn,
    view: Viewing,
    group: str = "",
    outcome: str | None = None,
    undo: str | None = None,
    n: str | None = None,
) -> HTMLResponse:
    """One group's rows one by one, each with an answer of its own."""

    def build() -> dict[str, Any]:
        desk = _desk(app, conn, view)
        page, items = _page(desk)
        answer = _answer(desk, page, items, group)
        if answer is None:
            raise Concealed
        return {
            "a": answer,
            **_told(desk, outcome, undo, n),
            "category_label": category_label,
            "new": NEW,
        }

    return figures_page(request, app, conn, session, view, "review", "where_one.html", build)


def _answer(
    desk: ReviewDesk, page: WherePage, items: Sequence[ItemView], key: str
) -> Answer | None:
    return next((a for a in answers(desk, page, items) if a.group.key == key[:16]), None)


def _back(desk: ReviewDesk, key: str, outcome: str, ruling: Ruling | None) -> RedirectResponse:
    """Back to the group's rows one by one while any wait, else to the page of groups."""
    page, _ = _page(desk)
    if page.group(key[:16]) is None:
        return _to(outcome, ruling, n=1)
    params = {"group": key[:16], "outcome": outcome, "n": "1"}
    if ruling is not None:
        params["undo"] = str(ruling.id)
    return RedirectResponse(f"/review/where/one?{urlencode(params)}", status_code=303)


@router.post("/row")
def answer_row(
    app: App,
    conn: Conn,
    session: Acting,
    view: Viewing,
    group: Annotated[str, Form()],
    row: Annotated[str, Form()],
    to: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Answer one row on its own: one of the member's own accounts hearth does not see, or a
    category, bound to what the row said when the page showed it."""
    desk = _desk(app, conn, view)
    shown = _shown([row])
    if len(shown) != 1:
        raise Concealed
    (review_id,) = shown
    desk.item(review_id)
    if to == NEW:
        query = urlencode({"group": group[:16], "row": review_id})
        return RedirectResponse(f"/review/where/account?{query}", status_code=303)
    kind, _, value = to.partition(":")
    if kind == "account":
        found = desk.own(value, unseen=True)
        target = found.ledger_account if found else ""
    else:
        target = value.strip() if kind == "category" else ""
    if not target:
        return _back(desk, group, "not-yours", None)
    try:
        ruling = _set_one(desk, shown, target)
    except NotTheirsRow, ValueError:
        return _back(desk, group, "not-yours", None)
    if ruling is None:
        return _back(desk, group, "changed", None)
    app.bookkeeper.recompute_in_background()
    return _back(desk, group, "moved" if kind == "account" else "booked", ruling)


def _set_one(desk: ReviewDesk, shown: Mapping[str, str], target: str) -> Ruling | None:
    """One row's own ruling onto `target`, while it still waits to be told where it went and
    says what the page showed; `None` when it changed since."""
    ((review_id, says),) = shown.items()
    return desk.answer_shown(review_id, says, target)


@router.post("/undo")
def undo(
    app: App,
    conn: Conn,
    session: Acting,
    view: Viewing,
    ruling: Annotated[int, Form()],
    group: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Take back one of the viewer's own answers: every row it covered waits again. One taken
    back from a group's rows one by one returns to them."""
    desk = _desk(app, conn, view)
    reopened = desk.undo(ruling)
    app.bookkeeper.recompute_in_background()
    return _back(desk, group, "undone", reopened) if group else _to("undone", reopened)


@router.get("/account")
def name_page(
    request: Request,
    app: App,
    conn: Conn,
    session: SignedIn,
    view: Viewing,
    group: str = "",
    row: str = "",
) -> HTMLResponse:
    """The form that names an account hearth does not see, from the group it answers, or the
    one row of it when the group is answered one by one."""

    def build() -> dict[str, Any]:
        desk = _desk(app, conn, view)
        page, _ = _page(desk)
        found = page.group(group[:16])
        shown = dict(found.shown()) if found else {}
        if row:
            shown = {row: shown[row]} if row in shown else {}
        if found and not shown:
            raise Concealed
        draft = Draft(kind=NEW_KIND[found.question], mask=found.mask or "") if found else Draft()
        checked = view.namer(conn).check(draft)
        return _name_context(
            view, conn, found, shown, checked, fresh=True, auto=True, one=bool(row)
        )

    return figures_page(request, app, conn, session, view, "review", "name_account.html", build)


def _name_context(
    view: Disclosure,
    conn: Conn,
    group: WhereGroup | None,
    shown: Mapping[str, str],
    checked: Checked,
    *,
    fresh: bool,
    auto: bool,
    one: bool,
) -> dict[str, Any]:
    namer = view.namer(conn)
    you = view.you() if view.books is not None else None
    choices = namer.owner_choices()
    return {
        "focus": True,
        "group": group,
        "shown": shown,
        "one": group is not None and one,
        "checked": checked,
        "fresh": fresh,
        "auto": auto,
        "kinds": namer.kinds(),
        "owner_choices": choices,
        "alone": {c.key for c in choices if len(c.owners) == 1},
        "you": you,
        "problems": PROBLEMS,
        "wrongs": WRONGS,
    }


PROBLEMS = {
    Problem.MISSING: "Give it a ledger account name, or leave the details above to propose one.",
    Problem.MALFORMED: "A ledger account name is words of letters and digits, each starting"
    " with a capital, joined by colons.",
    Problem.UNDER_ASSETS: "This kind of account goes under Assets.",
    Problem.UNDER_LIABILITIES: "This kind of account goes under Liabilities.",
    Problem.IN_USE: "That name is not available. Choose another.",
}
"""What a ledger name refused says. One sentence for every account a name could collide with,
whoever owns it (``docs/privacy-boundary.md``)."""

WRONGS = {
    Wrong.NAME: "Give it a name the household will recognise.",
    Wrong.NAME_TOO_LONG: "That name is longer than hearth keeps. Shorten it.",
    Wrong.WHERE_TOO_LONG: "That is longer than hearth keeps. Shorten it.",
    Wrong.MASK: "The last digits are two to four numbers, or leave them out.",
}


@router.post("/account")
def name_account(
    request: Request,
    app: App,
    conn: Conn,
    session: Acting,
    view: Viewing,
    kind: Annotated[str, Form()],
    name: Annotated[str, Form()] = "",
    where_it_is: Annotated[str, Form(alias="where")] = "",
    mask: Annotated[str, Form()] = "",
    owners: Annotated[str, Form()] = "",
    ledger: Annotated[str, Form()] = "",
    auto: Annotated[str, Form()] = "",
    group: Annotated[str, Form()] = "",
    title: Annotated[str, Form()] = "",
    rows: Annotated[list[str] | None, Form()] = None,
    one: Annotated[str, Form()] = "",
) -> Response:
    """Name an account hearth does not see, then move the rows the page showed into it."""
    namer = view.namer(conn)
    # A name the page proposed as the details were typed is proposed again here, beside the
    # member's own accounts, so it is never refused for one of theirs.
    typed = "" if auto == "1" else ledger[:300]
    try:
        draft = Draft.of(kind, where_it_is[:200], name[:200], mask[:20], owners[:200], typed)
    except ValueError:
        raise Concealed from None
    checked = namer.check(draft)
    desk = _desk(app, conn, view)
    shown = _shown(rows or [])
    if not checked.ok:
        page, _ = _page(desk)
        found = page.group(group[:16])
        still = found.shown() if found else {}
        kept = {rid: says for rid, says in shown.items() if rid in still}
        context = _name_context(
            view, conn, found, kept, checked, fresh=False, auto=not typed.strip(), one=one == "1"
        )
        page_context = chrome(app, conn, session, view) | {"section": "review"}
        return render(request, "name_account.html", status_code=400, **page_context, **context)
    try:
        account = namer.name(checked, now=app.clock())
    except NotTheirs:
        raise Concealed from None
    if not shown:
        app.bookkeeper.recompute_in_background()
        return _to("named")
    # The desk that knows the new account, so the rows can move into it straight away.
    desk = _desk(app, conn, Disclosure.load(conn, session.member, view.status))
    if one == "1" and len(shown) == 1:
        outcome = "named"
        try:
            ruling = _set_one(desk, shown, account.ledger_account)
            outcome = "moved" if ruling else "named-waiting"
        except Concealed, NotTheirsRow, ValueError:
            ruling = None
        app.bookkeeper.recompute_in_background()
        return _back(desk, group, outcome, ruling)
    try:
        moved = desk.move_group(title.strip()[:200] or account.name, account.ledger_account, shown)
    except Concealed, NotTheirsRow, ValueError:
        app.bookkeeper.recompute_in_background()
        return _to("named")
    app.bookkeeper.recompute_in_background()
    return _to("changed" if moved.changed else "moved", moved.ruling, n=moved.changed)
