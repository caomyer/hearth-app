"""Money hearth sees one side of: the "Where did it go?" page, worked out from one member's desk.

A transfer to savings at a bank nobody connected, a car loan's monthly payment, a card paid
from a checking account hearth has never seen: each is one row on a connected account, and
its other side is somewhere hearth does not see. No category is right for it, so it is not
asked "is this category right?" but "where did it go?", and asked once for every row like it.

So the rows are grouped by what makes them one answer: the account they are on, the way the
money moved, and the place the bank's own words say it went, which for a transfer is often the
last digits of the account it went to ("Online transfer to SAV ...0417"). The card payments
hearth could not match on one account are one group a direction, as the answer is usually the
one account most were paid from or to. A member answers a group by naming the account
(``ReviewDesk.move_group``), and the next row like them is proposed onto it
(``ReviewDesk.learned``). A row unlike the rest of its group is answered on its own
(``ReviewDesk.set_account``).

Everything here is worked out from the member's own desk, so nothing in it describes anyone
else's rows or accounts.
"""

import hashlib
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from hearth.accounts import Account, Kind
from hearth.review.desk import FREE, ItemView, Learned, ReviewDesk
from hearth.review.pile import Moved, PileItem
from hearth.review.queue import category_label
from hearth.review.rulings import Action, Ruling, Scope

MASK = re.compile(
    r"(?:\.{2,}|\*{2,}|\bx|#)\s*(\d{4})\b"
    r"|\bending in\s+(\d{4})\b"
    r"|\b(?:to|from)\b\D*?\s(\d{4})\b",
    re.IGNORECASE,
)
"""How a bank's words name the last digits of the account money went to or came from:
"SAV ...0417", "ending in 0417", "acct x0417", "to Bramblewood CU 0417"."""

NUMBERED = re.compile(
    r"\s+(?:transaction|trans|conf(?:irmation)?|ref(?:erence)?|id)\s*(?:#|no\.?|number)\s*:?.*$",
    re.IGNORECASE,
)
"""A bank's running number for one transfer, and whatever follows it, which differs on every
row of the same move: "Online Transfer to CHK ...0417 transaction#: 1234567 10/02"."""

DIGITS = re.compile(r"\d+")

CARD_GROUP = "Card payments hearth could not match"


class Question(StrEnum):
    """What a group asks, which decides how its answers are worded and which comes first."""

    PAID_FROM = "paid-from"
    """Money arrived at a card from an account hearth does not see."""
    PAID_TO = "paid-to"
    """Money left a bank account for a card hearth does not see."""
    WENT = "went"
    """Money left a connected account for somewhere hearth does not see."""
    CAME = "came"
    """Money arrived in a connected account from somewhere hearth does not see."""
    LOAN = "loan"
    """A loan payment, or a loan paid out, to or from a lender hearth does not see."""


@dataclass(frozen=True, slots=True)
class WhereGroup:
    """Rows that one answer settles, as the page asks about them."""

    key: str
    """An opaque name for the group, the same on every page load while its rows are."""
    title: str
    question: Question
    rows: tuple[ItemView, ...]
    """Newest first."""
    mask: str | None
    """The last digits the bank's words name for the other side, when every row names them."""
    learned: Learned | None
    """What the member said rows like these went into before, if they have."""

    @property
    def total(self) -> Decimal:
        return sum((view.item.amount for view in self.rows), Decimal(0))

    @property
    def moved(self) -> Decimal:
        return sum((abs(view.item.amount) for view in self.rows), Decimal(0))

    @property
    def since(self) -> date:
        return min(view.item.date for view in self.rows)

    @property
    def accounts(self) -> tuple[Account, ...]:
        """The connected accounts the rows are on, in order of first appearance."""
        found = {view.account.id: view.account for view in self.rows if view.account}
        return tuple(found.values())

    @property
    def described_once(self) -> bool:
        """Whether every row has the same description, numbers aside."""
        return len({_plain(_words(view.item)) for view in self.rows}) == 1

    @property
    def outgoing(self) -> bool:
        return self.total < 0

    def shown(self) -> Mapping[str, str]:
        """Every row the page shows, with what it says, as a form sends them back."""
        return {view.item.review_id: view.item.says for view in self.rows}


