"""Review in the app: the viewer's own queue, and every decision the review pages can make,
made through the same desk by the same rules.

Each decision is asked as the app asks it, through a bridge the member signed in to, and is
checked against the desk's own rules: what it accepts, what it refuses and how it says so, a
proposal or a row that changed since the app showed it, and anything that is not on the
member's desk, which changes nothing and answers like something that does not exist. What
each viewer may be told is checked by the leak crawl in ``test_doorway.py``.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from hearth import books, db
from hearth.disclosure import may_learn, rulings_in
from hearth.native import stdio
from hearth.native.bridge import Acted, Bridge
from hearth.native.common import Decided
from hearth.review.ids import says
from hearth.review.rulings import RulingStore
from hearth.sharing import Facet
from tests import household
from tests.conftest import FakeClock, World, password_of
from tests.native import on_disk
from tests.native.test_doorway import FIXTURES, differences
from tests.test_review_web import KITES, add_rows

CHECKING = "Assets:Zq:CaseyChecking"
KITE_ROWS = (
    ("casey-kite-1", "Kites", "-10.00", KITES),
    ("casey-kite-2", "Kites", "-11.00", KITES),
    ("casey-kite-3", "Kites", "-12.00", KITES),
)


def bridge(world: World, username: str) -> Bridge:
    door = Bridge(world.settings, world.bookkeeper, world.clock)
    door.sign_in(username, password_of(username))
    return door


def ready(answer: dict[str, Any]) -> dict[str, Any]:
    assert answer["state"] == "ready", answer
    return answer


def decided(done: object) -> Decided:
    assert isinstance(done, Decided), done
    return done


def newest(world: World) -> int:
    conn = db.connect(world.settings.database)
    try:
        return RulingStore(conn).newest()
    finally:
        conn.close()


def said(amount: str, day: date = date(2026, 10, 4)) -> str:
    """What a row on Casey's checking says, as the app is shown it: one `add_rows` wrote, by
    default."""
    return says(CHECKING, day, Decimal(amount), "USD")


def rows_of(answer: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in answer["rows"]}


@pytest.fixture
def kites(world: World) -> World:
    """Casey's three kite rows, all suggested as kites: a group that is one decision."""
    add_rows(world, *KITE_ROWS)
    return world


# What the app is shown


def test_the_queue_is_the_viewers_own_counted_as_the_desk_counts_it(kites: World) -> None:
    answer = ready(bridge(kites, "casey").answer("review"))

    assert answer["remaining"] == {"rows": 4, "moved": "56.45"}
    assert answer["elsewhere"] == {"rows": 1, "moved": "55.55"}
    assert answer["reviewed"] == 1
    assert answer["total"] == 5
    assert answer["groups_waiting"] == 2
    groups = {g["name"]: g for g in answer["groups"]}
    assert list(groups) == ["Kites", "Unsure"]
    assert groups["Kites"]["one_decision"] is True
    assert groups["Kites"]["suggested"] == [{"name": KITES, "label": "Kites"}]
    assert groups["Unsure"]["one_decision"] is False
    rows = rows_of(answer)
    assert set(rows) == {"casey-kite-1", "casey-kite-2", "casey-kite-3", "casey-row-1"}
    assert rows["casey-row-1"]["suggested"] is None
    assert rows["casey-row-1"]["reason"] == "Nothing suggested"
    assert rows["casey-kite-1"]["reason"] == "Low confidence"
    assert rows["casey-kite-1"]["says"] == said("-10.00")


def test_nobody_elses_queue_is_anyones_not_even_a_count(kites: World) -> None:
    for username in ("avery", "blake"):
        answer = ready(bridge(kites, username).answer("review"))
        assert answer["remaining"]["rows"] == 0
        assert answer["groups"] == []
        assert answer["rows"] == []
        assert ready(bridge(kites, username).answer("where"))["groups"] == []


def test_the_queue_is_ordered_and_narrowed_as_the_page_orders_and_narrows_it(
    kites: World,
) -> None:
    casey = bridge(kites, "casey")

    largest = ready(casey.answer("review", {"order": "amount"}))
    assert [r["id"] for r in largest["rows"]][:3] == ["casey-row-1", "casey-kite-3", "casey-kite-2"]
    found = ready(casey.answer("review", {"text": "KITE-2"}))
    assert [r["id"] for r in found["rows"]] == ["casey-kite-2"]
    assert found["shown"] == {"rows": 1, "moved": "11.00"}
    assert found["filtered"] is True
    assert found["remaining"]["rows"] == 4
    nothing = ready(casey.answer("review", {"category": "-"}))
    assert [r["id"] for r in nothing["rows"]] == ["casey-row-1"]
    elsewhere = kites.account_ids["avery_solo"]
    assert ready(casey.answer("review", {"account": elsewhere}))["rows"] == []


