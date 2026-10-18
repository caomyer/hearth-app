from datetime import date
from decimal import Decimal

from beancount import loader
from beancount.core.data import Directive

from hearth.reports.balances import (
    AccountBalance,
    AssertionCheck,
    account_balances,
    check_balance_assertions,
)
from tests.reports.conftest import LEDGER


def by_account(entries: list[Directive], as_of: date) -> dict[str, AccountBalance]:
    return {b.account: b for b in account_balances(entries, as_of=as_of)}


def test_every_assertion_in_the_ledger_is_reproduced_by_the_postings(
    entries: list[Directive],
) -> None:
    checks = check_balance_assertions(entries)

    assert len(checks) == 5
    assert all(check.agrees for check in checks)


def test_a_wrong_assertion_is_caught_with_both_figures() -> None:
    wrong = LEDGER + "\n2026-05-02 balance Assets:Alex:Checking  3748.99 USD\n"
    entries, errors, _ = loader.load_string(wrong)
    assert len(errors) == 1  # beancount agrees it is wrong

    bad = [c for c in check_balance_assertions(entries) if not c.agrees]

    assert bad == [
        AssertionCheck(
            "Assets:Alex:Checking",
            date(2026, 5, 2),
            "USD",
            Decimal("3748.99"),
            Decimal("3748.90"),
        )
    ]
    balance = by_account(entries, date(2026, 5, 2))["Assets:Alex:Checking"]
    assert not balance.verified
    assert balance.disagreements == tuple(bad)
    assert balance.last_verified == date(2026, 5, 1)


def test_an_assertion_covers_sub_accounts_and_counts_only_earlier_days() -> None:
    ledger = """
2026-01-01 open Assets:Bank
2026-01-01 open Assets:Bank:Savings
2026-01-01 open Equity:Opening
2026-01-02 * "In"
  Assets:Bank  10.00 USD
  Assets:Bank:Savings  5.00 USD
  Equity:Opening
2026-01-03 balance Assets:Bank  15.00 USD
2026-01-03 * "Same day, after the assertion"
  Assets:Bank  1.00 USD
  Equity:Opening
"""
    entries, errors, _ = loader.load_string(ledger)
    assert errors == []

    (check,) = check_balance_assertions(entries)

    assert check.agrees
    assert check.computed == Decimal("15.00")


def test_balances_carry_their_evidence(entries: list[Directive]) -> None:
    balances = by_account(entries, date(2026, 5, 31))

    assert balances["Assets:Alex:Checking"] == AccountBalance(
        account="Assets:Alex:Checking",
        units={"USD": Decimal("3748.90")},
        last_verified=date(2026, 5, 1),
        disagreements=(),
    )
    assert balances["Assets:Joint:Checking"].units == {"USD": Decimal("556.67")}
    assert balances["Liabilities:Blair:Card"].units == {"USD": Decimal("-80.25")}


def test_an_account_no_member_owns_is_still_balanced(
    entries: list[Directive],
) -> None:
    savings = by_account(entries, date(2026, 5, 31))["Assets:Unassigned:Savings"]

    assert savings.units == {"USD": Decimal("495.00")}
    assert savings.last_verified is None


def test_balances_answer_for_the_end_of_the_as_of_day(entries: list[Directive]) -> None:
    assert by_account(entries, date(2026, 1, 24))["Assets:Alex:Checking"].units == {
        "USD": Decimal("4000.00")
    }
    assert by_account(entries, date(2026, 1, 25))["Assets:Alex:Checking"].units == {
        "USD": Decimal("3959.90")
    }


def test_only_accounts_open_on_the_day_are_listed(entries: list[Directive]) -> None:
    march = by_account(entries, date(2026, 3, 30))
    april = by_account(entries, date(2026, 4, 1))

    assert "Assets:Old:Savings" in march
    assert "Assets:Old:Savings" not in april
    assert "Assets:Blair:Brokerage" not in april
    assert "Expenses:Food" not in march


def test_an_open_account_with_nothing_in_it_has_no_units(entries: list[Directive]) -> None:
    brokerage = by_account(entries, date(2026, 6, 1))["Assets:Blair:Brokerage"]

    assert brokerage.units == {}
