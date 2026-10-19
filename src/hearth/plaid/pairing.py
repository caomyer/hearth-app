"""Card-payment pairing: which feed rows are the two sides of one payment from a bank to a card.

A payment from a checking account to a card the household also connects arrives from Plaid as
two rows: money leaving the bank, and money arriving at the card. Each looks like an ordinary
transaction on its own, so booked as two of them it would be spending on one side and income on
the other, inflating both. It is one transfer between two balance-sheet accounts, and pairing
finds the two rows so the import books it once, as that.

The rule is beancount_nextgen's (``_pair_card_payments`` in ``bcng.csvimport.triage``), mirrored
so the CSV route and this one pair payments alike, as they share review ids (``hearth.review.ids``):

- a leg is a row that says it is a card payment, on a depository account (the bank's side) or
  a credit card (the card's side); the pair is one of each;
- the two amounts are exactly opposite, as `Decimal`, in the same currency;
- the bank's row is dated from `EARLIEST` days before the card's to `LATEST` days after it;
- a bank row that names the card it pays ("card ending in 1234") pairs only with that card;
- the closest dates pair first, and where a row has two equally close partners nothing is
  guessed: neither pairing is made.

One deliberate difference. bcng's code skips a tied candidate but leaves its rows free, so a
row left unpaired by a tie can still pair later with a partner further away. Its docstring says
both are left for review, and here they are: a row in a tie is not paired at all, because a
match that was ambiguous at its closest is not made certain by a worse one.

A leg nothing pairs with stays a leg (`Pairing.unpaired`). The import books it flagged in the
review group `GROUP`, against ``hearth.review.pile.TRANSFER_PENDING``: never as spending or
income, and never dropped, so every balance stays right and the gap shows. A leg is unpaired
for honest reasons a pairing cannot tell apart: its other side is at an institution nobody
connected, came outside the window or for a different amount, or never arrived because a
connection stopped.

Pairing runs in the import, over the whole household, and who owns which account is not an
input to it. Nothing it computes reaches a page except through what the import books. An
unpaired leg records nothing about why it is unpaired, so it is the same fact whatever any
other account holds: a member reviewing their own leg learns nothing about an account they
cannot see, not even that one exists with a payment of the same size.
"""

import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from hearth.plaid.client import Transaction
from hearth.plaid.registry import Registry

EARLIEST = -2
LATEST = 5
"""How far the bank's row may be dated from the card's, in days: bcng's ``-2 <= gap <=
pair_days``, with its default ``pair_days``."""

GROUP = "card-payment-unmatched"
"""The review group an unpaired leg is booked in, as bcng names it."""

CARD_PAYMENT = "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"
"""Plaid's category for a card payment. Plaid gives it to the bank's side reliably; a real
card's own side of the same payment has come with five other categories, income among them, so
on that side the wording decides as well."""

BANK_PAYS = (
    re.compile(r"\bcard ending in \d{4}\b", re.IGNORECASE),
    # bcng's patterns for a depository's payment to each card issuer it imports.
    re.compile(r"^CHASE CREDIT CRD"),
    re.compile(r"^AMERICAN EXPRESS"),
    re.compile(r"^DISCOVER DES:E-PAYMENT"),
)
"""How a bank's own row says it pays a card, when Plaid's category does not say so."""

NAMES_CARD = re.compile(r"\bcard ending in (\d{4})\b", re.IGNORECASE)
"""A bank row naming the last digits of the card it pays."""

CARD_PAID = re.compile(r"\bpayments?\b", re.IGNORECASE)
"""How a card's row says money arrived as a payment, as a refund or a statement credit does
not: "Payment Thank You", "AUTOMATIC PAYMENT - THANK YOU"."""

CARD_REPAID = re.compile(
    r"\bpayment\b.*\b(returned|reversal|reversed)\b|\b(returned|reversed)\b.*\bpayment\b"
    r"|\bcredit balance refund\b",
    re.IGNORECASE,
)
"""How a card's row says money left it back to the bank: a payment returned or reversed, or a
credit balance refunded. These pair with the bank's row for the same money arriving."""


class Side(StrEnum):
    BANK = "bank"
    """A depository account's row."""
    CARD = "card"
    """A credit card's row."""


