"""What each connected account held before the history its bank sent: its opening balance.

A bank sends a window of history, not an account's life, so its rows added up are what moved
over the window, not what the account holds (``books.unanchored``). With every sync it also
reports what the account holds, its current balance (``feed.Balance``). What the account held
before the window is that balance less every row the window holds:

    opening = current balance - the rows booked for the account, in the books' sign

It is written as an opening entry against `EQUITY` the day before the first of those rows,
or, while none has arrived, on the day the balance was reported; and the current balance as a
`balance` assertion the day after the later of the day it was reported and the last row, as
beancount reads an assertion as the balance at the start of its day. A card's current
balance is what is owed, so on a Liabilities account it is asserted negated. Both carry where
they came from: which balance, reported by whom, and when.

The assertion is the proof. The opening is derived from the rows as the bank sent them; the
books hold entries made of those rows by pairing, by a newer connection taking over from an
older one, and by every ruling members make, and the recompute checks the assertion against
those (``books.compute``). Books that do not add up to what the bank says are wrong somewhere,
and the recompute says so through its failed balance check rather than serve a figure.

Plaid's current balance leaves pending rows out. That was measured, not read: on a real
household's five cards, the balance derived this way reproduced every statement balance
there was to the cent with pending rows left out, and missed each by exactly that card's
pending total with them in. So the opening is derived from posted rows alone, which
are exactly the rows the books hold.

An opening that cannot be derived is never guessed. The account's balance stays not known,
never zero, and `Unknown` says why, so the account's page can.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from beancount.core.amount import Amount
from beancount.core.data import (
    EMPTY_SET,
    Balance,
    Directive,
    Posting,
    Transaction,
    new_metadata,
)

from hearth.connections import plaid_balance_source
from hearth.plaid import booking
from hearth.plaid.client import Transaction as Row
from hearth.plaid.feed import Feed, Outcome
from hearth.plaid.registry import Item, LinkedAccount, Registry

EQUITY = "Equity:Opening-Balances"
"""What an opening balance is balanced against: beancount's own name for it."""

NARRATION = "Opening balance"

_PROVENANCE = "balance"
"""Which of the balances a bank reports an anchor was derived from: Plaid's current balance."""

BALANCED = frozenset({"depository", "credit", "loan"})
"""Kinds of account whose current balance is money held or owed, which their rows move."""


class Unknown(StrEnum):
    """Why an account's opening balance could not be derived."""

    INVESTMENT = "investment"
    """An investment account's balance is what its holdings are worth, which its rows do not
    move: Plaid sends no investment transactions without its Investments product."""
    KIND = "kind"
    """An account of a kind whose balance is not plainly money held or owed."""
    SHARED = "shared"
    """More than one real bank account feeds the ledger account, so no one balance is its."""
    NO_BALANCE = "no-balance"
    """The bank has not reported what the account holds."""
    UNFINISHED = "unfinished"
    """The sync that reported the balance has not finished, so the rows may not be all of
    those the balance includes."""
    CURRENCY = "currency"
    """The balance is not in the currency of the account's rows."""
    GAP = "gap"
    """The account was connected again, and the rows between its connections are not all in."""


PASSING = frozenset({Unknown.NO_BALANCE, Unknown.UNFINISHED, Unknown.CURRENCY, Unknown.GAP})
"""Reasons an opening is not known yet that a later sync can clear: an account unknown only
for one of these is, like one anchored, an account whose balance is, or will be, its bank's."""


@dataclass(frozen=True, slots=True)
class Anchor:
    """An account's opening balance, and the balance its bank reported, as the books hold them."""

    account: str
    day: date
    """The day before the first row booked for the account, or `as_of` if there is none yet:
    the opening is what it held at the end of it."""
    opening: Amount
    reported: Amount
    """The bank's current balance, in the books' sign."""
    as_of: date
    """The last day the reported balance covers: the later of the day it was reported and the
    day of the last row booked for the account."""
    reported_at: datetime
    institution: str
    item_id: str

    def entries(self) -> tuple[Transaction, Balance]:
        """The opening entry, and the assertion of the balance the bank reported."""
        opening = Transaction(
            {**self._provenance(), "source": plaid_balance_source(self.item_id)},
            self.day,
            "*",
            None,
            NARRATION,
            EMPTY_SET,
            EMPTY_SET,
            [
                Posting(self.account, self.opening, None, None, None, None),
                Posting(EQUITY, -self.opening, None, None, None, None),
            ],
        )
        asserted = Balance(
            self._provenance(),
            self.as_of + timedelta(days=1),
            self.account,
            self.reported,
            # Written `~ 0`: beancount would otherwise infer a tolerance from how many places
            # Plaid gave, five cents for a round `9120.0`. Checked exactly, as hearth does.
            Decimal(0),
            None,
        )
        return opening, asserted

    def _provenance(self) -> dict[str, object]:
        # No `source` on the assertion: it is dated after the sync it came from, and a
        # connection vouches only for what the books hold up to its sync (`books.vouched`).
        meta = new_metadata(booking.ORIGIN, 0)
        meta[_PROVENANCE] = "current"
        meta["reported-by"] = self.institution
        meta["reported-at"] = self.reported_at.isoformat()
        return meta


