"""The review queue: a member's own unreviewed rows, and the rulings they make on them.

Every row here comes from the viewer's own desk (``Disclosure.review_desk``), so nothing on
this page can describe anyone else's pile. A row or group that is not the viewer's answers
exactly like one that does not exist.

Every accept is bound to what the page showed: each form sends back the rows it showed with
the account each proposed, and a row that changed in between is left waiting and said so.
Where the member was in the queue (view, order, filter, page) travels in a form field and is
parsed again on the way back, so a redirect can only ever land on this page.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Annotated, Any
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from hearth.disclosure import Concealed
from hearth.review import cadence
from hearth.review.desk import FREE, NotOneDecision, NotTheirs, ReviewDesk
from hearth.review.queue import NOTHING_SUGGESTED, ORDER_LABELS, Filter, Order, confidence, queue
from hearth.review.rulings import Action, Ruling, RulingStore
from hearth.web.deps import Acting, App, Conn, SignedIn, Viewing
from hearth.web.routes.pages import figures_page

router = APIRouter(prefix="/review")

QUICK_ORDERS = (Order.CONFIDENCE, Order.AMOUNT)
"""The two orders that find real errors fastest, one click away."""

GROUP_PREVIEW = 8
"""Rows a group without one clear suggestion shows open on the grouped view."""

OUTCOMES = {
    "accepted": "Accepted as suggested.",
    "booked": "Saved under the category you chose.",
    "moved": "Moved to the account of yours you chose.",
    "rejected": "Marked as not a transaction. It books nothing.",
    "changed": "Some of those changed since the page showed them, so they were left waiting."
    " Here they are as they are now.",
    "not-an-account": "That row was not booked. Enter a full account name, like"
    " Expenses:Groceries.",
    "not-yours": "That transaction is still waiting. It can go to a spending or income"
    " category, like Expenses:Groceries, or move to one of your own accounts in hearth. An"
    " account hearth does not hold yet, such as a mortgage nobody has connected, cannot take it"
    " yet, so it stays waiting for now. That is not something you did wrong.",
    "reminder-saved": "Reminder saved.",
    "not-one-decision": "That group is not one kind of transaction, so it cannot be accepted"
    " all at once. Its transactions are waiting for a look one by one.",
    "undone": "Taken back. Those transactions are waiting for review again.",
}
"""Fixed messages for what a review action did; the query names one, it never carries text."""

UNDOABLE = frozenset({"accepted", "booked", "moved", "rejected", "changed"})
"""Outcomes that recorded a ruling the member may want to take straight back."""

_ROW_ID = re.compile(r"[A-Za-z0-9-]{1,64}")


@dataclass(frozen=True, slots=True)
class Where:
    """Where the member is in the queue. Anything unrecognised falls back to the default."""

    one_by_one: bool = False
    order: Order = Order.SIZE
    filter: Filter = field(default_factory=Filter)
    page: int = 1

    @classmethod
    def parse(cls, params: Mapping[str, str]) -> Where:
        try:
            order = Order(params.get("order", ""))
        except ValueError:
            order = Order.SIZE
        page = params.get("page", "")
        return cls(
            one_by_one=params.get("view") == "rows",
            order=order,
            filter=Filter(
                account=params.get("account", "")[:64],
                category=params.get("category", "")[:200],
                group=params.get("group", "")[:200],
            ),
            page=int(page) if page.isdigit() and 0 < int(page) < 10_000 else 1,
        )

    def params(self) -> dict[str, str]:
        found = {
            "view": "rows" if self.one_by_one else "",
            "order": "" if self.order is Order.SIZE else self.order.value,
            "account": self.filter.account,
            "category": self.filter.category,
            "group": self.filter.group,
            "page": str(self.page) if self.page > 1 else "",
        }
        return {key: value for key, value in found.items() if value}

    def query(self) -> str:
        return urlencode(self.params())

    def url(self, **changes: Any) -> str:
        """This page, with `changes` made; a new order or filter starts again at page one."""
        if "filter" in changes or "order" in changes or "one_by_one" in changes:
            changes.setdefault("page", 1)
        query = replace(self, **changes).query()
        return f"/review?{query}" if query else "/review"

    def cleared(self) -> str:
        return self.url(filter=Filter())

    def in_group(self, group: str) -> str:
        """One group's rows one by one, keeping the rest of the filter."""
        return self.url(one_by_one=True, filter=replace(self.filter, group=group))


def _back(back: str) -> Where:
    return Where.parse(dict(parse_qsl(back[:2000])))


def _to(
    where: Where, outcome: str, focus: str = "", undo: Ruling | None = None
) -> RedirectResponse:
    params = where.params() | {"outcome": outcome}
    if undo is not None:
        params["undo"] = str(undo.id)
    anchor = ""
    if _ROW_ID.fullmatch(focus):
        params["focus"] = focus
        anchor = f"#r-{focus}"
    return RedirectResponse(f"/review?{urlencode(params)}{anchor}", status_code=303)


