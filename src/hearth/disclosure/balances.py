"""Every account the viewer may see, its balance, and how hearth knows that balance.

The native app's sidebar, its list of accounts and an account's own page are built from this.
An account is listed only when the viewer may see its balance or its transactions, as on its
web page (``Disclosure.account``). Each says what its balance rests on, and every absence is
its own state, never a blank: a balance not shared with the viewer, one hearth does not see,
one not known yet, one being brought in, and one whose check against its bank failed.

What goes with which facet follows the pages. The balance, what kind of account it is and
whether it agrees with its bank go with its balances; the day its data reaches and the day
its bank's balance covers are when its money last moved, which goes with its transactions;
why a balance is not known, and that a connection needs its member, go to the owners and the
connection's holder alone.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from beancount.core.data import Balance

from hearth import figures
from hearth.accounts import Account
from hearth.connections import Connection, State
from hearth.disclosure.common import Household, MemberRef, PartRef, Worth
from hearth.disclosure.gate import WITHHELD, Disclosed
from hearth.disclosure.people import AccountRow, account_rows
from hearth.plaid.opening import anchors, held
from hearth.reports.coverage import Span
from hearth.sharing import Facet


class Standing(StrEnum):
    """What an account's balance rests on, as far as the viewer may be told."""

    MATCHES = "matches"
    """It agrees with the balance its bank last reported, which hearth checks it against."""
    HOLDINGS = "holdings"
    """It is what its brokerage last reported the account holds, valued at hearth's prices."""
    CHECKED = "checked"
    """It agrees with a balance check the books hold, such as a statement's closing balance."""
    UNCHECKED = "unchecked"
    """It is what the books hold, and nothing in them checks it against a bank."""
    NOT_KNOWN = "not_known"
    """The books do not know it yet: not zero, not known."""
    ARRIVING = "arriving"
    """Hearth is bringing the account in now; its balance follows."""
    UNSEEN = "unseen"
    """Hearth does not see the account, and is never told its balance (`books.unseen`)."""
    FAILED = "failed"
    """The latest recompute found the books disagree with its bank, or a statement; the
    balance shown is from the books before."""
    WITHHELD = "withheld"
    """Its balance is not shared with the viewer."""


GROUPS = {"Cash": "Cash", "Credit": "Credit", "Investments": "Investments"}
"""The viewer's own accounts are grouped by kind, in this order, as the pages group them."""

UNSEEN_GROUP = "Not seen by hearth"


@dataclass(frozen=True, slots=True)
class Failed:
    """A balance check that failed: what the books say, what was asserted, and as of when."""

    asserted: Decimal
    computed: Decimal
    as_of: date


@dataclass(frozen=True, slots=True)
class AccountStanding:
    id: str
    name: str
    owners: tuple[MemberRef, ...]
    part: PartRef
    kind: Disclosed[str]
    """"Cash", "Credit" or "Investments", read off its positions, so it goes with its balance."""
    balance: Disclosed[Worth]
    standing: Standing
    why: str | None
    """Why its balance is not known (``plaid.opening.Unknown``), told to its owners alone."""
    checked: Disclosed[date | None]
    """The last day its bank's balance, or the books' latest check of it, covers."""
    failed: Failed | None
    updated: Disclosed[date | None]
    """The last day its data is known through (`figures.last_day`)."""
    behind: Disclosed[bool]
    """Whether that day is long enough before the books' own day to say so (`books.behind`)."""
    priced: date | None
    """For a brokerage account whose balance the viewer may see, its newest price's day."""
    transactions: bool
    """Whether the viewer may see its transactions."""
    spans: Disclosed[tuple[Span, ...]]
    institution: str | None
    """Where it is: its bank's name, from the viewer's own connection to it, or as its owner
    named it for an account hearth does not see. Nobody else's connection is ever named."""
    mask: str | None
    attention: bool
    """Whether the viewer's own connection that feeds it has to be signed in to again."""
    unseen_kind: str | None = None
    """What kind of account its owner said one hearth does not see is, in their words."""
    reconciling: bool = False
    """Whether its bank's balance agrees with the books only by an entry reconciling the
    imported ledger with the bank that nobody has confirmed yet (``books.reconciling``): its
    balance is then not shown as matching its bank. Told with its balance."""
    unseen_between: Disclosed[tuple[date, date] | None] = None
    """For a carried account, the days from its cut-over that neither the imported ledger nor
    its bank's history holds (``books.unseen_between``), which go with its transactions."""
    held_back: Disclosed[tuple[date, date] | None] = None
    """For a matched account not carried on yet, the last day its imported ledger knows of it
    today and the day ahead its ledger holds an entry on (``books.held_back``), which go with
    its transactions."""


@dataclass(frozen=True, slots=True)
class AccountGroup:
    name: str
    accounts: tuple[AccountStanding, ...]
    yours: bool
    """Whether these are the viewer's own accounts, alone or jointly; otherwise someone else's
    the viewer may see into, named by their owners."""


def visible(h: Household, account: Account) -> bool:
    """Whether the viewer may see the account at all: its balance or its transactions."""
    return h.gate.may(Facet.ACCOUNT_BALANCES, account.owners) or h.gate.may(
        Facet.TRANSACTIONS, account.owners
    )


