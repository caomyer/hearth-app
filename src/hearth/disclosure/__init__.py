"""What one viewer may see of the household, assembled for rendering.

This package is the only place that reads the books and ownership on behalf of a page. Pages
receive the views built here and nothing else, so a figure the viewer may not see is never
computed into a view, let alone rendered into a response. Every decision goes through one
``Gate`` (``hearth.disclosure.gate``), the only importer of ``hearth.access``.

Anything that does not exist and anything the viewer may not see raise the same
``Concealed``, so a response can never tell the two apart.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from hearth import audit, members, sharing
from hearth.accounts import Account, all_accounts
from hearth.audit import EventKind
from hearth.books import Books, Failure, Status
from hearth.connections import Connection, Connections, State
from hearth.disclosure.common import IDENTITY_HUES, Household, MemberRef, PartRef, Worth
from hearth.disclosure.gate import (
    WITHHELD,
    Concealed,
    Disclosed,
    Gate,
    Withheld,
    may_administer,
    may_review,
)
from hearth.disclosure.household import HouseholdView, household
from hearth.disclosure.people import (
    AccountRow,
    Day,
    PersonView,
    account_rows,
    days,
    person,
    person_of,
)
from hearth.disclosure.worth import NetWorthView, net_worth
from hearth.figures import Pace
from hearth.members import Member, MemberId
from hearth.months import Month
from hearth.reports.balances import AssertionCheck
from hearth.reports.coverage import Span
from hearth.sharing import Facet, SharingChoices
from hearth.valuation.networth import Freshness

if TYPE_CHECKING:
    # hearth.review raises this package's Concealed, so it is imported where it is used.
    from hearth.clock import Clock
    from hearth.review.desk import Decision, ReviewDesk
    from hearth.review.pile import PileItem
    from hearth.review.rulings import RulingStore

__all__ = [
    "WITHHELD",
    "AccountRow",
    "ActivityLine",
    "Concealed",
    "Day",
    "Disclosed",
    "Disclosure",
    "FailureNotice",
    "HouseholdView",
    "Lag",
    "MemberRef",
    "NetWorthView",
    "Pace",
    "PartRef",
    "PersonView",
    "Withheld",
    "Worth",
    "may_administer",
    "review_decisions",
]

OUTSIDER = Member(MemberId(0), "", "", is_admin=False, must_change_password=False)
"""A household member who owns nothing: what anyone else sees, for the sharing preview."""


@dataclass(frozen=True, slots=True)
class ActivityLine:
    at: datetime
    kind: EventKind
    actor: MemberRef | None
    subject: MemberRef | None
    detail: str


@dataclass(frozen=True, slots=True)
class FailedCheck:
    account: str
    owners: tuple[MemberRef, ...]
    check: AssertionCheck
    location: str | None

    @property
    def difference(self) -> Decimal:
        return self.check.asserted - self.check.computed


@dataclass(frozen=True, slots=True)
class FailureNotice:
    """Why today's figures are missing. The detail names an account and amounts, so only
    those the account's balances are visible to may read it; everyone else gets one sentence."""

    at: datetime
    summary: str
    showing_from: datetime | None
    detail: Disclosed[FailedCheck] | None


@dataclass(frozen=True, slots=True)
class Lag:
    accounts: int
    since: date | None


@dataclass(frozen=True, slots=True)
class AccountView:
    """One account, which the viewer may see the balance of, the transactions of, or both.

    What kind of account it is reads off its positions, so it goes with the balance."""

    id: str
    name: str
    owners: tuple[MemberRef, ...]
    spans: tuple[Span, ...]
    kind: Disclosed[str]
    balance: Disclosed[Worth]
    month: Month
    days: Disclosed[tuple[Day, ...]]


