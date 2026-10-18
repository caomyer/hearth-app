"""Per-account balances, with the ledger's own balance assertions as evidence.

A beancount `balance` assertion states an account's balance at the start of its day, including
its sub-accounts, usually copied from a bank statement. Every assertion is recomputed here from
the postings, independently of beancount's own check, so a balance hearth shows is one that
reproduces the statements, and an account whose balance does not is reported as such.
"""

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import accumulate

from beancount.core.data import Balance, Close, Directive, Open, Transaction

from hearth.reports.common import is_balance_sheet, units


@dataclass(frozen=True, slots=True)
class AssertionCheck:
    account: str
    date: date
    currency: str
    asserted: Decimal
    computed: Decimal

    @property
    def agrees(self) -> bool:
        return self.asserted == self.computed


@dataclass(frozen=True, slots=True)
class AccountBalance:
    """An account's balance at the end of the as-of day.

    Who owns the account is the app database's to say (`hearth.accounts`). `last_verified` is the
    latest assertion on or before the day that the postings reproduce, and `disagreements`
    lists every one on or before the day that they do not; while there are any, the balance
    is not trustworthy and must not be shown as though it were.
    """

    account: str
    units: dict[str, Decimal]
    last_verified: date | None
    disagreements: tuple[AssertionCheck, ...]

    @property
    def verified(self) -> bool:
        return not self.disagreements


def _covers(asserted_account: str, account: str) -> bool:
    return account == asserted_account or account.startswith(asserted_account + ":")


def check_balance_assertions(entries: Iterable[Directive]) -> list[AssertionCheck]:
    """Recompute every balance assertion from the postings dated before it.

    An assertion is compared exactly: statements give balances to the cent, and a tolerance
    would let a missing or doubled small transaction pass.
    """
    by_account: defaultdict[tuple[str, str], list[tuple[date, Decimal]]] = defaultdict(list)
    assertions: list[Balance] = []
    for entry in entries:
        if isinstance(entry, Transaction):
            for posting in entry.postings:
                amount = units(posting, entry)
                by_account[posting.account, amount.currency].append((entry.date, amount.number))
        elif isinstance(entry, Balance):
            assertions.append(entry)
    # Per account and currency: posting dates in order, and the running total after each.
    running: dict[tuple[str, str], tuple[list[date], list[Decimal]]] = {}
    for key, postings in by_account.items():
        postings.sort(key=lambda posting: posting[0])
        days = [day for day, _ in postings]
        totals = list(accumulate((number for _, number in postings), initial=Decimal(0)))
        running[key] = (days, totals)
    checks = []
    for assertion in assertions:
        asserted = assertion.amount
        if not isinstance(asserted.number, Decimal):
            raise ValueError(f"balance assertion on {assertion.account} has no number")
        computed = Decimal(0)
        for (account, currency), (days, totals) in running.items():
            if currency == asserted.currency and _covers(assertion.account, account):
                computed += totals[bisect_left(days, assertion.date)]
        checks.append(
            AssertionCheck(
                assertion.account, assertion.date, asserted.currency, asserted.number, computed
            )
        )
    return checks


def account_balances(entries: Sequence[Directive], *, as_of: date) -> list[AccountBalance]:
    """Every Assets and Liabilities account open at the end of `as_of`, with its balance.

    Units are per commodity: cash in its currency, securities in shares, never valued here
    (see `hearth.valuation`). An open account with nothing in it has empty `units`.
    """
    opened: dict[str, date] = {}
    closed: dict[str, date] = {}
    totals: defaultdict[str, defaultdict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for entry in entries:
        if isinstance(entry, Open):
            opened[entry.account] = entry.date
        elif isinstance(entry, Close):
            closed[entry.account] = entry.date
        elif isinstance(entry, Transaction) and entry.date <= as_of:
            for posting in entry.postings:
                amount = units(posting, entry)
                totals[posting.account][amount.currency] += amount.number
    checks: defaultdict[str, list[AssertionCheck]] = defaultdict(list)
    for check in check_balance_assertions(entries):
        if check.date <= as_of:
            checks[check.account].append(check)
    balances = []
    for account in sorted(opened):
        if not is_balance_sheet(account) or opened[account] > as_of:
            continue
        if account in closed and closed[account] <= as_of:
            continue
        mine = checks[account]
        agreeing = [c.date for c in mine if c.agrees]
        balances.append(
            AccountBalance(
                account=account,
                units={c: n for c, n in sorted(totals[account].items()) if n != 0},
                last_verified=max(agreeing, default=None),
                disagreements=tuple(c for c in mine if not c.agrees),
            )
        )
    return balances
