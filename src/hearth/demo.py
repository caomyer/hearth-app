"""An invented household for trying hearth out. Every name, account and figure is made up.

The demo writes a real beancount ledger and real stored prices, so it runs through exactly the
code a household's own books do: loading, balance checks, valuation, coverage and review. It
deliberately includes what real books have: history that changes source part way through the
year, a fund whose price has gone stale, and transactions waiting for review.

With `first_review`, it is the household on the day its first import lands instead: two years
of history, every row of it unreviewed, grouped by merchant and kind as an import groups them,
so the review queue can be tried at the size a real backfill has.

`seed_from_zero` is a household on its first day instead, the one most households start as:
members and nothing else, no ledger and no connections, and invented banks to connect, so the
whole way in can be tried from the first sign-in.
"""

import calendar
import random
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from beancount.core.data import Transaction

from hearth import accounts, auth, books, db, members, passwords
from hearth.books import Status
from hearth.config import Settings
from hearth.connections import Connections, RegisteredConnections, plaid_source
from hearth.disclosure import Disclosure
from hearth.members import MemberId
from hearth.plaid.client import Environment
from hearth.plaid.connector import Mapping
from hearth.plaid.fake import (
    DEMO_INSTITUTIONS,
    FakeAccount,
    FakeInstitution,
    FakePlaid,
    FakeTransaction,
)
from hearth.plaid.registry import ItemState, Registry
from hearth.plaid.setup import PlaidSetup
from hearth.review.ids import review_id
from hearth.review.pile import UNRESOLVED
from hearth.valuation.pricestore import PriceStore
from hearth.valuation.quotes import Quote
from hearth.vault.secret import KeyMaterial, Secret

PRICE_SOURCE = "demo"
FEED_STARTS = date(2026, 6, 8)
"""Statement downloads cover the year until here; a bank feed covers it from here on."""

HISTORY_DAYS = 430
SET_ASIDE_DAYS = 7
"""The last week's transactions wait for review, as the latest import would leave them."""
FIRST_REVIEW_DAYS = 730
"""A first import's backfill: two years, none of it reviewed yet."""

CARD_EXPIRED_DAYS_AGO = 12
"""Avery's card connection stopped updating this long ago, so the demo shows that state: more
than a week (`books.BEHIND_AFTER`), so Avery's own figures say the day its data stops."""


@dataclass(frozen=True, slots=True)
class DemoMember:
    username: str
    display_name: str
    is_admin: bool


MEMBERS = (
    DemoMember("avery", "Avery", is_admin=True),
    DemoMember("blake", "Blake", is_admin=False),
    DemoMember("casey", "Casey", is_admin=False),
)


@dataclass(frozen=True, slots=True)
class DemoAccount:
    ledger: str
    name: str
    owners: tuple[str, ...]


ACCOUNTS = (
    DemoAccount("Assets:Avery:Checking", "Everyday checking", ("avery",)),
    DemoAccount("Liabilities:Avery:Card", "Rewards card", ("avery",)),
    DemoAccount("Assets:Avery:Brokerage", "Individual brokerage", ("avery",)),
    DemoAccount("Assets:Blake:Checking", "Checking", ("blake",)),
    DemoAccount("Assets:Blake:Retirement", "Retirement", ("blake",)),
    DemoAccount("Assets:Casey:Checking", "Student checking", ("casey",)),
    DemoAccount("Liabilities:Casey:Card", "Starter card", ("casey",)),
    DemoAccount("Assets:Joint:Checking", "Joint checking", ("avery", "blake")),
    DemoAccount("Assets:Joint:Brokerage", "Joint brokerage", ("avery", "blake")),
)

FUNDS = {
    "TMIX": Decimal("82.40"),
    "INTX": Decimal("51.10"),
    "BIDX": Decimal("74.90"),
    "TD55": Decimal("66.30"),
}

FUND_NAMES = {
    "TMIX": "Total Market Index",
    "INTX": "International Index",
    "BIDX": "Bond Index",
    "TD55": "Target Date 2055",
}

