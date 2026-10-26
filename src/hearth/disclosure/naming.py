"""Naming an account hearth does not see: savings at a bank nobody connected, a car loan.

A member names one from the money they can see move into or out of it, and owns it alone or
with whichever members they choose, as with an account a connection is filed into: only an
owner sees it, moves rows into it, or later connects it (``access.may_feed``). No balance is
asked for. One typed today would be wrong tomorrow, as hearth does not see what is spent from
the account, and a guessed balance is a wrong one.

Its ledger name is the household's one accepted probe (``docs/privacy-boundary.md``): a name
taken by any account the books know, at, under or above it, is refused with one answer that
says nothing about what it hit. The name hearth proposes is worked out from the member's own
accounts alone, so a proposal discloses nothing; it can still meet an account they cannot see,
and is refused like a typed one.
"""

import itertools
import sqlite3
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from hearth import accounts
from hearth.accounts import Account, Kind
from hearth.disclosure.gate import Gate
from hearth.members import Member, MemberId
from hearth.plaid import mapping
from hearth.plaid.mapping import Problem

MAX_NAME = 80
MAX_WHERE = 80


class Wrong(StrEnum):
    """What is wrong with a named account as the form sent it. Each is a fixed message."""

    NAME = "name"
    NAME_TOO_LONG = "name-too-long"
    WHERE_TOO_LONG = "where-too-long"
    MASK = "mask"


@dataclass(frozen=True, slots=True)
class OwnerChoice:
    key: str
    label: str
    owners: frozenset[MemberId]


@dataclass(frozen=True, slots=True)
class Draft:
    """A named account as the form has it."""

    kind: Kind = Kind.BANK
    where: str = ""
    name: str = ""
    mask: str = ""
    owners: str = ""
    ledger_account: str = ""

    @classmethod
    def of(
        cls, kind: str, where: str, name: str, mask: str, owners: str, ledger_account: str
    ) -> Draft:
        """A draft as a form sent it. Raises `ValueError` for a kind no form offers."""
        return cls(Kind(kind), where, name, mask, owners, ledger_account)


@dataclass(frozen=True, slots=True)
class Checked:
    """A draft and what is wrong with it, if anything: `wrong` about its details, `problem`
    about its ledger name."""

    draft: Draft
    wrong: Wrong | None = None
    problem: Problem | None = None

    @property
    def ok(self) -> bool:
        return self.wrong is None and self.problem is None


class Namer:
    """Names accounts for one member, as the access policy lets them."""

    def __init__(
        self,
        gate: Gate,
        *,
        household: Sequence[Member],
        accounts: Sequence[Account],
        ledger_accounts: Collection[str],
        conn: sqlite3.Connection,
    ) -> None:
        self._gate = gate
        self._viewer = gate.viewer
        self._members = list(household)
        self._accounts = list(accounts)
        self._ledger_accounts = frozenset(ledger_accounts)
        self._db = conn

    def __repr__(self) -> str:
        return "Namer()"

    def kinds(self) -> list[Kind]:
        """The kinds of account a member may name, in the order the form offers them."""
        return list(Kind)

    def owner_choices(self) -> tuple[OwnerChoice, ...]:
        """Who may own it: the member, alone or with any of the others, as for a new account a
        connection is filed into."""
        others = [m for m in self._members if m.id != self._viewer.id]
        combos = [
            frozenset({self._viewer.id, *(m.id for m in group)})
            for size in range(len(others) + 1)
            for group in itertools.combinations(others, size)
        ]
        return tuple(self._choice(owners) for owners in combos if self._gate.may_feed(owners))

    def proposal(self, draft: Draft) -> str:
        """The ledger name hearth proposes for `draft`, beside the member's own accounts alone."""
        proposed = mapping.proposed_named(
            root=draft.kind.root,
            member=self._viewer.display_name,
            institution=draft.where,
            name=draft.name,
        )
        mine = [a.ledger_account for a in self._accounts if self._gate.may_feed(a.owners)]
        return mapping.unused(proposed, mine, _digits(draft.mask) or None)

    def check(self, draft: Draft) -> Checked:
        """`draft` with its ledger name proposed where none was typed, and what is wrong."""
        if not draft.ledger_account.strip():
            draft = Draft(
                draft.kind,
                draft.where,
                draft.name,
                draft.mask,
                draft.owners,
                self.proposal(draft) if _printable(draft.name) else "",
            )
        name, where, digits = _printable(draft.name), _printable(draft.where), draft.mask.strip()
        wrong = None
        if not name:
            wrong = Wrong.NAME
        elif len(name) > MAX_NAME:
            wrong = Wrong.NAME_TOO_LONG
        elif len(where) > MAX_WHERE:
            wrong = Wrong.WHERE_TOO_LONG
        elif digits and not (digits.isdigit() and 2 <= len(digits) <= 4):
            wrong = Wrong.MASK
        problem = mapping.check_new(
            draft.ledger_account.strip(),
            credit=draft.kind.root == "Liabilities",
            existing={*self._ledger_accounts, *(a.ledger_account for a in self._accounts)},
        )
        return Checked(draft, wrong, problem)

    def name(self, checked: Checked, *, now: datetime) -> Account:
        """Register a checked draft as an account its chosen owners own, which hearth does not
        see. Raises `NotTheirs` for owners the form did not offer."""
        if not checked.ok:
            raise ValueError("only a draft with nothing wrong is named")
        draft = checked.draft
        owners = next((c.owners for c in self.owner_choices() if c.key == draft.owners), None)
        if owners is None or not self._gate.may_feed(owners):
            raise NotTheirs
        return accounts.name_account(
            self._db,
            ledger_account=draft.ledger_account.strip(),
            name=_printable(draft.name),
            kind=draft.kind,
            institution=_printable(draft.where) or None,
            mask=draft.mask.strip() or None,
            owners=owners,
            by=self._viewer.id,
            now=now,
        )

    def _choice(self, owners: frozenset[MemberId]) -> OwnerChoice:
        others = [
            m.display_name for m in self._members if m.id in owners and m.id != self._viewer.id
        ]
        if not others:
            label = f"Just {self._viewer.display_name}"
        elif len(others) == 1:
            label = f"Joint with {others[0]}"
        else:
            label = f"Joint with {', '.join(others[:-1])} and {others[-1]}"
        return OwnerChoice(",".join(str(o) for o in sorted(owners)), label, owners)


class NotTheirs(ValueError):  # noqa: N818 - it names a refusal, as Concealed does
    """Owners the form did not offer: an account a member does not own is not theirs to name."""


def _printable(text: str) -> str:
    return "".join(ch for ch in text if ch.isprintable()).strip()


def _digits(text: str) -> str:
    digits = text.strip()
    return digits if digits.isdigit() else ""
