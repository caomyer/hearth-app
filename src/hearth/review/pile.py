"""Reading the rows an import left for review.

Every imported transaction is in the ledger. One nobody has checked is a `!` entry, as
beancount_nextgen writes them, whose first posting is the bank's and whose second balances it
against the proposed account, or against `Equity:Review:Unresolved` when nothing could be
proposed (`Equity:Review:TransferPending` for one side of a transfer whose other side is
missing). Metadata carries the row's `review-id`, `review-group`, `model-confidence` and
`source`. Together they are the pile: every row a member may review, whether or not anyone has.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from beancount.core.amount import Amount
from beancount.core.data import Directive, Transaction

UNRESOLVED = "Equity:Review:Unresolved"
"""Where a row sits when nothing could be proposed. Never an account anyone chose."""

TRANSFER_PENDING = "Equity:Review:TransferPending"
"""Where one side of a transfer sits while its other side is not in the books: a card payment
whose bank row, or card row, nothing pairs with (``hearth.plaid.pairing``). Never an account
anyone chose either, so accepting the row as proposed cannot settle it."""

WAITING = frozenset({UNRESOLVED, TRANSFER_PENDING})
"""The accounts a row balances against while it waits for a member to say where it belongs."""


@dataclass(frozen=True, slots=True)
class PileItem:
    """One review row, as the import proposed it."""

    review_id: str
    group: str
    date: date
    account: str
    amount: Decimal
    currency: str
    payee: str | None
    narration: str
    proposed: str | None
    confidence: Decimal | None
    source: str | None
    location: str


def _location(entry: Transaction) -> str:
    return f"{entry.meta.get('filename', '?')}:{entry.meta.get('lineno', '?')}"


def read_pile(entries: Iterable[Directive]) -> list[PileItem]:
    """Every review row in `entries`, refusing one that cannot be ruled on durably."""
    items: list[PileItem] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Transaction) or entry.flag != "!":
            continue
        where = _location(entry)
        review_id = entry.meta.get("review-id")
        group = entry.meta.get("review-group")
        if not isinstance(review_id, str) or not review_id:
            raise ValueError(f"{where}: a set-aside entry has no review-id to key a ruling to")
        if not isinstance(group, str) or not group:
            raise ValueError(f"{where}: set-aside entry {review_id} has no review-group")
        if review_id in seen:
            raise ValueError(f"{where}: review id {review_id} appears twice in the pile")
        seen.add(review_id)
        if len(entry.postings) != 2:
            raise ValueError(f"{where}: set-aside entry {review_id} must have exactly two postings")
        bank, proposal = entry.postings
        units = bank.units
        if not isinstance(units, Amount) or not isinstance(units.number, Decimal):
            raise ValueError(f"{where}: set-aside entry {review_id} has no amount")
        confidence = entry.meta.get("model-confidence")
        source = entry.meta.get("source")
        items.append(
            PileItem(
                review_id=review_id,
                group=group,
                date=entry.date,
                account=bank.account,
                amount=units.number,
                currency=units.currency,
                payee=entry.payee,
                narration=entry.narration or "",
                proposed=None if proposal.account in WAITING else proposal.account,
                confidence=confidence if isinstance(confidence, Decimal) else None,
                source=source if isinstance(source, str) else None,
                location=where,
            )
        )
    return items
