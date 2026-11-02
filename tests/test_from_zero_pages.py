"""Every page a household on its first day can load, read the way its members read it.

Most households start with a bank login and nothing else, and have never heard of a ledger.
So on every page such a household can reach, at each step of its first day, nothing may state
a figure, a zero or an absence that nobody established, and nothing may need beancount's
vocabulary to understand. The walk reaches every page the application serves: a new page
fails it until it is taught to the walk, so whoever adds one reads it as a new household would.

The steps: nobody has connected anything; Avery has connected a bank; Avery has filed its
seven accounts, before the bank has sent any history; the bank's two years of history have
arrived; Avery has connected a brokerage too, which has not said what it holds yet; and it
has. Blake connects nothing throughout. Every value is invented.

From the moment the accounts are filed, what each holds is what the bank says, so net worth
is a figure; how they moved is not known until the history arrives. Once it has, spending and
income are known, and a zero can be one: a day on which a covered account spent nothing, or a
chart's axis. So on those steps the charts, and the tables that read them out, are left out of
the search for a stated zero, as they are for a reader (``tests.firstday.shown``); every other
line of every page is still searched, and every word of every page, charts included, is still
searched for bookkeeping. The brokerage's balance cannot be worked out from anything its bank
sends, so Avery's figure is not known again, and must say so rather than read as zero, until
the brokerage says what the account holds. Then it is a figure again, and every page that
was honest while the brokerage was empty is read once more, as that is when one stops being.
"""

import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from html import unescape

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from hearth import accounts, refresh
from hearth.plaid.fake import FakeAccount, FakeHolding, FakeInstitution
from hearth.plaid.registry import Registry
from hearth.web.routes import ROUTERS
from tests.firstday import PATHS, ZERO, FirstDay, until_booked, until_transacted

NOT_WALKED = {
    "/healthz": "answers machines, not people",
}
"""Pages the walk does not read, and why. Everything else is read at every step."""

UNESTABLISHED = re.compile(
    ZERO + r"|\bNo (income|spending|transactions|change)\b"
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
    "/review/where/account": ("Ledger account",),
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
BROKERAGE = "a brokerage connected"
HOLDINGS = "its holdings arrived"
HISTORY_IN = (
    ARRIVED,
    "a first decision made",
    "before an account is named",
    "an account named",
    BROKERAGE,
    HOLDINGS,
)
"""Steps on which the history is in, so a chart's zero can be one."""

QUARRY = FakeInstitution(
    "ins_quarry",
    "Quarry Brokerage",
    (
        FakeAccount(
            "ira",
            "Individual account",
            "8818",
            "investment",
            "brokerage",
            Decimal("18452.30"),
            holdings=(
                FakeHolding("ZQTM", "Quarry Total Market", Decimal("101.375"), Decimal("142.06")),
                FakeHolding(None, "Quarry Stable Value Trust", Decimal(25), Decimal("10.00")),
            ),
        ),
    ),
)
"""An invented brokerage, whose balance is what its investments are worth: no opening
balance can be derived from it, so what Avery holds is not known again, never $0, until it
says what it holds: a fund, a fraction of a share and all, a trust with no ticker, and its
cash, which together are worth the balance it reports."""


AVERYS = r"\$\s*12,771"
"""Avery's net worth, as the bank's balances add up: what the seven accounts hold, less what
the five cards owe, the headline rounding it to the dollar."""


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
    yield from decide(fresh, avery)
    yield from name_where_it_went(fresh, avery)
    yield from walk(fresh, NAMED_STEP)
    fresh.plaid.institutions[QUARRY.id] = QUARRY
    # Plaid prepares a brokerage's holdings after it is linked, as it often does.
    fresh.plaid.fail_next("/investments/holdings/get", "PRODUCT_NOT_READY")
    fresh.link("avery", QUARRY.id)
    fresh.map(fresh.signed_in("avery"), {"8818": "Assets:Avery:Quarry:Brokerage"})
    until_booked(fresh, "Assets:Avery:Quarry:Brokerage")
    yield from walk(fresh, BROKERAGE)
    # The next morning's ask finds them ready.
    refresh.sync_then_recompute(fresh.settings, fresh.setup, fresh.bookkeeper, fresh.clock)
    yield from walk(fresh, HOLDINGS)


def decide(fresh: FirstDay, avery: TestClient) -> Iterator[Visit]:
    """Avery makes a first decision from the review page, as a member does: the first group
    with one suggestion, accepted with the form the page gave. The page it returns to, saying
    what was done and offering to undo it, is read; so is every page after, on the next step,
    with the decision among what Review says was decided."""
    page = avery.get("/review").text
    form = re.search(r'<form method="post" action="/review/groups/accept".*?</form>', page, re.S)
    assert form is not None, "nothing to accept on the review page"
    data: dict[str, list[str]] = {}
    for name, value in re.findall(r'name="([^"]+)" value="([^"]*)"', form[0]):
        data.setdefault(unescape(name), []).append(unescape(value))
    response = avery.post("/review/groups/accept", data=data)
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    landed = avery.get(location).text
    assert "Undo takes back exactly this decision" in landed
    yield Visit("a first decision made", "avery", location, landed)


NAMED_STEP = "an account named"
"""Avery has said where the card payments hearth could not match came from: a checking
account at a bank Avery has not connected, which hearth does not see."""


def name_where_it_went(fresh: FirstDay, avery: TestClient) -> Iterator[Visit]:
    """Avery answers the card payments hearth could not match as a member does: from Where did
    it go?, naming the account they came from on the page that names it."""
    where = avery.get("/review/where").text
    yield Visit("before an account is named", "avery", "/review/where", where)
    key = re.search(r'id="w-([0-9a-f]{12})"', where)
    assert key is not None, "nothing waits to be told where it went"
    assert f'href="/review/where/one?group={key[1]}"' in where, "no way to answer them one by one"
    one = avery.get(f"/review/where/one?group={key[1]}").text
    assert 'action="/review/where/row"' in one, "no row to answer on its own"
    yield Visit("before an account is named", "avery", "/review/where/one", one)
    form = avery.get(f"/review/where/account?group={key[1]}").text
    yield Visit("before an account is named", "avery", "/review/where/account", form)
    data: dict[str, list[str] | str] = {}
    for name, value in re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form):
        existing = data.setdefault(unescape(name), [])
        assert isinstance(existing, list)
        existing.append(unescape(value))
    owners = re.search(
        r'<select class="select" id="own" name="owners"[^>]*>\s*<option value="([^"]+)"', form
    )
    assert owners is not None
    # What a browser sends: the details typed, and the first owner offered, just Avery.
    data |= {
        "kind": "bank",
        "where": "Hawthorn Bank",
        "name": "Hawthorn checking",
        "owners": owners[1],
    }
    response = avery.post("/review/where/account", data=data)
    assert response.status_code == 303, response.text
    landed = avery.get(response.headers["location"]).text
    assert "Undo takes back exactly this answer" in landed
    yield Visit(NAMED_STEP, "avery", response.headers["location"], landed)
    until_unseen(fresh)


