"""Naming where a connected account goes: the ledger account a member types, and the name the
household calls it, end to end through the mapping page.

The shape is a real one: one institution sends seven accounts, five of them called the same
thing, onto books that already hold most of them. A typed name is only ever a new account, so it
can never reach an account the member does not own, however it is spelled or forged.
"""

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from html import unescape

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from hearth import accounts
from hearth.plaid.connector import NEW, Hold
from hearth.plaid.fake import FakeAccount, FakeInstitution
from hearth.plaid.mapping import Problem
from hearth.plaid.registry import Registry
from hearth.web.routes.connect import OUTCOMES, PROBLEMS
from tests import household
from tests.conftest import World, link

CARD = "CREDIT CARD"
OAKHOLLOW = FakeInstitution(
    "ins_oakhollow",
    "Oakhollow Trust",
    (
        *(
            FakeAccount(f"c{n}", CARD, f"{n}{n}0{n}", "credit", "credit card", Decimal(f"{n}0.25"))
            for n in range(1, 6)
        ),
        FakeAccount("chk", "TOTAL CHECKING", "6606", "depository", "checking", Decimal("3020")),
        FakeAccount("sav", "SAVINGS", "7707", "depository", "savings", Decimal("9000")),
    ),
)
"""Seven accounts from one invented bank: five cards it calls the same, told apart by mask."""

ALREADY = {
    "Assets:Zq:CaseySavings": "Shrew Savings",
    "Liabilities:Zq:CaseyTern": "Tern Card",
    "Liabilities:Zq:CaseyHeron": "Heron Card",
    "Liabilities:Zq:CaseyPlover": "Plover Card",
}
"""Casey's accounts the books hold besides the household's own (`household.ACCOUNTS`)."""

WHERE = {
    "1101": "Liabilities:Zq:CaseyCard",
    "2202": "Liabilities:Zq:CaseyTern",
    "3303": "Liabilities:Zq:CaseyHeron",
    "4404": "Liabilities:Zq:CaseyPlover",
    "6606": "Assets:Zq:CaseyChecking",
    "7707": "Assets:Zq:CaseySavings",
}
"""Where each account already in the books goes; the fifth card, 5505, is new."""


@dataclass(frozen=True)
class Row:
    target: str
    path: str = ""
    name: str = ""
    owners: str = ""
    confirm: bool = True


def existing(target: str) -> Row:
    return Row(target)


def new(path: str, name: str = "", owners: str = "") -> Row:
    return Row(NEW, path, name, owners)


def post_map(
    world: World, client: TestClient, rows: Mapping[str, Row], near: list[str] | None = None
) -> Response:
    data: dict[str, str | list[str]] = {
        "csrf": world.csrf(client),
        "account": list(rows),
        "near": near or [],
    }
    for account_id, row in rows.items():
        data[f"target-{account_id}"] = row.target
        data[f"path-{account_id}"] = row.path
        data[f"name-{account_id}"] = row.name
        data[f"owners-{account_id}"] = row.owners
        if row.confirm:
            data[f"confirm-{account_id}"] = "1"
    return client.post("/connect/map", data=data)


def near_named(page: str) -> list[str]:
    """The pairs a word apart the page named, as its form sends them back."""
    return [
        unescape(v) for v in re.findall(r'<input type="hidden" name="near" value="([^"]+)">', page)
    ]


def row_of(page: str, account_id: str) -> str:
    """One account's row of the mapping form."""
    start = page.index(f'name="account" value="{account_id}"')
    return page[start : page.index("</fieldset>", start)]


def own(world: World, username: str) -> str:
    """The owner choice of a new account that is the member's alone."""
    return str(world.member_ids[username])


def plaid_accounts(world: World, username: str) -> dict[str, str]:
    """A member's first connection's accounts, by kind ("depository", "credit")."""
    conn = world.connect()
    try:
        found = Registry(conn).accounts_of(world.item_ids[username])
    finally:
        conn.close()
    return {a.type: a.account_id for a in found}


