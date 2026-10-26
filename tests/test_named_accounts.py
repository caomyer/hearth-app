"""Accounts hearth does not see: named by a member, opened in the books, never valued, and
left out of every total, which says so.

The household (``tests.household``) has two: Casey's loan, which a payment from Casey's
checking went to, and Avery's and Blake's joint savings, which Avery moved money into from an
account of Avery's own. Each test states one contract and attacks it as a member would.
"""

import re
from html import unescape

import pytest
from beancount.core.data import Open
from fastapi.testclient import TestClient

from hearth import accounts, db
from hearth.ownbooks import MOVES_ONLY, SEEN
from hearth.review.rulings import RulingStore
from hearth.sharing import DEFAULT_SHARED, Facet
from tests import household
from tests.conftest import World

APOS = "\N{RIGHT SINGLE QUOTATION MARK}"
DASH = "\N{EN DASH}"
JOINT_SAVINGS = "Assets:Zq:JointSavings"
CASEY_LOAN = "Liabilities:Zq:CaseyLoan"


def text(page: str) -> str:
    """A page as a reader reads it."""
    page = re.sub(r"<(script|style)\b.*?</\1>", "", page, flags=re.DOTALL)
    read = " ".join(unescape(re.sub(r"<[^>]+>", " ", page)).split())
    return re.sub(r"\s+([,.:;])", r"\1", read.replace("$ ", "$"))


def rulings_recorded(world: World) -> int:
    conn = world.connect()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM review_rulings").fetchone()[0])
    finally:
        conn.close()


# In the books


def test_the_books_open_each_named_account_marked_as_one_hearth_does_not_see(
    world: World,
) -> None:
    found = world.bookkeeper.status.books
    assert found is not None

    opens = {e.account: e for e in found.entries if isinstance(e, Open)}
    joint = opens[JOINT_SAVINGS]
    assert joint.meta[SEEN] == MOVES_ONLY
    assert (joint.meta["name"], joint.meta["institution"], joint.meta["mask"]) == (
        "Tamarin Savings",
        "Okapi Credit Union",
        "8136",
    )
    # On the household's first day of history, so every row moved into it is after it opens.
    assert joint.date == household.OPENED
    assert found.unseen == {JOINT_SAVINGS, CASEY_LOAN}
    # Its balance is never known, and spending never waits on history it will never have.
    assert found.unseen <= found.unanchored
    assert not found.unseen & found.awaiting
    # What moved into it is in the books, as a move: never valued as what it holds.
    assert not [p for p in found.today.positions if p.holding.account in found.unseen]
    assert not [
        p for v in found.month_ends for p in v.positions if p.holding.account in found.unseen
    ]
    written = world.settings.books_file.read_text()
    assert f'{SEEN}: "{MOVES_ONLY}"' in written


def test_moving_money_into_an_account_hearth_does_not_see_leaves_net_worth_as_it_was(
    world: World,
) -> None:
    """The rejected design: rows summed into a bucket would make the bucket's balance, and
    net worth would fall by what moved out. A named account has the same rows, and none of
    them is ever read as a balance."""
    casey = text(world.signed_in("casey").get("/you").text)

    assert re.search(r"\$\s*4,505\b", casey)  # Casey's net worth, unmoved by the payment
    assert "333.17" not in casey.split("Your net worth")[1].split("Accounts")[0]


# What the pages say


def test_an_account_hearth_does_not_see_never_reads_as_zero(world: World) -> None:
    casey = world.signed_in("casey")
    page = text(casey.get(f"/accounts/{world.account_ids['casey_named']}").text)

    assert "Balance Not known" in page
    assert "Hearth does not see this account's own records." in page
    assert "Paid in" not in page or "$333.17" in page
    assert not re.search(r"\$\s*0(?:\.00)?(?![.,]?\d)", page)
    you = text(casey.get("/you").text)
    listed = (
        f"Hearth does not see this Gharial Loan Pangolin Lending Not connected {DASH} Not known"
    )
    assert listed in you
    assert not re.search(r"Gharial Loan[^$]*\$\s*0(?:\.00)?(?![.,]?\d)", you)


