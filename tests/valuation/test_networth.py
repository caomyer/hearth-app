from datetime import date
from decimal import Decimal

import pytest

from hearth.valuation.holdings import Holding
from hearth.valuation.networth import (
    Freshness,
    IncompleteValuationError,
    NetWorth,
    PriceLookup,
    missed_sessions,
    value_holdings,
)
from hearth.valuation.quotes import Quote
from tests.valuation.conftest import quote

THURSDAY = date(2026, 10, 15)
FRIDAY = date(2026, 10, 16)
MONDAY = date(2026, 10, 19)
WEDNESDAY = date(2026, 10, 21)
THURSDAY_AFTER = date(2026, 10, 22)

CASH = Holding("Assets:Bank:Checking", "USD", Decimal("1000.00"))
CARD = Holding("Liabilities:Card", "USD", Decimal("-250.50"))
FUND = Holding("Assets:Broker:Taxable", "FUNDA", Decimal("10.5"))
OTHER = Holding("Assets:Broker:Taxable", "FUNDB", Decimal("3"))


def lookup_from(*quotes: Quote) -> PriceLookup:
    def lookup(symbol: str, on_or_before: date) -> Quote | None:
        found = [q for q in quotes if q.symbol == symbol and q.as_of <= on_or_before]
        return max(found, key=lambda q: q.as_of, default=None)

    return lookup


def value(*holdings: Holding, lookup: PriceLookup, as_of: date = MONDAY) -> NetWorth:
    return value_holdings(holdings, lookup, as_of=as_of, currency="USD")


@pytest.mark.parametrize(
    ("price_day", "as_of", "missed"),
    [
        (FRIDAY, FRIDAY, 0),
        (FRIDAY, MONDAY, 0),
        (THURSDAY, MONDAY, 1),
        (FRIDAY, WEDNESDAY, 2),
        (FRIDAY, THURSDAY_AFTER, 3),
    ],
)
def test_missed_sessions_count_weekdays_between(price_day: date, as_of: date, missed: int) -> None:
    assert missed_sessions(price_day, as_of) == missed


def test_cash_and_liabilities_in_the_currency_are_worth_their_units() -> None:
    worth = value(CASH, CARD, lookup=lookup_from())

    assert worth.complete
    assert worth.total == Decimal("749.50")
    assert all(p.freshness is Freshness.CURRENT and p.quote is None for p in worth.positions)


def test_a_priced_holding_is_units_times_the_raw_close_with_its_evidence() -> None:
    friday = quote(as_of=FRIDAY, price="101.25")

    (fund,) = value(FUND, lookup=lookup_from(friday)).positions

    assert fund.freshness is Freshness.CURRENT
    assert fund.value == Decimal("1063.125")
    assert fund.quote == friday
    assert fund.price_age_days == 3
    assert fund.reason is None


def test_an_unpriced_holding_is_never_worth_zero() -> None:
    worth = value(CASH, FUND, lookup=lookup_from())

    (fund,) = worth.unpriced
    assert fund.holding == FUND
    assert fund.value is None
    assert fund.reason == "no price recorded"
    assert not worth.complete


def test_an_incomplete_net_worth_refuses_to_give_a_total() -> None:
    worth = value(CASH, FUND, lookup=lookup_from())

    with pytest.raises(IncompleteValuationError, match=r"FUNDA in Assets:Broker:Taxable"):
        _ = worth.total


def test_the_partial_figure_is_a_floor_that_names_what_it_leaves_out() -> None:
    worth = value(CASH, FUND, OTHER, lookup=lookup_from(quote("FUNDB", FRIDAY, "20")))

    assert worth.known_total == Decimal("1060.00")
    assert worth.missing == ("10.5 FUNDA in Assets:Broker:Taxable: no price recorded",)


def test_a_price_lagging_more_than_two_sessions_is_stale_and_says_how_old() -> None:
    worth = value(FUND, lookup=lookup_from(quote(as_of=FRIDAY)), as_of=THURSDAY_AFTER)

    (fund,) = worth.stale
    assert fund.freshness is Freshness.STALE
    assert fund.value == Decimal("1063.125")
    assert fund.price_age_days == 6
    assert fund.reason == "latest price is from 2026-10-16, 6 days old"
    assert worth.complete


def test_a_price_within_two_sessions_is_current() -> None:
    worth = value(FUND, lookup=lookup_from(quote(as_of=FRIDAY)), as_of=WEDNESDAY)

    assert worth.stale == ()
    assert worth.positions[0].freshness is Freshness.CURRENT


def test_a_price_in_another_currency_is_unpriced_not_converted() -> None:
    euro = quote(as_of=FRIDAY, currency="EUR")

    (fund,) = value(FUND, lookup=lookup_from(euro)).unpriced

    assert fund.value is None
    assert fund.quote == euro
    assert fund.reason == "priced in EUR, and currency conversion is not supported"


def test_a_lookup_that_returns_a_future_price_is_a_bug_not_a_value() -> None:
    def future(symbol: str, on_or_before: date) -> Quote:
        return quote(as_of=WEDNESDAY)

    with pytest.raises(ValueError, match="for a 2026-10-19 valuation"):
        value(FUND, lookup=future)


def test_nothing_held_is_a_complete_zero() -> None:
    worth = value(lookup=lookup_from())

    assert worth.complete
    assert worth.total == Decimal(0)
