"""Hearth's own books held against a ledger a household kept before, account by account.

A household that kept a ledger before it connected its banks holds something worth more than
any test: a ledger reconciled to the banks' statements says what each account held at each
statement's close, and what moved over each cycle. Where hearth's books, derived from what
the bank sent (``hearth.plaid``), and such a ledger disagree, hearth's books are wrong until
shown otherwise. `compare` reads both at checkpoints, the ends of statement cycles or of
months, and says for each account and each checkpoint whether they agree, by how much they
differ if not, and which rows make the difference.

Balances are at the end of each day, in the ledger's sign. Hearth books a row on the day the
bank posted it; a statement counts a row on that day too, and a ledger built from statements
records it as `post-date` when it books the row on the day it was made, so the ledger's rows
are read on their `post-date` where they carry one.

A ledger whose account holds nothing that says what it held, no balance assertion, no `pad`
and no posting against equity (`books.unanchored`'s rule), says only how it moved: a ledger
that starts part way through a card's life is one. So does hearth for an account whose
opening balance it could not derive. For such an account only what moved over each stretch
is compared, never a sum of rows as though it were a balance.

This is a check run by hand on the household's own machine (``hearth compare``). It reads,
never writes, and what it finds is the household's: it prints to the terminal and nothing
else.
"""

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path

from beancount.core.data import Balance, Directive, Open, Pad, Transaction

from hearth.books import REVIEW_EQUITY, unanchored
from hearth.months import Month
from hearth.plaid import opening
from hearth.reports.common import is_balance_sheet, root

CURRENCY = "USD"

POST_DATE = "post-date"
"""Where a ledger built from statements records the day the bank posted a row."""

APART = timedelta(days=7)
"""How far apart two books may date one row and still be read as the same row: a card's
charge and its posting, or a payment and its arrival, are a few days apart at most."""

GENERIC = frozenset(
    {
        "ach",
        "and",
        "card",
        "com",
        "credit",
        "debit",
        "for",
        "from",
        "online",
        "payment",
        "payments",
        "pos",
        "purchase",
        "recurring",
        "sale",
        "the",
        "thank",
        "transfer",
        "with",
        "www",
    }
)
"""Words a bank puts in any row's narration, which say nothing of whose row it is."""

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class Cycle:
    """A statement cycle on one account: the rows the bank posted from `start` to `end`."""

    account: str
    start: date
    end: date


def statement_cycles(path: Path) -> list[Cycle]:
    """The statement cycles a beancount_nextgen import reconciled, from its `run.json`.

    Only cycles the import found to agree to the cent are taken: they are what makes the
    ledger ground truth.
    """
    try:
        run = json.loads(path.read_text())
        found = run["checks"]["statement_cycles"]
        return [
            Cycle(cycle["account"], date.fromisoformat(start), date.fromisoformat(end))
            for cycle in found
            if cycle.get("ok")
            for start, end in [cycle["cycle"]]
        ]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ComparisonError(f"{path} holds no statement cycles hearth can read.") from error


class ComparisonError(Exception):
    """The comparison could not be made, and why, in a sentence."""


@dataclass(frozen=True, slots=True)
class Row:
    """One posting on the account compared: the day it counts on, its amount, its words.

    `booked` is the entry's own date where the ledger books the row on another day than it
    posted, as a statement import books a card's charge on the day it was made. `payee` is the
    entry's, as hearth books a merchant's cleaned name beside the bank's raw words.
    """

    day: date
    amount: Decimal
    narration: str
    booked: date | None = None
    payee: str = ""


@dataclass(frozen=True, slots=True)
class Apart:
    """One row both books hold, dated on either side of a checkpoint."""

    ours: Row
    theirs: Row


class Verdict(Enum):
    AGREES = "agrees"
    DATED_APART = "agrees but for the day rows are dated"
    DIFFERS = "DIFFERS"


