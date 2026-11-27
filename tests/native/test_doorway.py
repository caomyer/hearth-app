"""The native app's doorway answers with disclosure's views, and with nothing else.

The figures are the ones worked out by hand in ``tests/household.py``. The last test is the
doorway's share of the privacy boundary: whatever a viewer asks, under every sharing scenario
the page crawl uses, no datum they may not see is in any answer the app is given once they have
signed in to the bridge.
"""

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from hearth import books
from hearth.books import LedgerError
from hearth.native import doorway as asks
from hearth.native.bridge import Acted, Bridge, Outcome
from hearth.native.common import Decided
from hearth.native.doorway import Doorway
from hearth.plaid.approval import Approval
from hearth.refresh import ASKING, Begun, Refresher
from hearth.review.ids import says
from hearth.review.queue import Order
from hearth.review.rulings import RulingStore
from hearth.sharing import DEFAULT_SHARED, Facet
from tests import household
from tests import learned as learned_household
from tests.conftest import World, password_of
from tests.household import USERNAMES
from tests.learned import Learned
from tests.test_privacy_boundary import LEARNED_SCENARIOS, SCENARIOS, TAX_SCENARIOS

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


NARROWED = {
    "account": "id",
    "transactions": "with",
    "review": "with",
    "review_row": "id",
    "statement_summary": "with",
    "income_statement": "with",
    "balance_sheet": "with",
    "year_by_year": "with",
    "price_history": "symbol",
}
"""Screens that are asked with what narrows them, which the crawl asks every way
`narrowings` names. A screen added to `SCREENS` is asked plainly unless it is named here."""

REVIEW_ROWS = [(p[4], p[2], p[0], p[3], p[5]) for p in household.PILE] + [
    (m[4], m[2], m[0], m[3], m[5]) for m in household.MOVED
]
"""Every review row in the household: its id, account, day, amount and group."""

OWNER = {"Assets:Zq:CaseyChecking": "casey", "Assets:Zq:AverySolo": "avery"}
"""Whose desk each review row is on: its account's one owner."""


STATEMENTS = ("statement_summary", "income_statement", "balance_sheet", "year_by_year")

STATEMENT_PERIODS: list[dict[str, object]] = [
    {},
    {"from": "2026-08-01", "to": "2026-08-31"},
    {"from": "2026-09-01", "to": "2026-10-15"},
    {"from": "2026-10-03", "to": "2026-10-03"},
    {"from": "2026-07-01", "to": "2026-09-30"},
]


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
    # The rows of the viewer's statements, by every category and all of them at once.
    asked += [("transactions", {"within": "statements", "limit": 200})]
    asked += [("transactions", {"within": "statements", "categories": [c]}) for c in categories]
    asked += [("transactions", {"categories": categories, "limit": 200})]
    # Every statement, for periods that cut months, a single day, a quarter, and none, at
    # market value and at cost, split every way.
    for period in STATEMENT_PERIODS:
        for valuation in ("market", "cost"):
            asked += [
                ("statement_summary", {**period, "valuation": valuation}),
                ("balance_sheet", {**period, "valuation": valuation}),
            ]
        asked += [
            ("income_statement", {**period, "columns": c}) for c in ("month", "quarter", "year")
        ]
    asked += [("year_by_year", {"valuation": v}) for v in ("market", "cost")]
    groups = [*sorted({row[4] for row in REVIEW_ROWS}), "Kites", "zz-nothing"]
    asked += [("review", {})]
    asked += [("review", {"order": o}) for o in ("confidence", "amount", "date", "account")]
    asked += [("review", {"account": i}) for i in ids]
    asked += [("review", {"category": c}) for c in ["-", *categories]]
    asked += [("review", {"group": g}) for g in groups]
    asked += [("review", {"text": w}) for w in words]
    asked += [("review_row", {"id": row[0]}) for row in REVIEW_ROWS]
    asked += [("review_row", {"id": "no-such-row"})]
    # Every security in the household, cash, and one nobody holds: a price history is a
    # holding's, so one the viewer may not see is missing, whoever holds it.
    symbols = [*household.SECURITIES, "USD", "ZQZZ"]
    asked += [("price_history", {"symbol": s}) for s in symbols]
    return asked


