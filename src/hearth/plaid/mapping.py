"""Where a connected account goes in the ledger: proposals, and the warning a swap needs.

A mapping is confirmed by its member, never inferred. The proposal here is only the first
option offered, and the member may name a new ledger account themselves. What this module adds
is the check that matters: two ledger accounts whose names differ by a single word, such as
``Assets:Bank:Checking`` and ``Assets:Bank:Joint:Checking``, are exactly the pair a member can
swap without anything looking wrong, because both are real and open. Every such pair among the
targets on the page is named, so the member checks it against the bank's balances before
confirming.

A name the member types is only ever a new account. One the books already have, or one inside
or above it, is refused whoever owns it: the ledger's names are one namespace, and an account
reached by typing its name would be an account read without being its owner.
"""

import re
from collections.abc import Collection, Iterable, Sequence
from enum import StrEnum

MAX_LENGTH = 200
"""The longest ledger account name a member may type: far past any real one."""

_COMPONENT = re.compile(r"[A-Z0-9][A-Za-z0-9-]*")
"""beancount's rule for each name after the root, in ASCII so no two names look alike."""
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_KINDS = {
    "checking": "Checking",
    "savings": "Savings",
    "credit card": "Card",
    "money market": "MoneyMarket",
    "cd": "Deposit",
    "brokerage": "Brokerage",
    "ira": "Ira",
    "401k": "Retirement",
    "mortgage": "Mortgage",
    "student": "StudentLoan",
}


def words(ledger_account: str) -> tuple[str, ...]:
    """A ledger account's words, lower-cased: "Assets:JointChecking" is assets, joint, checking."""
    return tuple(w.lower() for part in ledger_account.split(":") for w in _CAMEL.findall(part))


def differ_by_one_word(a: str, b: str) -> bool:
    """Whether one word changed, added or dropped turns `a` into `b`."""
    x, y = words(a), words(b)
    if x == y:
        return False
    if len(x) == len(y):
        return sum(1 for p, q in zip(x, y, strict=True) if p != q) == 1
    if abs(len(x) - len(y)) != 1:
        return False
    longer, shorter = (x, y) if len(x) > len(y) else (y, x)
    return any(longer[:i] + longer[i + 1 :] == shorter for i in range(len(longer)))


def near_pairs(targets: Sequence[str], others: Iterable[str] = ()) -> list[tuple[str, str]]:
    """Every pair of distinct names one word apart, where at least one is a chosen target."""
    chosen = list(dict.fromkeys(targets))
    pool = list(dict.fromkeys([*chosen, *others]))
    pairs = []
    for i, a in enumerate(pool):
        for b in pool[i + 1 :]:
            if (a in chosen or b in chosen) and differ_by_one_word(a, b):
                pairs.append((a, b))
    return pairs


def component(text: str) -> str:
    """A ledger name component: letters and digits only, starting with a capital."""
    joined = "".join(w[:1].upper() + w[1:] for w in re.findall(r"[A-Za-z0-9]+", text))
    return joined or "Account"


def proposed(*, credit: bool, member: str, institution: str, name: str, subtype: str | None) -> str:
    """ "Assets:Jordan:Lakeside:Checking", "Liabilities:Jordan:Pinecone:Card"."""
    root = "Liabilities" if credit else "Assets"
    kind = _KINDS.get((subtype or "").lower()) or component(name)
    bank = component(institution.split()[0]) if institution.split() else "Bank"
    return f"{root}:{component(member)}:{bank}:{kind}"


def proposed_named(*, root: str, member: str, institution: str, name: str) -> str:
    """ "Assets:Jordan:Bramblewood:Savings" for savings at Bramblewood Credit Union called
    "Bramblewood savings"; "Liabilities:Jordan:CarLoan" for a car loan from no institution."""
    where: list[str] = re.findall(r"[A-Za-z0-9]+", institution)
    first = component(where[0]) if where else ""
    known = {w.casefold() for w in where}
    rest = [w for w in re.findall(r"[A-Za-z0-9]+", name) if w.casefold() not in known]
    kind = component(" ".join(rest)) if rest else component(name or "Account")
    return ":".join(part for part in (root, component(member), first, kind) if part)


def unused(name: str, taken: Iterable[str], mask: str | None) -> str:
    """`name`, or the nearest name to it that `check_new` would take beside `taken`: beside
    any taken account it would sit inside, then with the account's last digits when another
    account has it, or one inside it, already."""
    taken = set(taken)
    parts = name.split(":")
    while inside := [i for i in range(2, len(parts)) if ":".join(parts[:i]) in taken]:
        parts = [*parts[: inside[0] - 1], "".join(parts[inside[0] - 1 :])]
    name = ":".join(parts)
    if not in_use(name, taken):
        return name
    base = f"{name}{component(mask)}" if mask else name
    candidate, n = base, 2
    while in_use(candidate, taken):
        candidate, n = f"{base}{n}", n + 1
    return candidate


class Problem(StrEnum):
    """Why a confirmed row of the mapping form cannot be saved as it is. Each is a fixed
    message; none carries anything about another member's accounts."""

    MISSING = "missing"
    MALFORMED = "malformed"
    UNDER_ASSETS = "under-assets"
    UNDER_LIABILITIES = "under-liabilities"
    IN_USE = "in-use"
    """The books have this name, or one inside or above it. One answer for every shape and
    every owner, so it tells neither whose the name is nor where it sits."""
    TWICE = "twice"
    """Another confirmed row on the page goes to the same ledger account, or names a new one
    inside or above the new one this row names."""
    NAME_TOO_LONG = "name-too-long"
    NAME_NOT_NEW = "name-not-new"
    """A name was typed for an account the books already have, which keeps the one it has."""


def root(*, credit: bool) -> str:
    """Where an account's kind puts it: what is owed under Liabilities, the rest under Assets."""
    return "Liabilities" if credit else "Assets"


def check_new(path: str, *, credit: bool, existing: Collection[str]) -> Problem | None:
    """What is wrong with `path` as a new ledger account, if anything.

    `existing` is every ledger account the books know, whoever owns it. The answer is the same
    for all of them, so it says only that a name is in use, never whose it is.
    """
    if not path:
        return Problem.MISSING
    first, _, rest = path.partition(":")
    if first != root(credit=credit):
        return Problem.UNDER_LIABILITIES if credit else Problem.UNDER_ASSETS
    if len(path) > MAX_LENGTH or not all(_COMPONENT.fullmatch(c) for c in rest.split(":")):
        return Problem.MALFORMED
    if in_use(path, existing):
        return Problem.IN_USE
    return None


def in_use(path: str, existing: Iterable[str]) -> bool:
    """Whether the books have `path`, or one inside or above it."""
    return any(path == other or _nested(path, other) for other in existing)


def _nested(a: str, b: str) -> bool:
    """Whether one account sits inside the other, where a parent's balance would include it."""
    return a.startswith(f"{b}:") or b.startswith(f"{a}:")
