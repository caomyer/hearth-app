"""Chart SVG for the pages, drawn on the server to the design system's own markup.

The approved charts are hand-placed SVG in ``design/screens``: household.html draws
net worth at each month end, me.html draws a month's spending
day by day against a typical month, and net-worth.html splits each month's change
into money saved and market movement. Each function here emits the elements those
files use, in the same order and with the same classes, so ``design/hearth.css``
styles the app's charts exactly as it styles the design's.

Every chart comes twice, as in the design: a wide SVG and a narrow one, which the
stylesheet swaps at 640px. The functions only place what they are given. Callers
decide what a chart may show and format the money in its tooltips; only the axes
are formatted here, compactly and rounded to the gridline ("$1.25M", "$10K").

Labels carry category names and other words a member typed, so every piece of
text and every attribute is escaped on its way into the markup.
"""

import calendar
import itertools
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from markupsafe import Markup, escape

_MINUS = "\N{MINUS SIGN}"
_TIP_SEPARATOR = "|"  # design.js splits data-tip into value and label here
_BAR_WIDTH = 18.0  # "bars no wider than 18px"
_RADIUS = 3.0
_DOT_RADIUS = "4.5"
_GAP = 2.0  # between a market segment and the saving it moves against, or money in and out
_PAIR_WIDTH = 12.6  # each of a month's two bars, money in and money out
_PAIR_GAP = 3.0  # either side of a month's pair of bars
_STEPS = 4  # gridlines a scale aims for
_CHAR_WIDTH = 6  # an 11px tabular figure, rounded up
_LABEL_SPACE = 30.0  # the narrowest band that still fits a month name
_UNITS = ((Decimal(10**6), "M"), (Decimal(1000), "K"))


@dataclass(frozen=True, slots=True)
class ChartPoint:
    """One month of a month-by-month chart.

    ``on`` is the day the value stands for: a month end, or today for the month in
    progress. ``tip`` and ``tip_label`` are shown on hover exactly as given.
    """

    on: date
    value: Decimal
    axis_label: str
    tip: str
    tip_label: str
    incomplete: bool = False  # history is missing for part of the month


@dataclass(frozen=True, slots=True)
class MovePoint:
    """One month's change in net worth, split into money put in and prices moving."""

    axis_label: str
    saved: Decimal
    market: Decimal
    tip: str
    tip_label: str


@dataclass(frozen=True, slots=True)
class InOutPoint:
    """One whole month of money in and money out, and what was left.

    A month that is not ``known`` is drawn in outline at the height of what the books hold of
    it, with no point on the left-over line; its tooltip is the caller's, and should carry no
    value, as the figure is not the month's.
    """

    axis_label: str
    money_in: Decimal
    money_out: Decimal
    tip: str
    tip_label: str
    known: bool = True

    @property
    def left_over(self) -> Decimal:
        return self.money_in - self.money_out


@dataclass(frozen=True, slots=True)
class _Frame:
    """Where the plot sits inside one SVG's viewBox."""

    width: int
    height: int
    left: int
    right: int
    top: int
    bottom: int

    @property
    def label_y(self) -> int:
        return self.height - 8


@dataclass(frozen=True, slots=True)
class _Scale:
    lo: Decimal
    hi: Decimal
    ticks: tuple[Decimal, ...]
    top: int
    bottom: int

    def y(self, value: Decimal) -> float:
        share = (value - self.lo) / (self.hi - self.lo)
        return self.bottom - float(share) * (self.bottom - self.top)


@dataclass(frozen=True, slots=True)
class _Segment:
    css: str
    inner: float
    outer: float
    rising: bool


# ---------- public charts ----------


def month_end_line(
    points: Sequence[ChartPoint], *, labelled_by: str, ticks: Sequence[int]
) -> Markup:
    """Household net worth at each month end, the last point being today (household.html)."""
    _require_points(points)
    _require_indices(ticks, len(points))
    _require_ascending([p.on for p in points])
    scale = _scale([p.value for p in points], top=10, bottom=172, from_zero=False)
    labels = _axis_labels(scale.ticks)
    left = _left_margin(labels, 46)

    def draw(frame: _Frame) -> list[str]:
        xs = _spread([_month_position(p.on) for p in points], frame)
        ys = [scale.y(p.value) for p in points]
        return [
            *_grid(frame, scale, labels, skip_zero=False),
            _area(xs, ys, frame),
            _tag("polyline", {"class": "line", "points": _points(xs, ys)}),
            _dot(xs[-1], ys[-1]),
            *_line_ticks(points, ticks, xs, frame),
            *_line_hits(points, xs, ys, frame),
        ]

    return _pair(labelled_by, _frame(420, 200, left, 8, 172), _frame(320, 200, left, 8, 172), draw)


