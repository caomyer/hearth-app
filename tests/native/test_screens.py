"""The app's accounts, an account's own page, and the journal, from disclosure's views.

The figures are the ones worked out by hand in ``tests/household.py``, and in
``tests.native.on_disk`` for a household whose books hearth keeps from its bank, where every
balance is checked against what the bank reported. What each viewer may be told is checked
against the contract in ``tests/household.py`` by the leak crawl in ``test_doorway.py``.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from hearth import books
from hearth.disclosure import rulings_in
from hearth.figures import MOVED_IN
from hearth.native.doorway import MOST, PAGE, Doorway
from hearth.sharing import DEFAULT_SHARED, Facet
from tests import household
from tests.conftest import FakeClock, World
from tests.native import on_disk
from tests.native.test_doorway import differences

FIXTURES = Path(__file__).parent / "fixtures"
EVERYTHING = frozenset(Facet)


def ask(world: World, viewer: str, screen: str, **given: object) -> dict[str, Any]:
    return Doorway(world.settings, world.bookkeeper, viewer).answer(screen, given)


def amount(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def listed(answer: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every account an accounts answer lists, by name."""
    return {a["name"]: a for g in answer["groups"] for a in g["accounts"]}


def payees(answer: dict[str, Any]) -> list[str]:
    return [e["payee"] for e in answer["entries"]]


@pytest.fixture
def kept(tmp_path: Path, clock: FakeClock) -> Doorway:
    """Avery's doorway onto the household whose books hearth keeps from what its bank sent."""
    settings = on_disk.build(tmp_path / "data", "fixture-passphrase", now=clock())
    keeper = books.Bookkeeper(books.from_household(settings, rulings=rulings_in), clock)
    keeper.recompute(wait=True)
    assert keeper.status.failure is None, keeper.status.failure
    return Doorway(settings, keeper, "avery")


# Accounts


def test_a_members_own_accounts_are_grouped_by_kind_with_their_balances(world: World) -> None:
    answer = ask(world, "avery", "accounts")

    assert answer["state"] == "ready"
    groups = [(g["name"], [a["name"] for a in g["accounts"]]) for g in answer["groups"]]
    assert groups == [
        ("Cash", ["Narwhal Joint", "Quokka Savings"]),
        ("Investments", ["Wombat Shares"]),
        ("Not seen by hearth", ["Tamarin Savings"]),
    ]
    accounts = listed(answer)
    for key in ("avery_solo", "avery_invest", "joint"):
        name = next(a.name for a in household.ACCOUNTS if a.key == key)
        assert Decimal(accounts[name]["balance"]["value"]) == amount(household.BALANCE[key])
        assert accounts[name]["id"] == world.account_ids[key]
    assert accounts["Narwhal Joint"]["owners"] == ["Avery", "Blake"]


def test_a_card_is_credit_and_its_balance_is_what_is_owed(world: World) -> None:
    card = listed(ask(world, "casey", "accounts"))["Coati Card"]

    assert card["kind"] == "Credit"
    assert Decimal(card["balance"]["value"]) == -amount(household.BALANCE["casey_card"])


def test_an_account_hearth_does_not_see_is_unseen_never_a_balance_of_zero(world: World) -> None:
    unseen = listed(ask(world, "blake", "accounts"))["Tamarin Savings"]

    assert unseen["standing"] == "unseen"
    assert unseen["balance"] is None
    assert unseen["institution"] == "Okapi Credit Union"
    assert unseen["mask"] == "8136"
    assert unseen["unseen_kind"] == "Bank account"


def test_a_ledger_balance_nothing_checks_says_so(world: World) -> None:
    accounts = listed(ask(world, "avery", "accounts"))

    assert accounts["Quokka Savings"]["standing"] == "unchecked"
    assert accounts["Quokka Savings"]["checked"] is None


def test_others_accounts_are_listed_by_whose_only_as_far_as_they_share(world: World) -> None:
    assert "Quokka Savings" not in listed(ask(world, "casey", "accounts"))

    world.choose("avery", EVERYTHING)
    groups = ask(world, "casey", "accounts")["groups"]

    avery = next(g for g in groups if g["name"] == "Avery")
    assert avery["yours"] is False
    assert [a["name"] for a in avery["accounts"]] == ["Quokka Savings", "Wombat Shares"]
    # The joint account is also Blake's, who shares nothing of it.
    assert "Narwhal Joint" not in listed({"groups": groups})


def test_an_account_whose_balance_is_not_shared_says_withheld(world: World) -> None:
    world.choose("avery", DEFAULT_SHARED | {Facet.TRANSACTIONS})

    quokka = listed(ask(world, "casey", "accounts"))["Quokka Savings"]

    assert quokka["standing"] == "withheld"
    assert quokka["balance"] is None
    assert quokka["kind"] is None
    assert quokka["transactions"] is True


