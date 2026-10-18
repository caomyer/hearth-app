import sqlite3
from datetime import UTC, datetime
from typing import Any

import pytest

from hearth.review.rulings import Action, RulingStore, Scope
from tests.review.conftest import ALEX

AT = datetime(2026, 2, 1, 9, 0, tzinfo=UTC)
VALID: dict[str, Any] = {
    "member_id": ALEX,
    "scope": Scope.ROW,
    "group": None,
    "action": Action.REJECT,
    "account": None,
    "note": None,
    "rows": {"p2p0000001": None},
    "at": AT,
}


@pytest.mark.parametrize(
    ("change", "complaint"),
    [
        ({"group": "p2p"}, "group is named exactly"),
        ({"scope": Scope.GROUP}, "group is named exactly"),
        ({"action": Action.ACCOUNT}, "account is named exactly"),
        ({"account": "Expenses:Gifts"}, "account is named exactly"),
        ({"action": Action.ACCOUNT, "account": "Gifts"}, "not a beancount account"),
        ({"rows": {}}, "at least one row"),
        ({"action": Action.ACCEPT}, "proposal is recorded exactly"),
        ({"rows": {"p2p0000001": "Expenses:Gifts"}}, "proposal is recorded exactly"),
        ({"action": Action.ACCEPT, "rows": {"p2p0000001": "gifts"}}, "not a beancount account"),
        ({"at": datetime(2026, 2, 1, 9, 0)}, "timezone-aware"),
    ],
)
def test_a_malformed_ruling_is_refused(
    rulings: RulingStore, connection: sqlite3.Connection, change: dict[str, Any], complaint: str
) -> None:
    with pytest.raises(ValueError, match=complaint):
        rulings.record(**(VALID | change))

    assert connection.execute("SELECT count(*) FROM review_rulings").fetchone()[0] == 0


def test_a_ruling_by_someone_who_is_not_a_member_is_refused(rulings: RulingStore) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        rulings.record(**(VALID | {"member_id": 99}))


def test_the_store_keeps_every_ruling_and_returns_them_oldest_first(
    rulings: RulingStore,
) -> None:
    first = rulings.record(**VALID)
    second = rulings.record(**(VALID | {"action": Action.ACCOUNT, "account": "Expenses:Gifts"}))

    accepted = rulings.record(
        **(VALID | {"action": Action.ACCEPT, "rows": {"p2p0000001": "Expenses:Gifts"}})
    )

    assert list(rulings.covering({"p2p0000001"})) == [
        ("p2p0000001", first, None),
        ("p2p0000001", second, None),
        ("p2p0000001", accepted, "Expenses:Gifts"),
    ]
    assert list(rulings.covering({"other00001"})) == []


def test_the_store_records_no_amount(connection: sqlite3.Connection, rulings: RulingStore) -> None:
    columns = {
        row[1]
        for table in ("review_rulings", "review_ruling_rows")
        for row in connection.execute(f"PRAGMA table_info({table})")
    }

    assert not {"amount", "number", "balance"} & columns