# account that pays, category, payees, typical amount, visits a month
SPENDING = (
    ("Liabilities:Avery:Card", "Groceries", ("Corner Grocer", "Market on Fifth"), 64, 6),
    ("Liabilities:Avery:Card", "Dining", ("Lantern Noodle Bar", "Hilltop Cafe"), 38, 5),
    ("Liabilities:Avery:Card", "Shopping", ("Bookshop on Main", "Corner Hardware"), 52, 3),
    ("Liabilities:Avery:Card", "Subscriptions", ("Lumen Video", "Stride Fitness"), 15, 2),
    ("Assets:Avery:Checking", "Transport", ("Northside Parking", "City Transit"), 31, 4),
    ("Liabilities:Avery:Card", "Coffee", ("Bean Street", "Morning Kiln", "Drip House"), 6, 9),
    ("Liabilities:Avery:Card", "Household", ("Linen Loft", "Lamp Lane", "Hearth Supply"), 35, 3),
    ("Liabilities:Avery:Card", "Travel", ("Skyway Air", "Harbor Inn", "Rail North"), 160, 1),
    ("Assets:Avery:Checking", "Gifts", ("Paper Crane Gifts", "Bloom Florist"), 45, 1),
    ("Assets:Avery:Checking", "Pets", ("Paws Clinic", "Kibble Co"), 38, 2),
    ("Liabilities:Avery:Card", "Personal", ("Blue Comb Barber", "Fern Pharmacy"), 28, 2),
    ("Assets:Avery:Checking", "Fitness", ("Summit Climbing", "Tide Pool Swim"), 22, 1),
    ("Assets:Blake:Checking", "Dining", ("Greenleaf Bistro",), 44, 4),
    ("Assets:Blake:Checking", "Health", ("Riverside Clinic",), 95, 1),
    ("Assets:Blake:Checking", "Shopping", ("Paper Lantern Goods",), 60, 2),
    ("Assets:Casey:Checking", "Education", ("Campus Bookstore",), 48, 1),
    ("Liabilities:Casey:Card", "Dining", ("Hilltop Cafe",), 14, 4),
    ("Assets:Joint:Checking", "Groceries", ("Greenleaf Market",), 118, 4),
    ("Assets:Joint:Checking", "Utilities", ("City Power & Light",), 160, 1),
)


def seed(
    conn: sqlite3.Connection, ledger: Path, *, now: datetime, first_review: bool = False
) -> dict[str, str]:
    """Create the demo household and its books, returning each member's password."""
    ids: dict[str, MemberId] = {}
    chosen: dict[str, str] = {}
    for demo in MEMBERS:
        member, password = auth.create_member(
            conn,
            actor=None,
            username=demo.username,
            display_name=demo.display_name,
            is_admin=demo.is_admin,
            password=passwords.temporary_password(),
            now=now,
        )
        ids[demo.username] = member.id
        chosen[demo.username] = password
    for account in ACCOUNTS:
        accounts.register(
            conn,
            ledger_account=account.ledger,
            name=account.name,
            owners=[ids[owner] for owner in account.owners],
        )
    today = now.astimezone().date()
    history = FIRST_REVIEW_DAYS if first_review else HISTORY_DAYS
    prices = _prices(today, history)
    store = PriceStore(conn)
    for quote in _quotes(prices, now):
        store.record(quote)
    review_days = FIRST_REVIEW_DAYS if first_review else SET_ASIDE_DAYS
    ledger.write_text(_ledger(today, prices, history, review_days))
    return chosen


FROM_ZERO = (
    DemoMember("avery", "Avery", is_admin=True),
    DemoMember("blake", "Blake", is_admin=False),
)


def seed_from_zero(conn: sqlite3.Connection, *, now: datetime) -> dict[str, str]:
    """Create a household with members and nothing else, returning each one's password."""
    chosen: dict[str, str] = {}
    for demo in FROM_ZERO:
        member, password = auth.create_member(
            conn,
            actor=None,
            username=demo.username,
            display_name=demo.display_name,
            is_admin=demo.is_admin,
            password=passwords.temporary_password(),
            now=now,
        )
        chosen[member.username] = password
    return chosen


def plaid_from_zero(today: Callable[[], date]) -> PlaidSetup:
    """The stand-in for Plaid with only the invented banks a member can try connecting."""
    stand_in = FakePlaid(DEMO_INSTITUTIONS, today=today, accept_any_keys=True)
    keys = DemoKeys()
    return PlaidSetup(stand_in=stand_in, keys=lambda: keys, wait=lambda _seconds: None)


