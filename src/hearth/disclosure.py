"""What one viewer may see of the household, assembled for rendering.

This is the only module that reads the ledger and ownership on behalf of a page.
Pages receive the views built here and nothing else, so a figure the viewer may
not see is never computed into a view, let alone rendered into a response.
Every decision is delegated to ``hearth.access``.

Anything that does not exist and anything the viewer may not see raise the same
``Concealed``, so a response can never tell the two apart.
"""

import sqlite3
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, final

from hearth import access, audit, figures, members, sharing
from hearth.accounts import Account, all_accounts
from hearth.audit import EventKind
from hearth.ledger import Ledger
from hearth.members import Member, MemberId
from hearth.months import Month
from hearth.sharing import Facet, SharingChoices

if TYPE_CHECKING:
    # hearth.review raises this module's Concealed, so it is imported where it is used.
    from hearth.clock import Clock
    from hearth.review.desk import Decision, ReviewDesk
    from hearth.review.pile import PileItem
    from hearth.review.rulings import RulingStore


class Concealed(Exception):  # noqa: N818 - it names an outcome, not a fault
    """Not there, or not for this viewer. Deliberately one and the same."""


@final
class Withheld:
    """Stands in for a figure the viewer may not see. It carries no value at all."""

    def __repr__(self) -> str:
        return "WITHHELD"


WITHHELD = Withheld()

type Disclosed[T] = T | Withheld


@dataclass(frozen=True, slots=True)
class MemberRef:
    username: str
    display_name: str


@dataclass(frozen=True, slots=True)
class MemberSummary:
    member: MemberRef
    is_viewer: bool
    net_worth: Disclosed[Decimal]
    spending: Disclosed[Decimal]


@dataclass(frozen=True, slots=True)
class JointSummary:
    owners: list[MemberRef]
    net_worth: Disclosed[Decimal]
    spending: Disclosed[Decimal]


@dataclass(frozen=True, slots=True)
class HouseholdView:
    month: Month
    members: list[MemberSummary]
    joint: list[JointSummary]
    net_worth: Disclosed[Decimal]
    spending: Disclosed[Decimal]


@dataclass(frozen=True, slots=True)
class AccountSummary:
    id: str
    name: str
    owners: list[MemberRef]
    balance: Disclosed[Decimal]


@dataclass(frozen=True, slots=True)
class TransactionLine:
    date: date
    payee: str
    narration: str
    category: str
    amount: Decimal
    account_id: str
    account_name: str


@dataclass(frozen=True, slots=True)
class MemberView:
    summary: MemberSummary
    month: Month
    breakdown: Disclosed[list[figures.CategoryAmount]]
    accounts: list[AccountSummary]
    transactions: list[TransactionLine]


@dataclass(frozen=True, slots=True)
class AccountView:
    account: AccountSummary
    month: Month
    transactions: Disclosed[list[TransactionLine]]


@dataclass(frozen=True, slots=True)
class ActivityLine:
    at: datetime
    kind: EventKind
    actor: MemberRef | None
    subject: MemberRef | None
    detail: str


