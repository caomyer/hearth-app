"""The books hearth keeps itself, for a household that starts with nothing but a bank login.

A member connects a bank and says where each of its accounts goes (``hearth.plaid.mapping``);
those accounts are the household's chart of accounts. Hearth derives its own entries from
them in memory on every recompute, from the app database and the feed store, the way the
review overlay lays rulings over the ledger: what Plaid sent lives in the feed, what members
decided lives in the app database, and nothing is stored twice, so nothing can drift.

That is one ``open`` per mapped account, dated at the start of the history asked for, and
every posted transaction Plaid sent for those accounts, as the import books it
(``hearth.plaid.booking``): flagged until a member reviews it, against the account Plaid's
category proposes, and a bank's payment to a card once, as the transfer ``hearth.plaid.pairing``
pairs it into. Every other account those entries name, and every category a member has
reviewed a row as, is opened alongside, so the file loads as it stands.

An account a member names rather than connects (``hearth.accounts.name_account``) opens too,
marked `seen: "moves-only"`: hearth sees only the rows members move into it from their
connected accounts, so nothing in the books says what it holds (``books.unseen``).

Every recompute also writes these entries to a beancount file, so every figure traces to an
entry that exists, one a person can open in fava, query, diff and check against a statement.
The file is an output, never an input: hearth does not read it, and says so at its top. A
household with a ledger of its own may inherit it (``HEARTH_LEDGER``); hearth reads that one,
never writes it, and the file includes it so that the file alone holds the whole books.
"""

import hashlib
import logging
import os
import sqlite3
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path

from beancount.core.data import Directive, Open, Transaction, new_metadata
from beancount.parser import printer

from hearth.accounts import Account, all_accounts
from hearth.plaid import booking, opening
from hearth.plaid.feed import Feed
from hearth.plaid.registry import Registry
from hearth.reports.common import is_balance_sheet
from hearth.review.pile import WAITING
from hearth.review.rulings import RulingStore

_log = logging.getLogger(__name__)

ORIGIN = "<hearth>"
"""The `filename` of every entry hearth derives, which tells them from an inherited ledger's."""

CURRENCY = "USD"
TITLE = "Household books"

HEADER = """\
; The household's books, as hearth keeps them.
;
; HEARTH WRITES THIS FILE. DO NOT EDIT IT: hearth never reads it back, so an edit changes
; nothing hearth shows, and the next recompute writes the file again without it. Change the
; books in hearth instead. A copy of this file found edited is kept beside it, renamed
; books.edited-<when>.beancount, before it is written again.
"""

_SIGNATURE = "; hearth wrote everything above; its sha256 is "


@dataclass(frozen=True, slots=True)
class Derived:
    """Hearth's own entries, and each account whose opening balance could not be derived,
    with why (``hearth.plaid.opening``)."""

    entries: list[Directive]
    unknown: Mapping[str, opening.Unknown] = field(default_factory=dict)


