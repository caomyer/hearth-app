"""A synthetic pile over a two-member household with sole, joint and unclaimed accounts."""

import sqlite3
from collections.abc import Collection, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import pytest
from beancount import loader
from beancount.core.data import Open

from hearth import db
from hearth.accounts import Account
from hearth.members import MemberId
from hearth.review.desk import ReviewDesk
from hearth.review.pile import PileItem, read_pile
from hearth.review.rulings import Ruling, RulingStore
from tests.conftest import FakeClock

ALEX = MemberId(1)
BLAIR = MemberId(2)

ACCOUNTS = [
    Account("a1", "Assets:Alex:Checking", "Alex checking", frozenset({ALEX})),
    Account("j1", "Assets:Joint:Checking", "Joint checking", frozenset({ALEX, BLAIR})),
    Account("b1", "Liabilities:Blair:Card", "Blair card", frozenset({BLAIR})),
    Account("n1", "Assets:Unclaimed:Savings", "Unclaimed savings", frozenset()),
]

PILE = """
2026-01-01 open Assets:Alex:Checking
2026-01-01 open Assets:Joint:Checking
2026-01-01 open Liabilities:Blair:Card
2026-01-01 open Assets:Unclaimed:Savings
2026-01-01 open Income:Salary
2026-01-01 open Expenses:Gifts
2026-01-01 open Expenses:Shopping
2026-01-01 open Equity:Review:Unresolved

2026-01-15 ! "PAYROLL DEPOSIT"
  review-id: "pay0000001"
  review-group: "payroll"
  source: "statement-a:4"
  Assets:Alex:Checking  2500.00 USD
  Income:Salary

2026-01-31 ! "PAYROLL DEPOSIT"
  review-id: "pay0000002"
  review-group: "payroll"
  source: "statement-a:9"
  Assets:Alex:Checking  2500.00 USD
  Income:Salary

2026-01-20 ! "P2P TRANSFER"
  review-id: "p2p0000001"
  review-group: "p2p"
  model-confidence: 0.412
  source: "statement-a:6"
  Assets:Alex:Checking  -40.00 USD
  Expenses:Gifts

2026-01-21 ! "P2P TRANSFER"
  review-id: "p2p0000002"
  review-group: "p2p"
  source: "statement-j:2"
  Assets:Joint:Checking  -60.00 USD
  Expenses:Gifts

2026-01-22 ! "P2P TRANSFER"
  review-id: "p2p0000003"
  review-group: "p2p"
  source: "statement-b:3"
  Liabilities:Blair:Card  -15.00 USD
  Expenses:Gifts

2026-01-23 ! "ADJUSTMENT"
  review-id: "unk0000001"
  review-group: "unknown-type"
  source: "statement-b:5"
  Liabilities:Blair:Card  -9.99 USD
  Equity:Review:Unresolved

2026-01-24 ! "TRANSFER"
  review-id: "orp0000001"
  review-group: "p2p"
  source: "statement-u:1"
  Assets:Unclaimed:Savings  -5.00 USD
  Expenses:Gifts
"""


OPENED = {e.account: (e.date, None) for e in loader.load_string(PILE)[0] if isinstance(e, Open)}
"""Every account the books in `PILE` open."""


def owners_only(member: MemberId, owners: Collection[MemberId]) -> bool:
    """The owner clause of hearth.access.may_see, which is what reviewing needs."""
    return member in owners


@pytest.fixture
def pile() -> list[PileItem]:
    entries, errors, _ = loader.load_string(PILE)
    assert errors == []
    return read_pile(entries)


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    conn = db.connect(Path(":memory:"))
    db.migrate(conn)
    for member_id, name in ((ALEX, "alex"), (BLAIR, "blair")):
        conn.execute(
            "INSERT INTO members (id, username, display_name, password_hash, is_admin,"
            " must_change_password, created_at) VALUES (?, ?, ?, 'x', 0, 0, '2026-01-01')",
            (member_id, name, name.title()),
        )
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def rulings(connection: sqlite3.Connection) -> RulingStore:
    return RulingStore(connection)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 2, 1, 9, 0, tzinfo=UTC))


class DeskFactory(Protocol):
    def __call__(self, member: MemberId, items: list[PileItem] | None = None) -> ReviewDesk: ...


@pytest.fixture
def desk_for(pile: list[PileItem], rulings: RulingStore, clock: FakeClock) -> DeskFactory:
    def make(member: MemberId, items: list[PileItem] | None = None) -> ReviewDesk:
        return ReviewDesk(
            member,
            pile=pile if items is None else items,
            accounts=ACCOUNTS,
            rulings=rulings,
            may_review=owners_only,
            opened=OPENED,
            may_move_to=lambda owners: member in owners,
            clock=clock,
        )

    return make


def shown(desk: ReviewDesk, group: str) -> dict[str, str]:
    """What a page shows of `group`: each waiting row with the account it proposes now."""
    return {
        view.item.review_id: view.item.proposed
        for view in desk.items()
        if view.item.group == group and view.unreviewed and view.item.proposed is not None
    }


def accept_shown(desk: ReviewDesk, group: str, note: str | None = None) -> Ruling:
    """Accept `group` as a page showing it now would."""
    ruling = desk.accept_group(group, shown(desk, group), note).ruling
    assert ruling is not None
    return ruling
