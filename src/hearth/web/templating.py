"""Jinja rendering, with the number and date formatting every page shares.

The rules follow the design system (``design/system.html``, Numbers): aggregates in whole
dollars, line items to the cent, a true minus sign everywhere, and the dollar sign set small
on hero figures.
"""

from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup, escape

from hearth.connections import PLAID
from hearth.disclosure import Withheld, left_out
from hearth.months import Month
from hearth.names import readable

_CENT = Decimal("0.01")
_DOLLAR = Decimal(1)
MINUS = "\N{MINUS SIGN}"


def _sign(amount: Decimal) -> str:
    return MINUS if amount < 0 else ""


def money(amount: Decimal) -> str:
    """Dollars to the cent, negatives with a true minus: "$820.45"."""
    rounded = amount.quantize(_CENT, rounding=ROUND_HALF_EVEN)
    return f"{_sign(rounded)}${abs(rounded):,.2f}"


def dollars(amount: Decimal) -> str:
    """Whole dollars, for aggregates: "$611,295"."""
    rounded = amount.quantize(_DOLLAR, rounding=ROUND_HALF_UP)
    return f"{_sign(rounded)}${abs(rounded):,.0f}"


def hero(amount: Decimal) -> Markup:
    """A hero figure in whole dollars with the currency sign set small, as the design draws it."""
    rounded = amount.quantize(_DOLLAR, rounding=ROUND_HALF_UP)
    return Markup('{}<span class="cur">$</span>{}').format(_sign(rounded), f"{abs(rounded):,.0f}")


def amount(value: Decimal) -> str:
    """A transaction's amount, signed, without the currency: "+3,299.60"."""
    rounded = value.quantize(_CENT, rounding=ROUND_HALF_EVEN)
    sign = MINUS if rounded < 0 else "+" if rounded > 0 else ""
    return f"{sign}{abs(rounded):,.2f}"


def compact(value: Decimal) -> str:
    """A short label: "$8.2K", "$1.3M"."""
    size = abs(value)
    for limit, suffix in ((Decimal(1_000_000), "M"), (Decimal(1_000), "K")):
        if size >= limit:
            return f"{_sign(value)}${size / limit:.1f}{suffix}"
    return dollars(value)


def units(value: Decimal) -> str:
    """Security units to three decimals: "1,284.301"."""
    return f"{_sign(value)}{abs(value):,.3f}"


def percent(value: Decimal) -> str:
    return f"{(value * 100).quantize(_DOLLAR, rounding=ROUND_HALF_UP):,.0f}"


def day(value: date) -> str:
    """ "Oct 17"."""
    return f"{value:%b} {value.day}"


def full_day(value: date) -> str:
    """ "Saturday, Oct 17"."""
    return f"{value:%A}, {value:%b} {value.day}"


def year_day(value: date) -> str:
    """ "Oct 18, 2024"."""
    return f"{value:%b} {value.day}, {value.year}"


def month_year(value: date | Month) -> str:
    """ "Oct 2024"."""
    return f"{date(value.year, value.month, 1):%b %Y}"


def clock_time(value: datetime) -> str:
    """ "6:00 AM", in the household machine's own time zone."""
    local = value.astimezone()
    return f"{local:%I:%M %p}".lstrip("0")


def when(value: datetime, today: date) -> str:
    """ "today, 6:00 AM", "yesterday, Oct 17, 6:00 AM" or "Oct 12, 6:00 AM"."""
    local = value.astimezone()
    days = (today - local.date()).days
    if days == 0:
        return f"today, {clock_time(value)}"
    if days == 1:
        return f"yesterday, {day(local.date())}, {clock_time(value)}"
    return f"{day(local.date())}, {clock_time(value)}"


def close(price_day: date, today: date) -> str:
    """Which close a price is: "Fri close" within the last week, "Oct 13 close" before."""
    if 0 <= (today - price_day).days < 7:
        return f"{price_day:%a} close"
    return f"{day(price_day)} close"


def category(ledger_account: str | None) -> str:
    """What a transaction was, as the design labels it.

    Spending reads as its most specific category ("Expenses:Food:Dining" is "Dining"), money
    earned as "Income", and a move between balance-sheet accounts as "Transfer".
    """
    if not ledger_account:
        return "Transfer"
    if ledger_account.startswith("Income:"):
        return "Income"
    return readable(ledger_account.rsplit(":", 1)[-1])


def origin(value: str) -> str:
    """Where a span of an account's history came from: "bank feed" for rows booked from a
    connection, otherwise its origin as words ("bank-feed" is "bank feed")."""
    return "bank feed" if value == PLAID else value.replace("-", " ")


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural_form or singular + 's'}"


def names(items: Any, attribute: str = "display_name") -> Markup:
    """ "Alex", "Alex and Sam", "Alex, Sam and Jo", escaped."""
    words = [str(escape(getattr(item, attribute))) for item in items]
    if len(words) < 3:
        return Markup(" and ".join(words))  # noqa: S704 - every part was escaped above
    return Markup(", ".join(words[:-1]) + " and " + words[-1])  # noqa: S704 - escaped above


PRICE_SOURCES = {
    "plaid": "each brokerage's own price, through Plaid",
    "eodhd": "EODHD",
    "hand": "entered by hand",
    "demo": "invented for the demo",
}
"""What a page calls each source a price is stored under (``books.PRICE_SOURCES``)."""


def price_source(source: str) -> str:
    return PRICE_SOURCES.get(source, source)


def _is_withheld(value: object) -> bool:
    return isinstance(value, Withheld)


environment = Environment(
    loader=FileSystemLoader(Path(__file__).parent.parent / "templates"),
    autoescape=select_autoescape(default=True, default_for_string=True),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
environment.filters.update(
    money=money,
    dollars=dollars,
    hero=hero,
    amount=amount,
    compact=compact,
    units=units,
    percent=percent,
    day=day,
    full_day=full_day,
    year_day=year_day,
    month_year=month_year,
    clock_time=clock_time,
    category=category,
    origin=origin,
    names=names,
    left_out=left_out,
    price_source=price_source,
)
environment.globals.update(when=when, close=close, plural=plural, MINUS=MINUS)
environment.tests["withheld"] = _is_withheld


def render(
    request: Request, template: str, *, status_code: int = 200, **context: Any
) -> HTMLResponse:
    context.setdefault("session", None)
    body = environment.get_template(template).render(request=request, **context)
    return HTMLResponse(body, status_code=status_code)