def test_a_balance_hearth_keeps_from_a_bank_matches_what_the_bank_reported(
    kept: Doorway,
) -> None:
    accounts = listed(kept.answer("accounts"))

    checking = accounts["Everyday checking"]
    assert checking["standing"] == "matches"
    assert checking["balance"]["value"] == "3250.40"
    assert checking["checked"] == household.NOW.date().isoformat()
    assert checking["institution"] == "Larkspur Savings Bank"
    assert checking["behind"] is False
    assert accounts["Rewards card"]["balance"]["value"] == "-612.15"


def test_a_connection_is_named_to_its_holder_alone(kept: Doorway) -> None:
    blake = Doorway(kept.settings, kept.bookkeeper, "blake")

    assert blake.answer("accounts")["groups"] == []


# One account


def test_an_account_is_answered_by_its_id_with_its_history(world: World) -> None:
    answer = ask(world, "avery", "account", id=world.account_ids["avery_solo"])

    assert answer["state"] == "ready"
    assert answer["account"]["name"] == "Quokka Savings"
    assert Decimal(answer["account"]["balance"]["value"]) == amount(household.BALANCE["avery_solo"])
    assert answer["history"][0]["from"] == household.OPENED.isoformat()


@pytest.mark.parametrize("key", ["casey_checking", "casey_card", "unowned", "casey_named"])
def test_an_account_the_viewer_may_not_see_is_missing(world: World, key: str) -> None:
    assert ask(world, "avery", "account", id=world.account_ids[key]) == {"state": "missing"}


@pytest.mark.parametrize(
    "given",
    [{}, {"id": ""}, {"id": 7}, {"id": None}, {"id": "x" * 65}, {"id": "a", "month": "2026-10"}],
)
def test_an_account_asked_for_in_another_shape_is_missing(world: World, given: object) -> None:
    door = Doorway(world.settings, world.bookkeeper, "avery")

    assert door.answer("account", given) == {"state": "missing"}  # type: ignore[arg-type]


def test_a_screen_that_takes_nothing_refuses_to_be_narrowed(world: World) -> None:
    door = Doorway(world.settings, world.bookkeeper, "avery")

    assert door.answer("overview", {"id": "x"}) == {"state": "missing"}
    assert door.answer("accounts", {"limit": 1}) == {"state": "missing"}


# The journal


def test_the_journal_is_this_year_so_far_newest_first(world: World) -> None:
    answer = ask(world, "avery", "transactions")

    assert answer["from"] == "2026-01-01"
    assert answer["to"] == household.NOW.date().isoformat()
    days = [e["date"] for e in answer["entries"]]
    assert days == sorted(days, reverse=True)
    # Avery's own and the joint account's rows, and the move into the joint savings.
    assert sorted(payees(answer)) == sorted(
        [
            "Payee Avocet Market",
            "Payee Avocet September",
            "Payee Umber Stall",
            "Payee Aardvark Payroll",
            "Payee Jacana Hall",
            "Payee Juniper Joint",
            "Payee Saki Sweep",
        ]
    )
    assert answer["total"] == answer["counts"]["everything"] == len(answer["entries"])


def test_an_opening_balance_is_never_an_entry(world: World) -> None:
    assert "Opening" not in payees(ask(world, "avery", "transactions"))


def test_each_entry_keeps_its_cents_and_its_sign(world: World) -> None:
    entries = ask(world, "avery", "transactions")["entries"]

    market = next(e for e in entries if e["payee"] == "Payee Avocet Market")
    assert market["amount"] == "-1311.07"
    assert market["narration"] == "Memo Avocet note"
    assert market["category"] == {"name": "Expenses:Avocados", "label": "Avocados"}
    assert [s["amount"] for s in market["sides"]] == ["-1311.07", "1311.07"]
    assert market["sides"][0]["account"]["name"] == "Quokka Savings"
    payroll = next(e for e in entries if e["payee"] == "Payee Aardvark Payroll")
    assert payroll["amount"] == "3113.58"


def test_a_move_between_a_members_own_accounts_is_one_transfer(world: World) -> None:
    entries = ask(world, "avery", "transactions")["entries"]

    sweeps = [e for e in entries if e["payee"] == "Payee Saki Sweep"]
    assert len(sweeps) == 1
    sweep = sweeps[0]
    assert sweep["kind"] == "transfer"
    assert [a["name"] for a in sweep["accounts"]] == ["Quokka Savings", "Tamarin Savings"]
    assert sweep["amount"] == "777.31"
    assert sweep["category"] is None


def test_the_other_owner_of_an_unseen_account_reads_only_which_way_money_moved(
    world: World,
) -> None:
    entries = ask(world, "blake", "transactions")["entries"]

    tamarin = [{"id": world.account_ids["joint_named"], "name": "Tamarin Savings"}]
    moved = [e for e in entries if e["accounts"] == tamarin]
    assert [(e["payee"], e["amount"]) for e in moved] == [(MOVED_IN, "777.31")]
    assert moved[0]["sides"][1] == {
        "amount": "-777.31",
        "account": None,
        "category": None,
        "waiting": False,
        "elsewhere": True,
    }


