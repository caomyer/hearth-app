"""A household on disk, as ``hearth serve`` leaves one, for the Mac app's end-to-end test.

    HEARTH_FIXTURE_PASSWORD=... uv run python -m tests.native.on_disk <data directory>

Two members, no ledger, and one invented bank Avery connected through the stand-in for Plaid,
by the path a member takes, so ``hearth bridge`` keeps the books from what the bank sent as it
would for a real household. Every name and figure is invented. Each member's password is the
one given, so the test that signs in knows it without it being written anywhere.
"""

import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from hearth import auth, db, members
from hearth.books import Status
from hearth.clock import system_clock
from hearth.config import Settings
from hearth.disclosure import Disclosure
from hearth.plaid import feed
from hearth.plaid.connector import NEW, Mapping
from hearth.plaid.fake import FakeAccount, FakeInstitution, FakePlaid, FakeTransaction
from hearth.plaid.registry import Registry
from hearth.plaid.setup import PlaidSetup
from hearth.vault.secret import Secret
from tests.vault.conftest import MemoryKeyStore

USERNAMES = ("avery", "blake")

GROCERIES = "FOOD_AND_DRINK_GROCERIES"

WAITING = 5
"""How many of Avery's rows wait in Review's queue: everything the bank sent but the transfer."""

ELSEWHERE = 1
"""How many wait to be told where their money went: the transfer to savings nobody connected."""

NET_WORTH = Decimal("21038.25")
"""Checking and savings, less what the card owes: what Avery's overview says."""


def larkspur(today: date) -> FakeInstitution:
    """Avery's bank. What it sent waits in Review: two market rows of one kind, which one
    accept settles, beside rows of a kind each, one Plaid gave no category, and a transfer to
    an account nobody connected, which waits under "Where did it go?"."""
    days = [today - timedelta(days=n) for n in (20, 12, 5, 9)]
    return FakeInstitution(
        "ins_larkspur",
        "Larkspur Savings Bank",
        (
            FakeAccount(
                "chk",
                "Everyday checking",
                "4471",
                "depository",
                "checking",
                Decimal("3250.40"),
                transactions=(
                    FakeTransaction("Cobble Street Market", Decimal("84.12"), days[0], GROCERIES),
                    FakeTransaction("Cobble Street Market", Decimal("31.06"), days[3], GROCERIES),
                    FakeTransaction("Payroll", Decimal("-2400.00"), days[1], "INCOME_WAGES"),
                    FakeTransaction(
                        "Online transfer to SAV 0417",
                        Decimal("250.00"),
                        days[1],
                        "TRANSFER_OUT_ACCOUNT_TRANSFER",
                    ),
                ),
            ),
            FakeAccount(
                "sav",
                "Savings",
                "9035",
                "depository",
                "savings",
                Decimal("18400.00"),
                transactions=(
                    FakeTransaction(
                        "Interest", Decimal("-12.50"), days[2], "INCOME_INTEREST_EARNED"
                    ),
                ),
            ),
            FakeAccount(
                "card",
                "Rewards card",
                "6620",
                "credit",
                "credit card",
                Decimal("612.15"),
                transactions=(FakeTransaction("Quill Bookshop", Decimal("41.90"), days[2]),),
            ),
        ),
    )


LEDGER_ACCOUNTS = {
    "4471": "Assets:Avery:Larkspur:Checking",
    "9035": "Assets:Avery:Larkspur:Savings",
    "6620": "Liabilities:Avery:Larkspur:Card",
}


