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
import io
import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from beancount.core.data import (
    Balance,
    Close,
    Commodity,
    Directive,
    Open,
    Price,
    Transaction,
)
from beancount.parser import _parser, grammar  # type: ignore[attr-defined]

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


class Refusal(StrEnum):
    """Why nothing was imported, for a screen that says it in its own words."""

    INHERITED = "inherited"
    """A ledger is inherited where it is (``HEARTH_LEDGER``)."""
    BASE_UNREADABLE = "base_unreadable"
    ONCE = "once"
    """A ledger is brought in once, and one is in (the app's import)."""
    RULED = "ruled"
    CARRIED = "carried"
    UNSURE = "unsure"
    UNREADABLE = "unreadable"
    NOT_A_FILE = "not_a_file"
    TOO_LARGE = "too_large"
    TOO_MANY = "too_many"
    NOT_DOWNLOADED = "not_downloaded"
    HEARTHS_OWN = "hearths_own"
    """A file is one hearth keeps itself, in its data directory."""
    ABSOLUTE_INCLUDE = "absolute_include"
    HOME_INCLUDE = "home_include"
    NOT_CLEAN = "not_clean"
    ASSERTION = "assertion"
    INCLUDE_MISSING = "include_missing"
    """An include matches no file."""
    INCLUDE_OUTSIDE = "include_outside"
    """An include could lead out of the folder that holds every file the ledger reads."""
    LINE_TOO_LONG = "line_too_long"
    TOO_SLOW = "too_slow"
    """Reading it took longer than `hearth.ledger_child.LIMIT`, so it was stopped."""
    BUSY = "busy"
    """Another import held the base's lock for longer than `base.WAIT`."""
    LOCK_UNAVAILABLE = "lock_unavailable"
    """The file system the data directory is on cannot lock a file."""
    FAILED = "failed"
    """Reading it stopped on an unexpected error."""


class ImportRefused(Exception):  # noqa: N818 - it names a refusal, as `booking.Refused` does
    """Nothing was imported, for the reason given, which names no amount and no payee: in a
    sentence for a terminal, which may name paths and commands, and as `why` with `plain`, a
    sentence for the Mac app that names neither a path nor a command nor a variable."""

    def __init__(self, said: str, why: Refusal, plain: str) -> None:
        super().__init__(said)
        self.why = why
        self.plain = plain


