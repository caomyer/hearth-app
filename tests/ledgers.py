"""An invented ledger kept before hearth, of the shapes a real one has, for importing.

Every name and figure is invented. The ledger is opened from ``main.beancount``, which sets
options, runs one of beancount's own plugins and names one that is not, and includes a file of
accounts, which includes a file of prices in turn, and every half-year in ``years/`` by a glob.
It has a `pad`, lots held at cost and priced, `custom` directives, a plugin line in an included
file, a document, and one file with Windows line endings and no newline at its end, so a copy
that changed a byte would show. Its figures are worked out by hand below, never by hearth.

Below it, a household that imported a ledger and then connected its banks, for matching each
connected account to the base's account it is: its banks, and the base written from what
they sent (`matched_base`).
"""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from hearth.connections import plaid_source
from hearth.plaid.client import Transaction
from hearth.plaid.fake import FakeAccount, FakeInstitution, FakeTransaction

MAIN = """\
option "title" "Zq Household"
option "operating_currency" "USD"
option "insert_pythonpath" "TRUE"
plugin "beancount.plugins.implicit_prices"
plugin "zq_meddler" "loudly"
include "accounts.beancount"
include "years/*.beancount"

2026-01-01 custom "fava-option" "language" "en"
"""

ACCOUNTS = """\
plugin "beancount.plugins.auto_accounts"
2026-01-01 open Assets:Zq:Checking USD
2026-01-01 open Assets:Zq:Brokerage
2026-01-01 open Liabilities:Zq:Card USD
2026-01-01 open Equity:Zq:Opening USD
2026-01-01 open Expenses:Zq:Food USD
2026-01-01 open Income:Zq:Wages USD
2026-01-01 commodity ZQX
include "prices/zqx.beancount"
"""

PRICES = """\
2026-06-30 price ZQX 110.00 USD
"""

FIRST_HALF = """\
2026-01-01 pad Assets:Zq:Checking Equity:Zq:Opening
2026-01-02 balance Assets:Zq:Checking  1000.00 USD

2026-02-01 * "Zq Grocer" "Groceries"
  Expenses:Zq:Food  45.50 USD
  Liabilities:Zq:Card

2026-03-01 * "Zq Employer" "Wages"
  Assets:Zq:Checking  2000.00 USD
  Income:Zq:Wages

2026-03-05 * "Zq Broker" "Buy"
  Assets:Zq:Brokerage  10 ZQX {100.00 USD}
  Assets:Zq:Checking  -1000.00 USD

2026-04-01 balance Assets:Zq:Checking  2000.00 USD
2026-04-01 custom "budget" Expenses:Zq:Food "month" 300.00 USD
2026-04-02 document Assets:Zq:Checking "statements/2026-03.pdf"
"""

SECOND_HALF = (
    '2026-07-01 * "Zq Card Co" "Payment"\r\n'
    "  Liabilities:Zq:Card  45.50 USD\r\n"
    "  Assets:Zq:Checking\r\n"
    "\r\n"
    "2026-07-02 balance Liabilities:Zq:Card  0.00 USD\r\n"
    "2026-07-02 balance Assets:Zq:Checking  1954.50 USD"
)

MEDDLER = '''\
"""A plugin that is not beancount's own: were it ever run, it would say so on disk."""

from pathlib import Path

__plugins__ = ("meddle",)


def meddle(entries, options_map, config=None):
    Path(options_map["filename"]).with_name("meddled").write_text("ran")
    return entries, []
'''

FILES = {
    "main.beancount": MAIN,
    "accounts.beancount": ACCOUNTS,
    "prices/zqx.beancount": PRICES,
    "years/2026-1.beancount": FIRST_HALF,
    "years/2026-2.beancount": SECOND_HALF,
}
"""Every file the ledger reads, by its path from the folder it is opened in."""