@dataclass(frozen=True, slots=True)
class WherePage:
    groups: tuple[WhereGroup, ...]
    """Largest first."""

    @property
    def rows(self) -> int:
        return sum(len(group.rows) for group in self.groups)

    @property
    def moved(self) -> Decimal:
        return sum((group.moved for group in self.groups), Decimal(0))

    def group(self, key: str) -> WhereGroup | None:
        return next((group for group in self.groups if group.key == key), None)


def alike(item: PileItem) -> str:
    """What two rows share when one answer is right for both: the account they are on, which
    way the money moved, and where the bank's words say it went or came from."""
    direction = "in" if item.amount > 0 else "out"
    if item.transfer:
        side = "card" if item.account.startswith("Liabilities:") else "bank"
        return f"card|{item.account}|{side}|{direction}"
    words = _words(item)
    found = mask(words)
    place = f"mask:{found}" if found else f"says:{_plain(words)}"
    return f"move|{item.account}|{direction}|{place}"


def mask(text: str) -> str | None:
    """The last digits of an account a bank's words name, if they name one."""
    found = MASK.search(NUMBERED.sub("", text))
    return next((digits for digits in found.groups() if digits), None) if found else None


def where(views: Sequence[ItemView], learned: Mapping[str, Learned]) -> WherePage:
    """The member's rows waiting to be told where their money went, grouped one answer each."""
    grouped: dict[str, list[ItemView]] = {}
    for view in views:
        if view.one_sided:
            grouped.setdefault(alike(view.item), []).append(view)
    groups = [_group(key, rows, learned) for key, rows in grouped.items()]
    groups.sort(key=lambda g: (-len(g.rows), g.title.casefold(), g.key))
    return WherePage(tuple(groups))


def question(view: ItemView) -> Question:
    """What a row whose other side hearth does not see asks of the member."""
    item = view.item
    if item.transfer:
        return Question.PAID_FROM if item.account.startswith("Liabilities:") else Question.PAID_TO
    if view.moved is Moved.LOAN:
        return Question.LOAN
    return Question.CAME if item.amount > 0 else Question.WENT


def _group(key: str, rows: list[ItemView], learned: Mapping[str, Learned]) -> WhereGroup:
    newest = sorted(rows, key=lambda v: (v.item.date, v.item.review_id), reverse=True)
    asked = question(newest[0])
    title = CARD_GROUP if newest[0].item.transfer else _title(newest)
    masks = {mask(_words(view.item)) for view in newest}
    said = Counter(
        (found.account.id, found.like)
        for view in newest
        if (found := learned.get(view.item.review_id)) is not None
    )
    proposal = None
    if said:
        account_id = said.most_common(1)[0][0][0]
        proposal = next(
            found
            for view in newest
            if (found := learned.get(view.item.review_id)) and found.account.id == account_id
        )
    return WhereGroup(
        key=hashlib.sha256(key.encode()).hexdigest()[:12],
        title=title,
        question=asked,
        rows=tuple(newest),
        mask=next(iter(masks)) if len(masks) == 1 else None,
        learned=proposal,
    )


def _words(item: PileItem) -> str:
    """The bank's own words for a row: its description, or the merchant it names."""
    return " ".join((item.narration or item.payee or "").split())


def _plain(text: str) -> str:
    """A description with its running numbers taken out, so every row of one move reads alike."""
    return " ".join(DIGITS.sub("#", NUMBERED.sub("", text)).casefold().split())


def _title(rows: Sequence[ItemView]) -> str:
    """What the group is called: the newest row's words, without its running number."""
    words = NUMBERED.sub("", _words(rows[0].item)).strip()
    return words or "No description"


