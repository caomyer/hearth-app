"""An invented household whose every private datum is a unique, searchable string.

Avery (the admin) and Blake own two joint accounts; each of them, and Casey,
also owns two accounts alone. Every name, balance, payee, category and figure below is
distinct, so finding one in a response proves exactly which datum leaked.

The figures and the visibility rules are written out by hand here rather than
computed by hearth, so the tests check hearth against the contract instead of
against itself.
"""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from hearth.ledger import InMemoryLedger, Transaction
from hearth.sharing import Facet

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
THIS_MONTH = "2026-10"
LAST_MONTH = "2026-09"

USERNAMES = ("avery", "blake", "casey")
ADMIN = "avery"


@dataclass(frozen=True)
class AccountFixture:
    key: str
    ledger_account: str
    name: str
    owners: frozenset[str]
    balance: Decimal
    transactions: tuple[Transaction, ...]


OCT, SEP = date(2026, 10, 3), date(2026, 9, 21)


def _t(day: date, payee: str, category: str, amount: str, narration: str = "") -> Transaction:
    return Transaction(day, payee, narration, category, Decimal(amount))


ACCOUNTS = (
    AccountFixture(
        key="avery_solo",
        ledger_account="Assets:Zq:AverySolo",
        name="Quokka Savings",
        owners=frozenset({"avery"}),
        balance=Decimal("52341.17"),
        transactions=(
            _t(OCT, "Payee Avocet Market", "Expenses:Avocados", "-311.07", "Memo Avocet note"),
            _t(SEP, "Payee Avocet September", "Expenses:Avocados", "-88.83"),
            _t(SEP, "Payee Umber Stall", "Expenses:Umbrellas", "-19.06"),
        ),
    ),
    AccountFixture(
        key="avery_card",
        ledger_account="Liabilities:Zq:AveryCard",
        name="Puffin Card",
        owners=frozenset({"avery"}),
        balance=Decimal("-2468.13"),
        transactions=(_t(OCT, "Payee Pipit Pantry", "Expenses:Pastries", "-54.21"),),
    ),
    AccountFixture(
        key="joint",
        ledger_account="Assets:Zq:Joint",
        name="Narwhal Joint",
        owners=frozenset({"avery", "blake"}),
        balance=Decimal("20000.42"),
        transactions=(_t(OCT, "Payee Jacana Hall", "Expenses:Jointery", "-640.84"),),
    ),
    AccountFixture(
        key="joint_card",
        ledger_account="Liabilities:Zq:JointCard",
        name="Numbat Joint Card",
        owners=frozenset({"avery", "blake"}),
        balance=Decimal("-1188.09"),
        transactions=(_t(OCT, "Payee Jabiru Jams", "Expenses:Jamboree", "-73.31"),),
    ),
    AccountFixture(
        key="blake_solo",
        ledger_account="Assets:Zq:BlakeSolo",
        name="Bilby Brokerage",
        owners=frozenset({"blake"}),
        balance=Decimal("73519.27"),
        transactions=(
            _t(OCT, "Payee Bittern Nursery", "Expenses:Bonsai", "-927.53"),
            _t(OCT, "Payee Bittern Wages", "Income:Bittern", "2604.19"),
        ),
    ),
    AccountFixture(
        key="blake_cash",
        ledger_account="Assets:Zq:BlakeCash",
        name="Bandicoot Cash",
        owners=frozenset({"blake"}),
        balance=Decimal("361.44"),
        transactions=(_t(OCT, "Payee Brolga Bakery", "Expenses:Breadmaking", "-66.18"),),
    ),
    AccountFixture(
        key="casey_checking",
        ledger_account="Assets:Zq:CaseyChecking",
        name="Caracal Checking",
        owners=frozenset({"casey"}),
        balance=Decimal("4815.62"),
        transactions=(_t(OCT, "Payee Curlew Maps", "Expenses:Cartography", "-45.19"),),
    ),
    AccountFixture(
        key="casey_card",
        ledger_account="Liabilities:Zq:CaseyCard",
        name="Coati Card",
        owners=frozenset({"casey"}),
        balance=Decimal("-310.25"),
        transactions=(_t(OCT, "Payee Kestrel Kites", "Expenses:Kites", "-12.34"),),
    ),
    # Claimed by nobody: it must be visible to nobody, not to everybody.
    AccountFixture(
        key="unowned",
        ledger_account="Assets:Zq:Orphan",
        name="Ocelot Orphan",
        owners=frozenset(),
        balance=Decimal("9134.58"),
        transactions=(_t(OCT, "Payee Oriole Unowned", "Expenses:Orphanage", "-17.36"),),
    ),
)

