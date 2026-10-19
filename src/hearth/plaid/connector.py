"""One member's connections to their institutions: what they see of them, and what they do.

The connector does not decide who may do what. It is built by ``hearth.disclosure`` with the
viewer's gate and asks it about every Item, credential set and target account, the way the
review desk is handed ``may_review``. An Item someone else holds behaves exactly like one
that does not exist: both raise `Concealed`.

Everything a live call would cost is checked before the call: whether the operation is
approved for this run, which Plaid account the member's next connection would use, and
whether that account has a connection left. A member out of connections is told so, with
what to do about it, before Plaid Link ever opens.
"""

import contextlib
import itertools
import logging
import secrets
import sqlite3
import threading
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from hearth import accounts as hearth_accounts
from hearth import db
from hearth.accounts import Account
from hearth.clock import Clock
from hearth.disclosure import Concealed
from hearth.members import Member, MemberId
from hearth.plaid import credentials as sets
from hearth.plaid import mapping
from hearth.plaid.approval import ApprovalRequired, Operation
from hearth.plaid.client import Environment, Institution, PlaidError, PlaidRejectedError
from hearth.plaid.credentials import Capacity, CredentialSet, CredentialStore
from hearth.plaid.feed import Balance, Coverage, Feed, Outcome, Run
from hearth.plaid.mapping import Problem
from hearth.plaid.owner import is_alive
from hearth.plaid.registry import History, Item, ItemState, Registry
from hearth.plaid.setup import DAYS_REQUESTED, PlaidSetup
from hearth.plaid.sync import Skipped, SyncBusyError, Synced, check_waits, sync_item
from hearth.vault.secret import Secret
from hearth.vault.tokens import TokenVault

_log = logging.getLogger(__name__)

STATEMENT_MAX_BYTES = 5 * 1024 * 1024
STATEMENTS_DIR = "statements"
MAX_INSTITUTION_NAME = 80
MAX_ACCOUNT_NAME = 80

NEW = "new"
"""The target a mapping sends for a new ledger account, whose name comes in its own field."""

type Runner = Callable[[Callable[[], None]], None]
"""Runs work after the response is sent: a thread in the app, at once in tests."""

_following: set[str] = set()
"""The Items this process is syncing in the background and asking again for history: at
most one follower each, so an Item is never asked more often than `check_waits` allows."""
_following_lock = threading.Lock()


class Policy(Protocol):
    """The viewer's answers from the access policy, as the disclosure gate gives them."""

    @property
    def viewer(self) -> Member: ...

    def may_use_connection(self, holder: MemberId) -> bool: ...

    def may_use_credentials(self, owner: MemberId | None) -> bool: ...

    def may_manage_household_credentials(self) -> bool: ...

    def may_feed(self, owners: Collection[MemberId]) -> bool: ...


class Refusal(StrEnum):
    """Why a step was not taken. Each is a fixed message; none carries anything sent."""

    NO_PLAID_ACCOUNT = "no-plaid-account"
    NO_SLOT = "no-slot"
    NOT_APPROVED = "not-approved"
    PLAID_FAILED = "plaid-failed"
    CREDENTIALS_INCOMPLETE = "credentials-incomplete"
    CREDENTIALS_IN_USE = "credentials-in-use"
    NOT_UNDERSTOOD = "not-understood"
    TARGET_TAKEN = "target-taken"
    STATEMENT_UNREADABLE = "statement-unreadable"


class Refused(Exception):  # noqa: N818 - it names an outcome, not a fault
    def __init__(self, reason: Refusal, plaid_code: str | None = None) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.plaid_code = plaid_code
        """Plaid's own error code, when Plaid refused: safe to show, and what to look up."""


class Hold(StrEnum):
    """Why nothing on a mapping form was saved, though the member can put it right on the page."""

    PROBLEMS = "fix-marked"
    """A confirmed row names a new account that cannot be used, or a target another row has."""
    NEAR = "near-unseen"
    """What was chosen puts two targets a word apart that the page the member confirmed on did
    not name."""


class Held(Exception):  # noqa: N818 - it names an outcome, not a fault
    """Nothing was saved. `view` is the form as it was sent, with what needs another look."""

    def __init__(self, reason: Hold, view: MappingView) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.view = view


@dataclass(frozen=True, slots=True)
class PlaidAccountView:
    """What may be shown about a Plaid account: never its keys."""

    scope: str
    is_household: bool
    environment: Environment
    registered_by: str
    saved_at: datetime
    capacity: Capacity


@dataclass(frozen=True, slots=True)
class AccountView:
    account_id: str
    name: str
    mask: str | None
    type: str
    subtype: str | None
    balance: Balance | None
    coverage: Coverage
    feeds: str | None
    """The ledger account its member confirmed this account feeds."""
    statements: int
    """Statement files uploaded for it, waiting for the import."""

    @property
    def is_credit(self) -> bool:
        return self.type in {"credit", "loan"}

    @property
    def is_investment(self) -> bool:
        return self.type == "investment"

    @property
    def kind(self) -> str:
        if self.is_credit:
            return "Credit card" if self.subtype == "credit card" else "Loan"
        return (self.subtype or self.type).capitalize()