@dataclass(frozen=True, slots=True)
class Step:
    """One account at one checkpoint: what each book holds at the end of `day`, and what moved
    in each since the end of `since`, the checkpoint before, if there is one.

    `theirs` is None where the ledger holds no balance for the account, only how it moved.
    The rows are those that make a difference: in what moved, rows held by one book alone,
    and rows `apart`, dated in this stretch by one book and outside it by the other; in the
    balance, rows `straddling` the checkpoint, dated on or before it by one book and after it
    by the other.
    """

    day: date
    since: date | None
    ours: Decimal
    theirs: Decimal | None
    moved_ours: Decimal | None = None
    moved_theirs: Decimal | None = None
    only_ours: tuple[Row, ...] = ()
    only_theirs: tuple[Row, ...] = ()
    apart: tuple[Apart, ...] = ()
    straddling: tuple[Apart, ...] = ()

    @property
    def balance_difference(self) -> Decimal | None:
        return None if self.theirs is None else self.ours - self.theirs

    @property
    def moved_difference(self) -> Decimal | None:
        if self.moved_ours is None or self.moved_theirs is None:
            return None
        return self.moved_ours - self.moved_theirs

    @property
    def verdict(self) -> Verdict:
        """Whether the books agree here; if not, whether rows dated differently are the whole
        of it: a ledger that books a card's charge on the day it was made, rather than the day
        it posted, differs from hearth that way across every month end."""
        balance, moved = self.balance_difference, self.moved_difference
        if not balance and not moved:
            return Verdict.AGREES
        dated = sum((p.ours.amount for p in self.straddling if p.ours.day <= self.day), ZERO) - sum(
            (p.theirs.amount for p in self.straddling if p.theirs.day <= self.day), ZERO
        )
        if not self.only_ours and not self.only_theirs and balance in (None, dated):
            return Verdict.DATED_APART
        return Verdict.DIFFERS


@dataclass(frozen=True, slots=True)
class Skipped:
    """A checkpoint asked for that one book does not reach, and why."""

    day: date
    why: str


@dataclass(frozen=True, slots=True)
class Account:
    """One account both books hold, compared at each checkpoint they both reach.

    `unbalanced` says why balances are not compared, where they are not: a book that does not
    say what the account held says only how it moved.
    """

    name: str
    steps: tuple[Step, ...]
    skipped: tuple[Skipped, ...] = ()
    unbalanced: str | None = None

    @property
    def compared(self) -> tuple[Step, ...]:
        """The steps that compare something: where balances are not compared, the first
        checkpoint only starts the first stretch."""
        return self.steps if self.unbalanced is None else self.steps[1:]


@dataclass(frozen=True, slots=True)
class Comparison:
    accounts: tuple[Account, ...]
    only_ours: tuple[str, ...]
    """Accounts hearth's books hold that the ledger does not, so nothing is compared."""


def compare(
    ours: Sequence[Directive],
    theirs: Sequence[Directive],
    *,
    through: date,
    cycles: Sequence[Cycle] = (),
    monthly: bool = False,
    accounts: Sequence[str] = (),
) -> Comparison:
    """Hold hearth's books, `ours`, known `through` a day, against a ledger, `theirs`.

    Each Assets and Liabilities account hearth's books open is compared where the ledger
    holds it too: at the close of each of its statement `cycles` and the day before each opens, and,
    `monthly`, at the first and last day both books hold it and every month end between.
    `accounts` narrows it to those named; a name that is none of those accounts is refused.
    """
    theirs_held = {p.account for e in theirs if isinstance(e, Transaction) for p in e.postings}
    balance_sheet = {e.account for e in ours if isinstance(e, Open) and is_balance_sheet(e.account)}
    strangers = [name for name in accounts if name not in balance_sheet]
    if strangers:
        raise ComparisonError(
            f"hearth's books open no Assets or Liabilities account {', '.join(strangers)}."
        )
    opened = sorted(name for name in balance_sheet if not accounts or name in accounts)
    unknown = unanchored(ours)
    found = []
    for name in opened:
        if name not in theirs_held:
            continue
        reconciled = [c for c in cycles if c.account == name]
        asked = {d for c in reconciled for d in (c.start - timedelta(1), c.end)}
        begins, ends = _span(theirs, name, reconciled)
        found.append(
            _account(
                name,
                ours,
                theirs,
                asked,
                monthly=monthly,
                begins=begins,
                until=min(ends, through),
                known=name not in unknown,
            )
        )
    return Comparison(tuple(found), tuple(name for name in opened if name not in theirs_held))


