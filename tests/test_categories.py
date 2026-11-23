"""What kind of money each income and expense account is, and what that changes: a move
booked as an expense is not spending, wherever spending is added up, and what it leaves out
is in what was left over, so the figures still add up (``hearth.categories``).

Every account and figure is invented, and every figure below is worked out here by hand.
"""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hearth import accounts, books, categories, figures
from hearth.categories import Kind, NotACategory
from hearth.cli import main
from hearth.config import DATA_DIR_VARIABLE, LEDGER_VARIABLE
from hearth.disclosure import Disclosure
from hearth.figures import ownership
from hearth.members import find
from hearth.months import Month
from tests.conftest import FakeClock
from tests.firstday import FirstDay, first_day
from tests.household import noon

TODAY = date(2026, 10, 15)
CHECKING = "Assets:Zq:Checking"
RETIREMENT = "Expenses:Zq:Retirement"


@pytest.mark.parametrize(
    ("account", "kind"),
    [
        ("Income:Salary", Kind.PAY),
        ("Income:Zq:Wages", Kind.PAY),
        ("Income:Job:Bonus", Kind.PAY),
        ("Income:Bank:Interest", Kind.INVESTMENT_INCOME),
        ("Income:Brokerage:Dividends", Kind.INVESTMENT_INCOME),
        ("Income:CapitalGains", Kind.INVESTMENT_INCOME),
        ("Income:Gifts", Kind.OTHER_INCOME),
        ("Income:Zq:Refunds", Kind.OTHER_INCOME),
        ("Expenses:Taxes:Federal", Kind.TAX),
        ("Expenses:Tax", Kind.TAX),
        ("Expenses:Payroll:Withholding", Kind.TAX),
        ("Expenses:Retirement:401k", Kind.NOT_SPENDING),
        ("Expenses:Zq:Retirement401k", Kind.NOT_SPENDING),
        ("Expenses:RothIRA", Kind.NOT_SPENDING),
        ("Expenses:Transfers", Kind.NOT_SPENDING),
        ("Expenses:Food:Dining", Kind.SPENDING),
        ("Expenses:Transport:Taxi", Kind.SPENDING),
        ("Expenses:Interest", Kind.SPENDING),
        ("Expenses:Zq:Gadgets", Kind.SPENDING),
    ],
)
def test_a_kind_is_guessed_from_generic_words_in_the_name(account: str, kind: Kind) -> None:
    assert categories.guess(account) is kind


def test_only_an_income_or_expense_account_has_a_kind() -> None:
    with pytest.raises(NotACategory):
        categories.guess("Assets:Zq:Checking")
    with pytest.raises(NotACategory):
        categories.guess("Equity:Opening")


LEDGER = f"""option "operating_currency" "USD"

2026-08-01 open {CHECKING} USD
2026-08-01 open Equity:Zq:Opening USD
2026-08-01 open Income:Zq:Salary USD
2026-08-01 open Expenses:Zq:Groceries USD
2026-08-01 open {RETIREMENT} USD

2026-08-01 * "Opening"
  {CHECKING}  1000.00 USD
  Equity:Zq:Opening

2026-09-01 * "Zq Payroll" ""
  {CHECKING}  2500.00 USD
  {RETIREMENT}  500.00 USD
  Income:Zq:Salary  -3000.00 USD

2026-09-12 * "Zq Grocer" ""
  {CHECKING}  -200.00 USD
  Expenses:Zq:Groceries

2026-10-01 balance {CHECKING}  3300.00 USD
"""
"""Avery's September: 3,000.00 pay, of which 500.00 went to a retirement account before it
reached the checking account, booked as an expense, and 200.00 on groceries."""


@pytest.fixture
def household(tmp_path: Path, clock: FakeClock) -> FirstDay:
    clock.now = noon(TODAY)
    ledger = tmp_path / "main.beancount"
    ledger.write_text(LEDGER)
    fresh = first_day(tmp_path, clock, inherits=ledger)
    conn = fresh.connect()
    try:
        accounts.claim(
            conn,
            ledger_account=CHECKING,
            name="Checking",
            owners=[fresh.member_ids["avery"]],
            now=clock(),
        )
    finally:
        conn.close()
    fresh.bookkeeper.recompute(wait=True)
    return fresh


def _view(fresh: FirstDay) -> tuple[Decimal, Decimal, dict[str, Decimal]]:
    """Avery's September as the Household page reads it: money in, money out, and where it
    went, by category name."""
    fresh.bookkeeper.recompute(wait=True)
    conn = fresh.connect()
    try:
        viewer = find(conn, "avery")
        assert viewer is not None
        view = Disclosure.load(conn, viewer, fresh.bookkeeper.status).household()
    finally:
        conn.close()
    last = view.left_over.last
    assert last.month == Month(2026, 9)
    assert last.covered
    return last.money_in, last.money_out, {row.name: row.amount for row in view.went.rows}


