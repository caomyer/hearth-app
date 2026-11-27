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

import glob
import os
import stat
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

from beancount.core.data import (
    Balance,
    Close,
    Commodity,
    Directive,
    Document,
    Open,
    Price,
    Transaction,
)
from beancount.parser import _parser, grammar, printer  # type: ignore[attr-defined]

from hearth import base, books, carrying, db
from hearth.base import Base, BaseError, Imported
from hearth.config import LEDGER_VARIABLE, Settings
from hearth.plaid import booking, feed
from hearth.reports.balances import check_balance_assertions
from hearth.reports.common import is_balance_sheet
from hearth.review.rulings import RulingStore
from hearth.valuation.holdings import holdings_at
from hearth.valuation.networth import NetWorth, value_holdings
from hearth.valuation.pricetable import PriceTable
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
    kind: str = ""
    """What kind of line it is: a `plugin`, a `custom` directive, a `document`, or the
    `documents` option."""


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


def import_ledger(
    settings: Settings, source: Path, *, now: datetime, once: bool = False
) -> Outcome:
    """Import the ledger opened from `source` as the household's base, or raise
    `ImportRefused` having written nothing.

    `now` is the clock's moment (``hearth.clock``), never read here, so the caller decides it.
    Every day the import names, today and the day a base was imported, is that moment on the
    household's own calendar, as every figure hearth shows takes it (``books.compute``): the
    machine's local day, not Greenwich's.

    With `once`, as the Mac app imports, a household that holds a base already is refused
    rather than given another: the app brings a ledger in once, and hearth's books are the
    truth from then on. The command line may still replace a base nobody has ruled on since.
    """
    before = household_allows(settings, now, once=once)
    examined = examine(source, now=now, keeping=settings.data_dir)
    source, original = examined.source, examined.loaded
    _assertions_pass(original.entries)
    root, contents = examined.root, examined.contents
    newest = _newest_ruling(settings)
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


def household_allows(settings: Settings, now: datetime, *, once: bool) -> Imported | None:
    """The base the household holds now, if any; or raise `ImportRefused` when the household
    may not import a ledger: `HEARTH_LEDGER` is set, its base cannot be read, it holds one and
    `once` says a ledger is imported once, or rulings rest on the base it holds."""
    if settings.ledger is not None:
        raise ImportRefused(
            f"{LEDGER_VARIABLE} is set, to {settings.ledger}: hearth would then be given two"
            f" ledgers, which it refuses. Unset {LEDGER_VARIABLE} and import again; a hearth"
            " serving with it set refuses to compute once a ledger is imported."
        )
    try:
        before = base.current(settings.ledger_base)
    except BaseError as error:
        raise ImportRefused(str(error)) from error
    if before is not None and once:
        raise ImportRefused(
            f"Your ledger was brought in on {_day(before.imported_at.astimezone().date())}, and"
            " a ledger is brought in once: hearth's books are the truth from then on."
        )
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
    return before


LARGEST = 64 * 1024 * 1024
"""The largest file a ledger may read, in bytes: far more than any household's text, and a
bound on what a file chosen by mistake, a film say, costs to look at."""


@dataclass(frozen=True, slots=True)
class Examined:
    """A ledger read where it is, writing nothing: what it loads as, every file it read by its
    path relative to the folder that holds them all, with their bytes (`root` is the one it is
    opened from), and the check a person holds against their own figures."""

    source: Path
    loaded: books.Loaded
    root: str
    contents: dict[str, bytes]
    check: Check


def examine(source: Path, *, now: datetime, keeping: Path) -> Examined:
    """Read the ledger opened from `source` where it is, and work out its check, writing
    nothing anywhere; or raise `ImportRefused`, saying why it cannot be imported.

    It is read as a base would be: regular files of at most `LARGEST` bytes, none of them in
    `keeping`, hearth's own data directory, and no include by an absolute path, which a frozen
    copy would still read from where it is. Each is refused before beancount follows any
    include (`_scan`), so no file beyond what a base may hold is ever opened, and nothing
    hearth keeps, its books or a base it froze, is ever read as a ledger to import. The check
    is the import's (`_check`), worked out from the files as they are.
    """
    source = Path(os.path.normpath(source.absolute()))
    _scan(source, keeping)
    loaded = _load(source)
    common, names = base.layout(loaded.files, source)
    root = next(name for name, path in names.items() if path == source)
    try:
        contents = {name: path.read_bytes() for name, path in names.items()}
    except OSError as error:
        raise ImportRefused(f"{error.filename} cannot be read: {error.strerror}.") from error
    where = Base(common, root, tuple(sorted(contents)), base.digest(root, contents))
    check = _check(loaded, where, _noted(where), now.astimezone().date(), now)
    return Examined(source, loaded, root, contents, check)


ROOTS = ("Assets", "Liabilities", "Equity", "Income", "Expenses")
"""Beancount's five kinds of account, in the order a balance sheet and then an income
statement read them."""


@dataclass(frozen=True, slots=True)
class Opened:
    """An account a ledger opens: its name, its kind (its root), and the days it is open."""

    account: str
    kind: str
    opened: date
    closed: date | None