def build(data_dir: Path, password: str, *, now: datetime) -> Settings:
    """A household in `data_dir`, which holds none yet."""
    settings = Settings.in_directory(data_dir)
    db.initialise(settings.database)
    feed.initialise(settings.feed_database)
    conn = db.connect(settings.database)
    try:
        for username in USERNAMES:
            auth.create_member(
                conn,
                actor=None,
                username=username,
                display_name=username.title(),
                is_admin=username == "avery",
                password=password,
                now=now,
            )
        avery = members.find(conn, "avery")
        assert avery is not None
        stand_in = FakePlaid([larkspur(now.date())], today=now.date, accept_any_keys=True)
        keys = MemoryKeyStore()
        setup = PlaidSetup(stand_in=stand_in, keys=lambda: keys, wait=lambda _seconds: None)
        status = Status(books=None, failure=None, recomputing=False)
        connector = Disclosure.load(conn, avery, status).connector(
            conn,
            database=settings.database,
            feed_path=settings.feed_database,
            data_dir=settings.data_dir,
            setup=setup,
            clock=lambda: now,
            runner=lambda work: work(),
        )
        connector.save_credentials(
            household=False,
            client_id=Secret("fixture-client"),
            secret=Secret("fixture-secret"),
            environment="demo",
            item_limit="10",
            used_elsewhere="0",
        )
        start = connector.start_link(served_at="http://localhost")
        connector.finish_link(Secret(stand_in.public_token(start.link_token, "ins_larkspur")), "")
        chosen = [
            Mapping(
                row.account.account_id,
                NEW,
                str(avery.id),
                confirm=True,
                path=LEDGER_ACCOUNTS[row.account.mask or ""],
                name=row.account.name,
            )
            for row in connector.mapping().rows
        ]
        connector.confirm(chosen, near_seen=connector.mapping(chosen).near)
    finally:
        conn.close()
    return settings


LEDGER_ACCOUNTS_BEFORE: dict[str, str] = {
    "4471": "Assets:Larkspur:Checking",
    "9035": "Assets:Larkspur:Savings",
    "6620": "Liabilities:Larkspur:Card",
}
"""Where the ledger Avery kept before hearth books each account, by its last digits: other
names than hearth's own, so matching moves each onto the ledger's."""

HOLIDAY = "Assets:Larkspur:Holiday"
"""The savings the transfer went to, which the ledger keeps and nobody has claimed."""

AGAINST: dict[str, str] = {
    "Cobble Street Market": "Expenses:Food:Groceries",
    "Payroll": "Income:Avery:Salary",
    "Online transfer to SAV 0417": HOLIDAY,
    "Interest": "Income:Avery:Interest",
    "Quill Bookshop": "Expenses:Books",
}


def write_ledger(settings: Settings, folder: Path) -> Path:
    """The ledger Avery kept before hearth, in `folder`, written from what the bank sent: each
    row as an importer of the household's own keeps it, with its bare `plaid-id`, from an
    opening balance on the day before its first, so it ends where the bank's balance stands
    today. It runs a plugin that is not beancount's own and has a `custom` directive, as a real
    one may, and includes a file of accounts. Returns the file it is opened from."""
    conn = db.connect(settings.database)
    try:
        linked = Registry(conn).confirmed()
    finally:
        conn.close()
    banks = {account.mask: account for account in linked}
    opens = sorted({*LEDGER_ACCOUNTS_BEFORE.values(), *AGAINST.values(), "Equity:Opening"})
    accounts = "".join(f"2020-01-01 open {name} USD\n" for name in opens)
    text = (
        'option "title" "Avery\'s books"\noption "operating_currency" "USD"\n'
        'plugin "zq_tally"\ninclude "accounts.beancount"\n\n'
        '2020-01-01 custom "fava-option" "language" "en"\n\n'
    )
    reported = {
        account.mask: account.current or Decimal(0) for account in larkspur(date.today()).accounts
    }
    with feed.Feed.open(settings.feed_database) as fed:
        for mask, name in LEDGER_ACCOUNTS_BEFORE.items():
            rows = [r for r in fed.transactions(banks[mask].account_id) if not r.pending]
            moved = sum((-r.amount for r in rows), Decimal(0))
            # A card's balance is what it owes: the ledger holds it below zero.
            held = -reported[mask] if name.startswith("Liabilities") else reported[mask]
            first = min(r.day for r in rows)
            text += f"{first - timedelta(days=2)} pad {name} Equity:Opening\n"
            text += f"{first - timedelta(days=1)} balance {name}  {held - moved:.2f} USD\n\n"
            for row in rows:
                text += (
                    f'{row.day} * "{row.name}" ""\n  plaid-id: "{row.transaction_id}"\n'
                    f"  {name}  {-row.amount:.2f} USD\n  {AGAINST[row.name or '']}\n\n"
                )
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "accounts.beancount").write_text(accounts)
    (folder / "household.beancount").write_text(text)
    return folder / "household.beancount"


if __name__ == "__main__":
    made = build(Path(sys.argv[1]), os.environ["HEARTH_FIXTURE_PASSWORD"], now=system_clock())
    if len(sys.argv) > 2 and sys.argv[2] == "--ledger":
        print(write_ledger(made, Path(sys.argv[3])))