def anchors(entry: Directive) -> bool:
    """Whether `entry` is one of an `Anchor`'s: an opening balance or the balance it was
    derived from."""
    return isinstance(entry, Transaction | Balance) and _PROVENANCE in (entry.meta or {})


@dataclass(frozen=True, slots=True)
class Openings:
    anchors: tuple[Anchor, ...]
    unknown: Mapping[str, Unknown]
    """Each ledger account whose opening could not be derived, and why."""


def derive(registry: Registry, feed: Feed, booked: booking.Booked) -> Openings:
    """Every ledger account the import feeds, anchored or with the reason it is not."""
    anchors: list[Anchor] = []
    unknown: dict[str, Unknown] = {}
    twice = [{a.account_id for a in group} for group in registry.connected_twice()]
    for account, linked in sorted(booked.fed.items()):
        found = _anchor(registry, feed, account, linked, booked.taken[account], twice)
        if isinstance(found, Anchor):
            anchors.append(found)
        elif found is not None:
            unknown[account] = found
    return Openings(tuple(anchors), unknown)


def _anchor(
    registry: Registry,
    feed: Feed,
    account: str,
    linked: Sequence[LinkedAccount],
    rows: Sequence[Row],
    twice: Sequence[set[str]],
) -> Anchor | Unknown | None:
    items = {a.item_id: registry.item(a.item_id) for a in linked}
    newest = max(linked, key=lambda a: _newness(items[a.item_id]))
    item = items[newest.item_id]
    if newest.is_investment:
        return Unknown.INVESTMENT
    if newest.type not in BALANCED:
        return Unknown.KIND
    if len(linked) > 1 and not any({a.account_id for a in linked} <= group for group in twice):
        return Unknown.SHARED
    balance = feed.balance(newest.account_id)
    if item is None or balance is None or balance.current is None:
        return Unknown.NO_BALANCE
    run = feed.recorded_by(balance, item.item_id)
    if run is None or run.outcome is not Outcome.COMPLETE:
        return Unknown.UNFINISHED
    if not balance.currency or any(row.currency != balance.currency for row in rows):
        return Unknown.CURRENCY
    if _gap(newest, linked, items, rows):
        return Unknown.GAP
    as_of = max([balance.recorded_at.astimezone().date(), *(row.day for row in rows)])
    # With no rows yet, all that is known is what it holds now, not what it held before.
    day = min(row.day for row in rows) - timedelta(days=1) if rows else as_of
    reported = balance.current if newest.type == "depository" else -balance.current
    moved = sum((-row.amount for row in rows), Decimal(0))
    return Anchor(
        account=account,
        day=day,
        opening=Amount(reported - moved, balance.currency),
        reported=Amount(reported, balance.currency),
        as_of=as_of,
        reported_at=balance.recorded_at,
        institution=item.institution,
        item_id=item.item_id,
    )


def _newness(item: Item | None) -> tuple[bool, datetime]:
    """The working connection first, then the one linked later, as the import gives way."""
    if item is None:
        return (False, datetime.min.replace(tzinfo=UTC))
    return (item.active, item.linked_at)


def _gap(
    newest: LinkedAccount,
    linked: Sequence[LinkedAccount],
    items: Mapping[str, Item | None],
    rows: Sequence[Row],
) -> bool:
    """Whether rows may be missing between an older connection's last sync and the first row
    the newest one sent: the newest one's history does not yet reach back to where the older
    one stopped."""
    older = {row.account_id for row in rows if row.account_id != newest.account_id}
    if not older:
        return False
    own = [row.day for row in rows if row.account_id == newest.account_id]
    if not own:
        return True
    for account in linked:
        item = items[account.item_id]
        if account.account_id not in older or item is None:
            continue
        stopped = item.last_synced_at.astimezone().date() if item.last_synced_at else None
        if stopped is None or stopped < min(own) - timedelta(days=1):
            return True
    return False
