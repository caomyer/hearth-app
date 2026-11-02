"""Accounts the household claimed that nothing brings in: no connection feeds them, nobody
named them, and no ledger hearth inherits holds them, as one claimed from a ledger hearth no
longer inherits is.

They are accounts hearth does not see (``books.unseen``), the same fact as a named account
arriving through an older door: their balance is never told, so every total leaves them out
and says so, never reading $0 and never "not known yet" for good. They differ from a named
account only in that nothing is moved into them, so a bank connected to one later counts
nothing twice. Every value is invented.
"""

import re
from dataclasses import replace
from html import unescape
from pathlib import Path

import pytest
from beancount.core.data import Open

from hearth import accounts, figures, sharing
from hearth.months import Month
from hearth.plaid.registry import Registry
from hearth.sharing import DEFAULT_SHARED, Facet
from tests.conftest import FakeClock, World, build_world
from tests.firstday import FirstDay, first_day, until_booked
from tests.test_from_zero_pages import UNESTABLISHED, readable
from tests.test_positions import BROKERAGE, CHECKING, FERNHILL, _scrubbed

APOS = "\N{RIGHT SINGLE QUOTATION MARK}"
DASH = "\N{EN DASH}"
CLAIMED_SAVINGS = "Assets:Zq:CaseyElsewhereSavings"
CLAIMED_CARD = "Liabilities:Zq:CaseyElsewhereCard"


def text(page: str) -> str:
    """A page as a reader reads it."""
    page = re.sub(r"<(script|style)\b.*?</\1>", "", page, flags=re.DOTALL)
    read = " ".join(unescape(re.sub(r"<[^>]+>", " ", page)).split())
    return re.sub(r"\s+([,.:;])", r"\1", read.replace("$ ", "$"))


def claim(world: World, ledger_account: str, name: str, *owners: str) -> accounts.Account:
    """Claim an account no ledger, connection or member's naming holds, and recompute."""
    conn = world.connect()
    try:
        account = accounts.claim(
            conn,
            ledger_account=ledger_account,
            name=name,
            owners=[world.member_ids[owner] for owner in owners],
            now=world.clock(),
        )
    finally:
        conn.close()
    world.recompute()
    return account


# In the books


def test_a_claimed_account_nothing_opens_is_one_hearth_does_not_see(world: World) -> None:
    before = world.bookkeeper.status.books
    assert before is not None

    claim(world, CLAIMED_SAVINGS, "Margay Savings", "casey")

    found = world.bookkeeper.status.books
    assert found is not None
    assert found.unseen == before.unseen | {CLAIMED_SAVINGS}
    # Never waited on, never a balance to derive: nothing will ever bring its history in.
    assert CLAIMED_SAVINGS not in found.awaiting | found.unanchored
    assert not [p for p in found.today.positions if p.holding.account == CLAIMED_SAVINGS]
    # Nothing opens it, so no row can be moved into it before its bank is connected.
    assert CLAIMED_SAVINGS not in {e.account for e in found.entries if isinstance(e, Open)}
    assert CLAIMED_SAVINGS not in world.settings.books_file.read_text()
    assert found.today.known_total == before.today.known_total


# What the pages say


def test_the_owners_total_is_a_figure_that_says_what_it_leaves_out(world: World) -> None:
    casey = world.signed_in("casey")
    before = text(casey.get("/you").text)

    claimed = claim(world, CLAIMED_CARD, "Margay Card", "casey")
    you = text(casey.get("/you").text)
    raw = casey.get(f"/accounts/{claimed.id}").text
    page = text(raw)

    figure = re.search(r"Household sees (\$[\d,]+)", you)
    assert figure is not None
    assert figure.group(1) in before  # the known total, unchanged as a number
    assert "Your net worth, excluding joint, without 2 accounts hearth does not see" in you
    assert "+ 2 accounts, not known" in you
    assert "Your net worth, without the 2 below" in you
    assert f"Margay Card Not connected {DASH} Not known owed" in you
    assert "Hearth does not know what Gharial Loan and Margay Card hold" in you
    assert "Not known yet" not in you
    assert "Balance Not known" in page
    assert "Nothing of it reaches hearth. Connect its bank whenever you like" in page
    # Nothing is moved into it, so the page neither offers moves nor names its ledger account.
    assert "Moves" not in page
    assert 'aria-label="Month"' not in raw
    assert CLAIMED_CARD not in page
    for read in (you, page):
        assert not re.search(r"Margay Card[^$]*\$\s*0(?:\.00)?(?![.,]?\d)", read)


