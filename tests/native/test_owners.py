"""Whose the imported ledger's accounts are, said in the app: an admin is shown each of the
ledger's Assets and Liabilities accounts nobody owns yet, by name alone, and says whose each
is, one member's or several's, all at once, exactly as ``hearth add-account`` claims one
(``accounts.claim_all``). Nobody else is told anything of it.

The household is the matching one (``tests.test_matching``): Avery, an admin, and Blake, with
the ledger brought in and the mortgage, the brokerage, a second twin card and an account
elsewhere still nobody's. Every name and figure is invented."""

import json
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from hearth import accounts, audit, books
from hearth.disclosure import may_learn, rulings_in
from hearth.native.bridge import Acted, Bridge
from hearth.sharing import Facet
from tests import ledgers
from tests.conftest import FakeClock
from tests.native.test_ledger import _accounts, ask, bridge, keep, match, matched, unclaimed
from tests.test_matching import NOW, Household, build

__all__ = ["matched", "unclaimed"]

UNOWNED = (
    "Assets:Zq:Brokerage",
    "Assets:Zq:Elsewhere",
    "Liabilities:Zq:CardB",
    "Liabilities:Zq:Mortgage",
)
"""The base's balance-sheet accounts nobody has claimed, in the order of their names."""


def owners(door: Bridge) -> dict[str, Any]:
    told = door.answer("ledger_owners")
    assert told["state"] == "ready", told
    return told


def assign(door: Bridge, chosen: dict[str, list[str]]) -> Acted:
    done = door.act("assign_owners", {"owners": chosen})
    assert isinstance(done, Acted)
    return done


def owned(household: Household) -> dict[str, frozenset[str]]:
    """Every claimed account of the household and the usernames of its owners."""
    conn = household.fresh.connect()
    try:
        names = {m: u for u, m in household.fresh.member_ids.items()}
        return {
            a.ledger_account: frozenset(names[o] for o in a.owners)
            for a in accounts.all_accounts(conn)
        }
    finally:
        conn.close()


def recomputed(household: Household, clock: FakeClock) -> books.Bookkeeper:
    keeper = books.Bookkeeper(
        books.from_household(household.settings, rulings=rulings_in, learns=may_learn), clock
    )
    keeper.recompute(wait=True)
    assert keeper.status.failure is None, keeper.status.failure
    return keeper


# What an admin is told.


def test_an_admin_is_told_each_account_nobody_owns_by_name_and_nothing_more(
    matched: Household, clock: FakeClock
) -> None:
    """Only the ledger's Assets and Liabilities accounts nobody has claimed: never one someone
    owns, nor an Equity, Income or Expenses account, which is seen through the account on its
    other side. Of each, what the ledger's check told the admin before it was brought in: its
    name, its kind, the days the ledger opens and closes it. And every member, the admin first,
    with whose an account may be said to be."""
    told = owners(bridge(matched, clock, "avery"))

    assert told["imported"] is True
    assert [a["account"] for a in told["accounts"]] == list(UNOWNED)
    assert told["accounts"][3] == {
        "account": "Liabilities:Zq:Mortgage",
        "kind": "Liabilities",
        "opened": "2026-01-01",
        "closed": None,
        "name": "Zq Mortgage",
    }
    assert told["you"] == "avery"
    assert [m["username"] for m in told["members"]] == ["avery", "blake"]
    assert told["choices"] == [["avery"], ["blake"], ["avery", "blake"]]
    assert set(told) == {
        "state",
        "imported",
        "you",
        "members",
        "choices",
        "accounts",
        "computed_at",
        "as_of",
        "currency",
        "recomputing",
    }


def test_anyone_but_an_admin_is_told_it_is_missing(matched: Household, clock: FakeClock) -> None:
    door = bridge(matched, clock, "blake")

    assert door.answer("ledger_owners") == {"state": "missing"}
    assert assign(door, {"Liabilities:Zq:Mortgage": ["blake"]}) is Acted.MISSING
    assert "Liabilities:Zq:Mortgage" not in owned(matched)