def test_a_move_booked_as_an_expense_is_not_spending_and_is_in_what_was_left_over(
    household: FirstDay,
) -> None:
    money_in, money_out, went = _view(household)

    assert (money_in, money_out) == (Decimal("3000.00"), Decimal("200.00"))
    assert went == {"Groceries": Decimal("200.00")}
    # Every expense is either spending or kept: nothing is lost from the figures.
    found = household.books()
    month = figures.month_flows(found, ownership(_accounts(household)), Month(2026, 9))
    assert month.kept is not None
    owners = frozenset({household.member_ids["avery"]})
    assert month.kept.by_owners[owners] == {RETIREMENT: Decimal("500.00")}
    assert month.spent(owners) + sum(month.kept.by_owners[owners].values()) == Decimal("700.00")
    assert money_in - money_out == Decimal("2800.00")
    assert found.kind(RETIREMENT) is Kind.NOT_SPENDING
    assert found.kinds["Expenses:Zq:Groceries"] is Kind.SPENDING
    assert found.kinds["Income:Zq:Salary"] is Kind.PAY


def _accounts(fresh: FirstDay) -> list[accounts.Account]:
    conn = fresh.connect()
    try:
        return accounts.all_accounts(conn)
    finally:
        conn.close()


def test_a_kind_a_person_sets_changes_the_totals_and_forgetting_it_guesses_again(
    household: FirstDay,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(household.settings.data_dir))
    monkeypatch.setenv(LEDGER_VARIABLE, str(household.settings.ledger))

    main(["categories"])
    assert capsys.readouterr().out.splitlines() == [
        "Expenses:Zq:Groceries  spending  guessed",
        f"{RETIREMENT}  not-spending  guessed",
        "Income:Zq:Salary  pay  guessed",
    ]

    before = books.standing(household.settings)
    main(["categories", "--kind", f"{RETIREMENT}=spending"])
    assert f"{RETIREMENT}  spending  set" in capsys.readouterr().out.splitlines()
    # A serving hearth notices, as the command runs in a process of its own.
    assert books.standing(household.settings) != before
    household.clock.advance(timedelta(minutes=1))
    _, money_out, went = _view(household)
    assert money_out == Decimal("700.00")
    assert went == {"Groceries": Decimal("200.00"), "Retirement": Decimal("500.00")}

    main(["categories", "--guess", RETIREMENT])
    assert f"{RETIREMENT}  not-spending  guessed" in capsys.readouterr().out.splitlines()
    assert _view(household)[1] == Decimal("200.00")


@pytest.mark.parametrize(
    ("given", "refusal"),
    [
        (["--kind", "Income:Zq:Salary=not-spending"], "is one of: pay, investment-income"),
        (["--kind", "Expenses:Zq:Groceries=pay"], "is one of: spending, tax, not-spending"),
        (["--kind", "Expenses:Zq:Nowhere=tax"], "the books name no income or expense account"),
        (["--kind", f"{CHECKING}=spending"], "the books name no income or expense account"),
        (["--kind", "Expenses:Zq:Groceries=lavish"], "is not a kind of category"),
        (["--guess", "Expenses:Zq:Nowhere"], "the books name no income or expense account"),
    ],
)
def test_the_command_refuses_what_is_not_so_and_changes_nothing(
    household: FirstDay, monkeypatch: pytest.MonkeyPatch, given: list[str], refusal: str
) -> None:
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(household.settings.data_dir))
    monkeypatch.setenv(LEDGER_VARIABLE, str(household.settings.ledger))

    with pytest.raises(SystemExit, match=refusal):
        main(["categories", "--kind", "Expenses:Zq:Groceries=tax", *given])

    conn = household.connect()
    try:
        assert categories.stored(conn) == {}
    finally:
        conn.close()


def test_a_kind_is_stored_only_where_a_person_set_one(household: FirstDay) -> None:
    conn = household.connect()
    try:
        assert categories.stored(conn) == {}
        categories.set_kind(conn, "Income:Zq:Salary", Kind.OTHER_INCOME, now=noon(TODAY))
        assert categories.stored(conn) == {"Income:Zq:Salary": Kind.OTHER_INCOME}
        assert categories.forget(conn, "Income:Zq:Salary")
        assert not categories.forget(conn, "Income:Zq:Salary")
        with pytest.raises(NotACategory):
            categories.set_kind(conn, "Income:Zq:Salary", Kind.TAX, now=noon(TODAY))
    finally:
        conn.close()
