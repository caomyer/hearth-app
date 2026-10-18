"""The charts, read back as SVG the way a browser would read them."""

import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import pytest
from markupsafe import Markup

from hearth.web.charts import (
    ChartPoint,
    MovePoint,
    cumulative_pace,
    month_end_line,
    monthly_bars,
    saved_vs_market,
)

MINUS = "\N{MINUS SIGN}"
MONEY = re.compile(rf"^[+{MINUS}]?\$(\d+(?:\.\d+)?)([KM]?)$")


def svgs(markup: Markup) -> list[ET.Element]:
    # Parsing the module's own output, so the XML attacks S314 guards against do not apply.
    root = ET.fromstring(f"<root>{markup}</root>")  # noqa: S314
    return list(root)


def axis_value(label: str) -> Decimal:
    """What a y-axis label such as "$1.15M", "+$10K" or a minus-signed "$10K" stands for."""
    match = MONEY.match(label)
    assert match, label
    value = Decimal(match[1]) * {"": 1, "K": 1000, "M": 1_000_000}[match[2]]
    return -value if label.startswith(MINUS) else value


def y_axis(svg: ET.Element) -> list[tuple[Decimal, float]]:
    """(value, y) for each y-axis label, read from the end-anchored texts left of the plot."""
    return [
        (axis_value(t.text or ""), float(t.attrib["y"]) - 4)
        for t in svg.iter("text")
        if t.get("text-anchor") == "end" and "class" not in t.attrib and "$" in (t.text or "")
    ]


def to_y(svg: ET.Element, value: Decimal) -> float:
    """Where ``value`` falls on the chart, by the chart's own axis labels."""
    (v0, y0), (v1, y1) = y_axis(svg)[0], y_axis(svg)[-1]
    return y0 + float((value - v0) / (v1 - v0)) * (y1 - y0)


def by_class(svg: ET.Element, css: str) -> list[ET.Element]:
    return [e for e in svg.iter() if css in (e.get("class") or "").split()]


def months(values: Sequence[str]) -> list[ChartPoint]:
    points = []
    for i, value in enumerate(values):
        year, month = divmod(9 + i, 12)
        on = date(2025 + year, month + 1, 1)
        points.append(ChartPoint(on, Decimal(value), f"{on:%b}", f"${value}", f"{on:%b}"))
    return points


NET_WORTH = months(["1142300", "1158900", "1171400", "1186200", "1179800", "1284898"])
SPENDING = months(["10920", "11640", "14210", "10480", "9870", "8214"])


def line(points: Sequence[ChartPoint] = NET_WORTH) -> Markup:
    return month_end_line(points, labelled_by="nw-t", ticks=[0, len(points) - 1])


def bars(
    points: Sequence[ChartPoint] = SPENDING,
    *,
    median: Decimal | None = Decimal("11130"),
    current: int | None = -1,
    current_label: str | None = "$8.2K",
) -> Markup:
    return monthly_bars(
        points,
        labelled_by="sp-t",
        ticks=[0, len(points) - 1],
        median=median,
        current=None if current is None else current % len(points),
        current_label=current_label,
    )


def pace(*, history: bool = True, label: str = "$1,813") -> Markup:
    actual = [Decimal(60 * day) for day in range(1, 19)]
    typical = [Decimal(90 * day) for day in range(1, 32)] if history else []
    return cumulative_pace(
        31,
        actual,
        typical,
        [v * Decimal("0.8") for v in typical],
        [v * Decimal("1.2") for v in typical],
        labelled_by="pace-t",
        today=18,
        today_label=label,
        month_abbr="Oct",
        tips=[(f"${60 * day}", f"Oct {day}") for day in range(1, 19)],
    )


MOVES = [
    MovePoint("Nov", Decimal("4600"), Decimal("3900"), "+$8,500 net", "Nov"),
    MovePoint("Feb", Decimal("4800"), Decimal("-7300"), "-$2,500 net", "Feb"),
    MovePoint("Apr", Decimal("-1200"), Decimal("-11600"), "-$12,800 net", "Apr"),
]


def every_chart() -> list[Markup]:
    moves = saved_vs_market(MOVES, labelled_by="chg-t")
    return [line(), bars(), pace(), moves]


@pytest.mark.parametrize("markup", every_chart())
def test_each_chart_comes_wide_and_narrow_labelled_by_its_title(markup: Markup) -> None:
    wide, narrow = svgs(markup)
    assert wide.attrib["class"] == "chart only-wide"
    assert narrow.attrib["class"] == "chart only-narrow"
    for svg in (wide, narrow):
        assert svg.attrib["role"] == "img"
        assert svg.attrib["aria-labelledby"] in {"nw-t", "sp-t", "pace-t", "chg-t"}
    assert wide.attrib["aria-labelledby"] == narrow.attrib["aria-labelledby"]


