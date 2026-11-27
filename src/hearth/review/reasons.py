"""Why a row is suggested as it is, in words a member reads in Review (``hearth.proposals``).

A row hearth books carries its reason in its metadata, as `review-why`, one of `Why`, and
`review-reason`, the words. Neither is ever a percentage: a reason says where a suggestion
came from, which is what a member needs to judge it.
"""

from dataclasses import dataclass
from enum import StrEnum

WHY = "review-why"
WORDS = "review-reason"


class Why(StrEnum):
    REMEMBERED = "remembered"
    """What the row's owners decided for the same merchant before: "Dining twice before"."""
    LEDGER = "ledger"
    """How the household's imported ledger books the same merchant: "As your ledger books it"."""
    BANK = "bank"
    """The bank's own category, as the ledger or hearth names it: "Bank says Restaurant"."""
    NEW = "new"
    """Nothing is suggested, and nothing was ever decided for the merchant: "New merchant"."""
    MIXED = "mixed"
    """Nothing is suggested, as the merchant was booked more than one way before."""
    ELSEWHERE = "elsewhere"
    """Nothing is suggested, as the merchant was only ever booked to no category before:
    moved onto an account, or not a transaction: "Not booked to a category before"."""


@dataclass(frozen=True, slots=True)
class Reason:
    why: Why
    words: str


def read(meta: object) -> Reason | None:
    """The reason an entry's metadata carries, or None for none, or one this hearth does not
    know, as a ledger another importer wrote may carry."""
    if not isinstance(meta, dict):
        return None
    why, words = meta.get(WHY), meta.get(WORDS)
    if not isinstance(why, str) or not isinstance(words, str) or not words.strip():
        return None
    try:
        return Reason(Why(why), words)
    except ValueError:
        return None
