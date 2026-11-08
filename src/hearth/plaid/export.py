"""What a member's own beancount and fava need of what hearth holds: one file, written on
request, that reads nothing but hearth's own stores and makes no live call.

Hearth keeps the connections and the rows; a ledger kept outside hearth keeps everything else.
The file is the only thing the two share. It says, for every account the member owns: what the
account is, how its connection stands, what the bank last said it holds, and every posted row,
each transaction once, as the bank sent it. Hearth reads no ledger to write it, so choosing
which rows a ledger still lacks is the other side's job (``docs/connections.md``).

Amounts are Plaid's own: strings, positive for money out of the account. A row's `pair` says
what hearth's pairing knows that a row alone cannot: that it is one side of a card payment and,
when the same member holds both sides, which account the other side is on. A card's side rarely
carries Plaid's payment category, so this is the only reliable word on it (``docs/pairing.md``).

An investment account also carries its activity, each trade, dividend, contribution, fee and
transfer of cash or shares as its brokerage reported it (``hearth.plaid.activity``), the day
that activity is complete through, and what the brokerage last said the account holds. Each
activity on a security carries hearth's own record of that security's price on its day, when
hearth asked for one that day: a brokerage sends a vest as shares with no value, and the
holdings hearth asks for each morning are the only record of what a share was worth then.

The file holds one member's accounts, because a ledger outside hearth has no privacy boundary
of its own. A pair names its other account only if that member owns it too; otherwise the row
is a leg with nothing on the other side, exactly as if that account were not connected, so the
file says nothing of an account the member cannot see.
"""

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from hearth.accounts import all_accounts
from hearth.members import MemberId
from hearth.plaid import booking, pairing
from hearth.plaid.client import Activity, Position, Security, Transaction
from hearth.plaid.feed import Balance, Feed
from hearth.plaid.registry import Item, LinkedAccount, Registry
from hearth.valuation.pricestore import PriceStore

PRICE_SOURCE = "plaid"
"""Whose prices an activity's `market_price` is read from: what a brokerage gave hearth."""

SCHEMA = "buzz.plaid.v3"
"""Checked by the importer that reads the file, so a change of shape fails loudly there."""

AMOUNT_CONVENTION = "plaid-native: positive = money out of the account."


@dataclass(frozen=True, slots=True)
class _Fed:
    """One ledger account the member owns, and every connected account feeding it."""

    ledger_account: str
    linked: tuple[LinkedAccount, ...]


def export(
    conn: sqlite3.Connection, feed: Feed, member: MemberId, *, now: datetime
) -> dict[str, object]:
    """The whole file for `member`, as plain JSON values.

    Raises `booking.Refused` while any account is connected twice, as the books do: its rows
    could not be told from two real payments.
    """
    feeds, rows = booking.taken_once(conn, feed)
    paired = pairing.staged(conn, rows)
    theirs = {a.ledger_account for a in all_accounts(conn) if member in a.owners}
    registry = Registry(conn)
    fed: dict[str, list[LinkedAccount]] = {}
    for linked, ledger_account in feeds.values():
        if ledger_account in theirs:
            fed.setdefault(ledger_account, []).append(linked)

    def other_side(row: Transaction) -> dict[str, object] | None:
        found = paired.of(row.transaction_id)
        if found is None:
            return None
        if isinstance(found, pairing.Leg):
            return _leg(found.side)
        mine_is_bank = found.bank.transaction_id == row.transaction_id
        other = found.card if mine_is_bank else found.bank
        linked, ledger_account = feeds[other.account_id]
        side = pairing.Side.BANK if mine_is_bank else pairing.Side.CARD
        if ledger_account not in theirs:
            return _leg(side)
        return {
            "side": side.value,
            "other_account": ledger_account,
            "other_name": linked.name,
            "other_mask": linked.mask,
            "other_date": other.day.isoformat(),
        }

    prices = PriceStore(conn)
    securities = feed.securities()
    accounts = []
    for ledger_account in sorted(fed):
        one = _Fed(ledger_account, tuple(fed[ledger_account]))
        ids = {linked.account_id for linked in one.linked}
        accounts.append(
            _account(
                one,
                registry,
                feed,
                [(row, other_side(row)) for row in rows if row.account_id in ids],
                securities,
                prices,
            )
        )
    return {
        "schema": SCHEMA,
        "generator": "hearth",
        "generated_at": now.isoformat(),
        "amount_convention": AMOUNT_CONVENTION,
        "accounts": accounts,
    }


