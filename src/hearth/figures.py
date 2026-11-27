"""Figures for one part of the household, read from the books.

A part is either one member's sole accounts or one group of joint owners' shared accounts.
Every household figure is a sum of parts, and each account belongs to exactly one part, so no
member figure contains any piece of a joint account (see ``docs/privacy-boundary.md``).

These functions compute; they never decide who may see the result. ``hearth.disclosure``
calls them only once ``hearth.access`` has said yes.
"""

import calendar
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from beancount.core.data import Transaction

from hearth.accounts import Account
from hearth.books import Books, uncategorised
from hearth.members import Member
from hearth.months import Month
from hearth.plaid.booking import moved
from hearth.plaid.opening import anchors
from hearth.reports.common import is_balance_sheet, units
from hearth.reports.flows import (
    Flows,
    Owners,
    OwnersOf,
    income_by_source,
    payers,
    spending_by_category,
)
from hearth.review.pile import TRANSFER_PENDING, UNRESOLVED
from hearth.valuation.networth import NetWorth

TYPICAL_MONTHS = 6
"""How many past months "typical" spending is the median of, for pace and categories."""

MIN_TYPICAL_MONTHS = 3
"""Fewer complete past months than this, and there is no typical to compare with."""

COMPARE_FROM_DAY = 8
"""The first day of the month in progress that is compared with a typical month. Before it,
a single purchase is most of the month so far, and setting a week's first days against
typical reads one payment as overspending."""


def compares(day: int) -> bool:
    """Whether the month in progress, `day` days in, is far enough along to compare."""
    return day >= COMPARE_FROM_DAY


QUIET_START = timedelta(days=10)
"""How long an account may go without a transaction at the start of a month and still count as
covered: a quiet week is normal, a history that starts weeks late is not."""


@dataclass(frozen=True, slots=True)
class Part:
    """Accounts that belong to the same owners: one member alone, or one joint group."""

    owners: Owners
    accounts: tuple[Account, ...]

    @property
    def is_joint(self) -> bool:
        return len(self.owners) > 1

    @property
    def ledger_accounts(self) -> frozenset[str]:
        return frozenset(account.ledger_account for account in self.accounts)


def parts(household: Sequence[Member], accounts: Sequence[Account]) -> list[Part]:
    """Every member's sole part, in household order, then every joint group.

    A member with no accounts still has a part, an empty one, so they appear in the
    household; an account nobody owns belongs to no part.
    """
    grouped: defaultdict[Owners, list[Account]] = defaultdict(list)
    for account in accounts:
        if account.owners:
            grouped[account.owners].append(account)
    solo = [Part(frozenset({m.id}), tuple(grouped.get(frozenset({m.id}), ()))) for m in household]
    joint = [Part(owners, tuple(found)) for owners, found in grouped.items() if len(owners) > 1]
    return solo + sorted(joint, key=lambda part: sorted(part.owners))


def ownership(accounts: Sequence[Account]) -> OwnersOf:
    """Who owns each ledger account, from the accounts table. An unclaimed account has no
    owner, and nothing it pays for is anyone's."""
    owners = {account.ledger_account: account.owners for account in accounts if account.owners}
    return owners.get


def worth(valuation: NetWorth, part: Part) -> NetWorth:
    """The positions of a household valuation that sit on the part's accounts."""
    mine = part.ledger_accounts
    positions = tuple(p for p in valuation.positions if p.holding.account in mine)
    return NetWorth(valuation.as_of, valuation.currency, positions)


@dataclass(frozen=True, slots=True)
class Unplaced:
    """Rows nothing says what they were yet, one way: how many, and how much money they moved
    that way. None of it is in any spending or income total."""

    rows: int = 0
    amount: Decimal = Decimal(0)

    def __add__(self, other: Unplaced) -> Unplaced:
        return Unplaced(self.rows + other.rows, self.amount + other.amount)