def import_ledger(
    settings: Settings, source: Path, *, now: datetime, once: bool = False, held: bool = False
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

    Every import holds the base's lock (``base.locked``) from asking whether the household may
    import through naming the new base, so two at once, from the app and the command line or
    two apps, take turns, and the second is refused or replaces the first as it would have
    later, never by racing it. Each file is read once (`_gathered`), and what is loaded is the
    copy of those bytes, never the files again. `held` says the caller holds the lock already
    and handed it to this process (``hearth.ledger_child``), which then does not wait on it.

    What a check or an import that died left in the data directory is swept first."""
    with nullcontext() if held else locked(settings):
        base.sweep(settings.data_dir)
        base.sweep_checks(settings.data_dir)
        before = household_allows(settings, now, once=once)
        gathered = _gathered(source, settings.data_dir)
        source, root, contents = gathered.source, gathered.root, gathered.contents
        newest = _newest_ruling(settings)
        today = now.astimezone().date()

        if before is not None and before.base.digest == base.digest(root, contents):
            copy = _load(before.base)
            check = _check(copy, before.base, _noted(before.base), today, now)
            return Outcome(
                before, check, unchanged=True, carried=_carried(settings, copy.entries, today)
            )

        # Staged beside the base's directory, which is made only when a base is kept in it.
        with base.staged(settings.data_dir, root, contents) as staged:
            _staged_holds(staged, gathered)
            copy = _load(staged)
            _assertions_pass(copy.entries)
            check = _check(copy, staged, list(gathered.noted), today, now)
            imported = base.commit(
                settings.ledger_base, staged, source=source, now=now, rulings_through=newest
            )
    return Outcome(
        imported, check, replaced=before, carried=_carried(settings, copy.entries, today)
    )


@contextmanager
def locked(settings: Settings) -> Iterator[int]:
    """The base's lock (``base.locked``) and the descriptor that holds it, or a refusal in
    words: another import held it too long, or the data directory's file system cannot lock a
    file."""
    try:
        with base.locked(settings.ledger_base) as descriptor:
            yield descriptor
    except base.LockTimedOut as error:
        raise ImportRefused(
            "Another import of a ledger is still running, and did not finish in time. Try again"
            " once it has.",
            Refusal.BUSY,
            "Another import of a ledger is still running on this Mac. Try again once it has"
            " finished.",
        ) from error
    except base.LockUnavailable as error:
        raise ImportRefused(
            f"The data directory's file system cannot lock a file ({error}), so an import"
            " cannot be kept from racing another.",
            Refusal.LOCK_UNAVAILABLE,
            "Hearth's data is on a disk that cannot keep two imports from running at once, so"
            " nothing is brought in there.",
        ) from error


def household_allows(settings: Settings, now: datetime, *, once: bool) -> Imported | None:
    """The base the household holds now, if any; or raise `ImportRefused` when the household
    may not import a ledger: `HEARTH_LEDGER` is set, its base cannot be read, it holds one and
    `once` says a ledger is imported once, or rulings rest on the base it holds."""
    if settings.ledger is not None:
        raise ImportRefused(
            f"{LEDGER_VARIABLE} is set, to {settings.ledger}: hearth would then be given two"
            f" ledgers, which it refuses. Unset {LEDGER_VARIABLE} and import again; a hearth"
            " serving with it set refuses to compute once a ledger is imported.",
            Refusal.INHERITED,
            "Hearth reads your ledger where it is, so another cannot be brought in.",
        )
    try:
        before = base.current(settings.ledger_base)
    except BaseError as error:
        raise ImportRefused(
            str(error),
            Refusal.BASE_UNREADABLE,
            "The ledger Hearth already holds cannot be read, so another cannot be brought in.",
        ) from error
    if before is not None and once:
        day = _day(before.imported_at.astimezone().date())
        raise ImportRefused(
            f"Your ledger was brought in on {day}, and a ledger is brought in once: hearth's"
            " books are the truth from then on.",
            Refusal.ONCE,
            f"Your ledger was brought in on {day}. A ledger is brought in once, and Hearth's"
            " books are the truth from then on.",
        )
    newest = _newest_ruling(settings)
    if before is not None and newest > before.rulings_through:
        since = newest - before.rulings_through
        day = _day(before.imported_at.astimezone().date())
        raise ImportRefused(
            f"The ledger imported on {day} is not replaced: {since} review"
            f" ruling{'s have' if since > 1 else ' has'} been made since, and rulings rest on"
            " the ledger as it was imported.",
            Refusal.RULED,
            f"The ledger brought in on {day} is not replaced: decisions made in Review since"
            " rest on it as it is.",
        )
    if before is not None:
        _not_carried_on(settings, before, now.astimezone().date())
    return before


LARGEST = 64 * 1024 * 1024
"""The most bytes a ledger may read, every file it includes together: far more than any
household's text, and a bound on what a file chosen by mistake, a film say, costs to look at."""

MOST_FILES = 1000
"""The most files a ledger may read: more than any household's ledger includes, and a bound
on what a glob that matches a folder of photographs costs."""

LONGEST_LINE = 64 * 1024
"""The longest line a ledger may have, in bytes: a ledger's lines are a few hundred bytes at
most, and beancount's parser takes time that grows with a line's length for every byte on it,
so the slowest file within `LARGEST` is one of lines this long, which a check reads in a few
seconds (``docs/real-ledger.md``)."""

DATALESS = 0x40000000
"""`SF_DATALESS`, the flag macOS sets on a file iCloud keeps in the cloud alone: reading it
would wait on the network."""


@dataclass(frozen=True, slots=True)
class Examined:
    """A ledger as hearth would import it: the file it is opened from, every file it reads by
    its path relative to the folder that holds them all, with their bytes, read once (`root` is
    the one it is opened from), what a copy of those bytes loads as, and the check a person
    holds against their own figures."""

    source: Path
    loaded: books.Loaded
    root: str
    contents: dict[str, bytes]
    check: Check


def examine(
    settings: Settings, source: Path, *, now: datetime, staging: Path | None = None
) -> Examined:
    """Read the ledger opened from `source` and work out its check, writing nothing that
    outlasts the call; or raise `ImportRefused`, saying why it cannot be imported.

    Each file is read once, as a base would hold it (`_gathered`), and beancount loads a
    private copy of those bytes in hearth's data directory, gone when the check ends, never the
    files where they are: so a file changed or swapped for a pipe after it was read is never
    opened again, and nothing is written at or beside the ledger. `staging` is the folder the
    copy is kept in, one the caller holds (``base.checking``), else one held here."""
    gathered = _gathered(source, settings.data_dir)
    source, root, contents = gathered.source, gathered.root, gathered.contents
    today = now.astimezone().date()
    with ExitStack() as stack:
        if staging is None:
            staging, _ = stack.enter_context(base.checking(settings.data_dir))
        staged = stack.enter_context(base.staged(staging, root, contents, prefix=base.CHECKING))
        _staged_holds(staged, gathered)
        loaded = _load(staged)
        check = _check(loaded, staged, list(gathered.noted), today, now)
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
    refused: ImportRefused | None

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


def check_ledger(
    settings: Settings, source: Path, *, now: datetime, staging: Path | None = None
) -> Checked:
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
        examined = examine(settings, source, now=now, staging=staging)
    except ImportRefused as refused:
        return Checked(None, refused)
    try:
        _assertions_pass(examined.loaded.entries)
    except ImportRefused as refused:
        return Checked(examined, refused)
    return Checked(examined, None)


@dataclass(frozen=True, slots=True)
class Summary:
    """What a ledger file holds, as the check reports it, in plain data a process may hand
    another (``hearth.ledger_child``): nothing of its entries but what the check counts."""

    files: int
    entries: int
    first: date | None
    last: date | None
    accounts: tuple[Opened, ...]
    assertions: int
    failing: int
    currencies: tuple[str, ...]
    commodities: tuple[str, ...]
    worth: Decimal
    worth_currency: str
    worth_as_of: date
    worth_floor: bool
    unpriced: tuple[tuple[str, str], ...]
    runs: tuple[str, ...]
    unused: tuple[Unused, ...]

    @classmethod
    def of(cls, checked: Checked) -> Summary | None:
        examined = checked.examined
        if examined is None:
            return None
        check = examined.check
        worth = check.worth
        return cls(
            files=check.files,
            entries=check.entries,
            first=check.first,
            last=check.last,
            accounts=checked.accounts,
            assertions=check.assertions,
            failing=checked.failing,
            currencies=checked.currencies,
            commodities=checked.commodities,
            worth=worth.known_total,
            worth_currency=worth.currency,
            worth_as_of=worth.as_of,
            worth_floor=not worth.complete,
            unpriced=tuple((p.holding.commodity, p.holding.account) for p in worth.unpriced),
            runs=check.runs,
            unused=check.unused,
        )

    def wire(self) -> dict[str, object]:
        return {
            "files": self.files,
            "entries": self.entries,
            "first": _iso(self.first),
            "last": _iso(self.last),
            "accounts": [
                [o.account, o.kind, o.opened.isoformat(), _iso(o.closed)] for o in self.accounts
            ],
            "assertions": self.assertions,
            "failing": self.failing,
            "currencies": list(self.currencies),
            "commodities": list(self.commodities),
            "worth": str(self.worth),
            "worth_currency": self.worth_currency,
            "worth_as_of": self.worth_as_of.isoformat(),
            "worth_floor": self.worth_floor,
            "unpriced": [list(u) for u in self.unpriced],
            "runs": list(self.runs),
            "unused": [[u.file, u.line, u.what, u.kind] for u in self.unused],
        }

    @classmethod
    def unwire(cls, found: dict[str, Any]) -> Summary:
        return cls(
            files=int(found["files"]),
            entries=int(found["entries"]),
            first=_day_of(found["first"]),
            last=_day_of(found["last"]),
            accounts=tuple(
                Opened(a, k, date.fromisoformat(o), _day_of(c)) for a, k, o, c in found["accounts"]
            ),
            assertions=int(found["assertions"]),
            failing=int(found["failing"]),
            currencies=tuple(found["currencies"]),
            commodities=tuple(found["commodities"]),
            worth=Decimal(found["worth"]),
            worth_currency=str(found["worth_currency"]),
            worth_as_of=date.fromisoformat(found["worth_as_of"]),
            worth_floor=bool(found["worth_floor"]),
            unpriced=tuple((c, a) for c, a in found["unpriced"]),
            runs=tuple(found["runs"]),
            unused=tuple(Unused(f, int(n), w, k) for f, n, w, k in found["unused"]),
        )


def _iso(day: date | None) -> str | None:
    return None if day is None else day.isoformat()


def _day_of(text: str | None) -> date | None:
    return None if text is None else date.fromisoformat(text)


@dataclass(frozen=True, slots=True)
class Gathered:
    """Every file a ledger reads, each read once: by its path relative to the folder that holds
    them all, with its bytes and the includes it names (each line and pattern), and `root`, the
    one it is opened from."""

    source: Path
    root: str
    contents: dict[str, bytes]
    includes: dict[str, tuple[tuple[int, str], ...]]
    noted: tuple[_Noted, ...]
    """Every plugin, include, document and custom directive, where it is, as the files were
    parsed once to find their includes, which the check reports without parsing them again."""


def _gathered(source: Path, keeping: Path) -> Gathered:
    """Every file the ledger opened from `source` reads, each read once; or raise
    `ImportRefused`.

    Includes are found as beancount's loader finds them, a glob relative to the file that has
    it, from the bytes already read, never by opening a file again. A file is opened without
    waiting, and only what was opened is looked at (`_read_once`): a regular file, not one
    iCloud has not downloaded, not one in `keeping`, hearth's own data directory, whatever path
    or link leads to it; at most `MOST_FILES` files and `LARGEST` bytes in all, and no line
    longer than `LONGEST_LINE`, all counted before a file is parsed. An include by an absolute
    path, or from the home folder, is refused before it is followed, as is one that matches no
    file at all, and one that leads out of the folder that holds every file the ledger reads
    (`_contained`): in the copy beancount loads, such an include would find whatever lies
    beside the copy, hearth's own books among it."""
    source = Path(os.path.normpath(source.absolute()))
    kept = _kept(keeping)
    read: dict[Path, bytes] = {}
    named: dict[Path, tuple[tuple[int, str], ...]] = {}
    notes: dict[Path, list[_Noted]] = {}
    total = 0
    waiting = [source]
    while waiting:
        path = waiting.pop(0)
        if path in read:
            continue
        if len(read) >= MOST_FILES:
            raise ImportRefused(
                f"{source} reads more than {MOST_FILES:,} files, which no ledger does.",
                Refusal.TOO_MANY,
                f"It includes more than {MOST_FILES:,} files, which no ledger does.",
            )
        name = _named(path, source)
        data = _read_once(path, name, keeping, kept, LARGEST - total)
        total += len(data)
        _lines_fit(data, path, name)
        read[path] = data
        builder = _Noting(name)
        _parser.Parser(builder).parse(io.BytesIO(data), filename=str(path), lineno=1)
        notes[path] = builder.noted
        included = [note for note in builder.noted if note.kind == "include"]
        _includes_stay_inside(included)
        named[path] = tuple((note.line, note.value) for note in included)
        for note in included:
            found = _globbed(path.parent, note.value)
            if not found:
                raise ImportRefused(
                    f"{note.file}, line {note.line}, includes {note.value}, which matches no file.",
                    Refusal.INCLUDE_MISSING,
                    f"{note.file}, line {note.line}, includes a file that is not there. Put it"
                    " back, or take the include out, then choose the ledger again.",
                )
            waiting += [Path(os.path.normpath(name)) for name in sorted(found)]
    _, names = base.layout(list(read), source)
    by_path = {path: name for name, path in names.items()}
    gathered = Gathered(
        source,
        by_path[source],
        {name: read[path] for name, path in names.items()},
        {by_path[path]: found for path, found in named.items()},
        tuple(
            replace(note, file=by_path[path])
            for path in sorted(notes, key=lambda p: by_path[p])
            for note in notes[path]
        ),
    )
    _contained(gathered)
    return gathered


def _globbed(folder: Path, pattern: str) -> list[str]:
    """What an include matches, exactly as beancount's loader globs it, which Path.glob does
    not."""
    return glob.glob(os.path.join(folder, pattern), recursive=True)  # noqa: PTH118, PTH207


def _contained(gathered: Gathered) -> None:
    """Refuse an include that could lead out of `common`, the folder that holds every file the
    ledger reads, wherever the ledger is copied.

    Its pattern is walked a part at a time from the file that names it, and never climbs above
    that folder, not even on the way back down into it (`a/../../x/b` from a file in `a` passes
    through the folder's parent, which in a copy is the folder the copy sits in), nor has a
    `..` after a wildcard, which `glob` takes literally. What the pattern can reach is then the
    folder's own tree, in the original and in any copy, by its shape alone, whatever happens to
    lie beside either: so a copy can never read a file beside it. A pattern that matched nothing
    in the original is refused before this (`_gathered`), as nothing it matches would hold the
    folder open above it."""
    for name, includes in gathered.includes.items():
        start = len(PurePosixPath(name).parent.parts)
        for line, value in includes:
            depth, wild = start, False
            for part in PurePosixPath(value).parts:
                wild = wild or glob.has_magic(part)
                if part == "..":
                    depth -= 1
                elif part != ".":
                    depth += 1
                if depth < 0 or (wild and part == ".."):
                    raise ImportRefused(
                        f"{name}, line {line}, includes {value}, which leads out of the folder"
                        " that holds every file the ledger reads. Make it name only files beside"
                        " the ledger, and import again.",
                        Refusal.INCLUDE_OUTSIDE,
                        f"{name}, line {line}, includes files outside your ledger's own folder."
                        " Make it name only files beside the ledger, then choose it again.",
                    )


def _staged_holds(staged: Base, gathered: Gathered) -> None:
    """Before beancount loads the copy: every include of every copied file, globbed in the
    copy as beancount will glob it, finds files of the copy and nothing else, and finds at
    least one. A second wall behind `_contained`, so the copy can never read past itself."""
    inside = {os.path.normpath(staged.directory / name) for name in staged.files}
    for name, includes in gathered.includes.items():
        folder = (staged.directory / name).parent
        for line, value in includes:
            found = {os.path.normpath(path) for path in _globbed(folder, value)}
            if not found or not found <= inside:
                raise ImportRefused(
                    f"{name}, line {line}, includes {value}, which a copy of the ledger would"
                    " not read as the ledger does.",
                    Refusal.INCLUDE_OUTSIDE,
                    f"{name}, line {line}, includes files outside your ledger's own folder."
                    " Make it name only files beside the ledger, then choose it again.",
                )


def _lines_fit(data: bytes, path: Path, name: str) -> None:
    """Refuse a file with a line longer than any ledger has, before it is parsed: beancount's
    parser takes time that grows with a line's length for every byte on it."""
    start = 0
    while True:
        end = data.find(b"\n", start)
        stop = len(data) if end < 0 else end
        if stop - start > LONGEST_LINE:
            raise ImportRefused(
                f"{path} has a line longer than {LONGEST_LINE // 1024} KB, which no ledger has.",
                Refusal.LINE_TOO_LONG,
                f"{name} has a line longer than {LONGEST_LINE // 1024} KB, which no ledger has,"
                " so it is not a ledger.",
            )
        if end < 0:
            return
        start = end + 1


def _kept(keeping: Path) -> frozenset[tuple[int, int]]:
    """The device and inode of every file in hearth's data directory, so a hard link to one of
    them, which no path comparison sees, is known for what it is."""
    found: set[tuple[int, int]] = set()
    for folder, _, files in os.walk(keeping):
        for name in files:
            try:
                status = os.lstat(os.path.join(folder, name))  # noqa: PTH118
            except OSError:
                continue
            found.add((status.st_dev, status.st_ino))
    return frozenset(found)


def _read_once(
    path: Path, name: str, keeping: Path, kept: frozenset[tuple[int, int]], room: int
) -> bytes:
    """`path`'s bytes, opened once without waiting and judged by what was opened: refused
    unless it is a regular file of at most `room` bytes that iCloud has downloaded and that is
    not one of hearth's own (`keeping`, `kept`)."""
    # By path, before anything is opened: the device-and-inode check below sees only files that
    # were in the data directory when it was walked (`_kept`), and this one also covers a file
    # hearth writes there after that, such as books a recompute writes anew meanwhile.
    if path.resolve().is_relative_to(keeping.resolve()):
        raise _hearths_own(path)
    try:
        # Non-blocking, so a pipe or a device answers at once rather than wait for a writer.
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as error:
        raise ImportRefused(
            f"{path} cannot be read: {error.strerror}.",
            Refusal.UNREADABLE,
            f"{name} cannot be read: {error.strerror}.",
        ) from error
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise ImportRefused(
                f"{path} is not a file, so it is not a ledger.",
                Refusal.NOT_A_FILE,
                f"{name} is not a file, so it is not a ledger.",
            )
        if (status.st_dev, status.st_ino) in kept:
            raise _hearths_own(path)
        if getattr(status, "st_flags", 0) & DATALESS:
            raise ImportRefused(
                f"{path} is kept in iCloud and is not downloaded to this Mac. Download it, and"
                " import again.",
                Refusal.NOT_DOWNLOADED,
                f"{name} is kept in iCloud and is not downloaded to this Mac. Download it in"
                " Finder, then choose the ledger again.",
            )
        if status.st_size > room:
            raise _too_large(path)
        with os.fdopen(descriptor, "rb", closefd=False) as file:
            data = file.read(room + 1)
    finally:
        os.close(descriptor)
    if len(data) > room:
        raise _too_large(path)
    return data


def _hearths_own(path: Path) -> ImportRefused:
    return ImportRefused(
        f"{path} is in hearth's own data directory, where it keeps its books, so it is not a"
        " ledger to import. Choose the ledger the household kept itself.",
        Refusal.HEARTHS_OWN,
        "That is one of the files Hearth keeps its own books in, not a ledger. Choose the"
        " ledger your household kept itself.",
    )


def _too_large(path: Path) -> ImportRefused:
    most = LARGEST // (1024 * 1024)
    return ImportRefused(
        f"{path} takes the ledger past {most} MB, which no ledger's text is.",
        Refusal.TOO_LARGE,
        f"The ledger and the files it includes come to more than {most} MB, which no ledger's"
        " text does.",
    )


def _named(path: Path, source: Path) -> str:
    """`path` as a person names it beside the ledger: from the ledger's own folder when it is
    inside it, else by its name alone; the ledger itself by its own name."""
    folder = source.parent
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
            f" {error.summary}",
            Refusal.UNSURE,
            "Hearth cannot tell yet whether decisions rest on the ledger it holds, so it is not"
            " replaced.",
        ) from error
    if begun:
        accounts = ", ".join(sorted(begun))
        raise ImportRefused(
            f"The ledger imported on {_day(before.imported_at.astimezone().date())} is not"
            f" replaced: hearth carries {accounts} on from it, and a decision has been made in"
            " Review on transactions carried on there. Those rest on the ledger as it was"
            " imported, and another one would move where they begin.",
            Refusal.CARRIED,
            "The ledger Hearth holds is not replaced: decisions made in Review rest on it as"
            " it is.",
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
        said = error.summary
        # Beancount's messages are never passed on, but where an error is may name a file; the
        # app is told only one inside the ledger's own copy, by its name, never a path.
        plain = (
            said.replace(" hearth ", " Hearth ")
            if "/" not in said
            else ("It reads a file that is not part of the ledger.")
        )
        raise ImportRefused(
            f"The ledger does not load cleanly. {said}",
            Refusal.NOT_CLEAN,
            f"It does not load cleanly. {plain}",
        ) from error


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
            " to what it asserts.",
            Refusal.ASSERTION,
            f"A balance check in it fails: on {_day(check.date)}{at}, its transactions do not"
            " add up to what it says.",
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
    so the base would read a file it does not hold. And one that starts with `~`, which
    beancount reads not as the home folder but as a folder named `~` beside the file, which
    is never what its author meant."""
    for note in noted:
        if note.kind != "include":
            continue
        if Path(note.value).is_absolute():
            raise ImportRefused(
                f"{note.file}, line {note.line}, includes {note.value} by its absolute path,"
                " which a frozen copy would still read from where it is. Make it relative to"
                " the file that includes it, and import again.",
                Refusal.ABSOLUTE_INCLUDE,
                f"{note.file}, line {note.line}, includes a file by its full path, which"
                " Hearth's copy would still read from where it is. Make the include relative"
                " to the file that has it, then choose the ledger again.",
            )
        if note.value.startswith("~"):
            raise ImportRefused(
                f"{note.file}, line {note.line}, includes {note.value}, which beancount reads"
                " as a folder named ~ beside the file, not the home folder. Make it relative"
                " to the file that includes it, and import again.",
                Refusal.HOME_INCLUDE,
                f"{note.file}, line {note.line}, includes a file starting with ~, which"
                " beancount reads as a folder named ~ beside the file, not your home folder."
                " Make the include relative to the file that has it, then choose the ledger"
                " again.",
            )


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
