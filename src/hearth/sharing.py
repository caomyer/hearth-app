"""What each member has chosen to share with the rest of the household.

Sharing is a choice per member and per facet of their finances. A member who has
never chosen gets the defaults: totals are shared, detail is not.
"""

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from hearth import audit
from hearth.audit import EventKind
from hearth.db import transaction
from hearth.members import MemberId


class Facet(StrEnum):
    NET_WORTH_TOTAL = "net_worth_total"
    MONTHLY_SPENDING_TOTAL = "monthly_spending_total"
    CATEGORY_BREAKDOWN = "category_breakdown"
    TRANSACTIONS = "transactions"
    ACCOUNT_BALANCES = "account_balances"


@dataclass(frozen=True, slots=True)
class FacetDescription:
    title: str
    explanation: str


DESCRIPTIONS: Mapping[Facet, FacetDescription] = {
    Facet.NET_WORTH_TOTAL: FacetDescription(
        "Net worth total", "The household sees your total, and nothing behind it."
    ),
    Facet.MONTHLY_SPENDING_TOTAL: FacetDescription(
        "Monthly spending total", "The household sees what you spent each month, as one number."
    ),
    Facet.CATEGORY_BREAKDOWN: FacetDescription(
        "Category breakdown", "Spending by category, such as food or housing, without transactions."
    ),
    Facet.TRANSACTIONS: FacetDescription(
        "Individual transactions", "Every transaction in full, including payees."
    ),
    Facet.ACCOUNT_BALANCES: FacetDescription(
        "Account balances", "The balance of each of your accounts, not just the total."
    ),
}

DEFAULT_SHARED = frozenset({Facet.NET_WORTH_TOTAL, Facet.MONTHLY_SPENDING_TOTAL})

type SharingChoices = Mapping[MemberId, frozenset[Facet]]


def load(conn: sqlite3.Connection) -> dict[MemberId, frozenset[Facet]]:
    """Every member's shared facets, with defaults filled in for choices not yet made."""
    shared = {
        MemberId(row["id"]): set(DEFAULT_SHARED) for row in conn.execute("SELECT id FROM members")
    }
    for row in conn.execute("SELECT member_id, facet, shared FROM sharing_preferences"):
        facets = shared[MemberId(row["member_id"])]
        if row["shared"]:
            facets.add(Facet(row["facet"]))
        else:
            facets.discard(Facet(row["facet"]))
    return {member: frozenset(facets) for member, facets in shared.items()}


def choose(
    conn: sqlite3.Connection, member: MemberId, facet: Facet, *, shared: bool, now: datetime
) -> None:
    title = DESCRIPTIONS[facet].title
    with transaction(conn):
        _store(conn, member, facet, shared=shared, now=now)
        audit.record(
            conn,
            EventKind.SHARING_CHANGED,
            at=now,
            actor=member,
            subject=member,
            detail=f"now shares {title.lower()}" if shared else f"stopped sharing {title.lower()}",
        )


def _store(
    conn: sqlite3.Connection, member: MemberId, facet: Facet, *, shared: bool, now: datetime
) -> None:
    conn.execute(
        "INSERT INTO sharing_preferences (member_id, facet, shared, updated_at)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT (member_id, facet) DO UPDATE"
        " SET shared = excluded.shared, updated_at = excluded.updated_at",
        (member, facet.value, shared, now.isoformat()),
    )
