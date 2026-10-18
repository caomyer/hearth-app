"""An invented household for trying hearth out. Every name, account and figure is made up.

The demo writes a real beancount ledger and real stored prices, so it runs through exactly the
code a household's own books do: loading, balance checks, valuation, coverage and review. It
deliberately includes what real books have: history that changes source part way through the
year, a fund whose price has gone stale, and transactions waiting for review.
"""

import calendar
import random
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from hearth import accounts, auth, members, passwords
from hearth.connections import Connection, KnownConnections, State
from hearth.members import MemberId
from hearth.review.ids import review_id
from hearth.review.pile import UNRESOLVED
from hearth.valuation.pricestore import PriceStore
from hearth.valuation.quotes import Quote

PRICE_SOURCE = "demo"
FEED_STARTS = date(2026, 6, 8)
"""Statement downloads cover the year until here; a bank feed covers it from here on."""

HISTORY_DAYS = 430
SET_ASIDE_DAYS = 7
"""The last week's transactions wait in the review pile, as the latest import would leave them."""

CARD_EXPIRED_DAYS_AGO = 5
"""Avery's card connection stopped updating this long ago, so the demo shows that state."""


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
    ("Assets:Blake:Checking", "Dining", ("Greenleaf Bistro",), 44, 4),
    ("Assets:Blake:Checking", "Health", ("Riverside Clinic",), 95, 1),
    ("Assets:Blake:Checking", "Shopping", ("Paper Lantern Goods",), 60, 2),
    ("Assets:Casey:Checking", "Education", ("Campus Bookstore",), 48, 1),
    ("Liabilities:Casey:Card", "Dining", ("Hilltop Cafe",), 14, 4),
    ("Assets:Joint:Checking", "Groceries", ("Greenleaf Market",), 118, 4),
    ("Assets:Joint:Checking", "Utilities", ("City Power & Light",), 160, 1),
)


def seed(conn: sqlite3.Connection, ledger: Path, *, now: datetime) -> dict[str, str]:
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
    today = now.date()
    prices = _prices(today)
    store = PriceStore(conn)
    for quote in _quotes(prices, today):
        store.record(quote)
    ledger.write_text(_ledger(today, prices))
    return chosen


def _prices(today: date) -> dict[str, dict[date, Decimal]]:
    """A gentle random walk per fund over the demo's history, one close per weekday."""
    rng = random.Random(7)  # noqa: S311 - invented figures, not security
    walks: dict[str, dict[date, Decimal]] = {}
    for fund, start in FUNDS.items():
        price = start
        closes = {}
        for day in _days(today - timedelta(days=HISTORY_DAYS), today):
            if day.weekday() < 5:
                price *= Decimal(1 + rng.gauss(0.0004, 0.009)).quantize(Decimal("0.0001"))
                closes[day] = price.quantize(Decimal("0.01"))
        walks[fund] = closes
    return walks


def _quotes(prices: dict[str, dict[date, Decimal]], today: date) -> Iterator[Quote]:
    fetched = datetime.combine(today, time(6, 0), tzinfo=UTC)
    stale_after = today - timedelta(days=6)
    for fund, closes in prices.items():
        for day, price in closes.items():
            # The international fund's source stopped answering a few sessions ago.
            if fund == "INTX" and day > stale_after:
                continue
            if day >= today:
                continue
            yield Quote(fund, day, price, "USD", PRICE_SOURCE, f"demo:{fund}", fetched)


def _ledger(today: date, prices: dict[str, dict[date, Decimal]]) -> str:
    start = today - timedelta(days=HISTORY_DAYS)
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
    writer = _Writer(today, prices)
    out += writer.opening(start)
    for day in _days(start + timedelta(days=1), today):
        out += writer.day(day)
    return "\n".join(out) + "\n"


class _Writer:
    """Writes the household's transactions day by day, keeping card balances to settle them."""

    def __init__(self, today: date, prices: dict[str, dict[date, Decimal]]) -> None:
        self._today = today
        self._prices = prices
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
                lines += self._entry(
                    day,
                    "Transfer to joint checking",
                    [("Assets:Joint:Checking", f"{amount} USD"), (payer, f"-{amount} USD")],
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
                lines += self._entry(
                    day, payee, [(account, f"{amount} USD"), (f"Income:{income}", "")]
                )
        if day.day == 25:
            for card, payer in (
                ("Liabilities:Avery:Card", "Assets:Avery:Checking"),
                ("Liabilities:Casey:Card", "Assets:Casey:Checking"),
            ):
                owed = self._owed[card]
                if owed:
                    lines += self._entry(
                        day, "Card payment", [(card, f"{owed} USD"), (payer, f"-{owed} USD")]
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
        if (self._today - day).days < SET_ASIDE_DAYS:
            return self._set_aside(day, payee, category, payer, amount)
        return self._entry(
            day, payee, [(f"Expenses:{category}", f"{amount} USD"), (payer, f"-{amount} USD")]
        )

    def _set_aside(
        self, day: date, payee: str, category: str, payer: str, amount: Decimal
    ) -> list[str]:
        """A row the import could not book on its own: flagged, with a suggestion to check."""
        unsure = self._rng.random() < 0.3
        proposal = UNRESOLVED if unsure else f"Expenses:{category}"
        meta = {
            "review-id": review_id(payer, day, -amount, payee),
            "review-group": "Unsure" if unsure else category,
        }
        return self._entry(
            day, payee, [(payer, f"-{amount} USD"), (proposal, f"{amount} USD")], "!", meta
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
        lines += [f'  {key}: "{value}"' for key, value in (meta or {}).items()]
        lines += [f"  {account}  {amount}".rstrip() for account, amount in postings]
        return [*lines, ""]


def _days(start: date, end: date) -> Iterator[date]:
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def connections(conn: sqlite3.Connection, today: date) -> KnownConnections:
    """The demo's connections: Avery's card has expired, everything else is working."""
    avery, blake = _seeded(conn, "avery"), _seeded(conn, "blake")
    stopped = today - timedelta(days=CARD_EXPIRED_DAYS_AGO)
    return KnownConnections(
        {
            avery.id: [
                Connection(
                    avery.id, "Cedar Card", ("Liabilities:Avery:Card",), State.EXPIRED, stopped
                ),
                Connection(
                    avery.id, "Northfield Bank", ("Assets:Avery:Checking",), State.WORKING, today
                ),
            ],
            blake.id: [
                Connection(
                    blake.id,
                    "Harbor Credit Union",
                    ("Assets:Blake:Checking",),
                    State.WORKING,
                    today,
                ),
            ],
        }
    )


def _seeded(conn: sqlite3.Connection, username: str) -> members.Member:
    member = members.find(conn, username)
    if member is None:
        raise LookupError(f"the demo has no member {username}; seed it first")
    return member
