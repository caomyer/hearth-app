"""Statements over invented books, every figure worked out by hand below, never by hearth.

The ledger here has every shape a change in net worth can come from: a pad, money opened against
equity, a lot opened at a gain it had already made, a lot with no price ever, a sale of a lot
with its gain booked as income, a dividend, tax, a retirement contribution booked as an
expense, a gift, money and shares moved to accounts outside the statement, a row nobody has
placed yet, a carried account's reconciling entry, holdings booked as a brokerage reports them,
with no lot, and one of those sold. The change in net worth is proved to add up to the sheets
to the cent at cost and at market value, for every period between the days below.
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
    Columns,
    Flows,
    Held,
    Other,
    Period,
    Tone,
    bridge,
    columns,
    flows,
    movement,
    sheets,
    tone,
    total,
)
from hearth.valuation.networth import PriceLookup
from hearth.valuation.quotes import Quote
from tests import household, ledgers

USD = "USD"

LEDGER = """
option "operating_currency" "USD"

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
"""BBB is never priced, so it is valued at what it cost."""

SCOPE = frozenset(
    {"Assets:Bank", "Assets:Broker", "Assets:Plaid", "Assets:Carried", "Liabilities:Card"}
)
"""Every account but the two that are theirs."""


def lookup(prices: dict[str, list[tuple[date, str]]]) -> PriceLookup:
    def found(symbol: str, day: date) -> Quote | None:
        earlier = [(d, p) for d, p in prices.get(symbol, []) if d <= day]
        if not earlier:
            return None
        d, p = earlier[-1]
        return Quote(symbol, d, Decimal(p), USD, "test", symbol, datetime(2025, 1, 1, tzinfo=UTC))

    return found


@pytest.fixture(scope="module")
def entries() -> list[Directive]:
    loaded, errors, _ = loader.load_string(LEDGER)
    assert not errors, errors
    return loaded


def kind(account: str) -> categories.Kind:
    return categories.guess(account)


def read(
    entries: Iterable[Directive], period: Period, scope: frozenset[str] = SCOPE
) -> tuple[Held, Held, Flows]:
    held = sheets(entries, [period.before, period.end], scope, lookup(PRICES), USD)
    moved = flows(entries, period, scope, kind, lookup(PRICES), USD)
    return total(held[period.before].values()), total(held[period.end].values()), moved


FIRST_QUARTER = Period(date(2025, 1, 1), date(2025, 3, 31))


def test_the_sheets_value_each_holding_at_cost_and_at_its_latest_price(
    entries: list[Directive],
) -> None:
    held = sheets(entries, [date(2025, 3, 31)], SCOPE, lookup(PRICES), USD)[date(2025, 3, 31)]

    # Bank: 5,000 padded, +3,000 -1,200 -300 -250 -100 +12 +360 +500 -400 -75 +130 -200.
    assert held["Assets:Bank"] == Held(Decimal("6477.00"), Decimal("6477.00"))
    assert "Liabilities:Card" not in held  # paid off: it holds nothing
    # Four AAA at a cost of 50, worth 100; five BBB at 20, never priced, so at cost.
    broker = held["Assets:Broker"]
    assert broker.cost == Decimal("300.00")
    assert broker.market == Decimal("500.00")
    assert broker.at_cost == {"BBB"}
    # Three CCC booked at 100 each, the day they were reported; one sold at its average cost.
    plaid = held["Assets:Plaid"]
    assert plaid.cost == Decimal("200.00")
    assert plaid.market == Decimal("240.00")
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
    start, end, moved = read(entries, FIRST_QUARTER)

    why = bridge(start, end, moved, market=False)

    assert (why.start, why.end) == (Decimal(0), Decimal("7997.00"))
    assert why.saved == Decimal("1300.00")
    assert why.investment_income == Decimal("172.00")
    assert why.movement is None
    assert why.others == {
        Other.OTHER_INCOME: Decimal("500.00"),
        Other.NOT_SPENDING: Decimal("-250.00"),
        # The pad's 5,000, the carried account's 1,000, ten AAA at their cost of 500, three
        # CCC as reported, at 300, and the carried account's reconciling 20.
        Other.OPENING: Decimal("6820.00"),
        Other.UNPLACED: Decimal("-75.00"),
        # 400 to their account, and two AAA at their cost of 100 to their brokerage.
        Other.OUTSIDE: Decimal("-500.00"),
        # One CCC, which cost 100 on average, went for 130.
        Other.REALISED: Decimal("30.00"),
        # Every entry balances to the cent.
        Other.ROUNDING: Decimal(0),
    }
    assert why.difference == 0