def _prices(today: date, history: int) -> dict[str, dict[date, Decimal]]:
    """A gentle random walk per fund over the demo's history, one close per weekday."""
    rng = random.Random(7)  # noqa: S311 - invented figures, not security
    walks: dict[str, dict[date, Decimal]] = {}
    for fund, start in FUNDS.items():
        price = start
        closes = {}
        for day in _days(today - timedelta(days=history), today):
            if day.weekday() < 5:
                price *= Decimal(1 + rng.gauss(0.0004, 0.009)).quantize(Decimal("0.0001"))
                closes[day] = price.quantize(Decimal("0.01"))
        walks[fund] = closes
    return walks


def _quotes(prices: dict[str, dict[date, Decimal]], now: datetime) -> Iterator[Quote]:
    today = now.astimezone().date()
    # Fetched at six this morning where hearth runs, or just now if the demo starts earlier.
    fetched = min(now, datetime.combine(today, time(6, 0)).astimezone(UTC))
    stale_after = today - timedelta(days=6)
    for fund, closes in prices.items():
        for day, price in closes.items():
            # The international fund's source stopped answering a few sessions ago.
            if fund == "INTX" and day > stale_after:
                continue
            if day >= today:
                continue
            yield Quote(fund, day, price, "USD", PRICE_SOURCE, f"demo:{fund}", fetched)


def _ledger(
    today: date, prices: dict[str, dict[date, Decimal]], history: int, review_days: int
) -> str:
    start = today - timedelta(days=history)
    out = ['option "title" "An invented household"', 'option "operating_currency" "USD"', ""]
    for fund, name in FUND_NAMES.items():
        out += [f"{start} commodity {fund}", f'  name: "{name}"']
    out += [f"{start} open {account.ledger}" for account in ACCOUNTS]
    categories = sorted({category for _, category, *_ in SPENDING} | {"Housing"})
    out += [f"{start} open Expenses:{category}" for category in categories]
    out += [
        f"{start} open Income:{name}" for name in ("Salary:Avery", "Salary:Blake", "Wages:Casey")
    ]
    out += [f"{start} open Equity:Opening", f"{start} open {UNRESOLVED}", ""]
    writer = _Writer(today, prices, review_days)
    out += writer.opening(start)
    for day in _days(start + timedelta(days=1), today):
        out += writer.day(day)
    return "\n".join(out) + "\n"


