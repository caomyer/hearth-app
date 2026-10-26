"""What one viewer may see of the household, assembled for rendering.

This package is the only place that reads the books and ownership on behalf of a page. Pages
receive the views built here and nothing else, so a figure the viewer may not see is never
computed into a view, let alone rendered into a response. Every decision goes through one
``Gate`` (``hearth.disclosure.gate``), the only importer of ``hearth.access``.

Anything that does not exist and anything the viewer may not see raise the same
``Concealed``, so a response can never tell the two apart.
"""

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from hearth import audit, members, ownbooks, sharing
from hearth.accounts import Account, all_accounts
from hearth.audit import EventKind
from hearth.books import Books, Failure, Status
from hearth.connections import Connection, Connections
from hearth.disclosure.common import (
    IDENTITY_HUES,
    Household,
    LeftOut,
    MemberRef,
    PartRef,
    Worth,
    left_out,
)
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
from hearth.disclosure.naming import Namer
from hearth.disclosure.people import (
    AccountRow,
    Day,
    PersonView,
    account_rows,
    days,
    other_side,
    person,
    person_of,
)
from hearth.disclosure.worth import NetWorthView, net_worth
from hearth.figures import Pace, covers, lines
from hearth.members import Member, MemberId
from hearth.months import Month
from hearth.reports.balances import AssertionCheck
from hearth.reports.coverage import Span
from hearth.sharing import Facet, SharingChoices
from hearth.valuation.networth import Freshness

if TYPE_CHECKING:
    # hearth.review and hearth.plaid raise this package's Concealed, so they are imported where
    # they are used.
    from pathlib import Path

    from hearth.books import Decide
    from hearth.clock import Clock
    from hearth.plaid.connector import Connector, Runner
    from hearth.plaid.setup import PlaidSetup
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
    "LeftOut",
    "MemberRef",
    "NetWorthView",
    "Pace",
    "PartRef",
    "PersonView",
    "Withheld",
    "Worth",
    "left_out",
    "may_administer",
    "review_decisions",
    "rulings_in",
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
    reported_by: str | None = None
    """The bank whose reported balance failed, when it was a bank's, not a statement's."""

    @property
    def difference(self) -> Decimal:
        return self.check.asserted - self.check.computed

    @property
    def as_of(self) -> date:
        """The last day the balance checked covers: a check is of the start of its day."""
        return self.check.date - timedelta(days=1)


@dataclass(frozen=True, slots=True)
class FailureNotice:
    """Why today's figures are missing. The detail names an account and amounts, so only
    those the account's balances are visible to may read it; everyone else gets one sentence."""

    at: datetime
    summary: str
    showing_from: datetime | None
    detail: Disclosed[FailedCheck] | None


@dataclass(frozen=True, slots=True)
class AccountView:
    """One account, which the viewer may see the balance of, the transactions of, or both.

    What kind of account it is reads off its positions, so it goes with the balance."""

    id: str
    name: str
    owners: tuple[MemberRef, ...]
    spans: Disclosed[tuple[Span, ...]]
    kind: Disclosed[str]
    balance: Disclosed[Worth]
    month: Month
    days: Disclosed[tuple[Day, ...]]
    covered: Disclosed[bool]
    """Whether the account's history is known in full for `month`, so that no transactions
    in it is a fact. It is decided by its transactions, so it goes with them."""
    arriving: bool = False
    """Whether the books have not caught up with the account yet (`Household.arriving`)."""
    unknown: str | None = None
    """Why its balance is not known, when the books say (``books.unknown``), told to its owners
    alone (``access.may_see_why_unknown``)."""
    unseen: UnseenAccount | None = None
    """What else there is to say of an account hearth does not see (`books.unseen`); None for
    any other account."""
    priced: date | None = None
    """For a brokerage account whose balance the viewer may see, the day of the newest price
    its holdings are valued at."""


@dataclass(frozen=True, slots=True)
class UnseenAccount:
    """An account hearth does not see, beside its balance, which is never known."""

    kind: str | None
    """What kind of account its owner said it is, in their words: "Bank account", "Loan"."""
    where: str | None
    mask: str | None
    ledger_account: str | None
    """Its ledger name, shown to its owners alone, who move money into it."""
    takes_moves: bool
    """Whether members may say money went to it or came from it: one a member named. One
    claimed that nothing opens takes none (``ownbooks.Derived.unfed``), so a bank connected to
    it later counts nothing twice; until then nothing of it reaches hearth."""
    moves: Disclosed[Moves]
    """What hearth has seen move into and out of it, which goes with its transactions."""
    said: Mapping[str, datetime] = field(default_factory=dict)
    """When the viewer moved each of its rows here, by review id: their own rulings only."""