def test_the_household_total_and_its_history_go_on_drawing_what_hearth_sees(
    world: World,
) -> None:
    world.choose("casey", frozenset(DEFAULT_SHARED | {Facet.ACCOUNT_BALANCES}))
    avery = world.signed_in("avery")
    before = text(avery.get("/").text)

    claim(world, CLAIMED_SAVINGS, "Margay Savings", "casey")
    page = text(avery.get("/").text)

    left_out = f"2 of Casey{APOS}s accounts and 1 of the joint accounts"
    assert f"Household net worth, without {left_out}" in page
    assert "Net worth, end of each month" in page
    assert "Month end Net worth Sep 30" in page
    assert f"Without {left_out} hearth does not see." in page
    # The net worth panel, that is: months before the books' history are not known below it.
    assert "Not known yet" not in page[: page.index("Left over in")]
    hero = re.compile(r"hearth does not see (\$[\d,]+)")
    found, was = hero.search(page), hero.search(before)
    assert found is not None
    assert was is not None
    assert found.group(1) == was.group(1)


def test_an_account_just_registered_is_not_known_yet_until_the_books_catch_up(
    world: World,
) -> None:
    """Between an account's registering and the recompute that follows it, as when a
    connection has just been filed, hearth does not know which it will be, so the figure
    waits for it: that is the balance about to arrive, which "Not known yet" is for."""
    conn = world.connect()
    try:
        accounts.register(
            conn,
            ledger_account=CLAIMED_SAVINGS,
            name="Margay Savings",
            owners=[world.member_ids["casey"]],
        )
    finally:
        conn.close()
    casey = world.signed_in("casey")

    arriving = text(casey.get("/you").text)
    world.recompute()
    after = text(casey.get("/you").text)

    assert "Not known yet. 1 account has no balance in hearth yet." in arriving
    assert "Margay Savings Not brought in yet" in arriving
    assert "Being brought in" in arriving
    assert "Not known yet" not in after
    assert "without 2 accounts hearth does not see" in after


# Spending


def test_spending_never_waits_on_an_account_nothing_brings_in(world: World) -> None:
    casey = world.signed_in("casey")
    before = text(casey.get("/you").text)

    claim(world, CLAIMED_CARD, "Margay Card", "casey")
    after = text(casey.get("/you").text)

    found = world.bookkeeper.status.books
    assert found is not None
    assert not found.awaiting
    spent = re.compile(r"Spent, Oct[^$]*(\$[\d,.]+)")
    was, now = spent.search(before), spent.search(after)
    assert was is not None
    assert now is not None
    assert now.group(1) == was.group(1)
    assert "History incomplete" not in after


