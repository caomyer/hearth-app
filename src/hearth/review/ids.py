"""Review ids: what names a row for as long as a ruling on it must hold.

The contract is beancount_nextgen's (`bcng.csvimport.triage.review_ids`), and ids made here
are the same as ids made there, so rulings carry across both. An id is a hash of the row's
account, date, amount and whitespace-normalised description, never its position in a file.
Rows that say exactly the same thing are told apart by their order among themselves, the only
thing that differs, so which of two identical rows carries which id cannot change what either
books.

A row Plaid sent is named by Plaid's own transaction id instead (`plaid_review_id`), which
Plaid keeps when it revises the row. A ruling therefore follows the transaction through a
revision, and is bound to what the row said when it was made (`says`), so a ruling on a row the
bank has since changed is not silently kept (``hearth.review.decision``).
"""

import hashlib
from collections import Counter
from collections.abc import Iterable
from datetime import date
from decimal import Decimal

ID_LENGTH = 10


def review_id(account: str, day: date, amount: Decimal, description: str, nth: int = 0) -> str:
    content = (account, day.isoformat(), str(amount), " ".join(description.split()), str(nth))
    return hashlib.sha256("\x1f".join(content).encode()).hexdigest()[:ID_LENGTH]


def review_ids(rows: Iterable[tuple[str, date, Decimal, str]]) -> list[str]:
    """Ids for rows in file order, each (account, date, amount, description)."""
    seen: Counter[tuple[str, date, Decimal, str]] = Counter()
    ids: list[str] = []
    for account, day, amount, description in rows:
        content = (account, day, amount, " ".join(description.split()))
        ids.append(review_id(account, day, amount, description, seen[content]))
        seen[content] += 1
    if len(set(ids)) != len(ids):
        raise ValueError("two different rows share a review id; lengthen the id")
    return ids


def plaid_review_id(transaction_id: str) -> str:
    """The review id of the row Plaid sent as `transaction_id`, whatever it says now."""
    if not transaction_id:
        raise ValueError("a Plaid row needs its transaction id")
    content = ("plaid", transaction_id)
    return hashlib.sha256("\x1f".join(content).encode()).hexdigest()[:ID_LENGTH]


def reconciling_review_id(account: str, cut_over: date) -> str:
    """The review id of the entry that reconciles `account`, carried on from the base from
    `cut_over`, with its bank (``hearth.plaid.opening.Carrying``): one per account and day, so
    the entry derived afresh on every recompute is always the same row, whatever it says."""
    content = ("reconciling", account, cut_over.isoformat())
    return hashlib.sha256("\x1f".join(content).encode()).hexdigest()[:ID_LENGTH]


def says(account: str, day: date, amount: Decimal, currency: str) -> str:
    """What a row says, as a ruling is bound to it: which account, which day, how much.

    Kept with a ruling in place of the row's figures, which the app database never holds, as a
    review id made from them is. A description Plaid tidies, or a category it refines, changes
    none of it: those change the proposal, which binds an accept already.
    """
    content = (account, day.isoformat(), f"{amount.normalize():f}", currency)
    return hashlib.sha256("\x1f".join(content).encode()).hexdigest()[:ID_LENGTH]
