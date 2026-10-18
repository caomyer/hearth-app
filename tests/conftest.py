import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hearth import accounts, auth, db, sessions
from hearth.app import create_app
from hearth.config import Settings
from hearth.members import MemberId
from hearth.sharing import Facet
from hearth.web.deps import SESSION_COOKIE
from tests import household


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def password_of(username: str) -> str:
    return f"{username}-invented-passphrase"


@dataclass
class World:
    """A running hearth over the invented household."""

    app: FastAPI
    settings: Settings
    clock: FakeClock
    member_ids: dict[str, MemberId]
    account_ids: dict[str, str]

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


@pytest.fixture
def world(tmp_path: Path, clock: FakeClock) -> World:
    settings = Settings.in_directory(tmp_path / "data")
    app = create_app(settings, household.ledger(), clock)
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
    finally:
        conn.close()
    return World(app, settings, clock, member_ids, account_ids)