@pytest.mark.parametrize(
    "given",
    [
        {"order": "biggest"},
        {"order": 1},
        {"category": "groceries"},
        {"text": ""},
        {"account": "x" * 65},
        {"page": 2},
    ],
)
def test_a_queue_asked_with_what_it_does_not_take_is_missing(
    kites: World, given: dict[str, object]
) -> None:
    assert bridge(kites, "casey").answer("review", given) == {"state": "missing"}


def test_a_rows_detail_says_where_it_stands_and_what_it_may_go_to(kites: World) -> None:
    casey = bridge(kites, "casey")
    assert decided(casey.act("accept", {"row": "casey-kite-1", "shown": KITES})).rows == 1

    answer = ready(casey.answer("review_row", {"id": "casey-kite-3"}))

    assert answer["decision"] == "waiting"
    assert answer["row"]["id"] == "casey-kite-3"
    labels = [c["label"] for c in answer["categories"]]
    assert "Kites" in labels
    assert all(c["name"].split(":")[0] in {"Expenses", "Income"} for c in answer["categories"])
    assert {a["name"] for a in answer["accounts"]} >= {"Gharial Loan"}
    # Rows like it, from the same merchant, are other kites only if they share its payee.
    assert answer["earlier"] == []
    done = ready(casey.answer("review_row", {"id": "casey-kite-1"}))
    assert done["decision"] == "reviewed"
    assert done["booked"] == {"name": KITES, "label": "Kites"}
    assert ready(casey.answer("review_row", {"id": "casey-where-1"}))["decision"] == "elsewhere"


def test_earlier_rows_from_the_same_merchant_say_how_they_were_booked(world: World) -> None:
    text = world.ledger.read_text()
    for review_id, day, amount in (
        ("casey-tea-1", "2026-09-02", "-4.00"),
        ("casey-tea-2", "2026-10-02", "-5.00"),
    ):
        text += (
            f'\n{day} ! "Teapot Corner" ""\n  review-id: "{review_id}"\n'
            f'  review-group: "Tea"\n  {CHECKING}  {amount} USD\n  {KITES}\n'
        )
    world.ledger.write_text(text)
    world.recompute()
    casey = bridge(world, "casey")
    shown = said("-4.00", date(2026, 9, 2))
    category = "Expenses:Cartography"
    assert decided(casey.act("book", {"row": "casey-tea-1", "says": shown, "category": category}))

    answer = ready(casey.answer("review_row", {"id": "casey-tea-2"}))

    assert answer["earlier"] == [
        {
            "date": "2026-09-02",
            "amount": "-4.00",
            "decision": "reviewed",
            "category": {"name": category, "label": "Cartography"},
        }
    ]


def test_a_row_that_is_not_the_viewers_has_no_detail(kites: World) -> None:
    for username in ("avery", "blake"):
        door = bridge(kites, username)
        for review_id in ("casey-kite-1", "casey-row-1", "avery-moved-1x", "nothing"):
            assert door.answer("review_row", {"id": review_id}) == {"state": "missing"}


def test_where_it_went_is_asked_a_group_at_a_time(world: World) -> None:
    answer = ready(bridge(world, "casey").answer("where"))

    assert answer["rows"] == 1
    (group,) = answer["groups"]
    assert group["question"] == "went"
    assert [row["id"] for row in group["rows"]] == ["casey-where-1"]
    assert [a["name"] for a in group["accounts"]] == ["Gharial Loan"]
    assert group["chosen"] == "new"


def test_the_overview_says_how_much_waits_for_the_viewer(kites: World) -> None:
    review = ready(bridge(kites, "casey").answer("overview"))["review"]

    # The row nothing was suggested for, and the one waiting to be told where its money went,
    # count at no category, so only the kites are spending.
    assert review == {"waiting": 5, "spending": "33.00"}
    assert ready(bridge(kites, "avery").answer("overview"))["review"] == {
        "waiting": 0,
        "spending": None,
    }


