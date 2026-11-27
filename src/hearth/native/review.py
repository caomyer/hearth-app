"""Review, for the native app: the viewer's own queue, one row of it, the rows whose other
side hearth does not see, and the decisions the viewer makes on them.

Every screen here is written from the viewer's own desk (``Disclosure.review_desk``), so
nothing in an answer describes anyone else's pile, not even a count. Every decision is one of
the desk's own calls, the ones the review pages make, with the viewer as the one who makes it:
nothing here decides who may rule on what, or what a ruling may say. A row, group or ruling
that is not on the viewer's desk raises ``Concealed`` there, which the doorway answers as
missing, exactly like one that does not exist.

Every decision is bound to what the app showed, as a page's form is: an accept carries each
row it showed with the account it proposed then, and every other decision each row with what
it said then (``PileItem.says``). A row that changed in between is left waiting, and the app
is told.
"""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass

from hearth.disclosure import Disclosure
from hearth.disclosure.common import Unreviewed
from hearth.native.common import (
    LONGEST_ID,
    LONGEST_TEXT,
    Acted,
    Answer,
    Around,
    Decided,
    Given,
    Malformed,
    count_in,
    day,
    money,
    only,
    table_in,
    text_in,
)
from hearth.review.desk import (
    FREE,
    Acceptance,
    BanksAccount,
    BanksRow,
    ItemView,
    NotOneDecision,
    NotOpen,
    NotTheirs,
    ReviewDesk,
    State,
)
from hearth.review.queue import (
    NOTHING_SUGGESTED,
    Choice,
    Filter,
    Order,
    QueueGroup,
    Tally,
    category_label,
    explain,
    queue,
)
from hearth.review.rulings import ACCOUNT, Ruling
from hearth.review.where import Answer as WhereAnswer
from hearth.review.where import alike, answers, where

EARLIER = 5
"""How many earlier rows from the same merchant one row's detail shows."""

LONGEST_RULING = 2**63 - 1


def _desk(view: Disclosure, around: Around) -> ReviewDesk:
    return view.review_desk(around.rulings, around.clock)


def waiting(view: Disclosure, around: Around) -> Answer:
    """What waits for the viewer in Review, for the Overview to say: how many of their rows
    nobody has checked, those waiting to be told where their money went among them, as the
    pages count them; how much of their own spending this month rests on such rows, at their
    suggested category, without tax and with it; the rows nobody has said what they were yet,
    which are in no spending or income total until placed; what they booked as not spending,
    which no spending figure counts; and their tax, which is never spending, and how much of
    it is unreviewed. All of it as their own page says it (``disclosure.common.Unreviewed``)."""
    desk = _desk(view, around)
    totals = view.you().totals
    spent = totals.spent_unreviewed if totals.covered else None
    earned = totals.earned_unreviewed if totals.covered else None
    out = spent if isinstance(spent, Unreviewed) else None
    with_tax = abs(out.amount + out.tax_unreviewed) if out is not None else None
    return {
        "waiting": sum(1 for item in desk.items() if item.unreviewed),
        "spending": money(abs(out.amount)) if out is not None and out.leans else None,
        "spending_with_tax": money(with_tax) if with_tax else None,
        "tax": money(abs(out.tax)) if out is not None and out.tax else None,
        "tax_unreviewed": (
            money(abs(out.tax_unreviewed)) if out is not None and out.tax_unreviewed else None
        ),
        "unplaced_spending": _unplaced(spent),
        "unplaced_income": _unplaced(earned),
        "not_spending": money(abs(out.kept)) if out is not None and out.kept else None,
    }


def _unplaced(found: object) -> Answer | None:
    """How many rows a total leaves out as nobody has said what they were yet, and how much
    money they moved its way; None when it leaves out none, or the viewer may not know."""
    if not isinstance(found, Unreviewed) or not found.leaves_out:
        return None
    return {"rows": found.unplaced.rows, "moved": money(abs(found.unplaced.amount))}