def in_and_out(
    months: Sequence[InOutPoint],
    *,
    labelled_by: str,
    ticks: Sequence[int],
    selected: int | None,
) -> Markup:
    """Money in and money out side by side each month, what was left over as a line through
    them, and the month a panel reads outlined (reporting-household.html)."""
    if not months:
        raise ValueError("a chart needs at least one month")
    _require_indices([*ticks, *([] if selected is None else [selected])], len(months))
    values = [v for m in months for v in (m.money_in, m.money_out)]
    values += [m.left_over for m in months if m.known]
    scale = _scale(values, top=10, bottom=192)
    labels = _axis_labels(scale.ticks)
    left = _left_margin(labels, 44)

    def draw(frame: _Frame) -> list[str]:
        band = (frame.right - frame.left) / len(months)
        width = min(_PAIR_WIDTH, (band - 2 * _PAIR_GAP - _GAP * 2) / 2)
        base = scale.y(Decimal(0))
        body = _grid(frame, scale, labels, skip_zero=False)
        centres = [frame.left + (i + 0.5) * band for i in range(len(months))]
        for i, month in enumerate(months):
            start = frame.left + i * band
            x = start + (band - 2 * width - _GAP) / 2
            marks = []
            for css, value, at in (
                ("in", month.money_in, x),
                ("out", month.money_out, x + width + _GAP),
            ):
                if value > 0:
                    kind = f"bar {css}" if month.known else "bar-gap"
                    marks.append(_bar_path(kind, at, width, base, scale.y(value)))
            if i == selected:
                top = min(scale.y(month.money_in), scale.y(month.money_out), base) - 4
                box = {"x": _c(start + 2), "y": _c(top), "width": _c(band - 4)}
                box |= {"height": _c(base - top), "rx": "4"}
                marks.append(_tag("rect", {"class": "sel", **box}))
            marks.append(_band_hit(start, band, frame, month.tip, month.tip_label))
            body.append("<g>" + "".join(marks) + "</g>")
            if i in ticks:
                body.append(_centred(centres[i], frame, month.axis_label))
        body.append(_rule("base", frame, base))
        run: list[int] = []
        for i, entry in enumerate([*months, None]):
            if entry is not None and entry.known:
                run.append(i)
                continue
            if len(run) > 1:
                xs, ys = [centres[j] for j in run], [scale.y(months[j].left_over) for j in run]
                body.append(_tag("polyline", {"class": "left", "points": _points(xs, ys)}))
            run = []
        for i, month in enumerate(months):
            if month.known:
                css = "left-dot is-bad" if month.left_over < 0 else "left-dot"
                y = _c(scale.y(month.left_over))
                body.append(
                    _tag("circle", {"class": css, "cx": _c(centres[i]), "cy": y, "r": "3.5"})
                )
        return body

    return _pair(labelled_by, _frame(520, 220, left, 4, 192), _frame(320, 220, left, 4, 192), draw)


