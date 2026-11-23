"""Importing a ledger the household kept before hearth: once, into a base hearth owns.

``hearth import-ledger PATH`` reads the ledger where it is, through the one path hearth reads
every ledger by (``books.load_ledger``), and keeps a frozen copy of every file it read in the
data directory (``hearth.base``). It writes nothing unless the ledger loads cleanly and every
balance assertion in it passes, and nothing at or beside the ledger ever.

What it prints is the check a person needs before trusting the copy: how many entries and
accounts, the days they span, the balance assertions and that they pass, net worth today in
the ledger's operating currency at the ledger's own prices, and every line it keeps but does
not use, with its file and line: a plugin that is not beancount's own, which is never run
(``books.runs``), a plugin in an included file, which beancount itself reads only in the file a
ledger is opened from, a `custom` directive, and the documents a ledger names, which hearth
neither copies nor opens.

Importing again replaces the base, until a member makes a review ruling after it: from then on
rulings rest on the base as it is, so it is refused, with a sentence saying why.
"""

import os
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from beancount.core.data import Balance, Directive, Document, Open, Price
from beancount.parser import _parser, grammar, printer  # type: ignore[attr-defined]

from hearth import base, books, carrying, db
from hearth.base import Base, BaseError, Imported
from hearth.config import LEDGER_VARIABLE, Settings
from hearth.plaid import booking, feed
from hearth.reports.balances import check_balance_assertions
from hearth.reports.common import is_balance_sheet
from hearth.review.rulings import RulingStore
from hearth.valuation.holdings import holdings_at
from hearth.valuation.networth import NetWorth, PriceLookup, value_holdings
from hearth.valuation.quotes import Quote

LEDGER_PRICE = "ledger"
"""The source of a price the ledger itself holds, as the check values its holdings at."""


class ImportRefused(Exception):  # noqa: N818 - it names a refusal, as `booking.Refused` does
    """Nothing was imported, for the reason given, which names no amount and no payee."""


@dataclass(frozen=True, slots=True)
class Unused:
    """A line the base keeps but hearth does not use: its file, relative to the base, its line
    and what it is."""

    file: str
    line: int
    what: str


@dataclass(frozen=True, slots=True)
class Check:
    """What the base holds, as hearth reads it, for a person to hold against their own."""

    files: int
    entries: int
    accounts: int
    first: date | None
    last: date | None
    assertions: int
    worth: NetWorth
    runs: tuple[str, ...]
    unused: tuple[Unused, ...]


@dataclass(frozen=True, slots=True)
class Outcome:
    imported: Imported
    check: Check
    replaced: Imported | None = None
    """The base named before, which a new one replaced; it stays in its own folder."""
    unchanged: bool = False
    """The ledger was the base already, byte for byte: nothing was written."""
    carried: tuple[Carried, ...] = ()
    """Each matched account hearth carries on from the base, and what that meets."""


def import_ledger(settings: Settings, source: Path, *, now: datetime) -> Outcome:
    """Import the ledger opened from `source` as the household's base, or raise
    `ImportRefused` having written nothing.

    `now` is the clock's moment (``hearth.clock``), never read here, so the caller decides it.
    Every day the import names, today and the day a base was imported, is that moment on the
    household's own calendar, as every figure hearth shows takes it (``books.compute``): the
    machine's local day, not Greenwich's."""
    if settings.ledger is not None:
        raise ImportRefused(
            f"{LEDGER_VARIABLE} is set, to {settings.ledger}: hearth would then be given two"
            f" ledgers, which it refuses. Unset {LEDGER_VARIABLE} and import again; a hearth"
            " serving with it set refuses to compute once a ledger is imported."
        )
    source = Path(os.path.normpath(source.absolute()))
    try:
        # Beancount reports a ledger it cannot open as an error in it, which names no cause.
        source.open("rb").close()
    except OSError as error:
        raise ImportRefused(f"{source} cannot be read: {error.strerror}.") from error
    try:
        before = base.current(settings.ledger_base)
    except BaseError as error:
        raise ImportRefused(str(error)) from error
    newest = _newest_ruling(settings)
    if before is not None and newest > before.rulings_through:
        since = newest - before.rulings_through
        raise ImportRefused(
            f"The ledger imported on {_day(before.imported_at.astimezone().date())} is not"
            f" replaced: {since} review ruling{'s have' if since > 1 else ' has'} been made"
            " since, and rulings rest on the ledger as it was imported."
        )
    if before is not None:
        _not_carried_on(settings, before, now.astimezone().date())

    original = _load(source)
    _assertions_pass(original.entries)
    _, names = base.layout(original.files, source)
    root = next(name for name, path in names.items() if path == source)
    try:
        contents = {name: path.read_bytes() for name, path in names.items()}
    except OSError as error:
        raise ImportRefused(f"{error.filename} cannot be read: {error.strerror}.") from error
    today = now.astimezone().date()

    if before is not None and before.base.digest == base.digest(root, contents):
        copy = _load(before.base)
        check = _check(copy, before.base, _noted(before.base), today, now)
        return Outcome(
            before, check, unchanged=True, carried=_carried(settings, copy.entries, today)
        )

    with base.staged(settings.ledger_base, root, contents) as staged:
        noted = _noted(staged)
        _includes_stay_inside(noted)
        copy = _load(staged)
        if _shape(copy.entries, staged.root_path.parent) != _shape(original.entries, source.parent):
            raise ImportRefused(
                "The copy does not read as the ledger does, so it was not kept. Was the ledger"
                " changed while it was being imported?"
            )
        check = _check(copy, staged, noted, today, now)
        imported = base.commit(
            settings.ledger_base, staged, source=source, now=now, rulings_through=newest
        )
    return Outcome(
        imported, check, replaced=before, carried=_carried(settings, copy.entries, today)
    )