def reasons(view: Disclosure, around: Around, review_ids: Collection[str]) -> Mapping[str, str]:
    """Why each of `review_ids` on the viewer's own desk is suggested as it is, in the words
    Review gives it beside the row (`explain`); a row not on their desk has none."""
    if not review_ids:
        return {}
    return {
        found.item.review_id: explain(found)
        for found in _desk(view, around).items()
        if found.item.review_id in review_ids
    }


@dataclass(frozen=True, slots=True)
class Narrowed:
    """What the queue was asked for: what to put first, and what to narrow it to."""

    order: Order
    filter: Filter


_QUEUE = frozenset({"order", "account", "category", "group", "text"})


def read_queue(given: Given) -> Narrowed:
    """What the queue takes, as ``review.queue`` offers it: an order, an account by its id, a
    suggested account or `NOTHING_SUGGESTED`, a group, and words. Anything else is
    malformed."""
    only(given, _QUEUE)
    order = text_in(given, "order", LONGEST_TEXT)
    if order is not None and order not in {o.value for o in Order}:
        raise Malformed
    category = text_in(given, "category", LONGEST_TEXT)
    if category is not None and category != NOTHING_SUGGESTED and not ACCOUNT.fullmatch(category):
        raise Malformed
    text = text_in(given, "text", LONGEST_TEXT)
    return Narrowed(
        Order(order) if order is not None else Order.SIZE,
        Filter(
            account=text_in(given, "account", LONGEST_ID) or "",
            category=category or "",
            group=text_in(given, "group", LONGEST_TEXT) or "",
            text=" ".join(text.split()) if text is not None else "",
        ),
    )


def queue_screen(view: Disclosure, asked: object, around: Around) -> Answer:
    """The viewer's queue: how much waits and how much is done, as the desk counts it, then
    the groups in the order asked, and every row the filter leaves in that order.

    A group lists its rows by id; each row is written once, in `rows`, in the queue's order.
    """
    assert isinstance(asked, Narrowed)  # noqa: S101 - read by `read_queue`
    found = queue(_desk(view, around).items(), asked.order, asked.filter)
    return {
        "order": found.order.value,
        "remaining": _tally(found.remaining),
        "shown": _tally(found.shown),
        "filtered": found.filter.active,
        "reviewed": found.reviewed,
        "total": found.total,
        "groups_waiting": found.group_count,
        "oldest": day(found.oldest),
        "changed": found.changed,
        "revised": found.revised,
        "kept": found.kept,
        "elsewhere": _tally(found.one_sided),
        "accounts": [_choice(choice) for choice in found.accounts],
        "categories": [_choice(choice) for choice in found.categories],
        "groups": [_group(group) for group in found.groups],
        "rows": [_row(row) for row in found.rows],
    }


def row_screen(view: Disclosure, asked: object, around: Around) -> Answer:
    """One row on the viewer's desk, as its detail shows it: where it stands, what it could be
    booked against, and the rows from the same merchant before it."""
    assert isinstance(asked, str)  # noqa: S101 - read by `_one`
    desk = _desk(view, around)
    found = desk.item(asked)
    items = desk.items()
    merchant = _merchant(found)
    earlier = sorted(
        (
            other
            for other in items
            if other.item.review_id != found.item.review_id
            and other.item.date <= found.item.date
            and merchant
            and _merchant(other) == merchant
        ),
        key=lambda v: (v.item.date, v.item.review_id),
        reverse=True,
    )[:EARLIER]
    return {
        "row": _row(found),
        "decision": _standing(found),
        "booked": _category(found.decision.account),
        # Only the viewer's own rows name a category here, as the page's list does.
        "categories": [
            _category(name)
            for name in sorted(
                {i.item.proposed for i in items if i.item.proposed}
                | {i.decision.account for i in items if i.decision.account},
                key=lambda name: (category_label(name).casefold(), name),
            )
            if name.split(":")[0] in FREE
        ],
        # Its own account is no other side for it.
        "accounts": [
            {"id": a.id, "name": a.name}
            for a in desk.movable()
            if a.ledger_account != found.item.account
        ],
        "earlier": [_earlier(other) for other in earlier],
    }


