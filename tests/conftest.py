import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hearth import accounts, auth, books, db, members, sessions
from hearth.app import create_app
from hearth.config import Settings
from hearth.disclosure import Disclosure, rulings_in
from hearth.members import MemberId
from hearth.plaid.connector import Connector
from hearth.plaid.fake import FakeAccount, FakeInstitution, FakePlaid, FakeTransaction
from hearth.plaid.setup import PlaidSetup
from hearth.review.ids import says
from hearth.review.rulings import Action, RulingStore, Scope
from hearth.sharing import Facet
from hearth.valuation.pricestore import PriceStore
from hearth.valuation.quotes import Quote
from hearth.vault.secret import Secret
from hearth.web.deps import SESSION_COOKIE
from tests import household
from tests.vault.conftest import MemoryKeyStore

if TYPE_CHECKING:
    from tests.firstday import FirstDay


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def password_of(username: str) -> str:
    return f"{username}-invented-passphrase"


class Waits:
    """Stands in for waiting between checks for history still arriving: records each wait,
    and runs `then`, if a test sets it, in place of the time passing."""

    def __init__(self) -> None:
        self.seconds: list[float] = []
        self.then: Callable[[float], None] | None = None

    def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)
        if self.then is not None:
            self.then(seconds)


@dataclass
class World:
    """A running hearth over the invented household."""

    app: FastAPI
    settings: Settings
    clock: FakeClock
    member_ids: dict[str, MemberId]
    account_ids: dict[str, str]
    bookkeeper: books.Bookkeeper
    ledger: Path
    plaid: FakePlaid
    setup: PlaidSetup
    item_ids: dict[str, str]
    waits: Waits

    @property
    def bookkeeper_establish(self) -> books.Establish:
        """How this world's books are recomputed, for a bookkeeper of a test's own."""
        return books.from_household(self.settings, rulings=rulings_in)

    def connect(self) -> sqlite3.Connection:
        return db.connect(self.settings.database)

    def anonymous(self) -> TestClient:
        return TestClient(self.app, follow_redirects=False)

    def signed_in(self, username: str) -> TestClient:
        client = self.anonymous()
        response = client.post(
            "/sign-in", data={"username": username, "password": password_of(username)}
        )
        assert response.status_code == 303, response.text
        return client

    def csrf(self, client: TestClient) -> str:
        signer = sessions.CookieSigner(self.settings.signing_key)
        token = signer.unsign(client.cookies[SESSION_COOKIE])
        assert token is not None
        return signer.csrf_token(token)

    @contextmanager
    def connector(self, username: str) -> Iterator[Connector]:
        """A member's connector, built as a page builds it."""
        conn = self.connect()
        try:
            member = members.find(conn, username)
            assert member is not None
            yield Disclosure.load(conn, member, self.bookkeeper.status).connector(
                conn,
                database=self.settings.database,
                feed_path=self.settings.feed_database,
                data_dir=self.settings.data_dir,
                setup=self.setup,
                clock=self.clock,
                runner=run_now,
            )
        finally:
            conn.close()

    def recompute(self) -> None:
        """Recompute the books now, as the morning schedule or "Recompute now" would, and wait
        for it, behind any recompute a ruling started in the background."""
        self.bookkeeper.recompute(wait=True)

    def choose(self, username: str, shared: frozenset[Facet]) -> None:
        """Set exactly which facets a member shares."""
        conn = self.connect()
        try:
            for facet in Facet:
                conn.execute(
                    "INSERT INTO sharing_preferences (member_id, facet, shared, updated_at)"
                    " VALUES (?, ?, ?, ?) ON CONFLICT (member_id, facet)"
                    " DO UPDATE SET shared = excluded.shared",
                    (self.member_ids[username], facet.value, facet in shared, "2026-01-01"),
                )
        finally:
            conn.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(household.NOW)


class SourceNotSetUp:
    """The price source as it is before a key is bought: never asked, never fails."""

    name = "eodhd"

    def unavailable_reason(self) -> str | None:
        return "no key"

    def quotes(self, symbol: str, start: date, end: date) -> list[Quote]:
        raise AssertionError("an unavailable source is never asked")


def run_now(work: object) -> None:
    """Runs what the app would run in the background, at once, so tests see its result."""
    assert callable(work)
    work()


