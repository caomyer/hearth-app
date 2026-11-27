"""The statement screens, from disclosure's views, with figures worked out by hand here from
``tests/household.py``, never by hearth.

What each viewer may be told is checked against the contract in ``tests/household.py`` by the
leak crawl in ``test_doorway.py``; the last tests here show that nothing a statement says, on
any tab, for any period and at cost or at market value, depends on a part it leaves out.
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from hearth import accounts, books, db, demo, figures, members
from hearth.config import Settings
from hearth.disclosure import Disclosure, Worth, may_learn, rulings_in
from hearth.months import Month
from hearth.native.doorway import Doorway
from hearth.plaid import feed
from hearth.reports import statements as statements_engine
from hearth.sharing import DEFAULT_SHARED, Facet
from hearth.valuation.pricestore import PriceStore
from hearth.valuation.pricetable import WIDE
from hearth.valuation.quotes import Quote
from tests import household
from tests.conftest import FakeClock, World
from tests.native.test_doorway import differences
from tests.native.test_screens import kept  # noqa: F401 - a fixture
from tests.test_positions import connected

FIXTURES = Path(__file__).parent / "fixtures"
EVERYTHING = frozenset(Facet)
NOTHING: frozenset[Facet] = frozenset()
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
        # ZQAA had no price until Sep 30: until then it was not valued, then it was, at what
        # it cost.
        "taken": 0,
        "unpriced": 0,
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
        "is_floor": False,
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


def book(world: World, text: str) -> None:
    """Entries added to the household's ledger, and the books recomputed over them."""
    with world.ledger.open("a") as ledger:
        ledger.write(text)
    world.recompute()
    assert world.bookkeeper.status.failure is None, world.bookkeeper.status.failure


SPLIT = """
2026-10-09 * "Payee Split Shop" ""
  Assets:Zq:AverySolo  -100.00 USD
  Expenses:Avocados  60.00 USD
  Expenses:Umbrellas  40.00 USD
"""


def test_a_split_entry_opens_as_the_part_of_it_the_line_adds_up(world: World) -> None:
    """An entry split across two categories is in each of their lines by its own part, and
    the journal a line opens onto gives each entry's part, which add up to the line."""
    book(world, SPLIT)
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

    split = next(e for e in rows["entries"] if e["payee"] == "Payee Split Shop")
    assert (D(split["amount"]), D(split["part"])) == (D("-100.00"), D("-60.00"))
    assert sum(-D(e["part"]) for e in rows["entries"]) == D(avocados["now"])
    assert D(avocados["now"]) == D("1311.07") + D("88.83") + D("60.00")


WITHHELD = """
2026-10-10 open Income:Salary
2026-10-10 open Expenses:Taxes
2026-10-10 * "Payee Employer" ""
  Assets:Zq:AverySolo  700.00 USD
  Income:Salary  -1000.00 USD
  Expenses:Taxes  300.00 USD
"""


def test_take_home_pay_is_pay_as_the_books_hold_it(world: World) -> None:
    """Pay booked before what was withheld from it, beside it in the same entry: take-home pay
    is pay as booked, 1,000, and the 300 withheld is tax, so saved from work is what reached
    the bank less what was spent."""
    book(world, WITHHELD)
    summary = ask(world, "avery", "statement_summary")

    assert D(line(summary, "pay")["now"]) == D("1000.00")
    assert D(line(summary, "tax")["now"]) == D("300.00")
    assert D(line(summary, "saved")["now"]) == D("700.00") - AVERY_SPENT


def test_spending_on_the_dashboard_is_spending_and_tax_here(world: World) -> None:
    """For the same parts and month, what the dashboard counts as spending (every expense but
    what is not spending) is the statement's spending and tax together."""
    book(world, WITHHELD)
    books_now = world.bookkeeper.status.books
    assert books_now is not None
    conn = world.connect()
    try:
        everyone = accounts.all_accounts(conn)
    finally:
        conn.close()
    owners = figures.ownership(everyone)
    october = figures.month_flows(books_now, owners, Month(2026, 10))
    parts = [
        frozenset({world.member_ids["avery"]}),
        frozenset(world.member_ids[u] for u in ("avery", "blake")),
    ]
    dashboard = sum((october.spent(p) for p in parts), D(0))

    summary = ask(world, "avery", "statement_summary", **{"from": "2026-10-01", "to": "2026-10-31"})

    statement = D(summary["spending"]["now"]) + D(line(summary, "tax")["now"])
    assert dashboard == statement == D("1311.07") + D("1640.84") + D("300.00")


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


