"""Jinja rendering, with the formatting every page shares."""

from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from hearth.disclosure import Withheld

_CENT = Decimal("0.01")
_MINUS = "\N{MINUS SIGN}"


def money(amount: Decimal) -> str:
    """Dollars to the cent, grouped by thousands, with a typographic minus (U+2212)."""
    rounded = amount.quantize(_CENT, rounding=ROUND_HALF_EVEN)
    sign = _MINUS if rounded < 0 else ""
    return f"{sign}${abs(rounded):,.2f}"


def signed_money(amount: Decimal) -> str:
    """Like ``money``, but marks gains with a plus sign."""
    text = money(amount)
    return text if text.startswith(_MINUS) or amount.is_zero() else f"+{text}"


def category(ledger_account: str) -> str:
    """A ledger category as people say it, without the "Expenses:" or "Income:" root."""
    parts = ledger_account.split(":")
    return " \N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} ".join(
        parts[1:] if parts[0] in {"Expenses", "Income"} and len(parts) > 1 else parts
    )


def day(value: date) -> str:
    return f"{value:%b} {value.day}"


def moment(value: datetime) -> str:
    return f"{value:%Y-%m-%d %H:%M} UTC"


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
    money=money, signed_money=signed_money, category=category, day=day, moment=moment
)
environment.tests["withheld"] = _is_withheld


def render(
    request: Request, template: str, *, status_code: int = 200, **context: Any
) -> HTMLResponse:
    context.setdefault("session", None)
    body = environment.get_template(template).render(request=request, **context)
    return HTMLResponse(body, status_code=status_code)