def test_an_unreviewed_journal_entry_names_the_review_it_waits_on_to_its_reviewer(
    kites: World,
) -> None:
    # Casey shares everything, so the others see the rows, and nothing of the review.
    kites.choose("casey", frozenset(Facet))

    def reviews(username: str) -> tuple[int, set[str]]:
        answer = ready(bridge(kites, username).answer("transactions", {"show": "unreviewed"}))
        entries: list[dict[str, Any]] = answer["entries"]
        return len(entries), {e["review"] for e in entries if e["review"]}

    assert reviews("casey") == (
        5,
        {"casey-kite-1", "casey-kite-2", "casey-kite-3", "casey-row-1", "casey-where-1"},
    )
    for username in ("avery", "blake"):
        shown, named = reviews(username)
        assert shown >= 5
        assert not named & {"casey-kite-1", "casey-row-1", "casey-where-1"}


# Decisions, made through the desk


def test_accepting_a_row_takes_it_out_of_the_queue_and_recomputes(kites: World) -> None:
    casey = bridge(kites, "casey")

    done = decided(casey.act("accept", {"row": "casey-kite-1", "shown": KITES}))
    kites.recompute()

    assert done.outcome is Acted.ACCEPTED
    assert done.ruling == newest(kites)
    assert (done.rows, done.changed) == (1, 0)
    assert "casey-kite-1" not in rows_of(ready(casey.answer("review")))
    assert ready(casey.answer("overview"))["review"]["waiting"] == 4


def test_an_accept_of_a_proposal_that_changed_since_it_was_shown_is_left_waiting(
    kites: World,
) -> None:
    casey = bridge(kites, "casey")
    before = newest(kites)

    done = decided(casey.act("accept", {"row": "casey-kite-1", "shown": "Expenses:Bunting"}))

    assert done == Decided(Acted.CHANGED, None, 0, 1)
    assert newest(kites) == before
    assert "casey-kite-1" in rows_of(ready(casey.answer("review")))


def test_a_group_is_accepted_bound_to_the_rows_shown(kites: World) -> None:
    casey = bridge(kites, "casey")
    shown = {"casey-kite-1": KITES, "casey-kite-2": KITES}

    done = decided(casey.act("accept_group", {"group": "Kites", "rows": shown}))

    assert (done.outcome, done.rows, done.changed) == (Acted.ACCEPTED, 2, 0)
    # A row of the group the app did not show waits to be seen.
    assert set(rows_of(ready(casey.answer("review")))) == {"casey-kite-3", "casey-row-1"}


def test_a_group_accept_that_names_a_changed_row_decides_the_rest_and_says_so(
    kites: World,
) -> None:
    casey = bridge(kites, "casey")
    shown = {"casey-kite-1": KITES, "casey-kite-2": "Expenses:Bunting", "nothing-at-all": KITES}

    done = decided(casey.act("accept_group", {"group": "Kites", "rows": shown}))

    assert (done.outcome, done.rows, done.changed) == (Acted.CHANGED, 1, 2)
    assert done.ruling == newest(kites)


def test_a_group_that_is_not_one_decision_cannot_be_accepted_at_once(kites: World) -> None:
    casey = bridge(kites, "casey")
    before = newest(kites)

    done = casey.act("accept_group", {"group": "Unsure", "rows": {"casey-row-1": KITES}})

    assert done == Decided(Acted.NOT_ONE_DECISION)
    assert newest(kites) == before


def test_a_row_is_booked_against_a_category_or_an_account_of_the_viewers_own(
    kites: World,
) -> None:
    casey = bridge(kites, "casey")
    shown = said("-10.00")

    booked = decided(
        casey.act("book", {"row": "casey-kite-1", "says": shown, "category": "Expenses:Bunting"})
    )
    loan = kites.account_ids["casey_named"]
    moved = decided(
        casey.act("book", {"row": "casey-kite-2", "says": said("-11.00"), "account": loan})
    )

    assert (booked.outcome, booked.rows) == (Acted.BOOKED, 1)
    assert (moved.outcome, moved.rows) == (Acted.MOVED, 1)
    assert set(rows_of(ready(casey.answer("review")))) == {"casey-kite-3", "casey-row-1"}


