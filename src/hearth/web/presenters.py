"""Charts and small presentation details derived from disclosed views.

Everything here works on views ``hearth.disclosure`` has already built, so it can only draw
what the viewer may see. It decides nothing.
"""

from datetime import date
from decimal import Decimal

from markupsafe import Markup

from hearth.disclosure import Disclosure, HouseholdView, NetWorthView, Pace, PersonView
from hearth.members import Member
from hearth.web import charts
from hearth.web.templating import compact, dollars

YEAR_TICK_EVERY = 3


def hue_of(view: Disclosure, member: Member) -> int:
    ref = view.ref(member.id)
    return ref.hue if ref else 1


def household_page(view: HouseholdView) -> dict[str, object]:
    """Everything the household template draws beyond the view itself: charts and labels."""
    spending = view.spending
    month = date(spending.month.year, spending.month.month, 1)
    last_full = date(spending.saved_month.year, spending.saved_month.month, 1)
    bars = spending.bars
    return {
        "charts": household_charts(view),
        "since": f"since {month:%b} 1",
        "month_name": f"{month:%B}",
        "month_abbr": f"{month:%b}",
        "saved_month_name": f"{last_full:%B}",
        "ordinal_today": ordinal(spending.to_day),
        "first_bar_label": _month_label(bars[0].month.year, bars[0].month.month, True),
        "last_bar_label": _month_label(bars[-1].month.year, bars[-1].month.month, True),
        "history_rows": [
            (
                _day_label(point.day, with_year=i == 0 or point.day.month == 1)
                + (" (today)" if i == len(view.history) - 1 else ""),
                ("at least " if point.is_floor else "") + dollars(point.value),
            )
            for i, point in enumerate(view.history)
        ],
        "spending_rows": [
            (
                _month_label(bar.month.year, bar.month.month, i == 0 or bar.month.month == 1)
                + (f", to the {ordinal(spending.to_day)}" if i == len(bars) - 1 else "")
                + ("" if bar.covered else " (history incomplete)"),
                dollars(bar.spent),
            )
            for i, bar in enumerate(bars)
        ],
    }


def household_charts(view: HouseholdView) -> dict[str, Markup]:
    out = {"net_worth": _month_end_line(view, "nw-t")} if view.history else {}
    bars = view.spending.bars
    points = [
        charts.ChartPoint(
            on=date(bar.month.year, bar.month.month, 1),
            value=bar.spent,
            axis_label=f"{date(bar.month.year, bar.month.month, 1):%b}",
            tip=dollars(bar.spent),
            tip_label=_month_label(bar.month.year, bar.month.month, i == 0 or bar.month.month == 1)
            + (f", to the {ordinal(view.spending.to_day)}" if i == len(bars) - 1 else "")
            + ("" if bar.covered else ", history incomplete"),
            incomplete=not bar.covered,
        )
        for i, bar in enumerate(bars)
    ]
    out["spending"] = charts.monthly_bars(
        points,
        labelled_by="sp-t",
        ticks=_ticks(len(points)),
        median=view.spending.typical,
        current=len(points) - 1,
        current_label=compact(bars[-1].spent) if view.spending.covered else "\u2013",
    )
    return out


def _month_end_line(view: HouseholdView, labelled_by: str) -> Markup:
    history = view.history
    last = len(history) - 1
    points = [
        charts.ChartPoint(
            on=point.day,
            value=point.value,
            axis_label=_axis_day(point.day, last_point=i == last),
            tip=("at least " if point.is_floor else "") + dollars(point.value),
            tip_label=_day_label(point.day, with_year=i == 0 or point.day.month == 1)
            + (" (today)" if i == last else ""),
        )
        for i, point in enumerate(history)
    ]
    return charts.month_end_line(points, labelled_by=labelled_by, ticks=_ticks(len(points)))


