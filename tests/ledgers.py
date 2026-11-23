"""An invented ledger kept before hearth, of the shapes a real one has, for importing.

Every name and figure is invented. The ledger is opened from ``main.beancount``, which sets
options, runs one of beancount's own plugins and names one that is not, and includes a file of
accounts, which includes a file of prices in turn, and every half-year in ``years/`` by a glob.
It has a `pad`, lots held at cost and priced, `custom` directives, a plugin line in an included
file, a document, and one file with Windows line endings and no newline at its end, so a copy
that changed a byte would show. Its figures are worked out by hand below, never by hearth.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

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