def test_a_members_own_total_says_how_many_accounts_it_leaves_out(world: World) -> None:
    page = text(world.signed_in("casey").get("/you").text)

    assert "Your net worth, excluding joint, without 1 account hearth does not see" in page
    assert "+ 1 account, not known" in page
    assert "Your net worth, without the account below" in page
    assert "Hearth does not know what Gharial Loan holds" in page


@pytest.mark.parametrize("balances", [False, True])
def test_another_members_total_says_it_is_without_some_accounts_and_counts_them_only_with_balances(
    world: World, balances: bool
) -> None:
    shared = DEFAULT_SHARED | ({Facet.ACCOUNT_BALANCES} if balances else set())
    world.choose("casey", frozenset(shared))
    blake = world.signed_in("blake")

    member = text(blake.get("/members/casey").text)
    home = text(blake.get("/").text)

    if balances:
        assert "Casey's net worth, excluding joint, without 1 account hearth does not see" in member
        assert f"1 of Casey{APOS}s accounts" in home
        assert "Gharial Loan" in member
    else:
        assert "Casey's net worth, excluding joint, without some accounts hearth does not see" in (
            member
        )
        assert "+ some accounts, not known" in member
        assert f"some of Casey{APOS}s accounts" in home
        assert "1 account" not in member
        assert "Gharial Loan" not in member + home
    assert "Without" in home  # the members table says Casey's figure is not complete


def test_the_household_total_and_its_history_say_what_they_leave_out(world: World) -> None:
    world.choose("casey", DEFAULT_SHARED)
    page = text(world.signed_in("avery").get("/").text)

    left_out = f"some of Casey{APOS}s accounts and 1 of the joint accounts"
    assert f"Household net worth, without {left_out}" in page
    # The line is drawn through the accounts hearth sees, and says it is without the rest: an
    # account hearth does not see has no history of its own to hold the month ends back.
    assert "Net worth, end of each month" in page
    assert "Month end Net worth Sep 30" in page
    assert f"Without {left_out} hearth does not see." in page


# The joint account, from the other owner's side


@pytest.mark.parametrize("averys", [frozenset(), frozenset(Facet)])
def test_the_co_owner_learns_nothing_of_the_solo_account_from_the_joint_accounts_page(
    world: World, averys: frozenset[Facet]
) -> None:
    """Avery moved money from Avery's own account into the joint one. Blake owns the joint
    account and sees its side, the day and the amount; never which account it came from nor
    the bank's words for it, whatever Avery shares."""
    world.choose("avery", averys)
    joint = world.account_ids["joint_named"]

    page = world.signed_in("blake").get(f"/accounts/{joint}?month=2026-10").text

    read = text(page)
    assert "Transfer from another account" in read
    assert "+777.31" in read
    assert "Moved in" in read
    assert "Moved in from your accounts" not in read
    for averys_only in (
        "Quokka Savings",
        world.account_ids["avery_solo"],
        "Assets:Zq:AverySolo",
        "Payee Saki Sweep",
        "You said it went here",
    ):
        assert averys_only not in page, averys_only


def test_the_owner_of_both_reads_where_the_money_came_from(world: World) -> None:
    joint = world.account_ids["joint_named"]

    page = text(world.signed_in("avery").get(f"/accounts/{joint}?month=2026-10").text)

    assert "Transfer from Quokka Savings" in page
    assert "You said it went here, Oct 3" in page
    assert "Moved in from your accounts $777.31" in page
    assert "1 move from Quokka Savings since Oct 2026" in page
    assert "Okapi Credit Union · •• 8136 · Assets:Zq:JointSavings" in page


def test_a_ledger_name_is_shown_only_to_the_accounts_owners(world: World) -> None:
    world.choose("avery", frozenset(Facet))
    world.choose("blake", frozenset(Facet))
    joint = world.account_ids["joint_named"]

    page = world.signed_in("casey").get(f"/accounts/{joint}").text

    assert "Tamarin Savings" in page
    assert JOINT_SAVINGS not in page


# Answering a group


def where_form(page: str) -> dict[str, list[str]]:
    form = re.search(
        r'<form class="mt-6" method="post" action="/review/where/move">.*?</form>', page, re.S
    )
    assert form is not None, "nothing to answer on the page"
    data: dict[str, list[str]] = {}
    for name, value in re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form[0]):
        data.setdefault(unescape(name), []).append(unescape(value))
    return data


