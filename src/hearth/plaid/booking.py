"""The import: every posted row Plaid sent, as an entry in hearth's own books.

What Plaid sent lives in the feed store; this turns it into entries on every recompute, in
memory, as the review overlay lays rulings over them. Nothing is stored twice, so re-running
it books exactly what the feed holds: a row Plaid revised is booked as it now stands, under
the id it always had, and a row Plaid withdrew is no longer booked at all.

Each transaction is taken once before anything is paired (``docs/pairing.md``). An account
reached through two connections sends its rows twice, under two account ids, and nothing in
the rows can tell them from two real payments. So where `Registry.connected_twice` reports
one, each newer connection's rows stand for it from the day its history starts, and two
working connections are refused until one is removed (`_gives_way`). A pending row is never
booked: it may still change or vanish, and its posted version would compete with it.

Then, row by row:

- the two rows of a card payment `pairing` pairs are one transfer between the two accounts,
  booked once, as both banks' records of it agree;
- one side of a card payment nothing pairs with is booked flagged in `pairing.GROUP`,
  against ``hearth.review.pile.TRANSFER_PENDING``, never as spending or income;
- every other row is booked flagged against the account Plaid's category proposes, or
  against ``hearth.review.pile.UNRESOLVED`` when it proposes none: a transfer, a loan
  payment, or a category Plaid could not decide.

A flagged row carries what the review desk keys a ruling to: its review id, derived from the
Plaid transaction id so it survives Plaid revising the row, and its group, which is Plaid's
category, so that each group is one decision. Every entry carries its
`connections.plaid_source`, which coverage parses to tell which connection vouches for it.

An account the household's own ledger opens is that ledger's (``docs/real-ledger.md``):
hearth leaves its transactions there and books none from a connection, which would count every
one the ledger already holds a second time.
"""

import re
import sqlite3
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from beancount.core.amount import Amount
from beancount.core.data import EMPTY_SET, Posting, new_metadata
from beancount.core.data import Transaction as Entry

from hearth.accounts import all_accounts
from hearth.connections import plaid_source
from hearth.plaid import pairing
from hearth.plaid.client import PRIMARIES, Transaction
from hearth.plaid.feed import Feed
from hearth.plaid.mapping import component
from hearth.plaid.registry import Item, LinkedAccount, Registry
from hearth.review.ids import plaid_review_id
from hearth.review.pile import TRANSFER_PENDING, UNRESOLVED, Moved

ORIGIN = "<plaid>"
"""Where an entry booked here says it is from, until ``hearth.ownbooks`` files it among the
entries hearth derives."""

FLAGGED = "!"
PAIRED = "*"
"""A paired card payment is booked as checked: it is the two banks' own records of one
transfer, and nothing in it is a guess for a member to correct."""

UNCATEGORISED = "Uncategorized"
"""The group of a row Plaid names no merchant and no category for."""

UNPROPOSED = frozenset(
    {"TRANSFER_IN", "TRANSFER_OUT", "LOAN_PAYMENTS", "LOAN_DISBURSEMENTS", "OTHER"}
)
"""Primaries that say where money went without saying what it was for. A transfer's other
account, or a loan payment's split between principal and interest, is not in the row, so
nothing is proposed for them: a member says."""


class Refused(Exception):  # noqa: N818 - it names a refusal, as `Concealed` does
    """The feed cannot be booked as it stands; the message says why, naming no account."""


TWICE = (
    "A bank account is reached through two working connections, so its transactions would be"
    " counted twice. Hearth brings in no new transactions until one of them is removed: its"
    " holder removes the one they no longer need in Settings, Connections, and what it brought"
    " in stays."
)


@dataclass(frozen=True, slots=True)
class Booked:
    """What the import booked: its entries, and the accounts they post to besides the ones
    each connected account feeds, which the books must open.

    `fed` is each ledger account the import feeds, with every connected account feeding it,
    and `taken` the rows it booked for each, as the bank sent them: what an opening balance is
    derived from (``hearth.plaid.opening``), apart from how the entries were made of them."""

    entries: tuple[Entry, ...]
    accounts: frozenset[str]
    fed: Mapping[str, tuple[LinkedAccount, ...]] = field(default_factory=dict)
    taken: Mapping[str, tuple[Transaction, ...]] = field(default_factory=dict)