def standings(
    h: Household,
    accounts: Sequence[Account],
    connections: Sequence[Connection],
    failed: tuple[str, Failed] | None,
) -> tuple[AccountGroup, ...]:
    """Every account the viewer may see, grouped: theirs by kind, then those hearth does not
    see, then everyone else's they may see into, by whose they are."""
    shown = [a for a in accounts if visible(h, a)]
    found = [standing(h, a, connections, failed) for a in shown]
    groups: list[AccountGroup] = []
    yours = [s for s, a in zip(found, shown, strict=True) if h.viewer.id in a.owners]
    for kind, name in GROUPS.items():
        rows = [s for s in yours if s.standing is not Standing.UNSEEN and s.kind == kind]
        if rows:
            groups.append(AccountGroup(name, _sorted(rows), yours=True))
    unseen = [s for s in yours if s.standing is Standing.UNSEEN]
    if unseen:
        groups.append(AccountGroup(UNSEEN_GROUP, _sorted(unseen), yours=True))
    theirs = [s for s, a in zip(found, shown, strict=True) if h.viewer.id not in a.owners]
    for part in h.parts:
        rows = [s for s in theirs if s.part.owners == h.part_ref(part).owners]
        if rows:
            groups.append(AccountGroup(h.part_ref(part).owner_names, _sorted(rows), yours=False))
    return tuple(groups)


def _sorted(rows: Sequence[AccountStanding]) -> tuple[AccountStanding, ...]:
    """By kind, where the viewer may know it, then by name, as the pages list accounts."""
    kinds = list(GROUPS)

    def order(row: AccountStanding) -> tuple[int, str]:
        kind = row.kind if isinstance(row.kind, str) and row.kind in kinds else None
        return (kinds.index(kind) if kind else len(kinds), row.name)

    return tuple(sorted(rows, key=order))


def standing(
    h: Household,
    account: Account,
    connections: Sequence[Connection],
    failed: tuple[str, Failed] | None,
) -> AccountStanding:
    """One account the viewer may see (`visible`), and what its balance rests on."""
    gate, books = h.gate, h.books
    balances = gate.may(Facet.ACCOUNT_BALANCES, account.owners)
    transactions = gate.may(Facet.TRANSACTIONS, account.owners)
    ledger = account.ledger_account
    row: AccountRow | None = account_rows(h, [account])[0] if balances else None
    unseen = ledger in books.unseen
    check = _latest_check(h, ledger)
    feeding = next((c for c in connections if ledger in c.ledger_accounts), None)
    why = books.unknown.get(ledger) if gate.may_see_why_unknown(account.owners) else None
    failure = failed[1] if failed is not None and failed[0] == ledger and balances else None
    updated = figures.last_day(books, [account]) if transactions and not unseen else None
    return AccountStanding(
        id=account.id,
        name=account.name,
        owners=tuple(h.ref(m) for m in h.members if m.id in account.owners),
        part=h.part_ref(next(p for p in h.parts if p.owners == account.owners)),
        kind=row.kind if row else WITHHELD,
        balance=row.value if row else WITHHELD,
        standing=_standing(row, unseen, check, failure, ledger in books.reconciling),
        why=why.value if why is not None and row is not None and not row.value.is_known else None,
        checked=((check.date - timedelta(days=1) if check else None) if transactions else WITHHELD),
        failed=failure,
        updated=updated if transactions else WITHHELD,
        behind=(books.behind(updated) if updated else False) if transactions else WITHHELD,
        priced=row.priced if row and row.invested else None,
        transactions=transactions,
        spans=gate.disclose(Facet.TRANSACTIONS, account.owners, lambda: _spans(h, ledger)),
        institution=(
            account.named.institution
            if unseen and account.named
            else feeding.institution
            if feeding
            else None
        ),
        mask=account.named.mask if unseen and account.named else None,
        attention=feeding is not None and feeding.state is State.EXPIRED,
        unseen_kind=account.named.kind.label if unseen and account.named else None,
        reconciling=row is not None and ledger in books.reconciling,
        unseen_between=gate.disclose(
            Facet.TRANSACTIONS, account.owners, lambda: books.unseen_between.get(ledger)
        ),
        held_back=gate.disclose(
            Facet.TRANSACTIONS, account.owners, lambda: books.held_back.get(ledger)
        ),
    )


def _standing(
    row: AccountRow | None,
    unseen: bool,
    check: Balance | None,
    failure: Failed | None,
    reconciling: bool = False,
) -> Standing:
    if unseen:
        return Standing.UNSEEN
    if row is None:
        return Standing.WITHHELD
    if failure is not None:
        return Standing.FAILED
    if row.arriving:
        return Standing.ARRIVING
    if not row.value.is_known:
        return Standing.NOT_KNOWN
    if check is None:
        return Standing.UNCHECKED
    if held(check):
        return Standing.HOLDINGS
    if anchors(check) and reconciling:
        # Not proved by its bank until someone confirms what the ledger and the bank differ by.
        return Standing.UNCHECKED
    return Standing.MATCHES if anchors(check) else Standing.CHECKED


def _latest_check(h: Household, ledger_account: str) -> Balance | None:
    """The newest balance check the books hold of the account, in their currency or, for a
    brokerage's holdings, of anything it holds. Every one passed: books that fail one are not
    books (``books.compute``)."""
    found = [e for e in h.books.entries if isinstance(e, Balance) and e.account == ledger_account]
    return max(found, key=lambda e: e.date, default=None)


def _spans(h: Household, ledger_account: str) -> tuple[Span, ...]:
    coverage = next((c for c in h.books.coverage if c.account == ledger_account), None)
    return coverage.spans if coverage else ()
