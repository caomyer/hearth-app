"""When a review is due: the member's interval since they last ruled, and only if rows wait."""

from datetime import UTC, datetime, timedelta

import pytest

from hearth.review.cadence import Cadence, reminder

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("cadence", "since", "due"),
    [
        (Cadence.DAILY, timedelta(hours=23), False),
        (Cadence.DAILY, timedelta(days=1), True),
        (Cadence.WEEKLY, timedelta(days=6), False),
        (Cadence.WEEKLY, timedelta(days=7), True),
        (Cadence.MONTHLY, timedelta(days=29), False),
        (Cadence.MONTHLY, timedelta(days=31), True),
        (Cadence.OFF, timedelta(days=3650), False),
    ],
)
def test_a_review_is_due_once_the_interval_has_passed(
    cadence: Cadence, since: timedelta, due: bool
) -> None:
    assert reminder(cadence, 3, NOW - since, NOW).due is due


def test_nothing_is_due_when_nothing_is_waiting() -> None:
    assert not reminder(Cadence.DAILY, 0, NOW - timedelta(days=30), NOW).due


def test_a_member_who_never_reviewed_is_due_as_soon_as_something_waits() -> None:
    found = reminder(Cadence.WEEKLY, 1, None, NOW)

    assert found.due
    assert found.due_at == NOW


def test_a_month_after_the_31st_is_the_end_of_the_next_month() -> None:
    assert Cadence.MONTHLY.after(datetime(2026, 1, 31, tzinfo=UTC)) == datetime(
        2026, 2, 28, tzinfo=UTC
    )