def _span(entries: Sequence[Directive], account: str, cycles: Sequence[Cycle]) -> tuple[date, date]:
    """The days a ledger holds every row of `account` on, on the day each posted.

    A ledger built from statements over a window of months books each card row on the day it
    was made and keeps the day it posted beside it: a charge made on the window's last day and
    posted after it is in the ledger, one made the day after and posted the same day is not.
    At its end the ledger is whole to the earlier of its last booking and last posting. At its
    start it is whole from the later of its first booking and first posting, never before the
    window though it keeps a row booked before it, as a reversal booked on the day of the
    charge it reverses; but it lacks a charge made before that and posted after it, so it is
    whole only from `APART` after, the most a row posts after it was made. A ledger that books
    every row of the account on the day it posted is whole over all of it.
    Each account is read on its own rows, as each closes its statements on its own days, and
    is whole too over each statement cycle on it the import reconciled.
    """
    held = [
        e
        for e in entries
        if isinstance(e, Transaction) and any(p.account == account for p in e.postings)
    ]
    booked = [e.date for e in held]
    posted = [_posted(e) for e in held]
    if not booked:
        return date.max, date.min
    lag = APART if booked != posted else timedelta(0)
    return (
        min([max(min(booked), min(posted)) + lag, *(c.start for c in cycles)]),
        max([min(max(booked), max(posted)), *(c.end for c in cycles)]),
    )


def _account(
    name: str,
    ours: Sequence[Directive],
    theirs: Sequence[Directive],
    asked: set[date],
    *,
    monthly: bool,
    begins: date,
    until: date,
    known: bool,
) -> Account:
    """One account, at the days `asked`, within what both books hold: the ledger's rows run
    from `begins` to `until`, and hearth's books know what the account holds if `known`."""
    our_all = list(_rows(ours, name, openings=True))
    their_all = list(_rows(theirs, name, openings=True))
    our_rows = list(_rows(ours, name))
    their_rows = list(_rows(theirs, name))
    first = _first_anchor(theirs, name)
    if first is None:
        unbalanced: str | None = "the ledger holds no balance for it"
    elif not known:
        unbalanced = "hearth does not know its balance"
    else:
        unbalanced = None
    balanced = unbalanced is None
    # Hearth's books hold an account from the day before its first row, where its opening
    # balance is when hearth knows it.
    starts_ours = min(
        [*(r.day for r in our_all), *(r.day - timedelta(1) for r in our_rows)], default=date.max
    )
    # A ledger that never says what the account held says how it moved from the day before
    # its first row on it: it holds every row from there.
    starts_theirs = first if first is not None else begins - timedelta(1)
    starts = max(starts_ours, starts_theirs)
    if monthly and starts <= until:
        asked = asked | {starts, *_month_ends(starts, until), until}
    days, skipped = [], []
    for day in sorted(asked):
        if day < starts_ours:
            skipped.append(Skipped(day, f"hearth's books hold it from {starts_ours} on"))
        elif day < starts_theirs:
            skipped.append(Skipped(day, f"the ledger holds it from {starts_theirs} on"))
        elif day > until:
            skipped.append(Skipped(day, f"the books both reach only to {until}"))
        else:
            days.append(day)
    steps = []
    matched = _match(our_rows, their_rows)
    for i, day in enumerate(days):
        since = days[i - 1] if i else None
        ours_now, theirs_now = _held(our_all, day), _held(their_all, day)
        straddling = tuple(p for p in matched.apart if (p.ours.day <= day) != (p.theirs.day <= day))
        if since is None:
            steps.append(
                Step(day, None, ours_now, theirs_now if balanced else None, straddling=straddling)
            )
            continue
        steps.append(
            Step(
                day,
                since,
                ours_now,
                theirs_now if balanced else None,
                ours_now - _held(our_all, since),
                theirs_now - _held(their_all, since),
                *_stretch(matched, since, day),
                straddling,
            )
        )
    return Account(name, tuple(steps), tuple(skipped), unbalanced)