EXTREME = ["0001-01-01", "0002-02-29", "0002-03-01", "1899-12-31", "1900-01-01", "2025-12-31",
           "2026-01-01", "2026-10-15", "2026-10-16", "9999-12-31"]  # fmt: skip
"""Days from the first a date can be to the last, around the floor and the books' own day."""


def test_no_statement_ask_raises_whatever_days_it_is_given(world: World) -> None:
    """Every pair of extreme days, either way round, on every statement: each is answered,
    ready or missing, and one that starts before the year the books begin in is missing."""
    door = Doorway(world.settings, world.bookkeeper, "avery")
    for first in EXTREME:
        for last in EXTREME:
            for screen, more in [
                ("statement_summary", {"valuation": "cost"}),
                ("balance_sheet", {}),
                ("income_statement", {"columns": "year"}),
            ]:
                answer = door.answer(screen, {"from": first, "to": last, **more})
                assert answer["state"] in ("ready", "missing"), (screen, first, last)
                if first < "2026-01-01" or first > last or first > "2026-10-15":
                    assert answer["state"] == "missing", (screen, first, last)
        for screen in ("statement_summary", "balance_sheet", "income_statement"):
            assert door.answer(screen, {"from": first})["state"] in ("ready", "missing")
            assert door.answer(screen, {"to": first})["state"] in ("ready", "missing")


# Nothing of a part left out, by subtraction or otherwise