@pytest.mark.parametrize(
    ("target", "outcome"),
    [
        ({"category": "groceries"}, Acted.NOT_AN_ACCOUNT),
        ({"category": "Equity:Review:Unresolved"}, Acted.NOT_AN_ACCOUNT),
        ({"category": "Equity:Opening"}, Acted.NOT_YOURS),
        ({"category": "Assets:Zq:AverySolo"}, Acted.NOT_YOURS),
        ({"account": "avery_solo"}, Acted.NOT_YOURS),
        ({"account": "joint_named"}, Acted.NOT_YOURS),
        ({"account": "no-such-account"}, Acted.NOT_YOURS),
    ],
)
def test_a_booking_the_desk_refuses_changes_nothing_and_says_why(
    kites: World, target: dict[str, str], outcome: Acted
) -> None:
    casey = bridge(kites, "casey")
    before = newest(kites)
    if "account" in target:
        target = {"account": kites.account_ids.get(target["account"], target["account"])}

    done = casey.act("book", {"row": "casey-kite-1", "says": said("-10.00"), **target})

    assert done == Decided(outcome)
    assert newest(kites) == before


def test_a_booking_of_a_row_the_bank_changed_since_it_was_shown_is_left_waiting(
    kites: World,
) -> None:
    casey = bridge(kites, "casey")
    before = newest(kites)
    stale = said("-99.00")

    assert casey.act("book", {"row": "casey-kite-1", "says": stale, "category": KITES}) == Decided(
        Acted.CHANGED, changed=1
    )
    assert casey.act("exclude", {"row": "casey-kite-1", "says": stale}) == Decided(
        Acted.CHANGED, changed=1
    )
    assert newest(kites) == before


def test_a_row_is_excluded_where_the_desk_allows_it(kites: World) -> None:
    casey = bridge(kites, "casey")

    done = decided(casey.act("exclude", {"row": "casey-kite-1", "says": said("-10.00")}))

    assert (done.outcome, done.rows) == (Acted.EXCLUDED, 1)
    assert ready(casey.answer("review_row", {"id": "casey-kite-1"}))["decision"] == "excluded"


def test_undo_takes_back_exactly_the_last_decision_and_only_the_viewers_own(kites: World) -> None:
    casey = bridge(kites, "casey")
    shown = {"casey-kite-1": KITES, "casey-kite-2": KITES, "casey-kite-3": KITES}
    accepted = decided(casey.act("accept_group", {"group": "Kites", "rows": shown}))
    assert accepted.ruling is not None

    # Nobody else can take it back, and the answer is the one a ruling that is not there gets.
    for username in ("avery", "blake"):
        assert bridge(kites, username).act("undo", {"ruling": accepted.ruling}) is Acted.MISSING
    assert casey.act("undo", {"ruling": accepted.ruling + 100}) is Acted.MISSING

    done = decided(casey.act("undo", {"ruling": accepted.ruling}))

    assert (done.outcome, done.rows) == (Acted.UNDONE, 3)
    assert set(rows_of(ready(casey.answer("review")))) >= {"casey-kite-1", "casey-kite-2"}
    # An undo is not a decision to take back.
    assert casey.act("undo", {"ruling": newest(kites)}) is Acted.MISSING


def test_where_it_went_is_answered_a_group_or_a_row_at_a_time(world: World) -> None:
    casey = bridge(world, "casey")
    (group,) = ready(casey.answer("where"))["groups"]
    rows = {row["id"]: row["says"] for row in group["rows"]}
    loan = world.account_ids["casey_named"]

    moved = decided(
        casey.act("move_group", {"title": group["title"], "rows": rows, "account": loan})
    )
    assert (moved.outcome, moved.rows) == (Acted.MOVED, 1)
    assert ready(casey.answer("where"))["groups"] == []
    assert moved.ruling is not None
    assert decided(casey.act("undo", {"ruling": moved.ruling})).outcome is Acted.UNDONE

    (row_id, shown) = next(iter(rows.items()))
    alone = decided(
        casey.act("move_row", {"row": row_id, "says": shown, "category": "Expenses:Kites"})
    )
    assert (alone.outcome, alone.rows) == (Acted.BOOKED, 1)
    stale = casey.act("move_row", {"row": row_id, "says": shown, "category": "Expenses:Kites"})
    assert stale == Decided(Acted.CHANGED, changed=1)