def where_screen(view: Disclosure, _asked: object, around: Around) -> Answer:
    """The viewer's rows whose other side hearth does not see, grouped so that one answer
    settles each group (``hearth.review.where``), with what may answer each."""
    desk = _desk(view, around)
    items = desk.items()
    page = where(items, desk.learned(alike))
    return {
        "rows": page.rows,
        "moved": money(page.moved),
        "groups": [_where(found) for found in answers(desk, page, items)],
    }


@dataclass(frozen=True, slots=True)
class Decision:
    """One decision the app may make: how to read what it was given, and how to make it on the
    viewer's desk."""

    read: Callable[[Given], object]
    make: Callable[[ReviewDesk, object], Decided]


@dataclass(frozen=True, slots=True)
class Accept:
    row: str
    shown: str


def _read_accept(given: Given) -> Accept:
    only(given, frozenset({"row", "shown"}))
    return Accept(_required(given, "row", LONGEST_ID), _required(given, "shown", LONGEST_TEXT))


def _accept(desk: ReviewDesk, asked: object) -> Decided:
    """Accept one row as suggested, while it still waits and still proposes what was shown."""
    assert isinstance(asked, Accept)  # noqa: S101 - read by `_read_accept`
    return _accepted(desk.accept(asked.row, asked.shown))


@dataclass(frozen=True, slots=True)
class AcceptGroup:
    group: str
    shown: Mapping[str, str]


def _read_accept_group(given: Given) -> AcceptGroup:
    only(given, frozenset({"group", "rows"}))
    return AcceptGroup(_required(given, "group", LONGEST_TEXT), table_in(given, "rows"))


def _accept_group(desk: ReviewDesk, asked: object) -> Decided:
    """Accept the rows of a group the app showed, each as the account it was shown with, when
    the group is one decision (``desk.one_decision``)."""
    assert isinstance(asked, AcceptGroup)  # noqa: S101 - read by `_read_accept_group`
    try:
        return _accepted(desk.accept_group(asked.group, asked.shown))
    except NotOneDecision:
        return Decided(Acted.NOT_ONE_DECISION)


def _accepted(acceptance: Acceptance) -> Decided:
    ruling = acceptance.ruling.id if acceptance.ruling is not None else None
    outcome = Acted.CHANGED if acceptance.changed else Acted.ACCEPTED
    return Decided(outcome, ruling, acceptance.accepted, acceptance.changed)


@dataclass(frozen=True, slots=True)
class Booking:
    """One row, as it said when it was shown, and where it goes: a category or another account
    by its full name, or one of the viewer's own accounts by its id."""

    row: str
    says: str
    category: str | None
    account: str | None


def _read_booking(given: Given) -> Booking:
    only(given, frozenset({"row", "says", "category", "account"}))
    category = text_in(given, "category", LONGEST_TEXT)
    account = text_in(given, "account", LONGEST_ID)
    if (category is None) == (account is None) or (category is not None and not category.strip()):
        raise Malformed
    return Booking(
        _required(given, "row", LONGEST_ID),
        _required(given, "says", LONGEST_ID),
        category.strip() if category is not None else None,
        account,
    )


def _book(desk: ReviewDesk, asked: object) -> Decided:
    """Book one row against a category, or one of the viewer's own accounts the desk allows,
    as the review page's row form does."""
    assert isinstance(asked, Booking)  # noqa: S101 - read by `_read_booking`
    desk.item(asked.row)
    target = _target(desk, asked, unseen=False)
    if target is None:
        return Decided(Acted.NOT_YOURS)
    try:
        ruling = desk.book_shown(asked.row, asked.says, target)
    except NotTheirs:
        return Decided(Acted.NOT_YOURS)
    except BanksAccount:
        return Decided(Acted.BANKS_ACCOUNT)
    except NotOpen:
        return Decided(Acted.NOT_OPEN)
    except ValueError:
        return Decided(Acted.NOT_AN_ACCOUNT)
    return _settled(ruling, target)


