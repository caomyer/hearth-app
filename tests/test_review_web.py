"""Review on the web, as a member works through it: every row in the books, flagged until
checked, rulings reaching the figures, accepts bound to what the page showed, and nobody's
pile visible to anyone else, not even by subtracting from a total.
"""

import re
from datetime import timedelta

import pytest
from beancount.core.data import Transaction
from fastapi.testclient import TestClient

from hearth.sharing import DEFAULT_SHARED, Facet
from tests.conftest import World

KITES = "Expenses:Kites"
KITE_PAIR = (("casey-row-7", "Kites", "-10.00", KITES), ("casey-row-8", "Kites", "-11.00", KITES))


def shrikes(world: World, payee: str) -> list[Transaction]:
    """The books' transactions with `payee`, as the last recompute left them."""
    books = world.bookkeeper.status.books
    assert books is not None
    return [e for e in books.entries if isinstance(e, Transaction) and e.payee == payee]


def add_rows(world: World, *rows: tuple[str, str, str, str]) -> None:
    """Rows on Casey's checking, each (review id, group, amount, proposal), unreviewed."""
    text = world.ledger.read_text()
    for review_id, group, amount, proposal in rows:
        text += (
            f'\n2026-10-04 ! "Payee Shrike {review_id}" ""\n  review-id: "{review_id}"\n'
            f'  review-group: "{group}"\n  model-confidence: 0.5\n'
            f"  Assets:Zq:CaseyChecking  {amount} USD\n  {proposal}\n"
        )
    world.ledger.write_text(text)
    world.recompute()


def post(world: World, client: TestClient, path: str, **form: str | list[str]) -> str:
    response = client.post(path, data={"csrf": world.csrf(client), **form})
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert location.startswith("/review")
    return location


def test_a_ruling_reaches_the_books_at_the_next_recompute(world: World) -> None:
    add_rows(world, ("casey-row-9", "Kites", "-40.00", KITES))
    casey = world.signed_in("casey")
    account = world.account_ids["casey_checking"]
    assert "To review" in casey.get(f"/accounts/{account}").text

    post(world, casey, "/review/rows/book", review_id="casey-row-9", account=KITES, shown=KITES)
    world.recompute()

    page = casey.get(f"/accounts/{account}").text
    row = page[page.index("Payee Shrike casey-row-9") :].split("</tr>")[0]
    assert "To review" not in row
    assert [e.flag for e in shrikes(world, "Payee Shrike casey-row-9")] == ["*"]


def test_an_excluded_row_leaves_the_books(world: World) -> None:
    add_rows(world, ("casey-row-9", "Kites", "-40.00", KITES))
    casey = world.signed_in("casey")

    post(world, casey, "/review/rows/reject", review_id="casey-row-9")
    world.recompute()

    assert shrikes(world, "Payee Shrike casey-row-9") == []
    assert "Excluded: not a transaction" in casey.get("/review").text


def test_an_unreviewed_spending_total_says_how_much_rests_on_unreviewed_rows(
    world: World,
) -> None:
    add_rows(world, ("casey-row-9", "Kites", "-40.00", KITES))

    casey = world.signed_in("casey").get("/you").text

    assert '<span class="tnum">$40</span> not yet reviewed' in casey


def test_an_unreviewed_income_total_says_how_much_rests_on_unreviewed_rows(
    world: World,
) -> None:
    add_rows(world, ("casey-row-9", "Stipend", "75.00", "Income:Curlew"))

    casey = world.signed_in("casey").get("/you").text

    assert '<span class="tnum">$75</span> not yet reviewed' in casey


def test_accepting_with_a_suggestion_that_changed_since_the_page_leaves_it_waiting(
    world: World,
) -> None:
    add_rows(world, ("casey-row-9", "Kites", "-40.00", "Expenses:Cartography"))
    casey = world.signed_in("casey")

    location = post(
        world, casey, "/review/rows/book", review_id="casey-row-9", account=KITES, shown=KITES
    )

    # The account typed equals what the page showed, but the row now proposes something else:
    # the accept is refused rather than binding to a proposal nobody saw.
    assert "outcome=changed" in location
    assert "changed since the page showed them" in casey.get(location).text
    assert 'id="r-casey-row-9"' in casey.get("/review?view=rows").text


def test_a_group_accept_books_only_the_rows_the_page_showed_as_it_showed_them(
    world: World,
) -> None:
    add_rows(
        world,
        ("casey-row-7", "Kites", "-10.00", KITES),
        ("casey-row-8", "Kites", "-11.00", KITES),
        ("casey-row-9", "Kites", "-12.00", KITES),
    )
    casey = world.signed_in("casey")
    shown = [f"casey-row-7 {KITES}", "casey-row-8 Expenses:Cartography", "casey-row-1 Expenses:X"]

    location = post(world, casey, "/review/groups/accept", group="Kites", rows=shown)

    assert "outcome=changed" in location
    waiting = casey.get("/review?view=rows").text
    assert 'id="r-casey-row-7"' not in waiting
    assert 'id="r-casey-row-8"' in waiting  # shown with a proposal it does not have
    assert 'id="r-casey-row-9"' in waiting  # never shown
    assert 'id="r-casey-row-1"' in waiting  # another group's row cannot be smuggled in