# Figures, by hand. A member's cover only the accounts they own alone; the joint
# accounts are one line of their own. Half of a joint figure must appear nowhere.
NET_WORTH = {"avery": "49,873.04", "blake": "73,880.71", "casey": "4,505.37"}
SPENDING = {"avery": "365.28", "blake": "993.71", "casey": "57.53"}
AVERY_SPENDING_LAST_MONTH = "107.89"
JOINT_OWNERS = frozenset({"avery", "blake"})
JOINT_NET_WORTH = "18,812.33"
JOINT_SPENDING = "714.15"
JOINT_HALVES = ("9,406.16", "9,406.17", "357.07", "357.08", "320.42", "10,000.21")
HOUSEHOLD_NET_WORTH = "147,071.45"
HOUSEHOLD_SPENDING = "2,130.67"


def ledger() -> InMemoryLedger:
    return InMemoryLedger(
        {a.ledger_account: a.balance for a in ACCOUNTS},
        {a.ledger_account: a.transactions for a in ACCOUNTS},
    )


# The sharing contract, stated independently of hearth's implementation.

Choices = Mapping[str, frozenset[Facet]]
Rule = Callable[[str, Choices], bool]


def shared(facet: Facet, owners: frozenset[str]) -> Rule:
    """Visible to an owner, or to anyone once every owner shares ``facet``."""

    def rule(viewer: str, choices: Choices) -> bool:
        if not owners:
            return False
        return viewer in owners or all(facet in choices[owner] for owner in owners)

    return rule


def any_of(*rules: Rule) -> Rule:
    return lambda viewer, choices: any(rule(viewer, choices) for rule in rules)


def all_of(*rules: Rule) -> Rule:
    return lambda viewer, choices: all(rule(viewer, choices) for rule in rules)


def never(viewer: str, choices: Choices) -> bool:
    return False


@dataclass(frozen=True)
class Secret:
    text: str
    what: str
    visible: Rule


def _money(amount: Decimal) -> str:
    return f"{abs(amount):,.2f}"


def _account(key: str) -> AccountFixture:
    return next(a for a in ACCOUNTS if a.key == key)


def _net_worth(username: str) -> Rule:
    return shared(Facet.NET_WORTH_TOTAL, frozenset({username}))


def _spending(username: str) -> Rule:
    return shared(Facet.MONTHLY_SPENDING_TOTAL, frozenset({username}))


def _breakdown(username: str) -> Rule:
    return shared(Facet.CATEGORY_BREAKDOWN, frozenset({username}))


def _transactions_of(key: str) -> Rule:
    return shared(Facet.TRANSACTIONS, _account(key).owners)


