"""What the household holds: positions read from the books.

Holdings live in the books, like every other financial fact. A security is a commodity, held
as postings on an Assets account: a ledger's lots at cost, or what a brokerage reported in
hearth's own books (``hearth.plaid.opening``). The units held on a day are the sum of those
postings up to that day. The books record quantities only; what they are worth comes from
stored prices (see `hearth.valuation.networth`).
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from beancount.core.data import Directive, Transaction

from hearth.reports.common import is_balance_sheet, units


@dataclass(frozen=True, slots=True)
class Holding:
    """A quantity of one commodity in one account. Cash is a holding of its currency."""

    account: str
    commodity: str
    units: Decimal


def holdings_at(entries: Iterable[Directive], as_of: date) -> list[Holding]:
    """Every non-zero position on a balance-sheet account at the end of `as_of`."""
    held: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for entry in entries:
        if not isinstance(entry, Transaction) or entry.date > as_of:
            continue
        for posting in entry.postings:
            if not is_balance_sheet(posting.account):
                continue
            amount = units(posting, entry)
            held[posting.account, amount.currency] += amount.number
    return [
        Holding(account=account, commodity=commodity, units=quantity)
        for (account, commodity), quantity in sorted(held.items())
        if quantity != 0
    ]