@dataclass(frozen=True, slots=True)
class MonthFlows:
    """One month of money in and out, for every part at once.

    `to_day` is set when only the month's first days count: the month in progress, or an
    earlier month cut to the same day so the two compare like with like. The `unreviewed`
    flows are the part of each that rests on rows nobody has checked yet, at the category the
    import suggested.
    """

    month: Month
    to_day: int | None
    spending: Flows
    income: Flows
    unreviewed_spending: Flows
    unreviewed_income: Flows
    unreviewed: Mapping[Owners, int]
    """How many unreviewed rows each part paid or received this month."""
    kept: Flows | None = None
    """What was booked to expense accounts that are not spending (``hearth.categories``), such
    as a retirement contribution: left out of `spending`, and so part of what was left over,
    as money kept somewhere else rather than spent. `spending`, `taxed` and `kept` together
    are every expense."""
    taxed: Flows | None = None
    """What was booked to expense accounts of kind tax: never spending, and never left over,
    but its own figure beside spending."""
    unreviewed_tax: Flows | None = None
    """Of `taxed`, what rests on rows nobody has checked yet, at their suggested category."""
    unplaced_out: Mapping[Owners, Unplaced] = field(default_factory=dict)
    """Each part's rows taking money out that wait for a member to say what they were
    (``books.uncategorised``), such as a new merchant's: in no spending total until placed."""
    unplaced_in: Mapping[Owners, Unplaced] = field(default_factory=dict)
    """The same, of rows bringing money in: in no income total until placed."""

    def kept_out(self, owners: Owners) -> Decimal:
        """What the part booked to expense accounts that are not spending."""
        found = self.kept.by_owners.get(owners, {}) if self.kept is not None else {}
        return sum(found.values(), Decimal(0))

    def taxed_out(self, owners: Owners) -> Decimal:
        """What the part booked to expense accounts of kind tax."""
        found = self.taxed.by_owners.get(owners, {}) if self.taxed is not None else {}
        return sum(found.values(), Decimal(0))

    def taxed_unreviewed(self, owners: Owners) -> Decimal:
        """Of `taxed_out`, what rests on rows nobody has checked yet."""
        found = self.unreviewed_tax.by_owners.get(owners, {}) if self.unreviewed_tax else {}
        return sum(found.values(), Decimal(0))

    def spent_unplaced(self, owners: Owners) -> Unplaced:
        return self.unplaced_out.get(owners, Unplaced())

    def earned_unplaced(self, owners: Owners) -> Unplaced:
        return self.unplaced_in.get(owners, Unplaced())

    def spent_by_category(self, owners: Owners) -> dict[str, Decimal]:
        return dict(self.spending.by_owners.get(owners, {}))

    def spent(self, owners: Owners) -> Decimal:
        return sum(self.spending.by_owners.get(owners, {}).values(), Decimal(0))

    def earned(self, owners: Owners) -> Decimal:
        return sum(self.income.by_owners.get(owners, {}).values(), Decimal(0))

    def spent_unreviewed(self, owners: Owners) -> Decimal:
        return sum(self.unreviewed_spending.by_owners.get(owners, {}).values(), Decimal(0))

    def earned_unreviewed(self, owners: Owners) -> Decimal:
        return sum(self.unreviewed_income.by_owners.get(owners, {}).values(), Decimal(0))


def month_flows(
    books: Books, ownership: OwnersOf, month: Month, *, to_day: int | None = None
) -> MonthFlows:
    """Spending by category, income and what of each is unreviewed for a month, by owners.

    With `to_day`, only the month's first `to_day` days count.
    """
    start = date(month.year, month.month, 1)
    last = calendar.monthrange(month.year, month.month)[1]
    end = start.replace(day=min(to_day or last, last))
    unreviewed: defaultdict[Owners, int] = defaultdict(int)
    unplaced_out: defaultdict[Owners, Unplaced] = defaultdict(Unplaced)
    unplaced_in: defaultdict[Owners, Unplaced] = defaultdict(Unplaced)
    flagged: list[Transaction] = []
    for entry in _transactions(books, start, end):
        if entry.flag != "!":
            continue
        flagged.append(entry)
        paid_by = payers(entry, ownership)
        if isinstance(paid_by, str):
            continue
        unreviewed[paid_by] += 1
        if uncategorised(entry):
            waiting = sum(
                (
                    units(p, entry).number
                    for p in entry.postings
                    if p.account == UNRESOLVED and units(p, entry).currency == books.currency
                ),
                Decimal(0),
            )
            if waiting > 0:
                unplaced_out[paid_by] += Unplaced(1, waiting)
            elif waiting < 0:
                unplaced_in[paid_by] += Unplaced(1, -waiting)
    currency = books.currency
    return MonthFlows(
        month=month,
        to_day=to_day,
        spending=spending_by_category(
            books.entries, ownership, start=start, end=end, currency=currency, counts=books.spends
        ),
        income=income_by_source(books.entries, ownership, start=start, end=end, currency=currency),
        unreviewed_spending=spending_by_category(
            flagged, ownership, start=start, end=end, currency=currency, counts=books.spends
        ),
        unreviewed_income=income_by_source(
            flagged, ownership, start=start, end=end, currency=currency
        ),
        unreviewed=dict(unreviewed),
        unplaced_out=dict(unplaced_out),
        unplaced_in=dict(unplaced_in),
        kept=spending_by_category(
            books.entries,
            ownership,
            start=start,
            end=end,
            currency=currency,
            counts=books.keeps,
        ),
        taxed=spending_by_category(
            books.entries, ownership, start=start, end=end, currency=currency, counts=books.taxes
        ),
        unreviewed_tax=spending_by_category(
            flagged, ownership, start=start, end=end, currency=currency, counts=books.taxes
        ),
    )