class Connected(Protocol):
    """What pairing needs of a connected account (``hearth.plaid.registry.LinkedAccount``)."""

    @property
    def account_id(self) -> str: ...
    @property
    def type(self) -> str: ...
    @property
    def mask(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class Leg:
    """One side of a card payment, as its own account's row says it."""

    row: Transaction
    side: Side
    names: str | None = None
    """The last digits of the card a bank row says it pays, if it says."""


@dataclass(frozen=True, slots=True)
class Pair:
    """The two rows of one transfer between a bank account and a card."""

    bank: Transaction
    card: Transaction

    @property
    def gap(self) -> int:
        """Days from the card's row to the bank's."""
        return (self.bank.day - self.card.day).days


@dataclass(frozen=True, slots=True)
class Pairing:
    """Every leg among the rows given, each either in exactly one pair or unpaired.

    A row that is in neither is not a card payment, and pairing has nothing to say about it.
    """

    pairs: tuple[Pair, ...]
    unpaired: tuple[Leg, ...]
    tied: int
    """How many of the unpaired legs were in a tie, for a human reading a run: a count over
    the household, never a leg's own fact. It is not a stop signal. Rows given twice tie only
    the legs that have a partner and leave the rest unpaired and untied, so the count cannot
    tell a duplicated feed; ``Registry.connected_twice`` can."""
    _of: dict[str, Pair | Leg] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        found: dict[str, Pair | Leg] = {}
        for p in self.pairs:
            found[p.bank.transaction_id] = found[p.card.transaction_id] = p
        for leg in self.unpaired:
            found[leg.row.transaction_id] = leg
        object.__setattr__(self, "_of", found)

    def of(self, transaction_id: str) -> Pair | Leg | None:
        """The pair a row is in, the unpaired leg it is, or None for a row that is neither."""
        return self._of.get(transaction_id)


def staged(conn: sqlite3.Connection, rows: Iterable[Transaction]) -> Pairing:
    """Pair the given posted rows that are on accounts a member has mapped into the books.

    An account nobody mapped is not booked, so its rows are left out and a payment to it is a
    leg whose other side is not in the books, exactly as if it were never connected. A pending
    row may still change or vanish, and its posted version would compete with it, so it is
    refused. The caller gives each transaction once: an account connected twice sends its rows
    under two account ids, and pairing cannot tell them from two real payments
    (``Registry.connected_twice`` says when an account is).
    """
    accounts = [a for a in Registry(conn).confirmed() if a.hearth_account is not None]
    mapped = {a.account_id for a in accounts}
    return pair(accounts, [t for t in rows if t.account_id in mapped])


def pair(accounts: Iterable[Connected], rows: Iterable[Transaction]) -> Pairing:
    """Find every leg among `rows` and pair them, one to one, where pairing is certain.

    `accounts` says which kind of account each row is on; a row on any other account is
    refused. The result does not depend on the order of either.
    """
    by_id = {a.account_id: a for a in accounts}
    legs: list[Leg] = []
    seen: set[str] = set()
    for row in rows:
        if row.transaction_id in seen:
            raise ValueError(f"row {row.transaction_id} was given twice")
        seen.add(row.transaction_id)
        account = by_id.get(row.account_id)
        if account is None:
            raise ValueError(f"row {row.transaction_id} is on an account not given")
        if row.pending:
            raise ValueError(f"row {row.transaction_id} is pending; only posted rows pair")
        if not isinstance(row.amount, Decimal):
            raise TypeError(f"row {row.transaction_id} has a {type(row.amount).__name__} amount")
        leg = _leg(row, account)
        if leg is not None:
            legs.append(leg)

    bank = [leg for leg in legs if leg.side is Side.BANK]
    card = [leg for leg in legs if leg.side is Side.CARD]
    by_gap: dict[int, list[tuple[Leg, Leg]]] = defaultdict(list)
    for b in bank:
        for c in card:
            if _could_pair(b, c, by_id[c.row.account_id]):
                by_gap[abs((b.row.day - c.row.day).days)].append((b, c))

    taken: set[str] = set()
    tied: set[str] = set()
    pairs: list[Pair] = []
    for gap in sorted(by_gap):
        free = [
            (b, c)
            for b, c in by_gap[gap]
            if not {b.row.transaction_id, c.row.transaction_id} & (taken | tied)
        ]
        uses = Counter(leg.row.transaction_id for candidate in free for leg in candidate)
        for b, c in free:
            ids = {b.row.transaction_id, c.row.transaction_id}
            if all(uses[i] == 1 for i in ids):
                pairs.append(Pair(b.row, c.row))
                taken |= ids
            else:
                tied |= ids

    unpaired = [leg for leg in legs if leg.row.transaction_id not in taken]
    return Pairing(
        tuple(sorted(pairs, key=lambda p: (p.bank.day, p.bank.transaction_id))),
        tuple(sorted(unpaired, key=lambda leg: (leg.row.day, leg.row.transaction_id))),
        sum(leg.row.transaction_id in tied for leg in unpaired),
    )


def _leg(row: Transaction, account: Connected) -> Leg | None:
    """The leg `row` is, if it says it is one side of a card payment."""
    texts = [t for t in (row.name, row.original_description) if t]
    if account.type == "depository":
        if row.category == CARD_PAYMENT or any(p.search(t) for p in BANK_PAYS for t in texts):
            named = next((m.group(1) for t in texts if (m := NAMES_CARD.search(t))), None)
            return Leg(row, Side.BANK, named)
    elif account.type == "credit":
        if row.amount < 0:
            paid = row.category == CARD_PAYMENT or any(CARD_PAID.search(t) for t in texts)
        else:
            paid = any(CARD_REPAID.search(t) for t in texts)
        if paid:
            return Leg(row, Side.CARD)
    return None


def _could_pair(bank: Leg, card: Leg, card_account: Connected) -> bool:
    gap = (bank.row.day - card.row.day).days
    return (
        bank.row.amount == -card.row.amount
        and bank.row.amount != 0
        and bank.row.currency == card.row.currency
        and EARLIEST <= gap <= LATEST
        and (bank.names is None or bank.names == card_account.mask)
    )
