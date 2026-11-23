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

An investment account is anchored the same way at what its brokerage last said it holds
(``Feed.holdings``), not at its current balance: that balance is what its holdings are worth
at prices hearth keeps apart (``docs/valuation.md``), not units of anything. Each security is
held as a commodity, named by its ticker or made from its Plaid security
(``valuation.institution.commodity``), and the account's cash as money. Plaid sends no trades,
so nothing says how the account came to hold them, or what it held on any day before it was
asked: the whole anchor is dated on the day the brokerage reported it, and every day before
reads as not known rather than as an account that held nothing (``books.positioned``). Any
rows that did reach it are money, and its cash is derived from them as a bank account's is.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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
from hearth.plaid.feed import Balance as Reported
from hearth.plaid.feed import Feed, Outcome, Report
from hearth.plaid.registry import Item, LinkedAccount, Registry
from hearth.review.ids import reconciling_review_id
from hearth.review.pile import LEDGER_BANK, RECONCILING
from hearth.valuation.institution import commodity

EQUITY = "Equity:Opening-Balances"
"""What an opening balance is balanced against: beancount's own name for it."""

NARRATION = "Opening balance"
HELD = "Holdings as reported"

_PROVENANCE = "balance"
"""Which of the balances a bank reports an anchor was derived from (`Basis`)."""


class Basis(StrEnum):
    """What a bank reported that an anchor was derived from."""

    CURRENT = "current"
    """Plaid's current balance: the money an account holds, or owes."""
    HOLDINGS = "holdings"
    """What a brokerage said an investment account holds (``Feed.holdings``)."""
    DIFFERENCE = "difference"
    """What a carried account's current balance and the base it is carried on from disagree
    by, as of its cut-over (`Carrying`)."""


BALANCED = frozenset({"depository", "credit", "loan"})
"""Kinds of account whose current balance is money held or owed, which their rows move."""


class Unknown(StrEnum):
    """Why an account's opening balance could not be derived."""

    INVESTMENT = "investment"
    """An investment account whose brokerage has not said what it holds yet. Its balance is
    what its holdings are worth, which no row moves, so only its holdings can anchor it, and
    hearth asks for them only once reading holdings is approved (``hearth.plaid.holdings``)."""
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


PASSING = frozenset(
    {Unknown.INVESTMENT, Unknown.NO_BALANCE, Unknown.UNFINISHED, Unknown.CURRENCY, Unknown.GAP}
)
"""Reasons an opening is not known yet that a later sync, or a later ask for a brokerage's
holdings, can clear: an account unknown only for one of these is, like one anchored, an
account whose balance is, or will be, its bank's."""


@dataclass(frozen=True, slots=True)
class Anchor:
    """What an account held before its history, and what its bank reported it holds, as the
    books hold them: an amount of each commodity it holds, which for a bank account is one."""

    account: str
    day: date
    """The day before the first row booked for the account, or `as_of` if there is none yet:
    the opening is what it held at the end of it. For holdings, the day they were reported."""
    opening: tuple[Amount, ...]
    reported: tuple[Amount, ...]
    """The bank's current balance, in the books' sign, or each holding a brokerage reported."""
    as_of: date
    """The last day the reported balance covers: the later of the day it was reported and the
    day of the last row booked for the account. For holdings, the day they were reported, as
    a brokerage's report says nothing of rows after it."""
    reported_at: datetime
    institution: str
    item_id: str
    basis: Basis = Basis.CURRENT

    def entries(self) -> tuple[Transaction | Balance, ...]:
        """The opening entry, and the assertion of each amount the bank reported."""
        opening = Transaction(
            {**self._provenance(), "source": plaid_balance_source(self.item_id)},
            self.day,
            "*",
            None,
            HELD if self.basis is Basis.HOLDINGS else NARRATION,
            EMPTY_SET,
            EMPTY_SET,
            [
                *(Posting(self.account, amount, None, None, None, None) for amount in self.opening),
                *(Posting(EQUITY, -amount, None, None, None, None) for amount in self.opening),
            ],
        )
        asserted = tuple(
            Balance(
                self._provenance(),
                self.as_of + timedelta(days=1),
                self.account,
                amount,
                # Written `~ 0`: beancount would otherwise infer a tolerance from how many
                # places Plaid gave, five cents for a round `9120.0`. Checked exactly, as
                # hearth does.
                Decimal(0),
                None,
            )
            for amount in self.reported
        )
        return (opening, *asserted)

    def _provenance(self) -> dict[str, object]:
        return _provenance(self.basis, self.institution, self.reported_at)


