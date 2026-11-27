"""Statements over invented books, every figure worked out by hand below, never by hearth.

The ledger here has every shape a change in net worth can come from: a pad, money opened against
equity, a lot opened at a gain it had already made, a lot with no price ever, a sale of a lot
with its gain booked as income, a dividend, tax, a retirement contribution booked as an
expense, a gift, money and shares moved to accounts outside the statement, a row nobody has
placed yet, a carried account's reconciling entry, holdings booked as a brokerage reports them,
with no lot, and one of those sold. The change in net worth is proved to add up to the sheets
to the cent at cost and at market value, for every period between the days below; and a probe
for each way market movement could be told wrongly says what it is, line by line.
"""

from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import combinations
from pathlib import Path

import pytest
from beancount import loader
from beancount.core.data import Directive

from hearth import books, categories
from hearth.reports.statements import (
    MATERIAL,
    Better,
    Bridge,
    Columns,
    Flows,
    Held,
    Ledger,
    Other,
    Period,
    Tone,
    bridge,
    columns,
    tone,
    total,
)
from hearth.valuation.pricetable import PriceTable
from hearth.valuation.quotes import Quote
from tests import household, ledgers

USD = "USD"

LEDGER = """

2025-01-01 open Assets:Bank USD
2025-01-01 open Assets:Broker
2025-01-01 open Assets:Plaid
2025-01-01 open Assets:Carried USD
2025-01-01 open Assets:Theirs USD
2025-01-01 open Assets:TheirsBroker
2025-01-01 open Liabilities:Card USD
2025-01-01 open Equity:Opening-Balances
2025-01-01 open Equity:Ledger-Bank-Difference
2025-01-01 open Equity:Review:Unresolved
2025-01-01 open Income:Salary
2025-01-01 open Income:Dividends
2025-01-01 open Income:Gains
2025-01-01 open Income:Gifts
2025-01-01 open Expenses:Food
2025-01-01 open Expenses:Rent
2025-01-01 open Expenses:Taxes
2025-01-01 open Expenses:Retirement401k

2025-01-01 pad Assets:Bank Equity:Opening-Balances
2025-01-02 balance Assets:Bank 5000.00 USD

2025-01-05 * "Opening"
  Assets:Carried  1000.00 USD
  Equity:Opening-Balances

2025-01-10 * "Opening position"
  Assets:Broker  10 AAA {50.00 USD}
  Equity:Opening-Balances

2025-01-15 * "Employer" "Pay"
  Assets:Bank  3000.00 USD
  Income:Salary

2025-01-20 * "Grocer"
  Liabilities:Card  -200.00 USD
  Expenses:Food

2025-01-31 * "Landlord"
  Assets:Bank  -1200.00 USD
  Expenses:Rent

2025-02-01 * "Tax office"
  Assets:Bank  -300.00 USD
  Expenses:Taxes

2025-02-01 * "Plan"
  Assets:Bank  -250.00 USD
  Expenses:Retirement401k

2025-02-05 * "Buy"
  Assets:Broker  5 BBB {20.00 USD}
  Assets:Bank  -100.00 USD

2025-02-10 * "Fund" "Dividend"
  Assets:Bank  12.00 USD
  Income:Dividends

2025-02-15 * "Sell"
  Assets:Broker  -4 AAA {50.00 USD} @ 90.00 USD
  Assets:Bank  360.00 USD
  Income:Gains  -160.00 USD

2025-02-20 * "Holdings as reported"
  Assets:Plaid  3 CCC
  Equity:Opening-Balances  -3 CCC

2025-02-25 * "Gift"
  Assets:Bank  500.00 USD
  Income:Gifts

2025-03-01 * "To them"
  Assets:Bank  -400.00 USD
  Assets:Theirs  400.00 USD

2025-03-02 ! "Unknown"
  Assets:Bank  -75.00 USD
  Equity:Review:Unresolved

2025-03-05 * "Carried" "Reconciling"
  Assets:Carried  20.00 USD
  Equity:Ledger-Bank-Difference

2025-03-10 * "Sell some of what was reported"
  Assets:Plaid  -1 CCC @ 130.00 USD
  Assets:Bank  130.00 USD

2025-03-12 * "Shares to them"
  Assets:Broker  -2 AAA {50.00 USD}
  Assets:TheirsBroker  2 AAA {50.00 USD}

2025-03-15 * "Card payment"
  Liabilities:Card  200.00 USD
  Assets:Bank  -200.00 USD
"""