@dataclass(frozen=True, slots=True)
class ItemView:
    item_id: str
    institution: str
    state: ItemState
    linked_at: datetime
    last_synced_at: datetime | None
    problem: str | None
    history_complete: bool
    accounts: tuple[AccountView, ...]
    last_run: Run | None
    same_bank_as_another: bool
    """Another of the member's connections reaches the same institution: one of them may be a
    leftover from connecting again, still using a slot."""
    following: bool
    """A background sync is running for it, or waiting to ask again for history arriving."""
    next_check_at: datetime | None
    """When that follower next asks Plaid; None while it is asking, or when none follows."""

    @property
    def needs_login(self) -> bool:
        return self.state is ItemState.LOGIN_REQUIRED

    @property
    def short_name(self) -> str:
        """The institution's first word, as a list of its accounts names it."""
        return self.institution.partition(" ")[0]

    @property
    def short(self) -> tuple[AccountView, ...]:
        return tuple(a for a in self.accounts if a.coverage.is_short)


@dataclass(frozen=True, slots=True)
class Abilities:
    """Which live steps this run of hearth may take for the viewer's Plaid account."""

    link: bool
    sync: bool
    remove: bool


@dataclass(frozen=True, slots=True)
class ConnectView:
    own: PlaidAccountView | None
    household: PlaidAccountView | None
    chose_household: bool
    using: PlaidAccountView | None
    may_manage_household: bool
    environments: tuple[Environment, ...]
    abilities: Abilities
    items: tuple[ItemView, ...]
    days_requested: int
    today: date

    @property
    def remaining(self) -> int | None:
        return self.using.capacity.remaining if self.using else None

    @property
    def accounts(self) -> int:
        return sum(len(item.accounts) for item in self.items)

    @property
    def mapped(self) -> int:
        return sum(1 for item in self.items for a in item.accounts if a.feeds)

    @property
    def syncing(self) -> bool:
        """Whether a connection is bringing in an update now, or will ask again shortly."""
        return any(
            item.last_run is None or item.last_run.outcome is Outcome.RUNNING or item.following
            for item in self.items
        )

    @property
    def requested_from(self) -> date:
        return self.today - timedelta(days=self.days_requested - 1)


@dataclass(frozen=True, slots=True)
class LinkStart:
    """What Plaid Link opens with, or, on the stand-in, the invented banks to choose from."""

    link_token: str
    environment: Environment
    item_id: str | None
    """Set when signing an existing connection in again, which uses no new slot."""
    stand_in: tuple[tuple[str, str], ...]
    """(institution name, public token) for each invented bank, on the stand-in only."""


@dataclass(frozen=True, slots=True)
class OwnerChoice:
    key: str
    label: str
    owners: frozenset[MemberId]


@dataclass(frozen=True, slots=True)
class Target:
    ledger_account: str
    account: Account | None
    """The hearth account it already is, or None for a new ledger account."""
    taken: bool = False
    """Another connection already feeds it. Only that: never whose, nor from where."""

    @property
    def is_new(self) -> bool:
        return self.account is None


@dataclass(frozen=True, slots=True)
class ToMap:
    account: AccountView
    institution: str
    targets: tuple[Target, ...]
    """What it may go to: first a new ledger account, unless it is filed already, then the
    member's own accounts of its kind."""
    chosen: Target
    owner_choices: tuple[OwnerChoice, ...]
    """Who may own a new ledger account, or the fixed owners of the account it is filed in."""
    chosen_owners: str
    confirmed: bool
    near: bool
    """Its target is one word away from another target on the page."""
    name: str
    """What the household calls a new ledger account, as `hearth add-account --name` sets it."""
    default_name: str
    """The name a new ledger account is given when the member leaves it empty: what the
    institution and the bank call it, with its last digits when another account here would
    be called the same."""
    problem: Problem | None = None

    @property
    def offers_new(self) -> bool:
        return self.targets[0].is_new

    @property
    def path_problem(self) -> Problem | None:
        return None if self.problem in _NAME_PROBLEMS else self.problem

    @property
    def name_problem(self) -> Problem | None:
        return self.problem if self.problem in _NAME_PROBLEMS else None

    def owners(self) -> frozenset[MemberId] | None:
        """Who owns what it is going to, or None when the form named nobody it offered."""
        if self.chosen.account is not None:
            return self.chosen.account.owners
        return next((c.owners for c in self.owner_choices if c.key == self.chosen_owners), None)


@dataclass(frozen=True, slots=True)
class MappingView:
    rows: tuple[ToMap, ...]
    near: tuple[tuple[str, str], ...]

    @property
    def confirmed(self) -> int:
        return sum(1 for row in self.rows if row.confirmed)