def until_unseen(fresh: FirstDay) -> None:
    """Wait for the recompute the answer started to open the account it named."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        found = fresh.bookkeeper.status.books
        if found is not None and found.unseen and not fresh.bookkeeper.status.recomputing:
            return
        time.sleep(0.01)
    raise AssertionError("the named account never reached the books")


def problems(visit: Visit) -> list[str]:
    text = readable(visit.page)
    for name in NAMED.get(visit.path, ()):
        text = text.replace(name, "")
    figures = readable(_without_charts(visit.page)) if visit.step in HISTORY_IN else text
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
    """Not knowing must not read as broken: once Avery's accounts are filed, their balances
    are what the bank says, and every page with a figure the bank's history decides says why
    that one is not known yet and what fills it."""
    filed = {(v.viewer, v.path): readable(v.page) for v in visits if v.step == "its accounts filed"}

    for page in ("/", "/you", "/net-worth"):
        assert re.search(AVERYS, filed[("avery", page)]), page
        assert "no balance in hearth yet" not in filed[("avery", page)], page
    for page in ("/", "/net-worth", "/members/avery"):
        assert re.search(AVERYS, filed[("blake", page)]), page
    assert "History incomplete" in filed[("avery", "/you")]
    assert (
        "once hearth has the history of every account behind it" in filed[("avery", "/net-worth")]
    )
    assert "Not brought in yet" in filed[("avery", "/you")]


def test_history_makes_spending_known_and_balances_stay_what_the_bank_says(
    visits: list[Visit],
) -> None:
    """The bank's history says how every account moved, and its balances what each holds: so
    spending is a figure from then on, and net worth is the same figure it was, not what the
    history adds up to."""
    arrived = {(v.viewer, v.path): readable(v.page) for v in visits if v.step == ARRIVED}

    for page in ("/", "/you", "/net-worth"):
        assert re.search(AVERYS, arrived[("avery", page)]), page
        assert "no balance in hearth yet" not in arrived[("avery", page)], page
    assert "History incomplete" not in arrived[("avery", "/you")]
    # Avery holds nothing priced by a market: none of the change came from one, said in words.
    assert "none of it from the market" in arrived[("avery", "/net-worth")]
    assert re.search(r"Spent, Oct 1.15\s*\$\s*[1-9]", arrived[("avery", "/")])
    assert "waiting in Review" in arrived[("avery", "/connect/import")]
    assert "In your figures and in Review" in arrived[("avery", "/connect/import")]
    assert "is the one its bank reported" in arrived[("avery", "/connect/import")]


def test_an_account_whose_balance_cannot_be_worked_out_stays_not_known_never_zero(
    visits: list[Visit],
) -> None:
    """The brokerage's bank reports what its investments are worth, which no transaction it
    sends adds up to: so Avery's figure is not known again, and its own page says why."""
    brokerage = {(v.viewer, v.path): readable(v.page) for v in visits if v.step == BROKERAGE}

    for page in ("/", "/you", "/net-worth"):
        assert "no balance in hearth yet" in brokerage[("avery", page)], page
        assert not re.search(AVERYS, brokerage[("avery", page)]), page
    for page in ("/", "/net-worth", "/members/avery"):
        assert "Not known yet" in brokerage[("blake", page)], page
    # Plaid sends a brokerage no transactions, so waiting on them would never end: spending
    # stays the figure it was, and the brokerage is listed as what it is.
    assert "History incomplete" not in brokerage[("avery", "/you")]
    assert re.search(r"Investments\s+Card 8818", brokerage[("avery", "/you")])
    pages = [text for (viewer, _), text in brokerage.items() if viewer == "avery"]
    assert any(
        "What an investment account holds is what its investments are worth, and its"
        " brokerage has not said what they are yet" in p
        for p in pages
    )
    assert "1 account has no balance yet" in brokerage[("avery", "/connect/import")]