def positions() -> Iterator[household.Secret]:
    """What each position is, as the doorway writes it: its units and its price, each a quoted
    decimal, which every close of its price history is too. They go with its account's
    balances, as its symbol and its value do (``household.secrets``)."""
    for symbol, (held_in, units, price) in household.SECURITIES.items():
        owners = next(a.owners for a in household.ACCOUNTS if a.ledger_account == held_in)
        visible = household.shared(Facet.ACCOUNT_BALANCES, owners)
        yield household.secret(f'"units": "{units}"', f"{symbol} units", visible)
        yield household.secret(f'"{price}"', f"{symbol} price", visible)


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
        # Where it stands, and the part of it a narrowing's categories are, say of the narrowing.
        return [
            json.dumps({k: v for k, v in e.items() if k not in ("position", "part")})
            for e in entries
        ]

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
        # Every statement is there for every viewer: one of nothing but their own, if need be.
        assert answer["state"] == "ready" or ask not in STATEMENTS, (ask, given)
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
    seen = household.Seen("\n".join(json.dumps(answer) for answer in answers))

    leaked = [
        f"{secret.what}: {text!r}"
        for secret in [*household.secrets(world.account_ids), *positions()]
        if not secret.visible(viewer, choices)
        for text in secret.texts
        if text.replace(",", "") in seen
    ]
    assert not leaked, f"{viewer} could see private data: {leaked}"

    # Whatever anyone shares, nobody's queue is anyone else's: no row another member reviews,
    # not its id nor its payee, is in any review screen or any decision's reply.
    reviewing = household.Seen(
        "\n".join(
            json.dumps(door.answer(ask, given))
            for ask, given in [("where", {}), *narrowings(world)]
            if "review" in ask or ask == "where"
        )
        + json.dumps([a for a in answers if "action" in a])
    )
    others = [row for row in household.PILE + household.MOVED if OWNER[row[2]] != viewer]
    found = [text for row in others for text in (row[1], row[4]) if text in reviewing]
    assert not found, f"{viewer} was shown another member's review rows: {found}"


@pytest.mark.parametrize("facet", list(Facet))
@pytest.mark.parametrize("viewer", household.USERNAMES)
def test_no_statement_holds_what_a_part_shared_in_all_but_one_facet_keeps(
    world: World, facet: Facet, viewer: str
) -> None:
    """Every member shares every facet but one, the same one: every part not the viewer's is
    left out of their statements, and no statement, nor the journal of a statement's rows,
    holds a datum the viewer may not see, on any tab, period, split or valuation."""
    choices = dict.fromkeys(household.USERNAMES, frozenset(Facet) - {facet})
    for username, shared in choices.items():
        world.choose(username, shared)
    door = Doorway(world.settings, world.bookkeeper, viewer)

    answers = [door.answer(ask, given) for ask, given in narrowings(world) if ask in STATEMENTS]
    answers += [
        door.answer(ask, given)
        for ask, given in narrowings(world)
        if ask == "transactions" and "within" in given
    ]
    assert all(answer["state"] == "ready" for answer in answers)
    seen = household.Seen("\n".join(json.dumps(answer) for answer in answers))

    leaked = [
        f"{secret.what}: {text!r}"
        for secret in [*household.secrets(world.account_ids), *positions()]
        if not secret.visible(viewer, choices)
        for text in secret.texts
        if text.replace(",", "") in seen
    ]
    assert not leaked, f"{viewer} could see private data: {leaked}"
    mine = {"avery": ["Avery", "Joint accounts"], "blake": ["Blake", "Joint accounts"]}
    parts = [a["scope"]["parts"] for a in answers if isinstance(a.get("scope"), dict)]  # type: ignore[index]
    assert all(p == mine.get(viewer, ["Casey"]) for p in parts), parts