def _not_carried_on(settings: Settings, before: Imported, today: date) -> None:
    """Refuse to replace a base hearth carries an account on from once carrying has begun
    (``books.begun``): a ruling on a row carried on from its cut-over rests on
    the base as it is, and another base would move the cut-over under them."""
    if not settings.database.exists():
        return
    try:
        begun = books.begun_in(settings, today)
    except books.LedgerError as error:
        raise ImportRefused(
            f"Hearth cannot tell whether it carries an account on from the ledger imported on"
            f" {_day(before.imported_at.astimezone().date())}, so it is not replaced:"
            f" {error.summary}"
        ) from error
    if begun:
        accounts = ", ".join(sorted(begun))
        raise ImportRefused(
            f"The ledger imported on {_day(before.imported_at.astimezone().date())} is not"
            f" replaced: hearth carries {accounts} on from it, and a decision has been made in"
            " Review on transactions carried on there. Those rest on the ledger as it was"
            " imported, and another one would move where they begin."
        )


@dataclass(frozen=True, slots=True)
class Carried:
    """An account the household matched to a connection that hearth carries on from the
    base just imported: from which day, and how many of the bank's transactions before then
    the base does not hold, which hearth never brings in (``hearth.carrying``)."""

    account: str
    cut_over: date
    before: int
    postponed: bool = False
    """Its cut-over is after today, so it is not carried on yet (``carrying.postponed``)."""


def _carried(
    settings: Settings, entries: tuple[Directive, ...], today: date
) -> tuple[Carried, ...]:
    """What carrying each matched account on from `entries`, the base being imported, meets."""
    if not settings.database.exists() or not settings.feed_database.exists():
        return ()
    held = carrying.read(entries)
    conn = db.connect(settings.database)
    try:
        with feed.Feed.open(settings.feed_database) as fed:
            plans = booking.carried_plans(conn, fed, held, today)
            later = carrying.postponed(held, booking.feeding(conn, held.opened), today)
    except booking.Refused:
        return ()
    finally:
        conn.close()
    found = [Carried(account, plan.cut_over, len(plan.before)) for account, plan in plans.items()]
    found += [Carried(account, day, 0, postponed=True) for account, day in later.items()]
    return tuple(sorted(found, key=lambda c: c.account))


def _newest_ruling(settings: Settings) -> int:
    """The newest review ruling's id, 0 for none; a data directory with no household yet has
    none, and is not made one."""
    if not settings.database.exists():
        return 0
    db.initialise(settings.database)
    conn = db.connect(settings.database)
    try:
        return RulingStore(conn).newest()
    finally:
        conn.close()


def _load(ledger: Path | Base) -> books.Loaded:
    try:
        return books.load_ledger(ledger)
    except books.LedgerError as error:
        raise ImportRefused(f"The ledger does not load cleanly. {error.summary}") from error


def _assertions_pass(entries: tuple[Directive, ...]) -> None:
    """Refuse a ledger whose postings do not reproduce one of its balance assertions, which
    hearth's books would refuse to compute from; say where it is, not by how much."""
    for check in check_balance_assertions(entries):
        if check.agrees:
            continue
        where = next(
            (
                e.meta
                for e in entries
                if isinstance(e, Balance) and e.account == check.account and e.date == check.date
            ),
            None,
        )
        at = f", at {Path(where['filename']).name}, line {where['lineno']}" if where else ""
        raise ImportRefused(
            f"A balance assertion fails: on {_day(check.date)}{at}, the postings do not add up"
            " to what it asserts."
        )


