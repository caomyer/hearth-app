"""Flows and income by category, for the household and for each member, over a period.

Who spent is decided by who owns the account that paid: a transaction's expense postings belong
to the owners of its Assets and Liabilities postings. Income is attributed the same way, to the
owners of the account that received it. Flows from a joint account belongs to
the joint owners together, as a group, which is the only thing the ledger can show; it does not
know which of them made the purchase.

When that cannot be decided the spending is unattributed, never assigned to a guess: the paying
account has no owner, the transaction has no paying account, or it moves money between accounts
with different owners.

A member's own figure is their sole accounts only. Each joint group's spending is its own line,
belonging to its owners together and to none of them alone, so no member figure contains any
part of a joint account and nothing about a joint account can be derived from member figures.
The household figure is every group and the unattributed spending, each counted once.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from beancount.core.data import Directive, Transaction

from hearth.members import MemberId
from hearth.reports.common import Excluded, is_balance_sheet, root, transactions, units

type ByCategory = dict[str, Decimal]

type Owners = frozenset[MemberId]
"""The members who own an account: one for a sole account, several for a joint one."""

type OwnersOf = Callable[[str], Owners | None]
"""Who owns a ledger account, or None for one nobody has claimed. ``hearth.figures`` supplies
it from the accounts table; this module only applies it."""


@dataclass(frozen=True, slots=True)
class Unattributed:
    date: date
    reason: str
    by_category: ByCategory


@dataclass(frozen=True, slots=True)
class MemberFlows:
    """One member's spending from accounts they own alone.

    It is incomplete while any spending in the period is unattributed, because some of it may
    have been paid from the member's accounts.
    """

    member: MemberId
    by_category: ByCategory
    complete: bool


@dataclass(frozen=True, slots=True)
class Flows:
    start: date
    end: date
    currency: str
    by_owners: Mapping[Owners, ByCategory]
    unattributed: tuple[Unattributed, ...]
    excluded: tuple[Excluded, ...]

    @property
    def household(self) -> ByCategory:
        """Everything, by category, each posting counted exactly once."""
        total: defaultdict[str, Decimal] = defaultdict(Decimal)
        for categories in self.by_owners.values():
            _add(total, categories)
        for item in self.unattributed:
            _add(total, item.by_category)
        return dict(sorted(total.items()))

    @property
    def complete(self) -> bool:
        """For the household figure: False when amounts in another currency were left out."""
        return not self.excluded

    @property
    def joint(self) -> dict[Owners, ByCategory]:
        """Money through joint accounts, one line per group of owners."""
        return {owners: c for owners, c in self.by_owners.items() if len(owners) > 1}

    def for_member(self, member: MemberId) -> MemberFlows:
        """The member's money through accounts they own alone; joint accounts are not in it."""
        return MemberFlows(
            member=member,
            by_category=dict(self.by_owners.get(frozenset({member}), {})),
            complete=not self.unattributed and not self.excluded,
        )


def _add(total: defaultdict[str, Decimal], categories: Mapping[str, Decimal]) -> None:
    for category, number in categories.items():
        total[category] += number


def payers(entry: Transaction, owners_of: OwnersOf) -> Owners | str:
    """Who owns the accounts the money went through, or why they cannot be named."""
    groups: set[Owners] = set()
    for posting in entry.postings:
        if not is_balance_sheet(posting.account):
            continue
        owners = owners_of(posting.account)
        if owners is None:
            return f"{posting.account} has no owner"
        groups.add(owners)
    if not groups:
        return "no Assets or Liabilities account paid"
    if len(groups) > 1:
        return "paid from accounts with different owners"
    return groups.pop()


def spending_by_category(
    entries: Iterable[Directive],
    owners_of: OwnersOf,
    *,
    start: date,
    end: date,
    currency: str,
    counts: Callable[[str], bool] | None = None,
) -> Flows:
    """Expenses from start to end, inclusive, by expense account and by who paid: of every
    expense account, or of those `counts` says to count (``books.Books.spends``).

    Amounts in any currency other than `currency` are left out and listed, never converted.
    """
    return _flows(entries, owners_of, "Expenses", Decimal(1), start, end, currency, counts)


def income_by_source(
    entries: Iterable[Directive], owners_of: OwnersOf, *, start: date, end: date, currency: str
) -> Flows:
    """Income from start to end, inclusive, by income account and by who received it.

    Amounts are positive money earned. Amounts in any currency other than `currency` are left
    out and listed, never converted.
    """
    return _flows(entries, owners_of, "Income", Decimal(-1), start, end, currency)


def _flows(
    entries: Iterable[Directive],
    owners_of: OwnersOf,
    kind: str,
    sign: Decimal,
    start: date,
    end: date,
    currency: str,
    counts: Callable[[str], bool] | None = None,
) -> Flows:
    by_owners: defaultdict[Owners, defaultdict[str, Decimal]] = defaultdict(
        lambda: defaultdict(Decimal)
    )
    unattributed: list[Unattributed] = []
    excluded: list[Excluded] = []
    for entry in transactions(entries, start, end):
        categories: defaultdict[str, Decimal] = defaultdict(Decimal)
        for posting in entry.postings:
            if root(posting.account) != kind or (
                counts is not None and not counts(posting.account)
            ):
                continue
            amount = units(posting, entry)
            if amount.currency != currency:
                excluded.append(
                    Excluded(entry.date, posting.account, amount.number, amount.currency)
                )
            else:
                categories[posting.account] += sign * amount.number
        if not categories:
            continue
        paid_by = payers(entry, owners_of)
        if isinstance(paid_by, str):
            unattributed.append(Unattributed(entry.date, paid_by, dict(categories)))
        else:
            _add(by_owners[paid_by], categories)
    return Flows(
        start=start,
        end=end,
        currency=currency,
        by_owners={owners: dict(sorted(c.items())) for owners, c in by_owners.items()},
        unattributed=tuple(unattributed),
        excluded=tuple(excluded),
    )