def institutions() -> list[FakeInstitution]:
    """One invented bank per member, with a checking account and a card (tests.household)."""
    return [
        FakeInstitution(
            f"ins_{p.username}",
            p.institution,
            (
                FakeAccount(
                    "chk",
                    p.checking,
                    p.checking_mask,
                    "depository",
                    "checking",
                    Decimal(p.checking_balance.replace(",", "")),
                    transactions=(
                        FakeTransaction(p.payee, Decimal("42.00"), household.NOW.date()),
                    ),
                ),
                FakeAccount(
                    "card",
                    p.card,
                    p.card_mask,
                    "credit",
                    "credit card",
                    Decimal(p.card_balance.replace(",", "")),
                    transactions=(),
                ),
            ),
        )
        for p in household.PLAID
    ]


def link(world: World, username: str, institution: str) -> str:
    """Connect `username` to an invented institution through the stand-in, as Link would."""
    with world.connector(username) as connector:
        start = connector.start_link(served_at="http://testserver")
        token = world.plaid.public_token(start.link_token, institution)
        return connector.finish_link(Secret(token), "")


@pytest.fixture
def world(tmp_path: Path, clock: FakeClock) -> World:
    ledger = tmp_path / "household.beancount"
    ledger.write_text(household.ledger_text())
    settings = Settings.in_directory(tmp_path / "data", ledger=ledger)
    db.initialise(settings.database)
    conn = db.connect(settings.database)
    try:
        member_ids = {}
        for username in household.USERNAMES:
            member, _ = auth.create_member(
                conn,
                actor=None,
                username=username,
                display_name=username.title(),
                is_admin=username == household.ADMIN,
                password=password_of(username),
                now=clock(),
            )
            member_ids[username] = member.id
        account_ids = {
            fixture.key: accounts.register(
                conn,
                ledger_account=fixture.ledger_account,
                name=fixture.name,
                owners=[member_ids[owner] for owner in fixture.owners],
            ).id
            for fixture in household.ACCOUNTS
        }
        for named in household.NAMED:
            account_ids[named.key] = accounts.name_account(
                conn,
                ledger_account=named.ledger_account,
                name=named.name,
                kind=accounts.Kind(named.kind),
                institution=named.institution,
                mask=named.mask,
                owners=[member_ids[owner] for owner in named.owners],
                by=member_ids[sorted(named.owners)[0]],
                now=clock(),
            ).id
        store_rulings = RulingStore(conn)
        for day, _, on, amount, review_id, group, by, onto in household.MOVED:
            store_rulings.record(
                member_id=member_ids[by],
                scope=Scope.GROUP,
                group=group,
                action=Action.ACCOUNT,
                account=onto,
                note=None,
                rows={review_id: None},
                says={review_id: says(on, day, Decimal(amount), "USD")},
                # Said the day it moved, long enough ago that a weekly review is due again.
                at=datetime.combine(day, time(9), tzinfo=UTC),
            )
        store = PriceStore(conn)
        for symbol, day, price in household.prices():
            store.record(Quote(symbol, day, price, "USD", "eodhd", "test", clock()))
    finally:
        conn.close()
    bookkeeper = books.Bookkeeper(books.from_household(settings, rulings=rulings_in), clock)
    bookkeeper.recompute()
    assert bookkeeper.status.failure is None, bookkeeper.status.failure
    fake = FakePlaid(institutions(), today=lambda: clock().date())
    keys = MemoryKeyStore()
    waits = Waits()
    setup = PlaidSetup(stand_in=fake, keys=lambda: keys, wait=waits)
    app = create_app(
        settings, bookkeeper, clock, price_source=SourceNotSetUp(), plaid=setup, runner=run_now
    )
    world = World(
        app, settings, clock, member_ids, account_ids, bookkeeper, ledger, fake, setup, {}, waits
    )
    for p in household.PLAID:
        client_id, secret = f"client-{p.username}", f"secret-{p.username}"
        fake.register_client(client_id, secret)
        with world.connector(p.username) as connector:
            connector.save_credentials(
                household=False,
                client_id=Secret(client_id),
                secret=Secret(secret),
                environment="demo",
                item_limit="10",
                used_elsewhere="4",
            )
        world.item_ids[p.username] = link(world, p.username, f"ins_{p.username}")
    waits.seconds.clear()
    return world


@pytest.fixture
def fresh(tmp_path: Path, clock: FakeClock) -> FirstDay:
    """A household on its first day: members, no ledger, nothing connected (`tests.firstday`)."""
    from tests.firstday import first_day

    return first_day(tmp_path, clock)