class _Writer:
    """Writes the household's transactions day by day, keeping card balances to settle them."""

    def __init__(
        self, today: date, prices: dict[str, dict[date, Decimal]], review_days: int
    ) -> None:
        self._today = today
        self._prices = prices
        self._review_days = review_days
        self._seen: Counter[tuple[str, date, Decimal, str]] = Counter()
        self._rng = random.Random(11)  # noqa: S311 - invented figures, not security
        self._owed: dict[str, Decimal] = {"Liabilities:Avery:Card": Decimal(0)}
        self._owed["Liabilities:Casey:Card"] = Decimal(0)
        self._row = 0

    def opening(self, start: date) -> list[str]:
        lines = []
        for account, amount in (
            ("Assets:Avery:Checking", "6200.00"),
            ("Assets:Blake:Checking", "4100.00"),
            ("Assets:Casey:Checking", "1450.00"),
            ("Assets:Joint:Checking", "9800.00"),
        ):
            lines += self._entry(
                start, "Opening balance", [(account, f"{amount} USD"), ("Equity:Opening", "")]
            )
        for account, fund, units in (
            ("Assets:Avery:Brokerage", "TMIX", "820"),
            ("Assets:Avery:Brokerage", "INTX", "640"),
            ("Assets:Blake:Retirement", "TD55", "2900"),
            ("Assets:Joint:Brokerage", "TMIX", "1100"),
            ("Assets:Joint:Brokerage", "BIDX", "520"),
        ):
            lines += self._entry(
                start,
                "Opening position",
                [(account, f"{units} {fund} {{{FUNDS[fund]} USD}}"), ("Equity:Opening", "")],
            )
        return lines

    def day(self, day: date) -> list[str]:
        lines: list[str] = []
        if day.day == 1:
            lines += self._spend(
                day, "Valley Mortgage Co", "Housing", "Assets:Joint:Checking", Decimal("2150.00")
            )
        if day.day == 2:
            for payer, amount in (
                ("Assets:Avery:Checking", "1400.00"),
                ("Assets:Blake:Checking", "1600.00"),
            ):
                lines += self._imported(
                    day,
                    "Transfer to joint checking",
                    (payer, Decimal(f"-{amount}")),
                    "Assets:Joint:Checking",
                    "Transfers to joint checking",
                    Decimal("0.97"),
                )
        if day.day == 5 and day in self._prices["TMIX"]:
            price = self._prices["TMIX"][day]
            units = (Decimal(600) / price).quantize(Decimal("0.001"))
            cost = (units * price).quantize(Decimal("0.01"))
            lines += self._entry(
                day,
                "Monthly purchase",
                [
                    ("Assets:Avery:Brokerage", f"{units} TMIX {{{price} USD}}"),
                    ("Assets:Avery:Checking", f"-{cost} USD"),
                ],
            )
        for payee, income, account, amount, on in (
            ("Example Employer", "Salary:Avery", "Assets:Avery:Checking", "3380.00", 15),
            ("Example Studio", "Salary:Blake", "Assets:Blake:Checking", "5200.00", 28),
            ("Campus Library", "Wages:Casey", "Assets:Casey:Checking", "720.00", 20),
        ):
            if day.day == on:
                lines += self._imported(
                    day, payee, (account, Decimal(amount)), f"Income:{income}", "Payroll", None
                )
        if day.day == 25:
            for card, payer in (
                ("Liabilities:Avery:Card", "Assets:Avery:Checking"),
                ("Liabilities:Casey:Card", "Assets:Casey:Checking"),
            ):
                owed = self._owed[card]
                if owed:
                    lines += self._imported(
                        day, "Card payment", (payer, -owed), card, "Card payments", Decimal("0.99")
                    )
                    self._owed[card] = Decimal(0)
        days_in_month = calendar.monthrange(day.year, day.month)[1]
        card_stopped = self._today - timedelta(days=CARD_EXPIRED_DAYS_AGO)
        for payer, category, payees, typical, visits in SPENDING:
            if payer == "Liabilities:Avery:Card" and day > card_stopped:
                continue  # The card's connection has expired: nothing arrives after this.
            if self._rng.random() < visits / days_in_month:
                spent = Decimal(max(3.0, self._rng.gauss(typical, typical * 0.35)))
                payee = self._rng.choice(payees)
                lines += self._spend(day, payee, category, payer, spent.quantize(Decimal("0.01")))
        return lines

    def _spend(
        self, day: date, payee: str, category: str, payer: str, amount: Decimal
    ) -> list[str]:
        if payer in self._owed:
            self._owed[payer] += amount
        if not self._unreviewed(day):
            return self._entry(
                day, payee, [(f"Expenses:{category}", f"{amount} USD"), (payer, f"-{amount} USD")]
            )
        draw = self._rng.random()
        if draw < 0.12:
            return self._imported(day, payee, (payer, -amount), None, "Unsure", None)
        confidence = Decimal(str(round(0.35 + 0.64 * self._rng.random(), 3)))
        group = payee if draw > 0.3 else "Unsure"
        proposed = f"Expenses:{category}"
        return self._imported(day, payee, (payer, -amount), proposed, group, confidence)

    def _unreviewed(self, day: date) -> bool:
        return (self._today - day).days < self._review_days

    def _imported(
        self,
        day: date,
        payee: str,
        bank: tuple[str, Decimal],
        proposed: str | None,
        group: str,
        confidence: Decimal | None,
    ) -> list[str]:
        """One imported row: reviewed if it is older than the review window, otherwise flagged
        unreviewed against what the import proposed, as beancount_nextgen writes it."""
        account, amount = bank
        if not self._unreviewed(day):
            assert proposed is not None  # noqa: S101 - only spending is ever unproposed
            return self._entry(day, payee, [(account, f"{amount} USD"), (proposed, "")])
        content = (account, day, amount, payee)
        nth = self._seen[content]
        self._seen[content] += 1
        meta = {"review-id": review_id(*content, nth), "review-group": group}
        if confidence is not None:
            meta["model-confidence"] = str(confidence)
        balance = proposed or UNRESOLVED
        return self._entry(
            day, payee, [(account, f"{amount} USD"), (balance, f"{-amount} USD")], "!", meta
        )

    def _entry(
        self,
        day: date,
        payee: str,
        postings: list[tuple[str, str]],
        flag: str = "*",
        meta: dict[str, str] | None = None,
    ) -> list[str]:
        self._row += 1
        origin = "bank-feed" if day >= FEED_STARTS else "statements"
        lines = [f'{day} {flag} "{payee}" ""', f'  source: "{origin}:{self._row}"']
        lines += [
            f"  {key}: {value}" if key == "model-confidence" else f'  {key}: "{value}"'
            for key, value in (meta or {}).items()
        ]
        lines += [f"  {account}  {amount}".rstrip() for account, amount in postings]
        return [*lines, ""]