def cumulative_pace(
    days_in_month: int,
    actual: Sequence[Decimal],
    typical: Sequence[Decimal],
    low: Sequence[Decimal],
    high: Sequence[Decimal],
    *,
    labelled_by: str,
    today: int,
    today_label: str,
    month_abbr: str,
    tips: Sequence[tuple[str, str]] = (),
) -> Markup:
    """A month's running total so far against a typical month and its usual range (me.html).

    Every series holds running totals at the end of each day, from day 1: ``actual``
    up to ``today``, the others for the whole month or, with no history yet, empty.
    ``tips`` holds a (value, label) pair per day of ``actual``.
    """
    if not 1 <= today <= days_in_month:
        raise ValueError(f"today must fall within the month, not on day {today}")
    if len(actual) != today:
        raise ValueError("actual needs one running total per day up to today")
    if tips and len(tips) != today:
        raise ValueError("tips needs one entry per day up to today")
    for name, series in (("typical", typical), ("low", low), ("high", high)):
        if series and len(series) != days_in_month:
            raise ValueError(f"{name} needs one running total per day of the month, or none")
    if bool(low) != bool(high):
        raise ValueError("a usual range needs both its low and its high")
    scale = _scale([*actual, *typical, *low, *high], top=10, bottom=182)
    labels = _axis_labels(scale.ticks)
    left = _left_margin(labels, 44)
    zero = Decimal(0)

    def draw(frame: _Frame, candidates: Sequence[int]) -> list[str]:
        step = (frame.right - frame.left) / days_in_month
        xs = [frame.left + day * step for day in range(days_in_month + 1)]

        def ys(series: Sequence[Decimal]) -> list[float]:
            return [scale.y(value) for value in (zero, *series)]

        body = _grid(frame, scale, labels, skip_zero=True)
        if high:
            outline = ys(high) + ys(low)[::-1]
            body.append(
                _tag("polygon", {"class": "band", "points": _points(xs + xs[::-1], outline)})
            )
        if typical:
            body.append(_tag("polyline", {"class": "median", "points": _points(xs, ys(typical))}))
        body.append(_rule("base", frame, scale.y(zero)))
        so_far = ys(actual)
        body.append(_tag("polyline", {"class": "line", "points": _points(xs[: today + 1], so_far)}))
        x, y = xs[today], so_far[today]
        body.append(_dot(x, y))
        if x - frame.left < 64:
            body.append(_text(x + 8, y - 10, today_label, "start", css="lbl"))
        else:
            body.append(_text(x - 8, y - 10, today_label, "end", css="lbl"))
        body += _day_labels(xs, days_in_month, today, month_abbr, candidates, frame)
        for day, (value, label) in enumerate(tips, start=1):
            box = {"x": _c(xs[day] - step / 2), "y": str(frame.top), "width": _c(step)}
            box |= {"height": str(frame.bottom - frame.top), "data-tip": _tip(value, label)}
            body.append(_tag("rect", {"class": "hit", **box}))
        return body

    last = days_in_month
    wide = _frame(640, 210, left, 8, 182)
    narrow = _frame(320, 210, left, 8, 182)
    return _markup(
        _svg(wide, "wide", labelled_by, draw(wide, (1, 8, 15, 22, last))),
        _svg(narrow, "narrow", labelled_by, draw(narrow, (1, 15, last))),
    )


def saved_vs_market(months: Sequence[MovePoint], *, labelled_by: str) -> Markup:
    """Each month's change, saving on the baseline and the market on top or below (net-worth.html).

    Saving always sits on the baseline. The market stacks onto it when they move the
    same way and hangs from the other side of the baseline when they do not.
    """
    if not months:
        raise ValueError("a chart needs at least one month")
    extents = [Decimal(0)]
    for move in months:
        extents += [_sum_if(move, positive=True), _sum_if(move, positive=False)]
    scale = _scale(extents, top=10, bottom=192)
    labels = _axis_labels(scale.ticks, signed=True)
    left = _left_margin(labels, 48)

    def draw(frame: _Frame) -> list[str]:
        band = (frame.right - frame.left) / len(months)
        width = _bar_width(band)
        stride = math.ceil(_LABEL_SPACE / band)
        body = _grid(frame, scale, labels, skip_zero=True)
        for i, move in enumerate(months):
            start = frame.left + i * band
            x = start + (band - width) / 2
            segments = _segments(move, scale)
            for segment in segments:
                body.append(_segment(segment, x, width, rounded=_outermost(segment, segments)))
            body.append(_band_hit(start, band, frame, move.tip, move.tip_label))
            if (len(months) - 1 - i) % stride == 0:
                body.append(_centred(start + band / 2, frame, move.axis_label))
        base = _c(scale.y(Decimal(0)))
        line = {"class": "base", "x1": str(frame.left), "x2": str(frame.right), "y1": base}
        body.append(_tag("line", line | {"y2": base, "style": "stroke:var(--ink-3)"}))
        return body

    return _pair(labelled_by, _frame(1000, 220, left, 6, 192), _frame(320, 220, left, 6, 192), draw)


# ---------- scales and axis labels ----------