def _move_row(desk: ReviewDesk, asked: object) -> Decided:
    """Say where one row's money went, on its own, as the page of a group's rows one by one
    does: one of the viewer's own accounts hearth does not see, or a category."""
    assert isinstance(asked, Booking)  # noqa: S101 - read by `_read_booking`
    desk.item(asked.row)
    target = _target(desk, asked, unseen=True)
    if target is None:
        return Decided(Acted.NOT_YOURS)
    try:
        ruling = desk.answer_shown(asked.row, asked.says, target)
    except NotOpen:
        return Decided(Acted.NOT_OPEN)
    except ValueError:
        return Decided(Acted.NOT_YOURS)
    return _settled(ruling, target)


def _target(desk: ReviewDesk, asked: Booking | Moving, *, unseen: bool) -> str | None:
    if asked.account is not None:
        found = desk.own(asked.account, unseen=unseen)
        return found.ledger_account if found is not None else None
    return asked.category or None


def _settled(ruling: Ruling | None, target: str) -> Decided:
    if ruling is None:
        return Decided(Acted.CHANGED, changed=1)
    booked = target.split(":")[0] in FREE
    return Decided(Acted.BOOKED if booked else Acted.MOVED, ruling.id, 1)


@dataclass(frozen=True, slots=True)
class Excluding:
    row: str
    says: str


def _read_exclude(given: Given) -> Excluding:
    only(given, frozenset({"row", "says"}))
    return Excluding(_required(given, "row", LONGEST_ID), _required(given, "says", LONGEST_ID))


def _exclude(desk: ReviewDesk, asked: object) -> Decided:
    """One row is not a transaction, where the desk allows it."""
    assert isinstance(asked, Excluding)  # noqa: S101 - read by `_read_exclude`
    try:
        ruling = desk.reject_shown(asked.row, asked.says)
    except BanksRow:
        return Decided(Acted.BANKS_ROW)
    if ruling is None:
        return Decided(Acted.CHANGED, changed=1)
    return Decided(Acted.EXCLUDED, ruling.id, 1)


@dataclass(frozen=True, slots=True)
class Moving:
    """A group of rows whose other side hearth does not see, each as it said when shown, and
    where they went."""

    title: str
    shown: Mapping[str, str]
    category: str | None
    account: str | None


def _read_move_group(given: Given) -> Moving:
    only(given, frozenset({"title", "rows", "category", "account"}))
    category = text_in(given, "category", LONGEST_TEXT)
    account = text_in(given, "account", LONGEST_ID)
    if (category is None) == (account is None) or (category is not None and not category.strip()):
        raise Malformed
    return Moving(
        _required(given, "title", LONGEST_TEXT).strip(),
        table_in(given, "rows"),
        category.strip() if category is not None else None,
        account,
    )


def _move_group(desk: ReviewDesk, asked: object) -> Decided:
    """Answer one group of "Where did it go?": every row shown goes to one of the viewer's own
    accounts hearth does not see, or to a category, as one decision."""
    assert isinstance(asked, Moving)  # noqa: S101 - read by `_read_move_group`
    for review_id in asked.shown:
        desk.item(review_id)
    target = _target(desk, asked, unseen=True)
    if target is None:
        return Decided(Acted.NOT_YOURS)
    try:
        moved = desk.move_group(asked.title or "Where did it go?", target, asked.shown)
    except NotOpen:
        return Decided(Acted.NOT_OPEN)
    except ValueError:
        return Decided(Acted.NOT_YOURS)
    ruling = moved.ruling.id if moved.ruling is not None else None
    if moved.changed:
        return Decided(Acted.CHANGED, ruling, moved.accepted, moved.changed)
    booked = target.split(":")[0] in FREE
    return Decided(Acted.BOOKED if booked else Acted.MOVED, ruling, moved.accepted)


def _read_undo(given: Given) -> int:
    only(given, frozenset({"ruling"}))
    if "ruling" not in given:
        raise Malformed
    return count_in(given, "ruling", 1, LONGEST_RULING, default=0)