def derive(
    conn: sqlite3.Connection,
    feed: Feed,
    *,
    opened: Collection[str] = (),
    since: date | None = None,
) -> Derived:
    """Hearth's own entries: an `open` for each ledger account a confirmed mapping feeds,
    every transaction the import books for them (``hearth.plaid.booking``), and each one's
    opening balance with the balance its bank reported (``hearth.plaid.opening``).

    Each is dated at the start of the history its connection asked for, or at the earliest
    transaction Plaid sent for it if that is earlier, or on its opening balance if that is
    earlier still, so every entry it could ever hold is on or after the day it opens. A ledger
    account fed by more than one connection opens at the earliest of them. Accounts in
    `opened`, which an inherited ledger opens already, are left to it, with their transactions.

    Every other account an entry names, and every category a member has reviewed a row as,
    opens on the first of those days, or on `since`, the inherited ledger's first day, when
    hearth opens none of its own. Every account a member named (`Unseen`) opens on the
    earliest of the first row ruled onto it and the household's first day of history, so a
    ruling is never refused for a day before it opened. Raises ``booking.Refused`` when the
    feed cannot be booked.
    """
    registry = Registry(conn)
    named = {account.id: account for account in all_accounts(conn)}
    found: dict[str, Opened] = {}
    for linked in registry.confirmed():
        account = named.get(linked.hearth_account or "")
        item = registry.item(linked.item_id)
        if account is None or item is None or account.ledger_account in opened:
            continue
        day = item.requested_from
        earliest = feed.held(linked.account_id).earliest
        if earliest is not None and earliest < day:
            day = earliest
        start = Opened(account.ledger_account, day, account.name, item.institution, linked.mask)
        known = found.get(start.account)
        if known is None or start.day < known.day:
            found[start.account] = start
    booked = booking.book(conn, feed, opened=opened)
    openings = opening.derive(registry, feed, booked)
    for anchor in openings.anchors:
        if anchor.day < found[anchor.account].day:
            found[anchor.account] = replace(found[anchor.account], day=anchor.day)
    first = min((o.day for o in found.values()), default=since)
    unseen = _unseen(conn, named.values(), booked, set(opened) | found.keys(), first, since)
    equity = {opening.EQUITY} if openings.anchors else set()
    extra = (booked.accounts | equity | _ruled(conn)) - set(opened) - found.keys() - unseen.keys()
    ordered = sorted(found.values(), key=lambda o: (o.day, o.account))
    entries: list[Directive] = [o.entry(0) for o in ordered]
    named_first = sorted(unseen.values(), key=lambda u: (u.day, u.ledger_account))
    entries += [u.entry(0) for u in named_first]
    if first is not None:
        entries += [Open(new_metadata(ORIGIN, 0), first, a, [], None) for a in sorted(extra)]
    entries += [entry for anchor in openings.anchors for entry in anchor.entries()]
    entries += booked.entries
    filed = [_filed(entry, lineno) for lineno, entry in enumerate(entries, start=1)]
    return Derived(filed, openings.unknown)


def _ruled(conn: sqlite3.Connection) -> set[str]:
    """The categories members have reviewed rows as: what a ruling books a row against, once
    the books lay it over the row. A balance-sheet account is one the household names itself,
    and opens as an account of its own; hearth does not open one because a ruling named it."""
    return {
        account
        for account in RulingStore(conn).accounts()
        if not is_balance_sheet(account) and account not in WAITING
    }


def _unseen(
    conn: sqlite3.Connection,
    accounts: Iterable[Account],
    booked: booking.Booked,
    taken: Collection[str],
    first: date | None,
    since: date | None,
) -> dict[str, Unseen]:
    """An `open` for each account a member named, that no ledger and no connection holds.

    It opens on the earliest of the first row any ruling moved onto it and the household's
    first day of history (`first`, or `since` over an inherited ledger), or on the day it was
    named while there is no history at all, so every row a member could move into it is on
    or after the day it opens.
    """
    ruled = RulingStore(conn).ruled_onto()
    days = {
        str(entry.meta["review-id"]): entry.date
        for entry in booked.entries
        if isinstance(entry.meta, dict) and "review-id" in entry.meta
    }
    found: dict[str, Unseen] = {}
    for account in accounts:
        if account.named is None or account.ledger_account in taken:
            continue
        moved = [days[r] for r in ruled.get(account.ledger_account, ()) if r in days]
        candidates = [d for d in (first, since, min(moved, default=None)) if d is not None]
        day = min(candidates, default=account.named.named_at.astimezone().date())
        found[account.ledger_account] = Unseen(account, day)
    return found


def _filed(entry: Directive, lineno: int) -> Directive:
    """`entry` as one of the entries hearth derives, at its place among them."""
    return entry._replace(meta={**entry.meta, "filename": ORIGIN, "lineno": lineno})


@dataclass(frozen=True, slots=True)
class Opened:
    account: str
    day: date
    name: str
    institution: str
    mask: str | None

    def entry(self, lineno: int) -> Open:
        """The `open`, with what a person reading the file needs to know which account it is.

        Who owns it is never written here: ownership decides who may see what, so it lives in
        the app database, where the ledger's readers cannot change it.
        """
        meta = new_metadata(ORIGIN, lineno)
        meta["name"] = self.name
        meta["institution"] = self.institution
        if self.mask:
            meta["mask"] = self.mask
        return Open(meta, self.day, self.account, [], None)


SEEN = "seen"
MOVES_ONLY = "moves-only"
"""The `seen` metadata of an account hearth sees only the moves into and out of."""


