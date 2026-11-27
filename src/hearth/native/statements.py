"""The statement screens: what each is asked with, and how its view is written for the app.

Each is written from ``Disclosure``'s statement views and nothing else, which read only the
parts the viewer may see in full (``hearth.disclosure.statements``). What an ask is given is
read strictly here; whether the viewer may see what it reads is never decided here.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from hearth.disclosure import Disclosure
from hearth.disclosure.statements import (
    LABELS,
    BalanceSheet,
    Bridge,
    Columns,
    Held,
    IncomeStatement,
    Line,
    Node,
    Period,
    Scope,
    SheetRow,
    Summary,
    Unreviewed,
    Valuing,
    Years,
    share,
)
from hearth.native.common import DAY, Answer, Around, Given, Malformed, only, text_in
from hearth.native.common import day as _day
from hearth.native.common import money as _money

VALUATIONS = {"market": True, "cost": False}
"""How holdings are valued: at the latest price on or before the day, or at what they cost."""


@dataclass(frozen=True, slots=True)
class Asked:
    """What a statement was asked for: which days, how holdings are valued, and for the
    income statement, how its period is split."""

    start: date | None
    end: date | None
    market: bool = True
    by: Columns = Columns.MONTH


def _days(given: Given) -> tuple[date | None, date | None]:
    start, end = _day_in(given, "from"), _day_in(given, "to")
    if start is not None and end is not None and start > end:
        raise Malformed
    return start, end


def _day_in(given: Given, key: str) -> date | None:
    value = given.get(key)
    if value is None and key not in given:
        return None
    if not isinstance(value, str) or not DAY.fullmatch(value):
        raise Malformed
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise Malformed from None


def _valued(given: Given) -> bool:
    valuation = text_in(given, "valuation", 16)
    if valuation is None:
        return True
    if valuation not in VALUATIONS:
        raise Malformed
    return VALUATIONS[valuation]


def read_valued(given: Given) -> Asked:
    """A summary or balance sheet: `from`, `to` and `valuation`."""
    only(given, frozenset({"from", "to", "valuation"}))
    start, end = _days(given)
    return Asked(start, end, _valued(given))


def read_columns(given: Given) -> Asked:
    """An income statement: `from`, `to` and `columns`."""
    only(given, frozenset({"from", "to", "columns"}))
    start, end = _days(given)
    by = text_in(given, "columns", 16)
    if by is not None and by not in {c.value for c in Columns}:
        raise Malformed
    return Asked(start, end, by=Columns(by) if by is not None else Columns.MONTH)


def read_years(given: Given) -> Asked:
    """Year by year: `valuation` alone."""
    only(given, frozenset({"valuation"}))
    return Asked(None, None, _valued(given))


def summary_screen(view: Disclosure, asked: object, _around: Around) -> Answer:
    assert isinstance(asked, Asked)  # noqa: S101 - read by `read_valued`
    found = view.statement_summary(asked.start, asked.end, market=asked.market)
    return _summary(found, asked.market)


def income_statement_screen(view: Disclosure, asked: object, _around: Around) -> Answer:
    assert isinstance(asked, Asked)  # noqa: S101 - read by `read_columns`
    return _income(view.income_statement(asked.start, asked.end, asked.by))


def balance_sheet_screen(view: Disclosure, asked: object, _around: Around) -> Answer:
    assert isinstance(asked, Asked)  # noqa: S101 - read by `read_valued`
    return _sheet(view.balance_sheet(asked.start, asked.end, market=asked.market))


def years_screen(view: Disclosure, asked: object, _around: Around) -> Answer:
    assert isinstance(asked, Asked)  # noqa: S101 - read by `read_years`
    return _years(view.years(market=asked.market))


def _period(period: Period) -> Answer:
    return {"from": _day(period.start), "to": _day(period.end)}


def _scope(scope: Scope) -> Answer:
    return {
        "parts": [part.name for part in scope.refs],
        "without": [part.name for part in scope.without],
        "leaves_out": scope.leaves_out or None,
        "awaiting": len(scope.unknown),
    }


def _valuing(valuing: Valuing) -> Answer:
    return {
        "priced_on": _day(valuing.priced_on),
        "unpriced": list(valuing.unpriced),
        "unvalued": list(valuing.unvalued),
        "stale": valuing.stale,
        # Amounts booked before anything priced them, converted at the first price there was.
        "later": valuing.later,
    }


def _unreviewed(found: Unreviewed) -> Answer:
    return {
        "rows": found.rows,
        "income": _money(found.income),
        "expenses": _money(found.expenses),
        "unplaced": found.unplaced,
        "unplaced_out": _money(found.unplaced_out),
        "unplaced_in": _money(found.unplaced_in),
    }


def _line(line: Line) -> Answer:
    return {
        "key": line.key,
        "label": line.label,
        "now": _money(line.now),
        "before": _money(line.before),
        "change": _money(line.change),
        "share": _money(share(line.now, line.before)),
        "tone": line.tone.value,
        "categories": list(line.categories),
        "total": line.total,
        "depth": line.depth,
    }


def _why(found: Bridge | None) -> Answer | None:
    if found is None:
        return None
    return {
        "start": _money(found.start),
        "saved": _money(found.saved),
        "investment_income": _money(found.investment_income),
        "market": _money(found.movement),
        "other": _money(found.other),
        "others": [
            {"key": key.value, "label": LABELS[key], "amount": _money(amount)}
            for key, amount in found.others.items()
        ],
        "end": _money(found.end),
        "difference": _money(found.difference),
    }


def _summary(found: Summary, market: bool) -> Answer:
    return {
        "period": _period(found.period),
        "against": _period(found.against),
        "valuation": "market" if market else "cost",
        "covered": found.covered,
        "against_covered": found.against_covered,
        "scope": _scope(found.scope),
        "net_worth": _line(found.net_worth),
        "savings_rate": _line(found.savings_rate),
        "spending": _line(found.spending),
        "market": _money(found.market),
        "lines": [_line(line) for line in found.lines],
        "why": _why(found.bridge),
        "valuing": _valuing(found.valuing),
        "unreviewed": _unreviewed(found.unreviewed),
        # Net worth at least this much: it leaves out holdings with no price.
        "is_floor": found.floor,
        # Accounts whose holdings are known only from inside the period, which net worth's
        # start, and why it changed, are without.
        "late": [account.name for account in found.late],
    }


def _node(node: Node) -> Answer:
    return {
        "name": node.name,
        "label": node.label,
        "kind": node.kind.value if node.kind is not None else None,
        "cells": [_money(cell) for cell in node.cells],
        "total": _money(node.total),
        "children": [_node(child) for child in node.children],
    }


def _income(found: IncomeStatement) -> Answer:
    return {
        "period": _period(found.period),
        "columns_by": found.by.value,
        "covered": found.covered,
        "scope": _scope(found.scope),
        "columns": [
            {
                **_period(column.period),
                "partial": column.partial,
                "unreviewed": _unreviewed(column.unreviewed),
            }
            for column in found.columns
        ],
        "income": _node(found.income),
        "expenses": _node(found.expenses),
        "net": {"cells": [_money(n) for n in found.net], "total": _money(found.net_total)},
        "unreviewed": _unreviewed(found.unreviewed),
        "later": found.later,
    }


def _held(held: Held | None, paid: Decimal | None, market: bool) -> Answer | None:
    """What a row held: its value as asked, and what it cost by its lots with the gain not
    yet realised over that, as Holdings says them; null where nothing says what it cost."""
    if held is None:
        return None
    return {
        "value": _money(held.value(market)),
        "cost": _money(paid),
        # A holding with no price has no gain to tell, only its cost, as Holdings says.
        "gain": _money(held.market - paid) if paid is not None and not held.unpriced else None,
        "securities": held.securities,
        # A market value without holdings no screen can price is at least what it says.
        "is_floor": market and held.is_floor,
    }


def _row(row: SheetRow, market: bool) -> Answer:
    change = (
        row.end.value(market) - row.start.value(market)
        if row.start is not None and row.end is not None
        else None
    )
    return {
        "label": row.label,
        "account": (
            {"id": row.account.id, "name": row.account.name} if row.account is not None else None
        ),
        "since": _day(row.since),
        "start": _held(row.start, row.paid[0], market),
        "end": _held(row.end, row.paid[1], market),
        "change": _money(change),
        "children": [_row(child, market) for child in row.children],
    }


def _sheet(found: BalanceSheet) -> Answer:
    return {
        "period": _period(found.period),
        "valuation": "market" if found.market else "cost",
        "scope": _scope(found.scope),
        "assets": _row(found.assets, found.market),
        "liabilities": _row(found.liabilities, found.market),
        "net_worth": _row(found.net_worth, found.market),
        "valuing": _valuing(found.valuing),
    }


def _years(found: Years) -> Answer:
    return {
        "valuation": "market" if found.market else "cost",
        "scope": _scope(found.scope),
        "years": [
            {
                "year": year.year,
                "partial": year.partial,
                "sparse": year.sparse,
                "net_worth": _money(year.net_worth),
                "pay": _money(year.pay),
                "spending": _money(year.spending),
                "saved": _money(year.saved),
                "savings_rate": _money(year.savings_rate),
            }
            for year in found.years
        ],
    }