@dataclass(frozen=True, slots=True)
class _Noted:
    """A directive hearth looks at before loading: in which file, on which line."""

    file: str
    line: int
    kind: str
    value: str


def _noted(frozen: Base) -> list[_Noted]:
    """Every plugin, include, `documents` option, document and custom directive in the base's
    files, where it is: the parser's own reading of each, so a line inside a string is never
    mistaken for one."""
    noted: list[_Noted] = []
    for name in frozen.files:
        builder = _Noting(name)
        with (frozen.directory / name).open("rb") as file:
            _parser.Parser(builder).parse(file, filename=str(frozen.directory / name), lineno=1)
        noted += builder.noted
    return noted


class _Noting(grammar.Builder):
    """Beancount's own builder, noting the directives `_noted` looks for as it builds."""

    def __init__(self, name: str) -> None:
        super().__init__()  # type: ignore[no-untyped-call]
        self.name = name
        self.noted: list[_Noted] = []

    def _note(self, lineno: int, kind: str, value: object) -> None:
        self.noted.append(_Noted(self.name, lineno, kind, str(value)))

    def plugin(self, filename: str, lineno: int, name: str, config: Any) -> Any:
        self._note(lineno, "plugin", name)
        return super().plugin(filename, lineno, name, config)  # type: ignore[no-untyped-call]

    def include(self, filename: str, lineno: int, include_filename: str) -> Any:
        self._note(lineno, "include", include_filename)
        return super().include(filename, lineno, include_filename)  # type: ignore[no-untyped-call]

    def option(self, filename: str, lineno: int, key: str, value: Any) -> Any:
        if key == "documents":
            self._note(lineno, "documents", value)
        return super().option(filename, lineno, key, value)  # type: ignore[no-untyped-call]

    def document(self, filename: str, lineno: int, *args: Any) -> Any:
        self._note(lineno, "document", args[2])
        return super().document(filename, lineno, *args)  # type: ignore[no-untyped-call]

    def custom(self, filename: str, lineno: int, *args: Any) -> Any:
        self._note(lineno, "custom", args[1])
        return super().custom(filename, lineno, *args)  # type: ignore[no-untyped-call]


def _includes_stay_inside(noted: list[_Noted]) -> None:
    """Refuse an include by an absolute path: in the copy it would still name the original,
    so the base would read a file it does not hold."""
    for note in noted:
        if note.kind == "include" and Path(note.value).expanduser().is_absolute():
            raise ImportRefused(
                f"{note.file}, line {note.line}, includes {note.value} by its absolute path,"
                " which a frozen copy would still read from where it is. Make it relative to"
                " the file that includes it, and import again."
            )


def _shape(entries: tuple[Directive, ...], folder: Path) -> list[str]:
    """The entries as beancount prints them, which leaves out the file and line each is on,
    with each document's file named relative to `folder`, as it was written."""
    shaped = []
    for entry in entries:
        if isinstance(entry, Document):
            entry = entry._replace(filename=os.path.relpath(entry.filename, folder))
        shaped.append(printer.format_entry(entry))
    return shaped


def _check(
    loaded: books.Loaded, frozen: Base, noted: list[_Noted], today: date, now: datetime
) -> Check:
    entries = loaded.entries
    currency = next(iter(loaded.options["operating_currency"]), books.CURRENCY)
    held = [h for h in holdings_at(entries, today) if is_balance_sheet(h.account)]
    worth = value_holdings(held, _priced(entries, now), as_of=today, currency=currency)
    top = [n for n in noted if n.file == frozen.root]
    runs = tuple(n.value for n in top if n.kind == "plugin" and books.runs(n.value))
    used = {id(n) for n in top if n.kind == "plugin" and books.runs(n.value)}
    unused = tuple(
        Unused(n.file, n.line, _unused(n, n.file == frozen.root))
        for n in noted
        if n.kind != "include" and id(n) not in used
    )
    days = [e.date for e in entries]
    return Check(
        files=len(frozen.files),
        entries=len(entries),
        accounts=len({e.account for e in entries if isinstance(e, Open)}),
        first=min(days, default=None),
        last=max(days, default=None),
        assertions=sum(1 for e in entries if isinstance(e, Balance)),
        worth=worth,
        runs=runs,
        unused=tuple(sorted(unused, key=lambda u: (u.file != frozen.root, u.file, u.line))),
    )