def _provenance(basis: Basis, institution: str, reported_at: datetime) -> dict[str, object]:
    # No `source` on an assertion: it is dated after the sync it came from, and a connection
    # vouches only for what the books hold up to its sync (`books.vouched`).
    meta = new_metadata(booking.ORIGIN, 0)
    meta[_PROVENANCE] = basis.value
    meta["reported-by"] = institution
    meta["reported-at"] = reported_at.isoformat()
    return meta


def anchors(entry: Directive) -> bool:
    """Whether `entry` is one of an `Anchor`'s: an opening balance or the balance it was
    derived from."""
    return isinstance(entry, Transaction | Balance) and _PROVENANCE in (entry.meta or {})


def held(entry: Directive) -> bool:
    """Whether `entry` is one of the anchor of what a brokerage reported an account holds."""
    return anchors(entry) and (entry.meta or {}).get(_PROVENANCE) == Basis.HOLDINGS.value


@dataclass(frozen=True, slots=True)
class Openings:
    anchors: tuple[Anchor, ...]
    unknown: Mapping[str, Unknown]
    """Each ledger account whose opening could not be derived, and why."""
    invested: frozenset[str] = frozenset()
    """Each ledger account an investment account feeds, anchored or not."""
    securities: Mapping[str, str] = field(default_factory=dict)
    """What each security an anchor holds is called, by the commodity it is held as."""


def derive(registry: Registry, feed: Feed, booked: booking.Booked) -> Openings:
    """Every ledger account the import feeds, anchored or with the reason it is not."""
    anchors: list[Anchor] = []
    unknown: dict[str, Unknown] = {}
    invested: set[str] = set()
    securities: dict[str, str] = {}
    twice = [{a.account_id for a in group} for group in registry.connected_twice()]
    for account, linked in sorted(booked.fed.items()):
        found = _anchor(registry, feed, account, linked, booked.taken[account], twice)
        if isinstance(found, Invested):
            invested.add(account)
            securities = {**found.securities, **securities}
            found = found.anchor
        if isinstance(found, Anchor):
            anchors.append(found)
        elif found is not None:
            unknown[account] = found
            if found is Unknown.INVESTMENT:
                invested.add(account)
    return Openings(tuple(anchors), unknown, frozenset(invested), securities)


@dataclass(frozen=True, slots=True)
class Invested:
    """An investment account's anchor, or why it has none, and what its securities are called."""

    anchor: Anchor | Unknown
    securities: Mapping[str, str] = field(default_factory=dict)


def _anchor(
    registry: Registry,
    feed: Feed,
    account: str,
    linked: Sequence[LinkedAccount],
    rows: Sequence[Row],
    twice: Sequence[set[str]],
) -> Anchor | Invested | Unknown | None:
    items = {a.item_id: registry.item(a.item_id) for a in linked}
    newest = max(linked, key=lambda a: _newness(items[a.item_id]))
    item = items[newest.item_id]
    if not newest.is_investment and newest.type not in BALANCED:
        return Unknown.KIND
    if len(linked) > 1 and not any({a.account_id for a in linked} <= group for group in twice):
        return Invested(Unknown.SHARED) if newest.is_investment else Unknown.SHARED
    if newest.is_investment:
        report = feed.holdings(newest.account_id)
        if item is None or report is None:
            return Invested(Unknown.INVESTMENT)
        return _holdings(account, report, rows, item, feed)
    found = _current(feed, newest, item, linked, items, rows)
    if isinstance(found, Unknown):
        return found
    balance, current, currency = found
    as_of = max([balance.recorded_at.astimezone().date(), *(row.day for row in rows)])
    # With no rows yet, all that is known is what it holds now, not what it held before.
    day = min(row.day for row in rows) - timedelta(days=1) if rows else as_of
    reported = current if newest.type == "depository" else -current
    moved = sum((-row.amount for row in rows), Decimal(0))
    assert item is not None  # noqa: S101 - `_current` found its balance through it
    return Anchor(
        account=account,
        day=day,
        opening=(Amount(reported - moved, currency),),
        reported=(Amount(reported, currency),),
        as_of=as_of,
        reported_at=balance.recorded_at,
        institution=item.institution,
        item_id=item.item_id,
    )