# By hand. Entries hearth uses, custom directives left out: six opens, a commodity, the price,
# the price the implicit_prices plugin makes of the lot, the pad and the transaction it makes,
# four transactions, four balance assertions and the document.
ENTRIES = 6 + 1 + 1 + 1 + 1 + 1 + 4 + 4 + 1
ACCOUNT_COUNT = 6
FIRST, LAST = date(2026, 1, 1), date(2026, 7, 2)
ASSERTIONS = 4
# Checking ends at 1,954.50; the card at nothing; ten ZQX at the ledger's newest price, 110.00.
NET_WORTH = Decimal("1954.50") + 10 * Decimal("110.00")
IMPLICIT_PRICE = (date(2026, 3, 5), Decimal("100.00"))
UNUSED = [
    'main.beancount, line 5: plugin "zq_meddler", which is not beancount\'s own, so hearth'
    " never runs it",
    'main.beancount, line 9: custom "fava-option"',
    'accounts.beancount, line 1: plugin "beancount.plugins.auto_accounts", in an included'
    " file, where beancount itself does not read plugins",
    'years/2026-1.beancount, line 17: custom "budget"',
    "years/2026-1.beancount, line 18: document: hearth neither copies nor opens the file it names",
]


def write(folder: Path, files: dict[str, str] | None = None) -> Path:
    """The ledger's files in `folder`, as bytes exactly as above, and the meddling plugin
    beside them where the ledger's python path would find it; returns the file it is opened
    from."""
    for name, text in (files or FILES).items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())
    (folder / "zq_meddler.py").write_text(MEDDLER)
    return folder / "main.beancount"


# A household that imported its ledger and then connected its banks, for matching each
# connected account to the base's account it is (``hearth.matching``).
#
# Its banks send exactly the rows below, and the base is written from what they sent, as a
# ledger fed from hearth would be: each account's entries in the shape one of the ledgers a
# household keeps has, hearth's own `source`, an importer's bare `plaid-id`, or no id at all.
# Every institution, account and figure is invented, and every account's amounts are told
# apart from every other's, so content matches only where it is meant to.


def sent(
    name: str, first: date, count: int, every: int, amount: str, step: str = "1.00"
) -> tuple[FakeTransaction, ...]:
    """`count` rows from `first`, `every` days apart, the first for `amount` in Plaid's sign
    (money leaving the account) and each `step` more than the one before."""
    return tuple(
        FakeTransaction(
            name,
            Decimal(amount) + n * Decimal(step),
            first + timedelta(days=n * every),
            "GENERAL_MERCHANDISE_OTHER_GENERAL_MERCHANDISE",
        )
        for n in range(count)
    )


GROCERIES = sent("Zq Grocer", date(2026, 2, 3), 10, 14, "41.10")
"""Ten rows on the checking account, Feb 3 to Jun 9; the base lacks the fifth, Mar 31."""
LACKED = GROCERIES[4]
CARD_PAID = date(2026, 6, 10)
"""The day the checking account paid the card, which received it the day after."""
PAYMENT = FakeTransaction(
    "Payment to Zq card ending 0202",
    Decimal("300.00"),
    CARD_PAID,
    "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
)
RECEIVED = FakeTransaction(
    "Payment Thank You", Decimal("-300.00"), CARD_PAID + timedelta(days=1), "LOAN_PAYMENTS_OTHER"
)
SINCE = sent("Zq Grocer", date(2026, 8, 3), 3, 31, "51.00")
"""Three rows on the checking account after the base stops."""
CHARGES = sent("Zq Bookshop", date(2026, 2, 10), 6, 20, "18.25")
INSTALMENTS = sent("Zq Mortgage", date(2026, 3, 1), 4, 31, "-500.00", "0")
INTEREST = sent("Zq Savings Interest", date(2026, 9, 1), 3, 15, "-7.77", "-1.11")
BLAKES = sent("Transfer from Zq Bank", date(2026, 2, 20), 3, 30, "-250.00", "-10")
UNION = sent("Zq Hardware", date(2026, 2, 5), 8, 15, "1000.00", "3")
TWIN = sent("Zq Diner", date(2026, 3, 1), 6, 10, "20.00")
BLAKE_CHECKING = sent("Zq Florist", date(2026, 2, 12), 5, 21, "70.00")
JOINT = sent("Zq Utilities", date(2026, 2, 14), 5, 28, "33.00")
BLAKE_CARD = sent("Zq Bakery", date(2026, 2, 7), 6, 9, "12.00")

