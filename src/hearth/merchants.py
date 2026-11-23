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
4. After the first remaining asterisk, a first word of letters alone is the same company's
   service and is kept ("UBER *EATS" is "uber eats", apart from "uber trip"); anything else
   there is the network's reference for one payment, and is dropped.
5. In a description only, a phrase the bank starts with to say how the money moved is
   dropped from the front (`LEAD_INS`), only as the whole phrase followed by its date or
   number, with that date or number: "PURCHASE AUTHORIZED ON 09/12 STARBUCKS" is "starbucks",
   "ACH DEBIT 0012 GEICO" is "geico", while "ELECTRONIC ARTS" and "ON THE BORDER" stay whole.
6. Everything from the first word that holds a digit or starts with "#" is dropped, with
   "store", "str", "no" or "location" just before it: a store number, and the town and state
   that follow it. Never while the words kept so far are only `GENERIC` words and `NOUNS`, so
   "The 1849 Club" and "The 9 Muses" stay two merchants, "BAR 22 NYC" is not every bar, and
   "7-Eleven" stays itself.
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

LEAD_INS: tuple[tuple[str, ...], ...] = (
    ("recurring", "payment", "authorized", "on"),
    ("purchase", "authorized", "on"),
    ("payment", "authorized", "on"),
    ("debit", "card", "purchase"),
    ("visa", "dda", "pur"),
    ("pos", "purchase"),
    ("pos", "debit"),
    ("pos", "withdrawal"),
    ("ach", "debit"),
    ("ach", "credit"),
    ("ach", "pmt"),
    ("dbt", "crd"),
    ("checkcard",),
    ("pos",),
)
"""The phrases a bank's description starts with to say how money moved, which name no
merchant: "PURCHASE AUTHORIZED ON 09/12", "ACH DEBIT 0012", "POS DEBIT 1234", "VISA DDA PUR
12345". Each is a lead-in only as the whole phrase, followed by its date or number, so a
merchant whose name merely starts with one of its words ("Electronic Arts", "Credit One
Bank", "On the Border", "Card Services") is never cut."""

GENERIC = frozenset(
    {
        "&",
        "a",
        "ach",
        "an",
        "and",
        "at",
        "check",
        "credit",
        "debit",
        "deposit",
        "fee",
        "for",
        "of",
        "on",
        "online",
        "payment",
        "pmt",
        "pos",
        "purchase",
        "recurring",
        "the",
        "to",
        "transfer",
        "withdrawal",
        "xfer",
    }
)
"""Words that name no merchant: a key of only these, or of these and numbers, is none."""

NOUNS = frozenset(
    {
        "bar",
        "bakery",
        "cafe",
        "café",
        "club",
        "deli",
        "diner",
        "gas",
        "grill",
        "hotel",
        "inn",
        "kitchen",
        "market",
        "pharmacy",
        "pizza",
        "pub",
        "restaurant",
        "salon",
        "shop",
        "station",
        "store",
    }
)
"""Words that say what kind of place a merchant is, not which: a name is never cut down to
one of these, nor to these and `GENERIC` words, so "BAR 22 NYC" stays "bar 22 nyc" rather
than becoming every bar's "bar"."""

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
    tokens = _before_reference(text)
    if described:
        tokens = _without_lead_in(tokens)
    kept: list[str] = []
    for token in tokens:
        numbered = token.startswith("#") or any(c.isdigit() for c in token)
        if numbered and any(w not in GENERIC | NOUNS for t in kept for w in _WORD.findall(t)):
            break
        kept.append(token)
    while len(kept) > 1 and kept[-1].strip(".#:") in _STORE:
        kept.pop()
    words = [w for t in kept for w in _WORD.findall(t)]
    if all(w in GENERIC or w.isdigit() for w in words):
        return None
    return " ".join(words)


def _before_reference(text: str) -> list[str]:
    """`text`'s words up to the network's reference after an asterisk. What follows the
    asterisk is the same company's service when its first word is letters alone, as in "UBER
    *EATS" and "UBER *TRIP", two services a member books apart, and is kept; a word with a
    digit in it is a reference for one payment ("AMZN Mktp US*2K4AB1"), and is not."""
    before, star, after = text.partition("*")
    words = before.split()
    service = after.split()[:1] if star else []
    if service and not any(c.isdigit() for c in service[0]) and _WORD.fullmatch(service[0]):
        words += service
    return words


def _without_lead_in(tokens: list[str]) -> list[str]:
    """`tokens` without a lead-in phrase at their front (`LEAD_INS`), where the whole phrase
    is followed by its date or number, nor that date or number and any after it."""
    start = 0
    while True:
        phrase = next(
            (
                p
                for p in LEAD_INS
                if tuple(t.strip(".:-") for t in tokens[start : start + len(p)]) == p
                and start + len(p) < len(tokens)
                and _NUMBERISH.fullmatch(tokens[start + len(p)])
            ),
            None,
        )
        if phrase is None:
            return tokens[start:]
        start += len(phrase)
        while start < len(tokens) and _NUMBERISH.fullmatch(tokens[start]):
            start += 1