def test_an_unreviewed_row_says_what_it_is_waiting_for(world: World) -> None:
    answer = ask(world, "casey", "transactions", show="unreviewed")

    assert answer["counts"]["unreviewed"] == answer["total"] == 2
    shrike = next(e for e in answer["entries"] if e["payee"] == "Payee Shrike Pending")
    assert shrike["unreviewed"] is True
    assert shrike["suggested"] is False
    assert shrike["waiting"] == "category"
    assert shrike["amount"] == "-23.45"


def test_the_journal_narrows_to_an_account(world: World) -> None:
    answer = ask(world, "avery", "transactions", account=world.account_ids["joint"])

    assert sorted(payees(answer)) == ["Payee Jacana Hall", "Payee Juniper Joint"]


def test_the_journal_narrows_to_a_category_and_the_categories_beneath_it(world: World) -> None:
    assert payees(ask(world, "avery", "transactions", category="Expenses:Avocados")) == [
        "Payee Avocet Market",
        "Payee Avocet September",
    ]
    branch = payees(ask(world, "avery", "transactions", category="Expenses"))
    assert "Payee Aardvark Payroll" not in branch
    assert "Payee Umber Stall" in branch


def test_the_journal_finds_words_in_a_payee_or_a_description_in_any_case(world: World) -> None:
    assert payees(ask(world, "avery", "transactions", text="avocet")) == [
        "Payee Avocet Market",
        "Payee Avocet September",
    ]
    assert payees(ask(world, "avery", "transactions", text="MEMO AVOCET")) == [
        "Payee Avocet Market"
    ]


def test_the_journal_narrows_to_a_period(world: World) -> None:
    answer = ask(world, "avery", "transactions", **{"from": "2026-09-01", "to": "2026-09-30"})

    assert answer["from"] == "2026-09-01"
    assert {e["date"] for e in answer["entries"]} == {household.SEP.isoformat()}


def test_transfers_are_a_chip_of_their_own(world: World) -> None:
    answer = ask(world, "avery", "transactions", show="transfers")

    assert payees(answer) == ["Payee Saki Sweep"]
    assert answer["counts"]["transfers"] == 1


def test_balance_checks_sit_in_line_on_the_day_they_cover(kept: Doorway) -> None:
    answer = kept.answer("transactions")
    entries: list[Any] = answer["entries"]  # type: ignore[assignment]

    checks = [e for e in entries if e["kind"] == "balance"]
    assert {e["accounts"][0]["name"] for e in checks} == {
        "Everyday checking",
        "Savings",
        "Rewards card",
    }
    assert all(e["checked"] == "bank" for e in checks)
    assert entries[0]["kind"] == "balance"
    assert "Cobble Street Market" in [e["payee"] for e in entries]


def test_the_journal_is_answered_a_window_at_a_time(world: World) -> None:
    whole = ask(world, "avery", "transactions")["entries"]

    pages = [ask(world, "avery", "transactions", offset=n, limit=2) for n in range(0, 8, 2)]

    assert [e for page in pages for e in page["entries"]] == whole
    assert all(page["total"] == len(whole) for page in pages)
    assert [e["position"] for e in whole] == list(range(len(whole)))
    assert ask(world, "avery", "transactions", offset=50)["entries"] == []


def test_a_window_is_never_wider_than_the_most_one_ask_is_answered_with() -> None:
    assert PAGE <= MOST <= 200


@pytest.mark.parametrize(
    "given",
    [
        {"from": "2026-13-01"},
        {"from": "15/10/2026"},
        {"from": "2026-10-15", "to": "2026-10-01"},
        {"to": 20261015},
        {"account": 7},
        {"account": ""},
        {"category": "Assets:Zq:AverySolo"},
        {"category": "Expenses:"},
        {"category": ["Expenses"]},
        {"text": ""},
        {"text": "x" * 201},
        {"show": "everything else"},
        {"offset": -1},
        {"offset": True},
        {"offset": "2"},
        {"limit": 0},
        {"limit": 201},
        {"limit": 2.5},
        {"page": 2},
    ],
)
def test_a_journal_asked_for_in_another_shape_is_missing(world: World, given: object) -> None:
    door = Doorway(world.settings, world.bookkeeper, "avery")

    assert door.answer("transactions", given) == {"state": "missing"}  # type: ignore[arg-type]


@pytest.mark.parametrize("key", ["casey_checking", "blake_solo", "unowned", "casey_named"])
def test_a_journal_narrowed_to_an_account_the_viewer_may_not_see_into_is_missing(
    world: World, key: str
) -> None:
    assert ask(world, "avery", "transactions", account=world.account_ids[key]) == {
        "state": "missing"
    }
    assert ask(world, "avery", "transactions", account="no-such-account") == {"state": "missing"}


# One shape, checked from both sides


@pytest.mark.parametrize("screen", ["accounts", "account", "transactions"])
def test_each_screen_has_the_shape_the_app_decodes(kept: Doorway, screen: str) -> None:
    """Each example in ``fixtures`` is what the app's own tests decode (``apple/HearthKit``)."""
    example = json.loads((FIXTURES / f"{screen}.json").read_text())
    given: dict[str, object] = {}
    if screen == "account":
        given["id"] = listed(kept.answer("accounts"))["Everyday checking"]["id"]

    assert differences(kept.answer(screen, given), example["answer"]) == []
