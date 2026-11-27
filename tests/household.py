"""An invented household whose every private datum is a unique, searchable string.

Avery (the admin) and Blake own a joint account; each of them, and Casey, also own accounts
alone, and one account belongs to nobody. Every name, balance, payee, category, security and
figure below is distinct, so finding one in a response proves exactly which datum leaked.

The household is written as a real beancount ledger with stored prices, so the tests run the
same code a household's own books do. The figures and visibility rules are worked out by hand
here, rather than computed by hearth, so the tests check hearth against the contract instead
of against itself.
"""

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_UP, Decimal

from hearth.sharing import Facet


def noon(day: date) -> datetime:
    """Midday of `day` where hearth runs, as the system clock reports it (in UTC).

    Every figure takes "today" as the local calendar day of its clock (``books.compute``), so
    a test that means "the clock reads `day`" means this moment, in whatever zone it runs.
    """
    return datetime.combine(day, time(12)).astimezone(UTC)


TODAY = date(2026, 10, 15)
NOW = noon(TODAY)
USERNAMES = ("avery", "blake", "casey")
ADMIN = "avery"
OPENED = date(2026, 8, 1)
OCT, SEP, PRICED = date(2026, 10, 3), date(2026, 9, 21), date(2026, 10, 14)


@dataclass(frozen=True)
class AccountFixture:
    key: str
    ledger_account: str
    name: str
    owners: frozenset[str]


ACCOUNTS = (
    AccountFixture("avery_solo", "Assets:Zq:AverySolo", "Quokka Savings", frozenset({"avery"})),
    AccountFixture("avery_invest", "Assets:Zq:AveryInvest", "Wombat Shares", frozenset({"avery"})),
    AccountFixture("joint", "Assets:Zq:Joint", "Narwhal Joint", frozenset({"avery", "blake"})),
    AccountFixture("blake_solo", "Assets:Zq:BlakeSolo", "Bilby Brokerage", frozenset({"blake"})),
    AccountFixture(
        "casey_checking", "Assets:Zq:CaseyChecking", "Caracal Checking", frozenset({"casey"})
    ),
    AccountFixture("casey_card", "Liabilities:Zq:CaseyCard", "Coati Card", frozenset({"casey"})),
    # Claimed by nobody: it must be visible to nobody, not to everybody.
    AccountFixture("unowned", "Assets:Zq:Orphan", "Ocelot Orphan", frozenset()),
)


@dataclass(frozen=True)
class NamedFixture:
    """An account a member named, which hearth does not see: no connection feeds it."""

    key: str
    ledger_account: str
    name: str
    owners: frozenset[str]
    kind: str
    institution: str
    mask: str | None


NAMED = (
    # Casey's own, which a loan payment from Casey's checking went to.
    NamedFixture(
        "casey_named",
        "Liabilities:Zq:CaseyLoan",
        "Gharial Loan",
        frozenset({"casey"}),
        "loan",
        "Pangolin Lending",
        None,
    ),
    # Avery's and Blake's, which Avery moved money into from Avery's own account: Blake sees
    # the joint side and nothing of Avery's account or the bank's words for it.
    NamedFixture(
        "joint_named",
        "Assets:Zq:JointSavings",
        "Tamarin Savings",
        frozenset({"avery", "blake"}),
        "bank",
        "Okapi Credit Union",
        "8136",
    ),
)

# Rows the import set aside that a member said went to one of those: date, payee, the
# account it is on, amount, review id, group, who said so, and where they said it went.
MOVED = (
    (
        OCT,
        "Payee Saki Sweep",
        "Assets:Zq:AverySolo",
        "-777.31",
        "avery-moved-1",
        "Transfer out: account transfer",
        "avery",
        "Assets:Zq:JointSavings",
    ),
    (
        OCT,
        "Payee Gecko Lender",
        "Assets:Zq:CaseyChecking",
        "-333.17",
        "casey-moved-1",
        "Loan payments: car payment",
        "casey",
        "Liabilities:Zq:CaseyLoan",
    ),
)