def _scale(values: Iterable[Decimal], *, top: int, bottom: int, from_zero: bool = True) -> _Scale:
    """Round gridlines about ``_STEPS`` apart that enclose every value.

    A scale from zero ends half a step above its data, as the design's do; a
    floating scale ends on the gridlines either side of its data.
    """
    data = [*values, *([Decimal(0)] if from_zero else [])]
    low, high = min(data), max(data)
    step = _nice_step((high - low if high != low else abs(high)) / _STEPS)
    if from_zero:
        lo, hi = _snap(low, step / 2, ROUND_FLOOR), _snap(high, step / 2, ROUND_CEILING)
        if lo == hi:
            hi = step
    else:
        lo, hi = _snap(low, step, ROUND_FLOOR), _snap(high, step, ROUND_CEILING)
        if lo == hi:
            lo, hi = lo - step, hi + step
    first = _snap(lo, step, ROUND_CEILING)
    count = int((hi - first) / step) + 1
    return _Scale(lo, hi, tuple(first + i * step for i in range(count)), top, bottom)


def _nice_step(raw: Decimal) -> Decimal:
    """The smallest 1, 2 or 5 times a power of ten at least ``raw``, and never under a dollar."""
    if raw <= 1:
        return Decimal(1)
    power = Decimal(1).scaleb(raw.adjusted())
    return next((power * m for m in (1, 2, 5) if power * m >= raw), power * 10)


def _snap(value: Decimal, step: Decimal, rounding: str) -> Decimal:
    return (value / step).to_integral_value(rounding=rounding) * step


def _axis_labels(ticks: Sequence[Decimal], *, signed: bool = False) -> list[str]:
    """Compact dollars, one unit and one precision for the whole axis: $1.10M, $1.15M."""
    unit, suffix = _unit(max(abs(t) for t in ticks))
    places = next((p for p in range(7) if all(_exact(t / unit, p) for t in ticks)), 6)

    def label(tick: Decimal) -> str:
        if tick == 0:
            return "$0"
        sign = _MINUS if tick < 0 else "+" if signed else ""
        return f"{sign}${abs(tick) / unit:.{places}f}{suffix}"

    return [label(t) for t in ticks]


def _unit(largest: Decimal) -> tuple[Decimal, str]:
    for unit, suffix in _UNITS:
        if largest >= unit:
            return unit, suffix
    return Decimal(1), ""


def _exact(value: Decimal, places: int) -> bool:
    return value == value.quantize(Decimal(1).scaleb(-places))


def _left_margin(labels: Sequence[str], design: int) -> int:
    """The design's left margin, widened when a y label would not fit in it."""
    return max(design, 10 + _CHAR_WIDTH * max(len(label) for label in labels))


def _sum_if(move: MovePoint, *, positive: bool) -> Decimal:
    return sum((v for v in (move.saved, move.market) if (v > 0) == positive and v), Decimal(0))


# ---------- geometry ----------


def _frame(width: int, height: int, left: int, right_margin: int, bottom: int) -> _Frame:
    return _Frame(width, height, left, width - right_margin, 10, bottom)


def _month_position(day: date) -> float:
    """Months since year zero, so month ends sit evenly apart and today falls between."""
    days = calendar.monthrange(day.year, day.month)[1]
    return day.year * 12 + day.month + day.day / days


def _spread(positions: Sequence[float], frame: _Frame) -> list[float]:
    first, span = positions[0], positions[-1] - positions[0]
    if not span:
        return [float(frame.right)] * len(positions)
    return [frame.left + (p - first) / span * (frame.right - frame.left) for p in positions]


def _bar_width(band: float) -> float:
    return min(_BAR_WIDTH, max(band - 6, band / 2))


def _segments(move: MovePoint, scale: _Scale) -> list[_Segment]:
    base = scale.y(Decimal(0))
    saved_end = scale.y(move.saved)
    segments = [_Segment("seg-saved", base, saved_end, move.saved > 0)] if move.saved else []
    if move.market:
        rising = move.market > 0
        away = -_GAP if rising else _GAP
        if not move.saved:
            inner, outer = base, scale.y(move.market)
        elif rising == (move.saved > 0):
            inner, outer = saved_end + away, scale.y(move.saved + move.market)
        else:
            inner = base + away
            outer = inner + scale.y(move.market) - base
        segments.append(_Segment("seg-market", inner, outer, rising))
    return [s for s in segments if (s.outer < s.inner if s.rising else s.outer > s.inner)]