def test_with_no_ledger_brought_in_there_is_nothing_to_say_whose(
    tmp_path: Path, clock: FakeClock
) -> None:
    fresh = build(tmp_path, clock, imported=False, claims={})
    door = bridge(fresh, clock, "avery")

    told = owners(door)

    assert told["imported"] is False
    assert told["accounts"] == []
    assert assign(door, {"Assets:Zq:Checking": ["avery"]}) is Acted.MISSING


# Saying whose.


def test_one_act_says_whose_each_is_alone_or_jointly_and_leaves_the_rest_for_later(
    matched: Household, clock: FakeClock
) -> None:
    """Through the one way an account comes to be owned: named as the household will see it,
    each owner's claim in the audit log with the admin who made it, and what was left for
    later still offered."""
    door = bridge(matched, clock, "avery")
    before = owned(matched)

    done = assign(
        door,
        {"Liabilities:Zq:Mortgage": ["avery", "blake"], "Assets:Zq:Brokerage": ["blake"]},
    )

    assert done is Acted.ASSIGNED
    after = owned(matched)
    assert after == {
        **before,
        "Liabilities:Zq:Mortgage": frozenset({"avery", "blake"}),
        "Assets:Zq:Brokerage": frozenset({"blake"}),
    }
    conn = matched.fresh.connect()
    try:
        names = {a.ledger_account: a.name for a in accounts.all_accounts(conn)}
        claims = [
            e
            for e in audit.all_events(conn)
            if e.kind is audit.EventKind.ACCOUNT_CLAIMED
            and e.actor_id == matched.fresh.member_ids["avery"]
        ]
    finally:
        conn.close()
    assert names["Liabilities:Zq:Mortgage"] == "Zq Mortgage"
    assert len(claims) == 3, "one line for each owner of each account"
    assert [a["account"] for a in owners(door)["accounts"]] == [
        "Assets:Zq:Elsewhere",
        "Liabilities:Zq:CardB",
    ]


@pytest.mark.parametrize(
    "chosen",
    [
        {"Assets:Zq:Checking": ["avery"]},  # someone owns it already
        {"Assets:Zq:Nowhere": ["avery"]},  # the ledger does not open it
        {"Expenses:Zq:Spent": ["avery"]},  # nobody owns a category
        {"Equity:Zq:Opening": ["avery"]},
        {"Liabilities:Zq:Mortgage": ["casey"]},  # no such member
        {"Liabilities:Zq:Mortgage": []},
        {"Liabilities:Zq:Mortgage": ["avery", "avery"]},
        {"Liabilities:Zq:Mortgage": "avery"},
        {"Liabilities:Zq:Mortgage": [7]},
    ],
)
def test_anything_not_offered_changes_nothing(
    matched: Household, clock: FakeClock, chosen: dict[str, Any]
) -> None:
    """Beside one that would be fine, so nothing at all is written: an account someone owns is
    never given another owner, and what is not there is told what anything missing is."""
    door = bridge(matched, clock, "avery")
    before = owned(matched)

    done = door.act("assign_owners", {"owners": {"Assets:Zq:Elsewhere": ["avery"], **chosen}})

    expected = Acted.REFUSED if "Assets:Zq:Checking" in chosen else Acted.MISSING
    assert done is expected
    assert owned(matched) == before
    assert door.act("assign_owners", {"owners": {}}) is Acted.MISSING
    assert door.act("assign_owners", {}) is Acted.MISSING
    assert owned(matched) == before


def test_an_account_claimed_meanwhile_refuses_the_whole_act_and_the_next_ask_is_as_things_stand(
    matched: Household, clock: FakeClock
) -> None:
    """Claimed for Blake on the command line after the admin was shown it: the admin's act
    writes nothing, never overwriting Blake's claim, and the next answer no longer offers it."""
    door = bridge(matched, clock, "avery")
    assert "Liabilities:Zq:Mortgage" in [a["account"] for a in owners(door)["accounts"]]
    conn = matched.fresh.connect()
    try:
        accounts.claim(
            conn,
            ledger_account="Liabilities:Zq:Mortgage",
            name="Mortgage",
            owners=[matched.fresh.member_ids["blake"]],
            now=NOW,
        )
    finally:
        conn.close()
    before = owned(matched)

    done = assign(door, {"Assets:Zq:Brokerage": ["avery"], "Liabilities:Zq:Mortgage": ["avery"]})

    assert done is Acted.REFUSED
    assert owned(matched) == before
    assert before["Liabilities:Zq:Mortgage"] == {"blake"}
    assert "Liabilities:Zq:Mortgage" not in json.dumps(owners(door))


