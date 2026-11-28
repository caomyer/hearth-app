"""What the books file must be, for the tests: the books hearth computed, as beancount itself
reads them back, and nothing it had to work out again."""

from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

from beancount import loader
from beancount.core.data import Directive, Transaction
from beancount.core.inventory import Inventory

from hearth.books import Books


def read_back(path: Path) -> list[Directive]:
    """The file as beancount loads it, uncached, so nothing is written beside it, refusing one
    beancount reports any error in."""
    entries, errors, _ = loader._load([(str(path), True)], None, None, None)
    assert [error.message for error in errors] == []
    return list(entries)


def balances(entries: Iterable[Directive]) -> dict[str, str]:
    """What every account holds after every entry, lots and their cost included."""
    held: defaultdict[str, Inventory] = defaultdict(Inventory)
    for entry in entries:
        if isinstance(entry, Transaction):
            for posting in entry.postings:
                held[posting.account].add_position(posting)
    return {account: str(inventory) for account, inventory in sorted(held.items())}


def kinds(entries: Iterable[Directive]) -> Counter[str]:
    return Counter(type(entry).__name__ for entry in entries)


def holds_the_books(path: Path, found: Books) -> list[Directive]:
    """Hold the file at `path` to the books `found`: it loads with no error, every account
    holds exactly what the books say, and it has the same entries of each kind, so no
    plugin made or dropped any as it was read. Returns what it read."""
    entries = read_back(path)
    assert balances(entries) == balances(found.entries)
    assert kinds(entries) == kinds(found.entries)
    return entries