def _undo(desk: ReviewDesk, asked: object) -> Decided:
    """Take back one of the viewer's own decisions: its rows on their desk wait again."""
    assert isinstance(asked, int)  # noqa: S101 - read by `_read_undo`
    reopened = desk.undo(asked)
    found = desk.ruling(reopened.id)
    return Decided(Acted.UNDONE, rows=found[1] if found is not None else 0)


DECISIONS: Mapping[str, Decision] = {
    "accept": Decision(_read_accept, _accept),
    "accept_group": Decision(_read_accept_group, _accept_group),
    "book": Decision(_read_booking, _book),
    "exclude": Decision(_read_exclude, _exclude),
    "move_group": Decision(_read_move_group, _move_group),
    "move_row": Decision(_read_booking, _move_row),
    "undo": Decision(_read_undo, _undo),
}
"""Every review decision the app may make, by the name it asks with."""


def _required(given: Given, key: str, longest: int) -> str:
    found = text_in(given, key, longest)
    if found is None:
        raise Malformed
    return found


def _tally(tally: Tally) -> Answer:
    return {"rows": tally.rows, "moved": money(tally.moved)}


def _choice(choice: Choice) -> Answer:
    return {"value": choice.value, "label": choice.label, "rows": choice.rows}


def _category(name: str | None) -> Answer | None:
    """An account a row is or may be booked against, as review names it."""
    return None if name is None else {"name": name, "label": category_label(name)}


def _group(group: QueueGroup) -> Answer:
    return {
        "name": group.name,
        "size": group.size,
        "total": money(group.total),
        "moved": money(group.tally.moved),
        "suggested": [_category(name) for name in group.suggested],
        "one_decision": group.one_decision,
        "least_sure": group.lowest,
        "rows": [row.item.review_id for row in group.rows],
    }


def _row(view: ItemView) -> Answer:
    item = view.item
    return {
        "id": item.review_id,
        # A carried account's difference with its bank is its own kind of row, which is
        # confirmed, never booked elsewhere, excluded or accepted with a group.
        "kind": "reconciling" if item.reconciling else "transaction",
        "group": item.group,
        "date": day(item.date),
        "payee": item.payee or None,
        "narration": item.narration,
        "account": {"id": view.account.id, "name": view.account.name} if view.account else None,
        "amount": money(item.amount),
        "suggested": _category(item.proposed),
        "reason": explain(view),
        # A row its bank counts in the balance it reports cannot be left out (`BanksRow`).
        "excludable": not view.bank_counts,
        "says": item.says,
    }


def _standing(view: ItemView) -> str:
    if view.one_sided:
        return "elsewhere"
    match view.decision.state:
        case State.UNREVIEWED:
            return "waiting"
        case State.REVIEWED:
            return "reviewed"
        case State.REJECTED:
            return "excluded"


def _merchant(view: ItemView) -> str:
    return " ".join((view.item.payee or view.item.narration or "").split()).casefold()


def _earlier(view: ItemView) -> Answer:
    booked = view.decision.account if view.decision.state is State.REVIEWED else None
    return {
        "date": day(view.item.date),
        "amount": money(view.item.amount),
        "decision": _standing(view),
        "category": _category(booked or (view.item.proposed if view.unreviewed else None)),
    }


def _where(found: WhereAnswer) -> Answer:
    group = found.group
    learned = group.learned
    return {
        "key": group.key,
        "title": group.title,
        "question": group.question.value,
        "total": money(group.total),
        "moved": money(group.moved),
        "since": day(group.since),
        "mask": group.mask,
        "described_once": group.described_once,
        "learned": (
            None
            if learned is None
            else {
                "account": {"id": learned.account.id, "name": learned.account.name},
                "like": learned.like,
            }
        ),
        "accounts": [{"id": a.id, "name": a.name} for a in found.accounts],
        "categories": [_category(name) for name in found.categories],
        "chosen": found.chosen,
        "by_account": found.by_account,
        "rows": [_row(row) for row in group.rows],
    }