# Each security: the account holding it, the units held, and its stored price.
SECURITIES = {
    "ZQAA": ("Assets:Zq:AveryInvest", "10", "1234.56"),
    "ZQBB": ("Assets:Zq:BlakeSolo", "50", "700.00"),
}

# date, payee, narration, expense or income account, paying account, amount on that account
TRANSACTIONS = (
    (
        OCT,
        "Payee Avocet Market",
        "Memo Avocet note",
        "Expenses:Avocados",
        "Assets:Zq:AverySolo",
        "-1311.07",
    ),
    (SEP, "Payee Avocet September", "", "Expenses:Avocados", "Assets:Zq:AverySolo", "-88.83"),
    (SEP, "Payee Umber Stall", "", "Expenses:Umbrellas", "Assets:Zq:AverySolo", "-19.06"),
    (OCT, "Payee Aardvark Payroll", "", "Income:Aardwork", "Assets:Zq:AverySolo", "3113.58"),
    (OCT, "Payee Jacana Hall", "", "Expenses:Jointery", "Assets:Zq:Joint", "-1640.84"),
    (OCT, "Payee Bittern Nursery", "", "Expenses:Bonsai", "Assets:Zq:BlakeSolo", "-1927.53"),
    (OCT, "Payee Bittern Wages", "", "Income:Bittern", "Assets:Zq:BlakeSolo", "2604.19"),
    (OCT, "Payee Curlew Maps", "", "Expenses:Cartography", "Assets:Zq:CaseyChecking", "-1045.19"),
    (OCT, "Payee Curlew Stipend", "", "Income:Curlew", "Assets:Zq:CaseyChecking", "1418.77"),
    (OCT, "Payee Kestrel Kites", "", "Expenses:Kites", "Liabilities:Zq:CaseyCard", "-12.34"),
    (OCT, "Payee Oriole Unowned", "", "Expenses:Orphanage", "Assets:Zq:Orphan", "-17.36"),
    # Last month, one row a part, so each part's September is one category and one total.
    (SEP, "Payee Bunting Barrow", "", "Expenses:Bunting", "Assets:Zq:BlakeSolo", "-246.13"),
    (SEP, "Payee Bittern September", "", "Income:Bittern", "Assets:Zq:BlakeSolo", "2222.71"),
    (SEP, "Payee Cormorant Coast", "", "Expenses:Cormorants", "Assets:Zq:CaseyChecking", "-357.24"),
    (SEP, "Payee Juniper Joint", "", "Expenses:Junipers", "Assets:Zq:Joint", "-468.35"),
)

# A row the import set aside for Casey to review: nothing could be proposed for it.
PILE = (
    (OCT, "Payee Shrike Pending", "Assets:Zq:CaseyChecking", "-23.45", "casey-row-1", "Unsure"),
    # Money whose other side hearth does not see, which nobody has said where it went yet.
    (
        OCT,
        "Payee Jerboa Transfer",
        "Assets:Zq:CaseyChecking",
        "-55.55",
        "casey-where-1",
        "Transfer out: account transfer",
    ),
)
UNRESOLVED = "Equity:Review:Unresolved"

# The cash each account ends with today; the opening balance makes the ledger arrive there.
CASH = {
    "Assets:Zq:AverySolo": "52341.17",
    "Assets:Zq:Joint": "20000.42",
    "Assets:Zq:BlakeSolo": "38519.27",
    "Assets:Zq:CaseyChecking": "4815.62",
    "Liabilities:Zq:CaseyCard": "-310.25",
    "Assets:Zq:Orphan": "9134.58",
}