def write(path: Path, document: dict[str, object]) -> None:
    """Write the file whole, readable only by its owner, so a reader never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, partial = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(document, file, indent=2, ensure_ascii=False)
            file.write("\n")
        Path(partial).replace(path)
    except BaseException:
        Path(partial).unlink(missing_ok=True)
        raise


def _leg(side: pairing.Side) -> dict[str, object]:
    return {
        "side": side.value,
        "other_account": None,
        "other_name": None,
        "other_mask": None,
        "other_date": None,
    }


def _account(
    fed: _Fed,
    registry: Registry,
    feed: Feed,
    rows: list[tuple[Transaction, dict[str, object] | None]],
    securities: dict[str, Security],
    prices: PriceStore,
) -> dict[str, object]:
    """One account: the working connection speaks for it when two have fed it."""
    items = {item.item_id: item for item in registry.all_items()}
    speaks = next((x for x in reversed(fed.linked) if items[x.item_id].active), fed.linked[-1])
    item = items[speaks.item_id]
    invested = speaks.is_investment
    activity = (
        sorted(
            (a for linked in fed.linked for a in feed.activity(linked.account_id)),
            key=lambda a: (a.day, a.activity_id),
        )
        if invested
        else []
    )
    through = feed.activity_through(speaks.item_id) if invested else None
    report = feed.holdings(speaks.account_id) if invested else None
    return {
        "ledger_account": fed.ledger_account,
        "account_id": speaks.account_id,
        "account_name": speaks.name,
        "account_type": speaks.type,
        "account_subtype": speaks.subtype,
        "mask": speaks.mask,
        "institution_id": item.institution_id,
        "institution_name": item.institution,
        "connection": _connection(item),
        "balance": _balance(feed.balance(speaks.account_id)),
        "transaction_count": len(rows),
        "transactions": [_row(row, pair) for row, pair in rows],
        "activity_through": through.isoformat() if through else None,
        "activity_count": len(activity),
        "activity": [_activity(a, securities, prices) for a in activity],
        "holdings": None
        if report is None
        else {
            "reported_at": report.reported_at.isoformat(),
            "positions": [_position(p) for p in report.positions],
        },
    }


def _activity(
    found: Activity, securities: dict[str, Security], prices: PriceStore
) -> dict[str, object]:
    security = securities.get(found.security_id) if found.security_id else None
    ticker = security.ticker if security else None
    priced = prices.revisions(ticker, found.day, PRICE_SOURCE) if ticker else []
    return {
        "activity_id": found.activity_id,
        "account_id": found.account_id,
        "date": found.day.isoformat(),
        "name": found.name,
        "type": found.type,
        "subtype": found.subtype,
        "security_id": found.security_id,
        "ticker": ticker,
        "security_name": security.name if security else None,
        "security_type": security.type if security else None,
        "quantity": _maybe(found.quantity),
        "price": _maybe(found.price),
        "amount": _maybe(found.amount),
        "fees": _maybe(found.fees),
        "currency": found.currency,
        "cancels": found.cancels,
        "market_price": _text(priced[-1].price) if priced else None,
    }


def _position(held: Position) -> dict[str, object]:
    return {
        "security_id": held.security_id,
        "ticker": held.ticker,
        "name": held.name,
        "cash": held.cash,
        "quantity": _text(held.quantity),
        "value": _maybe(held.value),
        "cost_basis": _maybe(held.cost_basis),
        "currency": held.currency,
    }


def _connection(item: Item) -> dict[str, object]:
    synced = item.last_synced_at
    return {
        "state": item.state.value,
        "last_synced_at": synced.isoformat() if synced else None,
        "problem": item.problem,
    }


def _balance(balance: Balance | None) -> dict[str, object] | None:
    if balance is None or balance.current is None:
        return None
    return {
        "current": _text(balance.current),
        "currency": balance.currency,
        "recorded_at": balance.recorded_at.isoformat(),
    }


def _row(row: Transaction, pair: dict[str, object] | None) -> dict[str, object]:
    return {
        "transaction_id": row.transaction_id,
        "account_id": row.account_id,
        "pending_transaction_id": row.pending_transaction_id,
        "date": row.day.isoformat(),
        "authorized_date": row.authorized_date.isoformat() if row.authorized_date else None,
        "amount": _text(row.amount),
        "currency": row.currency,
        "name": row.name,
        "merchant_name": row.merchant_name,
        "original_description": row.original_description,
        "category": row.category,
        "pending": False,
        "pair": pair,
    }


def _text(amount: Decimal) -> str:
    """A decimal as its digits, never through a float."""
    return format(amount, "f")


def _maybe(amount: Decimal | None) -> str | None:
    return None if amount is None else _text(amount)


def default_path(data_dir: Path) -> Path:
    """Where the file goes unless the command is told otherwise."""
    return data_dir / "export.json"