def every_statement(world: World, viewer: str) -> list[dict[str, Any]]:
    """Every statement screen, across periods, columns and both valuations."""
    periods: list[dict[str, object]] = [
        {},
        {"from": "2026-08-01", "to": "2026-08-31"},
        {"from": "2026-09-01", "to": "2026-10-15"},
        {"from": "2026-10-03", "to": "2026-10-03"},
        {"from": "2026-07-01", "to": "2026-09-30"},
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


@pytest.mark.parametrize("facet", list(Facet))
def test_a_part_shared_in_all_but_one_facet_is_named_and_changes_nothing(
    world: World, facet: Facet
) -> None:
    """Blake, and with Avery the joint account, share every facet but one: neither is in
    Casey's statements, both are named, and every statement Casey can ask for is what it is
    when they share nothing at all. Whichever facet is held back."""
    world.choose("avery", NOTHING)
    world.choose("blake", NOTHING)
    alone = every_statement(world, "casey")
    world.choose("avery", EVERYTHING - {facet})
    world.choose("blake", EVERYTHING - {facet})

    shared = every_statement(world, "casey")

    assert shared[0]["scope"]["parts"] == ["Casey"]
    assert shared[0]["scope"]["without"] == ["Avery", "Blake", "Joint accounts"]
    assert shared == alone
    # Shared in full, they are in.
    world.choose("avery", EVERYTHING)
    world.choose("blake", EVERYTHING)
    assert every_statement(world, "casey")[0]["scope"]["without"] == []


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


def sample(
    folder: Path, sources: tuple[str, ...] = (demo.PRICE_SOURCE,)
) -> tuple[Settings, books.Bookkeeper]:
    """The sample household the app opens, valued at the prices from `sources`: its own,
    unless asked for none."""
    now = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    ledger = folder / "household.beancount"
    settings = Settings.in_directory(folder, ledger=ledger)
    db.initialise(settings.database)
    conn = db.connect(settings.database)
    try:
        demo.seed(conn, ledger, now=now)
    finally:
        conn.close()
    feed.initialise(settings.feed_database)
    demo.connect(settings, demo.plaid(lambda: now.astimezone().date(), ledger), now=now)
    keeper = books.Bookkeeper(
        books.from_household(settings, rulings=rulings_in, learns=may_learn, sources=sources),
        lambda: now,
    )
    keeper.recompute(wait=True)
    assert keeper.status.failure is None, keeper.status.failure
    return settings, keeper


SAMPLE_PERIODS: list[dict[str, object]] = [
    {},
    {"from": "2025-01-01", "to": "2025-12-31"},
    {"from": "2025-11-15", "to": "2026-02-10"},
    {"from": "2026-06-01", "to": "2026-06-30"},
]


def test_why_net_worth_changed_adds_up_on_the_sample_for_every_member(tmp_path: Path) -> None:
    """The sample household the app opens, at its own prices: two years of banks, cards,
    brokerages with lots at cost, moves between members, and lots bought for a cash amount not
    to the cent of units times cost, which the books' rounding line holds. Every member's
    change in net worth adds up to the cent, for a whole year, the year so far and stretches
    that cut months, at market value and at cost, with nothing left as a difference; and the
    market moved, so the market-value half of this proves something."""
    settings, keeper = sample(tmp_path)
    moved = []
    for viewer in ("avery", "blake"):
        door = Doorway(settings, keeper, viewer)
        for period in SAMPLE_PERIODS:
            for valuation in ("market", "cost"):
                why = door.answer("statement_summary", {**period, "valuation": valuation})["why"]
                assert isinstance(why, dict), (viewer, period)
                parts = [D(why["start"]), D(why["saved"]), D(why["investment_income"])]
                parts += [D(why["market"] or 0), *(D(o["amount"]) for o in why["others"])]
                assert sum(parts, D(0)).quantize(D("0.01")) == D(why["end"]).quantize(D("0.01"))
                assert D(why["difference"]) == 0, (viewer, period, valuation, why)
                if valuation == "market":
                    moved.append(D(why["market"]))
    assert any(abs(m) > 100 for m in moved), moved


@pytest.mark.parametrize("sources", [(demo.PRICE_SOURCE,), ()], ids=["priced", "no prices"])
def test_the_balance_sheet_says_the_net_worth_overview_does(
    tmp_path: Path, sources: tuple[str, ...]
) -> None:
    """For the same day and the same parts, the balance sheet's net worth at market value is
    Overview's to the cent: with the sample's prices, and with none, when every holding is
    named, left out, and both totals are floors."""
    settings, keeper = sample(tmp_path, sources)
    conn = db.connect(settings.database)
    try:
        for viewer in ("avery", "blake"):
            member = members.find(conn, viewer)
            assert member is not None
            view = Disclosure.load(conn, member, keeper.status)
            sheet = view.balance_sheet(None, None, market=True)
            parts = {p.name for p in sheet.scope.refs}
            overview = view.household()
            rows = [*overview.members, *overview.joint]
            worths = [r.net_worth for r in rows if r.part.name in parts]
            assert all(isinstance(w, Worth) for w in worths)
            known = sum((w.value for w in worths if isinstance(w, Worth)), D(0))
            floor = any(w.is_floor for w in worths if isinstance(w, Worth))
            assert sheet.net_worth.end is not None
            assert sheet.net_worth.end.market == known, (viewer, sources)
            assert sheet.net_worth.end.is_floor == floor
            assert floor == (sources == ())
    finally:
        conn.close()


def test_a_brokerage_known_only_from_inside_the_period_is_never_read_as_nothing_before(
    tmp_path: Path, clock: FakeClock
) -> None:
    """A brokerage connected today holds what it reported today: what it held on Jan 1 is not
    known. Net worth's start says nothing, no change is coloured, why it changed is without
    it and names it, its own start on the balance sheet is not known, and a period that ended
    before today has no net worth at all."""
    fresh = connected(tmp_path, clock)
    door = Doorway(fresh.settings, fresh.bookkeeper, "avery")

    def answer(screen: str, given: dict[str, object] | None = None) -> dict[str, Any]:
        return door.answer(screen, given or {})

    summary = answer("statement_summary")
    sheet = answer("balance_sheet")
    (invested,) = [g for g in sheet["assets"]["children"] if g["label"] == "Investments"]
    assert summary["late"] == [invested["children"][0]["label"]]
    assert summary["net_worth"]["before"] is None
    assert summary["net_worth"]["tone"] == "quiet"
    assert D(summary["net_worth"]["now"]) == D("12500.00")  # the brokerage's 10,000 in it
    # Why it changed is without the brokerage: the checking account's 2,500, whose 500 to
    # the brokerage is a move across the edge.
    why = summary["why"]
    assert D(why["end"]) == D("2500.00")
    assert D(why["difference"]) == 0
    assert invested["children"][0]["start"] is None
    assert D(invested["children"][0]["end"]["value"]) == D("10000.00")
    assert sheet["net_worth"]["start"] is None
    september = answer("statement_summary", {"from": "2026-09-01", "to": "2026-09-30"})
    assert september["net_worth"]["now"] is None
    assert september["net_worth"]["tone"] == "quiet"


def test_the_recompute_reads_the_ledger_once_and_every_statement_reads_that(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The books a recompute leaves hold the ledger read once; a statement asked afterwards
    reads that and never the history again."""
    found = world.bookkeeper.status.books
    assert found is not None
    assert found.ledger is not None

    def replayed(*_: object) -> None:
        raise AssertionError("a statement read the history again")

    monkeypatch.setattr(statements_engine.Ledger, "read", replayed)
    for screen in ("statement_summary", "income_statement", "balance_sheet", "year_by_year"):
        assert ask(world, "avery", screen)["state"] == "ready", screen


@pytest.mark.parametrize("price", ["1234.567890123456789012345", "1E+200", "0.1E-130"])
def test_a_price_no_table_can_pack_is_refused_at_entry_and_never_stops_a_recompute(
    world: World, price: str
) -> None:
    """Typed by hand, a price too long or too large is refused with a plain word; one that is
    stored all the same, as an older hearth may have kept it, is held whole, and the books are
    still worked out."""
    avery = world.signed_in("avery")
    form = {"csrf": world.csrf(avery), "symbol": "ZQAA", "day": "2026-10-14", "price": price}

    refused = avery.post("/net-worth/prices", data=form)

    assert refused.headers["location"] == "/net-worth?outcome=price-out-of-range"
    page = avery.get(refused.headers["location"]).text
    assert "between 0.000000000001 and 1,000,000,000" in page
    conn = world.connect()
    try:
        PriceStore(conn).record(
            Quote("ZQAA", date(2026, 10, 14), Decimal(price), "USD", "hand", "older", world.clock())
        )
    finally:
        conn.close()
    world.recompute()
    status = world.bookkeeper.status
    assert status.failure is None, status.failure
    assert status.books is not None
    assert status.books.prices("ZQAA", date(2026, 10, 15)).price == Decimal(price)  # type: ignore[union-attr]
    assert ask(world, "avery", "balance_sheet")["state"] == "ready"


@pytest.mark.parametrize("price", ["0.00000002", "0.000000000001", "0.000000000001234567890123"])
def test_a_token_priced_at_a_tiny_fraction_of_a_cent_is_kept_when_typed_by_hand(
    world: World, price: str
) -> None:
    """A token can trade at 0.00000002 or less: typed by hand, it is kept as typed, and the
    books are worked out at it."""
    avery = world.signed_in("avery")
    form = {"csrf": world.csrf(avery), "symbol": "ZQAA", "day": "2026-10-14", "price": price}

    recorded = avery.post("/net-worth/prices", data=form)

    assert recorded.headers["location"] == "/net-worth?outcome=price-recorded"
    world.recompute()
    status = world.bookkeeper.status
    assert status.failure is None, status.failure
    assert status.books is not None
    assert status.books.prices("ZQAA", date(2026, 10, 15)).price == Decimal(price)  # type: ignore[union-attr]
    assert ask(world, "avery", "balance_sheet")["state"] == "ready"


@pytest.mark.parametrize(
    ("price", "as_kept"),
    [
        ("1.000000000000000000000000000000", "1"),
        ("0.000000020000000000000", "2E-8"),
        ("1E+2", "100"),
        ("101.2500", "101.25"),
    ],
)
def test_a_price_typed_by_hand_is_kept_as_it_was_checked(
    world: World, price: str, as_kept: str
) -> None:
    """Zeros that say nothing are dropped before a price is checked for its digits, and the
    price kept is the one checked: never stored longer, so it packs in the price table."""
    avery = world.signed_in("avery")
    form = {"csrf": world.csrf(avery), "symbol": "ZQAA", "day": "2026-10-14", "price": price}

    recorded = avery.post("/net-worth/prices", data=form)

    assert recorded.headers["location"] == "/net-worth?outcome=price-recorded"
    conn = world.connect()
    try:
        stored = PriceStore(conn).latest("ZQAA", date(2026, 10, 14), "hand")
    finally:
        conn.close()
    assert stored is not None
    assert str(stored.price) == as_kept
    world.recompute()
    status = world.bookkeeper.status
    assert status.books is not None
    series = status.books.prices._series["ZQAA"]
    i = list(series.days).index(date(2026, 10, 14).toordinal())
    assert series.exponents[i] != WIDE


def test_whether_a_statement_is_there_says_nothing_of_anyone_elses_history(world: World) -> None:
    """An account nobody in Avery's statements owns, whose history begins in 2020: every
    statement Avery can ask for, over every year back to then, is there or missing exactly as
    before it was in the books."""
    years = [{"from": f"{y}-01-01", "to": f"{y}-12-31"} for y in range(2019, 2027)]

    def states(viewer: str) -> list[str]:
        return [
            ask(world, viewer, screen, **period)["state"]
            for period in years
            for screen in ("statement_summary", "income_statement", "balance_sheet")
        ]

    before = {viewer: states(viewer) for viewer in household.USERNAMES}
    book(
        world,
        "\n2020-01-01 open Assets:Zq:Elsewhere2020\n2020-01-01 open Equity:Elsewhere\n"
        '2020-01-02 * "Opening"\n  Assets:Zq:Elsewhere2020  100.00 USD\n  Equity:Elsewhere\n',
    )
    assert {viewer: states(viewer) for viewer in household.USERNAMES} == before


FOREIGN_GIFT = """
2026-08-01 open Assets:Zq:Abroad
2026-10-06 * "Payee Two Currencies" ""
  Assets:Zq:AverySolo  -50.00 USD
  Assets:Zq:Abroad  -10 EUR
  Expenses:Avocados  50.00 USD
  Expenses:Avocados  10 EUR
"""


def journal_sum(
    world: World, viewer: str, line_: dict[str, Any], period: dict[str, Any]
) -> Decimal:
    """What the journal a line opens onto adds up to, every page of it."""
    found, offset = Decimal(0), 0
    while True:
        page = ask(
            world,
            viewer,
            "transactions",
            within="statements",
            categories=line_["categories"],
            offset=offset,
            limit=200,
            **period,
        )
        found += sum((Decimal(e["part"]) for e in page["entries"]), Decimal(0))
        offset += len(page["entries"])
        if offset >= page["total"] or not page["entries"]:
            return found


def test_every_line_is_what_the_rows_it_opens_onto_add_up_to(world: World) -> None:
    """For every viewer and every line of the summary that adds up rows, the journal it opens
    onto adds up to it, with an entry in two currencies that crosses the statement's edge
    left out of both."""
    book(world, FOREIGN_GIFT)
    for viewer in household.USERNAMES:
        summary = ask(world, viewer, "statement_summary")
        for found in summary["lines"]:
            if not found["categories"]:
                continue
            rows = journal_sum(world, viewer, found, summary["period"])
            income = found["key"] in ("pay", "investment_income", "other_income")
            assert (rows if income else -rows) == D(found["now"]), (viewer, found["key"])
    avocados = line(ask(world, "avery", "statement_summary"), "spending:Expenses:Avocados")
    assert D(avocados["now"]) == D("1311.07") + D("88.83")


def test_every_line_of_the_sample_is_what_its_rows_add_up_to(tmp_path: Path) -> None:
    settings, keeper = sample(tmp_path)
    for viewer in ("avery", "blake"):
        door = Doorway(settings, keeper, viewer)
        summary: dict[str, Any] = door.answer("statement_summary")
        for found in summary["lines"]:
            if not found["categories"]:
                continue
            total, offset = Decimal(0), 0
            while True:
                page: dict[str, Any] = door.answer(
                    "transactions",
                    {
                        "within": "statements",
                        "categories": found["categories"],
                        "offset": offset,
                        "limit": 200,
                        **summary["period"],
                    },
                )
                total += sum((Decimal(e["part"]) for e in page["entries"]), Decimal(0))
                offset += len(page["entries"])
                if offset >= page["total"] or not page["entries"]:
                    break
            income = found["key"] in ("pay", "investment_income", "other_income")
            assert (total if income else -total) == D(found["now"]), (viewer, found["key"])