AVERYS_WITH_HOLDINGS = r"\$\s*31,22(4|3\.55)"
"""Avery's net worth once the brokerage says what it holds: the bank's accounts as before,
and the brokerage at the balance it reports, which its holdings at its own prices make up,
the headline rounding it to the dollar."""


def test_a_brokerage_that_says_what_it_holds_is_a_figure_and_still_has_no_trades(
    visits: list[Visit],
) -> None:
    """What the account holds, at the brokerage's own prices, is the balance it reports, so
    Avery's figure is a figure again, not a floor. It is valued from the day it was asked, and
    nothing says the account has a history it does not: no trades come with it."""
    held = {(v.viewer, v.path): readable(v.page) for v in visits if v.step == HOLDINGS}

    for page in ("/", "/you", "/net-worth"):
        assert re.search(AVERYS_WITH_HOLDINGS, held[("avery", page)]), page
        assert "no balance in hearth yet" not in held[("avery", page)], page
        assert "at least" not in held[("avery", page)], page
    for page in ("/", "/net-worth", "/members/avery"):
        assert re.search(AVERYS_WITH_HOLDINGS, held[("blake", page)]), page
    you = held[("avery", "/you")]
    assert re.search(r"Investments\s+Card 8818\s+Holdings only\s+Wed close\s+\$18,452\.30", you)
    worth = " ".join(held[("avery", "/net-worth")].split())
    assert (
        "Card 8818 · Avery"
        " Quarry Stable Value Trust No ticker 25.000 $10.00 Wed close $250.00"
        " Quarry Total Market ZQTM 101.375 $142.06 Wed close $14,401.33"
        " Cash Uninvested \N{EN DASH} \N{EN DASH} $3,800.97"
        " Positions you can see $18,452.30"
    ) in worth
    # No month before the brokerage was asked is drawn: nothing says what it held then.
    assert "once hearth has the history of every account behind it" in worth
    (brokerage,) = [
        text
        for (viewer, path), text in held.items()
        if viewer == "avery" and path.startswith("/accounts/") and "Card 8818" in text
    ]
    assert "Hearth does not bring in a brokerage's trades" in brokerage
    assert "What its brokerage says it holds, at Wed close prices." in brokerage
    # Today is the only day every account's value is known: one point is not a line.
    assert (
        "The line appears once hearth has the history of every account behind it"
        in held[("avery", "/")]
    )
    assert (
        "no trade history arrives, even once the account shows a value"
        in held[("avery", "/connect")]
    )


def test_an_account_hearth_does_not_see_is_left_out_of_every_total_and_never_zero(
    visits: list[Visit],
) -> None:
    """Naming the account the card payments came from changes no figure: net worth is what the
    banks say the connected accounts hold, as it was, and now says what it leaves out; the
    named account's own balance is not known, never $0, and its owner reads why."""
    named = {(v.viewer, v.path): readable(v.page) for v in visits if v.step == NAMED_STEP}

    you = " ".join(named[("avery", "/you")].split())
    assert re.search(AVERYS, you)
    assert "Your net worth, excluding joint, without 1 account hearth does not see" in you
    assert "+ 1 account, not known" in you
    assert "Hearth does not know what Hawthorn checking holds" in you
    for page in ("/", "/net-worth"):
        read = " ".join(named[("avery", page)].split())
        assert re.search(AVERYS, read), page
        assert "without 1 account hearth does not see" in read, page
    # Blake sees Avery's net worth, not Avery's balances: that it is not complete, not how.
    for page in ("/", "/members/avery"):
        read = " ".join(named[("blake", page)].split())
        assert re.search(AVERYS, read), page
        assert "some accounts hearth does not see" in read, page
        assert "Hawthorn" not in read, page
        assert "1 account hearth does not see" not in read, page
    (page,) = [
        text
        for (viewer, path), text in named.items()
        if viewer == "avery"
        and path.startswith("/accounts/")
        and "Hawthorn checking" in text
        and "What hearth sees of it" in text
    ]
    assert "Not known" in " ".join(page.split())