def _days(start: date, end: date) -> Iterator[date]:
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


DEMO_BANKS = (
    (
        FakeInstitution(
            "ins_northfield",
            "Northfield Bank",
            (
                FakeAccount(
                    "chk", "Everyday checking", "2047", "depository", "checking", Decimal(0)
                ),
            ),
        ),
        ("Assets:Avery:Checking",),
    ),
    (
        FakeInstitution(
            "ins_cedar",
            "Cedar Card",
            (FakeAccount("card", "Rewards card", "8812", "credit", "credit card", Decimal(0)),),
        ),
        ("Liabilities:Avery:Card",),
    ),
    (
        FakeInstitution(
            "ins_harbor",
            "Harbor Credit Union",
            (FakeAccount("chk", "Checking", "3190", "depository", "checking", Decimal(0)),),
        ),
        ("Assets:Blake:Checking",),
    ),
)
"""The banks the demo's members are already connected to, each account with the ledger account
it feeds. The stand-in sends them the history and balance that ledger account already holds."""


def _as_the_bank_sends(
    bank: FakeInstitution, feeds: tuple[str, ...], ledger: Path
) -> FakeInstitution:
    """The bank's accounts as Plaid would give them: amounts out of the account positive, and a
    card's balance as what is owed."""
    entries = books.load_entries(ledger)
    accounts_sent = []
    for account, ledger_account in zip(bank.accounts, feeds, strict=True):
        sent = []
        held = Decimal(0)
        for entry in entries:
            if not isinstance(entry, Transaction):
                continue
            for posting in entry.postings:
                if posting.account != ledger_account or posting.units is None:
                    continue
                number = posting.units.number
                if number is None:
                    continue
                held += number
                if entry.payee != "Opening balance":
                    name = entry.payee or entry.narration or ""
                    sent.append(FakeTransaction(name, -number, entry.date))
        current = -held if account.type == "credit" else held
        accounts_sent.append(replace(account, current=current, transactions=tuple(sent)))
    return replace(bank, accounts=tuple(accounts_sent))


class DemoKeys:
    """The demo's vault keys, in memory: it holds no real secret, and a throwaway household
    should leave nothing behind in the Keychain. Never used outside the demo."""

    def __init__(self) -> None:
        self._keys: dict[str, KeyMaterial] = {}

    def get(self, name: str) -> KeyMaterial | None:
        return self._keys.get(name)

    def put(self, name: str, key: KeyMaterial) -> None:
        self._keys.setdefault(name, key)

    def delete(self, name: str) -> None:
        self._keys.pop(name, None)


def plaid(today: Callable[[], date], ledger: Path) -> PlaidSetup:
    """The stand-in for Plaid: the demo's own banks, and the invented ones a member can try."""
    institutions = [_as_the_bank_sends(bank, feeds, ledger) for bank, feeds in DEMO_BANKS]
    stand_in = FakePlaid([*institutions, *DEMO_INSTITUTIONS], today=today, accept_any_keys=True)
    keys = DemoKeys()
    # The stand-in prepares history the moment it is asked again, so there is nothing to wait for.
    return PlaidSetup(stand_in=stand_in, keys=lambda: keys, wait=lambda _seconds: None)