def test_why_net_worth_changed_at_market_value(entries: list[Directive]) -> None:
    start, end, moved = read(entries, FIRST_QUARTER)

    why = bridge(start, end, moved, market=True)

    # 6,477 in the bank, four AAA at 100, five BBB at their cost, two CCC at 120, and 1,020.
    assert why.end == Decimal("8237.00")
    # Ten AAA came in at 80, 300 over their cost: a gain they had already made, which is the
    # opening's, not the market's. Two AAA left at 90, 80 over their cost, which went with them.
    assert why.others[Other.OPENING] == Decimal("7120.00")
    assert why.others[Other.OUTSIDE] == Decimal("-580.00")
    # What is not yet realised ends at 240 (four AAA 200 over cost, two CCC 40 over), of which
    # the moves account for 220.
    assert why.movement == Decimal("20.00")
    assert movement(start, end, moved) == Decimal("20.00")
    assert why.difference == 0


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
    # From 300 not yet realised to 240, with 80 of it gone with the shares that left.
    assert at_market.movement == Decimal("20.00")
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
    for first, last in combinations(sorted(days), 2):
        period = Period(first, last)
        start, end, moved = read(entries, period)
        why = bridge(start, end, moved, market=market)
        assert why.difference.quantize(Decimal("0.01")) == 0, (period, why)
        assert why.explained.quantize(Decimal("0.01")) == why.end.quantize(Decimal("0.01"))


def test_what_cannot_be_valued_is_left_out_and_any_difference_shown() -> None:
    loaded, errors, _ = loader.load_string(
        """
option "operating_currency" "USD"
2025-01-01 open Assets:Euro
2025-01-01 open Equity:Opening-Balances
2025-01-05 * "Opening"
  Assets:Euro  100 EUR
  Equity:Opening-Balances  -100 EUR
"""
    )
    assert not errors
    scope = frozenset({"Assets:Euro"})
    period = Period(date(2025, 1, 1), date(2025, 1, 31))
    # With no rate at all, the euros are named and left out of both sides alike.
    held = sheets(loaded, [period.end], scope, lookup({}), USD)[period.end]["Assets:Euro"]
    assert held.unvalued == {"EUR"}
    assert (held.cost, held.market) == (Decimal(0), Decimal(0))
    # A rate that arrives after they were booked values them, but nothing said what they were
    # worth when they came in: the difference is theirs, shown, never hidden.
    rates = {"EUR": [(date(2025, 1, 20), "1.10")]}
    end = total(sheets(loaded, [period.end], scope, lookup(rates), USD)[period.end].values())
    moved = flows(loaded, period, scope, kind, lookup(rates), USD)
    assert end.cost_unknown == {"EUR"}
    assert moved.unconverted == 1  # the opening's equity side, in euros
    why = bridge(Held(), end, moved, market=True)
    assert why.difference == Decimal("110.00")


def test_a_move_to_an_account_outside_counts_none_of_its_categories(
    entries: list[Directive],
) -> None:
    """Rent paid from the bank: read with the bank outside the statement, the card's grocery
    is the only spending, and the bank's moves are each one amount at the edge."""
    moved = flows(
        entries, FIRST_QUARTER, frozenset({"Liabilities:Card"}), kind, lookup(PRICES), USD
    )

    assert moved.by_account == {"Expenses:Food": Decimal("200.00")}
    assert moved.others(market=False)[Other.OUTSIDE] == Decimal("200.00")  # the payment


