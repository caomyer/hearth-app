"""Which merchant a row is from: the one rule that says two rows are "the same merchant".

A bank names one merchant many ways. Plaid sends a cleaned-up `merchant_name` for most rows
("Blue Bottle Coffee"), and for the rest only the bank's own description, which carries what
the card network added: a payment processor's prefix ("SQ *BLUE BOTTLE"), a store number and
the town ("STARBUCKS #1234 SEATTLE WA"), a reference after an asterisk ("AMZN Mktp
US*2K4AB1"), in any case. An imported ledger names it as its importer did, as the entry's payee
or, without one, its narration.

`merchant` reads every one of these the same way:

1. The words are the merchant name where there is one, else the description: Plaid's
   `merchant_name`, else its `name`, else its `original_description`; a ledger entry's payee,
   else its narration.
2. Case is ignored.
3. A payment processor's prefix, one of `PROCESSORS` followed by an asterisk, is dropped: the
   processor took the payment, it did not sell anything.
4. Everything from the first remaining asterisk on is dropped: what follows it is the network's
   reference for that one payment.
5. Everything from the first word after the first that holds a digit or starts with "#" is
   dropped, with "store", "str", "no" or "location" just before it: a store number, and the
   town and state that usually follow it. The first word is always kept, so "7-Eleven" and
   "24 Hour Fitness" stay themselves.
6. Punctuation is a space, except "&" and "'" within a word, and runs of spaces are one.

What is left is the merchant. Nothing left, as for a row with no words at all, is no merchant:
`None`, which is never the same merchant as anything, not even another row with none.
"""

import re

PROCESSORS = frozenset(
    {
        "bt",
        "ckc",
        "goog",
        "google",
        "iz",
        "paypal",
        "pp",
        "py",
        "sp",
        "sq",
        "sumup",
        "tst",
        "zettle",
    }
)
"""Payment processors whose prefix, before an asterisk, card networks put ahead of the
merchant's own name: Square (``SQ *``), Toast (``TST*``), PayPal, Shopify (``SP *``), SumUp,
Zettle and the like. Generic payment services, never a merchant, bank or person."""

_PREFIX = re.compile(r"^\s*([a-z]+)\s*\*\s*")
_STORE = frozenset({"store", "str", "no", "location", "loc"})
_WORD = re.compile(r"[a-z0-9]+(?:['&][a-z0-9]+)*|&")


def merchant(*texts: str | None) -> str | None:
    """The merchant the first of `texts` with any words in it names, by the module's rule, or
    None when none has any."""
    words = next((t for t in texts if t and t.strip()), None)
    if words is None:
        return None
    text = words.casefold()
    prefixed = _PREFIX.match(text)
    if prefixed is not None and prefixed.group(1) in PROCESSORS:
        text = text[prefixed.end() :]
    text = text.split("*", 1)[0]
    kept: list[str] = []
    for token in text.split():
        if kept and (token.startswith("#") or any(c.isdigit() for c in token)):
            break
        kept.append(token)
    while len(kept) > 1 and kept[-1].strip(".#:") in _STORE:
        kept.pop()
    found = " ".join(w for t in kept for w in _WORD.findall(t))
    return found or None