def _transactions(books: Books, start: date, end: date) -> Iterable[Transaction]:
    """Transactions from `start` to `end`: not an opening balance, which is what an account
    held before its history, never something that happened (``hearth.plaid.opening``)."""
    for entry in books.entries:
        if isinstance(entry, Transaction) and start <= entry.date <= end and not anchors(entry):
            yield entry


def past_months(month: Month, count: int) -> list[Month]:
    """The `count` months before `month`, oldest first."""
    months = []
    current = month
    for _ in range(count):
        current = current.previous()
        months.append(current)
    return months[::-1]


def typical(values: Sequence[Decimal]) -> Decimal | None:
    """The median, or None when there are too few months to call anything typical."""
    if len(values) < MIN_TYPICAL_MONTHS:
        return None
    return Decimal(statistics.median(values))


@dataclass(frozen=True, slots=True)
class Pace:
    """Cumulative spending by day: this month so far, and the usual shape of a month."""

    days_in_month: int
    today: int
    actual: tuple[Decimal, ...]
    typical: tuple[Decimal, ...]
    low: tuple[Decimal, ...]
    high: tuple[Decimal, ...]
    months_compared: int


def pace(books: Books, ownership: OwnersOf, owners: Owners, months: Sequence[Month]) -> Pace:
    """This month's cumulative spending against the median and middle half of `months`.

    The typical series are empty when there are too few complete months to compare with.
    """
    month = books.month
    days = calendar.monthrange(month.year, month.month)[1]
    actual = _cumulative(books, ownership, owners, month, books.as_of.day)
    history = [_cumulative(books, ownership, owners, m, days) for m in months]
    if len(history) < MIN_TYPICAL_MONTHS:
        return Pace(days, books.as_of.day, actual, (), (), (), len(history))
    by_day = list(zip(*history, strict=True))
    return Pace(
        days_in_month=days,
        today=books.as_of.day,
        actual=actual,
        typical=tuple(Decimal(statistics.median(day)) for day in by_day),
        low=tuple(_quartiles(day)[0] for day in by_day),
        high=tuple(_quartiles(day)[1] for day in by_day),
        months_compared=len(history),
    )


def _quartiles(values: Sequence[Decimal]) -> tuple[Decimal, Decimal]:
    first, _, third = statistics.quantiles(values, n=4, method="inclusive")
    return Decimal(first), Decimal(third)


def _cumulative(
    books: Books, ownership: OwnersOf, owners: Owners, month: Month, days: int
) -> tuple[Decimal, ...]:
    """Cumulative spending through each of the first `days` days of `month`.

    A month shorter than `days` holds its final total for the days it does not have.
    """
    start = date(month.year, month.month, 1)
    last = calendar.monthrange(month.year, month.month)[1]
    daily: defaultdict[int, Decimal] = defaultdict(Decimal)
    for entry in _transactions(books, start, start.replace(day=last)):
        if payers(entry, ownership) != owners:
            continue
        for posting in entry.postings:
            if books.spends(posting.account):
                amount = units(posting, entry)
                if amount.currency == books.currency:
                    daily[entry.date.day] += amount.number
    running = Decimal(0)
    series = []
    for day in range(1, days + 1):
        running += daily.get(day, Decimal(0))
        series.append(running)
    return tuple(series)