def _outermost(segment: _Segment, segments: Sequence[_Segment]) -> bool:
    """Only the segment furthest from the baseline on its side gets the rounded end."""
    return not any(
        other is not segment
        and other.rising == segment.rising
        and (other.outer < segment.outer if segment.rising else other.outer > segment.outer)
        for other in segments
    )


# ---------- marks ----------


def _pair(
    labelled_by: str, wide: _Frame, narrow: _Frame, draw: Callable[[_Frame], list[str]]
) -> Markup:
    return _markup(
        _svg(wide, "wide", labelled_by, draw(wide)),
        _svg(narrow, "narrow", labelled_by, draw(narrow)),
    )


def _markup(wide: str, narrow: str) -> Markup:
    # Safe by construction: _tag escapes every attribute value and every text.
    return Markup(f"{wide}\n{narrow}")  # noqa: S704


def _svg(frame: _Frame, variant: str, labelled_by: str, body: Sequence[str]) -> str:
    opening = _tag(
        "svg",
        {
            "class": f"chart only-{variant}",
            "viewBox": f"0 0 {frame.width} {frame.height}",
            "role": "img",
            "aria-labelledby": labelled_by,
        },
        closed=False,
    )
    return "\n".join([opening, *body, "</svg>"])


def _grid(frame: _Frame, scale: _Scale, labels: Sequence[str], *, skip_zero: bool) -> list[str]:
    """Hairlines at each tick, and the tick's label left of the plot."""
    lines = "".join(
        _tag("line", {"x1": str(frame.left), "x2": str(frame.right), "y1": _c(y), "y2": _c(y)})
        for tick in scale.ticks
        if not (skip_zero and tick == 0)
        for y in [scale.y(tick)]
    )
    texts = "".join(
        _tag(
            "text",
            {"x": str(frame.left - 8), "y": _c(scale.y(tick) + 4), "text-anchor": "end"},
            label,
        )
        for tick, label in zip(scale.ticks, labels, strict=True)
    )
    return [f'<g class="grid">{lines}</g>', texts]


def _area(xs: Sequence[float], ys: Sequence[float], frame: _Frame) -> str:
    floor = str(frame.bottom)
    outline = f"{_c(xs[0])},{floor} {_points(xs, ys)} {_c(xs[-1])},{floor}"
    return _tag("polygon", {"class": "area", "points": outline})


def _line_ticks(
    points: Sequence[ChartPoint], ticks: Sequence[int], xs: Sequence[float], frame: _Frame
) -> list[str]:
    """Month names under the line; the first and last are pinned to the plot's edges."""
    marks = []
    for i in ticks:
        if i == len(points) - 1:
            marks.append(_text(xs[i] + 6, frame.label_y, points[i].axis_label, "end"))
        elif i == 0:
            marks.append(_text(xs[i] - 4, frame.label_y, points[i].axis_label, "start"))
        else:
            marks.append(_text(xs[i], frame.label_y, points[i].axis_label, "middle"))
    return marks


def _line_hits(
    points: Sequence[ChartPoint], xs: Sequence[float], ys: Sequence[float], frame: _Frame
) -> list[str]:
    """Hover targets from each point down to the floor, split halfway between points."""
    edges = [xs[0] - 4, *((a + b) / 2 for a, b in itertools.pairwise(xs)), xs[-1] + 6]
    hits = []
    for i, point in enumerate(points):
        top = ys[i] - 6
        box = {"x": _c(edges[i]), "y": _c(top), "width": _c(edges[i + 1] - edges[i])}
        box |= {"height": _c(frame.bottom - top), "data-tip": _tip(point.tip, point.tip_label)}
        hits.append(_tag("rect", {"class": "hit", **box}))
    return hits


def _day_labels(
    xs: Sequence[float],
    days: int,
    today: int,
    month_abbr: str,
    candidates: Sequence[int],
    frame: _Frame,
) -> list[str]:
    """A few days of the month, "Today" in their place, dropping any that would collide."""
    kept: list[int] = []
    for day in sorted(set(candidates)):
        crowded = abs(xs[day] - xs[today]) < 24 or (bool(kept) and xs[day] - xs[kept[-1]] < 24)
        if day != today and not crowded:
            kept.append(day)
    marks = []
    for day in sorted([*kept, today]):
        anchor = "start" if day == 1 else "end" if day == days else "middle"
        if day == today:
            attrs = {"x": _c(xs[day]), "y": str(frame.label_y), "text-anchor": anchor}
            marks.append(
                _tag("text", attrs | {"class": "lbl-2", "style": "font-weight:600"}, "Today")
            )
        else:
            text = f"{month_abbr} 1" if day == 1 else str(day)
            marks.append(_text(xs[day], frame.label_y, text, anchor))
    return marks