@pytest.mark.parametrize("markup", every_chart())
def test_axis_labels_are_compact_whole_amounts(markup: Markup) -> None:
    for svg in svgs(markup):
        for value, _ in y_axis(svg):
            assert value == value.to_integral_value()


def test_net_worth_axis_is_round_and_encloses_every_month() -> None:
    wide, _ = svgs(line())
    axis = y_axis(wide)
    labels = [t.text for t in wide.iter("text") if t.get("x") == "38"]
    assert labels == ["$1.10M", "$1.15M", "$1.20M", "$1.25M", "$1.30M"]
    lo, hi = axis[0][0], axis[-1][0]
    assert all(lo <= p.value <= hi for p in NET_WORTH)
    assert len(by_class(wide, "dot")) == 1


def test_the_line_ends_at_today_with_the_dot() -> None:
    wide, _ = svgs(line())
    (dot,) = by_class(wide, "dot")
    last = by_class(wide, "line")[0].attrib["points"].split()[-1]
    assert last == f"{dot.attrib['cx']},{dot.attrib['cy']}"
    assert float(dot.attrib["cy"]) == pytest.approx(to_y(wide, NET_WORTH[-1].value), abs=0.1)


def test_spending_axis_starts_at_zero_and_clears_the_tallest_month() -> None:
    for svg in svgs(bars()):
        axis = y_axis(svg)
        assert axis[0][0] == 0
        assert axis[-1][0] >= max(p.value for p in SPENDING)


def test_the_current_month_is_inked_and_labelled_above_its_bar() -> None:
    for svg in svgs(bars()):
        (now,) = by_class(svg, "is-now")
        assert now.tag == "path"
        assert by_class(svg, "bar").index(now) == len(SPENDING) - 1
        (label,) = by_class(svg, "lbl")
        assert label.text == "$8.2K"
        top = to_y(svg, SPENDING[-1].value)
        assert float(label.attrib["y"]) < top


def test_the_median_line_sits_at_the_median() -> None:
    for svg in svgs(bars()):
        (median,) = by_class(svg, "median")
        assert float(median.attrib["y1"]) == pytest.approx(to_y(svg, Decimal("11130")), abs=0.1)
        (base,) = by_class(svg, "base")
        assert float(base.attrib["y1"]) == pytest.approx(to_y(svg, Decimal(0)), abs=0.1)


def test_no_median_and_no_current_month_draw_neither() -> None:
    wide, _ = svgs(bars(median=None, current=None, current_label=None))
    assert by_class(wide, "median") == []
    assert by_class(wide, "is-now") == []
    assert by_class(wide, "lbl") == []


def test_a_month_with_missing_history_is_marked_partial() -> None:
    points = [
        *SPENDING[:-1],
        ChartPoint(date(2026, 3, 1), Decimal(900), "Mar", "$900", "Mar", True),
    ]
    wide, _ = svgs(bars(points))
    (partial,) = by_class(wide, "is-partial")
    assert partial.attrib["class"].split() == ["bar", "is-now", "is-partial"]
    assert len(by_class(wide, "is-partial")) == 1


def test_every_month_has_a_hover_target_with_its_tip() -> None:
    wide, _ = svgs(bars())
    tips = [hit.attrib["data-tip"] for hit in by_class(wide, "hit")]
    assert tips == [f"{p.tip}|{p.tip_label}" for p in SPENDING]


def test_labels_and_tips_from_the_ledger_are_escaped() -> None:
    hostile = "<script>alert(1)</script>"
    points = [ChartPoint(date(2026, 1, 1), Decimal(5), hostile, hostile, f'x" onload="{hostile}')]
    for markup in (
        month_end_line(points, labelled_by='t"><script>', ticks=[0]),
        bars(points, current=0, current_label=hostile),
        saved_vs_market(
            [MovePoint(hostile, Decimal(1), Decimal(1), hostile, hostile)], labelled_by="t"
        ),
        pace(label=hostile),
    ):
        assert "<script>" not in markup
        texts = [t.text for svg in svgs(markup) for t in svg.iter("text")]
        tips = [e.attrib["data-tip"] for svg in svgs(markup) for e in by_class(svg, "hit")]
        assert hostile in texts or any(hostile in tip for tip in tips)
        assert all("onload" not in e.attrib for svg in svgs(markup) for e in svg.iter())


def test_a_bar_in_the_tip_cannot_split_it_early() -> None:
    points = [ChartPoint(date(2026, 1, 1), Decimal(5), "Jan", "$5", "Rent | Utilities")]
    wide, _ = svgs(bars(points, current=None, current_label=None))
    (hit,) = by_class(wide, "hit")
    assert hit.attrib["data-tip"].count("|") == 1