def book(conn: sqlite3.Connection, feed: Feed, *, opened: Collection[str] = ()) -> Booked:
    """Every posted row on an account a member has mapped into the books, as an entry.

    Accounts in `opened`, which an inherited ledger opens, are left to it. Raises `Refused`
    while any account is connected twice.
    """
    registry = Registry(conn)
    before = _gives_way(registry, feed)
    named = {account.id: account.ledger_account for account in all_accounts(conn)}
    feeds: dict[str, tuple[LinkedAccount, str]] = {}
    for linked in registry.confirmed():
        ledger_account = named.get(linked.hearth_account or "")
        if ledger_account is not None and ledger_account not in opened:
            feeds[linked.account_id] = (linked, ledger_account)
    rows = [
        row
        for account_id in sorted(feeds)
        for row in feed.transactions(account_id)
        if not row.pending and (account_id not in before or row.day < before[account_id])
    ]
    paired = pairing.staged(conn, rows)
    entries: list[Entry] = []
    for row in sorted(rows, key=lambda r: (r.day, r.transaction_id)):
        linked, ledger_account = feeds[row.account_id]
        found = paired.of(row.transaction_id)
        if isinstance(found, pairing.Pair):
            if found.bank.transaction_id == row.transaction_id:
                card, card_account = feeds[found.card.account_id]
                entries.append(
                    _transfer(found, (linked.item_id, ledger_account), (card.item_id, card_account))
                )
        elif isinstance(found, pairing.Leg):
            entries.append(
                _flagged(row, linked.item_id, ledger_account, TRANSFER_PENDING, pairing.GROUP)
            )
        else:
            entries.append(
                _flagged(
                    row, linked.item_id, ledger_account, proposal(row.category), group(row.category)
                )
            )
    accounts = {p.account for e in entries for p in e.postings} - {a for _, a in feeds.values()}
    fed: dict[str, tuple[LinkedAccount, ...]] = {}
    for linked, ledger_account in feeds.values():
        fed[ledger_account] = (*fed.get(ledger_account, ()), linked)
    taken: dict[str, tuple[Transaction, ...]] = dict.fromkeys(fed, ())
    for row in rows:
        ledger_account = feeds[row.account_id][1]
        taken[ledger_account] = (*taken[ledger_account], row)
    return Booked(tuple(entries), frozenset(accounts), fed, taken)


def _gives_way(registry: Registry, feed: Feed) -> dict[str, date]:
    """Each account reached again through a newer connection, with the day from which the
    newer one's rows stand for it instead (`Registry.connected_twice`).

    Removing a bank and connecting it again is an ordinary thing to do, and Plaid then sends
    the whole history again, under new ids. An older connection gives way to every newer
    one: its rows still count before the history of any connection linked after it starts,
    which nothing else holds, and not from that day on. The working connection is the newest;
    of removed ones, the one linked later is newer. Two working connections are two live feeds
    of one account, which nothing here can tell apart from two real payments, so they are
    refused (`TWICE`) until one is removed, after which this rule applies.
    """
    found: dict[str, date] = {}
    for group in registry.connected_twice():
        items = {a.item_id: registry.item(a.item_id) for a in group}
        if sum(1 for a in group if _active(items[a.item_id])) > 1:
            raise Refused(TWICE)
        by_item: dict[str, list[LinkedAccount]] = {}
        for account in group:
            by_item.setdefault(account.item_id, []).append(account)
        newest_first = sorted(
            by_item, key=lambda i: (_active(items[i]), _linked(items[i])), reverse=True
        )
        starts = date.max
        for item_id in newest_first:
            for account in by_item[item_id]:
                if starts < date.max:
                    found[account.account_id] = min(starts, found.get(account.account_id, date.max))
            starts = min(
                starts, *(feed.held(a.account_id).earliest or date.max for a in by_item[item_id])
            )
    return found


def _active(item: Item | None) -> bool:
    return item is not None and item.active


def _linked(item: Item | None) -> datetime:
    return item.linked_at if item is not None else datetime.min.replace(tzinfo=UTC)


def proposal(category: str | None) -> str:
    """The account Plaid's detailed category proposes, or `UNRESOLVED` when it proposes none.

    ``INCOME_WAGES`` is ``Income:Wages``, ``FOOD_AND_DRINK_COFFEE`` is
    ``Expenses:FoodAndDrink:Coffee``. A category hearth does not know proposes nothing rather
    than a guess.
    """
    split = _split(category)
    if split is None:
        return UNRESOLVED
    primary, detail = split
    if primary in UNPROPOSED:
        return UNRESOLVED
    if primary == "INCOME":
        return f"Income:{_name(detail)}"
    return f"Expenses:{_name(primary)}:{_name(detail)}"