@dataclass(frozen=True, slots=True)
class Checked:
    """What a ledger file holds, read where it is, and why it would not be imported if it would
    not (`check_ledger`). `examined` is None when the file does not load, as `refused` says."""

    examined: Examined | None
    refused: str | None

    @property
    def accounts(self) -> tuple[Opened, ...]:
        if self.examined is None:
            return ()
        entries = self.examined.loaded.entries
        closed = {e.account: e.date for e in entries if isinstance(e, Close)}
        found = [
            Opened(e.account, e.account.split(":", 1)[0], e.date, closed.get(e.account))
            for e in entries
            if isinstance(e, Open)
        ]
        order = {kind: n for n, kind in enumerate(ROOTS)}
        return tuple(sorted(found, key=lambda o: (order.get(o.kind, len(ROOTS)), o.account)))

    @property
    def currencies(self) -> tuple[str, ...]:
        """The ledger's operating currencies, as its options name them."""
        if self.examined is None:
            return ()
        return tuple(self.examined.loaded.options["operating_currency"])

    @property
    def commodities(self) -> tuple[str, ...]:
        """Every other commodity it holds or prices, by the name it gives it."""
        if self.examined is None:
            return ()
        named: set[str] = set()
        for entry in self.examined.loaded.entries:
            if isinstance(entry, (Commodity, Price)):
                named.add(entry.currency)
            elif isinstance(entry, Transaction):
                named |= {p.units.currency for p in entry.postings if p.units is not None}
        return tuple(sorted(named - set(self.currencies)))

    @property
    def failing(self) -> int:
        """How many of its balance assertions its postings do not reproduce."""
        if self.examined is None:
            return 0
        checks = check_balance_assertions(self.examined.loaded.entries)
        return sum(1 for check in checks if not check.agrees)


def check_ledger(settings: Settings, source: Path, *, now: datetime) -> Checked:
    """What the ledger opened from `source` holds, as the Mac app's import would bring it in,
    writing nothing anywhere: asked again, it reads the file again and answers the same.

    `refused` is the sentence the app's import (`import_ledger` with `once`) would refuse with,
    and None when it would go ahead. A household that may not import a ledger at all, as one
    that holds a base or inherits a ledger, is told why and nothing of the file: the ledger it
    keeps is every member's, and its file read again would tell whoever asked what hearth tells
    each member only of their own. Otherwise what the file holds is said beside a balance
    assertion of it that fails."""
    try:
        household_allows(settings, now, once=True)
        examined = examine(source, now=now, keeping=settings.data_dir)
    except ImportRefused as refused:
        return Checked(None, str(refused))
    try:
        _assertions_pass(examined.loaded.entries)
    except ImportRefused as refused:
        return Checked(examined, str(refused))
    return Checked(examined, None)


def _scan(source: Path, keeping: Path) -> None:
    """Refuse a ledger that is not regular files of at most `LARGEST` bytes outside `keeping`,
    or that includes a file by its absolute path, before beancount follows any include: each
    file is parsed for its includes alone, each resolved as the loader resolves it, a glob
    relative to the file that has it."""
    kept = keeping.resolve()
    seen: set[Path] = set()
    waiting = [source]
    while waiting:
        path = waiting.pop(0)
        if path in seen:
            continue
        seen.add(path)
        if path.resolve().is_relative_to(kept):
            raise ImportRefused(
                f"{path} is in hearth's own data directory, where it keeps its books, so it is"
                " not a ledger to import. Choose the ledger the household kept itself."
            )
        _regular(path)
        builder = _Noting(_named(path, source.parent))
        with path.open("rb") as file:
            _parser.Parser(builder).parse(file, filename=str(path), lineno=1)
        included = [note for note in builder.noted if note.kind == "include"]
        _includes_stay_inside(included)
        for note in included:
            # Exactly as beancount's loader globs it, which Path.glob does not.
            pattern = os.path.join(path.parent, note.value)  # noqa: PTH118
            found = glob.glob(pattern, recursive=True)  # noqa: PTH207
            waiting += [Path(os.path.normpath(name)) for name in sorted(found)]


def _regular(path: Path) -> None:
    """Refuse a path that is not a file a ledger may read: a folder, a pipe that would wait for
    a writer, a device, or one too large to be a household's text."""
    try:
        found = path.stat()
    except OSError as error:
        raise ImportRefused(f"{path} cannot be read: {error.strerror}.") from error
    if not stat.S_ISREG(found.st_mode):
        raise ImportRefused(f"{path} is not a file, so it is not a ledger.")
    if found.st_size > LARGEST:
        raise ImportRefused(
            f"{path} is larger than {LARGEST // (1024 * 1024)} MB, which no ledger's text is."
        )
    try:
        # Beancount reports a ledger it cannot open as an error in it, which names no cause.
        path.open("rb").close()
    except OSError as error:
        raise ImportRefused(f"{path} cannot be read: {error.strerror}.") from error


def _named(path: Path, folder: Path) -> str:
    """`path` as a person names it beside the ledger: from the ledger's own folder when it is
    inside it, else by its name alone."""
    if path.is_relative_to(folder):
        return PurePosixPath(*path.relative_to(folder).parts).as_posix()
    return path.name


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
        Unused(n.file, n.line, _unused(n, n.file == frozen.root), n.kind)
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


def _priced(entries: tuple[Directive, ...], now: datetime) -> PriceTable:
    """Every price the ledger holds, as the books' own price table holds stored ones: the
    newest on or before a day, and each commodity's first in a currency, so an imported
    ledger is valued, and converted at a first price, exactly as the books are."""
    return PriceTable.of(
        Quote(entry.currency, entry.date, number, entry.amount.currency, LEDGER_PRICE, "price", now)
        for entry in entries
        if isinstance(entry, Price)
        and isinstance(number := entry.amount.number, Decimal)
        and number > 0
    )


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
