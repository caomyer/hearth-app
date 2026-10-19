"""One member's review queue: their unreviewed rows, ordered, filtered and counted.

A first review can be a thousand rows. What makes that tractable is grouping, since most rows
are one decision about a kind of transaction, and ordering, since the rows that hide real
errors are the least confident and the largest. So the queue orders groups and rows by any one
dimension, narrows them by account, suggested category or group, and counts honestly: how many
rows are left, how much money they move, and how much of that the current filter leaves.

Everything here is worked out from the member's own desk, so nothing in it can describe
anyone else's pile.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from hearth.names import readable
from hearth.review.decision import PROPOSAL_CHANGED, REVISED, State
from hearth.review.desk import ItemView, one_decision

PAGE_SIZE = 50
"""Rows on one page of the one-by-one view: enough to work through, few enough to render."""

LOW_CONFIDENCE = Decimal("0.6")
HIGH_CONFIDENCE = Decimal("0.9")


class Order(StrEnum):
    """What to put first. Each order has the one direction that is useful for review."""

    SIZE = "size"
    """Largest group first, so the bulk of the queue goes in a few decisions."""
    CONFIDENCE = "confidence"
    """Least sure first: nothing suggested, then the lowest confidence."""
    AMOUNT = "amount"
    """Most money first, either way."""
    DATE = "date"
    """Newest first."""
    ACCOUNT = "account"
    """By the account the money moved through."""
    CATEGORY = "category"
    """By the suggested category, rows with nothing suggested first."""

    @property
    def label(self) -> str:
        return ORDER_LABELS[self]


ORDER_LABELS = {
    Order.SIZE: "Group size",
    Order.CONFIDENCE: "Lowest confidence",
    Order.AMOUNT: "Largest amount",
    Order.DATE: "Newest",
    Order.ACCOUNT: "Account",
    Order.CATEGORY: "Category",
}


class Confidence(StrEnum):
    """How sure the import was, in words: a band tells you what to check, a score does not."""

    NONE = "Nothing suggested"
    LOW = "Low confidence"
    MEDIUM = "Fairly confident"
    HIGH = "Confident"
    UNSCORED = "Suggested by a rule"
    CHANGED = "Suggestion changed since you accepted it"
    REVISED = "Changed by the bank since it was reviewed"


def confidence(view: ItemView) -> Confidence:
    if view.decision.reason == PROPOSAL_CHANGED:
        return Confidence.CHANGED
    if view.decision.reason == REVISED:
        return Confidence.REVISED
    if view.item.proposed is None:
        return Confidence.NONE
    score = view.item.confidence
    if score is None:
        return Confidence.UNSCORED
    if score < LOW_CONFIDENCE:
        return Confidence.LOW
    return Confidence.MEDIUM if score < HIGH_CONFIDENCE else Confidence.HIGH


@dataclass(frozen=True, slots=True)
class Filter:
    """What the member narrowed the queue to. An empty field narrows nothing.

    A value that matches none of the member's own rows, such as someone else's account,
    simply matches nothing: it is never told apart from a value that does not exist.
    """

    account: str = ""
    """An account's opaque id."""
    category: str = ""
    """A suggested account, or `NOTHING_SUGGESTED`."""
    group: str = ""

    def matches(self, view: ItemView) -> bool:
        return (
            (not self.account or (view.account is not None and view.account.id == self.account))
            and (not self.category or _category_key(view) == self.category)
            and (not self.group or view.item.group == self.group)
        )

    @property
    def active(self) -> bool:
        return bool(self.account or self.category or self.group)


NOTHING_SUGGESTED = "-"
"""The category filter's value for rows the import could suggest nothing for."""


def _category_key(view: ItemView) -> str:
    return view.item.proposed or NOTHING_SUGGESTED


@dataclass(frozen=True, slots=True)
class Tally:
    """A count of rows and the money they move, in either direction."""

    rows: int = 0
    moved: Decimal = Decimal(0)

    @classmethod
    def of(cls, views: Sequence[ItemView]) -> Tally:
        return cls(len(views), sum((abs(v.item.amount) for v in views), Decimal(0)))


@dataclass(frozen=True, slots=True)
class QueueGroup:
    name: str
    rows: tuple[ItemView, ...]
    """The group's unreviewed rows that pass the filter, in the queue's order."""
    waiting: tuple[ItemView, ...]
    """All the group's unreviewed rows, filtered or not."""

    @property
    def size(self) -> int:
        return len(self.waiting)

    @property
    def tally(self) -> Tally:
        return Tally.of(self.rows)

    @property
    def suggested(self) -> tuple[str, ...]:
        return tuple(sorted({v.item.proposed for v in self.rows if v.item.proposed}))

    @property
    def acceptable(self) -> tuple[ItemView, ...]:
        """The rows one accept settles: those with the group's one suggestion.

        A group with several suggestions, or none, is no kind of transaction one decision
        covers, so it offers no accept: its rows are looked at one by one.
        """
        if not self.one_decision:
            return ()
        return self.rows

    @property
    def one_decision(self) -> bool:
        """Whether the whole group, not just the filtered rows, is one kind of transaction."""
        return one_decision(self.name, [v.item for v in self.waiting])

    @property
    def lowest(self) -> Confidence:
        return min((confidence(v) for v in self.rows), key=_CONFIDENCE_RANK.index)

    @property
    def total(self) -> Decimal:
        return sum((v.item.amount for v in self.rows), Decimal(0))


_CONFIDENCE_RANK = [
    Confidence.REVISED,
    Confidence.CHANGED,
    Confidence.NONE,
    Confidence.LOW,
    Confidence.MEDIUM,
    Confidence.UNSCORED,
    Confidence.HIGH,
]