@dataclass(frozen=True, slots=True)
class Moves:
    """The money hearth has seen move into and out of an account it does not see: a flow, by
    its dates, never what the account holds, which hearth is never told."""

    net: Decimal
    """In less out. What moved, not a balance: interest, and anything spent from the account,
    never reach hearth."""
    count: int
    since: Month | None
    other: str | None
    """The one account every move came from or went to, when the viewer may be told it
    (``access.may_name_other_side``); None when there are several, or it is not theirs."""
    yours: bool
    """Whether the viewer may be told the other side of every move: all of them their own."""


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

    def account(
        self, account_id: str, month: Month, rulings: RulingStore | None = None
    ) -> AccountView:
        """One account, as the viewer may see it. With `rulings`, the page can say which rows of
        an account hearth does not see the viewer moved there, and when."""
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
        # Judged with its part, as `covers` asks, so a quiet month is not read as a gap; but
        # another account with no history yet says nothing of whether this one is whole.
        part = next(p for p in h.parts if p.owners == account.owners)
        judged = [
            a for a in part.accounts if a == account or a.ledger_account not in h.books.awaiting
        ]
        return AccountView(
            id=account.id,
            name=account.name,
            owners=tuple(h.ref(m) for m in h.members if m.id in account.owners),
            spans=self._gate.disclose(
                Facet.TRANSACTIONS, account.owners, lambda: coverage.spans if coverage else ()
            ),
            kind=row.kind if isinstance(row, AccountRow) else WITHHELD,
            balance=row.value if isinstance(row, AccountRow) else WITHHELD,
            month=month,
            days=self._gate.disclose(
                Facet.TRANSACTIONS, account.owners, lambda: days(h, [account], month)
            ),
            # An account the books hold nothing of yet has no history to be whole, not an empty one.
            covered=self._gate.disclose(
                Facet.TRANSACTIONS,
                account.owners,
                lambda: account.ledger_account not in h.arriving and covers(h.books, judged, month),
            ),
            arriving=isinstance(row, AccountRow) and row.arriving,
            unknown=(
                h.books.unknown.get(account.ledger_account)
                if isinstance(row, AccountRow) and self._gate.may_see_why_unknown(account.owners)
                else None
            ),
            unseen=(
                self._unseen(h, account, rulings)
                if account.ledger_account in h.books.unseen
                else None
            ),
            priced=row.priced if isinstance(row, AccountRow) and row.invested else None,
        )

    def _unseen(self, h: Household, account: Account, rulings: RulingStore | None) -> UnseenAccount:
        """What the page of an account hearth does not see says beside its balance, which is
        never known: where it is, its ledger name to its owners, and what moved."""

        def moves() -> Moves:
            seen = lines(h.books, [account], date.min, h.books.as_of)
            others = [other_side(h, line) for line in seen]
            named = {found.name for found in others if found is not None}
            yours = all(found is not None for found in others)
            return Moves(
                net=sum((line.amount for line in seen), Decimal(0)),
                count=len(seen),
                since=Month.of(min(line.date for line in seen)) if seen else None,
                other=next(iter(named)) if yours and len(named) == 1 else None,
                yours=yours,
            )

        said: dict[str, datetime] = {}
        if rulings is not None:
            # The viewer's own rulings alone: when they said a row went here is theirs to know.
            decided = review_decisions(h.books.pile, self._accounts, rulings)
            for review_id, decision in decided.items():
                ruling = decision.ruling
                if (
                    ruling is not None
                    and decision.account == account.ledger_account
                    and ruling.member_id == self._viewer.id
                ):
                    said[review_id] = ruling.at
        named = account.named
        return UnseenAccount(
            kind=named.kind.label if named else None,
            where=named.institution if named else None,
            mask=named.mask if named else None,
            ledger_account=(
                account.ledger_account if named and self._gate.may_feed(account.owners) else None
            ),
            takes_moves=named is not None,
            moves=self._gate.disclose(Facet.TRANSACTIONS, account.owners, moves),
            said=said,
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
        """The viewer's own review desk: the set-aside rows ``may_review`` gives them, which they
        may move to a category or to an account of their own that the books hold open."""
        from beancount.core.data import Balance, Close, Open

        from hearth.plaid.booking import moved
        from hearth.plaid.opening import PASSING, anchors
        from hearth.review.desk import ReviewDesk

        books = self.books
        entries = books.entries if books else ()
        closes = {e.account: e.date for e in entries if isinstance(e, Close)}
        return ReviewDesk(
            self._viewer.id,
            pile=books.pile if books else (),
            accounts=self._accounts,
            rulings=rulings,
            may_review=may_review,
            opened={
                e.account: (e.date, closes.get(e.account)) for e in entries if isinstance(e, Open)
            },
            may_move_to=self._gate.may_feed,
            banks={e.account for e in entries if isinstance(e, Balance) and anchors(e)}
            | {a for a, why in (books.unknown if books else {}).items() if why in PASSING},
            clock=clock,
            moves=moved,
        )

    def connector(
        self,
        conn: sqlite3.Connection,
        *,
        database: Path,
        feed_path: Path,
        data_dir: Path,
        setup: PlaidSetup,
        clock: Clock,
        runner: Runner,
    ) -> Connector:
        """The viewer's own connections, and only theirs (see ``access``)."""
        from beancount.core.data import Open

        from hearth.plaid.connector import Connector

        books = self.books
        ledger = {e.account for e in books.entries if isinstance(e, Open)} if books else set()
        own = (
            {e.account for e in ownbooks.own(books.entries) if isinstance(e, Open)}
            if books
            else set()
        )
        return Connector(
            self._gate,
            household=self._members,
            accounts=self._accounts,
            ledger_accounts=ledger,
            conn=conn,
            database=database,
            feed_path=feed_path,
            data_dir=data_dir,
            setup=setup,
            clock=clock,
            runner=runner,
            inherited=ledger - own,
            unbalanced=books.unanchored if books else (),
        )

    def namer(self, conn: sqlite3.Connection) -> Namer:
        """The viewer's way to name an account hearth does not see, owned by them alone or with
        others they choose (see ``access.may_feed``)."""
        from beancount.core.data import Open

        books = self.books
        return Namer(
            self._gate,
            household=self._members,
            accounts=self._accounts,
            ledger_accounts={e.account for e in books.entries if isinstance(e, Open)}
            if books
            else set(),
            conn=conn,
        )

    def matched(self) -> int:
        """How many moves between the viewer's own connected accounts hearth matched by
        itself and counts once: card payments both banks sent (``hearth.plaid.pairing``)."""
        from beancount.core.data import Transaction

        books = self._require_books()
        mine = {a.ledger_account for a in self._accounts if may_review(self._viewer.id, a.owners)}
        return sum(
            1
            for entry in books.entries
            if isinstance(entry, Transaction)
            and entry.flag == "*"
            and "review-id" not in (entry.meta or {})
            and len(entry.postings) == 2
            and all(
                posting.account in mine and "source" in (posting.meta or {})
                for posting in entry.postings
            )
        )

    def _failed_check(self, failure: Failure) -> Disclosed[FailedCheck] | None:
        check = failure.disagreement
        if check is None:
            return None
        account = next((a for a in self._accounts if a.ledger_account == check.account), None)
        owners = account.owners if account else frozenset[MemberId]()
        # A bank's balance failing says when its connection last synced and what a review of
        # it did; only an owner can put it right, and only an owner is told.
        if failure.reported_by is not None and not self._gate.may_see_why_unknown(owners):
            return WITHHELD
        return self._gate.disclose(
            Facet.ACCOUNT_BALANCES,
            owners,
            lambda: FailedCheck(
                check.account,
                tuple(ref for m in self._members if m.id in owners if (ref := self._ref_for(m.id))),
                check,
                failure.location,
                failure.reported_by,
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
    """Every review row's decision, for the books to lay over the ledger. Never rendered.

    A ruling counts only while ``may_review`` still lets its author review the row.
    """
    from hearth.review.desk import resolve

    return resolve(pile, accounts, rulings, may_review)


def rulings_in(conn: sqlite3.Connection) -> Decide:
    """How the rulings recorded in `conn` decide review rows, for a recompute of the books."""
    from hearth.review.rulings import RulingStore

    accounts, store = all_accounts(conn), RulingStore(conn)
    return lambda pile: review_decisions(pile, accounts, store)