@dataclass(frozen=True, slots=True)
class Line:
    """One transaction as it touched one of the part's accounts."""

    date: date
    payee: str
    narration: str
    category: str | None
    amount: Decimal
    account: Account
    unreviewed: bool
    one_sided: bool = False
    """A row waiting to be told where its money went, whose other side hearth does not see:
    one side of a card payment not found yet (`TRANSFER_PENDING`), or a transfer or loan
    payment nothing was proposed for (``booking.moved``). Review asks about these apart from
    what is left (``hearth.review.where``)."""
    unseen: bool = False
    """Whether the line is on an account hearth does not see (`books.unseen`)."""
    other_side: tuple[str, ...] = ()
    """On an account hearth does not see (`books.unseen`), the accounts the money moved from
    or to. The line says only which way it moved: who may be told which account the other
    side is, is for ``hearth.disclosure`` to decide."""
    review_id: str | None = None
    """The review row the line was booked from, if it was one."""
    kept: bool = False
    """Whether its category is an expense that is not spending (``hearth.categories``), such
    as a retirement contribution, which no spending figure counts."""


MOVED_IN = "Transfer from another account"
MOVED_OUT = "Transfer to another account"
"""What a line on an account hearth does not see says. It is the other side of a row on a
connected account, so the bank's words for it are that account's transaction detail, which
an owner of only the unseen account, such as a joint one, may not see: like a paired card
payment's, it repeats none of them."""


def lines(books: Books, accounts: Sequence[Account], start: date, end: date) -> list[Line]:
    """Every posting to `accounts` from start to end, newest first.

    The category is the transaction's single income or expense account, when it has one. A
    posting on an account hearth does not see says only which way the money moved (`MOVED_IN`,
    `MOVED_OUT`), with the accounts on the other side for disclosure to name or not.
    """
    by_name = {account.ledger_account: account for account in accounts}
    found = []
    for entry in _transactions(books, start, end):
        others = [p.account for p in entry.postings if not is_balance_sheet(p.account)]
        category = others[0] if len(others) == 1 else None
        review_id = (entry.meta or {}).get("review-id")
        for posting in entry.postings:
            account = by_name.get(posting.account)
            if account is None:
                continue
            amount = units(posting, entry)
            if amount.currency != books.currency:
                continue
            if posting.account in books.unseen:
                found.append(
                    Line(
                        date=entry.date,
                        payee=MOVED_IN if amount.number > 0 else MOVED_OUT,
                        narration="",
                        category=None,
                        amount=amount.number,
                        account=account,
                        unreviewed=entry.flag == "!",
                        unseen=True,
                        other_side=tuple(
                            p.account
                            for p in entry.postings
                            if p.account != posting.account and is_balance_sheet(p.account)
                        ),
                        review_id=review_id if isinstance(review_id, str) else None,
                    )
                )
                continue
            found.append(
                Line(
                    date=entry.date,
                    payee=entry.payee or entry.narration or "",
                    narration=(entry.narration or "") if entry.payee else "",
                    category=category,
                    amount=amount.number,
                    account=account,
                    unreviewed=entry.flag == "!",
                    one_sided=entry.flag == "!" and _one_sided(entry),
                    review_id=review_id if isinstance(review_id, str) else None,
                )
            )
    return sorted(found, key=lambda line: line.date, reverse=True)


def _one_sided(entry: Transaction) -> bool:
    """Whether a flagged row waits to be told where its money went (`Line.one_sided`)."""
    waiting = {p.account for p in entry.postings}
    group = (entry.meta or {}).get("review-group")
    return TRANSFER_PENDING in waiting or (
        UNRESOLVED in waiting and isinstance(group, str) and moved(group) is not None
    )


