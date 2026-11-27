"""What the household holds: positions read from the books.

Holdings live in the books, like every other financial fact. A security is a commodity, held
as postings on an Assets account: a ledger's lots at cost, or what a brokerage reported in
hearth's own books (``hearth.plaid.opening``). The units held on a day are the sum of those
postings up to that day. The books record quantities, and what the lots of a ledger cost;
what they are worth comes from stored prices (see `hearth.valuation.networth`).
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from beancount.core.amount import Amount
from beancount.core.data import Directive, Transaction
from beancount.core.position import Cost

from hearth.reports.common import is_balance_sheet, units


@dataclass(frozen=True, slots=True)
class Holding:
    """A quantity of one commodity in one account. Cash is a holding of its currency."""

    account: str
    commodity: str
    units: Decimal
    cost: Amount | None = None
    """What the units held cost, when every posting that holds them is a lot at cost in one
    currency, as a ledger's purchases are. None when any is not: what a brokerage reported in
    hearth's own books says nothing of cost, and cash is held at no cost at all."""


@dataclass
class _Held:
    units: Decimal = Decimal(0)
    cost: Decimal = Decimal(0)
    cost_currency: str | None = None
    at_cost: bool = True


def holdings_at(entries: Iterable[Directive], as_of: date) -> list[Holding]:
    """Every non-zero position on a balance-sheet account at the end of `as_of`, with what it
    cost when the books hold that (`Holding.cost`)."""
    held: defaultdict[tuple[str, str], _Held] = defaultdict(_Held)
    for entry in entries:
        if not isinstance(entry, Transaction) or entry.date > as_of:
            continue
        for posting in entry.postings:
            if not is_balance_sheet(posting.account):
                continue
            amount = units(posting, entry)
            found = held[posting.account, amount.currency]
            found.units += amount.number
            lot = posting.cost
            if (
                isinstance(lot, Cost)
                and isinstance(lot.number, Decimal)
                and found.cost_currency in (None, lot.currency)
            ):
                found.cost += amount.number * lot.number
                found.cost_currency = lot.currency
            else:
                found.at_cost = False
    return [
        Holding(
            account=account,
            commodity=commodity,
            units=found.units,
            cost=(
                Amount(found.cost, found.cost_currency)
                if found.at_cost and found.cost_currency is not None
                else None
            ),
        )
        for (account, commodity), found in sorted(held.items())
        if found.units != 0
    ]
