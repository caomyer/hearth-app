"""The privacy boundary, attacked from every route.

For each sharing scenario and each member as viewer, this crawls every page the
application serves, through every path parameter the viewer could try and a set
of tampered query parameters and headers, and asserts two things:

* no response carries a datum the contract says the viewer may not see, and
* every datum the viewer may see does turn up somewhere, so the crawl provably
  reached the data rather than passing because it saw nothing.

If a route is added that the crawl does not know how to reach, the build fails
until it is taught to, so no route can sit outside this suite.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import quote, urlencode

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Mount

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
}

# Pages that change state are attacked in test_tampering; the crawl reads.
STATE_CHANGING_ROUTES = {
    ("POST", "/sign-in"),
    ("POST", "/sign-out"),
    ("POST", "/settings/password"),
    ("POST", "/settings/sharing/{facet}"),
    ("POST", "/admin/members"),
    ("POST", "/admin/members/{username}/reset-password"),
}

# All an anonymous visitor may be served; everything else sends them to sign in.
PUBLIC_PAGES = {"/sign-in", "/healthz"}

TAMPERED_QUERIES: tuple[dict[str, str], ...] = (
    {},
    {"month": household.THIS_MONTH},
    {"month": household.LAST_MONTH},
    {"month": "not-a-month"},
    {"month": "2026-13"},
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
        if secret.text in seen and not visible:
            leaked.append(f"{secret.what}: {secret.text!r}")
        if visible and secret.text not in seen:
            missing.append(f"{secret.what}: {secret.text!r}")
    assert not leaked, f"{viewer} could see private data: {leaked}"
    assert not missing, f"{viewer} could not see data shared with them: {missing}"


def test_an_anonymous_visitor_sees_nothing_at_all(world: World) -> None:
    seen = everything_seen(world, world.anonymous())

    # The redirect to sign in carries the visitor's own URL back to them, so an
    # account id they typed in is not news to them; nothing else may appear.
    leaked = [
        s.what
        for s in household.secrets(world.account_ids)
        if s.text in seen and not s.what.endswith(" id")
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


def test_at_defaults_a_non_owner_sees_every_members_totals_and_the_household_totals(
    world: World,
) -> None:
    page = world.signed_in("casey").get(f"/?month={household.THIS_MONTH}").text

    for username in household.USERNAMES:
        assert household.NET_WORTH[username] in page
        assert household.SPENDING[username] in page
    assert "Avery &amp; Blake, joint" in page
    assert household.JOINT_NET_WORTH in page
    assert household.JOINT_SPENDING in page
    assert household.HOUSEHOLD_NET_WORTH in page
    assert household.HOUSEHOLD_SPENDING in page


def amounts(page: str) -> set[Decimal]:
    return {Decimal(text.replace(",", "")) for text in re.findall(r"\d[\d,]*\.\d\d", page)}


def test_a_joint_account_cannot_be_recovered_from_what_one_owner_shares(world: World) -> None:
    scenario = "avery at defaults, blake shares balances, transactions and breakdown"
    for username, shared in SCENARIOS[scenario].items():
        world.choose(username, shared)
    client = world.signed_in("casey")
    joint = next(a for a in household.ACCOUNTS if a.key == "joint")
    joint_spending = sum(-t.amount for t in joint.transactions if t.amount < 0)

    shown = amounts(client.get(f"/members/blake?month={household.THIS_MONTH}").text)
    shown |= amounts(client.get(f"/?month={household.THIS_MONTH}").text)

    # Casey sees everything blake shares alone, and blake's total is just that.
    assert Decimal(household.NET_WORTH["blake"].replace(",", "")) in shown
    derivable = shown | {a - b for a in shown for b in shown}
    derivable |= {2 * a for a in derivable}
    assert joint.balance not in derivable
    assert joint_spending not in derivable


def test_once_both_joint_owners_share_detail_it_is_shown(world: World) -> None:
    detail = DEFAULT_SHARED | {Facet.ACCOUNT_BALANCES, Facet.TRANSACTIONS}
    world.choose("avery", detail)
    world.choose("blake", detail)
    client = world.signed_in("casey")
    joint = next(a for a in household.ACCOUNTS if a.key == "joint")

    page = client.get(f"/members/blake?month={household.THIS_MONTH}").text
    assert f"{joint.balance:,.2f}" in page
    assert joint.transactions[0].payee in page
    page = client.get(f"/?month={household.THIS_MONTH}").text
    assert household.JOINT_NET_WORTH in page
    assert household.HOUSEHOLD_NET_WORTH in page