def test_the_household_fixture_for_its_owners_accounts() -> None:
    """Avery's own and the joint account, this year so far, by hand from tests/household.py."""
    loaded, errors, _ = loader.load_string(household.ledger_text())
    assert not errors
    scope = frozenset({"Assets:Zq:AverySolo", "Assets:Zq:AveryInvest", "Assets:Zq:Joint"})
    period = Period(date(2026, 1, 1), household.TODAY)
    stored = {
        s: [(day, str(p)) for name, day, p in household.prices() if name == s]
        for s in household.SECURITIES
    }
    held = sheets(loaded, [period.before, period.end], scope, lookup(stored), USD)
    moved = flows(loaded, period, scope, kind, lookup(stored), USD)
    start, end = total(held[period.before].values()), total(held[period.end].values())

    assert end.market == Decimal("52341.17") + Decimal("12345.60") + Decimal("20000.42")
    why = bridge(start, end, moved, market=True)
    # Nothing is pay by its name; Aardwork is other income.
    assert why.saved == -(
        Decimal("1311.07")
        + Decimal("88.83")
        + Decimal("19.06")
        + Decimal("1640.84")
        + Decimal("468.35")
    )
    # What each account held before its history, so the ledger arrives at its cash today:
    # Avery's 52,341.17 less the 917.31 its rows added, the joint 20,000.42 less its -2,109.19,
    # and ten ZQAA at 1,234.56. As the ledger is written, the 777.31 Avery moved out waits for
    # review, not placed yet; the books lay Avery's ruling over it (the doorway's tests).
    assert why.others[Other.OPENING] == (
        Decimal("51423.86") + Decimal("22109.61") + Decimal("12345.60")
    )
    assert why.others[Other.UNPLACED] == Decimal("-777.31")
    assert why.others[Other.OUTSIDE] == 0
    assert why.others[Other.OTHER_INCOME] == Decimal("3113.58")
    assert why.movement == 0
    assert why.difference == 0


def test_the_imported_ledger_fixture(tmp_path: Path) -> None:
    """tests/ledgers.py: a pad, wages, a grocery on the card paid off, ten ZQX bought at 100."""
    loaded = books.load_entries(ledgers.write(tmp_path))
    scope = frozenset({"Assets:Zq:Checking", "Assets:Zq:Brokerage", "Liabilities:Zq:Card"})
    prices = {
        "ZQX": [
            (ledgers.IMPLICIT_PRICE[0], str(ledgers.IMPLICIT_PRICE[1])),
            (date(2026, 6, 30), "110.00"),
        ]
    }
    period = Period(ledgers.FIRST, ledgers.LAST)
    held = sheets(loaded, [period.before, period.end], scope, lookup(prices), USD)
    moved = flows(loaded, period, scope, kind, lookup(prices), USD)
    start, end = total(held[period.before].values()), total(held[period.end].values())

    assert end.market == ledgers.NET_WORTH
    for market in (True, False):
        why = bridge(start, end, moved, market=market)
        assert why.saved == Decimal("2000.00") - Decimal("45.50")
        assert why.others[Other.OPENING] == Decimal("1000.00")  # the pad
        assert why.movement == (Decimal("100.00") if market else None)
        assert why.difference == 0


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
    loaded, errors, _ = loader.load_string(
        """
option "operating_currency" "USD"
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
    assert not errors, errors
    scope = frozenset({"Assets:Bank", "Assets:Broker"})
    period = Period(date(2025, 1, 2), date(2025, 1, 31))
    held = sheets(loaded, [period.before, period.end], scope, lookup({}), USD)
    moved = flows(loaded, period, scope, kind, lookup({}), USD)

    why = bridge(
        total(held[period.before].values()), total(held[period.end].values()), moved, market=False
    )

    assert why.end == Decimal("9.999")
    assert why.others[Other.ROUNDING] == Decimal("-0.001")
    assert why.difference == 0