def test_a_brokerage_beside_an_account_nothing_brings_in_still_shows_no_spending_nobody_established(
    tmp_path: Path, clock: FakeClock
) -> None:
    """A brokerage sends no transactions, so a member whose every account hearth sees is one
    has spent nothing anybody established. A claimed card nothing brings in shows hearth no
    more of what they spend, so it must not make that nothing read as $0."""
    alone = replace(FERNHILL, accounts=(FERNHILL.accounts[1],))
    fresh = first_day(tmp_path, clock, institutions=(alone,))
    fresh.link("avery", alone.id)
    fresh.map(fresh.signed_in("avery"), {"4242": BROKERAGE})
    until_booked(fresh, BROKERAGE)
    conn = fresh.connect()
    try:
        card = accounts.claim(
            conn,
            ledger_account="Liabilities:Avery:Elsewhere:Card",
            name="Elsewhere card",
            owners=[fresh.member_ids["avery"]],
            now=clock(),
        )
    finally:
        conn.close()
    fresh.bookkeeper.recompute(wait=True)
    found = fresh.books()

    assert not figures.flows_cover(found, [card, *_held(fresh, BROKERAGE)], Month(2026, 10))
    for viewer in ("avery", "blake"):
        client = fresh.signed_in(viewer)
        for path in ("/", "/you", "/net-worth", "/members/avery", f"/accounts/{card.id}"):
            response = client.get(path)
            if response.status_code == 404:
                continue
            page = readable(response.text).replace("$0.00 owed", "")
            assert not UNESTABLISHED.search(page), (viewer, path, UNESTABLISHED.search(page))
    you = " ".join(readable(fresh.signed_in("avery").get("/you").text).split())
    assert "without 1 account hearth does not see" in you
    # Both reasons hold, so both are named: neither alone would be true.
    assert "Brokerage accounts only" not in you
    assert "Brokerage and unconnected accounts only" in you
    assert "every account of yours here is a brokerage account or one hearth does not see" in you
    assert (
        "Plaid does not send a brokerage's transactions. Accounts hearth does not see are not"
        " connected"
    ) in you


def _held(fresh: FirstDay, ledger_account: str) -> list[accounts.Account]:
    conn = fresh.connect()
    try:
        return [a for a in accounts.all_accounts(conn) if a.ledger_account == ledger_account]
    finally:
        conn.close()


# Whom it tells


COMPOSITIONS = {
    "a named loan": (),
    "and a claimed savings account": ((CLAIMED_SAVINGS, "Margay Savings"),),
    "and a claimed card": ((CLAIMED_CARD, "Margay Card"),),
    "and both": ((CLAIMED_SAVINGS, "Margay Savings"), (CLAIMED_CARD, "Margay Card")),
}
"""What Casey's accounts hearth does not see may be, beside the ones it does: one named, or
with claimed ones too. Each leaves Casey's known total exactly where it was."""

LEVELS = {
    "nothing": frozenset[Facet](),
    "spending only": frozenset({Facet.MONTHLY_SPENDING_TOTAL}),
    "the default": frozenset(sharing.DEFAULT_SHARED),
    "all but balances": frozenset(set(Facet) - {Facet.ACCOUNT_BALANCES}),
}
"""What Casey may share with Blake, never balances, which the count goes with."""

PAGES = ("/", "/net-worth", "/members/casey")


def _composed(tmp_path: Path, clock: FakeClock, claimed: tuple[tuple[str, str], ...]) -> World:
    tmp_path.mkdir()
    fresh = build_world(tmp_path, clock)
    for ledger_account, name in claimed:
        claim(fresh, ledger_account, name, "casey")
    return fresh


@pytest.mark.parametrize("level", LEVELS)
def test_how_many_accounts_hearth_does_not_see_reaches_only_whoever_may_see_the_balances(
    tmp_path: Path, clock: FakeClock, level: str
) -> None:
    """Blake may not see Casey's balances. Whatever Casey's accounts hearth does not see are,
    one named or claimed ones beside it, Blake's pages read the same to the byte: whether a
    line appears, its words, its order and whom it names depend only on what Blake may see."""
    pages: dict[str, dict[str, str]] = {}
    for i, (composition, claimed) in enumerate(COMPOSITIONS.items()):
        fresh = _composed(tmp_path / str(i), clock, claimed)
        fresh.choose("casey", LEVELS[level])
        blake = fresh.signed_in("blake")
        pages[composition] = {path: _scrubbed(blake.get(path).text) for path in PAGES}
    first, *rest = pages.values()
    for composition, read in zip(COMPOSITIONS, [first, *rest], strict=True):
        for path in PAGES:
            assert read[path] == first[path], (level, composition, path)
    seen = " ".join(text(page) for page in first.values())
    assert "Margay" not in seen
    if Facet.TRANSACTIONS not in LEVELS[level]:
        # A row paid into the named loan names it with the transactions it is one of.
        assert "Gharial" not in seen
    assert not re.search(rf"\d+ of Casey{APOS}s accounts", seen)
    if Facet.NET_WORTH_TOTAL in LEVELS[level]:
        assert f"some of Casey{APOS}s accounts" in seen


