"""What kind of money each income and expense account is.

A ledger books money going out under Expenses and money coming in under Income, but not all of
it is spending or earning in the sense a household means. A retirement contribution taken
from pay is often booked as an expense, though the money is still the household's, only
somewhere else; tax is money out, but no choice anyone made at a till. So every income and
expense account has a kind (`Kind`):

- under Income: pay, investment income, or other income;
- under Expenses: spending, tax, or not spending, a move such as a retirement contribution
  booked as an expense, which is money kept, not spent.

A kind is guessed from the account's name alone, by generic words (`guess`): "Salary" or
"Wages" is pay, "Interest" or "Dividends" investment income, "Tax" tax, "Retirement", "401k"
or "IRA" not spending. No employer, bank, broker or person is named anywhere in the rule.
Anything it does not recognise is spending under Expenses and other income under Income, so no
account is ever left out of every total.

A guess is never stored. A kind is stored, in the app database's `category_kinds`, only when a
person changes it (`set_kind`), and forgetting it (`forget`) goes back to the guess. For now
an admin changes one with ``hearth categories``. Kinds are the household's, as its categories
are, and the books read them on every recompute (``books.Books.kinds``).

What a kind changes is the figures that mean "spending": every spending total, by category,
by day and by pace, counts only spending, never tax nor "not spending" (``figures``). Tax is
its own figure beside spending. What a household did not spend is not lost: income less tax
and spending is what was left over, so an amount moved somewhere else rather than spent is in
what was left over, which still adds up. Income totals count every income kind.
"""

import re
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import StrEnum

from hearth.db import transaction


class Kind(StrEnum):
    PAY = "pay"
    INVESTMENT_INCOME = "investment-income"
    OTHER_INCOME = "other-income"
    SPENDING = "spending"
    TAX = "tax"
    NOT_SPENDING = "not-spending"

    @property
    def label(self) -> str:
        return LABELS[self]


LABELS = {
    Kind.PAY: "Pay",
    Kind.INVESTMENT_INCOME: "Investment income",
    Kind.OTHER_INCOME: "Other income",
    Kind.SPENDING: "Spending",
    Kind.TAX: "Tax",
    Kind.NOT_SPENDING: "Not spending",
}

KINDS = {
    "Income": (Kind.PAY, Kind.INVESTMENT_INCOME, Kind.OTHER_INCOME),
    "Expenses": (Kind.SPENDING, Kind.TAX, Kind.NOT_SPENDING),
}
"""The kinds an account under each root may be."""

UNRECOGNISED = {"Income": Kind.OTHER_INCOME, "Expenses": Kind.SPENDING}

WORDS: Mapping[str, tuple[tuple[Kind, frozenset[str]], ...]] = {
    "Income": (
        (
            Kind.PAY,
            frozenset(
                {
                    "salary",
                    "salaries",
                    "wage",
                    "wages",
                    "payroll",
                    "paycheck",
                    "paycheque",
                    "bonus",
                    "bonuses",
                    "commission",
                    "commissions",
                    "overtime",
                }
            ),
        ),
        (
            Kind.INVESTMENT_INCOME,
            frozenset(
                {
                    "interest",
                    "dividend",
                    "dividends",
                    "gain",
                    "gains",
                    "investment",
                    "investments",
                    "yield",
                    "coupon",
                    "coupons",
                    "distribution",
                    "distributions",
                }
            ),
        ),
    ),
    "Expenses": (
        (
            Kind.NOT_SPENDING,
            frozenset({"retirement", "401k", "403b", "457b", "ira", "roth", "pension"}),
        ),
        (Kind.TAX, frozenset({"tax", "taxes", "taxation", "withholding", "fica"})),
    ),
}
"""The generic words each kind is guessed from, in the order they are tried: an expense
account named for a retirement contribution is a move, whatever tax it saves. Only words that
say so unambiguously: "contribution", "transfer", "investment" and "savings" name fees,
charity and spending as often as moves, so each of those is spending until a person says
otherwise, as is a word such as "PayPal" that merely begins with "pay"."""

PAY_ALONE = "pay"
"""Pay, when it is a whole part of the name on its own ("Income:Pay", "Income:Job:Pay"), never
the start of a longer word."""

_TOKEN = re.compile(r"\d+[A-Za-z]?|[A-Z]+(?![a-z])|[A-Z]?[a-z]+")


class NotACategory(ValueError):  # noqa: N818 - it names a refusal, as `Concealed` does
    """An account that is neither income nor expense, or a kind its root does not take."""


def root(account: str) -> str | None:
    """ "Income" or "Expenses" for a category, None for anything else."""
    first = account.split(":", 1)[0]
    return first if first in KINDS else None


def words(account: str) -> list[str]:
    """The words of an account's name below its root, lowercased: "Expenses:Retirement401k"
    is ["retirement", "401k"]."""
    return [w.lower() for part in account.split(":")[1:] for w in _TOKEN.findall(part)]


def guess(account: str) -> Kind:
    """The kind an account's name says it is, by `WORDS`; for a name it does not recognise,
    spending under Expenses and other income under Income."""
    found = root(account)
    if found is None:
        raise NotACategory(f"{account} is not an income or expense account")
    said = set(words(account))
    if found == "Income" and PAY_ALONE in (p.casefold() for p in account.split(":")[1:]):
        return Kind.PAY
    for kind, known in WORDS[found]:
        if said & known:
            return kind
    return UNRECOGNISED[found]


def stored(conn: sqlite3.Connection) -> dict[str, Kind]:
    """Every kind a person set, by account."""
    rows = conn.execute("SELECT account, kind FROM category_kinds ORDER BY account")
    return {row["account"]: Kind(row["kind"]) for row in rows}


def kinds(accounts: Iterable[str], chosen: Mapping[str, Kind]) -> dict[str, Kind]:
    """Each income and expense account of `accounts` with its kind: the one `chosen` for it,
    or else its guess."""
    return {a: chosen.get(a) or guess(a) for a in sorted(set(accounts)) if root(a) is not None}


def set_kind(conn: sqlite3.Connection, account: str, kind: Kind, *, now: datetime) -> None:
    """Record that a person says `account` is of `kind`, which its root must take."""
    found = root(account)
    if found is None:
        raise NotACategory(f"{account} is not an income or expense account")
    if kind not in KINDS[found]:
        allowed = ", ".join(k.value for k in KINDS[found])
        raise NotACategory(f"an account under {found} is one of: {allowed}")
    with transaction(conn):
        conn.execute(
            "INSERT INTO category_kinds (account, kind, set_at) VALUES (?, ?, ?)"
            " ON CONFLICT (account) DO UPDATE SET kind = excluded.kind, set_at = excluded.set_at",
            (account, kind.value, now.isoformat()),
        )


def forget(conn: sqlite3.Connection, account: str) -> bool:
    """Go back to guessing `account`'s kind; whether a person had set one."""
    with transaction(conn):
        cursor = conn.execute("DELETE FROM category_kinds WHERE account = ?", (account,))
    return cursor.rowcount > 0