# Figures by hand. Member figures are sole accounts only; the joint account is its own line.
BALANCE = {
    "avery_solo": "52,341.17",
    "avery_invest": "12,345.60",
    "joint": "20,000.42",
    "blake_solo": "73,519.27",
    "casey_checking": "4,815.62",
    "casey_card": "310.25",
    "unowned": "9,134.58",
}
NET_WORTH = {"avery": "64,686.77", "blake": "73,519.27", "casey": "4,505.37"}
SPENDING = {"avery": "1,311.07", "blake": "1,927.53", "casey": "1,057.53"}
INCOME = {"avery": "3,113.58", "blake": "2,604.19", "casey": "1,418.77"}
JOINT_NET_WORTH = "20,000.42"
JOINT_SPENDING = "1,640.84"
HOUSEHOLD_NET_WORTH = "162,711.83"
HOUSEHOLD_SPENDING = "5,936.97"
HOUSEHOLD_INCOME = "7,136.54"


def ledger_text() -> str:
    """The household's books, as beancount reads them."""
    lines = ['option "operating_currency" "USD"', ""]
    lines += [f"{OPENED} commodity {symbol}" for symbol in SECURITIES]
    lines += [f"{OPENED} open {account.ledger_account}" for account in ACCOUNTS]
    lines += [f"{OPENED} open {category}" for category in sorted({t[3] for t in TRANSACTIONS})]
    lines += [f"{OPENED} open Equity:Opening", f"{OPENED} open {UNRESOLVED}", ""]
    moved: dict[str, Decimal] = {}
    for *_, account, amount in TRANSACTIONS:
        moved[account] = moved.get(account, Decimal(0)) + Decimal(amount)
    for _, _, account, amount, _, _ in PILE:
        moved[account] = moved.get(account, Decimal(0)) + Decimal(amount)
    for _, _, account, amount, *_ in MOVED:
        moved[account] = moved.get(account, Decimal(0)) + Decimal(amount)
    for account, cash in CASH.items():
        opening = Decimal(cash) - moved.get(account, Decimal(0))
        lines += [f'{OPENED} * "Opening"', f"  {account}  {opening} USD", "  Equity:Opening", ""]
    for symbol, (account, units, price) in SECURITIES.items():
        lines += [
            f'{OPENED} * "Opening position"',
            f"  {account}  {units} {symbol} {{{price} USD}}",
            "  Equity:Opening",
            "",
        ]
    for day, payee, account, amount, review_id, group in [*PILE, *(m[:6] for m in MOVED)]:
        lines += [
            f'{day} ! "{payee}" ""',
            f'  review-id: "{review_id}"',
            f'  review-group: "{group}"',
            f"  {account}  {amount} USD",
            f"  {UNRESOLVED}",
            "",
        ]
    for day, payee, narration, category, account, amount in TRANSACTIONS:
        lines += [
            f'{day} * "{payee}" "{narration}"',
            f"  {account}  {amount} USD",
            f"  {category}",
            "",
        ]
    return "\n".join(lines)


def prices() -> Iterator[tuple[str, date, Decimal]]:
    """The stored closes: each security at the end of September and on the latest session."""
    for symbol, (_, _, price) in SECURITIES.items():
        for day in (date(2026, 9, 30), PRICED):
            yield symbol, day, Decimal(price)


# The sharing contract, stated independently of hearth's implementation.

Choices = Mapping[str, frozenset[Facet]]
Rule = Callable[[str, Choices], bool]
JOINT = frozenset({"avery", "blake"})
PARTS = (*(frozenset({u}) for u in USERNAMES), JOINT)


def shared(facet: Facet, owners: frozenset[str]) -> Rule:
    """Visible to an owner, or to anyone once every owner shares `facet`. What nobody owns,
    nobody sees."""

    def rule(viewer: str, choices: Choices) -> bool:
        if not owners:
            return False
        return viewer in owners or all(facet in choices[owner] for owner in owners)

    return rule


def any_of(*rules: Rule) -> Rule:
    return lambda viewer, choices: any(rule(viewer, choices) for rule in rules)


def all_of(*rules: Rule) -> Rule:
    return lambda viewer, choices: all(rule(viewer, choices) for rule in rules)


def but_not(rule: Rule, unless: Rule) -> Rule:
    return lambda viewer, choices: rule(viewer, choices) and not unless(viewer, choices)


