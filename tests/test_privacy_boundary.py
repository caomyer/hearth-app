"""The privacy boundary, attacked from every route.

For each sharing scenario and each member as viewer, this crawls every page the
application serves, through every path parameter the viewer could try and a set
of tampered query parameters and headers, and asserts two things:

* no response carries a datum the contract says the viewer may not see, and
* every datum the viewer may see does turn up somewhere, so the crawl provably
  reached the data rather than passing because it saw nothing.

The one way into the household that is not a route, ``hearth plaid adopt``, is attacked the
same way: the crawl runs again once a member holds what it took in.

If a route is added that the crawl does not know how to reach, the build fails
until it is taught to, so no route can sit outside this suite.
"""

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from pathlib import Path
from urllib.parse import quote, urlencode

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Mount

from hearth.plaid import cli as plaid_cli
from hearth.plaid.fake import FakeAccount, FakeInstitution
from hearth.sharing import DEFAULT_SHARED, Facet
from hearth.web.routes import ROUTERS
from tests import household
from tests.conftest import World

EVERYTHING = frozenset(Facet)
NOTHING: frozenset[Facet] = frozenset()

SCENARIOS: dict[str, dict[str, frozenset[Facet]]] = {
    "defaults": {},
    "avery shares everything": {"avery": EVERYTHING},
    "blake shares everything": {"blake": EVERYTHING},
    "both joint owners share everything": {"avery": EVERYTHING, "blake": EVERYTHING},
    "everyone shares everything": dict.fromkeys(household.USERNAMES, EVERYTHING),
    "everyone shares nothing": dict.fromkeys(household.USERNAMES, NOTHING),
    "blake withdraws net worth": {"blake": DEFAULT_SHARED - {Facet.NET_WORTH_TOTAL}},
    "blake shares detail but no totals": {
        "blake": frozenset({Facet.CATEGORY_BREAKDOWN, Facet.TRANSACTIONS, Facet.ACCOUNT_BALANCES})
    },
    "avery at defaults, blake shares balances, transactions and breakdown": {
        "blake": DEFAULT_SHARED
        | {Facet.ACCOUNT_BALANCES, Facet.TRANSACTIONS, Facet.CATEGORY_BREAKDOWN}
    },
    "avery keeps the joint line private, blake shares everything": {
        "avery": DEFAULT_SHARED - {Facet.NET_WORTH_TOTAL, Facet.MONTHLY_SPENDING_TOTAL},
        "blake": EVERYTHING,
    },
    "everyone also shares income": dict.fromkeys(
        household.USERNAMES, DEFAULT_SHARED | {Facet.MONTHLY_INCOME_TOTAL}
    ),
    "casey also shares income": {"casey": DEFAULT_SHARED | {Facet.MONTHLY_INCOME_TOTAL}},
    "everyone shares categories only": dict.fromkeys(
        household.USERNAMES, frozenset({Facet.CATEGORY_BREAKDOWN})
    ),
    # Last month's report: a member who shares nothing, one who shares only a spending total,
    # and one who shares categories, all at once.
    "casey shares nothing, avery only a spending total, blake categories too": {
        "casey": NOTHING,
        "avery": frozenset({Facet.MONTHLY_SPENDING_TOTAL}),
        "blake": DEFAULT_SHARED | {Facet.CATEGORY_BREAKDOWN},
    },
    "both joint owners share categories and income, casey shares nothing": {
        "avery": DEFAULT_SHARED | {Facet.CATEGORY_BREAKDOWN, Facet.MONTHLY_INCOME_TOTAL},
        "blake": DEFAULT_SHARED | {Facet.CATEGORY_BREAKDOWN, Facet.MONTHLY_INCOME_TOTAL},
        "casey": NOTHING,
    },
    "blake shares income but no spending total, casey only categories": {
        "blake": frozenset({Facet.NET_WORTH_TOTAL, Facet.MONTHLY_INCOME_TOTAL}),
        "casey": frozenset({Facet.CATEGORY_BREAKDOWN}),
    },
}

# Pages that change state are attacked in test_tampering; the crawl reads.
STATE_CHANGING_ROUTES = {
    ("POST", "/sign-in"),
    ("POST", "/sign-out"),
    ("POST", "/settings/password"),
    ("POST", "/settings/sharing/{facet}"),
    ("POST", "/admin/members"),
    ("POST", "/admin/members/{username}/reset-password"),
    ("POST", "/net-worth/recompute"),
    ("POST", "/net-worth/prices"),
    ("POST", "/net-worth/prices/refresh"),
    ("POST", "/review/groups/accept"),
    ("POST", "/review/rows/book"),
    ("POST", "/review/rows/reject"),
    ("POST", "/review/reminders"),
    ("POST", "/review/undo"),
    ("POST", "/review/where/move"),
    ("POST", "/review/where/row"),
    ("POST", "/review/where/undo"),
    ("POST", "/review/where/account"),
    ("POST", "/connect/credentials"),
    ("POST", "/connect/credentials/remove"),
    ("POST", "/connect/household"),
    ("POST", "/connect/link"),
    ("POST", "/connect/exchange"),
    ("POST", "/connect/reconnected"),
    ("POST", "/connect/exited"),
    ("POST", "/connect/items/{item_id}/sync"),
    ("POST", "/connect/items/{item_id}/remove"),
    ("POST", "/connect/accounts/{account_id}/statements"),
    ("POST", "/connect/map"),
}

