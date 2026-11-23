"""Which merchant a row is from: the one rule that says two rows are "the same merchant".

A bank names one merchant many ways. Plaid sends a cleaned-up `merchant_name` for most rows
("Blue Bottle Coffee"), and for the rest only the bank's own description, which carries what
the bank and the card network added: how the money moved ("PURCHASE AUTHORIZED ON 09/12",
"ACH DEBIT 0012", "POS DEBIT 1234"), a payment processor's prefix ("SQ *BLUE BOTTLE"), a store
number and the town ("STARBUCKS #1234 SEATTLE WA"), a reference after an asterisk ("AMZN Mktp
US*2K4AB1"), in any case. An imported ledger names it as its importer did, as the entry's payee
or, without one, its narration.

`merchant` reads every one of these the same way:

1. A name, Plaid's `merchant_name` or a ledger entry's payee, is used where there is one; only
   without one is a description read: Plaid's `name`, then its `original_description`, or a
   ledger entry's narration.
2. Letters of every script are kept, accents and all, in Unicode's compatible form, and case
   is ignored: "CAFÉ" is "café", "寿司 BAR" is "寿司 bar".
3. A payment processor's prefix, one of `PROCESSORS` before an asterisk, is dropped: it took
   the payment and sold nothing.
4. Everything from the first remaining asterisk on is dropped: the network's reference.
5. In a description only, the bank's own words for how the money moved are dropped from the
   front (`LEAD_INS`), with the dates and numbers among and after them: "PURCHASE AUTHORIZED
   ON 09/12 STARBUCKS" is "starbucks", "ACH DEBIT 0012 GEICO" is "geico".
6. Everything from the first word that holds a digit or starts with "#" is dropped, with
   "store", "str", "no" or "location" just before it: a store number, and the town and state
   that follow it. Never while the words kept so far are only `GENERIC` ones, so "The 1849
   Club" and "The 9 Muses" stay two merchants and "7-Eleven" stays itself.
7. Punctuation is a space, except "&" and "'" within a word.

What is left is the merchant, unless every word of it is `GENERIC`, such as "check" or
"payment", or a number, as in "CHECK 1001": that is no merchant, `None`, which is never the
same merchant as anything. Two rows are the same merchant exactly when this gives both the
same words.
"""

import re
import unicodedata

PROCESSORS = frozenset(
    {
        "apl",
        "bt",
        "ckc",
        "dd",
        "goog",
        "google",
        "in",
        "iz",
        "paypal",
        "pp",
        "py",
        "sp",
        "sq",
        "sumup",
        "tst",
        "wpy",
        "zettle",
    }
)
"""Payment processors whose prefix, before an asterisk, card networks put ahead of the
merchant's own name: Square (``SQ *``), Toast (``TST*``), PayPal, Shopify (``SP *``), Intuit
(``IN *``), DoorDash's marketplace (``DD *``), SumUp, Zettle, WePay and the like. Generic
payment services, never a merchant, bank or person."""

LEAD_INS = frozenset(
    {
        "ach",
        "authorized",
        "bill",
        "card",
        "checkcard",
        "credit",
        "crd",
        "dbt",
        "dda",
        "debit",
        "electronic",
        "mastercard",
        "on",
        "online",
        "payment",
        "pmt",
        "pos",
        "preauthorized",
        "pur",
        "purchase",
        "recurring",
        "sale",
        "transaction",
        "txn",
        "visa",
        "web",
        "withdrawal",
    }
)
"""The words a bank's description starts with to say how money moved, which name no
merchant: "PURCHASE AUTHORIZED ON", "ACH DEBIT", "POS DEBIT", "VISA DDA PUR"."""

GENERIC = frozenset(
    {
        "&",
        "a",
        "an",
        "and",
        "at",
        "check",
        "co",
        "deposit",
        "fee",
        "for",
        "in",
        "inc",
        "of",
        "on",
        "the",
        "to",
        "transfer",
        "xfer",
        *LEAD_INS,
    }
)
"""Words that say nothing of which merchant it is on their own: a key of only these is none."""

_PREFIX = re.compile(r"^\s*([^\W\d_]+)\s*\*\s*")
_STORE = frozenset({"store", "str", "no", "location", "loc"})
_WORD = re.compile(r"[^\W_]+(?:['&][^\W_]+)*|&")
_NUMBERISH = re.compile(r"[#\d][\d/.\-#]*")


def merchant(name: str | None, *described: str | None) -> str | None:
    """The merchant a row is from, by the module's rule: from `name`, Plaid's merchant name
    or a ledger entry's payee, where it has any words, else from the first of `described`,
    the bank's own descriptions, that has any. None for none, or only generic words."""
    if name and name.strip():
        return _key(name, described=False)
    text = next((t for t in described if t and t.strip()), None)
    return _key(text, described=True) if text is not None else None


def _key(text: str, *, described: bool) -> str | None:
    text = unicodedata.normalize("NFKC", text).casefold()
    prefixed = _PREFIX.match(text)
    if prefixed is not None and prefixed.group(1) in PROCESSORS:
        text = text[prefixed.end() :]
    tokens = text.split("*", 1)[0].split()
    if described:
        tokens = _without_lead_in(tokens)
    kept: list[str] = []
    for token in tokens:
        numbered = token.startswith("#") or any(c.isdigit() for c in token)
        if numbered and any(w not in GENERIC for t in kept for w in _WORD.findall(t)):
            break
        kept.append(token)
    while len(kept) > 1 and kept[-1].strip(".#:") in _STORE:
        kept.pop()
    words = [w for t in kept for w in _WORD.findall(t)]
    if all(w in GENERIC or w.isdigit() for w in words):
        return None
    return " ".join(words)


def _without_lead_in(tokens: list[str]) -> list[str]:
    """`tokens` without the bank's words for how money moved at their front, nor the dates and
    numbers among and just after them."""
    start = 0
    while start < len(tokens) and tokens[start].strip(".:-") in LEAD_INS:
        start += 1
        while start < len(tokens) and _NUMBERISH.fullmatch(tokens[start]):
            start += 1
    return tokens[start:]