PRICES = {
    "AAA": [
        (date(2025, 1, 10), "80.00"),
        (date(2025, 2, 28), "90.00"),
        (date(2025, 3, 31), "100.00"),
    ],
    "CCC": [(date(2025, 2, 20), "100.00"), (date(2025, 3, 31), "120.00")],
}
"""BBB is never priced, so it is not valued at market value, and named."""

SCOPE = frozenset(
    {"Assets:Bank", "Assets:Broker", "Assets:Plaid", "Assets:Carried", "Liabilities:Card"}
)
"""Every account but the two that are theirs."""

FETCHED = datetime(2025, 1, 1, tzinfo=UTC)


def table(prices: dict[str, list[tuple[date, str]]]) -> PriceTable:
    return PriceTable.of(
        Quote(symbol, day, Decimal(price), USD, "test", symbol, FETCHED)
        for symbol, series in prices.items()
        for day, price in series
    )


def load(text: str) -> list[Directive]:
    loaded, errors, _ = loader.load_string('option "operating_currency" "USD"\n' + text)
    assert not errors, errors
    return loaded


@pytest.fixture(scope="module")
def entries() -> list[Directive]:
    return load(LEDGER)


def kind(account: str) -> categories.Kind:
    return categories.guess(account)


def read(
    entries: Iterable[Directive],
    period: Period,
    scope: frozenset[str] = SCOPE,
    prices: dict[str, list[tuple[date, str]]] = PRICES,
) -> tuple[Held, Held, Flows]:
    ledger = Ledger.read(entries, table(prices), USD)
    return (
        total(ledger.held(period.before, scope).values()),
        total(ledger.held(period.end, scope).values()),
        ledger.flows(period, scope, kind),
    )


def why(entries: Iterable[Directive], period: Period, *, market: bool, **more: object) -> Bridge:
    start, end, moved = read(entries, period, **more)  # type: ignore[arg-type]
    return bridge(start, end, moved, market=market)


FIRST_QUARTER = Period(date(2025, 1, 1), date(2025, 3, 31))


def test_the_sheets_value_each_holding_at_cost_and_at_its_latest_price(
    entries: list[Directive],
) -> None:
    ledger = Ledger.read(entries, table(PRICES), USD)
    held = ledger.held(date(2025, 3, 31), SCOPE)

    # Bank: 5,000 padded, +3,000 -1,200 -300 -250 -100 +12 +360 +500 -400 -75 +130 -200.
    assert held["Assets:Bank"] == Held(Decimal("6477.00"), Decimal("6477.00"))
    assert "Liabilities:Card" not in held  # paid off: it holds nothing
    # Four AAA at a cost of 50, worth 100; five BBB at 20, never priced: at cost, and not
    # valued at market value, as no screen values them.
    broker = held["Assets:Broker"]
    assert (broker.cost, broker.market) == (Decimal("300.00"), Decimal("400.00"))
    assert broker.unpriced == {"BBB"}
    assert broker.is_floor
    # Three CCC booked at 100 each, the day they were reported; one sold at its average cost.
    plaid = held["Assets:Plaid"]
    assert (plaid.cost, plaid.market) == (Decimal("200.00"), Decimal("240.00"))
    assert held["Assets:Carried"].cost == Decimal("1020.00")


def test_the_quarter_by_kind_of_money(entries: list[Directive]) -> None:
    _, _, moved = read(entries, FIRST_QUARTER)

    assert moved.pay == Decimal("3000.00")
    assert moved.tax == Decimal("300.00")
    assert moved.spending == Decimal("1400.00")
    assert moved.not_spending == Decimal("250.00")
    assert moved.investment_income == Decimal("172.00")
    assert moved.other_income == Decimal("500.00")
    assert moved.saved == Decimal("1300.00")
    assert moved.savings_rate == Decimal("1300") / Decimal("2700")
    unreviewed = moved.unreviewed
    assert (unreviewed.rows, unreviewed.unplaced, unreviewed.unplaced_out) == (
        1,
        1,
        Decimal("75.00"),
    )


