"""Tax on the household page: its own figure beside spending, a refund said as one, and what
was left over said truly whatever tax came to."""

import re

from tests.conftest import World


def book(world: World, text: str) -> None:
    with world.ledger.open("a") as ledger:
        ledger.write(text)
    world.recompute()
    assert world.bookkeeper.status.failure is None, world.bookkeeper.status.failure


def plain(page: str) -> str:
    """The page's words, without its markup."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))


REFUNDED = """
2026-08-01 open Expenses:Taxes
2026-09-08 * "Payee Tax Office" ""
  Assets:Zq:AverySolo  -100.00 USD
  Expenses:Taxes
2026-09-18 * "Payee Tax Office" ""
  Assets:Zq:AverySolo  400.00 USD
  Expenses:Taxes
"""


def test_a_refund_larger_than_the_tax_is_said_as_a_refund(world: World) -> None:
    """September: 100 of tax paid and 400 refunded. The left-over panel adds the 300 back as
    tax refunded, never "less tax" of a negative amount; the chart's tip says so too."""
    book(world, REFUNDED)

    page = world.signed_in("avery").get("/").text
    words = plain(page)

    assert "Tax refunded" in words
    assert "More tax back than paid" in words
    assert "tax refunded $300" in page
    assert "tax $-300" not in page
    assert "tax -$300" not in page
    assert not re.search(r"&minus; </span>Tax</p>", page)


TAXED_ABOVE_PAY = """
2026-08-01 open Expenses:Taxes
2026-08-01 open Income:Salary
2026-09-08 * "Payee Employer" ""
  Assets:Zq:AverySolo  100.00 USD
  Income:Salary
2026-09-18 * "Payee Tax Office" ""
  Assets:Zq:AverySolo  -500.00 USD
  Expenses:Taxes
"""


def test_tax_above_money_in_leaves_a_share_of_money_in_that_is_true(world: World) -> None:
    """September: 100 came in and 500 went to tax. There was money in, so the panel never says
    there was none: what was left is a share of money in, below nothing."""
    book(world, TAXED_ABOVE_PAY)

    words = plain(world.signed_in("avery").get("/").text)

    assert "No money in that month" not in words
    assert re.search(r"\S?\d+% of money in", words), words