def never(viewer: str, choices: Choices) -> bool:
    return False


@dataclass(frozen=True)
class Secret:
    """One private datum, in every form a page renders it, and the rule that governs it.

    Money is shown to the cent in tables and in whole dollars as a total, so a figure is
    leaked if either form appears, and reached if either does.
    """

    texts: tuple[str, ...]
    what: str
    visible: Rule


def secret(text: str, what: str, visible: Rule) -> Secret:
    """A secret; an amount in the thousands is also looked for in whole dollars. (A smaller
    whole number, such as "12", would match too much else to prove anything.)"""
    forms = [text]
    if re.fullmatch(r"\d{1,3}(,\d{3})+\.\d\d", text):
        whole = Decimal(text.replace(",", "")).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        forms.append(f"{whole:,}")
    return Secret(tuple(forms), what, visible)


def appears(text: str, seen: str) -> bool:
    """Whether `text` is in `seen` as itself, not as part of another word, figure or id.

    hearth and the stand-in Plaid make up random ids: Plaid's request, Item and transaction
    ids, the review ids drawn from them, account ids. A short secret, a card's four mask digits,
    a ticker or a whole-dollar figure, turns up inside one of them now and then by chance, which
    is nobody's datum. So an end of `text` that is a letter or a digit counts only where no
    letter or digit runs into it, nor a point into the start of a figure: "5528" is not found
    in "demo3e5528fa", "15528" or "0.5528", and is in "mask=5528", "(5528)" and "-5528.00". An
    end that is neither, as in "&nbsp;5528" or '"10"', is found as it stands.
    """
    pattern = re.escape(text)
    if text[:1].isalnum():
        pattern = ("(?<![0-9A-Za-z.])" if text[0].isdigit() else "(?<![0-9A-Za-z])") + pattern
    if text[-1:].isalnum():
        pattern += "(?![0-9A-Za-z])"
    return re.search(pattern, seen) is not None


class Seen:
    """Everything a member was shown, or hearth wrote, searched for secrets with ``in`` as
    ``appears`` finds them."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __contains__(self, text: str) -> bool:
        return appears(text, self.text)


def owners_of(key: str) -> frozenset[str]:
    return next(a.owners for a in ACCOUNTS if a.key == key)


def _key_of(ledger_account: str) -> str:
    return next(a.key for a in ACCOUNTS if a.ledger_account == ledger_account)


def _detail(key: str) -> Rule:
    """Spending detail reaches a viewer through the transactions or the category breakdown."""
    return any_of(
        shared(Facet.TRANSACTIONS, owners_of(key)),
        shared(Facet.CATEGORY_BREAKDOWN, owners_of(key)),
    )


def _total(facet: Facet, username: str, key: str) -> Rule:
    """A member total that equals its only transaction is visible through either."""
    return any_of(shared(facet, frozenset({username})), _detail(key))


@dataclass(frozen=True)
class PlaidFixture:
    """A member's connection to an invented institution, through their own Plaid account."""

    username: str
    institution: str
    checking: str
    checking_mask: str
    checking_balance: str
    card: str
    card_mask: str
    card_balance: str
    payee: str


PLAID = (
    PlaidFixture(
        "avery",
        "Gryphon Mutual",
        "Aardvark Everyday",
        "4417",
        "3,141.59",
        "Axolotl Rewards",
        "6623",
        "271.82",
        "Zephyrine Bakery",
    ),
    PlaidFixture(
        "blake",
        "Hippogriff Savings",
        "Badger Basic",
        "5528",
        "1,618.03",
        "Basilisk Card",
        "7734",
        "141.42",
        "Quixotic Cobbler",
    ),
    PlaidFixture(
        "casey",
        "Kraken Federal",
        "Capybara Current",
        "6639",
        "2,718.28",
        "Chimera Credit",
        "8845",
        "173.20",
        "Wobbly Lanternworks",
    ),
)
"""Each connection is its holder's alone, joint owners and admins included."""