def all_accounts(world: World) -> list[accounts.Account]:
    conn = world.connect()
    try:
        return accounts.all_accounts(conn)
    finally:
        conn.close()


def feeds(world: World, account_id: str) -> accounts.Account | None:
    conn = world.connect()
    try:
        linked = Registry(conn).account(account_id)
        assert linked is not None
        return next((a for a in accounts.all_accounts(conn) if a.id == linked.hearth_account), None)
    finally:
        conn.close()


@pytest.fixture
def shaped(world: World) -> Iterator[dict[str, str]]:
    """Casey connected to Oakhollow, with the books already holding six of its seven accounts.
    Yields Casey's Oakhollow accounts by mask."""
    with world.ledger.open("a") as ledger:
        ledger.writelines(f"\n{household.OPENED} open {name}\n" for name in ALREADY)
    conn = world.connect()
    try:
        for name, label in ALREADY.items():
            accounts.register(
                conn, ledger_account=name, name=label, owners=[world.member_ids["casey"]]
            )
    finally:
        conn.close()
    world.recompute()
    world.plaid.institutions[OAKHOLLOW.id] = OAKHOLLOW
    item_id = link(world, "casey", OAKHOLLOW.id)
    conn = world.connect()
    try:
        yield {a.mask or "": a.account_id for a in Registry(conn).accounts_of(item_id)}
    finally:
        conn.close()


def test_seven_accounts_from_one_bank_are_each_named_and_filed_where_they_belong(
    world: World, shaped: dict[str, str]
) -> None:
    casey = world.signed_in("casey")
    page = casey.get("/connect/map").text
    for account_id in shaped.values():
        row = row_of(page, account_id)
        assert f'name="path-{account_id}"' in row
        assert f'name="name-{account_id}"' in row
    # Five cards the bank calls alike are each proposed, and named, with their own last digits.
    for mask in ("1101", "2202", "3303", "4404", "5505"):
        row = row_of(page, shaped[mask])
        assert f'value="Liabilities:Casey:Oakhollow:Card{mask}"' in row
        assert f'value="Oakhollow Trust CREDIT CARD {mask}"' in row
    # An account called like no other keeps the plain name.
    assert 'value="Oakhollow Trust TOTAL CHECKING"' in row_of(page, shaped["6606"])

    choices = {shaped[mask]: existing(target) for mask, target in WHERE.items()}
    choices[shaped["5505"]] = new("Liabilities:Zq:Oakhollow:Wren", "Wren card", own(world, "casey"))
    held = post_map(world, casey, choices, near_named(page))

    # Choosing the existing cards puts them a word apart, which the first page could not say.
    assert held.status_code == 400
    assert unescape(OUTCOMES[Hold.NEAR.value]) in unescape(held.text)
    assert "<code>Liabilities:Zq:CaseyTern</code> and <code>Liabilities:Zq:CaseyHeron</code>" in (
        held.text
    )
    assert all(feeds(world, account_id) is None for account_id in shaped.values())
    assert 'value="Liabilities:Zq:Oakhollow:Wren"' in held.text
    assert 'value="Wren card"' in held.text

    confirmed = post_map(world, casey, choices, near_named(held.text))

    assert confirmed.status_code == 303
    for mask, target in WHERE.items():
        filed = feeds(world, shaped[mask])
        assert filed is not None
        assert filed.ledger_account == target
    wren = feeds(world, shaped["5505"])
    assert wren is not None
    assert (wren.ledger_account, wren.name) == ("Liabilities:Zq:Oakhollow:Wren", "Wren card")
    assert wren.owners == {world.member_ids["casey"]}
    others = {a.ledger_account: a for a in all_accounts(world)}
    assert others["Liabilities:Zq:CaseyTern"].name == "Tern Card"