def secrets(account_ids: Mapping[str, str]) -> Iterator[Secret]:
    """Every private datum in the household, each with the rule that governs it."""
    for account in ACCOUNTS:
        balance = shared(Facet.ACCOUNT_BALANCES, account.owners)
        transactions = shared(Facet.TRANSACTIONS, account.owners)
        exists = any_of(balance, transactions)
        yield Secret(account.name, f"{account.key} name", exists)
        yield Secret(account_ids[account.key], f"{account.key} id", exists)
        yield Secret(account.ledger_account, f"{account.key} ledger name", never)
        yield Secret(_money(account.balance), f"{account.key} balance", balance)
        for t in account.transactions:
            for text in (t.payee, t.narration):
                if text:
                    yield Secret(text, f"{account.key} transaction", transactions)

    # Categories and amounts reach a viewer through a transaction or a breakdown.
    yield from _either("Avocados", "avery_solo", "avery")
    yield from _either("311.07", "avery_solo", "avery")
    yield from _either("88.83", "avery_solo", "avery")
    yield from _either("Umbrellas", "avery_solo", "avery")
    yield from _either("19.06", "avery_solo", "avery")
    yield from _either("Pastries", "avery_card", "avery")
    yield from _either("54.21", "avery_card", "avery")
    yield from _either("Bonsai", "blake_solo", "blake")
    yield from _either("927.53", "blake_solo", "blake")
    yield from _either("Breadmaking", "blake_cash", "blake")
    yield from _either("66.18", "blake_cash", "blake")
    yield from _either("Cartography", "casey_checking", "casey")
    yield from _either("45.19", "casey_checking", "casey")
    yield from _either("Kites", "casey_card", "casey")
    yield from _either("12.34", "casey_card", "casey")
    yield Secret("Bittern", "blake income category", _transactions_of("blake_solo"))
    yield Secret("2,604.19", "blake income", _transactions_of("blake_solo"))
    yield Secret("Orphanage", "unowned category", never)
    yield Secret("17.36", "unowned amount", never)
    # Joint spending is in no member's breakdown, only in the joint transactions.
    yield Secret("Jointery", "joint category", _transactions_of("joint"))
    yield Secret("640.84", "joint amount", _transactions_of("joint"))
    yield Secret("Jamboree", "joint card category", _transactions_of("joint_card"))
    yield Secret("73.31", "joint card amount", _transactions_of("joint_card"))
    for half in JOINT_HALVES:
        yield Secret(half, "a share of a joint figure", never)

    for username in USERNAMES:
        yield Secret(NET_WORTH[username], f"{username} net worth", _net_worth(username))
        yield Secret(SPENDING[username], f"{username} spending", _spending(username))
    yield Secret(AVERY_SPENDING_LAST_MONTH, "avery spending last month", _spending("avery"))
    joint_net_worth = shared(Facet.NET_WORTH_TOTAL, JOINT_OWNERS)
    joint_spending = shared(Facet.MONTHLY_SPENDING_TOTAL, JOINT_OWNERS)
    yield Secret(JOINT_NET_WORTH, "joint net worth", joint_net_worth)
    yield Secret(JOINT_SPENDING, "joint spending", joint_spending)
    # A total that is shown while one part is withheld gives that part away.
    yield Secret(
        HOUSEHOLD_NET_WORTH,
        "household net worth",
        all_of(joint_net_worth, *(_net_worth(u) for u in USERNAMES)),
    )
    yield Secret(
        HOUSEHOLD_SPENDING,
        "household spending",
        all_of(joint_spending, *(_spending(u) for u in USERNAMES)),
    )
    yield from partial_totals()


def partial_totals() -> Iterator[Secret]:
    """A household total missing one part: a smaller number posing as complete."""
    for figures, what in (
        ({**NET_WORTH, "joint": JOINT_NET_WORTH}, "net worth"),
        ({**SPENDING, "joint": JOINT_SPENDING}, "spending"),
    ):
        for left_out in figures:
            rest = sum(Decimal(v.replace(",", "")) for k, v in figures.items() if k != left_out)
            yield Secret(f"{rest:,.2f}", f"household {what} without {left_out}", never)


def _either(text: str, account_key: str, username: str) -> Iterator[Secret]:
    rule = any_of(_transactions_of(account_key), _breakdown(username))
    yield Secret(text, f"{username} spending detail", rule)
