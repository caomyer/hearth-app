"""Every page a household on its first day can load, read the way its members read it.

Most households start with a bank login and nothing else, and have never heard of a ledger.
So on every page such a household can reach, at each step of its first day, nothing may state
a figure, a zero or an absence that nobody established, and nothing may need beancount's
vocabulary to understand. The walk reaches every page the application serves: a new page
fails it until it is taught to the walk, so whoever adds one reads it as a new household would.

The steps: nobody has connected anything; Avery has connected a bank; Avery has filed its
seven accounts, before the bank has sent any history; the bank's two years of history have
arrived. Blake connects nothing throughout. Every value is invented.

Once the history is in, spending and income are known, and a zero can be one: a day on which a
covered account spent nothing, or a chart's axis. So on that step the charts, and the tables
that read them out, are left out of the search for a stated zero, as they are for a reader
(``tests.firstday.shown``); every other line of every page is still searched, and every word
of every page, charts included, is still searched for bookkeeping. What stays unknown, the
balances, the bank never sent, must still say so.
"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from html import unescape

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from hearth import accounts
from hearth.plaid.registry import Registry
from hearth.web.routes import ROUTERS
from tests.firstday import PATHS, FirstDay, until_booked, until_transacted

NOT_WALKED = {
    "/healthz": "answers machines, not people",
}
"""Pages the walk does not read, and why. Everything else is read at every step."""

UNESTABLISHED = re.compile(
    r"\$\s*0(\.00)?\b"
    r"|\bNo (income|spending|transactions|change)\b"
    r"|\bNothing (paid in|spent|earned)\b"
    r"|\bnot enough priced history\b",
    re.IGNORECASE,
)
"""A zero, an absence or a comparison stated as fact. On a first day none of them is known."""

VOCABULARY = re.compile(
    r"\b(ledgers?|books?|entry|entries|postings?|assertions?|beancount|imported|booked)\b",
    re.IGNORECASE,
)
"""Words a member must already know bookkeeping to read."""

NAMED = {
    "/connect/map": ("New ledger account", "Ledger account"),
}
"""Where a page names the beancount path itself: the member types one there, and calling it
anything else would be untrue. A line of help beside it says what it is for."""


def readable(page: str) -> str:
    """Everything a reader meets on a page: its text, chart labels included, and what is said
    aloud or on hover (labels, titles, tooltips, placeholders)."""
    page = re.sub(r"<(script|style)\b.*?</\1>", "", page, flags=re.DOTALL)
    said = re.findall(r'\b(?:aria-label|title|data-tip|placeholder|alt)="([^"]*)"', page)
    text = re.sub(r"<[^>]+>", " ", page)
    return unescape(" ".join([text, *said]))


@dataclass(frozen=True)
class Visit:
    step: str
    viewer: str
    path: str
    page: str


def served() -> set[str]:
    return {
        route.path
        for router in ROUTERS
        for route in router.routes
        if isinstance(route, APIRoute) and "GET" in (route.methods or set())
    }


def paths(fresh: FirstDay, viewer: str) -> dict[str, list[str]]:
    """Each served page as the viewer would reach it, with its path filled from the household."""
    conn = fresh.connect()
    try:
        account_ids = [a.id for a in accounts.all_accounts(conn)]
        item_ids = [i.item_id for i in Registry(conn).all_items() if i.active]
    finally:
        conn.close()
    other = "blake" if viewer == "avery" else "avery"
    fills: dict[str, Callable[[], list[str]]] = {
        "/members/{username}": lambda: [f"/members/{other}"],
        "/accounts/{account_id}": lambda: [f"/accounts/{i}" for i in account_ids],
        "/connect/items/{item_id}/remove": lambda: [f"/connect/items/{i}/remove" for i in item_ids],
    }
    return {
        route: fills[route]() if route in fills else [route]
        for route in sorted(served())
        if route not in NOT_WALKED
    }


def walk(fresh: FirstDay, step: str) -> Iterator[Visit]:
    for viewer in ("avery", "blake"):
        client = fresh.signed_in(viewer)
        for route, reached in paths(fresh, viewer).items():
            for path in reached:
                yield Visit(step, viewer, path, _get(client, fresh, route, path))
    yield Visit(step, "anyone", "/sign-in", fresh.anonymous().get("/sign-in").text)


def _get(client: TestClient, fresh: FirstDay, route: str, path: str) -> str:
    if route == "/sign-in":
        return fresh.anonymous().get(path).text
    response = client.get(path)
    assert response.status_code in {200, 404}, (path, response.status_code)
    return response.text


ARRIVED = "its history arrived"


def first_day_steps(fresh: FirstDay) -> Iterator[Visit]:
    yield from walk(fresh, "nothing connected")
    # The bank prepares its history after linking, as a real one does: none of it has come
    # when the accounts are filed.
    fresh.plaid.slow_history = True
    item_id = fresh.link("avery", "ins_willowmere")
    yield from walk(fresh, "a bank connected")
    fresh.map(fresh.signed_in("avery"), PATHS)
    until_booked(fresh, PATHS["7717"])
    yield from walk(fresh, "its accounts filed")
    fresh.plaid.slow_history = False
    fresh.plaid.history_ready(item_id)
    avery = fresh.signed_in("avery")
    synced = avery.post(f"/connect/items/{item_id}/sync", data={"csrf": fresh.csrf(avery)})
    assert synced.status_code == 303
    until_transacted(fresh, PATHS["6616"])
    yield from walk(fresh, ARRIVED)


def problems(visit: Visit) -> list[str]:
    text = readable(visit.page)
    for name in NAMED.get(visit.path, ()):
        text = text.replace(name, "")
    figures = readable(_without_charts(visit.page)) if visit.step == ARRIVED else text
    found = [m.group(0) for m in UNESTABLISHED.finditer(figures)]
    found += [m.group(0) for m in VOCABULARY.finditer(text)]
    return found


def _without_charts(page: str) -> str:
    """A page without its charts and the tables that read them out (``firstday.shown``)."""
    return re.sub(r"<svg.*?</svg>|<details.*?</details>", "", page, flags=re.DOTALL)


@pytest.fixture
def visits(fresh: FirstDay) -> list[Visit]:
    return list(first_day_steps(fresh))


def test_no_page_of_a_first_day_states_what_nobody_established_or_speaks_bookkeeping(
    visits: list[Visit],
) -> None:
    found = {f"{v.step}, {v.viewer} {v.path}": wrong for v in visits if (wrong := problems(v))}

    assert found == {}


def test_the_walk_reads_every_page_the_application_serves(visits: list[Visit]) -> None:
    read = {path for v in visits for path in [v.path]}
    routes = served() - NOT_WALKED.keys()
    for route in routes:
        pattern = re.escape(route).replace(r"\{", "{").replace(r"\}", "}")
        pattern = re.sub(r"\{[^}]+\}", "[^/]+", pattern)
        assert any(re.fullmatch(pattern, path) for path in read), (
            f"teach the first-day walk to read {route}"
        )


def test_a_first_day_explains_what_it_does_not_know(visits: list[Visit]) -> None:
    """Not knowing must not read as broken: once Avery's accounts are filed, every page with
    a figure says why it is not known and what fills it."""
    filed = {(v.viewer, v.path): readable(v.page) for v in visits if v.step == "its accounts filed"}

    for page in ("/", "/you", "/net-worth"):
        assert "The figure appears once they are brought in" in filed[("avery", page)], page
    for page in ("/", "/net-worth", "/members/avery"):
        assert "The figure appears once they are brought in" in filed[("blake", page)], page


def test_history_makes_spending_known_and_leaves_balances_unknown(visits: list[Visit]) -> None:
    """The bank's history says how every account moved, not what any of them held before it:
    spending is a figure from then on, and net worth still says why it is not one."""
    arrived = {(v.viewer, v.path): readable(v.page) for v in visits if v.step == ARRIVED}

    for page in ("/", "/you", "/net-worth"):
        assert "no balance in hearth yet" in arrived[("avery", page)], page
        assert "The figure appears once they are brought in" in arrived[("avery", page)], page
    assert "History incomplete" not in arrived[("avery", "/you")]
    assert re.search(r"Spent, Oct 1.15\s*\$\s*[1-9]", arrived[("avery", "/")])
    assert "waiting in Review" in arrived[("avery", "/connect/import")]
    assert "In your figures and in Review" in arrived[("avery", "/connect/import")]