@dataclass(frozen=True, slots=True)
class ImportItem:
    item: ItemView
    runs: tuple[Run, ...]

    @property
    def total(self) -> int:
        return sum(a.coverage.held.transactions for a in self.item.accounts)


@dataclass(frozen=True, slots=True)
class ImportView:
    items: tuple[ImportItem, ...]
    started_at: datetime | None
    unmapped: int

    @property
    def total(self) -> int:
        return sum(item.total for item in self.items)

    @property
    def running(self) -> bool:
        return any(r.outcome is Outcome.RUNNING for i in self.items for r in i.runs[:1])

    @property
    def history_complete(self) -> bool:
        return all(i.item.history_complete for i in self.items)

    @property
    def following(self) -> bool:
        """Whether hearth is still syncing, or will ask again, for any of these connections."""
        return any(i.item.following for i in self.items)

    @property
    def next_check_at(self) -> datetime | None:
        """When hearth next asks for history still arriving, if it is still asking."""
        return min((i.item.next_check_at for i in self.items if i.item.next_check_at), default=None)

    @property
    def alike(self) -> set[tuple[str, str]]:
        """The (bank, kind) labels more than one account here would share, such as a bank's
        several cards, which are told apart by their last digits."""
        return _twice((i.item.short_name, a.kind) for i in self.items for a in i.item.accounts)


@dataclass(frozen=True, slots=True)
class Mapping:
    """One row of the mapping form, as submitted."""

    account_id: str
    target: str
    """An account the row offered, by its ledger name, or `NEW` for the one named in `path`."""
    owners: str
    confirm: bool
    path: str = ""
    name: str = ""