def person_page(view: PersonView) -> dict[str, object]:
    """Labels and charts for a member's page, whoever is looking at it."""
    month = date(view.totals.month.year, view.totals.month.month, 1)
    return {
        "charts": person_charts(view),
        "since": f"since {month:%b} 1",
        "month_name": f"{month:%B}",
        "month_abbr": f"{month:%b}",
        "ordinal_today": ordinal(view.totals.to_day),
    }


def person_charts(view: PersonView) -> dict[str, Markup]:
    pace = view.pace
    if not isinstance(pace, Pace):
        return {}
    days = pace.today
    month = date(view.totals.month.year, view.totals.month.month, 1)
    return {
        "pace": charts.cumulative_pace(
            pace.days_in_month,
            pace.actual,
            pace.typical,
            pace.low,
            pace.high,
            labelled_by="pace-t",
            today=days,
            today_label=dollars(pace.actual[-1]) if pace.actual else "$0",
            month_abbr=f"{month:%b}",
            tips=[(dollars(v), f"{month:%b} {d + 1}") for d, v in enumerate(pace.actual)],
        )
    }


def net_worth_page(view: NetWorthView, today: date) -> dict[str, object]:
    month = today.replace(day=1)
    moves = [m for m in view.moves if m.saved is not None and m.market is not None]
    saved = sum((m.saved or Decimal(0) for m in moves), Decimal(0))
    market = sum((m.market or Decimal(0) for m in moves), Decimal(0))
    finding = None
    if moves:
        who = "your accounts" if len(view.moves_cover) == 1 else "the accounts you can see into"
        put = f"{dollars(abs(saved))} {'put in' if saved >= 0 else 'taken out'}"
        priced = (
            "none of it from the market"
            if market == 0
            else f"{dollars(abs(market))} {'from' if market > 0 else 'lost to'} the market"
        )
        changed = dollars(saved + market)
        finding = f"Over {len(moves)} months, {who} changed by {changed}: {put}, {priced}."
    return {
        "charts": net_worth_charts(view),
        "since": f"since {month:%b} 1",
        "moves_finding": finding,
        "moves_rows": [
            (_day_label(m.month_end, with_year=False), f"{dollars(m.saved)} / {dollars(m.market)}")
            for m in moves
            if m.saved is not None and m.market is not None
        ],
        "invested_total": sum(
            (
                p.value
                for h in view.holdings
                if any(not q.is_cash for q in h.held)
                for p in h.held
                if p.value is not None
            ),
            Decimal(0),
        ),
        "names_by_symbol": view.names,
    }


def net_worth_charts(view: NetWorthView) -> dict[str, Markup]:
    moves = view.moves
    points = [
        charts.MovePoint(
            axis_label=f"{move.month_end:%b}",
            saved=saved,
            market=market,
            tip=f"{dollars(saved)} saved, {dollars(market)} market",
            tip_label=_day_label(move.month_end, with_year=False),
        )
        for move in moves
        if (saved := move.saved) is not None and (market := move.market) is not None
    ]
    if not points:
        return {}
    return {"moves": charts.saved_vs_market(points, labelled_by="mv-t")}


def _ticks(count: int) -> list[int]:
    """Label the first point, every third after it, and the last."""
    ticks = list(range(0, count, YEAR_TICK_EVERY))
    if count - 1 not in ticks:
        if ticks and count - 1 - ticks[-1] < 2:
            ticks.pop()
        ticks.append(count - 1)
    return ticks


def _axis_day(day: date, *, last_point: bool) -> str:
    return f"{day:%b} {day.day}" if last_point else f"{day:%b}"


def _day_label(day: date, *, with_year: bool) -> str:
    return f"{day:%b} {day.day}" + (f", {day.year}" if with_year else "")


def _month_label(year: int, month: int, with_year: bool) -> str:
    return f"{date(year, month, 1):%b}" + (f" {year}" if with_year else "")


def ordinal(day: int) -> str:
    """ "18th", "1st"."""
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"
