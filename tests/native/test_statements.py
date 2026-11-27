"""The statement screens, from disclosure's views, with figures worked out by hand here from
``tests/household.py``, never by hearth.

What each viewer may be told is checked against the contract in ``tests/household.py`` by the
leak crawl in ``test_doorway.py``; the last tests here show that nothing a statement says, on
any tab, for any period and at cost or at market value, depends on a part it leaves out.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from hearth import books, db, demo
from hearth.config import Settings
from hearth.disclosure import may_learn, rulings_in
from hearth.native.doorway import Doorway
from hearth.plaid import feed
from hearth.sharing import DEFAULT_SHARED, Facet
from tests import household
from tests.conftest import World
from tests.native.test_doorway import differences
from tests.native.test_screens import kept  # noqa: F401 - a fixture

FIXTURES = Path(__file__).parent / "fixtures"
EVERYTHING = frozenset(Facet)
D = Decimal


def ask(world: World, viewer: str, screen: str, **given: object) -> dict[str, Any]:
    return Doorway(world.settings, world.bookkeeper, viewer).answer(screen, given)


def others(why: dict[str, Any]) -> dict[str, Decimal]:
    return {o["key"]: D(o["amount"]) for o in why["others"]}


def line(answer: dict[str, Any], key: str) -> dict[str, Any]:
    return next(found for found in answer["lines"] if found["key"] == key)


# Avery's own accounts and the joint one, this year so far. Every account opened on Aug 1, so
# what each held before its history is an opening balance: Avery's 52,341.17 less the 917.31
# its rows added, the joint 20,000.42 less its -2,109.19, and ten ZQAA at their cost.
AVERY_OPENING = D("51423.86") + D("22109.61") + D("12345.60")
AVERY_SPENT = D("1311.07") + D("88.83") + D("19.06") + D("1640.84") + D("468.35")
AVERY_NET_WORTH = D("52341.17") + D("12345.60") + D("20000.42")


def test_the_summary_reads_the_viewers_own_and_joint_accounts(world: World) -> None:
    answer = ask(world, "avery", "statement_summary")

    assert answer["state"] == "ready"
    assert answer["period"] == {"from": "2026-01-01", "to": household.TODAY.isoformat()}
    assert answer["against"] == {"from": "2025-01-01", "to": "2025-10-15"}
    assert answer["scope"] == {
        "parts": ["Avery", "Joint accounts"],
        "without": ["Blake", "Casey"],
        "leaves_out": "1 of the joint accounts hearth does not see",
        "awaiting": 0,
    }
    assert D(answer["net_worth"]["now"]) == AVERY_NET_WORTH
    assert D(answer["spending"]["now"]) == AVERY_SPENT
    # Nothing is pay by its name, so there is no rate to be had; Aardwork is other income.
    assert answer["savings_rate"]["now"] is None
    assert D(line(answer, "other_income")["now"]) == D("3113.58")
    assert D(line(answer, "saved")["now"]) == -AVERY_SPENT
    # The books begin on Aug 1, so neither period is held in full: nothing a year before is a
    # figure, and no change is coloured.
    assert answer["covered"] is False
    assert answer["against_covered"] is False
    assert all(found["before"] is None for found in answer["lines"])
    assert {found["tone"] for found in answer["lines"]} == {"quiet"}


def test_why_net_worth_changed_adds_up_to_the_balance_sheet(world: World) -> None:
    why = ask(world, "avery", "statement_summary")["why"]
    sheet = ask(world, "avery", "balance_sheet")

    assert D(why["start"]) == 0
    assert D(why["saved"]) == -AVERY_SPENT
    assert D(why["investment_income"]) == 0
    assert D(why["market"]) == 0  # ZQAA is priced at what it cost
    assert others(why) == {
        "other_income": D("3113.58"),
        "not_spending": 0,
        "opening": AVERY_OPENING,
        "unplaced": 0,
        # Avery said the 777.31 went to the joint savings hearth does not see.
        "outside": D("-777.31"),
        "realised": 0,
        "rounding": 0,
    }
    assert D(why["end"]) == AVERY_NET_WORTH == D(sheet["net_worth"]["end"]["value"])
    assert D(why["difference"]) == 0


def test_a_part_seen_in_full_is_in_and_one_seen_only_in_part_is_named(world: World) -> None:
    world.choose("blake", EVERYTHING)
    world.choose("casey", EVERYTHING - {Facet.TRANSACTIONS})

    answer = ask(world, "avery", "statement_summary")

    assert answer["scope"]["parts"] == ["Avery", "Blake", "Joint accounts"]
    assert answer["scope"]["without"] == ["Casey"]
    blake = D(household.BALANCE["blake_solo"].replace(",", ""))
    assert D(answer["net_worth"]["now"]) == AVERY_NET_WORTH + blake


def test_caseys_statement_with_rows_waiting(world: World) -> None:
    answer = ask(world, "casey", "statement_summary")
    why = answer["why"]

    assert answer["scope"]["parts"] == ["Casey"]
    assert answer["scope"]["leaves_out"] == "1 account hearth does not see"
    assert D(answer["spending"]["now"]) == D("1045.19") + D("12.34") + D("357.24")
    # The shrike and the jerboa rows wait, placed nowhere yet: 23.45 and 55.55 out.
    assert answer["unreviewed"] == {
        "rows": 2,
        "income": "0",
        "expenses": "0",
        "unplaced": 2,
        "unplaced_out": "79.00",
        "unplaced_in": "0",
    }
    # The checking account held 5,211.45 before its history and the card owed 297.91.
    assert others(why)["opening"] == D("5211.45") - D("297.91")
    assert others(why)["unplaced"] == D("-79.00")
    assert others(why)["outside"] == D("-333.17")  # to the loan hearth does not see
    assert D(why["end"]) == D(household.NET_WORTH["casey"].replace(",", ""))
    assert D(why["difference"]) == 0


def test_the_income_statement_is_the_account_tree_by_month(world: World) -> None:
    answer = ask(world, "avery", "income_statement", **{"from": "2026-08-01", "to": "2026-10-15"})

    assert [(c["from"], c["to"], c["partial"]) for c in answer["columns"]] == [
        ("2026-08-01", "2026-08-31", False),
        ("2026-09-01", "2026-09-30", False),
        ("2026-10-01", "2026-10-15", True),
    ]
    expenses = answer["expenses"]
    assert [D(c) for c in expenses["cells"]] == [
        0,
        D("88.83") + D("19.06") + D("468.35"),
        D("1311.07") + D("1640.84"),
    ]
    assert D(expenses["total"]) == AVERY_SPENT
    # Largest first.
    assert [c["label"] for c in expenses["children"]] == [
        "Jointery",
        "Avocados",
        "Junipers",
        "Umbrellas",
    ]
    assert answer["income"]["children"][0]["name"] == "Income:Aardwork"
    assert answer["income"]["children"][0]["kind"] == "other-income"
    assert D(answer["net"]["total"]) == D("3113.58") - AVERY_SPENT


def test_the_income_statement_splits_by_quarter_or_year(world: World) -> None:
    quarters = ask(world, "casey", "income_statement", columns="quarter")
    years = ask(world, "casey", "income_statement", columns="year")

    assert [c["from"] for c in quarters["columns"]] == [
        "2026-01-01",
        "2026-04-01",
        "2026-07-01",
        "2026-10-01",
    ]
    assert quarters["columns"][-1]["unreviewed"]["rows"] == 2
    assert len(years["columns"]) == 1
    assert years["expenses"]["total"] == quarters["expenses"]["total"]


def test_the_balance_sheet_groups_accounts_start_beside_end(world: World) -> None:
    answer = ask(world, "avery", "balance_sheet")

    assets = answer["assets"]
    assert [g["label"] for g in assets["children"]] == ["Cash", "Investments"]
    cash, invested = assets["children"]
    assert [a["label"] for a in cash["children"]] == ["Narwhal Joint", "Quokka Savings"]
    shares = invested["children"][0]
    assert shares["account"] == {"id": world.account_ids["avery_invest"], "name": "Wombat Shares"}
    assert shares["end"] == {
        "value": "12345.60",
        "cost": "12345.60",
        "gain": "0.00",
        "securities": True,
    }
    # Every account's history begins inside the period, so it says from when.
    assert {a["since"] for g in assets["children"] for a in g["children"]} == {"2026-08-01"}
    assert D(answer["net_worth"]["start"]["value"]) == 0
    assert D(answer["net_worth"]["change"]) == AVERY_NET_WORTH
    assert answer["liabilities"]["children"] == []


def test_the_balance_sheet_says_what_holdings_cost_as_holdings_does(world: World) -> None:
    """Cost and gain come from the same lots Holdings reads, so the two never disagree."""
    sheet = ask(world, "avery", "balance_sheet")
    holdings = ask(world, "avery", "holdings")

    (invested,) = [g for g in sheet["assets"]["children"] if g["label"] == "Investments"]
    for row in invested["children"]:
        listed = next(a for a in holdings["accounts"] if a["id"] == row["account"]["id"])
        costs = [D(h["cost"]) for h in listed["held"] if h["cost"] is not None]
        assert D(row["end"]["cost"]) == sum(costs, D(0))
        gains = [h["gain"] for h in listed["held"] if not h["is_cash"]]
        if None not in gains:
            assert D(row["end"]["gain"]) == sum((D(g) for g in gains), D(0))


def test_casey_owes_on_the_card(world: World) -> None:
    answer = ask(world, "casey", "balance_sheet", valuation="cost")

    (credit,) = answer["liabilities"]["children"]
    assert credit["label"] == "Credit"
    assert credit["children"][0]["end"]["value"] == "-310.25"


def test_year_by_year_marks_a_year_with_too_little_history(world: World) -> None:
    answer = ask(world, "avery", "year_by_year")

    (year,) = answer["years"]
    assert year["year"] == 2026
    assert year["partial"] is True
    # Only August, September and October of its ten months are in the books.
    assert year["sparse"] is True
    assert D(year["net_worth"]) == AVERY_NET_WORTH
    assert D(year["spending"]) == AVERY_SPENT


def test_a_figure_opens_onto_exactly_the_rows_it_adds_up(world: World) -> None:
    summary = ask(world, "avery", "statement_summary")
    avocados = line(summary, "spending:Expenses:Avocados")

    rows = ask(
        world,
        "avery",
        "transactions",
        within="statements",
        categories=avocados["categories"],
        **summary["period"],
    )

    assert [e["payee"] for e in rows["entries"]] == [
        "Payee Avocet Market",
        "Payee Avocet September",
    ]
    assert sum(-D(e["amount"]) for e in rows["entries"]) == D(avocados["now"])
    # The statement reads no move to an account outside it, and nor does the journal of it.
    every = ask(world, "avery", "transactions", within="statements", limit=200)
    assert "Payee Saki Sweep" in [
        e["payee"] for e in ask(world, "avery", "transactions")["entries"]
    ]
    assert "Payee Saki Sweep" not in [e["payee"] for e in every["entries"]]


@pytest.mark.parametrize(
    ("screen", "given"),
    [
        ("statement_summary", {"valuation": "both"}),
        ("statement_summary", {"from": "2026-10-15", "to": "2026-10-01"}),
        ("statement_summary", {"from": "2026-02-30"}),
        ("statement_summary", {"from": "2027-01-01"}),
        ("statement_summary", {"columns": "month"}),
        ("balance_sheet", {"valuation": 1}),
        ("income_statement", {"columns": "week"}),
        ("income_statement", {"valuation": "cost"}),
        ("income_statement", {"from": "2020-01-01", "to": "2026-10-15"}),
        ("year_by_year", {"from": "2026-01-01"}),
        ("transactions", {"within": "everything"}),
        ("transactions", {"categories": "Expenses:Avocados"}),
        ("transactions", {"categories": []}),
        ("transactions", {"categories": ["Assets:Zq:AverySolo"]}),
    ],
)
def test_a_statement_asked_for_in_another_shape_is_missing(
    world: World, screen: str, given: dict[str, object]
) -> None:
    assert ask(world, "avery", screen, **given) == {"state": "missing"}


# Nothing of a part left out, by subtraction or otherwise


def every_statement(world: World, viewer: str) -> list[dict[str, Any]]:
    """Every statement screen, across periods, columns and both valuations."""
    periods: list[dict[str, object]] = [
        {},
        {"from": "2026-08-01", "to": "2026-08-31"},
        {"from": "2026-09-01", "to": "2026-10-15"},
        {"from": "2026-10-03", "to": "2026-10-03"},
        {"from": "2025-10-16", "to": "2026-10-15"},
    ]
    found = []
    for period in periods:
        for valuation in ("market", "cost"):
            found.append(ask(world, viewer, "statement_summary", **period, valuation=valuation))
            found.append(ask(world, viewer, "balance_sheet", **period, valuation=valuation))
        for by in ("month", "quarter", "year"):
            found.append(ask(world, viewer, "income_statement", **period, columns=by))
        found.append(ask(world, viewer, "transactions", **period, within="statements", limit=200))
    for valuation in ("market", "cost"):
        found.append(ask(world, viewer, "year_by_year", valuation=valuation))
    for answer in found:
        assert answer["state"] == "ready", answer
        answer.pop("computed_at")
    return found


def test_nothing_of_the_joint_line_can_be_had_by_subtraction(world: World) -> None:
    """Blake shares everything, Avery keeps the joint line private: whatever moves in the joint
    account or in Avery's own, and whatever its holdings are worth, nothing in any of Casey's
    statements, on any tab, for any period, at cost or at market value, changes. So no figure
    of them, and no difference of two, holds anything of the joint account or of Avery's."""
    world.choose("blake", EVERYTHING)
    world.choose("avery", DEFAULT_SHARED - {Facet.NET_WORTH_TOTAL, Facet.MONTHLY_SPENDING_TOTAL})
    before = every_statement(world, "casey")
    summary = before[0]
    assert summary["scope"]["parts"] == ["Blake", "Casey"]
    assert summary["scope"]["without"] == ["Avery", "Joint accounts"]
    blake = D(household.BALANCE["blake_solo"].replace(",", ""))
    assert D(summary["net_worth"]["now"]) == blake + D(
        household.NET_WORTH["casey"].replace(",", "")
    )
    avery = ask(world, "avery", "statement_summary")["net_worth"]["now"]

    with world.ledger.open("a") as ledger:
        ledger.write(
            '\n2026-10-04 * "Payee Hidden Joint" ""\n'
            "  Assets:Zq:Joint  -999.11 USD\n"
            "  Expenses:Jointery\n"
            '\n2026-09-04 * "Payee Hidden Pay" ""\n'
            "  Assets:Zq:AverySolo  4444.44 USD\n"
            "  Income:Aardwork\n"
            "\n2026-10-05 price ZQAA 2000.00 USD\n"
        )
    world.recompute()
    assert world.bookkeeper.status.failure is None
    # What changed is real: Avery's own statement reads it.
    assert ask(world, "avery", "statement_summary")["net_worth"]["now"] != avery

    assert every_statement(world, "casey") == before