def test_a_group_is_answered_onto_the_members_own_account_and_undone_whole(
    world: World,
) -> None:
    casey = world.signed_in("casey")
    data = where_form(casey.get("/review/where").text)
    data |= {"answer": ["account"], "account": [world.account_ids["casey_named"]]}

    moved = casey.post("/review/where/move", data=data)

    assert moved.status_code == 303
    assert "outcome=moved" in moved.headers["location"]
    world.recompute()
    landed = text(casey.get(moved.headers["location"]).text)
    assert "Saved. It moved between your accounts, so it is neither spending nor income." in landed
    assert "the transaction will wait for an answer again" in landed
    assert "They moved" not in landed
    assert "1 transaction, $55.55: moved to Gharial Loan, an account hearth does not see." in landed
    loan = text(casey.get(f"/accounts/{world.account_ids['casey_named']}?month=2026-10").text)
    assert "2 moves" in loan

    ruling = re.search(r'name="ruling" value="(\d+)"', casey.get(moved.headers["location"]).text)
    assert ruling is not None
    undone = casey.post("/review/where/undo", data={"csrf": world.csrf(casey), "ruling": ruling[1]})
    assert "outcome=undone" in undone.headers["location"]
    back = text(casey.get(undone.headers["location"]).text)
    assert "Taken back. That transaction is waiting for an answer again." in back
    assert "Payee Jerboa Transfer" in back


def test_nobody_answers_for_another_members_rows_or_onto_their_account(world: World) -> None:
    casey = world.signed_in("casey")
    caseys = where_form(casey.get("/review/where").text)
    before = rulings_recorded(world)

    for username in ("avery", "blake"):
        client = world.signed_in(username)
        forged = caseys | {
            "csrf": [world.csrf(client)],
            "answer": ["account"],
            "account": [world.account_ids["joint_named"]],
        }
        assert client.post("/review/where/move", data=forged).status_code == 404

    onto_theirs = caseys | {"answer": ["account"], "account": [world.account_ids["joint_named"]]}
    refused = casey.post("/review/where/move", data=onto_theirs)
    assert "outcome=not-yours" in refused.headers["location"]
    onto_equity = caseys | {"answer": ["category"], "category": ["Equity:Household:Anything"]}
    assert (
        "outcome=not-yours"
        in casey.post("/review/where/move", data=onto_equity).headers["location"]
    )
    assert rulings_recorded(world) == before


def test_another_members_accounts_are_never_offered(world: World) -> None:
    page = world.signed_in("casey").get("/review/where").text

    assert "Payee Jerboa Transfer" in page  # the group the member is asked about
    assert re.search(
        rf'<option value="{world.account_ids["casey_named"]}"[^>]*>Gharial Loan<', page
    )
    for theirs in ("Tamarin Savings", "Quokka Savings", "Narwhal Joint", "Wombat Shares"):
        assert theirs not in page


# Naming an account


def name_form(
    world: World, client: TestClient, **fields: str | list[str]
) -> dict[str, list[str] | str]:
    page = client.get("/review/where/account?group=" + group_key(client)).text
    data: dict[str, list[str] | str] = {}
    for name, value in re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', page):
        existing = data.setdefault(unescape(name), [])
        assert isinstance(existing, list)
        existing.append(unescape(value))
    data |= {
        "kind": "bank",
        "where": "Dhole Bank",
        "name": "Dhole savings",
        "mask": "",
        "owners": "",
    }
    data |= fields
    return data


def group_key(client: TestClient) -> str:
    found = re.search(r'name="group" value="([0-9a-f]{12})"', client.get("/review/where").text)
    assert found is not None
    return found[1]


