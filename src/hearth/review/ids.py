"""Review ids: what names a row for as long as a ruling on it must hold.

The contract is beancount_nextgen's (`bcng.csvimport.triage.review_ids`), and ids made here
are the same as ids made there, so rulings carry across both. An id is a hash of the row's
account, date, amount and whitespace-normalised description, never its position in a file.
Rows that say exactly the same thing are told apart by their order among themselves, the only
thing that differs, so which of two identical rows carries which id cannot change what either
books.
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