def connect(settings: Settings, setup: PlaidSetup, *, now: datetime) -> None:
    """Connect the demo's members through the stand-in, by the same path a member takes.

    Avery and Blake bring Plaid accounts of their own; Avery's card connection has expired so
    that state can be seen. Casey has none, and can try the whole flow: their own keys, or the
    household's Plaid account, which Avery entered.
    """
    conn = db.connect(settings.database)
    try:
        status = Status(books=None, failure=None, recomputing=False)
        for username, banks in (
            ("avery", ("ins_northfield", "ins_cedar")),
            ("blake", ("ins_harbor",)),
        ):
            member = _seeded(conn, username)
            connector = Disclosure.load(conn, member, status).connector(
                conn,
                database=settings.database,
                feed_path=settings.feed_database,
                data_dir=settings.data_dir,
                setup=setup,
                clock=lambda: now,
                runner=lambda work: work(),
            )
            for household in (False, True) if username == "avery" else (False,):
                connector.save_credentials(
                    household=household,
                    client_id=Secret(f"demo-{username}-{'household' if household else 'own'}"),
                    secret=Secret("demo-secret"),
                    environment=Environment.DEMO.value,
                    item_limit="10",
                    used_elsewhere="3" if household else "0",
                )
            for bank in banks:
                start = connector.start_link(served_at="http://localhost")
                assert setup.stand_in is not None  # noqa: S101 - the demo always has it
                token = setup.stand_in.public_token(start.link_token, bank)
                connector.finish_link(Secret(token), "")
            rows = connector.mapping().rows
            chosen = [
                Mapping(row.account.account_id, feeds, row.chosen_owners, confirm=True)
                for row in rows
                for feeds in [_feeds(row.account.name, row.account.mask)]
            ]
            # As a member does: confirmed with the warning about names a word apart in view.
            connector.confirm(chosen, near_seen=connector.mapping(chosen).near)
        avery = _seeded(conn, "avery")
        cedar = next(i for i in Registry(conn).items_of(avery.id) if i.institution == "Cedar Card")
        stopped_on = now.astimezone().date() - timedelta(days=CARD_EXPIRED_DAYS_AGO)
        stopped = datetime.combine(stopped_on, time(12)).astimezone()
        Registry(conn).synced(cedar.item_id, at=stopped, history=None)
        Registry(conn).set_state(
            cedar.item_id, ItemState.LOGIN_REQUIRED, problem="ITEM_LOGIN_REQUIRED"
        )
        assert setup.stand_in is not None  # noqa: S101 - the demo always has it
        setup.stand_in.require_login(cedar.item_id)
    finally:
        conn.close()
    if settings.ledger is not None:
        _book_from_connections(settings.ledger, RegisteredConnections(settings.database))


def _book_from_connections(ledger: Path, connections: Connections) -> None:
    """Mark the bank-feed entries on accounts a connection feeds with that connection's
    source, as the import marks every entry it books (`connections.plaid_source`), so each
    connection vouches for its accounts' coverage and a stopped one shows as data that stops.

    The demo's ledger is the household's own, and the import leaves an inherited ledger's
    accounts to it, so its connections bring nothing in themselves: these rows stand for what
    they would have booked."""
    item_of_account = {
        account: connection.item_id
        for connection in connections.all()
        if connection.item_id is not None
        for account in connection.ledger_accounts
    }
    entries = ledger.read_text().split("\n\n")
    for i, entry in enumerate(entries):
        lines = entry.split("\n")
        source = next(
            (n for n, line in enumerate(lines) if line.startswith('  source: "bank-feed:')), None
        )
        fed = [
            item_of_account[account]
            for line in lines[1:]
            if line.startswith("  ") and (account := line.split()[0]) in item_of_account
        ]
        if source is None or not fed:
            continue
        row = lines[source].removeprefix('  source: "bank-feed:').removesuffix('"')
        lines[source] = f'  source: "{plaid_source(fed[0], f"demo-{row}")}"'
        entries[i] = "\n".join(lines)
    ledger.write_text("\n\n".join(entries))


def _feeds(name: str, mask: str | None) -> str:
    """The ledger account a demo bank's account feeds."""
    return next(
        ledger
        for bank, feeds in DEMO_BANKS
        for account, ledger in zip(bank.accounts, feeds, strict=True)
        if (account.name, account.mask) == (name, mask)
    )


def _seeded(conn: sqlite3.Connection, username: str) -> members.Member:
    member = members.find(conn, username)
    if member is None:
        raise LookupError(f"the demo has no member {username}; seed it first")
    return member