class Disclosure:
    """The household as one viewer is allowed to see it."""

    def __init__(
        self,
        viewer: Member,
        *,
        household: Sequence[Member],
        accounts: Sequence[Account],
        choices: SharingChoices,
        ledger: Ledger,
    ) -> None:
        self._viewer = viewer
        self._household = list(household)
        self._accounts = list(accounts)
        self._choices = choices
        self._ledger = ledger

    @classmethod
    def load(cls, conn: sqlite3.Connection, viewer: Member, ledger: Ledger) -> Disclosure:
        return cls(
            viewer,
            household=members.all_members(conn),
            accounts=all_accounts(conn),
            choices=sharing.load(conn),
            ledger=ledger,
        )

    def household(self, month: Month) -> HouseholdView:
        summaries = [self._summary(member, month) for member in self._household]
        joint = [
            self._joint_summary(owners, accounts, month)
            for owners, accounts in figures.joint(self._accounts).items()
        ]
        parts: list[MemberSummary | JointSummary] = [*summaries, *joint]
        return HouseholdView(
            month=month,
            members=summaries,
            joint=joint,
            # A total is shown only when every part of it is, or the one withheld
            # part could be recovered by subtraction.
            net_worth=_total(part.net_worth for part in parts),
            spending=_total(part.spending for part in parts),
        )

    def member(self, username: str, month: Month) -> MemberView:
        member = self._member_named(username)
        owned = [account for account in self._accounts if member.id in account.owners]
        visible = [account for account in owned if self._is_visible(account)]
        solo = figures.solo(member.id, self._accounts)
        return MemberView(
            summary=self._summary(member, month),
            month=month,
            breakdown=self._disclose(
                Facet.CATEGORY_BREAKDOWN,
                {member.id},
                lambda: figures.breakdown(solo, self._ledger, month),
            ),
            accounts=[self._account_summary(account) for account in visible],
            transactions=sorted(
                (
                    line
                    for account in visible
                    if self._may(Facet.TRANSACTIONS, account.owners)
                    for line in self._transaction_lines(account, month)
                ),
                key=lambda line: line.date,
                reverse=True,
            ),
        )

    def account(self, account_id: str, month: Month) -> AccountView:
        account = next((a for a in self._accounts if a.id == account_id), None)
        if account is None or not self._is_visible(account):
            raise Concealed
        return AccountView(
            account=self._account_summary(account),
            month=month,
            transactions=self._disclose(
                Facet.TRANSACTIONS,
                account.owners,
                lambda: sorted(
                    self._transaction_lines(account, month),
                    key=lambda line: line.date,
                    reverse=True,
                ),
            ),
        )

    def activity(self, events: Sequence[audit.AuditEvent]) -> list[ActivityLine]:
        return [
            ActivityLine(
                at=event.at,
                kind=event.kind,
                actor=self._ref_for(event.actor_id),
                subject=self._ref_for(event.subject_id),
                detail=event.detail,
            )
            for event in events
            if access.may_read_event(self._viewer, event)
        ]

    def review_desk(
        self, pile: Sequence[PileItem], rulings: RulingStore, clock: Clock
    ) -> ReviewDesk:
        """The viewer's own review desk: the set-aside rows ``access.may_review`` gives them."""
        from hearth.review.desk import ReviewDesk

        return ReviewDesk(
            self._viewer.id,
            pile=pile,
            accounts=self._accounts,
            rulings=rulings,
            may_review=access.may_review,
            clock=clock,
        )

    def _may(self, facet: Facet, owners: Collection[MemberId]) -> bool:
        return access.may_see(self._viewer, facet, owners, self._choices)

    def _disclose[T](
        self, facet: Facet, owners: Collection[MemberId], compute: Callable[[], T]
    ) -> Disclosed[T]:
        """Compute a figure only once it is known the viewer may see it."""
        return compute() if self._may(facet, owners) else WITHHELD

    def _is_visible(self, account: Account) -> bool:
        return self._may(Facet.ACCOUNT_BALANCES, account.owners) or self._may(
            Facet.TRANSACTIONS, account.owners
        )

    def _summary(self, member: Member, month: Month) -> MemberSummary:
        solo = figures.solo(member.id, self._accounts)
        return MemberSummary(
            member=_ref(member),
            is_viewer=member.id == self._viewer.id,
            net_worth=self._disclose(
                Facet.NET_WORTH_TOTAL,
                {member.id},
                lambda: figures.net_worth(solo, self._ledger),
            ),
            spending=self._disclose(
                Facet.MONTHLY_SPENDING_TOTAL,
                {member.id},
                lambda: figures.spending(solo, self._ledger, month),
            ),
        )

    def _joint_summary(
        self, owners: frozenset[MemberId], accounts: Sequence[Account], month: Month
    ) -> JointSummary:
        return JointSummary(
            owners=[_ref(m) for m in self._household if m.id in owners],
            net_worth=self._disclose(
                Facet.NET_WORTH_TOTAL,
                owners,
                lambda: figures.net_worth(accounts, self._ledger),
            ),
            spending=self._disclose(
                Facet.MONTHLY_SPENDING_TOTAL,
                owners,
                lambda: figures.spending(accounts, self._ledger, month),
            ),
        )

    def _account_summary(self, account: Account) -> AccountSummary:
        return AccountSummary(
            id=account.id,
            name=account.name,
            owners=[_ref(m) for m in self._household if m.id in account.owners],
            balance=self._disclose(
                Facet.ACCOUNT_BALANCES,
                account.owners,
                lambda: self._ledger.balance(account.ledger_account),
            ),
        )

    def _transaction_lines(self, account: Account, month: Month) -> list[TransactionLine]:
        return [
            TransactionLine(
                date=t.date,
                payee=t.payee,
                narration=t.narration,
                category=t.category,
                amount=t.amount,
                account_id=account.id,
                account_name=account.name,
            )
            for t in self._ledger.transactions(account.ledger_account)
            if month.contains(t.date)
        ]

    def _member_named(self, username: str) -> Member:
        member = next((m for m in self._household if m.username == username), None)
        if member is None:
            raise Concealed
        return member

    def _ref_for(self, member_id: MemberId | None) -> MemberRef | None:
        member = next((m for m in self._household if m.id == member_id), None)
        return _ref(member) if member else None


def review_decisions(
    pile: Sequence[PileItem], accounts: Sequence[Account], rulings: RulingStore
) -> dict[str, Decision]:
    """Every set-aside row's decision, for the import that books them. Never rendered.

    A ruling counts only while ``access.may_review`` still lets its author review the row.
    """
    from hearth.review.desk import resolve

    return resolve(pile, accounts, rulings, access.may_review)


def _ref(member: Member) -> MemberRef:
    return MemberRef(member.username, member.display_name)


def _total(parts: Iterable[Disclosed[Decimal]]) -> Disclosed[Decimal]:
    values = list(parts)
    if any(isinstance(value, Withheld) for value in values):
        return WITHHELD
    return sum((value for value in values if not isinstance(value, Withheld)), Decimal(0))
