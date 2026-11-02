"""A household on its first day, for the tests: members, no ledger, and an invented bank to
connect. The bank has the shape of a real one: seven accounts, five of them called the same
thing. Every value is invented."""

import re
import sqlite3
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from html import unescape
from pathlib import Path

from beancount import loader
from beancount.core.data import BeancountError, Directive, Open, Transaction
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hearth import auth, books, db, sessions
from hearth.app import create_app
from hearth.books import Bookkeeper
from hearth.config import Settings
from hearth.disclosure import Disclosure, rulings_in
from hearth.members import MemberId, find
from hearth.plaid.connector import NEW
from hearth.plaid.fake import FakeAccount, FakeInstitution, FakePlaid
from hearth.plaid.registry import Registry
from hearth.plaid.setup import PlaidSetup
from hearth.vault.secret import Secret
from hearth.web.deps import SESSION_COOKIE
from tests.conftest import FakeClock, SourceNotSetUp, Waits, password_of, run_now
from tests.vault.conftest import MemoryKeyStore

CARD = "CREDIT CARD"
WILLOWMERE = FakeInstitution(
    "ins_willowmere",
    "Willowmere Trust",
    (
        *(
            FakeAccount(f"c{n}", CARD, f"{n}{n}1{n}", "credit", "credit card", Decimal(f"{n}1.75"))
            for n in range(1, 6)
        ),
        FakeAccount("chk", "TOTAL CHECKING", "6616", "depository", "checking", Decimal("4130")),
        FakeAccount("sav", "SAVINGS", "7717", "depository", "savings", Decimal("8800")),
    ),
)
"""Seven accounts from one invented bank: five cards it calls the same, told apart by mask."""

SILENT = replace(WILLOWMERE, accounts=tuple(replace(a, current=None) for a in WILLOWMERE.accounts))
"""The same bank, saying nothing of what any account holds, as Plaid may (its `current` is
null): no balance hearth shows can come from it."""

PATHS = {
    "1111": "Liabilities:Avery:Willowmere:Wren",
    "2212": "Liabilities:Avery:Willowmere:Finch",
    "3313": "Liabilities:Avery:Willowmere:Lark",
    "4414": "Liabilities:Avery:Willowmere:Robin",
    "5515": "Liabilities:Avery:Willowmere:Swift",
    "6616": "Assets:Avery:Willowmere:Checking",
    "7717": "Assets:Avery:Willowmere:Savings",
}
"""Where the member says each account goes, by its last digits."""

ZERO = r"\$\s*0(?:\.00)?(?![.,]?\d)"
"""A sum of zero as a page states it, "$0" or "$0.00", and not the start of "$0.50"."""

FIRST_DAY = date(2024, 10, 16)
"""The first day of the 730 days of history asked for on Oct 15, 2026 (`household.NOW`)."""