def _bar_path(css: str, x: float, width: float, inner: float, outer: float) -> str:
    """A bar from ``inner`` to ``outer``, its far corners rounded as the design draws them."""
    r = min(_RADIUS, width / 2, abs(outer - inner))
    q, flat = _radius(r), _c(width - 2 * r)
    if outer < inner:
        turn = f"V{_c(outer + r)} q0,-{q} {q},-{q} h{flat} q{q},0 {q},{q}"
    else:
        turn = f"V{_c(outer - r)} q0,{q} {q},{q} h{flat} q{q},0 {q},-{q}"
    return _tag("path", {"class": css, "d": f"M{_c(x)},{_c(inner)} {turn} V{_c(inner)} Z"})


def _segment(segment: _Segment, x: float, width: float, *, rounded: bool) -> str:
    if rounded:
        return _bar_path(segment.css, x, width, segment.inner, segment.outer)
    top, height = min(segment.inner, segment.outer), abs(segment.outer - segment.inner)
    return _tag(
        "rect",
        {"class": segment.css, "x": _c(x), "y": _c(top), "width": _c(width), "height": _c(height)},
    )


def _band_hit(start: float, band: float, frame: _Frame, tip: str, tip_label: str) -> str:
    box = {"x": _c(start), "y": str(frame.top), "width": _c(band)}
    box |= {"height": str(frame.bottom - frame.top), "data-tip": _tip(tip, tip_label)}
    return _tag("rect", {"class": "hit", **box})


def _rule(css: str, frame: _Frame, y: float) -> str:
    attrs = {"class": css, "x1": str(frame.left), "x2": str(frame.right)}
    return _tag("line", attrs | {"y1": _c(y), "y2": _c(y)})


def _dot(x: float, y: float) -> str:
    return _tag("circle", {"class": "dot", "cx": _c(x), "cy": _c(y), "r": _DOT_RADIUS})


def _centred(x: float, frame: _Frame, text: str) -> str:
    return _text(x, frame.label_y, text, "middle")


def _text(x: float, y: float, text: str, anchor: str, *, css: str | None = None) -> str:
    """Text at a point; a whole-pixel ``y`` is a frame line and is written as one."""
    attrs = {"class": css} if css else {}
    row = str(y) if isinstance(y, int) else _c(y)
    return _tag("text", attrs | {"x": _c(x), "y": row, "text-anchor": anchor}, text)


# ---------- text ----------


def _tag(
    name: str, attrs: Mapping[str, str], text: str | None = None, *, closed: bool = True
) -> str:
    """One element, its attribute values and text escaped."""
    rendered = "".join(f' {key}="{escape(value)}"' for key, value in attrs.items())
    if text is not None:
        return f"<{name}{rendered}>{escape(text)}</{name}>"
    return f"<{name}{rendered}/>" if closed else f"<{name}{rendered}>"


def _tip(value: str, label: str) -> str:
    """The tooltip as design.js reads it; a "|" of the caller's would split it early."""
    safe = [part.replace(_TIP_SEPARATOR, "\N{BROKEN BAR}") for part in (value, label)]
    return _TIP_SEPARATOR.join(safe)


def _points(xs: Sequence[float], ys: Sequence[float]) -> str:
    return " ".join(f"{_c(x)},{_c(y)}" for x, y in zip(xs, ys, strict=True))


def _c(value: float) -> str:
    """A coordinate to one decimal, as the design writes them, never "-0.0"."""
    return f"{round(value, 1) + 0.0:.1f}"


def _radius(value: float) -> str:
    return "3" if value == _RADIUS else _c(value)


# ---------- argument checks ----------


def _require_points(points: Sequence[ChartPoint]) -> None:
    if not points:
        raise ValueError("a chart needs at least one point")


def _require_indices(indices: Iterable[int], count: int) -> None:
    for i in indices:
        if not 0 <= i < count:
            raise ValueError(f"index {i} is not one of the {count} points")


def _require_ascending(days: Sequence[date]) -> None:
    if any(a >= b for a, b in itertools.pairwise(days)):
        raise ValueError("points must be in date order, one per day at most")