def test_why_net_worth_changed_at_cost(entries: list[Directive]) -> None:
    found = why(entries, FIRST_QUARTER, market=False)

    assert (found.start, found.end) == (Decimal(0), Decimal("7997.00"))
    assert found.saved == Decimal("1300.00")
    assert found.investment_income == Decimal("172.00")
    assert found.movement is None
    assert found.others == {
        Other.OTHER_INCOME: Decimal("500.00"),
        Other.NOT_SPENDING: Decimal("-250.00"),
        # The pad's 5,000, the carried account's 1,000, ten AAA at their cost of 500, three
        # CCC as reported, at 300, and the carried account's reconciling 20.
        Other.OPENING: Decimal("6820.00"),
        Other.UNPLACED: Decimal("-75.00"),
        # 400 to their account, and two AAA at their cost of 100 to their brokerage.
        Other.OUTSIDE: Decimal("-500.00"),
        Other.TAKEN: Decimal(0),
        Other.UNPRICED: Decimal(0),
        # One CCC, which cost 100 on average, went for 130.
        Other.REALISED: Decimal("30.00"),
        # Every entry balances to the cent.
        Other.ROUNDING: Decimal(0),
    }
    assert found.difference == 0


def test_why_net_worth_changed_at_market_value(entries: list[Directive]) -> None:
    found = why(entries, FIRST_QUARTER, market=True)

    # 6,477 in the bank, four AAA at 100, two CCC at 120, 1,020 carried; BBB not valued.
    assert found.end == Decimal("8137.00")
    assert found.others == {
        Other.OTHER_INCOME: Decimal("500.00"),
        Other.NOT_SPENDING: Decimal("-250.00"),
        # Ten AAA came in worth 80 each, 300 over their cost: a gain they had already made,
        # which is the opening's, not the market's.
        Other.OPENING: Decimal("7120.00"),
        Other.UNPLACED: Decimal("-75.00"),
        # Two AAA left worth 90, 80 over their cost, which went with them.
        Other.OUTSIDE: Decimal("-580.00"),
        # Four AAA sold for a gain of 160 over their cost, 120 of which (at the day's 80) their
        # value already held: investment income says 160, and this takes 120 back out.
        Other.TAKEN: Decimal("-120.00"),
        # The 100 paid for BBB, which nothing prices.
        Other.UNPRICED: Decimal("-100.00"),
        Other.REALISED: Decimal(0),
        Other.ROUNDING: Decimal(0),
    }
    # What prices did: six AAA from 80 to 90 (+60), four from 90 to 100 (+40), two CCC from
    # 100 to 120 (+40), and one CCC sold for 30 over the day's 100.
    assert found.movement == Decimal("170.00")
    assert found.difference == 0


def test_a_period_that_starts_with_something_held(entries: list[Directive]) -> None:
    february = Period(date(2025, 2, 1), date(2025, 3, 31))
    start, end, moved = read(entries, february)

    # On Jan 31: 6,800 in the bank, 200 owed on the card, ten AAA at a cost of 500 and worth
    # 800 at 80, and 1,000 carried.
    assert (start.cost, start.market) == (Decimal("8100.00"), Decimal("8400.00"))
    at_cost = bridge(start, end, moved, market=False)
    assert at_cost.saved == Decimal("-300.00")  # tax, and nothing earned
    assert at_cost.other == Decimal("25.00")  # 500 - 250 + 320 - 75 - 500 + 30
    assert at_cost.difference == 0
    at_market = bridge(start, end, moved, market=True)
    assert at_market.movement == Decimal("170.00")
    assert at_market.difference == 0