@dataclass(frozen=True, slots=True)
class Unseen:
    """An account a member named, which hearth sees only the moves into and out of."""

    account: Account
    day: date

    @property
    def ledger_account(self) -> str:
        return self.account.ledger_account

    def entry(self, lineno: int) -> Open:
        """The `open`, saying what the account is and that hearth does not see it, so a person
        reading the file knows no figure on it is a balance."""
        named = self.account.named
        assert named is not None  # noqa: S101 - only a named account is unseen
        meta = new_metadata(ORIGIN, lineno)
        meta["name"] = self.account.name
        if named.institution:
            meta["institution"] = named.institution
        if named.mask:
            meta["mask"] = named.mask
        meta[SEEN] = MOVES_ONLY
        return Open(meta, self.day, self.account.ledger_account, [], None)


def moves_only(entry: Directive) -> bool:
    """Whether `entry` opens an account hearth sees only the moves into and out of."""
    return isinstance(entry, Open) and (entry.meta or {}).get(SEEN) == MOVES_ONLY


def own(entries: Iterable[Directive]) -> list[Directive]:
    """The entries among `entries` that hearth derived, as the books hold them."""
    return [e for e in entries if isinstance(e.meta, dict) and e.meta.get("filename") == ORIGIN]


def render(entries: Sequence[Directive], *, inherits: Path | None) -> str:
    """The books file: what it is, an inherited ledger if there is one, and `entries`."""
    parts = [HEADER, f'\noption "title" "{TITLE}"\noption "operating_currency" "{CURRENCY}"\n']
    if inherits is not None:
        parts.append(
            "\n; Inherited: the household's own ledger, which hearth reads and never writes.\n"
            f"include {_quoted(str(inherits.resolve()))}\n"
        )
    anchors = [entry for entry in entries if opening.anchors(entry)]
    opens = [e for e in entries if not isinstance(e, Transaction) and not opening.anchors(e)]
    booked = [e for e in entries if isinstance(e, Transaction) and not opening.anchors(e)]
    if opens:
        parts.append(
            "\n; One account for each bank account a member connected, opened on the first day"
            "\n; of the history asked for, each account a member named that hearth does not see"
            '\n; (seen: "moves-only": what it holds is not known, only what moved in and out),'
            "\n; and each category the transactions below name.\n"
        )
        parts.extend(f"\n{printer.format_entry(entry)}" for entry in opens)
    if anchors:
        parts.append(
            "\n; What each account held before the first transaction below, worked out from the"
            "\n; balance its bank reported less every transaction it sent, and that balance, which"
            "\n; beancount checks the transactions add up to.\n"
        )
        parts.extend(f"\n{printer.format_entry(entry)}" for entry in anchors)
    if booked:
        parts.append(
            "\n; Every transaction the banks sent. One marked ! is as hearth proposed it, and"
            "\n; nobody has reviewed it yet; one marked * a member reviewed, or is a payment"
            "\n; from a bank account to a card, which both of them sent.\n"
        )
        parts.extend(f"\n{printer.format_entry(entry)}" for entry in booked)
    return "".join(parts)


def _quoted(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write(path: Path, text: str, *, now: datetime) -> Path | None:
    """Write `text` to `path` whole, readable by its owner alone.

    A file hearth did not write as it stands, because someone edited it, is first renamed
    beside it rather than lost, and that copy is returned. A file already exactly `text` is
    left alone, so a viewer watching it reloads only when the books change.
    """
    text = text if text.endswith("\n") else f"{text}\n"
    signed = f"{text}{_SIGNATURE}{_digest(text)}\n"
    kept = None
    if path.exists():
        current = path.read_text(encoding="utf-8", errors="replace")
        if current == signed:
            return None
        if _edited(current):
            kept = _keep(path, now)
    temporary = path.with_name(f".{path.name}.writing")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(signed)
    temporary.replace(path)
    return kept


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _edited(current: str) -> bool:
    """Whether the file is not exactly as hearth last wrote it."""
    body, _, signature = current.removesuffix("\n").rpartition("\n")
    body += "\n"
    return signature != f"{_SIGNATURE}{_digest(body)}"


def _keep(path: Path, now: datetime) -> Path:
    stamp = now.astimezone().strftime("%Y%m%dT%H%M%S")
    kept, n = path.with_name(f"{path.stem}.edited-{stamp}{path.suffix}"), 2
    while kept.exists():
        kept, n = path.with_name(f"{path.stem}.edited-{stamp}-{n}{path.suffix}"), n + 1
    path.replace(kept)
    _log.warning("%s was edited by hand; hearth kept the edited copy as %s", path, kept.name)
    return kept