def test_the_import_tells_apart_accounts_the_bank_calls_the_same(
    world: World, shaped: dict[str, str]
) -> None:
    casey = world.signed_in("casey")
    page = casey.get("/connect/import").text

    for mask in ("1101", "2202", "3303", "4404", "5505"):
        assert f"Oakhollow Credit card &bull;&bull;&nbsp;{mask}:" in page
    # An account no other here would be mistaken for keeps the plain label, punctuated alike.
    assert re.search(r"Oakhollow Checking: \d", page)
    assert "Oakhollow Checking &bull;" not in page


@pytest.mark.parametrize(
    ("kind", "typed", "problem"),
    [
        ("depository", "Assets:Zq:AverySolo", Problem.IN_USE),
        ("depository", "Assets:Zq:Orphan", Problem.IN_USE),
        ("depository", "Assets:Zq:CaseyChecking", Problem.IN_USE),
        ("depository", "Assets:Zq:Joint", Problem.IN_USE),
        ("credit", "Liabilities:Zq:CaseyCard", Problem.IN_USE),
        ("depository", "Assets:Zq:AverySolo:Within", Problem.IN_USE),
        ("depository", "Assets:Zq", Problem.IN_USE),
        ("depository", "Liabilities:Zq:CaseyCard", Problem.UNDER_ASSETS),
        ("credit", "Assets:Zq:AverySolo", Problem.UNDER_LIABILITIES),
        ("depository", " Assets:Zq:AverySolo ", Problem.IN_USE),
    ],
)
def test_a_typed_name_never_reaches_an_account_that_exists_whoever_owns_it(
    world: World, kind: str, typed: str, problem: Problem
) -> None:
    blake = world.signed_in("blake")
    account_id = plaid_accounts(world, "blake")[kind]
    before = all_accounts(world)

    response = post_map(world, blake, {account_id: new(typed, "Mine now", own(world, "blake"))})

    assert response.status_code == 400
    row = row_of(response.text, account_id)
    assert PROBLEMS[problem] in row
    assert 'aria-invalid="true"' in row
    # Nothing is filed, created or reassigned, and the answer is the same whoever owns it.
    assert feeds(world, account_id) is None
    assert all_accounts(world) == before
    for fixture in household.ACCOUNTS:
        if "blake" not in fixture.owners:
            assert fixture.name not in response.text


@pytest.fixture
def probed(world: World) -> str:
    """One account each for Blake, Avery, Avery and Blake jointly, and nobody, each with a name
    beneath it, so a typed name can hit each owner's at, under or above it."""
    conn = world.connect()
    try:
        with world.ledger.open("a") as ledger:
            for owner, owners in PROBED.items():
                name = f"{probe(owner)}:Deep"
                ledger.write(f"\n{household.OPENED} open {name}\n")
                if owners:
                    ids = [world.member_ids[u] for u in owners]
                    accounts.register(conn, ledger_account=name, name=owner, owners=ids)
    finally:
        conn.close()
    world.recompute()
    return plaid_accounts(world, "blake")["depository"]


PROBED = {"Own": ("blake",), "Other": ("avery",), "Joint": ("avery", "blake"), "Unowned": ()}
"""Blake's view of who owns each probed account: Blake, another member, both, or nobody."""


def probe(owner: str) -> str:
    """Where the probed account of `owner` sits: two words from every other, so no two are
    named as a pair a word apart."""
    return f"Assets:{owner}:Zp{owner}"


def normalised(page: str, typed: str) -> str:
    """A held mapping page with the path typed, and the pairs a word apart it names among the
    viewer's own accounts, taken out, and its spacing evened."""
    page = re.sub(
        r'\s*<div class="alert alert-warn mt-6" id="near">.*?</div>\s*</div>', "", page, flags=re.S
    )
    page = re.sub(r'\s*<input type="hidden" name="near" value="[^"]*">', "", page)
    page = page.replace(" is-warn", "")
    page = re.sub(rf'(name="path-[^"]+" value="){re.escape(typed)}"', r'\1TYPED"', page)
    return re.sub(r"\s+", " ", page)