@pytest.mark.parametrize("market", [True, False])
def test_the_change_adds_up_to_the_sheets_for_every_period(
    entries: list[Directive], market: bool
) -> None:
    days = [
        date(2024, 12, 31),
        *(date(2025, 1, d) for d in (1, 2, 5, 9, 10, 11, 20, 31)),
        *(date(2025, 2, d) for d in (1, 5, 15, 19, 20, 21, 28)),
        *(date(2025, 3, d) for d in (1, 2, 10, 11, 12, 15, 30, 31)),
        date(2025, 6, 30),
    ]
    ledger = Ledger.read(entries, table(PRICES), USD)
    for first, last in combinations(sorted(days), 2):
        period = Period(first, last)
        start = total(ledger.held(period.before, SCOPE).values())
        end = total(ledger.held(period.end, SCOPE).values())
        found = bridge(start, end, ledger.flows(period, SCOPE, kind), market=market)
        assert found.difference.quantize(Decimal("0.01")) == 0, (period, found)


# The reviewer's probes: market movement is what prices did, and nothing else.

MOVED = """
2024-01-01 open Assets:A
2024-01-01 open Assets:B
2024-01-01 open Assets:Bank USD
2024-01-01 open Equity:Opening-Balances
2024-01-01 open Income:Gains
2024-01-01 open Expenses:Food
2024-01-01 open Assets:Euro
"""


def test_a_holding_with_no_lot_moved_between_own_accounts_keeps_its_cost() -> None:
    """Reported in A at 100, moved to B at 120, worth 150 at the end: nothing was realised,
    and the 500 it rose is market movement."""
    entries = load(
        MOVED
        + """
2025-01-02 * "Holdings as reported"
  Assets:A  10 X
  Equity:Opening-Balances  -10 X
2025-02-10 * "Moved"
  Assets:A  -10 X
  Assets:B  10 X
"""
    )
    prices = {
        "X": [(date(2025, 1, 2), "100"), (date(2025, 2, 10), "120"), (date(2025, 3, 31), "150")]
    }
    period = Period(date(2025, 2, 1), date(2025, 3, 31))
    scope = frozenset({"Assets:A", "Assets:B"})

    market = why(entries, period, market=True, scope=scope, prices=prices)
    cost = why(entries, period, market=False, scope=scope, prices=prices)

    assert (market.start, market.end, market.movement) == (
        Decimal(1000),
        Decimal(1500),
        Decimal(500),
    )
    assert market.other == 0
    assert (cost.start, cost.end, cost.other) == (Decimal(1000), Decimal(1000), Decimal(0))
    assert cost.others[Other.REALISED] == 0


def test_a_split_changes_units_and_nothing_else() -> None:
    """Ten at 100 split into twenty at 50, worth 55 at the end: no opening, no cost, and the
    100 it rose since is market movement."""
    entries = load(
        MOVED
        + """
2025-01-02 * "Holdings as reported"
  Assets:A  10 X
  Equity:Opening-Balances  -10 X
2025-02-10 * "Split"
  Assets:A  10 X
  Equity:Opening-Balances  -10 X
"""
    )
    prices = {
        "X": [(date(2025, 1, 2), "100"), (date(2025, 2, 10), "50"), (date(2025, 3, 31), "55")]
    }
    period = Period(date(2025, 2, 1), date(2025, 3, 31))
    scope = frozenset({"Assets:A"})

    market = why(entries, period, market=True, scope=scope, prices=prices)
    cost = why(entries, period, market=False, scope=scope, prices=prices)

    assert (market.start, market.end) == (Decimal(1000), Decimal(1100))
    assert market.movement == Decimal(100)
    assert market.others[Other.OPENING] == 0
    assert (cost.start, cost.end, cost.others[Other.OPENING]) == (
        Decimal(1000),
        Decimal(1000),
        Decimal(0),
    )