# All an anonymous visitor may be served; everything else sends them to sign in.
PUBLIC_PAGES = {"/sign-in", "/healthz"}

CASEY_GROUP = "move|Assets:Zq:CaseyChecking|out|says:payee jerboa transfer"
"""What `hearth.review.where.alike` keys Casey's set-aside transfer by."""

TAMPERED_QUERIES: tuple[dict[str, str], ...] = (
    {},
    {"month": "2026-10"},
    {"month": "2026-09"},
    {"month": "not-a-month"},
    {"outcome": "price-recorded", "saved": "transactions"},
    {"outcome": "link-failed", "code": "INSTITUTION_DOWN", "request": "Zq7RequestSynthetic"},
    {"outcome": "wrong-address", "oauth_state_id": "0f0f0f0f-synthetic"},
    {"month": "2026-13"},
    {"view": "rows", "order": "confidence", "group": "Unsure", "category": "-", "page": "2"},
    {"order": "amount", "outcome": "changed", "focus": "casey-row-1"},
    {"group": "0123456789ab", "outcome": "moved", "undo": "1", "focus": "0123456789ab"},
    # Casey's own group of money whose other side hearth does not see, tried by everyone.
    {"group": hashlib.sha256(CASEY_GROUP.encode()).hexdigest()[:12]},
    {
        "viewer": "blake",
        "as": "blake",
        "username": "blake",
        "owner": "blake",
        "member": "blake",
        "member_id": "2",
        "admin": "1",
        "debug": "1",
        "all": "true",
    },
)


def path_values(world: World) -> dict[str, list[str]]:
    """Everything a curious member might put into each path parameter."""
    ids = list(world.account_ids.values())
    return {
        "username": [
            *household.USERNAMES,
            "nobody",
            "BLAKE",
            " blake",
            "blake%2F",
            "..%2Fblake",
            "blake%00",
        ],
        "item_id": [
            *world.item_ids.values(),
            "nonexistent",
            "item-demo-000000000000",
            next(iter(world.item_ids.values())).upper(),
        ],
        "account_id": [
            *ids,
            "nonexistent",
            "1",
            quote(household.ACCOUNTS[0].ledger_account, safe=""),
            ids[0].upper(),
            ids[0][:-1],
            "%2E%2E",
        ],
    }


@dataclass(frozen=True)
class Probe:
    path: str
    query: dict[str, str]
    headers: dict[str, str]

    @property
    def url(self) -> str:
        return f"{self.path}?{urlencode(self.query)}" if self.query else self.path


def served_routes() -> Iterator[APIRoute]:
    for router in ROUTERS:
        for route in router.routes:
            assert isinstance(route, APIRoute), route
            yield route


def methods(route: APIRoute) -> set[str]:
    return route.methods or set()


def readable_routes() -> Iterator[APIRoute]:
    return (route for route in served_routes() if "GET" in methods(route))


def probes(world: World) -> Iterator[Probe]:
    values = path_values(world)
    for route in readable_routes():
        paths = [route.path]
        for name in route.param_convertors:
            paths = [path.replace(f"{{{name}}}", value) for path in paths for value in values[name]]
        for path in paths:
            for query in TAMPERED_QUERIES:
                yield Probe(path, query, {})
            yield Probe(path, {}, {"HX-Request": "true", "HX-Boosted": "true"})
    # Static files must serve nothing but static files.
    for path in ("/static/../hearth.sqlite3", "/static/%2e%2e/%2e%2e/signing.key"):
        yield Probe(path, {}, {})