@dataclass
class FirstDay:
    """A running hearth for a household on its first day: members, and nothing else."""

    app: FastAPI
    settings: Settings
    clock: FakeClock
    bookkeeper: Bookkeeper
    plaid: FakePlaid
    setup: PlaidSetup
    member_ids: dict[str, MemberId]

    def connect(self) -> sqlite3.Connection:
        return db.connect(self.settings.database)

    def anonymous(self) -> TestClient:
        return TestClient(self.app, follow_redirects=False)

    def signed_in(self, username: str) -> TestClient:
        client = self.anonymous()
        response = client.post(
            "/sign-in", data={"username": username, "password": password_of(username)}
        )
        assert response.status_code == 303, response.text
        return client

    def csrf(self, client: TestClient) -> str:
        signer = sessions.CookieSigner(self.settings.signing_key)
        token = signer.unsign(client.cookies[SESSION_COOKIE])
        assert token is not None
        return signer.csrf_token(token)

    def link(self, username: str, institution: str) -> str:
        """Connect `username` to an invented bank through the stand-in, as Link would."""
        conn = self.connect()
        try:
            member = find(conn, username)
            assert member is not None
            connector = Disclosure.load(conn, member, self.bookkeeper.status).connector(
                conn,
                database=self.settings.database,
                feed_path=self.settings.feed_database,
                data_dir=self.settings.data_dir,
                setup=self.setup,
                clock=self.clock,
                runner=run_now,
            )
            client_id = f"client-{username}"
            self.plaid.register_client(client_id, f"secret-{username}")
            connector.save_credentials(
                household=False,
                client_id=Secret(client_id),
                secret=Secret(f"secret-{username}"),
                environment="demo",
                item_limit="10",
                used_elsewhere="0",
            )
            start = connector.start_link(served_at="http://testserver")
            token = self.plaid.public_token(start.link_token, institution)
            return connector.finish_link(Secret(token), "")
        finally:
            conn.close()

    def map(self, client: TestClient, paths: dict[str, str], *, owner: str = "avery") -> None:
        """Say where each account goes on the mapping page, by its last digits, confirming
        again with the page's warning about names a word apart in view, as a member does.
        `client` is signed in as `owner`, whose accounts they all become."""
        conn = self.connect()
        try:
            found = [
                a
                for item in Registry(conn).all_items()
                for a in Registry(conn).accounts_of(item.item_id)
            ]
        finally:
            conn.close()
        ids = {a.mask: a.account_id for a in found}
        data: dict[str, str | list[str]] = {
            "csrf": self.csrf(client),
            "account": [ids[mask] for mask in paths],
            "near": [],
        }
        for mask, path in paths.items():
            account_id = ids[mask]
            data |= {
                f"target-{account_id}": NEW,
                f"path-{account_id}": path,
                f"name-{account_id}": f"Card {mask}",
                f"owners-{account_id}": str(self.member_ids[owner]),
                f"confirm-{account_id}": "1",
            }
        response = client.post("/connect/map", data=data)
        if response.status_code == 400:
            data["near"] = [
                unescape(v)
                for v in re.findall(
                    r'<input type="hidden" name="near" value="([^"]+)">', response.text
                )
            ]
            response = client.post("/connect/map", data=data)
        assert response.status_code == 303, response.text

    def books(self) -> books.Books:
        found = self.bookkeeper.status.books
        assert found is not None, self.bookkeeper.status.failure
        return found


def first_day(
    tmp_path: Path,
    clock: FakeClock,
    *,
    inherits: Path | None = None,
    institutions: Sequence[FakeInstitution] = (WILLOWMERE,),
) -> FirstDay:
    settings = Settings.in_directory(tmp_path / "data", ledger=inherits)
    db.initialise(settings.database)
    conn = db.connect(settings.database)
    member_ids = {}
    try:
        for username in ("avery", "blake"):
            member, _ = auth.create_member(
                conn,
                actor=None,
                username=username,
                display_name=username.title(),
                is_admin=username == "avery",
                password=password_of(username),
                now=clock(),
            )
            member_ids[username] = member.id
    finally:
        conn.close()
    bookkeeper = Bookkeeper(
        books.from_household(settings, rulings=rulings_in),
        clock,
        standing=lambda: books.standing(settings),
    )
    fake = FakePlaid(institutions, today=lambda: clock().date())
    keys = MemoryKeyStore()
    setup = PlaidSetup(stand_in=fake, keys=lambda: keys, wait=Waits())
    app = create_app(
        settings, bookkeeper, clock, price_source=SourceNotSetUp(), plaid=setup, runner=run_now
    )
    bookkeeper.recompute()
    return FirstDay(app, settings, clock, bookkeeper, fake, setup, member_ids)


def opens(entries: Iterable[Directive]) -> dict[str, Open]:
    return {e.account: e for e in entries if isinstance(e, Open)}


def written(settings: Settings) -> tuple[list[Directive], list[BeancountError]]:
    """The books file as beancount itself loads it: what a person opening it would see."""
    entries, errors, _ = loader.load_file(str(settings.books_file))
    return entries, errors


def until_booked(fresh: FirstDay, account: str) -> books.Books:
    """The books once a recompute started in the background has opened `account`."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        found = fresh.bookkeeper.status.books
        if found is not None and account in opens(found.entries):
            return found
        time.sleep(0.01)
    raise AssertionError(f"{account} never reached the books")


def until_transacted(fresh: FirstDay, account: str) -> books.Books:
    """The books once a recompute started in the background holds transactions on `account`."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        found = fresh.bookkeeper.status.books
        if found is not None and any(
            isinstance(e, Transaction) and any(p.account == account for p in e.postings)
            for e in found.entries
        ):
            return found
        time.sleep(0.01)
    raise AssertionError(f"no transaction on {account} reached the books")


def shown(page: str) -> str:
    """A page's text as a reader sees it, leaving out charts, whose axes start at $0, and the
    tables beside them, which label each month a figure cannot vouch for as incomplete."""
    page = re.sub(r"<svg.*?</svg>|<details.*?</details>", "", page, flags=re.DOTALL)
    return unescape(re.sub(r"<[^>]+>", "", page))