def _unused(note: _Noted, top: bool) -> str:
    match note.kind:
        case "plugin" if not top:
            return (
                f'plugin "{note.value}", in an included file, where beancount itself does not'
                " read plugins"
            )
        case "plugin" if note.value in books.SKIPPED:
            return f'plugin "{note.value}": hearth does not look for documents'
        case "plugin":
            return f'plugin "{note.value}", which is not beancount\'s own, so hearth never runs it'
        case "documents":
            return f'option "documents" "{note.value}": hearth does not look for documents'
        case "document":
            return "document: hearth neither copies nor opens the file it names"
        case _:
            return f'custom "{note.value}"'


def _priced(entries: tuple[Directive, ...], now: datetime) -> PriceLookup:
    """The newest price the ledger holds for a commodity on or before a day."""
    prices: dict[str, list[Price]] = {}
    for entry in entries:
        number = entry.amount.number if isinstance(entry, Price) else None
        if isinstance(entry, Price) and isinstance(number, Decimal) and number > 0:
            prices.setdefault(entry.currency, []).append(entry)

    def lookup(symbol: str, on_or_before: date) -> Quote | None:
        found = [p for p in prices.get(symbol, ()) if p.date <= on_or_before]
        if not found:
            return None
        newest = max(found, key=lambda p: p.date)
        number = newest.amount.number
        assert isinstance(number, Decimal)  # noqa: S101 - only priced entries are kept
        return Quote(
            symbol, newest.date, number, newest.amount.currency, LEDGER_PRICE, "price", now
        )

    return lookup


def describe(outcome: Outcome) -> list[str]:
    """What the import says: the check, and where the base now is."""
    check, imported = outcome.check, outcome.imported
    frozen = imported.base
    worth = check.worth
    if check.first is not None and check.last is not None:
        span = f", from {_day(check.first)} to {_day(check.last)}"
    else:
        span = ""
    assertions = f"{check.assertions}, all of which pass" if check.assertions else "none to check"
    total = f"{_money(worth.known_total)} {worth.currency}"
    if worth.complete:
        value = f"{total} on {_day(worth.as_of)}, at the ledger's own prices"
    else:
        without = ", ".join(f"{p.holding.commodity} in {p.holding.account}" for p in worth.unpriced)
        value = (
            f"at least {total} on {_day(worth.as_of)}, at the ledger's own prices, without"
            f" what it has no price for in {worth.currency}: {without}"
        )
    files = f"{check.files} file{'s' if check.files != 1 else ''}"
    if outcome.unchanged:
        lines = [
            f"{imported.source} is hearth's base already, imported on"
            f" {_day(imported.imported_at.astimezone().date())}: nothing was written."
        ]
    else:
        lines = [f"Imported {imported.source}, {files}, as hearth's base."]
    lines += [
        f"  Entries: {check.entries}{span}",
        f"  Accounts: {check.accounts}",
        f"  Balance assertions: {assertions}",
        f"  Net worth today: {value}",
    ]
    if check.runs:
        lines.append(f"  Beancount's own plugins it runs: {', '.join(check.runs)}")
    for carried in outcome.carried:
        if carried.postponed:
            lines.append(
                f"  Not carried on yet: {carried.account}, which the ledger has an entry or a"
                " balance assertion on dated after today; carrying it on would begin on"
                f" {_day(carried.cut_over)}"
            )
            continue
        missing = (
            f"{carried.before} of the bank's transactions before then"
            f" {'is' if carried.before == 1 else 'are'} not in it and never brought in"
            if carried.before
            else "every one of the bank's transactions before then is in it"
        )
        lines.append(f"  Carried on from {_day(carried.cut_over)}: {carried.account}; {missing}")
    if check.unused:
        count = len(check.unused)
        lines.append(f"Kept but not used, {count} line{'s' if count != 1 else ''}:")
        lines += [f"  {u.file}, line {u.line}: {u.what}" for u in check.unused]
    else:
        lines.append("Every line is used.")
    lines.append(f"The base is in {frozen.directory}, digest {frozen.digest}.")
    if outcome.replaced is not None:
        before = outcome.replaced
        lines.append(
            "It replaces the base imported on"
            f" {_day(before.imported_at.astimezone().date())}, which stays in"
            f" {before.base.directory}."
        )
    if not outcome.unchanged:
        lines.append(
            "Hearth reads the base from now on, never the original, which it did not change."
        )
    return lines


def _day(day: date) -> str:
    return f"{day:%b} {day.day}, {day.year}"


def _money(number: Decimal) -> str:
    return f"{number:,.2f}"