@dataclass(frozen=True, slots=True)
class Choice:
    """One value the queue can be narrowed to, and how many rows wait under it."""

    value: str
    label: str
    rows: int


@dataclass(frozen=True, slots=True)
class Queue:
    order: Order
    filter: Filter
    groups: tuple[QueueGroup, ...]
    """Groups with a row that passes the filter, in order."""
    group_count: int
    """Groups with anything waiting, whatever the filter."""
    reviewed: int
    """Rows on the member's pile they or a co-owner have reviewed or rejected."""
    remaining: Tally
    """Every unreviewed row on the member's pile, whatever the filter."""
    changed: int
    """Unreviewed rows that were accepted once and have since changed their suggestion."""
    revised: int
    """Unreviewed rows that were reviewed once and that their bank has since changed."""
    oldest: date | None
    accounts: tuple[Choice, ...]
    categories: tuple[Choice, ...]
    decided: tuple[ItemView, ...] = field(default=())
    """The member's reviewed and rejected rows, most recently decided first."""

    @property
    def rows(self) -> tuple[ItemView, ...]:
        """Every row that passes the filter, one after another in the queue's order."""
        if self.order is Order.SIZE:
            return tuple(view for group in self.groups for view in group.rows)
        return tuple(sorted((v for g in self.groups for v in g.rows), key=_row_key(self.order)))

    @property
    def shown(self) -> Tally:
        """What the filter leaves."""
        return Tally.of(self.rows)

    @property
    def total(self) -> int:
        return self.reviewed + self.remaining.rows

    def page(self, number: int) -> Page:
        rows = self.rows
        pages = max(1, -(-len(rows) // PAGE_SIZE))
        number = min(max(number, 1), pages)
        start = (number - 1) * PAGE_SIZE
        return Page(number, pages, rows[start : start + PAGE_SIZE], start)


@dataclass(frozen=True, slots=True)
class Page:
    number: int
    pages: int
    rows: tuple[ItemView, ...]
    start: int
    """How many filtered rows come before this page."""


def queue(views: Sequence[ItemView], order: Order, narrow: Filter) -> Queue:
    """The member's queue over `views`, all of them rows on their own desk."""
    waiting = [v for v in views if v.decision.state is State.UNREVIEWED]
    decided = [v for v in views if v.decision.state is not State.UNREVIEWED]
    by_group: dict[str, list[ItemView]] = {}
    for view in waiting:
        by_group.setdefault(view.item.group, []).append(view)
    row_key = _row_key(Order.DATE if order is Order.SIZE else order)
    groups = [
        QueueGroup(name, tuple(sorted(shown, key=row_key)), tuple(rows))
        for name, rows in by_group.items()
        if (shown := [v for v in rows if narrow.matches(v)])
    ]
    groups.sort(key=_group_key(order))
    return Queue(
        order=order,
        filter=narrow,
        groups=tuple(groups),
        group_count=len(by_group),
        reviewed=len(decided),
        remaining=Tally.of(waiting),
        changed=sum(1 for v in waiting if v.decision.reason == PROPOSAL_CHANGED),
        revised=sum(1 for v in waiting if v.decision.reason == REVISED),
        oldest=min((v.item.date for v in waiting), default=None),
        accounts=_choices(waiting, lambda v: (v.account.id, v.account.name) if v.account else None),
        categories=_choices(waiting, lambda v: (_category_key(v), _category_label(v))),
        decided=tuple(
            sorted(decided, key=lambda v: v.decision.ruling.id if v.decision.ruling else 0)[::-1]
        ),
    )


def _category_label(view: ItemView) -> str:
    proposed = view.item.proposed
    return readable(proposed.rsplit(":", 1)[-1]) if proposed else "Nothing suggested"


def _choices(
    views: Sequence[ItemView], pick: Callable[[ItemView], tuple[str, str] | None]
) -> tuple[Choice, ...]:
    counts: dict[tuple[str, str], int] = {}
    for view in views:
        if (key := pick(view)) is not None:
            counts[key] = counts.get(key, 0) + 1
    return tuple(
        Choice(value, label, rows) for (value, label), rows in sorted(counts.items(), key=_label)
    )


def _label(entry: tuple[tuple[str, str], int]) -> tuple[str, str]:
    (value, label), _ = entry
    return (label.casefold(), value)


def _row_key(order: Order) -> Callable[[ItemView], Any]:
    """Ties always fall back to newest first, then the row's id, so the order is stable."""

    def newest(view: ItemView) -> tuple[int, str]:
        return (-view.item.date.toordinal(), view.item.review_id)

    match order:
        case Order.CONFIDENCE:
            return lambda v: (_CONFIDENCE_RANK.index(confidence(v)), _score(v), *newest(v))
        case Order.AMOUNT:
            return lambda v: (-abs(v.item.amount), *newest(v))
        case Order.ACCOUNT:
            return lambda v: (v.account.name.casefold() if v.account else "", *newest(v))
        case Order.CATEGORY:
            return lambda v: (_category_order(v), *newest(v))
        case Order.DATE | Order.SIZE:
            return newest


def _score(view: ItemView) -> Decimal:
    return view.item.confidence if view.item.confidence is not None else Decimal(1)


def _category_order(view: ItemView) -> tuple[bool, str]:
    return (view.item.proposed is not None, _category_label(view).casefold())


def _group_key(order: Order) -> Callable[[QueueGroup], Any]:
    """Groups go in the order of their first row, except by size, where the biggest go first."""
    match order:
        case Order.SIZE:
            return lambda g: (-g.size, g.name.casefold())
        case Order.AMOUNT:
            return lambda g: (-sum(abs(v.item.amount) for v in g.rows), g.name.casefold())
        case _:
            row_key = _row_key(order)
            return lambda g: (row_key(g.rows[0]), g.name.casefold())