NEW = "new"
"""The account a form names when the member would rather name a new one."""


SUITS = {
    Question.PAID_FROM: (Kind.BANK, Kind.CASH, Kind.OWED),
    Question.PAID_TO: (Kind.CARD,),
    Question.WENT: (Kind.BANK, Kind.CASH, Kind.OWED, Kind.LOAN),
    Question.CAME: (Kind.BANK, Kind.CASH, Kind.OWED, Kind.LOAN),
    Question.LOAN: (Kind.LOAN, Kind.OWED),
}
"""Which kinds of account answer each question, listed first for it."""

NEW_KIND = {
    Question.PAID_FROM: Kind.BANK,
    Question.PAID_TO: Kind.CARD,
    Question.WENT: Kind.BANK,
    Question.CAME: Kind.BANK,
    Question.LOAN: Kind.LOAN,
}


@dataclass(frozen=True, slots=True)
class Answer:
    """One group as the page asks about it: which of the member's accounts may answer it,
    which categories, and what is chosen to begin with."""

    group: WhereGroup
    accounts: tuple[Account, ...]
    categories: tuple[str, ...]
    chosen: str
    """The account id chosen to begin with, or `NEW`."""
    by_account: bool
    """Whether the account answer is chosen to begin with, rather than a category."""


@dataclass(frozen=True, slots=True)
class Settled:
    """A group the member answered from this page, still standing, with its Undo."""

    ruling: Ruling
    rows: int
    moved: Decimal
    to: str
    unseen: bool
    question: Question


def answers(desk: ReviewDesk, page: WherePage, items: Sequence[ItemView]) -> list[Answer]:
    named = [a for a in desk.movable() if a.named is not None]
    used = {i.item.proposed for i in items if i.item.proposed} | {
        i.decision.account for i in items if i.decision.account
    }
    categories = sorted(a for a in used if a and a.split(":")[0] in FREE)
    answers = []
    for group in page.groups:
        suits = SUITS[group.question]
        ordered = sorted(
            named,
            key=lambda a: (a.named is None or a.named.kind not in suits, a.name.casefold()),
        )
        # Only what the member said of rows like these chooses an account to begin with: one
        # chosen for them otherwise would be a guess, saved by a careless click.
        chosen = group.learned.account.id if group.learned is not None else NEW
        wanted = "Income" if group.question is Question.CAME else "Expenses"
        answers.append(
            Answer(
                group=group,
                accounts=tuple(ordered),
                categories=tuple(
                    sorted(
                        categories,
                        key=lambda c: (not c.startswith(wanted), category_label(c).casefold()),
                    )
                ),
                chosen=chosen,
                by_account=group.question is not Question.CAME or group.learned is not None,
            )
        )
    return answers


def settled(desk: ReviewDesk, items: Sequence[ItemView]) -> list[Settled]:
    """The member's own answers from this page that still decide rows, newest first."""
    by_ruling: dict[int, list[ItemView]] = {}
    rulings: dict[int, Ruling] = {}
    for view in items:
        ruling = view.decision.ruling
        if (
            ruling is None
            or view.moved is None
            or ruling.scope is not Scope.GROUP
            or ruling.action is not Action.ACCOUNT
            or not view.decision.account
            or desk.ruling(ruling.id) is None
        ):
            continue
        by_ruling.setdefault(ruling.id, []).append(view)
        rulings[ruling.id] = ruling
    accounts = {a.ledger_account: a for a in desk.movable()}
    found = []
    for ruling_id in sorted(by_ruling, reverse=True):
        ruling = rulings[ruling_id]
        rows = by_ruling[ruling_id]
        target = accounts.get(ruling.account or "")
        found.append(
            Settled(
                ruling=ruling,
                rows=len(rows),
                moved=sum((abs(view.item.amount) for view in rows), Decimal(0)),
                to=target.name if target else category_label(ruling.account),
                unseen=target is not None and target.named is not None,
                question=question(rows[0]),
            )
        )
    return found