def test_a_name_in_use_is_refused_with_one_answer_whoever_owns_it_and_wherever_it_sits(
    world: World, probed: str
) -> None:
    blake = world.signed_in("blake")
    pages = {}
    for owner in PROBED:
        for shape, typed in {
            "exact": f"{probe(owner)}:Deep",
            "above": probe(owner),
            "beneath": f"{probe(owner)}:Deep:Within",
        }.items():
            # What the page would name a word apart among Blake's own accounts, as seen.
            seen = [f"{typed} {probe(o)}:Deep" for o, owners in PROBED.items() if "blake" in owners]
            response = post_map(
                world, blake, {probed: new(typed, owners=own(world, "blake"))}, seen
            )
            assert response.status_code == 400
            pages[owner, shape] = normalised(response.text, typed)

    # The whole page is the same, byte for byte, whoever owns the name hit and wherever it sits.
    assert len(set(pages.values())) == 1
    row = row_of(pages["Own", "exact"], probed)
    assert PROBLEMS[Problem.IN_USE] in row
    assert 'value="TYPED"' in row
    assert feeds(world, probed) is None


@pytest.mark.parametrize("owners", [("avery",), ("avery", "casey"), ()])
@pytest.mark.parametrize("shape", ["exact", "above", "two above", "beneath", "beside"])
def test_the_proposal_is_the_same_whatever_the_member_cannot_see(
    world: World, shape: str, owners: tuple[str, ...]
) -> None:
    blake = world.signed_in("blake")
    account_id = plaid_accounts(world, "blake")["depository"]

    def proposal() -> str:
        row = row_of(blake.get("/connect/map").text, account_id)
        (found,) = re.findall(rf'name="path-{account_id}" value="([^"]+)"', row)
        return str(found)

    natural = proposal()
    parent, last = natural.rsplit(":", 1)
    unseen = {
        "exact": natural,
        "above": parent,
        "two above": parent.rsplit(":", 1)[0],
        "beneath": f"{natural}:Within",
        "beside": f"{parent}:Beside{last}",
    }[shape]
    with world.ledger.open("a") as ledger:
        ledger.write(f"\n{household.OPENED} open {unseen}\n")
    if owners:
        conn = world.connect()
        try:
            ids = [world.member_ids[u] for u in owners]
            accounts.register(conn, ledger_account=unseen, name="Unseen", owners=ids)
        finally:
            conn.close()
    world.recompute()

    assert proposal() == natural


def test_a_name_typed_for_an_account_already_in_the_books_is_held_never_dropped(
    world: World, shaped: dict[str, str]
) -> None:
    casey = world.signed_in("casey")
    account_id = shaped["2202"]
    page = casey.get("/connect/map").text
    untouched = Row("Liabilities:Zq:CaseyTern", name="Oakhollow Trust CREDIT CARD 2202")

    held = post_map(
        world, casey, {account_id: Row(untouched.target, name="Tern")}, near_named(page)
    )

    assert held.status_code == 400
    row = row_of(held.text, account_id)
    assert PROBLEMS[Problem.NAME_NOT_NEW] in row
    assert 'value="Tern"' in row
    assert "selected>Liabilities:Zq:CaseyTern<" in row
    assert feeds(world, account_id) is None

    kept = post_map(world, casey, {account_id: untouched}, near_named(held.text))

    assert kept.status_code == 303
    filed = feeds(world, account_id)
    assert filed is not None
    assert (filed.ledger_account, filed.name) == ("Liabilities:Zq:CaseyTern", "Tern Card")