def test_naming_an_account_moves_the_group_into_it_and_lists_it_apart(world: World) -> None:
    casey = world.signed_in("casey")
    data = name_form(world, casey, owners=str(world.member_ids["casey"]))

    named = casey.post("/review/where/account", data=data)

    assert named.status_code == 303, named.text
    assert "outcome=moved" in named.headers["location"]
    conn = world.connect()
    try:
        (found,) = [a for a in accounts.all_accounts(conn) if a.name == "Dhole savings"]
    finally:
        conn.close()
    assert found.ledger_account == "Assets:Casey:Dhole:Savings"
    assert found.owners == {world.member_ids["casey"]}
    assert found.named is not None
    assert found.named.institution == "Dhole Bank"
    world.recompute()
    you = text(casey.get("/you").text)
    assert "without 2 accounts hearth does not see" in you
    assert f"Dhole savings Dhole Bank Not connected {DASH} Not known" in you


def test_a_name_in_use_is_refused_with_one_answer_whatever_it_hit(world: World) -> None:
    casey = world.signed_in("casey")
    answers = set()
    for path in (
        "Assets:Zq:AverySolo",
        "Assets:Zq:JointSavings:Sub",
        "Assets:Zq",
        "Assets:Zq:CaseyChecking",
    ):
        refused = casey.post(
            "/review/where/account", data=name_form(world, casey, ledger=path, auto="0")
        )
        assert refused.status_code == 400
        said = re.search(r'<div class="alert-body">.*?</div>', refused.text, re.S)
        assert said is not None
        answers.add(said[0])
        assert "Quokka" not in refused.text
        assert "Tamarin" not in refused.text
    assert answers == {
        '<div class="alert-body"><p style="color: var(--ink); font-weight: 600">The account was'
        " not added yet.</p><p>That name is not available. Choose another.</p></div>"
    }
    conn = world.connect()
    try:
        assert not [a for a in accounts.all_accounts(conn) if a.name == "Dhole savings"]
    finally:
        conn.close()


def test_a_member_names_an_account_only_they_own(world: World) -> None:
    casey = world.signed_in("casey")
    for owners in (
        str(world.member_ids["avery"]),
        f"{world.member_ids['avery']},{world.member_ids['blake']}",
    ):
        forged = casey.post("/review/where/account", data=name_form(world, casey, owners=owners))
        assert forged.status_code == 404
    conn = world.connect()
    try:
        assert not [a for a in accounts.all_accounts(conn) if a.name == "Dhole savings"]
    finally:
        conn.close()


def test_a_named_account_is_not_offered_as_where_a_connected_account_goes(world: World) -> None:
    """Not yet: what members moved into it would count a second time beside its bank's own
    rows, until their answers can be checked against those rows when it is connected."""
    with world.connector("casey") as connector:
        offered = {t.ledger_account for row in connector.mapping().rows for t in row.targets}

    assert CASEY_LOAN not in offered
    assert "Assets:Zq:CaseyChecking" in offered


def test_a_new_account_waits_for_nothing_before_rows_move_into_it(world: World) -> None:
    """It is open on every row's day from the moment it is named, before the books are next
    recomputed, so the rows that named it move into it at once."""
    casey = world.signed_in("casey")
    before = rulings_recorded(world)

    named = casey.post(
        "/review/where/account", data=name_form(world, casey, owners=str(world.member_ids["casey"]))
    )

    assert "outcome=moved" in named.headers["location"]
    assert rulings_recorded(world) == before + 1
    conn = db.connect(world.settings.database)
    try:
        assert [a for a in RulingStore(conn).ruled_onto() if a.endswith(":Dhole:Savings")]
    finally:
        conn.close()


def test_an_account_named_before_anything_moved_into_it_holds_no_figure_hostage(
    world: World,
) -> None:
    """No history of its own will ever arrive for it, so neither its months nor anyone's
    spending waits on it, and its balance is not known rather than not known yet."""
    casey = world.signed_in("casey")
    data = {
        "csrf": world.csrf(casey),
        "kind": "owed",
        "where": "",
        "name": "Lent to a sibling",
        "mask": "",
        "owners": str(world.member_ids["casey"]),
        "ledger": "",
        "auto": "1",
    }

    named = casey.post("/review/where/account", data=data)
    world.recompute()

    assert "outcome=named" in named.headers["location"]
    found = world.bookkeeper.status.books
    assert found is not None
    assert "Assets:Casey:LentToASibling" in found.unseen
    assert not found.awaiting
    you = text(casey.get("/you").text)
    assert "History incomplete" not in you
    assert "without 2 accounts hearth does not see" in you