def test_whoever_may_see_the_balances_is_told_how_many(tmp_path: Path, clock: FakeClock) -> None:
    """The count goes with the balances, as `Worth.awaiting` does, so it does reach Blake once
    Casey shares them: the pages above read alike because the count is withheld, not because
    the claimed accounts were missed."""
    told = []
    for i, claimed in enumerate((COMPOSITIONS["a named loan"], COMPOSITIONS["and both"])):
        fresh = _composed(tmp_path / str(i), clock, claimed)
        fresh.choose("casey", frozenset(DEFAULT_SHARED | {Facet.ACCOUNT_BALANCES}))
        told.append(text(fresh.signed_in("blake").get("/members/casey").text))

    assert "Casey's net worth, excluding joint, without 1 account hearth does not see" in told[0]
    assert "Casey's net worth, excluding joint, without 3 accounts hearth does not see" in told[1]
    assert "Margay Savings" in told[1]


# Connected later


def test_connecting_its_bank_later_brings_it_in_and_without_drops_it(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Nothing was moved into it, so the mapping page offers it as where the bank's account
    goes, and from then on it is valued at its bank's balance like any connected account."""
    bank = replace(FERNHILL, accounts=(FERNHILL.accounts[0],))
    fresh = first_day(tmp_path, clock, institutions=(bank,))
    conn = fresh.connect()
    try:
        accounts.claim(
            conn,
            ledger_account=CHECKING,
            name="Fernhill checking",
            owners=[fresh.member_ids["avery"]],
            now=clock(),
        )
    finally:
        conn.close()
    fresh.bookkeeper.recompute(wait=True)
    avery = fresh.signed_in("avery")
    assert CHECKING in fresh.books().unseen
    alone = avery.get("/you").text
    # Every account Avery has is one hearth does not see: no figure, and no spending either.
    assert "Not known. Hearth does not see this account, so it is never told what it holds" in (
        text(alone)
    )
    assert "every account of yours here is one hearth does not see, so nothing shows" in (
        text(alone)
    )
    assert "Unconnected accounts only" in text(alone)
    assert "brokerage" not in text(alone).lower()
    assert not UNESTABLISHED.search(readable(alone))

    fresh.link("avery", bank.id)
    conn = fresh.connect()
    try:
        (linked,) = [
            a
            for item in Registry(conn).all_items()
            for a in Registry(conn).accounts_of(item.item_id)
        ]
    finally:
        conn.close()
    mapping = avery.get("/connect/map").text
    assert f'<option value="{CHECKING}">{CHECKING}</option>' in mapping
    mapped = avery.post(
        "/connect/map",
        data={
            "csrf": fresh.csrf(avery),
            "account": [linked.account_id],
            "near": [],
            f"target-{linked.account_id}": CHECKING,
            f"confirm-{linked.account_id}": "1",
        },
    )
    assert mapped.status_code == 303, mapped.text
    until_booked(fresh, CHECKING)
    fresh.bookkeeper.recompute(wait=True)

    found = fresh.books()
    assert CHECKING not in found.unseen
    assert found.today.known_total == FERNHILL.accounts[0].current
    you = text(avery.get("/you").text)
    assert "hearth does not see" not in you
    assert "$2,500" in you