class Connector:
    """The viewer's connections, as the access policy lets them see and use them."""

    def __init__(
        self,
        policy: Policy,
        *,
        household: Sequence[Member],
        accounts: Sequence[Account],
        ledger_accounts: Collection[str],
        conn: sqlite3.Connection,
        database: Path,
        feed_path: Path,
        data_dir: Path,
        setup: PlaidSetup,
        clock: Clock,
        runner: Runner,
    ) -> None:
        self._policy = policy
        self._viewer = policy.viewer
        self._members = list(household)
        self._accounts = list(accounts)
        self._ledger_accounts = frozenset(ledger_accounts)
        self._db = conn
        self._database = database
        self._feed_path = feed_path
        self._data_dir = data_dir
        self._setup = setup
        self._clock = clock
        self._runner = runner
        self._registry = Registry(conn)

    def __repr__(self) -> str:
        return "Connector()"

    # What the viewer sees.

    def overview(self) -> ConnectView:
        store = self._store()
        own = self._plaid_account(store, sets.personal(self._viewer.id))
        household = self._plaid_account(store, sets.HOUSEHOLD)
        scope = store.scope_for(self._viewer.id)
        using = own if scope == sets.personal(self._viewer.id) else household if scope else None
        with Feed.open(self._feed_path) as feed:
            items = tuple(self._item_view(item, feed) for item in self._items())
        return ConnectView(
            own=own,
            household=household,
            chose_household=store.chose_household(self._viewer.id),
            using=using,
            may_manage_household=self._policy.may_manage_household_credentials(),
            environments=self._setup.environments,
            abilities=self._abilities(using),
            items=items,
            days_requested=DAYS_REQUESTED,
            today=self._today(),
        )

    def item(self, item_id: str) -> ItemView:
        found = self._own_item(item_id)
        with Feed.open(self._feed_path) as feed:
            return self._item_view(found, feed)

    def mapping(self, drafts: Sequence[Mapping] = ()) -> MappingView:
        """The mapping form, as hearth proposes it or, given `drafts`, as the member sent it.

        A drafted row whose target the row did not offer keeps what hearth proposed: `confirm`
        refuses such a form before it is drafted.
        """
        with Feed.open(self._feed_path) as feed:
            items = [(item, self._item_view(item, feed)) for item in self._items()]
        drafted = {draft.account_id: draft for draft in drafts}
        proposals: list[str] = []
        rows: list[ToMap] = []
        for item, view in items:
            # A bank's accounts that would be proposed the same name, such as several cards it
            # calls alike, are each proposed with their last digits from the start.
            alike = _twice(self._proposal(item, account) for account in view.accounts)
            called = _twice(_called(item.institution, account) for account in view.accounts)
            for account in view.accounts:
                row = self._to_map(item, account, proposals, alike, called)
                draft = drafted.get(account.account_id)
                rows.append(self._drafted(row, draft) if draft else row)
        confirmed = [row for row in rows if row.confirmed]
        crowded = {
            row.account.account_id
            for row in confirmed
            for other in confirmed
            if other is not row
            and (
                other.chosen.ledger_account == row.chosen.ledger_account
                or (
                    row.chosen.is_new
                    and other.chosen.is_new
                    and mapping.in_use(row.chosen.ledger_account, [other.chosen.ledger_account])
                )
            )
        }
        rows = [
            replace(row, problem=row.problem or Problem.TWICE)
            if row.account.account_id in crowded
            else row
            for row in rows
        ]
        offered = {t.ledger_account for row in rows for t in row.targets if not t.is_new}
        near = mapping.near_pairs([row.chosen.ledger_account for row in rows], offered)
        flagged = {name for pair in near for name in pair}
        return MappingView(
            rows=tuple(replace(row, near=row.chosen.ledger_account in flagged) for row in rows),
            near=tuple(near),
        )

    def import_view(self) -> ImportView:
        with Feed.open(self._feed_path) as feed:
            found = [
                ImportItem(
                    self._item_view(item, feed), tuple(feed.runs(item.item_id, now=self._clock()))
                )
                for item in self._items()
            ]
        starts = [run.started_at for i in found for run in i.runs]
        return ImportView(
            items=tuple(found),
            started_at=min(starts, default=None),
            unmapped=sum(1 for i in found for a in i.item.accounts if not a.feeds),
        )

    # What the viewer does.

    def save_credentials(
        self,
        *,
        household: bool,
        client_id: Secret | None,
        secret: Secret | None,
        environment: str,
        item_limit: str,
        used_elsewhere: str,
    ) -> None:
        if household and not self._policy.may_manage_household_credentials():
            raise Concealed
        if client_id is None or secret is None:
            raise Refused(Refusal.CREDENTIALS_INCOMPLETE)
        try:
            chosen = Environment(environment)
            limit, used = int(item_limit), int(used_elsewhere)
        except ValueError:
            raise Refused(Refusal.NOT_UNDERSTOOD) from None
        if chosen not in self._setup.environments or not 1 <= limit <= 1000 or not used >= 0:
            raise Refused(Refusal.NOT_UNDERSTOOD)
        try:
            self._store().save(
                sets.HOUSEHOLD if household else sets.personal(self._viewer.id),
                owner=None if household else self._viewer.id,
                registered_by=self._viewer.id,
                client_id=client_id,
                secret=secret,
                environment=chosen,
                item_limit=limit,
                used_elsewhere=used,
                now=self._clock(),
            )
        except sets.CredentialsInUseError:
            raise Refused(Refusal.CREDENTIALS_IN_USE) from None

    def remove_credentials(self, *, household: bool) -> None:
        if household and not self._policy.may_manage_household_credentials():
            raise Concealed
        scope = sets.HOUSEHOLD if household else sets.personal(self._viewer.id)
        try:
            self._store().remove(scope)
        except sets.CredentialsInUseError:
            raise Refused(Refusal.CREDENTIALS_IN_USE) from None

    def choose_household(self, chosen: bool) -> None:
        if not self._policy.may_use_credentials(None):
            raise Concealed
        self._store().choose_household(self._viewer.id, chosen=chosen, now=self._clock())

    def start_link(self) -> LinkStart:
        """Everything is checked before Plaid is asked for anything: approval, which Plaid
        account, and that it has a connection left."""
        store = self._store()
        scope = self._usable_scope(store)
        capacity = store.capacity(scope)
        if capacity is None or capacity.remaining < 1:
            raise Refused(Refusal.NO_SLOT)
        credentials = store.credentials(scope)
        if not self._setup.allows(credentials.environment, Operation.LINK, Operation.READ):
            raise Refused(Refusal.NOT_APPROVED)
        client = self._setup.client(credentials)
        token = self._plaid(
            lambda: client.create_link_token(
                user=self._client_user(), days_requested=DAYS_REQUESTED
            )
        )
        return LinkStart(token.token, credentials.environment, None, self._stand_in(token.token))

    def finish_link(self, public_token: Secret, institution_hint: str) -> str:
        """Exchange what Link returned, record the new Item, and start its first sync."""
        store = self._store()
        scope = self._usable_scope(store)
        credentials = store.credentials(scope)
        if not self._setup.allows(credentials.environment, Operation.LINK, Operation.READ):
            raise Refused(Refusal.NOT_APPROVED)
        client = self._setup.client(credentials)
        exchanged = self._plaid(lambda: client.exchange(public_token))
        # The Item exists at Plaid from here on, holding a slot: keep its token and record it
        # before Plaid is asked anything else, so whatever fails next, it is counted, listed,
        # and can still be synced or removed rather than leaked.
        self._vault().put(self._viewer.id, exchanged.item_id, exchanged.access_token)
        item = self._registry.add(
            exchanged.item_id,
            member=self._viewer.id,
            credentials=scope,
            institution=Institution(None, None),
            fallback_name=_institution_name(institution_hint),
            days_requested=DAYS_REQUESTED,
            now=self._clock(),
        )
        with contextlib.suppress(Refused):
            institution = self._plaid(lambda: client.institution(exchanged.access_token))
            self._registry.name_institution(item.item_id, institution)
        self._sync_later(item.item_id)
        return item.item_id

    def start_reconnect(self, item_id: str) -> LinkStart:
        """Sign an expired connection in again. The Item stays the same, so no slot is used."""
        item = self._own_item(item_id)
        credentials = self._store().credentials(item.credentials)
        if not self._setup.allows(credentials.environment, Operation.LINK, Operation.READ):
            raise Refused(Refusal.NOT_APPROVED)
        client = self._setup.client(credentials)
        token = self._vault().get(self._viewer.id, item.item_id)
        link = self._plaid(
            lambda: client.create_link_token(
                user=self._client_user(), days_requested=item.days_requested, access_token=token
            )
        )
        stand_in = (
            (("Sign in again", self._setup.stand_in.update_token(link.token)),)
            if self._setup.stand_in is not None and link.token.startswith("link-demo-")
            else ()
        )
        return LinkStart(link.token, credentials.environment, item.item_id, stand_in)

    def finish_reconnect(self, item_id: str, public_token: Secret | None) -> None:
        item = self._own_item(item_id)
        credentials = self._store().credentials(item.credentials)
        self._setup.signed_in_again(credentials.environment, public_token)
        self._registry.set_state(item.item_id, ItemState.WORKING)
        self._sync_later(item.item_id)

    def resync(self, item_id: str) -> None:
        item = self._own_item(item_id)
        credentials = self._store().credentials(item.credentials)
        if not self._setup.allows(credentials.environment, Operation.READ):
            raise Refused(Refusal.NOT_APPROVED)
        self._sync_later(item.item_id)

    def remove(self, item_id: str) -> None:
        """End the connection at Plaid, which frees its slot, then forget its token."""
        item = self._own_item(item_id)
        credentials = self._store().credentials(item.credentials)
        if not self._setup.allows(credentials.environment, Operation.REMOVE):
            raise Refused(Refusal.NOT_APPROVED)
        client = self._setup.client(credentials)
        token = self._vault().get(self._viewer.id, item.item_id)
        self._plaid(lambda: client.remove(token))
        self._vault().delete(self._viewer.id, item.item_id)
        self._registry.remove(item.item_id, at=self._clock())

    def confirm(
        self, submitted: Sequence[Mapping], *, near_seen: Collection[tuple[str, str]] = ()
    ) -> int:
        """Record each mapping the member confirmed. Returns how many were recorded.

        A target must be one the page offered: a new ledger account, named by the member and
        owned by them and the co-owners they chose, or an account the member already owns,
        whose owners stay as they are. A typed name is only ever a new account; one the books
        have, whoever owns it, is refused (``hearth.plaid.mapping``). One ledger account is fed
        by one connection at most, or every transaction would be imported twice.

        Nothing is saved while a confirmed row needs another look, or while what was chosen
        puts two targets a word apart that are not among `near_seen`, the pairs the page the
        member confirmed on named. Either raises `Held` with the form as it was sent.
        """
        rows = {row.account.account_id: row for row in self.mapping().rows}
        for entry in submitted:
            row = rows.get(entry.account_id)
            if row is None:
                raise Concealed
            if entry.confirm and not _understood(row, entry):
                raise Refused(Refusal.NOT_UNDERSTOOD)
        view = self.mapping(submitted)
        sent = {entry.account_id for entry in submitted if entry.confirm}
        chosen = [row for row in view.rows if row.account.account_id in sent]
        seen = {frozenset(pair) for pair in near_seen}
        unseen = {name for pair in view.near if frozenset(pair) not in seen for name in pair}
        # Each row a new warning touches is confirmed again, with the warning in view, whatever
        # else holds the form.
        again = replace(
            view,
            rows=tuple(
                replace(row, confirmed=False)
                if row.confirmed and row.chosen.ledger_account in unseen
                else row
                for row in view.rows
            ),
        )
        if any(row.problem for row in chosen):
            raise Held(Hold.PROBLEMS, again)
        if any(row.chosen.ledger_account in unseen for row in chosen):
            raise Held(Hold.NEAR, again)
        if any(row.chosen.taken for row in chosen):
            raise Refused(Refusal.TARGET_TAKEN)
        owned: list[tuple[ToMap, frozenset[MemberId]]] = []
        for row in chosen:
            owners = row.owners()
            if owners is None or not self._policy.may_feed(owners):
                raise Refused(Refusal.NOT_UNDERSTOOD)
            owned.append((row, owners))
        for row, owners in owned:
            account = row.chosen.account or hearth_accounts.register(
                self._db,
                ledger_account=row.chosen.ledger_account,
                name=_account_name(row.name) or row.default_name,
                owners=owners,
            )
            self._registry.confirm(
                row.account.account_id, account.id, by=self._viewer.id, now=self._clock()
            )
        return len(owned)

    def upload_statement(self, account_id: str, filename: str, data: bytes) -> None:
        """Keep a statement file for the import, to fill history a bank did not send."""
        account = self._registry.account(account_id)
        if account is None:
            raise Concealed
        self._own_item(account.item_id)
        if not data or len(data) > STATEMENT_MAX_BYTES:
            raise Refused(Refusal.STATEMENT_UNREADABLE)
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise Refused(Refusal.STATEMENT_UNREADABLE) from None
        folder = self._data_dir / STATEMENTS_DIR / str(self._viewer.id)
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        stored = f"{secrets.token_hex(12)}.csv"
        path = folder / stored
        path.touch(mode=0o600, exist_ok=False)
        path.write_bytes(data)
        self._db.execute(
            "INSERT INTO statement_uploads (member_id, account_id, original_name, stored_as,"
            " size_bytes, uploaded_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                self._viewer.id,
                account_id,
                _institution_name(filename) or "statement.csv",
                f"{STATEMENTS_DIR}/{self._viewer.id}/{stored}",
                len(data),
                self._clock().isoformat(),
            ),
        )

    # How.

    def _store(self) -> CredentialStore:
        return CredentialStore(self._db, self._vault())

    def _vault(self) -> TokenVault:
        return TokenVault(self._db, self._setup.keys())

    def _usable_scope(self, store: CredentialStore) -> str:
        scope = store.scope_for(self._viewer.id)
        if scope is None:
            raise Refused(Refusal.NO_PLAID_ACCOUNT)
        owner = store.get(scope)
        if owner is None or not self._policy.may_use_credentials(owner.owner):
            raise Refused(Refusal.NO_PLAID_ACCOUNT)
        return scope

    def _plaid_account(self, store: CredentialStore, scope: str) -> PlaidAccountView | None:
        found = store.get(scope)
        if found is None or not self._policy.may_use_credentials(found.owner):
            return None
        capacity = store.capacity(scope)
        assert capacity is not None  # noqa: S101 - the set was found just above
        return PlaidAccountView(
            scope=scope,
            is_household=found.is_household,
            environment=found.environment,
            registered_by=self._name(found),
            saved_at=found.saved_at,
            capacity=capacity,
        )

    def _name(self, found: CredentialSet) -> str:
        member = next((m for m in self._members if m.id == found.registered_by), None)
        return member.display_name if member else "a former member"

    def _abilities(self, using: PlaidAccountView | None) -> Abilities:
        environment = using.environment if using else Environment.PRODUCTION
        return Abilities(
            link=self._setup.allows(environment, Operation.LINK, Operation.READ),
            sync=self._setup.allows(environment, Operation.READ),
            remove=self._setup.allows(environment, Operation.REMOVE),
        )

    def _items(self) -> list[Item]:
        return [
            item
            for item in self._registry.items_of(self._viewer.id)
            if item.active and self._policy.may_use_connection(item.member)
        ]

    def _own_item(self, item_id: str) -> Item:
        item = self._registry.item(item_id)
        if item is None or not item.active or not self._policy.may_use_connection(item.member):
            raise Concealed
        return item

    def _item_view(self, item: Item, feed: Feed) -> ItemView:
        requested_from = item.linked_at.date() - timedelta(days=item.days_requested - 1)
        complete = item.history is History.COMPLETE
        uploads = dict(
            self._db.execute(
                "SELECT account_id, count(*) FROM statement_uploads WHERE member_id = ?"
                " GROUP BY account_id",
                (self._viewer.id,),
            ).fetchall()
        )
        ledger = {a.id: a.ledger_account for a in self._accounts}
        accounts = tuple(
            AccountView(
                account_id=a.account_id,
                name=a.name,
                mask=a.mask,
                type=a.type,
                subtype=a.subtype,
                balance=feed.balance(a.account_id),
                coverage=Coverage(a.account_id, requested_from, feed.held(a.account_id), complete),
                feeds=ledger.get(a.hearth_account) if a.hearth_account else None,
                statements=uploads.get(a.account_id, 0),
            )
            for a in self._registry.accounts_of(item.item_id)
        )
        runs = feed.runs(item.item_id, now=self._clock())
        with _following_lock:
            following = item.item_id in _following
        others = [
            i
            for i in self._registry.items_of(item.member)
            if i.active and i.item_id != item.item_id
        ]
        return ItemView(
            item_id=item.item_id,
            institution=item.institution,
            state=item.state,
            linked_at=item.linked_at,
            last_synced_at=item.last_synced_at,
            problem=item.problem,
            history_complete=complete,
            accounts=accounts,
            last_run=runs[0] if runs else None,
            same_bank_as_another=any(
                (i.institution_id or i.institution) == (item.institution_id or item.institution)
                for i in others
            ),
            following=following,
            next_check_at=item.next_check_at if following else None,
        )

    def _to_map(
        self,
        item: Item,
        account: AccountView,
        proposals: list[str],
        alike: Collection[str],
        called: Collection[str],
    ) -> ToMap:
        linked = self._registry.account(account.account_id)
        assert linked is not None  # noqa: S101 - the view was built from this account
        root = "Liabilities" if account.is_credit else "Assets"
        mine = [
            a
            for a in self._accounts
            if self._policy.may_feed(a.owners) and a.ledger_account.split(":")[0] == root
        ]
        current = next((a for a in self._accounts if a.id == linked.hearth_account), None)
        existing = [
            Target(
                a.ledger_account,
                a,
                taken=any(
                    fed.account_id != linked.account_id for fed in self._registry.feeding(a.id)
                ),
            )
            for a in mine
        ]
        if linked.confirmed_at is not None and any(t.account == current for t in existing):
            # Already filed: the choices are the accounts that exist, starting with its own.
            targets = sorted(existing, key=lambda t: t.account != current)
        else:
            taken = {
                *(a.ledger_account for a in self._accounts if self._policy.may_feed(a.owners)),
                *proposals,
            }
            proposal = self._proposal(item, account)
            if proposal in alike and account.mask:
                proposal += mapping.component(account.mask)
            fresh = mapping.unused(proposal, taken, account.mask)
            proposals.append(fresh)
            targets = [Target(fresh, None), *existing]
        chosen = targets[0]
        choices = self._owner_choices(chosen)
        name = _called(item.institution, account)
        if name in called and account.mask:
            name += f" {account.mask}"
        return ToMap(
            account=account,
            institution=item.institution,
            targets=tuple(targets),
            chosen=chosen,
            owner_choices=choices,
            chosen_owners=choices[0].key,
            confirmed=linked.confirmed_at is not None,
            near=False,
            name=name,
            default_name=name,
        )

    def _proposal(self, item: Item, account: AccountView) -> str:
        return mapping.proposed(
            credit=account.is_credit,
            member=self._viewer.display_name,
            institution=item.institution,
            name=account.name,
            subtype=account.subtype,
        )

    def _drafted(self, row: ToMap, draft: Mapping) -> ToMap:
        """`row` as the member sent it, with what is wrong with it if they confirmed it."""
        if draft.target == NEW and row.offers_new:
            path = draft.path.strip()
            new = Target(path, None)
            problem = mapping.check_new(
                path,
                credit=row.account.is_credit,
                existing={*self._ledger_accounts, *(a.ledger_account for a in self._accounts)},
            )
            drafted = replace(
                row,
                targets=(new, *row.targets[1:]),
                chosen=new,
                chosen_owners=draft.owners
                if any(c.key == draft.owners for c in row.owner_choices)
                else row.chosen_owners,
                name=draft.name,
                problem=problem,
            )
            if drafted.problem is None and len(_account_name(draft.name)) > MAX_ACCOUNT_NAME:
                drafted = replace(drafted, problem=Problem.NAME_TOO_LONG)
        else:
            target = next(
                (t for t in row.targets if not t.is_new and t.ledger_account == draft.target),
                row.chosen,
            )
            typed = _account_name(draft.name)
            drafted = replace(
                row,
                chosen=target,
                name=draft.name or row.name,
                problem=Problem.NAME_NOT_NEW
                if draft.confirm and typed and typed != row.default_name
                else None,
            )
        return replace(drafted, confirmed=draft.confirm)

    def _owner_choices(self, target: Target) -> tuple[OwnerChoice, ...]:
        """Who owns the target: fixed for an account that exists, any set including the
        member for a new one."""
        if target.account is not None:
            return (self._choice(target.account.owners),)
        others = [m for m in self._members if m.id != self._viewer.id]
        combos = [
            frozenset({self._viewer.id, *(m.id for m in group)})
            for size in range(len(others) + 1)
            for group in itertools.combinations(others, size)
        ]
        return tuple(self._choice(owners) for owners in combos)

    def _choice(self, owners: frozenset[MemberId]) -> OwnerChoice:
        others = [
            m.display_name for m in self._members if m.id in owners and m.id != self._viewer.id
        ]
        if not others:
            label = f"Just {self._viewer.display_name}" if self._viewer.id in owners else "Others"
        elif len(others) == 1:
            label = f"Joint with {others[0]}"
        else:
            label = f"Joint with {', '.join(others[:-1])} and {others[-1]}"
        return OwnerChoice(",".join(str(o) for o in sorted(owners)), label, owners)

    def _stand_in(self, link_token: str) -> tuple[tuple[str, str], ...]:
        fake = self._setup.stand_in
        if fake is None or not link_token.startswith("link-demo-"):
            return ()
        return tuple(
            (institution.name, fake.public_token(link_token, institution.id))
            for institution in fake.institutions.values()
        )

    def _client_user(self) -> str:
        """Plaid's name for the member: stable, and nothing that identifies them."""
        return f"hearth-member-{self._viewer.id}"

    def _sync_later(self, item_id: str) -> None:
        """Sync the Item in the background, then keep asking while its history arrives
        (``hearth.plaid.sync`` says why hearth asks rather than being told). An Item already
        followed is left to its follower, which keeps its own waits."""
        with _following_lock:
            if item_id in _following:
                return
            _following.add(item_id)
        database, feed_path, setup, clock = (
            self._database,
            self._feed_path,
            self._setup,
            self._clock,
        )

        def work() -> None:
            conn = db.connect(database)
            try:
                _follow(conn, feed_path, setup, clock, item_id)
            except Exception:
                _log.exception("a background sync stopped")
            finally:
                conn.close()
                _stop_following(item_id)

        try:
            self._runner(work)
        except BaseException:
            _stop_following(item_id)
            raise

    def _plaid[T](self, call: Callable[[], T]) -> T:
        """Make a Plaid call, turning its failure into a `Refused` raised outside the handler,
        so nothing of the call's own frames travels with it."""
        code: str | None = None
        try:
            return call()
        except ApprovalRequired:
            reason = Refusal.NOT_APPROVED
        except PlaidError as error:
            reason = Refusal.PLAID_FAILED
            code = error.error_code if isinstance(error, PlaidRejectedError) else None
            _log.warning("a Plaid call failed: %s", error)
        raise Refused(reason, code)

    def _today(self) -> date:
        return self._clock().astimezone().date()