def test_another_members_rows_cannot_be_ruled_on_or_grouped(world: World) -> None:
    avery = world.signed_in("avery")
    form = {"csrf": world.csrf(avery), "group": "Unsure", "rows": ["casey-row-1 Expenses:X"]}

    assert avery.post("/review/groups/accept", data=form).status_code == 404
    form["group"] = "No such group"
    assert avery.post("/review/groups/accept", data=form).status_code == 404
    assert 'id="r-casey-row-1"' in world.signed_in("casey").get("/review?view=rows").text


def test_filtering_by_someone_elses_account_answers_like_a_missing_one(world: World) -> None:
    avery = world.signed_in("avery")
    casey_account = world.account_ids["casey_checking"]

    theirs = avery.get(f"/review?account={casey_account}&view=rows").text
    missing = avery.get("/review?account=nonexistent&view=rows").text

    assert theirs.replace(casey_account, "X") == missing.replace("nonexistent", "X")


@pytest.mark.parametrize(
    ("order", "first"),
    [("confidence", "casey-row-1"), ("amount", "casey-row-8"), ("date", "casey-row-7")],
)
def test_the_queue_puts_first_what_its_order_says(world: World, order: str, first: str) -> None:
    add_rows(
        world,
        ("casey-row-7", "Kites", "-10.00", KITES),
        ("casey-row-8", "Maps", "-900.00", "Expenses:Cartography"),
    )

    page = world.signed_in("casey").get(f"/review?view=rows&order={order}").text

    assert re.findall(r'id="r-([a-z0-9-]+)"', page)[0] == first


def test_a_filter_says_what_it_leaves(world: World) -> None:
    add_rows(
        world,
        ("casey-row-7", "Kites", "-10.00", KITES),
        ("casey-row-8", "Maps", "-900.00", "Expenses:Cartography"),
    )

    page = world.signed_in("casey").get(f"/review?category={KITES}").text

    left = 'Showing <strong class="tnum">1</strong> of 3 waiting, <span class="tnum">$10.00</span>'
    assert f"{left} moved" in page
    assert '0</span> of <span class="tnum">3</span> reviewed' in page


def test_after_a_decision_the_queue_resumes_at_the_next_row(world: World) -> None:
    add_rows(world, ("casey-row-7", "Kites", "-10.00", KITES))
    casey = world.signed_in("casey")

    location = post(
        world,
        casey,
        "/review/rows/book",
        review_id="casey-row-7",
        account=KITES,
        shown=KITES,
        back="view=rows&order=amount&evil=https://example.com",
        focus="casey-row-1",
    )

    assert (
        location
        == "/review?view=rows&order=amount&outcome=accepted&undo=1&focus=casey-row-1#r-casey-row-1"
    )
    page = casey.get(location).text
    assert 'id="r-casey-row-1" data-row data-focus' in page
    # Still the full page, with its navigation and width, not the narrow one-task page whose
    # width squeezes the table to a word a line.
    assert '<main id="main" class="page">' in page
    assert '<nav class="nav" aria-label="Primary">' in page


def test_a_review_falls_due_on_the_members_own_cadence_and_nobody_elses(world: World) -> None:
    casey = world.signed_in("casey")
    assert "Your weekly review is due" in casey.get("/").text
    assert "Your weekly review is due" not in world.signed_in("avery").get("/").text

    post(world, casey, "/review/reminders", every="daily")
    post(world, casey, "/review/rows/reject", review_id="casey-row-1")
    add_rows(world, ("casey-row-7", "Kites", "-10.00", KITES))
    assert "review is due" not in casey.get("/").text

    world.clock.advance(timedelta(days=1))
    assert "Your daily review is due" in casey.get("/").text
    post(world, casey, "/review/reminders", every="off")
    assert "review is due" not in casey.get("/").text


def test_a_reminder_cannot_be_set_to_anything_but_a_cadence(world: World) -> None:
    casey = world.signed_in("casey")
    form = {"csrf": world.csrf(casey), "every": "hourly"}

    assert casey.post("/review/reminders", data=form).status_code == 404


@pytest.mark.parametrize("viewer", ["avery", "blake"])
def test_whether_another_member_has_reviewed_is_not_theirs_to_know(
    world: World, viewer: str
) -> None:
    """Casey shares categories and totals but not transactions. Whether Casey has checked a
    row must not change a byte of what anyone else sees, so Casey's pile cannot be counted,
    not even by subtracting from a household total."""
    world.choose("casey", DEFAULT_SHARED | {Facet.CATEGORY_BREAKDOWN, Facet.MONTHLY_INCOME_TOTAL})
    add_rows(world, ("casey-row-7", "Kites", "-10.00", KITES))
    pages = ("/", "/members/casey", "/net-worth", "/review")
    other = world.signed_in(viewer)
    before = [other.get(path).text for path in pages]

    casey = world.signed_in("casey")
    post(world, casey, "/review/rows/book", review_id="casey-row-7", account=KITES, shown=KITES)
    world.recompute()

    assert [other.get(path).text for path in pages] == before