def among(owners: frozenset[str]) -> Rule:
    """Visible to the owners themselves and nobody else, whatever anyone shares."""
    return lambda viewer, choices: viewer in owners


def holder(username: str) -> Rule:
    return lambda viewer, choices: viewer == username


def plaid_secrets() -> Iterator[Secret]:
    for p in PLAID:
        rule = holder(p.username)
        for text, what in (
            (p.institution, "institution"),
            (p.checking, "checking name"),
            (f"&nbsp;{p.checking_mask}", "checking mask"),
            (p.checking_balance, "checking bank balance"),
            (p.card, "card name"),
            (f"&nbsp;{p.card_mask}", "card mask"),
            (p.card_balance, "card bank balance"),
        ):
            yield secret(text, f"{p.username}'s connection: {what}", rule)
        # What Plaid sent is kept for the import; no page shows a fetched transaction.
        yield secret(p.payee, f"{p.username}'s fetched payee", never)


def secrets(account_ids: Mapping[str, str]) -> Iterator[Secret]:
    """Every private datum in the household, each with the rule that governs it."""
    yield from plaid_secrets()
    for account in ACCOUNTS:
        exists = any_of(
            shared(Facet.ACCOUNT_BALANCES, account.owners),
            shared(Facet.TRANSACTIONS, account.owners),
        )
        yield secret(account.name, f"{account.key} name", exists)
        yield secret(account_ids[account.key], f"{account.key} id", exists)
        # A ledger name is shown only where its owner files a connection into it: mapping.
        yield secret(account.ledger_account, f"{account.key} ledger name", among(account.owners))
    for key in ("avery_solo", "avery_invest", "casey_checking", "casey_card", "unowned"):
        yield secret(BALANCE[key], f"{key} balance", shared(Facet.ACCOUNT_BALANCES, owners_of(key)))
    # A part with a single account has a net worth equal to that account's balance.
    yield secret(
        BALANCE["blake_solo"],
        "blake's balance, which is blake's net worth",
        any_of(
            shared(Facet.ACCOUNT_BALANCES, owners_of("blake_solo")),
            shared(Facet.NET_WORTH_TOTAL, owners_of("blake_solo")),
        ),
    )
    yield secret(
        JOINT_NET_WORTH,
        "the joint balance, which is the joint line's net worth",
        any_of(shared(Facet.ACCOUNT_BALANCES, JOINT), shared(Facet.NET_WORTH_TOTAL, JOINT)),
    )

    for _, payee, narration, _, paid_from, _ in TRANSACTIONS:
        key = _key_of(paid_from)
        rule = shared(Facet.TRANSACTIONS, owners_of(key))
        yield secret(payee, f"{key} payee", rule)
        if narration:
            yield secret(narration, f"{key} narration", rule)
    for text, key in (
        ("Avocados", "avery_solo"),
        ("Bonsai", "blake_solo"),
        ("Cartography", "casey_checking"),
        ("Kites", "casey_card"),
        ("12.34", "casey_card"),
    ):
        yield secret(text, f"{key} spending detail", _detail(key))
    # Last month's detail. Sharing categories shares every month the books hold
    # (design/reporting.md, decision 1), so September's categories reach Household's "Where
    # September went" as well as each account's own month of transactions.
    for text in ("Umbrellas", "19.06", "88.83"):
        yield secret(text, "avery_solo last month", _detail("avery_solo"))
    for text, key in (("Bunting", "blake_solo"), ("Cormorants", "casey_checking")):
        yield secret(text, f"{key} last month's category", _detail(key))
    yield secret("Junipers", "joint last month's category", _detail("joint"))
    # One row a part last month, so its September spending is its only category and its only
    # transaction too: seen as a category row, as the part's one labelled row when it shares
    # only its total, or in the account's own month.
    for text, username, key in (
        ("246.13", "blake", "blake_solo"),
        ("357.24", "casey", "casey_checking"),
    ):
        yield secret(
            text,
            f"{username}'s September spending",
            _total(Facet.MONTHLY_SPENDING_TOTAL, username, key),
        )
    yield secret(
        "468.35",
        "joint September spending",
        any_of(shared(Facet.MONTHLY_SPENDING_TOTAL, JOINT), _detail("joint")),
    )
    # Avery's September is two categories on one day, so its total is shown as Avery's one
    # labelled row, to whoever sees the total but not the categories, and as that day's
    # spending in the account's own month of transactions.
    yield secret(
        "107.89",
        "avery's September spending as one row or one day",
        any_of(
            but_not(
                shared(Facet.MONTHLY_SPENDING_TOTAL, frozenset({"avery"})),
                shared(Facet.CATEGORY_BREAKDOWN, frozenset({"avery"})),
            ),
            shared(Facet.TRANSACTIONS, owners_of("avery_solo")),
        ),
    )
    # Blake's is the only money in last month, so what the household took in is Blake's,
    # wherever Blake is in it: among the parts whose both totals the viewer sees.
    yield secret(
        "2,222.71",
        "blake's September income",
        any_of(
            all_of(
                shared(Facet.MONTHLY_INCOME_TOTAL, frozenset({"blake"})),
                shared(Facet.MONTHLY_SPENDING_TOTAL, frozenset({"blake"})),
            ),
            shared(Facet.TRANSACTIONS, owners_of("blake_solo")),
        ),
    )
    for _, payee, set_aside_from, _, _, _ in PILE:
        rule = shared(Facet.TRANSACTIONS, owners_of(_key_of(set_aside_from)))
        yield secret(payee, "a set-aside row's payee", rule)
    for named in NAMED:
        exists = any_of(
            shared(Facet.ACCOUNT_BALANCES, named.owners), shared(Facet.TRANSACTIONS, named.owners)
        )
        yield secret(named.name, f"{named.key} name", exists)
        yield secret(account_ids[named.key], f"{named.key} id", exists)
        yield secret(named.institution, f"{named.key} institution", exists)
        if named.mask:
            yield secret(f"&nbsp;{named.mask}", f"{named.key} mask", exists)
        yield secret(named.ledger_account, f"{named.key} ledger name", among(named.owners))
    # A row moved onto an account hearth does not see is the paying account's transaction: its
    # words go with that account's transactions, and its amount with either account's.
    for _, payee, paid_from, amount, _, _, _, onto in MOVED:
        key = _key_of(paid_from)
        on = next(n for n in NAMED if n.ledger_account == onto)
        yield secret(payee, f"{key} moved payee", shared(Facet.TRANSACTIONS, owners_of(key)))
        yield secret(
            amount.lstrip("-"),
            f"{key} moved amount",
            any_of(
                shared(Facet.TRANSACTIONS, owners_of(key)),
                shared(Facet.TRANSACTIONS, on.owners),
            ),
        )
    yield secret("Orphanage", "unowned category", never)
    yield secret("17.36", "unowned amount", never)

    yield secret(
        SPENDING["avery"],
        "avery spending",
        _total(Facet.MONTHLY_SPENDING_TOTAL, "avery", "avery_solo"),
    )
    yield secret(
        SPENDING["blake"],
        "blake spending",
        _total(Facet.MONTHLY_SPENDING_TOTAL, "blake", "blake_solo"),
    )
    yield secret(
        SPENDING["casey"],
        "casey spending",
        shared(Facet.MONTHLY_SPENDING_TOTAL, frozenset({"casey"})),
    )
    # This month's joint spending is in Household's "so far" only as part of a sum, and its
    # categories only in last month's panel: on its own, it is the joint account's own month
    # of transactions, or the joint line's change in net worth since Oct 1, which it is all of.
    yield secret("Jointery", "joint spending detail", shared(Facet.TRANSACTIONS, JOINT))
    yield secret(
        JOINT_SPENDING,
        "joint spending",
        any_of(shared(Facet.NET_WORTH_TOTAL, JOINT), shared(Facet.TRANSACTIONS, JOINT)),
    )
    for username, key in (
        ("avery", "avery_solo"),
        ("blake", "blake_solo"),
        ("casey", "casey_checking"),
    ):
        yield secret(
            INCOME[username],
            f"{username} income",
            any_of(
                shared(Facet.MONTHLY_INCOME_TOTAL, frozenset({username})),
                shared(Facet.TRANSACTIONS, owners_of(key)),
            ),
        )
    for username in ("avery", "casey"):
        yield secret(
            NET_WORTH[username],
            f"{username} net worth",
            shared(Facet.NET_WORTH_TOTAL, frozenset({username})),
        )

    for symbol, (held_in, _, _) in SECURITIES.items():
        key = _key_of(held_in)
        yield secret(symbol, f"{key} position", shared(Facet.ACCOUNT_BALANCES, owners_of(key)))
    yield secret(
        "35,000.00", "blake position value", shared(Facet.ACCOUNT_BALANCES, owners_of("blake_solo"))
    )

    # A household total shown as complete must have every part visible, or a withheld part
    # could be had by subtraction. A total without some part is labelled "without" it, and
    # is a sum of parts each visible on its own.
    for text, facet, what in (
        (HOUSEHOLD_NET_WORTH, Facet.NET_WORTH_TOTAL, "household net worth"),
        (HOUSEHOLD_SPENDING, Facet.MONTHLY_SPENDING_TOTAL, "household spending"),
        (HOUSEHOLD_INCOME, Facet.MONTHLY_INCOME_TOTAL, "household income"),
    ):
        yield secret(text, what, all_of(*(shared(facet, part) for part in PARTS)))