ZQ_BANK = FakeInstitution(
    "ins_zq_bank",
    "Zq Bank",
    (
        FakeAccount(
            "chk",
            "ZQ CHECKING",
            "0101",
            "depository",
            "checking",
            Decimal("4000"),
            transactions=(*GROCERIES, PAYMENT, *SINCE),
        ),
        FakeAccount(
            "card",
            "ZQ CARD",
            "0202",
            "credit",
            "credit card",
            Decimal("0"),
            transactions=(*CHARGES, RECEIVED),
        ),
        FakeAccount(
            "loan", "ZQ MORTGAGE", "0303", "loan", "mortgage", Decimal("150000"),
            transactions=INSTALMENTS,
        ),
        FakeAccount(
            "ira", "ZQ BROKERAGE", "0404", "investment", "brokerage", Decimal("1000"),
            transactions=(),
        ),
        FakeAccount(
            "sav", "ZQ SAVINGS", "0505", "depository", "savings", Decimal("900"),
            transactions=INTEREST,
        ),
        FakeAccount(
            "blakes", "ZQ SAVINGS FOR BLAKE", "1010", "depository", "savings", Decimal("760"),
            transactions=BLAKES,
        ),
    ),
)  # fmt: skip
"""Avery's bank: a checking account and a card paid from it, both kept by hearth's own
`source`; a mortgage an importer keeps by bare `plaid-id`; a brokerage, which Plaid sends no
transactions for; savings the base never kept; and Blake's savings, which Avery's login also
reaches."""

ZQ_UNION = FakeInstitution(
    "ins_zq_union",
    "Zq Credit Union",
    (
        FakeAccount(
            "chk", "SHARE DRAFT", "0606", "depository", "checking", Decimal("5000"),
            transactions=UNION,
        ),
    ),
)  # fmt: skip
"""Avery's credit union, connected, removed and connected again: Plaid sent the same rows
again under new ids, so the base holds the old ones."""

ZQ_TWIN = FakeInstitution(
    "ins_zq_twin",
    "Zq Twin Card",
    (
        FakeAccount(
            "card", "TWIN CARD", "0707", "credit", "credit card", Decimal("0"), transactions=TWIN
        ),
    ),
)
"""A card whose rows the base holds, with no id, on two of its accounts nearly alike."""

ZQ_BLAKE = FakeInstitution(
    "ins_zq_blake",
    "Zq Savings Bank",
    (
        FakeAccount(
            "chk", "CHECKING", "0808", "depository", "checking", Decimal("800"),
            transactions=BLAKE_CHECKING,
        ),
        FakeAccount(
            "joint", "JOINT", "0909", "depository", "checking", Decimal("300"),
            transactions=JOINT,
        ),
        FakeAccount(
            "card", "CARD", "1111", "credit", "credit card", Decimal("0"),
            transactions=BLAKE_CARD,
        ),
    ),
)  # fmt: skip
"""Blake's bank: checking an importer keeps by bare `plaid-id`, the joint account by hearth's
`source`, and a card the base kept from statements, on the day each charge was made, with no
id at all."""

MATCH_INSTITUTIONS = (ZQ_BANK, ZQ_UNION, ZQ_TWIN, ZQ_BLAKE)

OPENING = Decimal("5000.00")
"""What the checking account held on Feb 1, by the base's pad and balance assertion."""
PADDED_ON = date(2026, 1, 31)
LAST_ASSERTED = date(2026, 6, 12)

CLAIMS = {
    "Assets:Zq:Checking": ("avery",),
    "Liabilities:Zq:Card": ("avery",),
    "Assets:Zq:Union": ("avery",),
    "Liabilities:Zq:CardA": ("avery",),
    "Assets:Zq:Blake:Checking": ("blake",),
    "Assets:Zq:Blake:Savings": ("blake",),
    "Liabilities:Zq:Blake:Card": ("blake",),
    "Assets:Zq:Joint": ("avery", "blake"),
}
"""Who owns each of the base's accounts. Nobody has claimed the mortgage, the brokerage or
the second twin card yet."""

BASE_OPENS = (
    *CLAIMS,
    "Liabilities:Zq:Mortgage",
    "Liabilities:Zq:CardB",
    "Assets:Zq:Brokerage",
    "Assets:Zq:Elsewhere",
    "Equity:Zq:Opening",
    "Expenses:Zq:Spent",
)

OLD_UNION_ITEM = "item-zq-union-before"
"""The Item the credit union was connected through before, which the base's ids name."""


