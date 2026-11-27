from datetime import date
from decimal import Decimal

import pytest
from beancount import loader
from beancount.core.amount import Amount
from beancount.core.data import Directive, Transaction
from beancount.core.number import D

from hearth.valuation.holdings import Holding, holdings_at

LEDGER = """
option "operating_currency" "USD"

2026-01-01 open Assets:Bank:Checking USD
2026-01-01 open Assets:Broker:Taxable
2026-01-01 open Liabilities:Card USD
2026-01-01 open Expenses:Food USD
2026-01-01 open Equity:Opening USD
2026-01-01 commodity FUNDA
2026-01-01 commodity FUNDB

2026-01-02 * "Opening position"
  Assets:Broker:Taxable  10.123 FUNDA {50.00 USD}
  Equity:Opening

2026-01-02 * "Opening cash"
  Assets:Bank:Checking  1000.00 USD
  Equity:Opening

2026-02-03 * "Buy"
  Assets:Broker:Taxable  2.5 FUNDB {40.00 USD}
  Assets:Bank:Checking  -100.00 USD

2026-02-04 * "Groceries"
  Liabilities:Card  -25.10 USD
  Expenses:Food

2026-03-02 * "Sell all"
  Assets:Broker:Taxable  -2.5 FUNDB {40.00 USD} @ 44.00 USD
  Assets:Bank:Checking  110.00 USD
  Equity:Opening  -10.00 USD
"""


@pytest.fixture(scope="module")
def entries() -> list[Directive]:
    loaded, errors, _ = loader.load_string(LEDGER)
    assert errors == []
    return loaded


def test_positions_on_a_day_sum_every_lot_up_to_it(entries: list[Directive]) -> None:
    assert holdings_at(entries, date(2026, 2, 28)) == [
        Holding("Assets:Bank:Checking", "USD", Decimal("900.00")),
        # 10.123 at 50.00 and 2.5 at 40.00, lots at cost; cash costs nothing the books hold.
        Holding("Assets:Broker:Taxable", "FUNDA", Decimal("10.123"), Amount(D("506.15"), "USD")),
        Holding("Assets:Broker:Taxable", "FUNDB", Decimal("2.5"), Amount(D("100.00"), "USD")),
        Holding("Liabilities:Card", "USD", Decimal("-25.10")),
    ]


def test_a_position_sold_to_zero_is_not_held(entries: list[Directive]) -> None:
    held = holdings_at(entries, date(2026, 3, 2))

    assert "FUNDB" not in {h.commodity for h in held}
    assert Holding("Assets:Bank:Checking", "USD", Decimal("1010.00")) in held


def test_nothing_after_the_day_counts(entries: list[Directive]) -> None:
    assert holdings_at(entries, date(2026, 1, 1)) == []
    assert {h.commodity for h in holdings_at(entries, date(2026, 2, 2))} == {"USD", "FUNDA"}


def test_income_expense_and_equity_accounts_are_not_holdings(entries: list[Directive]) -> None:
    roots = {h.account.split(":")[0] for h in holdings_at(entries, date(2026, 12, 31))}

    assert roots == {"Assets", "Liabilities"}


def test_unbooked_entries_are_refused_rather_than_guessed() -> None:
    # Straight from the parser, before booking, the elided amount has no number.
    from beancount.parser import parser

    raw, _, _ = parser.parse_string(
        '2026-01-02 * "Elided"\n  Equity:Opening  -5.00 USD\n  Assets:Bank:Checking\n'
    )
    unbooked = [e for e in raw if isinstance(e, Transaction)]

    with pytest.raises(ValueError, match="no number"):
        holdings_at(unbooked, date(2026, 12, 31))


COSTS = """
option "operating_currency" "USD"

2026-01-01 open Assets:Broker:Taxable
2026-01-01 open Income:Gains
2026-01-01 open Equity:Opening

2026-01-02 * "Two lots"
  Assets:Broker:Taxable  10 FUNDA {50.00 USD}
  Assets:Broker:Taxable  4 FUNDA {62.50 USD}
  Equity:Opening

2026-02-02 * "Sell the first lot in part"
  Assets:Broker:Taxable  -3 FUNDA {50.00 USD} @ 70.00 USD
  Assets:Broker:Taxable  210.00 USD
  Income:Gains  -60.00 USD

2026-01-02 * "Moved in at no cost the ledger says"
  Assets:Broker:Taxable  6 FUNDC
  Equity:Opening

2026-01-02 * "A lot in euros beside a lot in dollars"
  Assets:Broker:Taxable  1 FUNDD {10.00 USD}
  Assets:Broker:Taxable  1 FUNDD {9.00 EUR}
  Equity:Opening
"""


@pytest.fixture(scope="module")
def costed() -> list[Directive]:
    loaded, errors, _ = loader.load_string(COSTS)
    assert errors == []
    return loaded


def test_a_positions_cost_is_its_lots_less_what_was_sold_at_cost(costed: list[Directive]) -> None:
    held = {h.commodity: h for h in holdings_at(costed, date(2026, 12, 31))}

    # 10 at 50.00 and 4 at 62.50 is 750.00; three of the first sold take 150.00 of it.
    assert held["FUNDA"].units == Decimal(11)
    assert held["FUNDA"].cost == Amount(D("600.00"), "USD")


def test_a_position_any_of_whose_units_came_at_no_cost_has_none(costed: list[Directive]) -> None:
    held = {h.commodity: h for h in holdings_at(costed, date(2026, 12, 31))}

    assert held["FUNDC"].cost is None
    # Lots in two currencies add up to no one cost.
    assert held["FUNDD"].cost is None
    assert held["USD"].cost is None