class Disclosure:
    """The household as one viewer is allowed to see it."""

    def __init__(
        self,
        viewer: Member,
        *,
        household: Sequence[Member],
        accounts: Sequence[Account],
        choices: SharingChoices,
        status: Status,
    ) -> None:
        self._viewer = viewer
        self._members = list(household)
        self._accounts = list(accounts)
        self._choices = choices
        self._gate = Gate(viewer, choices)
        self.status = status

    @classmethod
    def load(cls, conn: sqlite3.Connection, viewer: Member, status: Status) -> Disclosure:
        return cls(
            viewer,
            household=members.all_members(conn),
            accounts=all_accounts(conn),
            choices=sharing.load(conn),
            status=status,
        )

    @property
    def books(self) -> Books | None:
        return self.status.books

    def household(self) -> HouseholdView:
        return household(self._household())

    def you(self) -> PersonView:
        return person_of(self._household(), self._viewer)

    def person(self, username: str) -> PersonView:
        return person(self._household(), username)

    def net_worth(self) -> NetWorthView:
        return net_worth(self._household())

    def account(self, account_id: str, month: Month) -> AccountView:
        h = self._household()
        account = next((a for a in self._accounts if a.id == account_id), None)
        if account is None or not (
            self._gate.may(Facet.ACCOUNT_BALANCES, account.owners)
            or self._gate.may(Facet.TRANSACTIONS, account.owners)
        ):
            raise Concealed
        row = self._gate.disclose(
            Facet.ACCOUNT_BALANCES, account.owners, lambda: account_rows(h, [account])[0]
        )
        coverage = next((c for c in h.books.coverage if c.account == account.ledger_account), None)
        return AccountView(
            id=account.id,
            name=account.name,
            owners=tuple(h.ref(m) for m in h.members if m.id in account.owners),
            spans=coverage.spans if coverage else (),
            kind=row.kind if isinstance(row, AccountRow) else WITHHELD,
            balance=row.value if isinstance(row, AccountRow) else WITHHELD,
            month=month,
            days=self._gate.disclose(
                Facet.TRANSACTIONS, account.owners, lambda: days(h, [account], month)
            ),
        )

    def may_price(self, symbol: str) -> bool:
        """Whether the viewer may enter a price by hand for `symbol` (see ``access``)."""
        books = self._require_books()
        held_in = {
            p.holding.account for p in books.today.positions if p.holding.commodity == symbol
        }
        holders = [a.owners for a in self._accounts if a.ledger_account in held_in]
        return self._gate.may_enter_price(holders)

    def stale_symbols(self) -> list[str]:
        """Securities with an old price that the viewer holds, for a source refresh."""
        books = self._require_books()
        mine = {a.ledger_account for a in self._accounts if self._viewer.id in a.owners}
        return sorted(
            {
                p.holding.commodity
                for p in books.today.positions
                if p.holding.account in mine and p.freshness is not Freshness.CURRENT
            }
        )

    def connections(self, known: Connections) -> list[Connection]:
        """The viewer's own connections. Nobody else's are ever listed (see ``access``)."""
        return [c for c in known.all() if self._gate.may_use_connection(c.member)]

    def lag(self, known: Connections) -> Lag | None:
        """That some account somewhere has stopped updating, and since when, without saying
        whose or which: enough to read a total correctly, nothing about anyone's accounts."""
        stopped = [c for c in known.all() if c.state is State.EXPIRED]
        if not stopped:
            return None
        days = [c.data_until for c in stopped if c.data_until is not None]
        return Lag(sum(len(c.ledger_accounts) for c in stopped), min(days, default=None))

    def preview(self) -> PersonView:
        """The viewer's own page as anyone else in the household sees it, given their choices."""
        outsider = Household.of(
            Gate(OUTSIDER, self._choices), self._members, self._accounts, self._require_books()
        )
        return person_of(outsider, self._viewer)

    def notice(self) -> FailureNotice | None:
        failure = self.status.failure
        if failure is None:
            return None
        return FailureNotice(
            at=failure.at,
            summary=failure.summary,
            showing_from=self.books.computed_at if self.books else None,
            detail=self._failed_check(failure),
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
            if self._gate.may_read(event)
        ]

    def review_desk(self, rulings: RulingStore, clock: Clock) -> ReviewDesk:
        """The viewer's own review desk: the set-aside rows ``may_review`` gives them."""
        from hearth.review.desk import ReviewDesk

        return ReviewDesk(
            self._viewer.id,
            pile=self.books.pile if self.books else (),
            accounts=self._accounts,
            rulings=rulings,
            may_review=may_review,
            clock=clock,
        )

    def _failed_check(self, failure: Failure) -> Disclosed[FailedCheck] | None:
        check = failure.disagreement
        if check is None:
            return None
        account = next((a for a in self._accounts if a.ledger_account == check.account), None)
        owners = account.owners if account else frozenset[MemberId]()
        return self._gate.disclose(
            Facet.ACCOUNT_BALANCES,
            owners,
            lambda: FailedCheck(
                check.account,
                tuple(ref for m in self._members if m.id in owners if (ref := self._ref_for(m.id))),
                check,
                failure.location,
            ),
        )

    def _household(self) -> Household:
        return Household.of(self._gate, self._members, self._accounts, self._require_books())

    def _require_books(self) -> Books:
        if self.books is None:
            raise RuntimeError("no books yet: check Disclosure.books before asking for figures")
        return self.books

    def ref(self, member_id: MemberId | None) -> MemberRef | None:
        """A member as pages name and colour them."""
        return self._ref_for(member_id)

    def _ref_for(self, member_id: MemberId | None) -> MemberRef | None:
        index = next((i for i, m in enumerate(self._members) if m.id == member_id), None)
        if index is None:
            return None
        member = self._members[index]
        return MemberRef(member.username, member.display_name, index % IDENTITY_HUES + 1)


def review_decisions(
    pile: Sequence[PileItem], accounts: Sequence[Account], rulings: RulingStore
) -> dict[str, Decision]:
    """Every set-aside row's decision, for the import that books them. Never rendered.

    A ruling counts only while ``may_review`` still lets its author review the row.
    """
    from hearth.review.desk import resolve

    return resolve(pile, accounts, rulings, may_review)
