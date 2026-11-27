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
4. After the first remaining asterisk, a first word that reads as a word (`reads_as_word`)
   is the same company's service and is kept ("UBER *EATS" is "uber eats", apart from "uber
   trip"); anything else there, digits or letters no word is spelt with ("US*2K4AB1",
   "US*RTQPL"), is the network's reference for one payment, and is dropped.
5. In a description only, the words the bank starts with to say how the money moved are
   dropped from the front, with the date or number after them: a run of `LEAD_IN_WORDS`
   alone, in any order, followed by a date or number. "PURCHASE AUTHORIZED ON 09/12
   STARBUCKS" is "starbucks", "CARD PURCHASE WITH PIN 09/12 SAFEWAY" is "safeway", "BILL
   PAYMENT 0912 COMCAST" is "comcast". A run with a word of a name in it is no lead-in, so
   "ELECTRONIC ARTS 123", "CREDIT ONE BANK" and "ON THE BORDER" stay whole; and a lead-in with
   nothing after its date or number is no merchant at all.
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

LEAD_IN_WORDS = frozenset(
    {
        "ach",
        "auth",
        "authorized",
        "bill",
        "card",
        "ccd",
        "check",
        "checkcard",
        "contactless",
        "crd",
        "credit",
        "dbt",
        "dda",
        "debit",
        "electronic",
        "international",
        "intl",
        "on",
        "online",
        "payment",
        "pin",
        "pmt",
        "pos",
        "ppd",
        "preauth",
        "preauthorized",
        "pur",
        "purchase",
        "pymt",
        "recurring",
        "visa",
        "web",
        "with",
        "withdrawal",
    }
)
"""The words a bank's description starts with to say how money moved, which name no merchant:
"PURCHASE AUTHORIZED ON 09/12", "ACH DEBIT 0012", "CARD PURCHASE WITH PIN 09/12", "VISA DDA
PUR 12345". Any run of them alone, in any order, followed by a date or number, is a lead-in,
so a phrase no bank was seen to use yet is one too; a run with any other word in it is not,
so a merchant whose name merely starts with one ("Electronic Arts", "Credit One Bank", "On
the Border", "Card Services") is never cut. A new word needs adding here; a new phrase of
these does not. Words after which what follows is not a merchant are not among them: cash
from any machine is one merchant, "ATM WITHDRAWAL 0912 MAIN ST" is "atm withdrawal"."""

_JOINING = frozenset({"on", "with"})
"""Lead-in words that join others and never start one alone: "ON 9 ..." is no lead-in."""

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
_VOWEL = re.compile(r"[aeiouy]")
_UNSPOKEN = re.compile(r"[^aeiouy]{4}|q(?!u)|[jkqvwxz]{2}")
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


def reads_as_word(word: str) -> bool:
    """Whether `word`, the first after an asterisk, is spelt as a word is, so a company's
    service ("TRIP", "EATS", "PRIME", "STREAMING") rather than a network's reference for one
    payment, which is random letters and digits ("2K4AB1", "RTQPL", "TQXWZ").

    A word of letters alone reads as one when it has a vowel (a, e, i, o, u or y), never four
    consonants running, no "q" but before "u", and no two of "j", "k", "q", "v", "w", "x" and
    "z" side by side. About 97 English words in 100 read so, and only about 4 references of
    letters alone in 10 at five letters, 2 in 10 at eight: one that does is its own merchant
    and learns nothing, which is the safe side to err on. A word in a script other than Latin
    reads as one: a network writes its references in Latin."""
    if not _WORD.fullmatch(word) or any(c.isdigit() for c in word):
        return False
    if not word.isascii():
        return True
    return _VOWEL.search(word) is not None and _UNSPOKEN.search(word) is None


def _before_reference(text: str) -> list[str]:
    """`text`'s words up to the network's reference after an asterisk. What follows the
    asterisk is the same company's service when its first word reads as a word, as in "UBER
    *EATS" and "UBER *TRIP", two services a member books apart, and is kept; otherwise it is
    a reference for one payment ("AMZN Mktp US*2K4AB1", "AMAZON PRIME*TQXWZ"), and is not."""
    before, star, after = text.partition("*")
    words = before.split()
    service = after.split()[:1] if star else []
    if service and reads_as_word(service[0]):
        words += service
    return words


def _without_lead_in(tokens: list[str]) -> list[str]:
    """`tokens` without the lead-in at their front: a run of `LEAD_IN_WORDS` alone, not one
    of `_JOINING` alone, followed by a date or number, with every date or number after it.
    Each lead-in after that one goes too, as in "PURCHASE 09/12 CARD 1234 SAFEWAY"."""
    start = 0
    while True:
        end = start
        while end < len(tokens) and tokens[end].strip(".:-") in LEAD_IN_WORDS:
            end += 1
        run = {t.strip(".:-") for t in tokens[start:end]}
        if not run - _JOINING or end == len(tokens) or not _NUMBERISH.fullmatch(tokens[end]):
            return tokens[start:]
        start = end
        while start < len(tokens) and _NUMBERISH.fullmatch(tokens[start]):
            start += 1