def test_where_it_went_takes_only_an_account_hearth_does_not_see(world: World) -> None:
    casey = bridge(world, "casey")
    (group,) = ready(casey.answer("where"))["groups"]
    rows = {row["id"]: row["says"] for row in group["rows"]}
    before = newest(world)

    for account in ("casey_checking", "casey_card", "joint_named", "avery_solo"):
        given = {"title": group["title"], "rows": rows, "account": world.account_ids[account]}
        assert casey.act("move_group", given) == Decided(Acted.NOT_YOURS)
    assert newest(world) == before


def test_a_row_its_bank_counts_offers_no_exclude_and_refuses_one(
    tmp_path: Path, clock: FakeClock
) -> None:
    settings = on_disk.build(tmp_path / "data", "fixture-passphrase", now=clock())
    keeper = books.Bookkeeper(
        books.from_household(settings, rulings=rulings_in, learns=may_learn), clock
    )
    keeper.recompute(wait=True)
    door = Bridge(settings, keeper, clock)
    door.sign_in("avery", "fixture-passphrase")

    rows = ready(door.answer("review"))["rows"]

    assert rows
    assert all(row["excludable"] is False for row in rows)
    row = rows[0]
    assert door.act("exclude", {"row": row["id"], "says": row["says"]}) == Decided(Acted.BANKS_ROW)


# Nothing that is not the viewer's, and nothing malformed


def everything_named(world: World) -> list[tuple[str, dict[str, object]]]:
    """Every decision, naming rows, groups, accounts and rulings on Casey's desk and nobody
    else's: what a viewer who is not Casey must not be able to change."""
    rows = [p[4] for p in household.PILE] + ["casey-kite-1"]
    loan = world.account_ids["casey_named"]
    found: list[tuple[str, dict[str, object]]] = []
    for row in rows:
        found += [
            ("accept", {"row": row, "shown": KITES}),
            ("book", {"row": row, "says": said("-10.00"), "category": KITES}),
            ("book", {"row": row, "says": said("-10.00"), "account": loan}),
            ("exclude", {"row": row, "says": said("-10.00")}),
            ("move_row", {"row": row, "says": said("-10.00"), "account": loan}),
            ("move_group", {"title": "x", "rows": {row: said("-10.00")}, "category": KITES}),
        ]
    found += [
        ("accept_group", {"group": group, "rows": dict.fromkeys(rows, KITES)})
        for group in ("Kites", "Unsure")
    ]
    conn = db.connect(world.settings.database)
    try:
        caseys = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM review_rulings WHERE member_id = ?", (world.member_ids["casey"],)
            )
        ]
    finally:
        conn.close()
    assert caseys
    found += [("undo", {"ruling": ruling}) for ruling in caseys]
    return found


@pytest.mark.parametrize("viewer", ["avery", "blake"])
def test_no_decision_on_what_is_not_the_viewers_changes_anything(kites: World, viewer: str) -> None:
    door = bridge(kites, viewer)
    casey = bridge(kites, "casey")
    # One decision of Casey's to try taking back.
    casey.act("accept", {"row": "casey-kite-3", "shown": KITES})
    kites.recompute()
    before = newest(kites)
    queue = ready(casey.answer("review"))

    for action, given in everything_named(kites):
        assert door.act(action, given) is Acted.MISSING, (action, given)

    assert newest(kites) == before
    assert ready(casey.answer("review")) == queue


@pytest.mark.parametrize(
    ("action", "given"),
    [
        ("accept", {"row": "casey-kite-1"}),
        ("accept", {"row": "casey-kite-1", "shown": KITES, "note": "x"}),
        ("accept", {"row": 5, "shown": KITES}),
        ("accept_group", {"group": "Kites", "rows": []}),
        ("accept_group", {"group": "Kites", "rows": {}}),
        ("accept_group", {"group": "Kites", "rows": {"casey-kite-1": 5}}),
        ("book", {"row": "casey-kite-1", "says": "x", "category": KITES, "account": "y"}),
        ("book", {"row": "casey-kite-1", "says": "x"}),
        ("book", {"row": "casey-kite-1", "says": "x", "category": "  "}),
        ("exclude", {"row": "casey-kite-1"}),
        ("undo", {}),
        ("undo", {"ruling": "1"}),
        ("undo", {"ruling": True}),
        ("undo", {"ruling": 0}),
        ("split", {"row": "casey-kite-1"}),
        ("note", {"row": "casey-kite-1", "note": "x"}),
    ],
)
def test_a_decision_given_what_it_does_not_take_is_missing(
    kites: World, action: str, given: dict[str, object]
) -> None:
    before = newest(kites)

    assert bridge(kites, "casey").act(action, given) is Acted.MISSING
    assert newest(kites) == before