def test_a_gain_made_before_the_period_and_taken_in_it_is_not_market_movement() -> None:
    """Ten lots bought at 50, worth 90 all period, sold at 90: investment income is the 400
    gain, the value already held it, and the market did nothing."""
    entries = load(
        MOVED
        + """
2024-12-01 * "Opening position"
  Assets:A  10 X {50 USD}
  Equity:Opening-Balances
2025-02-10 * "Sell"
  Assets:A  -10 X {50 USD} @ 90 USD
  Assets:Bank  900 USD
  Income:Gains  -400 USD
"""
    )
    prices = {"X": [(date(2024, 12, 1), "90")]}
    scope = frozenset({"Assets:A", "Assets:Bank"})

    market = why(entries, FIRST_QUARTER, market=True, scope=scope, prices=prices)
    cost = why(entries, FIRST_QUARTER, market=False, scope=scope, prices=prices)

    assert (market.start, market.end) == (Decimal(900), Decimal(900))
    assert market.investment_income == Decimal(400)
    assert market.others[Other.TAKEN] == Decimal(-400)
    assert market.movement == 0
    assert market.difference == 0
    assert (cost.start, cost.end, cost.investment_income) == (
        Decimal(500),
        Decimal(900),
        Decimal(400),
    )
    assert cost.others[Other.TAKEN] == 0


def test_money_in_another_currency_spent_is_no_gain_at_market_value() -> None:
    """A hundred euros at 1.10, fifty spent when the euro is 1.20: at market value the euros
    rose 10 and nothing was realised; at cost the fifty spent gave up 55 for 60."""
    entries = load(
        MOVED
        + """
2024-12-01 * "Opening"
  Assets:Euro  100 EUR
  Equity:Opening-Balances  -100 EUR
2025-02-10 * "Spent"
  Assets:Euro  -50 EUR
  Expenses:Food  50 EUR
"""
    )
    prices = {"EUR": [(date(2024, 12, 1), "1.10"), (date(2025, 2, 1), "1.20")]}
    scope = frozenset({"Assets:Euro"})

    market = why(entries, FIRST_QUARTER, market=True, scope=scope, prices=prices)
    cost = why(entries, FIRST_QUARTER, market=False, scope=scope, prices=prices)

    assert (market.start, market.end, market.saved) == (
        Decimal("110.00"),
        Decimal("60.00"),
        Decimal("-60.00"),
    )
    assert market.movement == Decimal("10.00")
    assert market.others[Other.REALISED] == 0
    assert market.difference == 0
    assert (cost.start, cost.end) == (Decimal("110.00"), Decimal("55.00"))
    assert cost.others[Other.REALISED] == Decimal("5.00")
    assert cost.difference == 0


def test_a_holding_booked_before_anything_priced_it_enters_at_its_first_price() -> None:
    """Reported mid-2024, first priced at 150 in March 2025: it opens then, at cost and at
    market value alike, and nothing is left as a difference."""
    entries = load(
        MOVED
        + """
2024-06-01 * "Holdings as reported"
  Assets:A  10 X
  Equity:Opening-Balances  -10 X
"""
    )
    prices = {"X": [(date(2025, 3, 1), "150")]}
    scope = frozenset({"Assets:A"})
    for market in (True, False):
        found = why(entries, FIRST_QUARTER, market=market, scope=scope, prices=prices)
        assert (found.start, found.end) == (Decimal(0), Decimal(1500))
        assert found.others[Other.OPENING] == Decimal(1500)
        assert found.movement in (None, Decimal(0))
        assert found.difference == 0


def test_money_received_before_any_rate_enters_at_the_first_rate() -> None:
    entries = load(
        MOVED
        + """
2025-01-05 open Income:Gift
2025-01-05 * "Gift"
  Assets:Euro  1000 EUR
  Income:Gift  -1000 EUR
"""
    )
    prices = {"EUR": [(date(2025, 2, 1), "1.19")]}
    scope = frozenset({"Assets:Euro"})
    for market in (True, False):
        found = why(entries, FIRST_QUARTER, market=market, scope=scope, prices=prices)
        assert found.end == Decimal("1190.00")
        assert found.others[Other.OTHER_INCOME] == Decimal("1190.00")
        assert found.difference == 0