def test_a_pair_a_word_apart_is_confirmed_again_even_when_the_form_was_held_for_another_row(
    world: World, shaped: dict[str, str]
) -> None:
    casey = world.signed_in("casey")
    page = casey.get("/connect/map").text
    pair = {
        shaped["2202"]: existing("Liabilities:Zq:CaseyTern"),
        shaped["3303"]: existing("Liabilities:Zq:CaseyHeron"),
    }
    wren = shaped["5505"]

    held = post_map(
        world,
        casey,
        {**pair, wren: new("not a ledger name", owners=own(world, "casey"))},
        near_named(page),
    )

    assert held.status_code == 400
    assert unescape(OUTCOMES[Hold.PROBLEMS.value]) in unescape(held.text)
    assert "<code>Liabilities:Zq:CaseyTern</code> and <code>Liabilities:Zq:CaseyHeron</code>" in (
        held.text
    )
    # Sent again as the held page has it: the path put right, each box as it is shown.
    shown = {a: "checked" in row_of(held.text, a).split("map-confirm")[1] for a in pair}
    again = {
        account_id: Row(row.target, confirm=shown[account_id]) for account_id, row in pair.items()
    }
    resent = post_map(
        world,
        casey,
        {**again, wren: new("Liabilities:Zq:Oakhollow:Wren", owners=own(world, "casey"))},
        near_named(held.text),
    )

    assert resent.headers["location"] == "/connect/map?outcome=partly-confirmed"
    assert all(feeds(world, account_id) is None for account_id in pair)
    assert feeds(world, wren) is not None


def test_an_untouched_proposal_is_taken_while_an_account_sits_at_its_parent_path(
    world: World, shaped: dict[str, str]
) -> None:
    with world.ledger.open("a") as ledger:
        ledger.write(f"\n{household.OPENED} open Assets:Casey:Oakhollow\n")
    conn = world.connect()
    try:
        accounts.register(
            conn,
            ledger_account="Assets:Casey:Oakhollow",
            name="Oakhollow",
            owners=[world.member_ids["casey"]],
        )
    finally:
        conn.close()
    world.recompute()
    casey = world.signed_in("casey")
    account_id = shaped["6606"]
    page = casey.get("/connect/map").text
    (proposal,) = re.findall(rf'name="path-{account_id}" value="([^"]+)"', row_of(page, account_id))

    response = post_map(
        world, casey, {account_id: new(proposal, owners=own(world, "casey"))}, near_named(page)
    )

    assert response.status_code == 303
    filed = feeds(world, account_id)
    assert filed is not None
    assert filed.ledger_account == proposal == "Assets:Casey:OakhollowChecking"


def test_a_new_account_is_always_its_members_whatever_owners_are_sent(world: World) -> None:
    blake = world.signed_in("blake")
    account_id = plaid_accounts(world, "blake")["depository"]
    before = all_accounts(world)

    forged = post_map(
        world, blake, {account_id: new("Assets:Zq:Fresh", owners=own(world, "avery"))}
    )

    assert forged.headers["location"] == "/connect/map?outcome=not-understood"
    assert all_accounts(world) == before


def test_an_account_already_filed_is_offered_no_new_name(world: World) -> None:
    blake = world.signed_in("blake")
    account_id = plaid_accounts(world, "blake")["depository"]
    assert post_map(world, blake, {account_id: existing("Assets:Zq:Joint")}).status_code == 303
    assert f'name="path-{account_id}"' not in blake.get("/connect/map").text

    forged = post_map(
        world, blake, {account_id: new("Assets:Zq:Fresh", owners=own(world, "blake"))}
    )

    assert forged.headers["location"] == "/connect/map?outcome=not-understood"
    filed = feeds(world, account_id)
    assert filed is not None
    assert filed.ledger_account == "Assets:Zq:Joint"