def test_a_sync_takes_nothing_beside_its_name(kites: World) -> None:
    assert bridge(kites, "casey").act("sync", {"row": "casey-kite-1"}) is Acted.MISSING


def test_signed_out_a_decision_is_told_what_every_action_is(kites: World) -> None:
    door = Bridge(kites.settings, kites.bookkeeper, kites.clock)
    before = newest(kites)

    assert door.act("accept", {"row": "casey-kite-1", "shown": KITES}) is Acted.SIGNED_OUT
    assert newest(kites) == before


def test_a_decision_crosses_the_wire_with_what_it_decided(kites: World) -> None:
    written: list[str] = []
    lines = [
        json.dumps({"id": 1, "sign_in": {"username": "casey", "password": password_of("casey")}}),
        json.dumps({"id": 2, "act": "accept", "with": {"row": "casey-kite-1", "shown": KITES}}),
        json.dumps({"id": 3, "act": "accept", "with": {"row": "nobody", "shown": KITES}}),
        json.dumps({"id": 4, "act": "accept", "with": [1]}),
    ]

    stdio.serve(Bridge(kites.settings, kites.bookkeeper, kites.clock), lines, written.append)

    replies = [json.loads(line) for line in written]
    assert replies[1] == {
        "id": 2,
        "outcome": "accepted",
        "ruling": newest(kites),
        "rows": 1,
        "changed": 0,
    }
    assert replies[2] == {"id": 3, "outcome": "missing"}
    assert replies[3] == {"id": None, "error": "bad request"}


# One shape, checked from both sides


@pytest.mark.parametrize(
    ("fixture", "ask", "given"),
    [
        ("review", "review", {}),
        ("review_row", "review_row", {"id": "casey-kite-2"}),
        ("where", "where", {}),
    ],
)
def test_the_review_screens_have_the_shape_the_app_decodes(
    kites: World, fixture: str, ask: str, given: dict[str, object]
) -> None:
    """``review.json``, ``review_row.json`` and ``where.json`` are what the app's own tests
    decode (``apple/HearthKit``)."""
    casey = bridge(kites, "casey")
    casey.act("accept", {"row": "casey-kite-1", "shown": KITES})
    example = json.loads((FIXTURES / f"{fixture}.json").read_text())

    assert differences(ready(casey.answer(ask, given)), example["answer"]) == []


def test_a_decisions_reply_has_the_shape_the_app_decodes(kites: World) -> None:
    example = json.loads((FIXTURES / "decided.json").read_text())
    done = decided(bridge(kites, "casey").act("accept", {"row": "casey-kite-1", "shown": KITES}))

    assert differences({"id": 1, **done.wire()}, example) == []


def test_the_household_the_mac_app_reviews_has_a_group_one_accept_settles_and_a_transfer(
    tmp_path: Path, clock: FakeClock
) -> None:
    """What the Mac app's end-to-end test of Review counts on, read as ``hearth bridge``
    reads the household ``tests.native.on_disk`` writes."""
    settings = on_disk.build(tmp_path / "data", "fixture-passphrase", now=clock())
    keeper = books.Bookkeeper(
        books.from_household(settings, rulings=rulings_in, learns=may_learn), clock
    )
    keeper.recompute(wait=True)
    door = Bridge(settings, keeper, clock)
    door.sign_in("avery", "fixture-passphrase")

    answer = ready(door.answer("review"))

    assert answer["remaining"]["rows"] == on_disk.WAITING
    assert answer["elsewhere"]["rows"] == on_disk.ELSEWHERE
    waiting = on_disk.WAITING + on_disk.ELSEWHERE
    assert ready(door.answer("overview"))["review"]["waiting"] == waiting
    (transfer,) = ready(door.answer("where"))["groups"]
    assert (transfer["title"], transfer["accounts"]) == ("Online transfer to SAV 0417", [])
    assert "Expenses:FoodAndDrink:Groceries" in [c["name"] for c in transfer["categories"]]
    first = answer["groups"][0]
    assert (first["name"], first["size"], first["one_decision"]) == (
        "Food and drink: groceries",
        2,
        True,
    )
    assert first["suggested"][0]["label"] == "Groceries"