def _rows(entries: Sequence[Directive], account: str, *, openings: bool = False) -> Iterator[Row]:
    """Every posting on `account`, on the day it posted. Hearth's opening balance is one only
    with `openings`: it says what the account held before its history, so it counts in the
    balance but is no row of the history the two books are matched on."""
    for entry in entries:
        if not isinstance(entry, Transaction) or (opening.anchors(entry) and not openings):
            continue
        day = _posted(entry)
        for posting in entry.postings:
            units = posting.units
            if posting.account != account or units is None or units.currency != CURRENCY:
                continue
            if not isinstance(units.number, Decimal):
                continue
            yield Row(
                day,
                units.number,
                entry.narration or entry.payee or "",
                entry.date if day != entry.date else None,
                entry.payee or "",
            )


def _posted(entry: Transaction) -> date:
    """The day `entry` counts on: the day the bank posted it, where the ledger says."""
    posted = (entry.meta or {}).get(POST_DATE)
    if isinstance(posted, str):
        posted = date.fromisoformat(posted)
    return posted if isinstance(posted, date) else entry.date


def _held(rows: Sequence[Row], day: date) -> Decimal:
    """What the rows add up to at the end of `day`."""
    return sum((r.amount for r in rows if r.day <= day), ZERO)


def _first_anchor(entries: Sequence[Directive], account: str) -> date | None:
    """The first day the ledger says what `account` held at its end, or None if it never
    does: a balance assertion on it, which holds at the start of its day, a `pad` into it, or
    a posting on it beside equity, as an opening balance is (`books.unanchored`'s rule).
    Before that, the ledger's balance is only what its rows add up to."""
    found = []
    for entry in entries:
        if isinstance(entry, Balance) and entry.account == account:
            found.append(entry.date - timedelta(1))
        elif (isinstance(entry, Pad) and entry.account == account) or (
            isinstance(entry, Transaction)
            and any(p.account == account for p in entry.postings)
            and any(
                root(p.account) == "Equity" and not p.account.startswith(REVIEW_EQUITY)
                for p in entry.postings
            )
        ):
            found.append(entry.date)
    return min(found, default=None)


def _month_ends(start: date, until: date) -> Iterator[date]:
    month = Month.of(start)
    while True:
        following = month.following()
        end = date(following.year, following.month, 1) - timedelta(1)
        if end > until:
            return
        if end >= start:
            yield end
        month = following


@dataclass(frozen=True, slots=True)
class _Matched:
    """Each book's rows, paired where both hold one row: on the same day, or `APART`."""

    only_ours: tuple[Row, ...]
    only_theirs: tuple[Row, ...]
    apart: tuple[Apart, ...]


def _match(ours: Sequence[Row], theirs: Sequence[Row]) -> _Matched:
    """Pair the rows both books hold: first those on the same day for the same amount, then,
    nearest first, those for the same amount up to `APART` from each other whose words
    agree. As in ``hearth.plaid.pairing``, an ambiguous match is not a match: a row with two
    candidates equally near pairs with neither, nor later with one further away."""
    same_day: dict[tuple[date, Decimal], list[Row]] = defaultdict(list)
    for row in theirs:
        same_day[row.day, row.amount].append(row)
    unpaired_ours = []
    for row in ours:
        if same_day[row.day, row.amount]:
            same_day[row.day, row.amount].pop()
        else:
            unpaired_ours.append(row)
    unpaired_theirs = [row for rows in same_day.values() for row in rows]
    by_gap: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for i, o in enumerate(unpaired_ours):
        for j, t in enumerate(unpaired_theirs):
            if o.amount == t.amount and abs(o.day - t.day) <= APART and _agree(o, t):
                by_gap[abs((o.day - t.day).days)].append((i, j))
    taken: set[tuple[str, int]] = set()
    tied: set[tuple[str, int]] = set()
    apart = []
    for gap in sorted(by_gap):
        free = [(i, j) for i, j in by_gap[gap] if not {("ours", i), ("theirs", j)} & (taken | tied)]
        uses = Counter(side for i, j in free for side in (("ours", i), ("theirs", j)))
        for i, j in free:
            sides = {("ours", i), ("theirs", j)}
            if all(uses[side] == 1 for side in sides):
                apart.append(Apart(unpaired_ours[i], unpaired_theirs[j]))
                taken |= sides
            else:
                tied |= sides
    return _Matched(
        tuple(r for i, r in enumerate(unpaired_ours) if ("ours", i) not in taken),
        tuple(r for j, r in enumerate(unpaired_theirs) if ("theirs", j) not in taken),
        tuple(apart),
    )


