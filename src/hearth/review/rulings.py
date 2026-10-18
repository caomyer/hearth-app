"""Rulings on set-aside rows, kept for good in the app database.

The store is append-only: a ruling is never edited or deleted, and a later ruling supersedes an
earlier one on the same row by being later. Every ruling names the rows it covers by review id,
a group ruling included, so it covers exactly the rows its author saw and nothing that arrives
afterwards. An accept also records, row by row, the account each row proposed when it was
accepted, so it books nothing the import proposes later. No amount is stored: the ledger is the
only place money is recorded.
"""

import json
import re
import sqlite3
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from hearth.db import transaction
from hearth.members import MemberId
from hearth.review.pile import UNRESOLVED

# beancount's rule for an account name.
ACCOUNT = re.compile(r"(Assets|Liabilities|Equity|Income|Expenses)(:[A-Z0-9][A-Za-z0-9-]*)+")


class Action(StrEnum):
    ACCEPT = "accept"
    """Book the row against the account the import proposed, while it still proposes it."""
    ACCOUNT = "account"
    """Book the row against an account the member names."""
    REJECT = "reject"
    """The row is not a transaction; book nothing."""


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


def check_account(account: str) -> None:
    if not ACCOUNT.fullmatch(account):
        raise ValueError(f"{account!r} is not a beancount account name")
    if account == UNRESOLVED:
        raise ValueError(f"{UNRESOLVED} is where an unruled row waits; name where it belongs")


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
        at: datetime,
    ) -> Ruling:
        """Record a ruling covering `rows`: each review id, with the account it was accepted as."""
        if (scope is Scope.GROUP) != (group is not None):
            raise ValueError("a group is named exactly when the ruling is on a group")
        if (action is Action.ACCOUNT) != (account is not None):
            raise ValueError("an account is named exactly when the ruling books against one")
        if account is not None:
            check_account(account)
        if not rows:
            raise ValueError("a ruling must cover at least one row")
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
                "INSERT INTO review_ruling_rows (ruling_id, review_id, account) VALUES (?, ?, ?)",
                [(ruling_id, review_id, rows[review_id]) for review_id in sorted(rows)],
            )
        if ruling_id is None:
            raise RuntimeError("the ruling was not recorded")
        return Ruling(ruling_id, member_id, scope, group, action, account, note, at)

    def covering(self, review_ids: Collection[str]) -> Iterator[tuple[str, Ruling, str | None]]:
        """Every ruling that covers one of `review_ids`, oldest first.

        Each comes with the row it covers and, for an accept, the account that row was accepted as.
        """
        rows = self._db.execute(
            "SELECT r.review_id, r.account, s.id, s.member_id, s.scope, s.group_name, s.action,"
            " s.account, s.note, s.at"
            " FROM review_ruling_rows AS r JOIN review_rulings AS s ON s.id = r.ruling_id"
            " WHERE r.review_id IN (SELECT value FROM json_each(?))"
            " ORDER BY s.id, r.review_id",
            (json.dumps(sorted(set(review_ids))),),
        )
        for review_id, accepted, ruling_id, member, scope, group, action, account, note, at in rows:
            yield (
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
            )
