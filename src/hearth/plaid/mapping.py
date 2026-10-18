"""Where a connected account goes in the ledger: proposals, and the warning a swap needs.

A mapping is confirmed by its member, never inferred. The proposal here is only the first
option offered. What this module adds is the check that matters: two ledger accounts whose
names differ by a single word, such as ``Assets:Bank:Checking`` and
``Assets:Bank:Joint:Checking``, are exactly the pair a member can swap without anything
looking wrong, because both are real and open. Every such pair among the targets on the page
is named, so the member checks it against the bank's balances before confirming.
"""

import re
from collections.abc import Iterable, Sequence

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


def unused(name: str, taken: Iterable[str], mask: str | None) -> str:
    """`name`, or `name` with the account's last digits when another account has it already."""
    taken = set(taken)
    if name not in taken:
        return name
    base = f"{name}{component(mask)}" if mask else name
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}{n}", n + 1
    return candidate
