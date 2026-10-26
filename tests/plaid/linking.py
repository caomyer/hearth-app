"""Linking against the stand-in posing as Plaid's production, for the plaid package's tests.

A plain module, not a test module, so that test files share it without importing each other.
"""

import json
import re
from dataclasses import dataclass, field, replace

from fastapi.testclient import TestClient

from hearth import auth
from hearth.plaid.approval import Approval, Operation
from hearth.plaid.client import DEMO_HOST, HOSTS
from tests.conftest import World, password_of


def form(world: World, client: TestClient, **fields: str) -> dict[str, str]:
    return {"csrf": world.csrf(client), **fields}


def save_keys(world: World, client: TestClient, **fields: str) -> None:
    sent = {
        "client_id": "client-dana",
        "secret": "secret-dana",
        "environment": "demo",
        "item_limit": "10",
        "used_elsewhere": "0",
        **fields,
    }
    response = client.post("/connect/credentials", data=form(world, client, **sent))
    assert response.status_code == 303


def stand_in_tokens(page: str) -> dict[str, str]:
    found = re.findall(
        r'name="public_token" value="([^"]+)">\s*'
        r'<input type="hidden" name="institution" value="([^"]+)"',
        page,
    )
    return {name: token for token, name in found}


def add_dana(world: World) -> TestClient:
    """A new member with no Plaid account of their own yet."""
    conn = world.connect()
    try:
        auth.create_member(
            conn,
            actor=None,
            username="dana",
            display_name="Dana",
            is_admin=False,
            password=password_of("dana"),
            now=world.clock(),
        )
    finally:
        conn.close()
    world.plaid.register_client("client-dana", "secret-dana")
    return world.signed_in("dana")


SERVED_AT = "https://localhost:8000"
RETURN = f"{SERVED_AT}/connect/oauth"
LINK_CALL = "/link/token/create"


@dataclass
class Relay:
    """Plaid's API, answered by the stand-in, remembering what each Link token was asked with."""

    world: World
    sent: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def __call__(self, url: str, body: bytes) -> tuple[int, bytes]:
        for host in HOSTS.values():
            if url.startswith(f"{host}/"):
                path = url.removeprefix(host)
                request = json.loads(body)
                kept = {k: v for k, v in request.items() if k not in {"client_id", "secret"}}
                self.sent.append((path, kept))
                return self.world.plaid(f"{DEMO_HOST}{path}", body)
        raise AssertionError(f"not a Plaid address: {url}")

    def link_requests(self) -> list[dict[str, object]]:
        return [request for path, request in self.sent if path == LINK_CALL]


def as_production(
    world: World, redirect_uri: str | None = RETURN, environment: str = "production"
) -> Relay:
    """Dana, with a Plaid account of their own in `environment`, on a hearth approved to
    connect, serving Plaid's way back at `redirect_uri`."""
    relay = Relay(world)
    hearth = world.app.state.hearth
    plaid = replace(
        hearth.plaid,
        stand_in=None,
        transport=relay,
        approval=Approval.of([Operation.LINK, Operation.READ]),
        redirect_uri=redirect_uri,
    )
    world.app.state.hearth = replace(hearth, plaid=plaid)
    dana = add_dana(world)
    keys = {
        "client_id": "client-dana",
        "secret": "secret-dana",
        "environment": environment,
        "item_limit": "10",
        "used_elsewhere": "0",
    }
    saved = dana.post("/connect/credentials", data={"csrf": world.csrf(dana), **keys})
    assert saved.headers["location"] == "/connect?outcome=saved"
    return relay


def dana_at(world: World, address: str) -> TestClient:
    client = TestClient(world.app, base_url=address, follow_redirects=False)
    signed_in = client.post("/sign-in", data={"username": "dana", "password": password_of("dana")})
    assert signed_in.status_code == 303
    return client


def opened_token(page: str) -> str:
    found = re.search(r'data-link-token="([^"]+)"', page)
    assert found is not None, "the page opens no Link"
    return found.group(1)