def test_pace_draws_the_usual_range_the_typical_month_and_today() -> None:
    wide, narrow = svgs(pace())
    for svg in (wide, narrow):
        assert len(by_class(svg, "band")) == 1
        assert len(by_class(svg, "median")) == 1
        (dot,) = by_class(svg, "dot")
        today = [t for t in svg.iter("text") if t.text == "Today"]
        assert len(today) == 1
        assert today[0].attrib["x"] == dot.attrib["cx"]
        assert [t.text for t in by_class(svg, "lbl")] == ["$1,813"]
        assert len(by_class(svg, "hit")) == 18
    wide_days = [t.text for t in wide.iter("text") if t.get("y") == "202"]
    assert wide_days == ["Oct 1", "8", "15", "Today", "22", "31"]
    narrow_days = [t.text for t in narrow.iter("text") if t.get("y") == "202"]
    assert narrow_days == ["Oct 1", "15", "Today", "31"]


def test_pace_with_no_history_draws_only_this_month() -> None:
    for svg in svgs(pace(history=False)):
        assert by_class(svg, "band") == []
        assert by_class(svg, "median") == []
        assert len(by_class(svg, "line")) == 1
        assert len(by_class(svg, "dot")) == 1


def test_pace_refuses_a_running_total_that_does_not_reach_today() -> None:
    with pytest.raises(ValueError, match="up to today"):
        cumulative_pace(
            31,
            [Decimal(1)],
            [],
            [],
            [],
            labelled_by="t",
            today=2,
            today_label="$1",
            month_abbr="Oct",
        )


def test_market_losses_hang_below_the_baseline() -> None:
    for svg in svgs(saved_vs_market(MOVES, labelled_by="chg-t")):
        (base,) = by_class(svg, "base")
        zero = float(base.attrib["y1"])
        assert zero == pytest.approx(to_y(svg, Decimal(0)), abs=0.1)
        saved, market = by_class(svg, "seg-saved"), by_class(svg, "seg-market")
        assert len(saved) == len(market) == len(MOVES)
        assert _extent(saved[0])[1] == pytest.approx(zero, abs=0.1)  # Nov: both rose
        assert _extent(market[0])[1] < zero
        assert _extent(market[1])[0] > zero  # Feb: the market fell
        assert _extent(saved[1])[1] == pytest.approx(zero, abs=0.1)
        assert _extent(saved[2])[0] == pytest.approx(zero, abs=0.1)  # Apr: both fell
        assert _extent(market[2])[0] > _extent(saved[2])[1]
        plot_bottom = 192
        assert max(_extent(e)[1] for e in market) <= plot_bottom


def test_moves_axis_signs_its_labels() -> None:
    wide, _ = svgs(saved_vs_market(MOVES, labelled_by="chg-t"))
    labels = [t.text or "" for t in wide.iter("text") if t.get("x") == "40"]
    assert "$0" in labels
    assert all(label.startswith(("+", MINUS)) for label in labels if label != "$0")
    assert any(label.startswith(MINUS) for label in labels)


def _extent(element: ET.Element) -> tuple[float, float]:
    """(top, bottom) of a segment, drawn as a rect or as a bar with a rounded end."""
    if element.tag == "rect":
        top = float(element.attrib["y"])
        return top, top + float(element.attrib["height"])
    shape = re.match(r"M[\d.]+,([\d.]+) V([\d.]+) q0,(-?)([\d.]+)", element.attrib["d"])
    assert shape, element.attrib["d"]
    inner, turn, radius = float(shape[1]), float(shape[2]), float(shape[4])
    outer = turn - radius if shape[3] else turn + radius
    return min(inner, outer), max(inner, outer)


@pytest.mark.parametrize(
    "markup",
    [
        line(NET_WORTH[:1]),
        bars(SPENDING[:1]),
        saved_vs_market(MOVES[:1], labelled_by="chg-t"),
    ],
)
def test_a_single_point_draws(markup: Markup) -> None:
    for svg in svgs(markup):
        assert len(by_class(svg, "hit")) == 1


@pytest.mark.parametrize("value", ["0", "1000", "-500"])
def test_equal_values_still_get_an_axis(value: str) -> None:
    flat = [ChartPoint(p.on, Decimal(value), p.axis_label, p.tip, p.tip_label) for p in SPENDING]
    for markup in (line(flat), bars(flat, median=None)):
        for svg in svgs(markup):
            axis = y_axis(svg)
            assert len({v for v, _ in axis}) >= 2
            assert 10 <= to_y(svg, Decimal(value)) <= 172
    moves = [MovePoint("M", Decimal(value), Decimal(value), "t", "l")] * 3
    for svg in svgs(saved_vs_market(moves, labelled_by="t")):
        assert 10 <= to_y(svg, 2 * Decimal(value)) <= 192