def every_answer(door: Doorway, world: World) -> list[dict[str, Any]]:
    """Every screen the viewer can ask for, plainly and every way the crawl narrows it."""
    found = [door.answer(ask) for ask in asks.SCREENS if ask not in NARROWED]
    return found + [door.answer(ask, given) for ask, given in narrowings(world)]


def taxed_flags(answers: list[dict[str, Any]]) -> list[object]:
    """Whatever every answer says of whether the viewer's statements hold any tax."""
    return [
        flag
        for answer in answers
        for flag in (answer.get("taxed"), (answer.get("scope") or {}).get("taxed"))
        if flag is not None
    ]


@pytest.mark.parametrize("scenario", TAX_SCENARIOS)
@pytest.mark.parametrize("viewer", household.USERNAMES)
def test_a_members_tax_reaches_no_answer_its_viewer_may_not_see(
    world: World, scenario: str, viewer: str
) -> None:
    """Blake pays tax in a month that is over and in this one. No answer holds it for anyone
    who may not see Blake's categories or transactions; and whether a viewer's statements hold
    any tax, which the app's switch goes by, is judged on their statements alone: it is what
    it was before Blake paid any, unless Blake is in them."""
    choices = dict.fromkeys(household.USERNAMES, DEFAULT_SHARED)
    choices.update(SCENARIOS[scenario])
    for username, shared in choices.items():
        world.choose(username, shared)
    door = Doorway(world.settings, world.bookkeeper, viewer)
    before = taxed_flags(every_answer(door, world))
    assert before, "every statement and the overview say it"
    assert not any(before), "nobody's books hold tax yet"

    with world.ledger.open("a") as ledger:
        ledger.write("\n" + household.tax_text())
    world.recompute()
    assert world.bookkeeper.status.failure is None, world.bookkeeper.status.failure
    answers = every_answer(door, world)
    seen = household.Seen("\n".join(json.dumps(answer) for answer in answers))

    for secret in household.tax_secrets():
        present = [text for text in secret.texts if text.replace(",", "") in seen]
        if not secret.visible(viewer, choices):
            assert not present, f"{viewer} could see {secret.what}: {present!r}"
    in_statements = viewer == "blake" or choices["blake"] == frozenset(Facet)
    after = taxed_flags(answers)
    assert after == [in_statements] * len(after), (viewer, scenario)