def _current(
    feed: Feed,
    newest: LinkedAccount,
    item: Item | None,
    linked: Sequence[LinkedAccount],
    items: Mapping[str, Item | None],
    rows: Sequence[Row],
) -> tuple[Reported, Decimal, str] | Unknown:
    """The current balance the newest connection's bank last reported, with its figure, when
    it can be held against `rows`, or why it cannot."""
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
    return balance, balance.current, balance.currency


@dataclass(frozen=True, slots=True)
class Carrying:
    """What proves an account hearth carries on from the base (``hearth.carrying``): its
    bank's current balance, asserted against the base's balance at the cut-over and every row
    booked since; and, where they do not agree, the one entry that reconciles them.

    It has no opening: the account's history before its cut-over is the base's, which already
    says what it held. The difference is derived on every recompute from three things alone,
    the base's balance at the cut-over, the rows booked since and the bank's current balance:

        difference = current balance - the base's balance at the cut-over - the rows since

    Since the current balance less the rows since is what the bank says the account held at
    the start of the cut-over, the difference is what the ledger and the bank disagree by on
    that day. It stays the same from sync to sync, as each new row moves the bank's balance and
    the rows since alike, and shrinks or vanishes as its cause is put right: a row the bank
    counts that had not arrived yet arriving, say. It is never a second entry: there is one
    for the account, dated its cut-over, with a review id made of the account and that day.
    """

    account: str
    cut_over: date
    reported: Amount
    difference: Decimal
    as_of: date
    reported_at: datetime
    institution: str
    item_id: str
    unseen: tuple[date, date] | None = None
    """Days from the cut-over that neither the base nor the bank's history holds
    (``carrying.Carry.unseen``): what moved in them is part of the difference."""

    def entries(self) -> tuple[Transaction | Balance, ...]:
        """The reconciling entry, if the two disagree, and the assertion of the bank's
        balance, which the books are checked against like any connected account's."""
        asserted = Balance(
            _provenance(Basis.CURRENT, self.institution, self.reported_at),
            self.as_of + timedelta(days=1),
            self.account,
            self.reported,
            Decimal(0),
            None,
        )
        if not self.difference:
            return (asserted,)
        meta = {
            **_provenance(Basis.DIFFERENCE, self.institution, self.reported_at),
            "review-id": reconciling_review_id(self.account, self.cut_over),
            "review-group": RECONCILING,
            "source": plaid_balance_source(self.item_id),
        }
        units = Amount(self.difference, self.reported.currency)
        reconciling = Transaction(
            meta,
            self.cut_over,
            booking.FLAGGED,
            None,
            f"The imported ledger and {self.institution} disagree by this much as of"
            f" {_day(self.cut_over)}{self._unseen()}",
            EMPTY_SET,
            EMPTY_SET,
            [
                Posting(self.account, units, None, None, None, None),
                Posting(LEDGER_BANK, -units, None, None, None, None),
            ],
        )
        return (reconciling, asserted)

    def _unseen(self) -> str:
        if self.unseen is None:
            return ""
        first, last = self.unseen
        if last == date.max or last < first:
            return f", including everything since, which {self.institution} has not sent"
        return (
            f", including what moved from {_day(first)} to {_day(last)}, which neither the"
            f" ledger nor {self.institution} holds"
        )


@dataclass(frozen=True, slots=True)
class Carried:
    """Every carried account's proof, and each carried account whose bank's balance cannot
    prove it yet, with why: its balance is then the base's and the rows since, unproved."""

    proofs: tuple[Carrying, ...]
    unknown: Mapping[str, Unknown]


