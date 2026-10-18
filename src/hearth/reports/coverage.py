"""How much of each account's history the ledger actually holds, and where it came from.

A ledger assembled from statement downloads for one stretch and a bank feed for another can look
seamless while one of them is missing weeks. Coverage reports, per account, the first and last
transaction, each origin's own span, and every quiet stretch long enough to be a gap, so the
dashboards can show how complete the picture is instead of implying it is.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

from beancount.core.data import Directive, Open, Transaction

from hearth.reports.common import is_balance_sheet

GAP_DAYS = 31
"""A month with no transaction at all is unusual for an account in use; report it."""

UNLABELLED = "unlabelled"

type Origin = Callable[[Transaction], str]


def origin_from_source_meta(entry: Transaction) -> str:
    """The importer's `source` metadata up to its first colon, or "unlabelled".

    bcng's import writes `source: "<staged file>:<row>"` on every entry, so entries from one
    download share an origin.
    """
    source = entry.meta.get("source") if entry.meta else None
    if not isinstance(source, str) or not source:
        return UNLABELLED
    return source.split(":", 1)[0]


@dataclass(frozen=True, slots=True)
class Span:
    origin: str
    earliest: date
    latest: date
    transactions: int


@dataclass(frozen=True, slots=True)
class Gap:
    """Consecutive transaction dates on an account further apart than the gap threshold."""

    after: date
    before: date

    @property
    def days(self) -> int:
        return (self.before - self.after).days


@dataclass(frozen=True, slots=True)
class AccountCoverage:
    account: str
    opened: date
    earliest: date | None
    latest: date | None
    spans: tuple[Span, ...]
    gaps: tuple[Gap, ...]


def account_coverage(
    entries: Iterable[Directive],
    *,
    as_of: date,
    origin: Origin = origin_from_source_meta,
    gap_days: int = GAP_DAYS,
) -> list[AccountCoverage]:
    """Coverage of every Assets and Liabilities account opened by `as_of`, from entries to then.

    `gaps` lists every stretch of more than `gap_days` between consecutive transaction dates.
    An account with no transactions has no earliest or latest date, which is itself the answer.
    """
    opened: dict[str, date] = {}
    days: defaultdict[str, set[date]] = defaultdict(set)
    spans: defaultdict[tuple[str, str], list[date]] = defaultdict(list)
    for entry in entries:
        if isinstance(entry, Open) and entry.date <= as_of:
            opened[entry.account] = entry.date
        elif isinstance(entry, Transaction) and entry.date <= as_of:
            source = origin(entry)
            for account in {p.account for p in entry.postings}:
                days[account].add(entry.date)
                spans[account, source].append(entry.date)
    coverage = []
    for account in sorted(opened):
        if not is_balance_sheet(account):
            continue
        dates = sorted(days[account])
        coverage.append(
            AccountCoverage(
                account=account,
                opened=opened[account],
                earliest=dates[0] if dates else None,
                latest=dates[-1] if dates else None,
                spans=tuple(
                    sorted(
                        (
                            Span(source, min(seen), max(seen), len(seen))
                            for (name, source), seen in spans.items()
                            if name == account
                        ),
                        key=lambda span: (span.earliest, span.origin),
                    )
                ),
                gaps=tuple(
                    Gap(after, before)
                    for after, before in pairwise(dates)
                    if (before - after).days > gap_days
                ),
            )
        )
    return coverage