def test_a_typed_name_a_word_from_another_is_confirmed_again_with_the_warning_in_view(
    world: World,
) -> None:
    casey = world.signed_in("casey")
    account_id = plaid_accounts(world, "casey")["depository"]
    page = casey.get("/connect/map").text
    choice = {account_id: new("Assets:Zq:CaseyChecking2", "Second", own(world, "casey"))}

    held = post_map(world, casey, choice, near_named(page))

    assert held.status_code == 400
    assert "<code>Assets:Zq:CaseyChecking2</code> and <code>Assets:Zq:CaseyChecking</code>" in (
        held.text
    )
    assert "checked" not in row_of(held.text, account_id).split("map-confirm")[1]
    assert feeds(world, account_id) is None

    confirmed = post_map(world, casey, choice, near_named(held.text))

    assert confirmed.status_code == 303
    filed = feeds(world, account_id)
    assert filed is not None
    assert (filed.ledger_account, filed.name) == ("Assets:Zq:CaseyChecking2", "Second")


def test_two_rows_never_name_the_same_new_account(world: World, shaped: dict[str, str]) -> None:
    casey = world.signed_in("casey")
    same = new("Liabilities:Zq:Oakhollow:Wren", owners=own(world, "casey"))

    response = post_map(world, casey, {shaped["1101"]: same, shaped["2202"]: same})

    assert response.status_code == 400
    for mask in ("1101", "2202"):
        assert PROBLEMS[Problem.TWICE] in row_of(response.text, shaped[mask])
        assert feeds(world, shaped[mask]) is None


def test_two_rows_never_name_new_accounts_one_inside_the_other(
    world: World, shaped: dict[str, str]
) -> None:
    casey = world.signed_in("casey")
    page = casey.get("/connect/map").text
    savings = shaped["7707"]
    (child,) = re.findall(rf'name="path-{savings}" value="([^"]+)"', row_of(page, savings))
    parent = child.rsplit(":", 1)[0]
    rows = {
        shaped["6606"]: new(parent, owners=own(world, "casey")),
        savings: new(child, owners=own(world, "casey")),
    }

    response = post_map(world, casey, rows, near_named(page))

    assert response.status_code == 400
    for account_id in rows:
        assert PROBLEMS[Problem.TWICE] in row_of(response.text, account_id)
        assert feeds(world, account_id) is None


def test_an_unconfirmed_row_is_neither_checked_nor_saved(
    world: World, shaped: dict[str, str]
) -> None:
    casey = world.signed_in("casey")
    rows = {
        shaped["1101"]: new("Liabilities:Zq:Oakhollow:Wren", owners=own(world, "casey")),
        shaped["5505"]: Row(NEW, "not a ledger name", confirm=False),
    }

    response = post_map(world, casey, rows)

    assert response.headers["location"] == "/connect/map?outcome=partly-confirmed"
    assert feeds(world, shaped["1101"]) is not None
    assert feeds(world, shaped["5505"]) is None


@pytest.mark.parametrize(
    ("typed", "called"),
    [
        ("", "Oakhollow Trust CREDIT CARD 5505"),
        ("   ", "Oakhollow Trust CREDIT CARD 5505"),
        ("  Wren\x00 card\x1b ", "Wren card"),
    ],
)
def test_a_new_account_is_called_what_the_member_typed_or_what_the_bank_calls_it(
    world: World, shaped: dict[str, str], typed: str, called: str
) -> None:
    casey = world.signed_in("casey")
    account_id = shaped["5505"]

    post_map(world, casey, {account_id: new("Liabilities:Zq:Wren", typed, own(world, "casey"))})

    filed = feeds(world, account_id)
    assert filed is not None
    assert filed.name == called


def test_a_name_too_long_is_marked_where_it_was_typed(world: World, shaped: dict[str, str]) -> None:
    casey = world.signed_in("casey")
    account_id = shaped["5505"]

    response = post_map(
        world, casey, {account_id: new("Liabilities:Zq:Wren", "W" * 81, own(world, "casey"))}
    )

    assert response.status_code == 400
    row = row_of(response.text, account_id)
    assert PROBLEMS[Problem.NAME_TOO_LONG] in row
    assert 'id="m-' in row.split('name="name-')[1].split("</div>")[0]
    assert feeds(world, account_id) is None