def test_saying_whose_recomputes_the_books_behind_the_app(
    matched: Household, clock: FakeClock
) -> None:
    door = bridge(matched, clock, "avery")
    status = door.bookkeeper.status
    assert status.books is not None
    before = status.books.computed_at
    clock.now = NOW + timedelta(minutes=5)

    assert assign(door, {"Assets:Zq:Brokerage": ["avery"]}) is Acted.ASSIGNED

    deadline = time.monotonic() + 30
    while (books_ := door.bookkeeper.status.books) is None or books_.computed_at == before:
        assert time.monotonic() < deadline, "the books were never recomputed"
        time.sleep(0.05)


# Saying whose, and matching, in any order.


def test_assigned_accounts_are_offered_to_their_owners_to_match_and_to_nobody_else(
    matched: Household, clock: FakeClock
) -> None:
    """Assigning the brokerage to Avery offers it to Avery's IRA; assigning the second twin
    card to Blake offers it to nobody of Avery's. The books recompute without fail after each,
    and after the match."""
    door = bridge(matched, clock, "avery")
    ira = matched.ids["ins_zq_bank/ira"]
    twin = matched.ids["ins_zq_twin/card"]
    assert "Assets:Zq:Brokerage" not in _offered(door, ira)

    assert assign(door, {"Assets:Zq:Brokerage": ["avery"], "Liabilities:Zq:CardB": ["blake"]}) is (
        Acted.ASSIGNED
    )
    recomputed(matched, clock)

    assert "Assets:Zq:Brokerage" in _offered(door, ira)
    assert "Liabilities:Zq:CardB" not in _offered(door, twin)
    assert match(door, {ira: "Assets:Zq:Brokerage"}) is Acted.MATCHED
    recomputed(matched, clock)


def _offered(door: Bridge, account_id: str) -> list[str]:
    return [c["account"] for c in _accounts(ask(door, "matching"))[account_id]["choices"]]


def test_an_account_a_connection_was_carried_onto_is_owned_and_never_offered_again(
    unclaimed: Household, clock: FakeClock
) -> None:
    """Nothing of the ledger claimed, as an import from the app leaves it. Avery's card, kept by
    hearth, matched onto the ledger's card nobody owned, as the ledger's ids record it is: the
    card becomes Avery's with it, so it is no longer offered, and saying it is Blake's is
    refused. Then everything else is said to be Avery's, and the books still recompute."""
    door = bridge(unclaimed, clock, "avery")
    card = unclaimed.ids["ins_zq_bank/card"]
    keep(unclaimed, "ins_zq_bank/card", "Liabilities:Avery:Zq:Card", "avery")
    assert "Liabilities:Zq:Card" in [a["account"] for a in owners(door)["accounts"]]
    # Books computed with hearth's own card open, before the match moves its claim away.
    stale = bridge(unclaimed, clock, "avery")
    assert match(door, {card: "Liabilities:Zq:Card"}) is Acted.MATCHED

    listed = [a["account"] for a in owners(stale)["accounts"]]
    assert "Liabilities:Avery:Zq:Card" not in listed, "only the ledger's own are offered"
    assert "Liabilities:Zq:Card" not in listed
    recomputed(unclaimed, clock)

    rest = [a["account"] for a in owners(door)["accounts"]]
    assert "Liabilities:Zq:Card" not in rest
    assert assign(door, {"Liabilities:Zq:Card": ["blake"]}) is Acted.REFUSED
    assert owned(unclaimed)["Liabilities:Zq:Card"] == {"avery"}
    assert assign(door, {a: ["avery"] for a in rest}) is Acted.ASSIGNED
    recomputed(unclaimed, clock)
    assert owners(door)["accounts"] == []


