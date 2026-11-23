"""What the Mac app is told of an account carried on from an imported base
(``docs/real-ledger.md``): its difference with its bank in Review, decisions the books keep
unreviewed, and the days of its history nobody holds, each as the pages tell them, and to
nobody but whoever the pages tell."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from hearth import accounts
from hearth.native.common import Acted, Decided
from hearth.native.doorway import SCREENS, Doorway
from hearth.plaid.registry import Registry
from hearth.review.ids import plaid_review_id, reconciling_review_id
from hearth.review.pile import LEDGER_BANK, RECONCILING
from hearth.review.queue import RECONCILING_REASON, Confidence
from tests.conftest import FakeClock
from tests.test_carrying import (
    AHEAD,
    CARD,
    CHECKING,
    CHECKING_CUT,
    CHECKING_HELD,
    EARLY,
    NOW,
    Household,
    _reconciling,
    _record,
    build,
)


def door(household: Household, viewer: str = "avery") -> Doorway:
    return Doorway(
        household.settings, household.fresh.bookkeeper, viewer, clock=household.fresh.clock
    )


def outcome(done: object) -> str:
    """What the app is told an action came to."""
    if isinstance(done, Decided):
        return str(done.wire()["outcome"])
    assert isinstance(done, Acted), done
    return done.value


def ready(answer: dict[str, Any]) -> dict[str, Any]:
    assert answer["state"] == "ready", answer
    return answer


def rows_of(answer: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in answer["rows"]}


def account_id(household: Household, ledger_account: str) -> str:
    conn = household.fresh.connect()
    try:
        return next(a.id for a in accounts.all_accounts(conn) if a.ledger_account == ledger_account)
    finally:
        conn.close()


@pytest.fixture
def differing(tmp_path: Path, clock: FakeClock) -> Household:
    """The carried household, its bank saying the checking account holds 8.00 less than the
    base and every row it sent."""
    household = build(tmp_path, clock)
    household.fresh.plaid.report(household.item, "chk", CHECKING_HELD - Decimal("8.00"))
    household.sync()
    return household


def test_a_difference_is_its_own_kind_of_row_in_the_apps_review(differing: Household) -> None:
    review_id = reconciling_review_id(CHECKING, CHECKING_CUT)
    answer = ready(door(differing).answer("review"))
    row = rows_of(answer)[review_id]

    assert row["kind"] == "reconciling"
    assert row["narration"].startswith(
        "The imported ledger and Zq Carry Bank disagree by this much as of Sep 2, 2026"
    )
    assert row["reason"] == RECONCILING_REASON
    assert row["suggested"]["name"] == LEDGER_BANK
    # Its account's balance is its bank's, so it is never left out.
    assert row["excludable"] is False
    (group,) = [g for g in answer["groups"] if g["name"] == RECONCILING]
    assert group["one_decision"] is False
    assert group["least_sure"] == Confidence.RECONCILING.value
    assert {r["kind"] for r in answer["rows"] if r["id"] != review_id} == {"transaction"}
    detail = ready(door(differing).answer("review_row", {"id": review_id}))
    assert (detail["row"]["kind"], detail["decision"]) == ("reconciling", "waiting")
    # Until it is confirmed, the account is not shown as matching its bank, and says why.
    listed = ready(door(differing).answer("account", {"id": account_id(differing, CHECKING)}))
    assert (listed["account"]["standing"], listed["account"]["reconciling"]) == ("unchecked", True)


def test_a_difference_is_confirmed_from_the_app_never_as_a_group_nor_excluded(
    differing: Household,
) -> None:
    review_id = reconciling_review_id(CHECKING, CHECKING_CUT)
    row = rows_of(ready(door(differing).answer("review")))[review_id]
    avery = door(differing)

    grouped = avery.act("accept_group", {"group": RECONCILING, "rows": {review_id: LEDGER_BANK}})
    assert outcome(grouped) == "not_one_decision"
    excluded = avery.act("exclude", {"row": review_id, "says": row["says"]})
    assert outcome(excluded) == "banks_row"
    assert [e.flag for e in _reconciling(differing.books())] == ["!"]

    confirmed = avery.act("accept", {"row": review_id, "shown": LEDGER_BANK})
    assert outcome(confirmed) == "accepted"
    found = differing.books()
    assert [e.flag for e in _reconciling(found)] == ["*"]
    assert review_id not in rows_of(ready(door(differing).answer("review")))
    listed = ready(door(differing).answer("account", {"id": account_id(differing, CHECKING)}))
    assert (listed["account"]["standing"], listed["account"]["reconciling"]) == (
        "matches",
        False,
    )


def test_nobody_else_is_told_of_a_difference_nor_may_decide_it(differing: Household) -> None:
    review_id = reconciling_review_id(CHECKING, CHECKING_CUT)
    blake = door(differing, "blake")
    told = []
    for screen in SCREENS:
        for asked in ({}, {"id": review_id}, {"group": RECONCILING}):
            told.append(repr(blake.answer(screen, asked)))
    decisions: dict[str, dict[str, object]] = {
        "accept": {"row": review_id, "shown": LEDGER_BANK},
        "accept_group": {"group": RECONCILING, "rows": {review_id: LEDGER_BANK}},
        "book": {"row": review_id, "says": "anything", "category": "Expenses:Zq:Spent"},
        "exclude": {"row": review_id, "says": "anything"},
        "move_row": {"row": review_id, "says": "anything", "category": "Expenses:Zq:Spent"},
    }
    for action, given in decisions.items():
        assert outcome(blake.act(action, given)) == "missing", action
    everything = "\n".join(told)

    assert review_id not in everything
    assert "disagree by this much" not in everything
    assert "Ledger-Bank-Difference" not in everything
    assert [e.flag for e in _reconciling(differing.books())] == ["!"]


def test_a_decision_the_books_keep_unreviewed_waits_in_the_app_and_says_why(
    tmp_path: Path, clock: FakeClock
) -> None:
    """A ruling on record that would move a row onto a carried account, or leave out one of
    its bank's rows, decides nothing: the row waits, and says which it was."""
    household = build(tmp_path, clock, match=("card",))
    conn = household.fresh.connect()
    try:
        kept = accounts.register(
            conn,
            ledger_account="Assets:Avery:Zq:Checking",
            name="Everyday",
            owners=[household.fresh.member_ids["avery"]],
        )
        Registry(conn).confirm(household.accounts["chk"], kept.id, by=None, now=NOW)
    finally:
        conn.close()
    moved, rejected = plaid_review_id(household.ids["R7"]), plaid_review_id(household.ids["K5"])
    _record(household, moved, "account", CARD)
    _record(household, rejected, "reject", None)
    household.books()

    answer = ready(door(household).answer("review"))
    rows = rows_of(answer)
    assert rows[moved]["reason"] == Confidence.KEPT_OFF.value
    assert rows[rejected]["reason"] == Confidence.KEPT_IN.value
    assert answer["kept"] == 2
    for review_id in (moved, rejected):
        detail = ready(door(household).answer("review_row", {"id": review_id}))
        assert (detail["decision"], detail["booked"]) == ("waiting", None)


def test_an_accounts_page_in_the_app_says_which_days_nobody_holds(
    tmp_path: Path, clock: FakeClock
) -> None:
    household = build(tmp_path, clock, match=("chk",), base=EARLY, claims=(CHECKING,))
    household.fresh.plaid.report(household.item, "chk", CHECKING_HELD + Decimal("50.00"))
    household.sync()
    identity = account_id(household, CHECKING)

    found = ready(door(household).answer("account", {"id": identity}))

    assert found["unseen"] == {"from": "2026-05-02", "to": "2026-08-02"}
    assert found["held_back"] is None
    # Days of its history go with its transactions, which Blake is not shown.
    assert door(household, "blake").answer("account", {"id": identity})["state"] == "missing"


def test_an_accounts_page_in_the_app_says_what_its_ledger_holds_it_back_to(
    tmp_path: Path, clock: FakeClock
) -> None:
    household = build(tmp_path, clock, match=("chk",), extra=AHEAD, claims=(CHECKING,))
    household.books()

    found = ready(door(household).answer("account", {"id": account_id(household, CHECKING)}))

    assert found["held_back"] == {"through": "2026-09-01", "from": date(2027, 3, 1).isoformat()}
    assert found["unseen"] is None