def _agree(ours: Row, theirs: Row) -> bool:
    """Whether two rows' narrations name the same thing: a word of three letters or more,
    other than one any bank row may carry, in both, read in each row's narration and payee."""
    return bool(_words(ours) & _words(theirs))


def _words(row: Row) -> set[str]:
    return {
        word
        for word in re.split(r"[^a-z0-9]+", f"{row.narration} {row.payee}".lower())
        if len(word) >= 3 and word not in GENERIC
    }


def _stretch(
    matched: _Matched, since: date, day: date
) -> tuple[tuple[Row, ...], tuple[Row, ...], tuple[Apart, ...]]:
    """The rows that make a difference in what moved after `since` through `day`: those one
    book holds alone, and those dated inside the stretch by one book and outside it by the
    other."""

    def within(row: Row) -> bool:
        return since < row.day <= day

    return (
        tuple(r for r in matched.only_ours if within(r)),
        tuple(r for r in matched.only_theirs if within(r)),
        tuple(p for p in matched.apart if within(p.ours) != within(p.theirs)),
    )


def describe(comparison: Comparison, ledger: str) -> Iterator[str]:
    """The comparison as lines for a terminal: every account, every checkpoint."""
    yield f"Hearth's own books against {ledger}"
    yield (
        "Balances are at the end of each day, in the ledger's sign; the ledger's rows are read"
        f" on their {POST_DATE} where they carry one."
    )
    verdicts: Counter[Verdict] = Counter()
    for account in comparison.accounts:
        yield ""
        yield account.name + (
            f"  ({account.unbalanced}: only what moved)" if account.unbalanced else ""
        )
        if not account.compared:
            yield "  no checkpoint both books reach"
        for step in account.compared:
            verdicts[step.verdict] += 1
            yield from _describe_step(step)
        for skipped in account.skipped:
            yield f"  {skipped.day}  not compared: {skipped.why}"
    for name in comparison.only_ours:
        yield ""
        yield f"{name}  not in the ledger: nothing to compare"
    yield ""
    yield (
        f"Checkpoints: {verdicts[Verdict.AGREES]} agree,"
        f" {verdicts[Verdict.DATED_APART]} agree but for the day rows are dated,"
        f" {verdicts[Verdict.DIFFERS]} differ."
    )


def _describe_step(step: Step) -> Iterator[str]:
    when = f"{step.day}" if step.since is None else f"{step.since + timedelta(1)}..{step.day}"
    parts = []
    if step.theirs is not None:
        parts.append(_figure("balance", step.ours, step.theirs))
    if step.moved_ours is not None and step.moved_theirs is not None:
        parts.append(_figure("moved", step.moved_ours, step.moved_theirs))
    yield f"  {when}  {step.verdict.value}  {'; '.join(parts)}"
    for row in step.only_ours:
        yield f"      only in hearth      {_row(row)}"
    for row in step.only_theirs:
        yield f"      only in the ledger  {_row(row)}"
    # The first checkpoint has no stretch before it; what is dated across it explains its
    # balance. After it, what is dated across a stretch's ends explains what moved.
    for pair in step.apart if step.since is not None else step.straddling:
        yield (
            f"      dated apart         {pair.ours.day} in hearth, {pair.theirs.day} in the"
            f" ledger  {pair.ours.amount:+,.2f}  {pair.ours.narration}"
        )


def _figure(what: str, ours: Decimal, theirs: Decimal) -> str:
    if ours == theirs:
        return f"{what} {ours:,.2f}" if what == "balance" else f"{what} {ours:+,.2f}"
    return f"{what} differs by {ours - theirs:+,.2f}: hearth {ours:,.2f}, the ledger {theirs:,.2f}"


def _row(row: Row) -> str:
    booked = f" (booked {row.booked})" if row.booked else ""
    return f"{row.day}{booked}  {row.amount:+,.2f}  {row.narration}"