def test_saying_whose_while_another_member_matches_never_fails_the_books(
    matched: Household, clock: FakeClock
) -> None:
    """Blake matches a connected account while the admin says whose the ledger's other
    accounts are, both as the app does it, each with the recompute behind it: the books hold
    both, and recompute."""
    admin = bridge(matched, clock, "avery")
    blake = bridge(matched, clock, "blake")
    savings = matched.ids["ins_zq_blake/chk"]
    listed = _accounts(ask(blake, "matching"))
    assert "Assets:Zq:Blake:Checking" in [c["account"] for c in listed[savings]["choices"]]

    assert assign(admin, {a: ["avery", "blake"] for a in UNOWNED}) is Acted.ASSIGNED
    assert match(blake, {savings: "Assets:Zq:Blake:Checking"}) is Acted.MATCHED

    keeper = recomputed(matched, clock)
    assert keeper.status.failure is None


# Nobody learns of an account until it is theirs.


def _every_answer(door: Bridge) -> str:
    """Every screen the doorway answers plainly, without when the books were computed."""
    from hearth.native import doorway

    found = []
    for ask_ in doorway.SCREENS:
        if ask_ in ("ledger_check", "account", "review_row", "price_history"):
            continue
        told = door.answer(ask_)
        if told["state"] == "loading":
            told = ask(door, ask_)
        found.append({k: v for k, v in told.items() if k not in ("computed_at", "recomputing")})
    return json.dumps(found, sort_keys=True)


def test_an_account_said_to_be_somebody_elses_changes_nothing_another_member_is_told(
    matched: Household, clock: FakeClock
) -> None:
    """Blake asks every screen before and after the admin says the mortgage and the brokerage
    are Avery's: every answer is the same, byte for byte. Said to be Blake's too, Blake sees
    the mortgage; and the account left for later nobody sees at all. Avery shares nothing, so
    no total of Avery's reaches Blake by the rules that would otherwise show it."""
    conn = matched.fresh.connect()
    try:
        for facet in Facet:
            conn.execute(
                "INSERT INTO sharing_preferences (member_id, facet, shared, updated_at)"
                " VALUES (?, ?, 0, '2026-01-01') ON CONFLICT (member_id, facet)"
                " DO UPDATE SET shared = 0",
                (matched.fresh.member_ids["avery"], facet.value),
            )
    finally:
        conn.close()
    blake = bridge(matched, clock, "blake")
    before = _every_answer(blake)
    admin = bridge(matched, clock, "avery")

    assert assign(admin, {"Liabilities:Zq:Mortgage": ["avery"]}) is Acted.ASSIGNED
    assert assign(admin, {"Assets:Zq:Brokerage": ["avery"]}) is Acted.ASSIGNED
    blake.bookkeeper.recompute(wait=True)

    assert _every_answer(blake) == before
    assert assign(admin, {"Liabilities:Zq:CardB": ["avery", "blake"]}) is Acted.ASSIGNED
    blake.bookkeeper.recompute(wait=True)
    after = _every_answer(blake)
    assert after != before
    assert "Assets:Zq:Elsewhere" not in after
    assert "Zq Mortgage" not in after


# Cost.


def test_the_owners_of_a_large_ledger_are_told_at_once(tmp_path: Path, clock: FakeClock) -> None:
    """Three hundred accounts nobody owns and twenty thousand entries: the answer is the books'
    opens, read in a few milliseconds, never a load of the ledger."""
    fresh = build(tmp_path, clock, imported=False, claims={})
    folder = tmp_path / "big"
    folder.mkdir()
    text = ledgers.big_ledger(accounts=300, entries=20_000)
    (folder / "main.beancount").write_text(text)
    from hearth.ledger_import import import_ledger

    import_ledger(fresh.settings, folder / "main.beancount", now=NOW)
    door = bridge(fresh, clock, "avery")
    started = time.perf_counter()

    told = owners(door)

    took = time.perf_counter() - started
    assert len(told["accounts"]) >= 300
    assert took < 0.5, f"{took:.2f}s"
    chosen = {a["account"]: ["avery"] for a in told["accounts"]}
    started = time.perf_counter()
    assert assign(door, chosen) is Acted.ASSIGNED
    took = time.perf_counter() - started
    assert took < 0.5, f"{took:.2f}s"