BLAKE_TAX = (("2026-09-12", "1,613.29"), ("2026-10-09", "1,527.46"))
"""Tax Blake paid from their own account, in a month that is over and in this one: booked
only where a test says so (`tax_text`), as every other figure here is without it. In the
thousands, so each is looked for in whole dollars as well as to the cent (`secret`), and of
figures no other sum here comes to."""

BLAKE_TAX_WAITING = ("2026-10-11", "1,348.27")
"""More of Blake's tax this month, waiting in Blake's review: how much of a total rests on
rows nobody has checked is Blake's review state, told only to whoever may know it."""


def tax_text() -> str:
    """Blake's tax, as beancount reads it, to append to the household's books, with this
    month's last row waiting in Blake's review, so what rests on it is crawled too."""
    lines = [f"{OPENED} open Expenses:Taxes", ""]
    for day, amount in BLAKE_TAX:
        lines += [
            f'{day} * "Payee Bittern Revenue" ""',
            f"  Assets:Zq:BlakeSolo  -{amount.replace(',', '')} USD",
            "  Expenses:Taxes",
            "",
        ]
    day, amount = BLAKE_TAX_WAITING
    lines += [
        f'{day} ! "Payee Bittern Revenue" ""',
        '  review-id: "blake-tax-1"',
        '  review-group: "Bittern Revenue"',
        f"  Assets:Zq:BlakeSolo  -{amount.replace(',', '')} USD",
        "  Expenses:Taxes",
        "",
    ]
    return "\n".join(lines)


def tax_secrets() -> Iterator[Secret]:
    """Blake's tax is never spending, and is its own figure only to whoever may see Blake's
    categories or transactions: anyone else is told Blake's spending without it, and nothing
    of it. How much of it waits in Blake's review is Blake's review state: only Blake, and
    whoever Blake's transactions are shared with, may be told it (``access.may_see_review_state``),
    so a page that says "of it not yet reviewed" beside Blake's tax to anyone else is a leak."""
    for _, amount in BLAKE_TAX:
        yield secret(amount, "blake tax", _detail("blake_solo"))
    yield secret(
        BLAKE_TAX_WAITING[1],
        "blake tax waiting in review",
        shared(Facet.TRANSACTIONS, owners_of("blake_solo")),
    )