def everything_seen(world: World, client: TestClient) -> str:
    """Every byte the client can make the application send back, joined up."""
    seen: list[str] = []
    for probe in probes(world):
        response = client.get(probe.url, headers=probe.headers)
        seen.extend(f"{name}: {value}" for name, value in response.headers.items())
        seen.append(response.text)
    return "\n".join(seen)


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("viewer", household.USERNAMES)
def test_a_member_sees_exactly_what_the_contract_allows(
    world: World, scenario: str, viewer: str
) -> None:
    choices = dict.fromkeys(household.USERNAMES, DEFAULT_SHARED)
    choices.update(SCENARIOS[scenario])
    for username, shared in choices.items():
        world.choose(username, shared)

    seen = everything_seen(world, world.signed_in(viewer))

    leaked, missing = [], []
    for secret in household.secrets(world.account_ids):
        visible = secret.visible(viewer, choices)
        present = [text for text in secret.texts if text in seen]
        if present and not visible:
            leaked.append(f"{secret.what}: {present!r}")
        if visible and not present:
            missing.append(f"{secret.what}: {secret.texts!r}")
    assert not leaked, f"{viewer} could see private data: {leaked}"
    assert not missing, f"{viewer} could not see data shared with them: {missing}"


def test_an_anonymous_visitor_sees_nothing_at_all(world: World) -> None:
    seen = everything_seen(world, world.anonymous())

    # The redirect to sign in carries the visitor's own URL back to them, so an
    # account id they typed in is not news to them; nothing else may appear.
    leaked = [
        s.what
        for s in household.secrets(world.account_ids)
        if any(text in seen for text in s.texts) and not s.what.endswith(" id")
    ]
    assert not leaked
    client = world.anonymous()
    for probe in probes(world):
        response = client.get(probe.url, headers=probe.headers)
        public = probe.path in PUBLIC_PAGES
        assert response.status_code in ({200} if public else {303, 404}), probe.url
        assert not any(name.title() in response.text for name in household.USERNAMES)


def test_the_crawl_reaches_every_route_the_application_serves(world: World) -> None:
    mounts = [route for route in world.app.routes if isinstance(route, Mount)]
    assert [mount.path for mount in mounts] == ["/static"]
    assert len(world.app.routes) == len(mounts) + len(ROUTERS), "a route is served from elsewhere"

    served = {(method, route.path) for route in served_routes() for method in methods(route)}
    crawled = {("GET", route.path) for route in readable_routes()}
    assert served <= crawled | STATE_CHANGING_ROUTES, "teach this suite to attack the new route"
    for route in readable_routes():
        assert set(route.param_convertors) <= set(path_values(world)), route.path


def amounts(page: str) -> set[Decimal]:
    return {Decimal(text.replace(",", "")) for text in re.findall(r"\d[\d,]*\.\d\d", page)}


def test_a_joint_account_cannot_be_recovered_from_what_one_owner_shares(world: World) -> None:
    """Blake shares everything; Avery keeps the joint line's totals. Nothing Casey is sent,
    nor any difference or doubling of what Casey is sent, is the joint balance or spending."""
    scenario = "avery keeps the joint line private, blake shares everything"
    for username, shared in SCENARIOS[scenario].items():
        world.choose(username, shared)
    client = world.signed_in("casey")

    shown: set[Decimal] = set()
    for path in ("/", "/members/blake", "/members/avery", "/net-worth"):
        shown |= amounts(client.get(path).text)

    assert Decimal(household.NET_WORTH["blake"].replace(",", "")) in shown
    derivable = shown | {a - b for a in shown for b in shown}
    derivable |= {2 * a for a in derivable}
    assert Decimal(household.JOINT_NET_WORTH.replace(",", "")) not in derivable
    assert Decimal(household.JOINT_SPENDING.replace(",", "")) not in derivable


ADOPTED = FakeInstitution(
    "ins_hollowmere",
    "Hollowmere Brokerage",
    (
        FakeAccount(
            "brk", "Wrenfield Growth Account", "6161", "investment", "brokerage", Decimal("1.00")
        ),
        FakeAccount("chk", "Thistlecombe Cash", "6262", "depository", "checking", Decimal("1.00")),
    ),
)


def test_an_adopted_connection_is_its_members_alone(world: World, tmp_path: Path) -> None:
    """``hearth plaid adopt`` takes an Item made outside hearth in as one member's own
    connection. Every route is attacked again with it held, its id among those tried: nobody
    else is shown anything of it, and its member is, so the crawl reached it."""
    world.plaid.institutions[ADOPTED.id] = ADOPTED
    item_id, token = world.plaid.made_elsewhere(
        "client-avery", ADOPTED.id, days=120, linked=household.TODAY
    )
    path = tmp_path / "token"
    path.write_text(token)
    adopt = partial(plaid_cli.adopt, world.settings, world.setup, world.clock, token_file=str(path))
    assert adopt(username="avery", confirm=item_id) == 0
    world.item_ids["avery-adopted"] = item_id
    texts = (ADOPTED.name, *(account.name for account in ADOPTED.accounts))

    assert all(text in everything_seen(world, world.signed_in("avery")) for text in texts)
    for other in ("blake", "casey"):
        seen = everything_seen(world, world.signed_in(other))
        assert not [text for text in texts if text in seen], other