def test_what_nothing_can_price_is_left_out_and_named() -> None:
    entries = load(
        MOVED
        + """
2025-01-05 * "Opening"
  Assets:Euro  100 EUR
  Equity:Opening-Balances  -100 EUR
"""
    )
    ledger = Ledger.read(entries, table({}), USD)
    held = ledger.held(date(2025, 1, 31), frozenset({"Assets:Euro"}))["Assets:Euro"]
    assert held.unvalued == {"EUR"}
    assert (held.cost, held.market) == (Decimal(0), Decimal(0))
    moved = ledger.flows(FIRST_QUARTER, frozenset({"Assets:Euro"}), kind)
    assert moved.unconverted == 2
    assert bridge(Held(), held, moved, market=True).difference == 0


def test_a_move_to_an_account_outside_counts_none_of_its_categories(
    entries: list[Directive],
) -> None:
    """Rent paid from the bank: read with the bank outside the statement, the card's grocery
    is the only spending, and the bank's payment to it is one amount at the edge."""
    _, _, moved = read(entries, FIRST_QUARTER, frozenset({"Liabilities:Card"}))

    assert moved.by_account == {"Expenses:Food": Decimal("200.00")}
    assert moved.others(market=False)[Other.OUTSIDE] == Decimal("200.00")


def test_the_household_fixture_for_its_owners_accounts() -> None:
    """Avery's own and the joint account, this year so far, by hand from tests/household.py."""
    entries = load(household.ledger_text().replace('option "operating_currency" "USD"', ""))
    scope = frozenset({"Assets:Zq:AverySolo", "Assets:Zq:AveryInvest", "Assets:Zq:Joint"})
    period = Period(date(2026, 1, 1), household.TODAY)
    stored = {
        s: [(day, str(p)) for name, day, p in household.prices() if name == s]
        for s in household.SECURITIES
    }
    found = why(entries, period, market=True, scope=scope, prices=stored)

    assert found.end == Decimal("52341.17") + Decimal("12345.60") + Decimal("20000.42")
    # Nothing is pay by its name; Aardwork is other income.
    spent = ["1311.07", "88.83", "19.06", "1640.84", "468.35"]
    assert found.saved == -sum((Decimal(n) for n in spent), Decimal(0))
    # What each account held before its history: Avery's 52,341.17 less the 917.31 its rows
    # added, the joint 20,000.42 less its -2,109.19, and ten ZQAA at their cost. ZQAA had no
    # price until Sep 30, so it was not valued until then, when it was worth its cost.
    assert found.others[Other.OPENING] == Decimal("51423.86") + Decimal("22109.61") + Decimal(
        "12345.60"
    )
    assert found.others[Other.UNPRICED] == 0
    # As the ledger is written, the 777.31 Avery moved out waits for review, not placed yet.
    assert found.others[Other.UNPLACED] == Decimal("-777.31")
    assert found.others[Other.OTHER_INCOME] == Decimal("3113.58")
    assert found.movement == 0
    assert found.difference == 0


def test_the_imported_ledger_fixture(tmp_path: Path) -> None:
    """tests/ledgers.py: a pad, wages, a grocery on the card paid off, ten ZQX bought at 100."""
    entries = books.load_entries(ledgers.write(tmp_path))
    scope = frozenset({"Assets:Zq:Checking", "Assets:Zq:Brokerage", "Liabilities:Zq:Card"})
    prices = {
        "ZQX": [
            (ledgers.IMPLICIT_PRICE[0], str(ledgers.IMPLICIT_PRICE[1])),
            (date(2026, 6, 30), "110.00"),
        ]
    }
    period = Period(ledgers.FIRST, ledgers.LAST)
    start, end, moved = read(entries, period, scope, prices)

    assert end.market == ledgers.NET_WORTH
    for market in (True, False):
        found = bridge(start, end, moved, market=market)
        assert found.saved == Decimal("2000.00") - Decimal("45.50")
        assert found.others[Other.OPENING] == Decimal("1000.00")  # the pad
        # Bought at 100 on the day priced at 100, worth 110 at the end.
        assert found.movement == (Decimal("100.00") if market else None)
        assert found.difference == 0