def _sync(
    conn: sqlite3.Connection, feed_path: Path, setup: PlaidSetup, clock: Clock, item: Item
) -> Synced:
    store = CredentialStore(conn, TokenVault(conn, setup.keys()))
    credentials = store.credentials(item.credentials)
    vault = TokenVault(conn, setup.keys())
    with Feed.open(feed_path) as feed:
        return sync_item(
            item,
            client=setup.client(credentials),
            access_token=vault.get(item.member, item.item_id),
            registry=Registry(conn),
            feed=feed,
            clock=clock,
        )


def _stop_following(item_id: str) -> None:
    with _following_lock:
        _following.discard(item_id)


def _follow(
    conn: sqlite3.Connection, feed_path: Path, setup: PlaidSetup, clock: Clock, item_id: str
) -> None:
    """Sync a working Item, and again after each of `check_waits` while Plaid has not sent all
    its history. Each sync is a read like any other, refused unless approved."""
    registry = Registry(conn)
    waits = check_waits()
    while True:
        item = registry.item(item_id)
        if item is None or item.state is not ItemState.WORKING:
            return
        registry.next_check(item_id, None)
        with contextlib.suppress(SyncBusyError):
            _sync(conn, feed_path, setup, clock, item)
        item = registry.item(item_id)
        if item is None or item.state is not ItemState.WORKING:
            return
        if item.history is History.COMPLETE:
            return
        wait = next(waits, None)
        if wait is None:
            _log.info("history is still arriving; the daily sync will collect the rest")
            return
        registry.next_check(item_id, clock() + wait)
        setup.wait(wait.total_seconds())


