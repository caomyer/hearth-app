"""How a ledger account's name reads to someone who has never seen a ledger."""

import re

_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def readable(component: str) -> str:
    """One part of a ledger account's name as words: "GasAndElectricity" reads as "Gas and
    electricity", "Groceries" as itself, and an abbreviation such as "ATM" keeps its capitals.

    Hearth files a category Plaid names under a name joined up the way beancount requires, so
    without this a member would read the joined-up word.
    """
    words = _WORDS.findall(component)
    if not words or "".join(words) != component.replace("-", ""):
        return component
    first, *rest = words
    return " ".join([first, *(w if w.isupper() and len(w) > 1 else w.lower() for w in rest)])