def group(category: str | None) -> str:
    """The review group of a row that is not a card payment: Plaid's category, in words.

    Every row in it proposes the same account, so accepting the group is one decision, that
    Plaid's category is right for these rows. A category is coarser than a merchant: a group
    can hold a row Plaid filed wrongly, so the queue shows a group's rows before it is
    accepted, and a member books any one of them elsewhere on its own, which a later group
    accept does not undo (``hearth.review.decision``). ``FOOD_AND_DRINK_COFFEE`` is "Food and
    drink: coffee". Plaid rates its own category in words; the feed does not keep them yet, so
    no confidence is written rather than one made up.
    """
    split = _split(category)
    if split is None:
        return UNCATEGORISED
    primary, detail = (_words(part) for part in split)
    if detail.startswith(f"{primary} ") or detail == primary:
        detail = detail.removeprefix(primary).strip()
    named = f"{primary}: {detail}" if detail else primary
    return named[:1].upper() + named[1:]


MOVES = {
    "TRANSFER_IN": Moved.TRANSFER,
    "TRANSFER_OUT": Moved.TRANSFER,
    "LOAN_PAYMENTS": Moved.LOAN,
    "LOAN_DISBURSEMENTS": Moved.LOAN,
}
"""Primaries whose rows move money to or from another account the row does not name."""


def moved(group_name: str) -> Moved | None:
    """What kind of move a review group's rows are, if they move money to or from an account
    hearth does not see the side of: one side of a card payment nothing pairs with, or a row
    Plaid calls a transfer or a loan payment. None for every other group, which a category
    settles."""
    if group_name == pairing.GROUP:
        return Moved.CARD
    primary = group_name.split(":", 1)[0].strip().casefold()
    return next((kind for p, kind in MOVES.items() if _words(p) == primary), None)


ABBREVIATIONS = frozenset({"ATM", "TV"})
"""Words in Plaid's categories that read as letters, which stay capitals in a group's name:
"Bank fees: ATM fees", not "atm fees"."""


def _words(part: str) -> str:
    return " ".join(w if w in ABBREVIATIONS else w.lower() for w in part.split("_"))


def _split(category: str | None) -> tuple[str, str] | None:
    if not category:
        return None
    primary = next((p for p in PRIMARIES if category.startswith(f"{p}_")), None)
    if primary is None:
        return None
    detail = category.removeprefix(f"{primary}_")
    return (primary, detail) if re.fullmatch(r"[A-Z0-9_]+", detail) else None


def _name(words: str) -> str:
    return component(_words(words))


def _flagged(row: Transaction, item_id: str, account: str, against: str, group: str) -> Entry:
    """A row for a member to review: their account's side as the bank sent it, balanced
    against `against`."""
    meta = new_metadata(ORIGIN, 0)
    meta["review-id"] = plaid_review_id(row.transaction_id)
    meta["review-group"] = group
    meta["source"] = plaid_source(item_id, row.transaction_id)
    units = _units(row)
    return Entry(
        meta,
        row.day,
        FLAGGED,
        _payee(row),
        _narration(row),
        EMPTY_SET,
        EMPTY_SET,
        [
            Posting(account, units, None, None, None, None),
            Posting(against, -units, None, None, None, None),
        ],
    )


CARD_PAYMENT = "Card payment"
"""What a paired payment says. The two accounts may be two members', and the entry is on both,
so it repeats neither bank's own words for it, which are that account's transaction detail."""


def _transfer(found: pairing.Pair, bank: tuple[str, str], card: tuple[str, str]) -> Entry:
    """A bank's payment to a card, booked once, on the day the card received it, as beancount
    _nextgen books it. Each side says which connection's row it is, so a card on a connection
    of its own is vouched for by its own connection's row, not the bank's."""
    (bank_item, bank_account), (card_item, card_account) = bank, card
    meta = new_metadata(ORIGIN, 0)
    meta["source"] = plaid_source(bank_item, found.bank.transaction_id)
    sides = (
        (bank_account, found.bank, bank_item),
        (card_account, found.card, card_item),
    )
    return Entry(
        meta,
        found.card.day,
        PAIRED,
        None,
        CARD_PAYMENT,
        EMPTY_SET,
        EMPTY_SET,
        [
            Posting(
                account,
                _units(row),
                None,
                None,
                None,
                {"source": plaid_source(item_id, row.transaction_id)},
            )
            for account, row, item_id in sides
        ],
    )


def _units(row: Transaction) -> Amount:
    """The row's amount on its own account. Plaid's sign is money leaving the account, so a
    purchase is a negative posting on a checking account and on a card alike."""
    if not isinstance(row.amount, Decimal):
        raise TypeError(f"row {row.transaction_id} has a {type(row.amount).__name__} amount")
    if not row.currency:
        raise Refused("A bank sent a transaction without a currency, so hearth cannot book it.")
    return Amount(-row.amount, row.currency)


def _payee(row: Transaction) -> str | None:
    return " ".join(row.merchant_name.split()) if row.merchant_name else None


def _narration(row: Transaction) -> str:
    return " ".join((row.name or row.original_description or "").split())
