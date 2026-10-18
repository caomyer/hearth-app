import hashlib
from datetime import date
from decimal import Decimal

import pytest

from hearth.review.ids import review_id, review_ids

DAY = date(2026, 1, 15)


def test_ids_are_beancount_nextgens() -> None:
    # bcng.csvimport.triage.review_ids, restated: sha256 over the unit-separated content.
    content = "\x1f".join(("Assets:Bank", "2026-01-15", "-12.50", "COFFEE SHOP", "0"))
    expected = hashlib.sha256(content.encode()).hexdigest()[:10]

    assert review_id("Assets:Bank", DAY, Decimal("-12.50"), "COFFEE  SHOP ") == expected


def test_an_id_depends_on_content_not_position() -> None:
    a = ("Assets:Bank", DAY, Decimal("-12.50"), "COFFEE SHOP")
    b = ("Assets:Bank", DAY, Decimal("-3.00"), "NEWSAGENT")
    new = ("Assets:Bank", DAY, Decimal("-8.00"), "BAKERY")

    first = dict(zip([a, b], review_ids([a, b]), strict=True))
    redownloaded = dict(zip([new, b, a], review_ids([new, b, a]), strict=True))

    assert redownloaded[a] == first[a]
    assert redownloaded[b] == first[b]


def test_identical_rows_are_told_apart_by_their_order_among_themselves() -> None:
    pay = ("Assets:Bank", DAY, Decimal("2500.00"), "PAYROLL")
    other = ("Assets:Bank", DAY, Decimal("1.00"), "FEE")

    first, second = review_ids([pay, pay])
    assert first != second
    assert review_ids([pay, other, pay]) == [first, review_ids([other])[0], second]


@pytest.mark.parametrize(
    ("changed", "value"),
    [(0, "Assets:Other"), (1, date(2026, 1, 16)), (2, Decimal("-12.51")), (3, "COFFEE BAR")],
)
def test_every_part_of_the_content_changes_the_id(changed: int, value: object) -> None:
    row: list[object] = ["Assets:Bank", DAY, Decimal("-12.50"), "COFFEE SHOP"]
    base = review_id(*row)  # type: ignore[arg-type]
    row[changed] = value

    assert review_id(*row) != base  # type: ignore[arg-type]
