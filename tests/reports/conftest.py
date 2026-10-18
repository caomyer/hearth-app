"""A synthetic two-member household: sole, joint and unassigned accounts, with assertions."""

import pytest
from beancount import loader
from beancount.core.data import Directive

LEDGER = """
option "operating_currency" "USD"

2026-01-01 open Assets:Alex:Checking USD
2026-01-01 open Assets:Joint:Checking USD
2026-01-01 open Assets:Unassigned:Savings USD
2026-01-01 open Liabilities:Blair:Card USD
2026-01-01 open Income:Salary
2026-01-01 open Expenses:Fees
2026-01-01 open Expenses:Food
2026-01-01 open Expenses:Rent
2026-01-01 open Expenses:Travel
2026-01-01 open Equity:Opening
2026-06-01 open Assets:Blair:Brokerage
2026-01-01 open Assets:Old:Savings USD
2026-03-31 close Assets:Old:Savings

2026-01-01 * "Opening balances"
  source: "statement-a:1"
  Assets:Alex:Checking  1000.00 USD
  Assets:Joint:Checking  2000.00 USD
  Assets:Unassigned:Savings  500.00 USD
  Equity:Opening

2026-01-15 * "Pay"
  source: "statement-a:2"
  Assets:Alex:Checking  3000.00 USD
  Income:Salary  -3000.00 USD

2026-01-20 * "Rent"
  source: "statement-j:1"
  Assets:Joint:Checking  -1500.00 USD
  Expenses:Rent

2026-01-22 * "Groceries"
  source: "statement-b:1"
  Liabilities:Blair:Card  -80.25 USD
  Expenses:Food

2026-01-25 * "Groceries"
  source: "statement-a:3"
  Assets:Alex:Checking  -40.10 USD
  Expenses:Food

2026-02-01 balance Assets:Alex:Checking  3959.90 USD
2026-02-01 balance Assets:Joint:Checking  500.00 USD
2026-02-01 balance Liabilities:Blair:Card  -80.25 USD

2026-02-03 ! "Groceries, not yet reviewed"
  source: "statement-j:2"
  Assets:Joint:Checking  -33.33 USD
  Expenses:Food

2026-02-10 * "Snack"
  source: "statement-u:1"
  Assets:Unassigned:Savings  -5.00 USD
  Expenses:Food

2026-02-12 * "Hotel abroad"
  source: "statement-j:3"
  Assets:Joint:Checking  -110.00 USD
  Expenses:Travel  100.00 EUR @ 1.10 USD

2026-03-05 * "Transfer to the joint account, with a fee"
  source: "statement-a:4"
  Assets:Alex:Checking  -201.00 USD
  Assets:Joint:Checking  200.00 USD
  Expenses:Fees  1.00 USD

2026-04-20 * "Groceries"
  source: "feed-a:1"
  Assets:Alex:Checking  -10.00 USD
  Expenses:Food

2026-05-01 balance Assets:Alex:Checking  3748.90 USD
2026-05-01 balance Assets:Joint:Checking  556.67 USD
"""


@pytest.fixture(scope="session")
def entries() -> list[Directive]:
    loaded, errors, _ = loader.load_string(LEDGER)
    assert errors == []
    return loaded