def test_a_member_who_shares_transactions_shares_them_flagged_as_they_stand(
    world: World,
) -> None:
    """The control for the test above: with transactions shared, the flag is visible, so the
    unreviewed part of Casey's total tells nobody anything the lines do not."""
    world.choose("casey", frozenset(Facet))
    add_rows(world, ("casey-row-7", "Kites", "-10.00", KITES))

    avery = world.signed_in("avery")
    before = avery.get("/members/casey").text
    casey = world.signed_in("casey")
    post(world, casey, "/review/rows/book", review_id="casey-row-7", account=KITES, shown=KITES)
    world.recompute()

    assert avery.get("/members/casey").text != before


# A group that is no one kind of transaction is never settled in one go, by any path.


@pytest.mark.parametrize(
    ("group", "rows"),
    [
        ("Unsure", [("casey-row-7", "-10.00", KITES), ("casey-row-8", "-11.00", KITES)]),
        (
            "Mixed",
            [("casey-row-7", "-10.00", KITES), ("casey-row-8", "-11.00", "Expenses:Cartography")],
        ),
    ],
)
def test_a_group_that_is_not_one_decision_cannot_be_accepted_by_any_path(
    world: World, group: str, rows: list[tuple[str, str, str]]
) -> None:
    add_rows(world, *((rid, group, amount, proposal) for rid, amount, proposal in rows))
    casey = world.signed_in("casey")

    # The page offers no group accept, so the keyboard's `a` has no form to press.
    page = casey.get("/review").text
    section = page[page.index(f">{group}</h2>") :].split("</section>")[0]
    assert "data-accept-group" not in section
    assert "Accept 2</button>" not in section

    # And a forged request is refused, recording nothing.
    shown = [f"{rid} {proposal}" for rid, _, proposal in rows]
    location = post(world, casey, "/review/groups/accept", group=group, rows=shown)
    assert "outcome=not-one-decision" in location
    assert "cannot be accepted all at once" in casey.get(location).text
    waiting = casey.get("/review?view=rows").text
    assert all(f'id="r-{rid}"' in waiting for rid, _, _ in rows)


def test_a_group_of_one_kind_is_one_decision(world: World) -> None:
    add_rows(
        world, ("casey-row-7", "Kites", "-10.00", KITES), ("casey-row-8", "Kites", "-11.00", KITES)
    )
    casey = world.signed_in("casey")
    page = casey.get("/review").text

    assert "Accept 2</button>" in page[page.index(">Kites</h2>") :].split("</section>")[0]


# Undo takes back exactly the last decision, and says so.


def test_undo_takes_back_exactly_the_last_decision_and_says_what_it_covers(world: World) -> None:
    add_rows(
        world, ("casey-row-7", "Kites", "-10.00", KITES), ("casey-row-8", "Kites", "-11.00", KITES)
    )
    casey = world.signed_in("casey")
    post(world, casey, "/review/rows/reject", review_id="casey-row-1")
    shown = [f"casey-row-7 {KITES}", f"casey-row-8 {KITES}"]
    location = post(world, casey, "/review/groups/accept", group="Kites", rows=shown)

    page = casey.get(location).text
    assert "Undo takes back exactly this decision: 2 transactions in Kites" in page
    # Pinned in view, as the page lands on the next item, often far below where it is written.
    assert '<div class="alert alert-info rnotice" role="status">' in page
    ruling = re.search(r'name="ruling" value="(\d+)"', page)
    assert ruling is not None

    post(world, casey, "/review/undo", ruling=ruling[1])

    waiting = casey.get("/review?view=rows").text
    assert 'id="r-casey-row-7"' in waiting
    assert 'id="r-casey-row-8"' in waiting
    assert 'id="r-casey-row-1"' not in waiting  # the earlier decision stands
    assert 'name="ruling"' not in casey.get("/review").text  # offered only right after one


def test_nobody_can_undo_or_be_offered_to_undo_someone_elses_decision(world: World) -> None:
    casey = world.signed_in("casey")
    location = post(world, casey, "/review/rows/reject", review_id="casey-row-1")
    ruling = re.search(r"undo=(\d+)", location)
    assert ruling is not None

    avery = world.signed_in("avery")
    form = {"csrf": world.csrf(avery), "ruling": ruling[1]}
    assert avery.post("/review/undo", data=form).status_code == 404
    form["ruling"] = "999999"
    assert avery.post("/review/undo", data=form).status_code == 404
    assert 'name="ruling"' not in avery.get(f"/review?outcome=accepted&undo={ruling[1]}").text
    assert "Excluded: not a transaction" in casey.get("/review").text