def _entry(
    day: date,
    account: str,
    units: Decimal,
    meta: dict[str, str],
    against: str = "Expenses:Zq:Spent",
) -> str:
    said = "".join(f'  {key}: "{value}"\n' for key, value in meta.items())
    return f'{day} * "Zq" "Invented"\n{said}  {account}  {units:.2f} USD\n  {against}\n\n'


def matched_base(
    rows: dict[str, list[Transaction]], items: dict[str, str], before: list[Transaction]
) -> dict[str, str]:
    """The base's files, written from what the banks sent: `rows` is each connected account's
    posted rows by ``"<institution id>/<account key>"``, `items` each institution's Item id,
    and `before` what the credit union sent before it was connected again."""

    def hearths(institution: str, row: Transaction) -> dict[str, str]:
        return {"source": plaid_source(items[institution], row.transaction_id)}

    def bare(row: Transaction) -> dict[str, str]:
        return {"plaid-id": row.transaction_id}

    text = "".join(f"2026-01-01 open {account} USD\n" for account in BASE_OPENS) + "\n"
    text += f"{PADDED_ON} pad Assets:Zq:Checking Equity:Zq:Opening\n"
    text += f"2026-02-01 balance Assets:Zq:Checking  {OPENING:.2f} USD\n\n"
    checking = rows["ins_zq_bank/chk"]
    kept = [r for r in checking if r.name == GROCERIES[0].name and r.day < CARD_PAID]
    kept = [r for r in kept if r.day != LACKED.day]
    for r in kept:
        text += _entry(r.day, "Assets:Zq:Checking", -r.amount, hearths("ins_zq_bank", r))
    payment = next(r for r in checking if r.name == PAYMENT.name)
    # A card payment, as one entry carrying only the paying account's id.
    text += _entry(
        CARD_PAID + timedelta(days=1),
        "Liabilities:Zq:Card",
        payment.amount,
        hearths("ins_zq_bank", payment),
        against="Assets:Zq:Checking",
    )
    held = OPENING - sum((r.amount for r in kept), Decimal(0)) - payment.amount
    text += f"{LAST_ASSERTED} balance Assets:Zq:Checking  {held:.2f} USD\n\n"
    for r in rows["ins_zq_bank/card"]:
        if r.amount > 0:
            text += _entry(r.day, "Liabilities:Zq:Card", -r.amount, hearths("ins_zq_bank", r))
    for r in rows["ins_zq_bank/loan"]:
        text += _entry(r.day, "Liabilities:Zq:Mortgage", -r.amount, bare(r), "Assets:Zq:Elsewhere")
    text += _entry(
        date(2026, 3, 5), "Assets:Zq:Brokerage", Decimal("1000.00"), {}, "Assets:Zq:Elsewhere"
    )
    for r in rows["ins_zq_bank/blakes"]:
        text += _entry(r.day, "Assets:Zq:Blake:Savings", -r.amount, hearths("ins_zq_bank", r))
    for n, r in enumerate(before):
        # The old connection's ids, and every other row two days from the day the new one
        # gives it, as a ledger kept from statements may date it.
        meta = {"source": plaid_source(OLD_UNION_ITEM, r.transaction_id)}
        text += _entry(r.day + timedelta(days=2 * (n % 2)), "Assets:Zq:Union", -r.amount, meta)
    twin = rows["ins_zq_twin/card"]
    text += "".join(_entry(r.day, "Liabilities:Zq:CardA", -r.amount, {}) for r in twin)
    text += "".join(_entry(r.day, "Liabilities:Zq:CardB", -r.amount, {}) for r in twin[:-1])
    for r in rows["ins_zq_blake/chk"]:
        text += _entry(r.day, "Assets:Zq:Blake:Checking", -r.amount, bare(r))
    for r in rows["ins_zq_blake/joint"]:
        text += _entry(r.day, "Assets:Zq:Joint", -r.amount, hearths("ins_zq_blake", r))
    for r in rows["ins_zq_blake/card"]:
        # Booked the day the charge was made, two days before it posted.
        meta = {"post-date": r.day.isoformat()}
        text += _entry(r.day - timedelta(days=2), "Liabilities:Zq:Blake:Card", -r.amount, meta)
    return {"main.beancount": 'option "operating_currency" "USD"\n\n' + text}