def sync_all(
    conn: sqlite3.Connection, feed_path: Path, setup: PlaidSetup, clock: Clock
) -> list[Synced | Skipped]:
    """Sync every working connection in the household, for the daily run and the command line.

    A connection already being followed for its history, here or in a `hearth serve` waiting
    to ask again, is left to its follower; one another sync is running is left to that sync.
    One connection that cannot be synced, say because its key is missing, stops only itself.
    """
    done: list[Synced | Skipped] = []
    for item in Registry(conn).all_items():
        if item.state is not ItemState.WORKING:
            continue
        with _following_lock:
            followed = item.item_id in _following
        if followed or (
            item.next_check_at is not None
            and item.next_check_at > clock()
            and is_alive(item.next_check_by)
        ):
            done.append(Skipped(item.item_id, "history is being followed"))
            continue
        try:
            done.append(_sync(conn, feed_path, setup, clock, item))
        except SyncBusyError:
            done.append(Skipped(item.item_id, "another sync is running"))
        except Exception:
            _log.exception("could not sync a connection")
    return done


def _understood(row: ToMap, entry: Mapping) -> bool:
    """Whether a confirmed row names something its row offered: a new ledger account with an
    owner choice the row offered, or one of the accounts listed for it."""
    if entry.target == NEW:
        return row.offers_new and any(c.key == entry.owners for c in row.owner_choices)
    return any(not t.is_new and t.ledger_account == entry.target for t in row.targets)


_NAME_PROBLEMS = frozenset({Problem.NAME_TOO_LONG, Problem.NAME_NOT_NEW})
"""What is wrong with the name a row was given, rather than with where it goes."""


def _twice[T](names: Iterable[T]) -> set[T]:
    """The names that come more than once."""
    return {name for name, count in Counter(names).items() if count > 1}


def _called(institution: str, account: AccountView) -> str:
    """What the institution and the bank call an account, together."""
    return f"{institution} {account.name}"


def _account_name(text: str) -> str:
    """What a member typed to call an account: printable and trimmed."""
    return "".join(ch for ch in text if ch.isprintable()).strip()


def _institution_name(text: str) -> str:
    """A name a page may show: printable, trimmed, and not too long."""
    cleaned = "".join(ch for ch in text if ch.isprintable()).strip()
    return cleaned[:MAX_INSTITUTION_NAME]