def covers(books: Books, accounts: Sequence[Account], month: Month) -> bool:
    """Whether the ledger holds the whole month for every one of `accounts` open in it.

    An account whose history starts after the month began leaves the month incomplete, and a
    figure for it must say so rather than look whole.

    So does data that stops before the month is out. `accounts`, which are one part's, hold
    the month only once one of them is known to reach past it, by the rule of
    `books.Reach.holds`, and once every account its connection vouches for (`books.vouched`)
    is known to be current through it as well. An account is known through a month by its own
    entries, or by a vouched connection having last synced successfully after the month (in
    it, for this month). A card whose connection last worked in May therefore leaves May and
    every month after it incomplete however current the part's other accounts are, while a
    quiet card whose connection still works holds its months.

    For an account its connection vouches for, its own entries are only those no other
    connection booked (`books.account_reaches`, `books.account_ends`): statement and manual
    entries count, but a payment booked through the checking account's feed does not. Coverage
    asks whether this account's feed is known to be complete through a month, not whether the
    books hold any entry on the account in it; that payment proves one transaction on the card
    and nothing of the purchases the card's own feed did not deliver. Presence is not
    completeness.

    A connection vouches only for an account whose entries it booked, which the import marks
    with their `plaid:<item_id>:...` source (`connections.plaid_source`). It books everything
    a connection sent into the accounts hearth opened for it, so those are judged by their
    connection; an account an inherited ledger holds gets nothing from the import, so it is
    judged by the rule below, as one no connection feeds.

    Accounts no connection vouches for, such as those a statement import brings in, are judged
    together by the ledger alone, and the ledger cannot tell quiet from missing: one of them
    that stopped while another in the same part carries on reads as complete. Judging each on
    its own entries would be no better, as every quiet card would hold its part back. The
    honest remedy is a balance assertion from each statement's close, which says the account
    is whole through it whether or not anything moved.

    Every rule here is decided by month alone, never by the day data stops or a connection
    last synced, which are close to transaction detail; and it is the same for every viewer.
    A month the ledger has not reached never reads as a month in which nothing happened.
    Spending and income figures ask `flows_cover` instead, which judges only the accounts that
    can move them.

    An account hearth does not see (`books.unseen`) is not judged at all: its rows are moves
    members made from their connected accounts, which those accounts' history holds, so it
    has no history of its own to be whole or not.

    A brokerage account's holdings are known from the day its brokerage reported them
    (`books.positioned`), and Plaid sends no trades to say what it held before: a month that
    ended earlier is not whole, whatever rows reached the account in it, or its value there
    would be what nobody said it held. That is a question of value alone, so `flows_cover`
    does not ask it.
    """
    last = date(month.year, month.month, calendar.monthrange(month.year, month.month)[1])
    if any(books.positioned.get(a.ledger_account, date.min) > last for a in accounts):
        return False
    return _history_covers(books, accounts, month)


def _history_covers(books: Books, accounts: Sequence[Account], month: Month) -> bool:
    """`covers` by the accounts' history alone, whatever their holdings are known from."""
    first = date(month.year, month.month, 1)
    last = first.replace(day=calendar.monthrange(month.year, month.month)[1])
    by_account = {coverage.account: coverage for coverage in books.coverage}
    accounts = [a for a in accounts if a.ledger_account not in books.unseen]
    # Days a carried account's base and its bank's history both leave out are known by
    # neither, however current its connection is.
    for account in accounts:
        unseen = books.unseen_between.get(account.ledger_account)
        if unseen is not None and unseen[0] <= last and first <= unseen[1]:
            return False
        # Matched, and held back by the base's entry dated ahead: only the base's own record
        # up to today says anything, whatever its connection has sent.
        held_back = books.held_back.get(account.ledger_account)
        if held_back is not None and last > held_back[0]:
            return False
    opened: list[str] = []
    for account in accounts:
        coverage = by_account.get(account.ledger_account)
        if coverage is None or coverage.opened > last:
            continue
        opened.append(account.ledger_account)
        expected_from = max(first, coverage.opened)
        if coverage.earliest is None or coverage.earliest > expected_from + QUIET_START:
            return False
    if not opened:
        return True
    return any(_known(books, a.ledger_account, month) for a in accounts) and all(
        _known(books, account, month) for account in opened if account in books.synced
    )


def _known(books: Books, account: str, month: Month) -> bool:
    """Whether the account's own entries, or its vouched connection's last sync, hold `month`."""
    reach = books.account_reaches.get(account)
    if reach is not None and reach.holds(month, books.month):
        return True
    if account not in books.synced:
        return False
    synced = Month.of(books.synced[account])
    return synced > month or synced == month == books.month


