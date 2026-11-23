"""The native app's doorway answers with disclosure's views, and with nothing else.

The figures are the ones worked out by hand in ``tests/household.py``. The last test is the
doorway's share of the privacy boundary: whatever a viewer asks, under every sharing scenario
the page crawl uses, no datum they may not see is in any answer the app is given once they have
signed in to the bridge.
"""

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from hearth import books
from hearth.books import LedgerError
from hearth.native import doorway as asks
from hearth.native.bridge import Acted, Bridge, Outcome
from hearth.native.doorway import Doorway
from hearth.plaid.approval import Approval
from hearth.refresh import ASKING, Begun, Refresher
from hearth.review.ids import says
from hearth.review.rulings import RulingStore
from hearth.sharing import DEFAULT_SHARED, Facet
from tests import household
from tests.conftest import World, password_of
from tests.household import USERNAMES
from tests.test_privacy_boundary import SCENARIOS

FIXTURES = Path(__file__).parent / "fixtures"


def doorway(world: World, viewer: str) -> Doorway:
    return Doorway(world.settings, world.bookkeeper, viewer)


def amount(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def test_the_overview_is_the_households_net_worth_to_the_cent(world: World) -> None:
    answer = doorway(world, "avery").answer("overview")

    assert answer["state"] == "ready"
    assert answer["currency"] == "USD"
    assert answer["as_of"] == household.TODAY.isoformat()
    assert answer["connected"] is True
    worth = answer["net_worth"]
    assert isinstance(worth, dict)
    assert Decimal(worth["value"]) == amount(household.HOUSEHOLD_NET_WORTH)
    assert worth["is_known"] is True
    assert worth["is_partial"] is False
    assert worth["without"] == []


def test_money_crosses_as_a_decimal_string_never_a_float(world: World) -> None:
    answer = doorway(world, "avery").answer("overview")

    def floats(value: object) -> list[float]:
        if isinstance(value, float):
            return [value]
        if isinstance(value, dict):
            return [f for item in value.values() for f in floats(item)]
        if isinstance(value, list):
            return [f for item in value for f in floats(item)]
        return []

    assert floats(answer) == []
    history = answer["history"]
    assert isinstance(history, list)
    assert history
    assert Decimal(history[-1]["value"]) == amount(household.HOUSEHOLD_NET_WORTH)


def test_a_total_that_leaves_a_part_out_says_which(world: World) -> None:
    world.choose("avery", DEFAULT_SHARED - {Facet.NET_WORTH_TOTAL})

    worth = doorway(world, "casey").answer("overview")["net_worth"]

    assert isinstance(worth, dict)
    assert worth["is_partial"] is True
    assert set(worth["without"]) == {"Avery", "Joint accounts"}
    assert Decimal(worth["value"]) == amount(household.NET_WORTH["blake"]) + amount(
        household.NET_WORTH["casey"]
    )


def test_a_screen_that_does_not_exist_is_missing(world: World) -> None:
    assert doorway(world, "avery").answer("everything") == {"state": "missing"}


def test_someone_who_is_not_a_member_is_told_what_a_missing_screen_is(world: World) -> None:
    assert doorway(world, "mallory").answer("overview") == doorway(world, "avery").answer("nope")


def test_before_the_first_recompute_the_books_are_loading(world: World) -> None:
    waiting = books.Bookkeeper(world.bookkeeper_establish, world.clock)

    assert Doorway(world.settings, waiting, "avery").answer("overview") == {"state": "loading"}


def test_a_failed_first_recompute_says_so_in_one_sentence(world: World) -> None:
    def establish(now: datetime) -> books.Books:
        raise LedgerError("The ledger could not be read.")

    failed = books.Bookkeeper(establish, world.clock)
    failed.recompute(wait=True)

    assert Doorway(world.settings, failed, "avery").answer("overview") == {
        "state": "failed",
        "summary": "The ledger could not be read.",
    }


def test_moments_are_utc_to_the_second_in_one_shape(world: World) -> None:
    kolkata = timezone(timedelta(hours=5, minutes=30))
    world.clock.now = datetime(2026, 10, 15, 17, 30, 7, 250_000, tzinfo=kolkata)
    world.recompute()

    answer = doorway(world, "avery").answer("overview")

    assert answer["computed_at"] == "2026-10-15T12:00:07Z"


def test_the_overview_has_the_shape_the_app_decodes(world: World) -> None:
    """``overview.json`` is what the app's own tests decode (``apple/HearthKit``): a change of
    shape here that the app has not been taught fails on this side first."""
    example = json.loads((FIXTURES / "overview.json").read_text())

    assert differences(doorway(world, "avery").answer("overview"), example["answer"]) == []


def differences(told: object, example: object, at: str = "answer") -> list[str]:
    """Where `told` has another shape than `example`: other keys, or another kind of value.
    A null fits anything, as it is how a figure that is not known is written, and an empty
    list fits any list."""
    if told is None or example is None:
        return []
    if isinstance(told, dict) and isinstance(example, dict):
        if told.keys() != example.keys():
            return [f"{at}: keys {sorted(told)} != {sorted(example)}"]
        return [d for key in told for d in differences(told[key], example[key], f"{at}.{key}")]
    if isinstance(told, list) and isinstance(example, list):
        return differences(told[0], example[0], f"{at}[0]") if told and example else []
    return [] if type(told) is type(example) else [f"{at}: {type(told).__name__}"]


NARROWED = {"account": "id", "transactions": "with", "review": "with", "review_row": "id"}
"""Screens that are asked with what narrows them, which the crawl asks every way
`narrowings` names. A screen added to `SCREENS` is asked plainly unless it is named here."""

REVIEW_ROWS = [(p[4], p[2], p[0], p[3], p[5]) for p in household.PILE] + [
    (m[4], m[2], m[0], m[3], m[5]) for m in household.MOVED
]
"""Every review row in the household: its id, account, day, amount and group."""

OWNER = {"Assets:Zq:CaseyChecking": "casey", "Assets:Zq:AverySolo": "avery"}
"""Whose desk each review row is on: its account's one owner."""


def narrowings(world: World) -> list[tuple[str, dict[str, object]]]:
    """Every way the crawl narrows a screen: every account in the household by id, each
    member's and the one nobody owns among them, every category and payee in it, each chip,
    periods, windows, and things that are in nobody's books at all."""
    ids = [*world.account_ids.values(), "no-such-account"]
    categories = [*sorted({t[3] for t in household.TRANSACTIONS}), "Expenses", "Income"]
    words = [t[1] for t in household.TRANSACTIONS] + [p[1] for p in household.PILE]
    words += [m[1] for m in household.MOVED] + ["Transfer", "Memo", "zz-nothing"]
    asked: list[tuple[str, dict[str, object]]] = [("transactions", {})]
    asked += [("account", {"id": i}) for i in ids]
    asked += [("transactions", {"account": i}) for i in ids]
    asked += [("transactions", {"category": c}) for c in categories]
    asked += [("transactions", {"text": w}) for w in words]
    asked += [("transactions", {"show": s}) for s in ("unreviewed", "transfers", "balance_checks")]
    asked += [
        ("transactions", {"from": "2025-01-01", "to": "2026-12-31"}),
        ("transactions", {"from": "2026-09-01", "to": "2026-09-30", "show": "unreviewed"}),
    ]
    asked += [("transactions", {"offset": n, "limit": 1}) for n in range(0, 12, 3)]
    groups = [*sorted({row[4] for row in REVIEW_ROWS}), "Kites", "zz-nothing"]
    asked += [("review", {})]
    asked += [("review", {"order": o}) for o in ("confidence", "amount", "date", "account")]
    asked += [("review", {"account": i}) for i in ids]
    asked += [("review", {"category": c}) for c in ["-", *categories]]
    asked += [("review", {"group": g}) for g in groups]
    asked += [("review", {"text": w}) for w in words]
    asked += [("review_row", {"id": row[0]}) for row in REVIEW_ROWS]
    asked += [("review_row", {"id": "no-such-row"})]
    return asked


def decisions(world: World) -> list[tuple[str, frozenset[str], dict[str, object]]]:
    """Every review decision, on every row in the household and on rows that are in nobody's
    books, against every account and the categories, each with whose desk holds what it names
    (nobody's, for what is not there): as the row said, as it did not, and as a forged group
    would have it."""
    ids = [*world.account_ids.values(), "no-such-account"]
    found: list[tuple[str, frozenset[str], dict[str, object]]] = []
    for review_id, account, day, amount, group in [
        *REVIEW_ROWS,
        ("no-such-row", "Assets:Zq:CaseyChecking", household.OCT, "-1.00", "Unsure"),
    ]:
        mine = frozenset({OWNER[account]}) if review_id != "no-such-row" else frozenset()
        # A group is on the desk of everyone with a row in it, whichever row is named.
        grouped = frozenset(OWNER[row[1]] for row in REVIEW_ROWS if row[4] == group)
        said = says(account, day, Decimal(amount), "USD")
        stale = says(account, day, Decimal(amount) * 2, "USD")
        found += [
            ("accept", mine, {"row": review_id, "shown": "Expenses:Kites"}),
            ("exclude", mine, {"row": review_id, "says": stale}),
            ("book", mine, {"row": review_id, "says": stale, "category": "Expenses:Kites"}),
            ("accept_group", grouped, {"group": group, "rows": {review_id: "Expenses:Kites"}}),
            ("accept_group", frozenset(), {"group": "zz-forged", "rows": {review_id: "x"}}),
        ]
        found += [("book", mine, {"row": review_id, "says": said, "account": i}) for i in ids]
        found += [("move_row", mine, {"row": review_id, "says": said, "account": i}) for i in ids]
        found += [
            ("move_group", mine, {"title": group, "rows": {review_id: said}, "account": i})
            for i in ids
        ]
        found += [
            ("book", mine, {"row": review_id, "says": said, "category": "Expenses:Kites"}),
            ("exclude", mine, {"row": review_id, "says": said}),
        ]
    # Whose each ruling is, the crawl's own decisions among them, is the desk's to say.
    found += [("undo", frozenset(USERNAMES), {"ruling": n}) for n in range(1, 40)]
    return found


def assert_within(narrowed: dict[str, object], whole: dict[str, object]) -> None:
    def rows(answer: dict[str, object]) -> list[str]:
        entries = answer["entries"]
        assert isinstance(entries, list)
        return [json.dumps({k: v for k, v in e.items() if k != "position"}) for e in entries]

    held = rows(whole)
    assert all(row in held for row in rows(narrowed)), "a narrowed journal found more"


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("viewer", household.USERNAMES)
def test_no_answer_holds_what_its_viewer_may_not_see(
    world: World, scenario: str, viewer: str
) -> None:
    choices = dict.fromkeys(household.USERNAMES, DEFAULT_SHARED)
    choices.update(SCENARIOS[scenario])
    for username, shared in choices.items():
        world.choose(username, shared)

    # Asked as the app asks: through a bridge the viewer signed in to with their password, once
    # it has synced every member's connection, one of which needs its member to sign in again,
    # so the banks screen has every member's outcome to keep to the viewer's own.
    world.plaid.require_login(world.item_ids["blake"])
    syncs = Refresher(
        world.settings,
        replace(world.setup, approval=Approval.of(ASKING)),
        world.bookkeeper,
        world.clock,
    )
    assert syncs.start() is Begun.STARTED
    syncs.wait()
    door = Bridge(world.settings, world.bookkeeper, world.clock, syncs)
    assert door.sign_in(viewer, password_of(viewer)) is Outcome.SIGNED_IN
    answers = [door.answer(ask) for ask in asks.SCREENS if ask not in NARROWED]
    assert all(answer["state"] == "ready" for answer in answers)
    whole = door.answer("transactions", {"limit": asks.MOST})
    for ask, given in narrowings(world):
        answer = door.answer(ask, given)
        answers.append(answer)
        if ask == "transactions" and answer["state"] == "ready":
            # Narrowing the journal only ever leaves out: it never finds what the whole of it
            # did not hold, whatever it is asked to look for.
            assert_within(answer, whole)
    # Every decision there is, on every row: one on a row on nobody's desk, or anyone else's,
    # changes nothing and is told what a row that does not exist is told.
    conn = world.connect()
    try:
        rulings = RulingStore(conn)
        for action, desks, given in decisions(world):
            before = rulings.newest()
            done = door.act(action, given)
            told = done.value if isinstance(done, Acted) else done.wire()
            answers.append({"action": action, "told": told})
            if viewer not in desks:
                assert done is Acted.MISSING, (action, given, done)
                assert rulings.newest() == before
    finally:
        conn.close()
    world.recompute()
    answers += [door.answer(ask) for ask in asks.SCREENS if ask not in NARROWED]
    answers += [door.answer(ask, given) for ask, given in narrowings(world) if "review" in ask]
    # The doorway writes money without the thousands separators a page shows it with.
    seen = "\n".join(json.dumps(answer) for answer in answers)

    leaked = [
        f"{secret.what}: {text!r}"
        for secret in household.secrets(world.account_ids)
        if not secret.visible(viewer, choices)
        for text in secret.texts
        if text.replace(",", "") in seen
    ]
    assert not leaked, f"{viewer} could see private data: {leaked}"

    # Whatever anyone shares, nobody's queue is anyone else's: no row another member reviews,
    # not its id nor its payee, is in any review screen or any decision's reply.
    reviewing = "\n".join(
        json.dumps(door.answer(ask, given))
        for ask, given in [("where", {}), *narrowings(world)]
        if "review" in ask or ask == "where"
    ) + json.dumps([a for a in answers if "action" in a])
    others = [row for row in household.PILE + household.MOVED if OWNER[row[2]] != viewer]
    found = [text for row in others for text in (row[1], row[4]) if text in reviewing]
    assert not found, f"{viewer} was shown another member's review rows: {found}"