def carried(registry: Registry, feed: Feed, booked: booking.Booked) -> Carried:
    """The proof of every account the import carries on from the base (`Carrying`)."""
    proofs: list[Carrying] = []
    unknown: dict[str, Unknown] = {}
    twice = [{a.account_id for a in group} for group in registry.connected_twice()]
    for account, kept in sorted(booked.carried.items()):
        linked, rows = kept.fed, kept.taken
        items = {a.item_id: registry.item(a.item_id) for a in linked}
        newest = max(linked, key=lambda a: _newness(items[a.item_id]))
        item = items[newest.item_id]
        if len(linked) > 1 and not any({a.account_id for a in linked} <= group for group in twice):
            unknown[account] = Unknown.SHARED
            continue
        found = _current(feed, newest, item, linked, items, rows)
        if isinstance(found, Unknown):
            unknown[account] = found
            continue
        assert item is not None  # noqa: S101 - `_current` found its balance through it
        balance, current, currency = found
        reported = current if newest.type == "depository" else -current
        held = kept.carry.opening.get(currency, Decimal(0))
        moved = sum((-row.amount for row in rows), Decimal(0))
        cut_over = kept.carry.cut_over
        proofs.append(
            Carrying(
                account=account,
                cut_over=cut_over,
                reported=Amount(reported, currency),
                difference=reported - held - moved,
                # On or after the cut-over, so the reconciling entry is inside what it asserts.
                as_of=max(
                    [balance.recorded_at.astimezone().date(), cut_over, *(r.day for r in rows)]
                ),
                reported_at=balance.recorded_at,
                institution=item.institution,
                item_id=item.item_id,
                unseen=kept.carry.unseen,
            )
        )
    return Carried(tuple(proofs), unknown)


def _day(day: date) -> str:
    return f"{day:%b} {day.day}, {day.year}"


def reconciling(entry: Directive) -> bool:
    """Whether `entry` is the one that reconciles a carried account's base with its bank."""
    return anchors(entry) and (entry.meta or {}).get(_PROVENANCE) == Basis.DIFFERENCE.value


def _holdings(
    account: str, report: Report, rows: Sequence[Row], item: Item, feed: Feed
) -> Invested:
    """An investment account anchored at what its brokerage last said it holds.

    Each security is held as its commodity, and cash, and money as good as cash, as money at
    what the brokerage says it is worth. One whose worth the brokerage did not say is held
    as a security instead, so it is valued at a price or not at all, never at a guess. Rows
    on the account are money, and its cash before them is derived as a bank account's is,
    from the rows up to the day of the report: a row after it is not in the report, so it is
    added to what the report says rather than taken from the opening.
    """
    if any(not row.currency for row in rows):
        return Invested(Unknown.CURRENCY)
    reported: defaultdict[str, Decimal] = defaultdict(Decimal)
    securities: dict[str, str] = {}
    for position in report.positions:
        if position.cash and position.value is not None and position.currency:
            reported[position.currency] += position.value
            continue
        security = commodity(position.security_id, position.ticker)
        reported[security] += position.quantity
        if position.name:
            securities[security] = position.name
    day = report.reported_at.astimezone().date()
    moved: defaultdict[str, Decimal] = defaultdict(Decimal)
    for row in rows:
        if row.day <= day:
            moved[str(row.currency)] -= row.amount
    if not reported and not moved:
        # It holds nothing, as its brokerage said: a figure of zero someone established.
        balance = feed.balance(report.account_id)
        reported[(balance.currency if balance else None) or "USD"] = Decimal(0)
    held = sorted({*reported, *moved})
    return Invested(
        Anchor(
            account=account,
            day=day,
            opening=tuple(Amount(reported[c] - moved[c], c) for c in held),
            reported=tuple(Amount(reported[c], c) for c in held),
            as_of=day,
            reported_at=report.reported_at,
            institution=item.institution,
            item_id=item.item_id,
            basis=Basis.HOLDINGS,
        ),
        securities,
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