@router.get("")
def review(
    request: Request,
    app: App,
    conn: Conn,
    session: SignedIn,
    view: Viewing,
    outcome: str | None = None,
    focus: str | None = None,
    undo: str | None = None,
) -> HTMLResponse:
    where = Where.parse(request.query_params)

    def build() -> dict[str, Any]:
        desk = view.review_desk(RulingStore(conn), app.clock)
        items = desk.items()
        found = queue(items, where.order, where.filter)
        return {
            "message": OUTCOMES.get(outcome or ""),
            # Only the id travels in the query; taking back anyone else's ruling is refused.
            "undo": _undoable(desk, undo) if outcome in UNDOABLE else None,
            "where": where,
            "queue": found,
            "page": found.page(where.page) if where.one_by_one else None,
            "orders": list(Order),
            "order_labels": ORDER_LABELS,
            "quick_orders": QUICK_ORDERS,
            "group_preview": GROUP_PREVIEW,
            "nothing_suggested": NOTHING_SUGGESTED,
            "confidence": confidence,
            # Not "focus", which base.html takes as the narrow page without navigation.
            "landing": focus if focus and _ROW_ID.fullmatch(focus) else None,
            # Suggest only accounts from the viewer's own rows: nobody else's are named.
            "categories": sorted(
                {i.item.proposed for i in items if i.item.proposed}
                | {i.decision.account for i in items if i.decision.account}
            ),
            "cadences": list(cadence.Cadence),
        }

    return figures_page(request, app, conn, session, view, "review", "review.html", build)


@dataclass(frozen=True, slots=True)
class Undoable:
    """The decision an Undo button would take back, and how many rows it covers."""

    ruling: Ruling
    rows: int


def _undoable(desk: ReviewDesk, ruling_id: str | None) -> Undoable | None:
    """The viewer's own decision named in the query; anyone else's offers nothing."""
    if not ruling_id or not ruling_id.isdigit() or len(ruling_id) > 18:
        return None
    found = desk.ruling(int(ruling_id))
    if found is None or found[1] == 0 or found[0].action is Action.REOPEN:
        return None
    return Undoable(*found)


def _desk(app: App, conn: Conn, view: Viewing) -> ReviewDesk:
    return view.review_desk(RulingStore(conn), app.clock)


def _ruled(app: App) -> None:
    """A ruling changes the books, so they are recomputed, behind the page."""
    app.bookkeeper.recompute_in_background()


@router.post("/groups/accept")
def accept_group(
    app: App,
    conn: Conn,
    session: Acting,
    view: Viewing,
    group: Annotated[str, Form()],
    rows: Annotated[list[str], Form()],
    back: Annotated[str, Form()] = "",
    focus: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Accept the rows of a group the page showed, each as the account it was shown with."""
    shown = dict(row.split(" ", 1) for row in rows if " " in row)
    try:
        accepted = _desk(app, conn, view).accept_group(group, shown)
    except NotOneDecision:
        return _to(_back(back), "not-one-decision", focus)
    if accepted.accepted:
        _ruled(app)
    outcome = "changed" if accepted.changed else "accepted"
    return _to(_back(back), outcome, focus, accepted.ruling)


@router.post("/rows/book")
def book_row(
    app: App,
    conn: Conn,
    session: Acting,
    view: Viewing,
    review_id: Annotated[str, Form()],
    account: Annotated[str, Form()],
    shown: Annotated[str, Form()] = "",
    back: Annotated[str, Form()] = "",
    focus: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Settle one row: its suggestion, if the account is still what was shown, or another."""
    desk = _desk(app, conn, view)
    chosen = account.strip()
    where = _back(back)
    if shown and chosen == shown:
        accepted = desk.accept(review_id, shown)
        if not accepted.accepted:
            return _to(where, "changed", review_id)
        _ruled(app)
        return _to(where, "accepted", focus, accepted.ruling)
    try:
        ruling = desk.set_account(review_id, chosen)
    except NotTheirs:
        return _to(where, "not-yours", review_id)
    except ValueError:
        return _to(where, "not-an-account", review_id)
    _ruled(app)
    return _to(where, "booked" if chosen.split(":")[0] in FREE else "moved", focus, ruling)


@router.post("/rows/reject")
def reject_row(
    app: App,
    conn: Conn,
    session: Acting,
    view: Viewing,
    review_id: Annotated[str, Form()],
    back: Annotated[str, Form()] = "",
    focus: Annotated[str, Form()] = "",
) -> RedirectResponse:
    ruling = _desk(app, conn, view).reject(review_id)
    _ruled(app)
    return _to(_back(back), "rejected", focus, ruling)


@router.post("/undo")
def undo(
    app: App,
    conn: Conn,
    session: Acting,
    view: Viewing,
    ruling: Annotated[int, Form()],
    back: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Take back one of the viewer's own decisions; its rows wait for review again."""
    reopened = _desk(app, conn, view).undo(ruling)
    _ruled(app)
    rows = RulingStore(conn).get(reopened.id)
    first = rows[1][0] if rows and len(rows[1]) == 1 else ""
    return _to(_back(back), "undone", first)


@router.post("/reminders")
def remind(
    app: App,
    conn: Conn,
    session: Acting,
    every: Annotated[str, Form()],
    back: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """The signed-in member's own reminder; there is no way to name anyone else's."""
    try:
        chosen = cadence.Cadence(every)
    except ValueError:
        raise Concealed from None
    cadence.save(conn, session.member.id, chosen, app.clock())
    return _to(_back(back), "reminder-saved")