@pytest.mark.parametrize("scenario", LEARNED_SCENARIOS)
@pytest.mark.parametrize("viewer", learned_household.USERNAMES)
def test_no_answer_carries_a_category_a_suggestion_could_have_carried(
    learned: Learned, scenario: str, viewer: str
) -> None:
    """The doorway's share of the suggestions' boundary (``tests/learned.py``): no answer holds
    another member's category, and a reason is told only on a row the viewer reviews."""
    choices = dict.fromkeys(learned_household.USERNAMES, DEFAULT_SHARED)
    choices.update(LEARNED_SCENARIOS[scenario])
    for username, shared in choices.items():
        learned.choose(username, shared)
    door = Doorway(learned.fresh.settings, learned.fresh.bookkeeper, viewer)

    ids = [*learned.account_ids.values(), "no-such-account"]
    categories = [f"Expenses:Zq:{word}" for word in learned_household.SECRETS] + ["Expenses"]
    words = [
        learned_household.DELI,
        learned_household.GROCER,
        learned_household.MARKET,
        *learned_household.SECRETS,
    ]
    asked: list[tuple[str, dict[str, object]]] = [("transactions", {"limit": asks.MOST})]
    asked += [("account", {"id": i}) for i in ids]
    asked += [("transactions", {"account": i}) for i in ids]
    asked += [("transactions", {"category": c}) for c in categories]
    asked += [("transactions", {"text": w}) for w in words]
    asked += [("transactions", {"show": "unreviewed"})]
    # Review, every way it is narrowed, and every row of everyone's pile on its own.
    pile = learned.pile()
    asked += [("review", {"category": c}) for c in categories]
    asked += [("review", {"text": w}) for w in words]
    asked += [("review", {"account": i}) for i in ids]
    asked += [("review", {"group": g}) for g in sorted({i.group for i in pile})]
    asked += [("review", {"order": o.value}) for o in Order]
    asked += [("review_row", {"id": i.review_id}) for i in pile]
    answers = [door.answer(ask) for ask in asks.SCREENS if ask not in NARROWED]
    answers += [door.answer(ask, given) for ask, given in asked]
    # Every decision on a row of another's desk is told it is missing, and records nothing,
    # whatever it would book it to: one of the secret categories among them.
    owner = {i.review_id: learned_household.OWNERS[i.account] for i in pile}
    others = [i for i in pile if viewer not in owner[i.review_id]]
    grouped = {i.group for i in pile if viewer in owner[i.review_id]}
    conn = learned.fresh.connect()
    try:
        rulings = RulingStore(conn)
        before = rulings.newest()
        for item in others:
            for category in categories:
                tries: list[tuple[str, dict[str, object]]] = [
                    ("accept", {"row": item.review_id, "shown": category}),
                    ("accept_group", {"group": item.group, "rows": {item.review_id: category}}),
                    ("book", {"row": item.review_id, "says": item.says, "category": category}),
                    ("exclude", {"row": item.review_id, "says": item.says}),
                    ("move_row", {"row": item.review_id, "says": item.says, "category": category}),
                    (
                        "move_group",
                        {"title": "x", "rows": {item.review_id: item.says}, "category": category},
                    ),
                ]
                for action, given in tries:
                    done = door.act(action, given)
                    if action == "accept_group" and item.group in grouped:
                        # A group is on the desk of everyone with a row in it: another's row
                        # in it is told what a row that is not there is, and decides nothing.
                        assert isinstance(done, Decided), (given, done)
                        assert done.rows == 0, (given, done)
                    else:
                        assert done is Acted.MISSING, (action, given)
        assert rulings.newest() == before
    finally:
        conn.close()
    seen = household.Seen("\n".join(json.dumps(answer) for answer in answers))

    leaked = [
        word
        for word, owner in learned_household.SECRETS.items()
        if word in seen and not learned_household.may_see(viewer, owner, choices)
    ]
    assert not leaked, f"{viewer} could see another member's categories: {leaked}"
    reviews = {
        learned.account_ids[account]
        for account, owners in learned_household.OWNERS.items()
        if viewer in owners
    }
    reasons = []
    for answer in answers:
        entries = answer.get("entries", [])
        assert isinstance(entries, list)
        for entry in entries:
            if entry["reason"] is not None:
                assert {a["id"] for a in entry["accounts"]} <= reviews, entry
                reasons.append(entry["reason"])
    mine = {"avery": "Pantry once before", "blake": "As your ledger books it"}
    assert mine[viewer] in reasons if viewer in mine else reasons == []

    # Review in the app shows only the viewer's own rows, and the journal says of each the
    # very words Review does: one source for a row's reason.
    said: dict[str, str] = {}
    shown: list[Any] = answers
    for answer in shown:
        rows = answer.get("rows")
        found = [*(rows if isinstance(rows, list) else []), *filter(None, [answer.get("row")])]
        found += [
            r for g in answer.get("groups", []) for r in g.get("rows", []) if isinstance(r, dict)
        ]
        for row in found:
            assert row["account"]["id"] in reviews, row
            assert said.setdefault(row["id"], row["reason"]) == row["reason"], row
    for answer in shown:
        for entry in answer.get("entries", []):
            if entry["review"] is not None:
                assert entry["reason"] == said[entry["review"]], entry
    assert (mine[viewer] in said.values()) if viewer in mine else said == {}