def test_a_change_is_coloured_only_when_large_in_money_and_in_proportion() -> None:
    assert MATERIAL.amount == 1000
    assert MATERIAL.share == Decimal("0.10")
    # Over 1,000 but under 10%.
    assert tone(Decimal(21000), Decimal(20000), Better.LESS) is Tone.QUIET
    # Over 10% but under 1,000.
    assert tone(Decimal(1500), Decimal(1000), Better.LESS) is Tone.QUIET
    # Both: more spending is unfavourable, more saved favourable, whichever way is up.
    assert tone(Decimal(12000), Decimal(10000), Better.LESS) is Tone.UNFAVOURABLE
    assert tone(Decimal(8000), Decimal(10000), Better.LESS) is Tone.FAVOURABLE
    assert tone(Decimal(12000), Decimal(10000), Better.MORE) is Tone.FAVOURABLE
    assert tone(Decimal(-12000), Decimal(-10000), Better.MORE) is Tone.UNFAVOURABLE
    assert tone(Decimal(12000), None, Better.MORE) is Tone.QUIET
    assert tone(Decimal(12000), Decimal(10000), Better.NEITHER) is Tone.QUIET


def test_a_period_splits_into_calendar_columns_cut_to_it() -> None:
    period = Period(date(2026, 2, 15), date(2026, 11, 26))

    assert [(c.start, c.end) for c in columns(period, Columns.QUARTER)] == [
        (date(2026, 2, 15), date(2026, 3, 31)),
        (date(2026, 4, 1), date(2026, 6, 30)),
        (date(2026, 7, 1), date(2026, 9, 30)),
        (date(2026, 10, 1), date(2026, 11, 26)),
    ]
    assert len(columns(period, Columns.MONTH)) == 10
    assert columns(period, Columns.YEAR) == [period]
    assert Period(date(2024, 2, 29), date(2024, 12, 31)).a_year_before() == Period(
        date(2023, 2, 28), date(2023, 12, 31)
    )


def test_what_an_entry_leaves_over_within_its_tolerance_is_the_rounding_line() -> None:
    """3.333 units at 3.00 is 9.999, paid for with 10.00: beancount balances it to its
    tolerance, and the thousandth left over is said, so nothing is a difference."""
    entries = load(
        """
2025-01-01 open Assets:Bank USD
2025-01-01 open Assets:Broker
2025-01-01 open Equity:Opening-Balances
2025-01-01 * "Opening"
  Assets:Bank  10.00 USD
  Equity:Opening-Balances
2025-01-02 * "Buy"
  Assets:Broker  3.333 X {3.00 USD}
  Assets:Bank  -10.00 USD
"""
    )
    period = Period(date(2025, 1, 2), date(2025, 1, 31))
    found = why(
        entries, period, market=False, scope=frozenset({"Assets:Bank", "Assets:Broker"}), prices={}
    )

    assert found.end == Decimal("9.999")
    assert found.others[Other.ROUNDING] == Decimal("-0.001")
    assert found.difference == 0


def test_the_price_table_answers_as_the_store_would() -> None:
    prices = table({"X": [(date(2025, 1, 2), "100.25"), (date(2025, 2, 10), "120")]})

    assert prices.price("X", date(2025, 1, 1), USD) is None
    assert prices.price("X", date(2025, 2, 9), USD) == Decimal("100.25")
    assert prices.first("X") == date(2025, 1, 2)
    found = prices("X", date(2026, 1, 1))
    assert found is not None
    assert (found.as_of, found.price, found.fetched_at) == (
        date(2025, 2, 10),
        Decimal(120),
        FETCHED,
    )


def test_a_category_that_comes_to_nothing_is_no_line() -> None:
    """Bought and refunded in the same month: the category is in no total, so in no tree."""
    entries = load(
        MOVED
        + """
2025-01-05 * "Opening"
  Assets:Bank  100.00 USD
  Equity:Opening-Balances
2025-02-03 * "Shop"
  Assets:Bank  -40.00 USD
  Expenses:Food
2025-02-09 * "Shop" "Refund"
  Assets:Bank  40.00 USD
  Expenses:Food
"""
    )
    _, _, moved = read(entries, FIRST_QUARTER, frozenset({"Assets:Bank"}), {})

    assert moved.by_account == {}
