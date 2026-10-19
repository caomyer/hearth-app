"""Rulings on unreviewed rows, kept for good in the app database.

The store is append-only: a ruling is never edited or deleted, and a later ruling supersedes an
earlier one on the same row by being later. Every ruling names the rows it covers by review id,
a group ruling included, so it covers exactly the rows its author saw and nothing that arrives
afterwards. An accept also records, row by row, the account each row proposed when it was
accepted, so it books nothing the import proposes later. Every ruling records, row by row, what
the row said when it was made (``hearth.review.ids.says``), so it decides nothing about a row
its bank has since revised. No amount is stored: the ledger is the only place money is
recorded.
"""

import json
import re
import sqlite3
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NamedTuple

from hearth.db import transaction
from hearth.members import MemberId
from hearth.review.pile import WAITING

# beancount's rule for an account name.
ACCOUNT = re.compile(r"(Assets|Liabilities|Equity|Income|Expenses)(:[A-Z0-9][A-Za-z0-9-]*)+")


class Action(StrEnum):
    ACCEPT = "accept"
    """The account the import proposed is right, while it still proposes it."""
    ACCOUNT = "account"
    """The row belongs against an account the member names."""
    REJECT = "reject"
    """The row is not a transaction; book nothing."""
    REOPEN = "reopen"
    """Take a decision back: the rows are unreviewed again, as if nobody had ruled."""


class Scope(StrEnum):
    ROW = "row"
    GROUP = "group"


@dataclass(frozen=True, slots=True)
class Ruling:
    id: int
    member_id: MemberId
    scope: Scope
    group: str | None
    action: Action
    account: str | None
    note: str | None
    at: datetime


class Covered(NamedTuple):
    """One row a ruling covers, as the ruling recorded it."""

    review_id: str
    ruling: Ruling
    accepted: str | None
    """The account the row proposed when it was accepted, for an accept."""
    says: str | None
    """What the row said when the ruling was made; None for a ruling recorded before that
    was kept."""


def check_account(account: str) -> None:
    if not ACCOUNT.fullmatch(account):
        raise ValueError(f"{account!r} is not a beancount account name")
    if account in WAITING:
        raise ValueError(f"{account} is where an unruled row waits; name where it belongs")


class RulingStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        if connection.autocommit is not True:
            raise ValueError("open the connection with hearth.db.connect, in autocommit mode")
        self._db = connection

    def record(
        self,
        *,
        member_id: MemberId,
        scope: Scope,
        group: str | None,
        action: Action,
        account: str | None,
        note: str | None,
        rows: Mapping[str, str | None],
        says: Mapping[str, str],
        at: datetime,
    ) -> Ruling:
        """Record a ruling covering `rows`: each review id, with the account it was accepted as
        and what it said (`says`)."""
        if (scope is Scope.GROUP) != (group is not None):
            raise ValueError("a group is named exactly when the ruling is on a group")
        if (action is Action.ACCOUNT) != (account is not None):
            raise ValueError("an account is named exactly when the ruling books against one")
        if account is not None:
            check_account(account)
        if not rows:
            raise ValueError("a ruling must cover at least one row")
        if set(says) != set(rows):
            raise ValueError("what each row says is recorded for exactly the rows ruled on")
        for accepted in rows.values():
            if (action is Action.ACCEPT) != (accepted is not None):
                raise ValueError("a row's proposal is recorded exactly when the ruling accepts")
            if accepted is not None:
                check_account(accepted)
        if at.utcoffset() is None:
            raise ValueError("a ruling's time must be timezone-aware")
        with transaction(self._db):
            ruling_id = self._db.execute(
                "INSERT INTO review_rulings"
                " (member_id, scope, group_name, action, account, note, at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (member_id, scope, group, action, account, note, at.isoformat()),
            ).lastrowid
            self._db.executemany(
                "INSERT INTO review_ruling_rows (ruling_id, review_id, account, says)"
                " VALUES (?, ?, ?, ?)",
                [
                    (ruling_id, review_id, rows[review_id], says[review_id])
                    for review_id in sorted(rows)
                ],
            )
        if ruling_id is None:
            raise RuntimeError("the ruling was not recorded")
        return Ruling(ruling_id, member_id, scope, group, action, account, note, at)

    def covering(self, review_ids: Collection[str]) -> Iterator[Covered]:
        """Every ruling that covers one of `review_ids`, oldest first, row by row."""
        rows = self._db.execute(
            "SELECT r.review_id, r.account, r.says, s.id, s.member_id, s.scope, s.group_name,"
            " s.action, s.account, s.note, s.at"
            " FROM review_ruling_rows AS r JOIN review_rulings AS s ON s.id = r.ruling_id"
            " WHERE r.review_id IN (SELECT value FROM json_each(?))"
            " ORDER BY s.id, r.review_id",
            (json.dumps(sorted(set(review_ids))),),
        )
        for (
            review_id,
            accepted,
            said,
            ruling_id,
            member,
            scope,
            group,
            action,
            account,
            note,
            at,
        ) in rows:
            yield Covered(
                review_id,
                Ruling(
                    ruling_id,
                    MemberId(member),
                    Scope(scope),
                    group,
                    Action(action),
                    account,
                    note,
                    datetime.fromisoformat(at),
                ),
                accepted,
                said,
            )

    def get(self, ruling_id: int) -> tuple[Ruling, list[str]] | None:
        """A ruling and the review ids it covers, or `None` when there is no such ruling."""
        found = self._db.execute(
            "SELECT id, member_id, scope, group_name, action, account, note, at"
            " FROM review_rulings WHERE id = ?",
            (ruling_id,),
        ).fetchone()
        if found is None:
            return None
        rows = self._db.execute(
            "SELECT review_id FROM review_ruling_rows WHERE ruling_id = ? ORDER BY review_id",
            (ruling_id,),
        )
        ruling_id, member, scope, group, action, account, note, at = found
        ruling = Ruling(
            ruling_id,
            MemberId(member),
            Scope(scope),
            group,
            Action(action),
            account,
            note,
            datetime.fromisoformat(at),
        )
        return ruling, [row["review_id"] for row in rows]

    def accounts(self) -> set[str]:
        """Every account a ruling has booked a row against, whether or not it still decides
        one."""
        rows = self._db.execute(
            "SELECT DISTINCT account FROM review_rulings WHERE account IS NOT NULL"
        )
        return {row["account"] for row in rows}

    def latest_by(self, member_id: MemberId) -> datetime | None:
        """When `member_id` last ruled, or `None` if they never have; taking a
        decision back is not reviewing."""
        row = self._db.execute(
            "SELECT at FROM review_rulings WHERE member_id = ? AND action != ? "
            "ORDER BY id DESC LIMIT 1",
            (member_id, Action.REOPEN),
        ).fetchone()
        return None if row is None else datetime.fromisoformat(row["at"])
