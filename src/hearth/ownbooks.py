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
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from beancount.core.data import Directive, Open, Transaction, new_metadata
from beancount.parser import printer

from hearth.accounts import all_accounts
from hearth.plaid import booking
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


def derive(
    conn: sqlite3.Connection,
    feed: Feed,
    *,
    opened: Collection[str] = (),
    since: date | None = None,
) -> list[Directive]:
    """Hearth's own entries: an `open` for each ledger account a confirmed mapping feeds, and
    every transaction the import books for them (``hearth.plaid.booking``).

    Each is dated at the start of the history its connection asked for, or at the earliest
    transaction Plaid sent for it if that is earlier, so every row it could ever book is on or
    after the day it opens. A ledger account fed by more than one connection opens at the
    earliest of them. Accounts in `opened`, which an inherited ledger opens already, are left
    to it, with their transactions.

    Every other account an entry names, and every category a member has reviewed a row as,
    opens on the first of those days, or on `since`, the inherited ledger's first day, when
    hearth opens none of its own. Raises ``booking.Refused`` when the feed cannot be booked.
    """
    registry = Registry(conn)
    named = {account.id: account for account in all_accounts(conn)}
    found: dict[str, Opening] = {}
    for linked in registry.confirmed():
        account = named.get(linked.hearth_account or "")
        item = registry.item(linked.item_id)
        if account is None or item is None or account.ledger_account in opened:
            continue
        day = item.requested_from
        earliest = feed.held(linked.account_id).earliest
        if earliest is not None and earliest < day:
            day = earliest
        opening = Opening(account.ledger_account, day, account.name, item.institution, linked.mask)
        known = found.get(opening.account)
        if known is None or opening.day < known.day:
            found[opening.account] = opening
    booked = booking.book(conn, feed, opened=opened)
    first = min((o.day for o in found.values()), default=since)
    extra = (booked.accounts | _ruled(conn)) - set(opened) - found.keys()
    ordered = sorted(found.values(), key=lambda o: (o.day, o.account))
    entries: list[Directive] = [o.entry(0) for o in ordered]
    if first is not None:
        entries += [Open(new_metadata(ORIGIN, 0), first, a, [], None) for a in sorted(extra)]
    entries += booked.entries
    return [_filed(entry, lineno) for lineno, entry in enumerate(entries, start=1)]


def _ruled(conn: sqlite3.Connection) -> set[str]:
    """The categories members have reviewed rows as: what a ruling books a row against, once
    the books lay it over the row. A balance-sheet account is one the household names itself,
    and opens as an account of its own; hearth does not open one because a ruling named it."""
    return {
        account
        for account in RulingStore(conn).accounts()
        if not is_balance_sheet(account) and account not in WAITING
    }


def _filed(entry: Directive, lineno: int) -> Directive:
    """`entry` as one of the entries hearth derives, at its place among them."""
    return entry._replace(meta={**entry.meta, "filename": ORIGIN, "lineno": lineno})


@dataclass(frozen=True, slots=True)
class Opening:
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
    opens = [entry for entry in entries if not isinstance(entry, Transaction)]
    booked = [entry for entry in entries if isinstance(entry, Transaction)]
    if opens:
        parts.append(
            "\n; One account for each bank account a member connected, opened on the first day"
            "\n; of the history asked for, and each category the transactions below name.\n"
        )
        parts.extend(f"\n{printer.format_entry(entry)}" for entry in opens)
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