# One by one


def one_by_one(client: TestClient) -> str:
    return client.get("/review/where/one?group=" + group_key(client)).text


def row_form(page: str) -> dict[str, str]:
    form = re.search(r'<form method="post" action="/review/where/row".*?</form>', page, re.S)
    assert form is not None, "no row to answer on its own"
    return {
        unescape(name): unescape(value)
        for name, value in re.findall(
            r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form[0]
        )
    }


def test_a_row_is_answered_on_its_own_onto_the_members_account(world: World) -> None:
    casey = world.signed_in("casey")
    page = one_by_one(casey)
    assert re.search(
        rf'<option value="account:{world.account_ids["casey_named"]}"[^>]*>Gharial Loan<', page
    )
    for theirs in ("Tamarin Savings", "Quokka Savings", "Narwhal Joint", "Wombat Shares"):
        assert theirs not in page
    data = row_form(page) | {"to": f"account:{world.account_ids['casey_named']}"}

    moved = casey.post("/review/where/row", data=data)

    assert moved.status_code == 303
    assert moved.headers["location"].startswith("/review/where?outcome=moved&undo=")
    conn = world.connect()
    try:
        newest = conn.execute(
            "SELECT scope, account FROM review_rulings ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert tuple(newest) == ("row", CASEY_LOAN)
    assert "Payee Jerboa Transfer" not in casey.get("/review/where").text


def test_the_last_row_answered_one_by_one_is_spoken_of_alone_and_when_undone(
    world: World,
) -> None:
    casey = world.signed_in("casey")
    key = group_key(casey)
    data = row_form(one_by_one(casey)) | {"to": "category:Expenses:Gifts"}

    landed = casey.get(casey.post("/review/where/row", data=data).headers["location"]).text

    said = text(landed)
    assert "Saved under the category you chose." in said
    assert "Undo takes back exactly this answer: the transaction will wait" in said
    form = re.search(r'<form class="alert-actions".*?</form>', landed, re.S)
    assert form is not None
    undo = {
        unescape(name): unescape(value)
        for name, value in re.findall(r'name="([^"]+)" value="([^"]*)"', form[0])
    }
    undone = casey.post("/review/where/undo", data=undo)
    assert undone.headers["location"].startswith("/review/where?outcome=undone&undo=")
    assert "That transaction is waiting for an answer again." in text(
        casey.get(undone.headers["location"]).text
    )
    assert key == group_key(casey)

    data = row_form(one_by_one(casey)) | {"to": f"account:{world.account_ids['casey_named']}"}
    landed = casey.get(casey.post("/review/where/row", data=data).headers["location"]).text
    assert "Saved. It moved between your accounts, so it is neither" in text(landed)


def test_nobody_answers_another_members_row_on_its_own(world: World) -> None:
    casey = world.signed_in("casey")
    caseys = row_form(one_by_one(casey))
    before = rulings_recorded(world)

    for username in ("avery", "blake"):
        client = world.signed_in(username)
        assert client.get(f"/review/where/one?group={caseys['group']}").status_code == 404
        forged = caseys | {"csrf": world.csrf(client), "to": "category:Expenses:Gifts"}
        assert client.post("/review/where/row", data=forged).status_code == 404
    onto_theirs = caseys | {"to": f"account:{world.account_ids['joint_named']}"}
    assert (
        "outcome=not-yours" in casey.post("/review/where/row", data=onto_theirs).headers["location"]
    )
    stale = caseys | {
        "row": caseys["row"].split(" ", 1)[0] + " something else",
        "to": "category:Expenses:Gifts",
    }
    assert "outcome=changed" in casey.post("/review/where/row", data=stale).headers["location"]
    assert rulings_recorded(world) == before


def test_an_account_named_for_one_row_takes_that_row_alone(world: World) -> None:
    casey = world.signed_in("casey")
    form = row_form(one_by_one(casey))
    asked = casey.post("/review/where/row", data=form | {"to": "new"})
    assert asked.status_code == 303
    page = casey.get(asked.headers["location"]).text
    rows = re.findall(r'<input type="hidden" name="rows" value="([^"]*)">', page)
    assert [unescape(r) for r in rows] == [form["row"]]
    assert "Then 1 transaction goes to it" in text(page)
    data = {
        unescape(name): unescape(value)
        for name, value in re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', page)
    } | {
        "kind": "bank",
        "where": "Dhole Bank",
        "name": "Dhole savings",
        "owners": str(world.member_ids["casey"]),
    }

    named = casey.post("/review/where/account", data=data)

    assert named.status_code == 303
    assert "outcome=moved" in named.headers["location"]
    conn = world.connect()
    try:
        newest = conn.execute(
            "SELECT scope, account FROM review_rulings ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert tuple(newest) == ("row", "Assets:Casey:Dhole:Savings")


def test_one_by_one_speaks_of_the_one_row_and_undo_returns_to_it(world: World) -> None:
    casey = world.signed_in("casey")
    key = group_key(casey)
    said = text(casey.get(f"/review/where/one?group={key}&outcome=moved&n=1").text)
    assert "Saved. It moved between your accounts" in said
    form = row_form(one_by_one(casey))
    moved = casey.post("/review/where/row", data=form | {"to": "category:Expenses:Gifts"})
    ruling = re.search(r"undo=(\d+)", moved.headers["location"])
    assert ruling is not None

    undone = casey.post(
        "/review/where/undo",
        data={"csrf": world.csrf(casey), "ruling": ruling[1], "group": key},
    )

    assert undone.headers["location"].startswith(
        f"/review/where/one?group={key}&outcome=undone&n=1&undo="
    )
    landed = text(casey.get(undone.headers["location"]).text)
    assert "Taken back. That transaction is waiting for an answer again." in landed


def test_an_account_named_as_its_rows_vanish_is_still_added(world: World) -> None:
    casey = world.signed_in("casey")
    data = name_form(world, casey, owners=str(world.member_ids["casey"]))
    data["rows"] = ["nosuchrow0 anything"]

    named = casey.post("/review/where/account", data=data)

    assert named.status_code == 303
    assert "outcome=named" in named.headers["location"]
    conn = world.connect()
    try:
        assert [a.name for a in accounts.all_accounts(conn) if a.name == "Dhole savings"]
    finally:
        conn.close()


def test_the_net_worth_preview_shows_only_for_an_account_the_member_owns_alone(
    world: World,
) -> None:
    avery = world.signed_in("avery")
    choices = re.findall(
        r'<option value="([^"]+)"( data-alone)?', avery.get("/review/where/account").text
    )
    joint = next(key for key, alone in choices if not alone)
    alone = next(key for key, alone in choices if alone)

    def preview(owners: str) -> str:
        data = {"csrf": world.csrf(avery), "kind": "bank", "name": "", "owners": owners}
        page = avery.post("/review/where/account", data=data).text
        found = re.search(r'<div class="hero-partial[^>]*>', page)
        assert found is not None
        return found[0]

    assert " hidden" in preview(joint)
    assert " hidden" not in preview(alone)


def test_a_row_named_one_by_one_after_it_was_answered_is_spoken_of_alone(world: World) -> None:
    """The page offered Casey's only row; it was answered elsewhere before the new account was
    sent, so the account is added, nothing is saved, and the message is about that one row."""
    casey = world.signed_in("casey")
    form = row_form(one_by_one(casey))
    page = casey.get(casey.post("/review/where/row", data=form | {"to": "new"}).headers["location"])
    data = {
        unescape(name): unescape(value)
        for name, value in re.findall(
            r'<input type="hidden" name="([^"]+)" value="([^"]*)">', page.text
        )
    } | {
        "kind": "bank",
        "where": "Dhole Bank",
        "name": "Dhole savings",
        "owners": str(world.member_ids["casey"]),
    }
    casey.post("/review/where/row", data=form | {"to": "category:Expenses:Gifts"})
    before = rulings_recorded(world)

    named = casey.post("/review/where/account", data=data)

    assert named.headers["location"] == "/review/where?outcome=named-waiting&n=1"
    landed = text(casey.get(named.headers["location"]).text)
    assert "Account added. That transaction changed since the page showed it" in landed
    assert rulings_recorded(world) == before