def test_the_fixtures_shapes_are_what_the_statement_screens_answer(kept: Doorway) -> None:  # noqa: F811
    """Each example in ``fixtures`` is what the app's own tests decode (``apple/HearthKit``)."""
    for screen in ("statement_summary", "income_statement", "balance_sheet", "year_by_year"):
        example = json.loads((FIXTURES / f"{screen}.json").read_text())
        assert differences(kept.answer(screen), example["answer"]) == [], screen


def test_why_net_worth_changed_adds_up_on_the_sample_for_every_member(tmp_path: Path) -> None:
    """The sample household the app opens: two years of banks, cards, brokerages with lots at
    cost and prices, moves between members, and lots bought for a cash amount not to the cent
    of units times cost, which the books' rounding line holds. Every member's change in net
    worth adds up to the cent, for a whole year, the year so far and stretches that cut months,
    at market value and at cost, with nothing left as a difference."""
    now = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    ledger = tmp_path / "household.beancount"
    settings = Settings.in_directory(tmp_path, ledger=ledger)
    db.initialise(settings.database)
    conn = db.connect(settings.database)
    try:
        demo.seed(conn, ledger, now=now)
    finally:
        conn.close()
    feed.initialise(settings.feed_database)
    demo.connect(settings, demo.plaid(lambda: now.astimezone().date(), ledger), now=now)
    keeper = books.Bookkeeper(
        books.from_household(settings, rulings=rulings_in, learns=may_learn), lambda: now
    )
    keeper.recompute(wait=True)
    assert keeper.status.failure is None, keeper.status.failure
    periods: list[dict[str, object]] = [
        {},
        {"from": "2025-01-01", "to": "2025-12-31"},
        {"from": "2025-11-15", "to": "2026-02-10"},
        {"from": "2026-06-01", "to": "2026-06-30"},
    ]
    for viewer in ("avery", "blake"):
        door = Doorway(settings, keeper, viewer)
        for period in periods:
            for valuation in ("market", "cost"):
                why = door.answer("statement_summary", {**period, "valuation": valuation})["why"]
                assert isinstance(why, dict), (viewer, period)
                parts = [D(why["start"]), D(why["saved"]), D(why["investment_income"])]
                parts += [D(why["market"] or 0), *(D(o["amount"]) for o in why["others"])]
                assert sum(parts, D(0)).quantize(D("0.01")) == D(why["end"]).quantize(D("0.01"))
                assert D(why["difference"]) == 0, (viewer, period, valuation, why)