def last_day(books: Books, accounts: Sequence[Account]) -> date | None:
    """The last day the data behind `accounts`, one part's, is known through, by the rule
    `covers` decides months by: the part's newest known day, held back to the day each account
    its connection vouches for is known through. An account is known through its own last day
    in the ledger, or its vouched connection's last sync if that is later; a vouched account's
    own last day ignores entries another connection booked, as `covers` explains. An account
    hearth does not see has no data of its own to stop (`covers`), so it is left out."""
    ends = books.account_ends
    known = {
        account.ledger_account: max(
            ends[account.ledger_account], books.synced.get(account.ledger_account, date.min)
        )
        for account in accounts
        if account.ledger_account in ends and account.ledger_account not in books.unseen
    }
    if not known:
        return None
    return min([max(known.values()), *(day for name, day in known.items() if name in books.synced)])


def flows_cover(books: Books, accounts: Sequence[Account], month: Month) -> bool:
    """`covers` for a spending or income figure, judged only over the accounts that can move it,
    and by their history alone: when a brokerage's holdings are known from says nothing of
    what moved.

    An account that only ever takes transfers (`books.flowing`) adds nothing to spending or
    income, so its quiet months never leave such a figure incomplete; a quiet card still does.
    So does an account with no history in the books yet (`books.awaiting`): nothing says yet
    what it spends, so it is judged as one that might.

    A month that ends before the history of every account that can move the figure begins
    is not covered either (`_begun`).

    Accounts that are all brokerages (`books.invested`), or all ones hearth does not see
    (`books.unseen`), cover no month at all: Plaid sends a brokerage no transactions, and
    hearth is told nothing of an account it does not see, so nothing says what they spent or
    earned, and a figure of nothing would be one nobody established.
    """
    seen = [a for a in accounts if a.ledger_account not in books.unseen]
    if all(a.ledger_account in books.invested for a in seen):
        return False
    judged = books.flowing | books.awaiting
    moving = [a for a in accounts if a.ledger_account in judged]
    if moving and not _begun(books, moving, month):
        return False
    return _history_covers(books, moving, month)


def _begun(books: Books, accounts: Sequence[Account], month: Month) -> bool:
    """Whether the books' history of any of `accounts` has begun by the end of `month`.

    Before it has, the books hold nothing of what they spent or earned, which is not the same
    as their having spent or earned nothing: a month from before the history starts is one
    the books have not reached, never a month of $0 to draw or to take a typical month from.
    """
    last = date(month.year, month.month, calendar.monthrange(month.year, month.month)[1])
    opened = {coverage.account: coverage.opened for coverage in books.coverage}
    return any(opened.get(a.ledger_account, date.max) <= last for a in accounts)


FIXED_MONTHS = 3
FIXED_TOLERANCE = Decimal("0.05")


@dataclass(frozen=True, slots=True)
class Payment:
    day: date
    amount: Decimal


def category_payments(
    books: Books, ownership: OwnersOf, owners: Iterable[Owners], month: Month
) -> dict[str, list[Payment]]:
    """Each expense category's individual payments in `month` by the given owners."""
    groups = set(owners)
    start = date(month.year, month.month, 1)
    end = min(books.as_of, start.replace(day=calendar.monthrange(month.year, month.month)[1]))
    found: defaultdict[str, list[Payment]] = defaultdict(list)
    for entry in _transactions(books, start, end):
        if payers(entry, ownership) not in groups:
            continue
        for posting in entry.postings:
            if books.spends(posting.account):
                amount = units(posting, entry)
                if amount.currency == books.currency:
                    found[posting.account].append(Payment(entry.date, amount.number))
    return dict(found)


def fixed_categories(
    books: Books, ownership: OwnersOf, owners: Iterable[Owners], months: Sequence[Month]
) -> frozenset[str]:
    """Categories paid exactly once a month at a steady amount, like rent or a mortgage.

    They are shown as one line with the day they were paid rather than as a bar against a
    typical pace, because a bill paid on the 1st is not "over typical" on the 2nd.
    """
    recent = list(months)[-FIXED_MONTHS:]
    if len(recent) < FIXED_MONTHS:
        return frozenset()
    groups = list(owners)
    history = [category_payments(books, ownership, groups, month) for month in recent]
    fixed = set()
    for category in set().union(*history):
        payments = [month.get(category, []) for month in history]
        if any(len(found) != 1 for found in payments):
            continue
        amounts = [found[0].amount for found in payments]
        if max(amounts) - min(amounts) <= FIXED_TOLERANCE * max(amounts):
            fixed.add(category)
    return frozenset(fixed)
