"""Hearth's own books, for a household that starts with nothing but a bank login.

Most of these run the way a new household meets hearth: members and no ledger, a member
connecting a bank and saying where its accounts go through the mapping page, the books those
accounts become, and the file hearth writes them to. The bank has the shape of a real one:
seven accounts, five of them called the same thing. Every value is invented.
"""

import re
import sqlite3
import stat
from collections.abc import Iterable, Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from beancount import loader
from beancount.core.data import Directive, Open, Transaction

from hearth import accounts, auth, books, db, ownbooks, sharing
from hearth.disclosure import Disclosure
from hearth.members import find
from hearth.plaid import booking, feed, opening
from hearth.plaid.client import BankAccount, Institution, SyncPage
from hearth.plaid.client import Transaction as PlaidTransaction
from hearth.plaid.feed import Feed
from hearth.plaid.registry import Registry
from hearth.review.pile import UNRESOLVED
from hearth.sharing import Facet
from tests import household
from tests.conftest import FakeClock, password_of
from tests.firstday import (
    FIRST_DAY,
    PATHS,
    SILENT,
    ZERO,
    FirstDay,
    first_day,
    opens,
    shown,
    until_booked,
    until_transacted,
    written,
)


def connected(entries: Iterable[Directive]) -> dict[str, Open]:
    """The opens of the accounts a member connected, which say which account they are."""
    return {a: o for a, o in opens(entries).items() if "institution" in o.meta}


# A household with nothing yet.


def test_a_household_with_no_ledger_has_books_and_a_way_in(fresh: FirstDay) -> None:
    status = fresh.bookkeeper.status
    assert status.failure is None
    assert status.books is not None
    assert status.books.entries == ()

    client = fresh.signed_in("blake")
    for page in ("/", "/net-worth"):
        response = client.get(page)
        assert response.status_code == 200
        assert "HEARTH_LEDGER" not in response.text
        assert "No figures yet" not in response.text
        assert 'href="/connect"' in response.text
        # Nothing is connected, so there is no total, not a total of zero.
        assert "$0" not in response.text
    assert "Nothing is connected yet" in client.get("/").text
    assert "Nothing to add up yet" in client.get("/net-worth").text


def test_the_empty_books_are_written_and_load_cleanly(fresh: FirstDay) -> None:
    entries, errors = written(fresh.settings)

    assert errors == []
    assert entries == []
    assert "DO NOT EDIT" in fresh.settings.books_file.read_text()


# The accounts a member connects become the chart of accounts.


def test_each_confirmed_account_opens_on_the_first_day_its_connection_asked_for(
    fresh: FirstDay,
) -> None:
    fresh.plaid.slow_history = True
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), PATHS)

    found = until_booked(fresh, PATHS["7717"])

    assert {a: o.date for a, o in connected(found.entries).items()} == dict.fromkeys(
        PATHS.values(), FIRST_DAY
    )


def test_an_account_opens_in_time_for_what_it_held_before_its_first_transaction(
    fresh: FirstDay,
) -> None:
    """Every account but savings has a transaction on the first day asked for, and what it held
    before that is its opening balance, the day before: each opens on that day instead.
    Savings' first, its interest, is weeks later, so it opens on the first day asked for."""
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), PATHS)

    found = until_transacted(fresh, PATHS["7717"])

    before = FIRST_DAY - timedelta(days=1)
    expected = dict.fromkeys(PATHS.values(), before) | {PATHS["7717"]: FIRST_DAY}
    assert {a: o.date for a, o in connected(found.entries).items()} == expected
    entries, errors = written(fresh.settings)
    assert errors == []
    assert {a: o.date for a, o in connected(entries).items()} == expected
    # The categories the transactions are proposed against open alongside, on the same day.
    others = {a: o.date for a, o in opens(entries).items() if a not in PATHS.values()}
    assert others
    assert set(others.values()) == {before}
    assert all(a.split(":")[0] in {"Expenses", "Income", "Equity"} for a in others)
    wren = opens(entries)[PATHS["1111"]]
    assert (wren.meta["name"], wren.meta["institution"], wren.meta["mask"]) == (
        "Card 1111",
        "Willowmere Trust",
        "1111",
    )


def test_an_account_nobody_has_confirmed_is_not_in_the_books(fresh: FirstDay) -> None:
    fresh.link("avery", "ins_willowmere")
    chosen = {mask: path for mask, path in PATHS.items() if mask != "5515"}
    fresh.map(fresh.signed_in("avery"), chosen)

    found = until_booked(fresh, PATHS["7717"])

    assert set(connected(found.entries)) == set(chosen.values())
    assert set(connected(written(fresh.settings)[0])) == set(chosen.values())
    assert not [
        e
        for e in found.entries
        if isinstance(e, Transaction) and any(p.account == PATHS["5515"] for p in e.postings)
    ]


def test_the_household_pages_show_the_books_once_an_account_is_in_them(fresh: FirstDay) -> None:
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), PATHS)
    until_booked(fresh, PATHS["7717"])

    page = fresh.signed_in("blake").get("/").text

    assert "Nothing is connected yet" not in page
    assert "Household net worth" in page


def test_ownership_never_reaches_the_file(fresh: FirstDay) -> None:
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), PATHS)
    until_booked(fresh, PATHS["7717"])

    entries = written(fresh.settings)[0]
    for entry in connected(entries).values():
        assert set(entry.meta) - {"filename", "lineno"} == {"name", "institution", "mask"}
    for entry in opens(entries).values():
        assert set(entry.meta) - {"filename", "lineno"} <= {"name", "institution", "mask"}
    booked = [e for e in entries if isinstance(e, Transaction) and not opening.anchors(e)]
    assert booked
    for row in booked:
        # Why a row is suggested as it is names no member: only a category, or the bank's.
        assert set(row.meta) - {"filename", "lineno", "__tolerances__"} <= {
            "review-id",
            "review-group",
            "review-why",
            "review-reason",
            "review-bank",
            "review-plain",
            "source",
        }
        assert all(set(p.meta or {}) <= {"filename", "lineno", "source"} for p in row.postings)
    # An opening balance says where it came from: which balance, whose, and when; not whose.
    anchors = [e for e in entries if opening.anchors(e)]
    assert len(anchors) == 2 * len(PATHS)
    for anchor in anchors:
        assert set(anchor.meta) - {"filename", "lineno", "__tolerances__"} <= {
            "balance",
            "reported-by",
            "reported-at",
            "source",
        }


# Deriving the opens, against the app database and the feed directly.


@pytest.fixture
def stores(tmp_path: Path) -> Iterator[tuple[sqlite3.Connection, Feed]]:
    database, fed = tmp_path / "hearth.sqlite3", tmp_path / "feed.sqlite3"
    db.initialise(database)
    feed.initialise(fed)
    conn = db.connect(database)
    member, _ = auth.create_member(
        conn,
        actor=None,
        username="avery",
        display_name="Avery",
        is_admin=True,
        password=password_of("avery"),
        now=household.NOW,
    )
    conn.execute(
        "INSERT INTO plaid_credentials (scope, member_id, registered_by, environment,"
        " item_limit, used_elsewhere, saved_at) VALUES ('own', ?, ?, 'demo', 10, 0, ?)",
        (member.id, member.id, household.NOW.isoformat()),
    )
    try:
        with Feed.open(fed) as store:
            yield conn, store
    finally:
        conn.close()


def _connected(
    conn: sqlite3.Connection,
    item_id: str,
    account_id: str,
    *,
    linked: datetime,
    feeds: str,
    mask: str = "7717",
) -> None:
    """An Item reaching one account, confirmed to feed the ledger account `feeds`."""
    registry = Registry(conn)
    member = find(conn, "avery")
    assert member is not None
    registry.add(
        item_id,
        member=member.id,
        credentials="own",
        institution=Institution("ins_willowmere", "Willowmere Trust"),
        fallback_name="",
        days_requested=730,
        now=linked,
    )
    registry.record_accounts(
        item_id,
        [BankAccount(account_id, "SAVINGS", None, mask, "depository", "savings", None, None, None)],
    )
    account = next((a for a in accounts.all_accounts(conn) if a.ledger_account == feeds), None)
    if account is None:
        account = accounts.register(conn, ledger_account=feeds, name="Savings", owners=[member.id])
    registry.confirm(account_id, account.id, by=member.id, now=linked)


def _sent(store: Feed, item_id: str, account_id: str, day: date) -> None:
    run = store.start_run(item_id, now=household.NOW)
    assert run is not None
    found = PlaidTransaction(
        f"t-{day}", account_id, day, None, Decimal("12.00"), "USD", "Sorrel Bakery", None, None,
        None, pending=False, pending_transaction_id=None,
    )  # fmt: skip
    store.apply(run, item_id, SyncPage((found,), (), (), "next", False, None), now=household.NOW)
    store.finish(run, feed.Outcome.COMPLETE, now=household.NOW)


def test_an_account_opens_before_an_older_transaction_the_bank_sent(
    stores: tuple[sqlite3.Connection, Feed],
) -> None:
    conn, store = stores
    _connected(conn, "item-a", "acct-a", linked=household.NOW, feeds="Assets:Avery:Savings")
    older = FIRST_DAY - timedelta(days=3)
    _sent(store, "item-a", "acct-a", older)

    assert {a: o.date for a, o in opens(ownbooks.derive(conn, store).entries).items()} == {
        "Assets:Avery:Savings": older,
        UNRESOLVED: older,
    }


def test_an_account_fed_by_two_connections_opens_once_at_the_earliest(
    stores: tuple[sqlite3.Connection, Feed],
) -> None:
    conn, store = stores
    earlier = household.NOW - timedelta(days=200)
    # Either connection may be met first; the earlier one comes first here, so a later one
    # met after it cannot pass for the earliest. A card reissued under new digits is two
    # accounts to Plaid, and one to the household.
    _connected(conn, "item-a", "acct-a", linked=earlier, feeds="Assets:Avery:Savings")
    _connected(
        conn, "item-b", "acct-b", linked=household.NOW, feeds="Assets:Avery:Savings", mask="7718"
    )

    assert [(a, o.date) for a, o in opens(ownbooks.derive(conn, store).entries).items()] == [
        ("Assets:Avery:Savings", FIRST_DAY - timedelta(days=200))
    ]


def test_a_bank_connected_again_books_nothing_until_one_connection_reaches_it(
    stores: tuple[sqlite3.Connection, Feed],
) -> None:
    conn, store = stores
    _connected(conn, "item-a", "acct-a", linked=household.NOW, feeds="Assets:Avery:Savings")
    _connected(conn, "item-b", "acct-b", linked=household.NOW, feeds="Assets:Avery:Savings")
    _sent(store, "item-a", "acct-a", FIRST_DAY)

    with pytest.raises(booking.Refused, match="two working connections"):
        ownbooks.derive(conn, store)


def test_an_account_an_inherited_ledger_opens_is_left_to_it(
    stores: tuple[sqlite3.Connection, Feed],
) -> None:
    conn, store = stores
    _connected(conn, "item-a", "acct-a", linked=household.NOW, feeds="Assets:Avery:Savings")

    assert ownbooks.derive(conn, store, opened={"Assets:Avery:Savings"}).entries == []


def test_books_hearth_cannot_load_cleanly_are_refused_not_served(fresh: FirstDay) -> None:
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), {"7717": PATHS["7717"]})
    until_booked(fresh, PATHS["7717"])
    conn = fresh.connect()
    try:
        # Not a name the mapping page would take, as a damaged database might hold one.
        conn.execute("UPDATE accounts SET ledger_account = 'Assets:avery savings'")
    finally:
        conn.close()

    fresh.bookkeeper.recompute()

    failure = fresh.bookkeeper.status.failure
    assert failure is not None
    assert failure.summary == "Hearth's own books have 1 error it will not serve figures past."


# Inheriting a ledger of the household's own.


INHERITED = """\
option "operating_currency" "USD"
2026-01-01 open Assets:Avery:Willowmere:Checking USD
2026-01-01 open Expenses:Food USD
2026-01-05 * "Sorrel Bakery" "Bread"
  Expenses:Food  12.50 USD
  Assets:Avery:Willowmere:Checking
"""


def test_an_inherited_ledger_is_included_beneath_hearths_own_and_never_written(
    tmp_path: Path, clock: FakeClock
) -> None:
    folder = tmp_path / "ledger"
    folder.mkdir()
    ledger = folder / "household.beancount"
    ledger.write_text(INHERITED)
    fresh = first_day(tmp_path, clock, inherits=ledger)
    conn = fresh.connect()
    try:
        accounts.register(
            conn,
            ledger_account="Assets:Avery:Willowmere:Checking",
            name="Checking",
            owners=[fresh.member_ids["avery"]],
        )
    finally:
        conn.close()
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), {"7717": PATHS["7717"]})

    found = until_transacted(fresh, PATHS["7717"])

    entries, errors = written(fresh.settings)
    assert errors == []
    # The file alone is the whole books: the inherited ledger, and hearth's own over it.
    assert opens(entries).keys() == opens(found.entries).keys()
    assert opens(entries)[PATHS["7717"]].date == FIRST_DAY
    assert opens(entries)["Assets:Avery:Willowmere:Checking"].date == date(2026, 1, 1)
    inherited = [e for e in entries if isinstance(e, Transaction) and e.payee == "Sorrel Bakery"]
    assert len(inherited) == 1
    assert {p.account for e in entries if isinstance(e, Transaction) for p in e.postings} >= {
        PATHS["7717"]
    }
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


# The file is an output, never an input.


def test_the_file_says_it_is_written_by_hearth_and_is_its_owners_alone(fresh: FirstDay) -> None:
    text = fresh.settings.books_file.read_text()

    assert text.startswith(ownbooks.HEADER)
    assert stat.S_IMODE(fresh.settings.books_file.stat().st_mode) == 0o600


def test_an_edit_to_the_file_changes_nothing_hearth_shows_and_is_kept_aside(
    fresh: FirstDay,
) -> None:
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), {"7717": PATHS["7717"]})
    before = until_booked(fresh, PATHS["7717"])
    path = fresh.settings.books_file
    edited = path.read_text() + (
        "\n2026-10-01 open Expenses:Food\n"
        f'2026-10-02 * "Sorrel Bakery"\n  Expenses:Food  999.00 USD\n  {PATHS["7717"]}\n'
    )
    path.write_text(edited)

    fresh.bookkeeper.recompute()

    after = fresh.books()
    assert after.entries == before.entries
    assert after.today == before.today
    (kept,) = [p for p in path.parent.iterdir() if p.name.startswith("books.edited-")]
    assert kept.read_text() == edited
    assert path.read_text() != edited
    assert written(fresh.settings)[1] == []


def test_a_file_as_hearth_wrote_it_is_left_alone(fresh: FirstDay) -> None:
    path = fresh.settings.books_file
    stamp = path.stat().st_mtime_ns

    fresh.bookkeeper.recompute()

    assert path.stat().st_mtime_ns == stamp
    assert [p.name for p in path.parent.iterdir() if p.name.startswith("books.")] == [
        "books.beancount"
    ]


def test_new_books_replace_the_old_file_without_keeping_a_copy(fresh: FirstDay) -> None:
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), {"7717": PATHS["7717"]})
    until_booked(fresh, PATHS["7717"])

    names = [p.name for p in fresh.settings.data_dir.iterdir() if p.name.startswith("books")]
    assert names == ["books.beancount"]


# What an account holds is its bank's word; what it held before, and how it got there, is not
# known until its history is in.


def test_an_account_with_no_history_yet_holds_what_its_bank_says_and_its_months_stay_unknown(
    fresh: FirstDay,
) -> None:
    fresh.plaid.slow_history = True
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), PATHS)
    found = until_booked(fresh, PATHS["7717"])

    assert found.awaiting == frozenset(PATHS.values())
    assert found.unanchored == frozenset()
    avery = fresh.signed_in("avery")
    pages = {path: shown(avery.get(path).text) for path in ("/", "/you", "/net-worth")}
    for where, page in pages.items():
        assert "no balance in hearth yet" not in page, where
        # What the bank says the seven accounts hold, cards owed.
        assert "$12,771" in page, where
        zeros = [m.start() for m in re.finditer(ZERO, page)]
        assert zeros == [], (where, page[max(0, zeros[0] - 200) : zeros[0] + 80])
    # How it moved is not known: no month reads as whole, and no month end is drawn, only today.
    assert "History incomplete" in pages["/you"]
    conn = fresh.connect()
    try:
        member = find(conn, "avery")
        assert member is not None
        household = Disclosure.load(conn, member, fresh.bookkeeper.status).household()
    finally:
        conn.close()
    assert [point.day for point in household.history] == [found.as_of]


def test_an_account_whose_bank_says_nothing_of_its_balance_is_not_known_never_zero(
    tmp_path: Path, clock: FakeClock
) -> None:
    fresh = first_day(tmp_path, clock, institutions=(SILENT,))
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), PATHS)
    found = until_transacted(fresh, PATHS["7717"])

    assert found.unanchored == frozenset(PATHS.values())
    assert set(found.unknown.values()) == {opening.Unknown.NO_BALANCE}
    avery, blake = fresh.signed_in("avery"), fresh.signed_in("blake")
    conn = fresh.connect()
    try:
        card = next(a.id for a in accounts.all_accounts(conn) if a.ledger_account == PATHS["1111"])
    finally:
        conn.close()
    pages = {
        "avery /accounts": avery.get(f"/accounts/{card}").text,
        "avery /": avery.get("/").text,
        "avery /you": avery.get("/you").text,
        "avery /net-worth": avery.get("/net-worth").text,
        "blake /": blake.get("/").text,
        "blake /members/avery": blake.get("/members/avery").text,
        "blake /net-worth": blake.get("/net-worth").text,
    }
    for where, page in pages.items():
        assert "Not known yet" in page, where
        # Two years of rows added up are what moved, never a balance, nor a zero.
        zeros = [m.start() for m in re.finditer(ZERO, shown(page))]
        assert zeros == [], (where, shown(page)[max(0, zeros[0] - 200) : zeros[0] + 80])
    assert "Its bank has not said what it holds." in shown(pages["avery /accounts"])
    assert "The line appears once hearth has the history" in pages["avery /"]
    # Whoever sees only the total learns it is not known, not which account holds it back.
    for where in ("blake /", "blake /members/avery", "blake /net-worth"):
        assert "Card 1111" not in pages[where]
        assert PATHS["1111"] not in pages[where]


def test_only_an_accounts_owners_are_told_why_its_balance_is_not_known(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Why is what its connection did, which is its holder's alone; a member Avery shares
    balances with sees only that the balance is not known."""
    fresh = first_day(tmp_path, clock, institutions=(SILENT,))
    conn = fresh.connect()
    try:
        sharing.choose(
            conn, fresh.member_ids["avery"], Facet.ACCOUNT_BALANCES, shared=True, now=clock()
        )
    finally:
        conn.close()
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), PATHS)
    until_transacted(fresh, PATHS["7717"])
    conn = fresh.connect()
    try:
        card = next(a.id for a in accounts.all_accounts(conn) if a.ledger_account == PATHS["1111"])
    finally:
        conn.close()

    page = shown(fresh.signed_in("blake").get(f"/accounts/{card}").text)

    assert "Not known yet." in page
    assert "Its bank has not said" not in page
    assert "This account is connected, but its balance is not in hearth yet." in page


def test_a_total_only_member_never_gives_away_how_many_accounts_they_connected(
    tmp_path: Path, clock: FakeClock
) -> None:
    fresh = first_day(tmp_path, clock, institutions=(SILENT,))
    conn = fresh.connect()
    try:
        sharing.choose(
            conn,
            fresh.member_ids["avery"],
            Facet.MONTHLY_SPENDING_TOTAL,
            shared=False,
            now=fresh.clock(),
        )
    finally:
        conn.close()
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), PATHS)
    until_transacted(fresh, PATHS["7717"])
    avery, blake = fresh.signed_in("avery"), fresh.signed_in("blake")

    assert "7 accounts have no balance" in shown(avery.get("/you").text)
    for path in ("/", "/net-worth", "/members/avery"):
        page = shown(blake.get(path).text)
        assert "Not known yet" in page, path
        assert "Some accounts have no balance" in page, path
        counts = re.findall(r"\b\d+\s+(?:connected\s+)?accounts?\b", page)
        assert counts == [], (path, counts)


def test_an_account_is_known_once_the_books_hold_anything_for_it() -> None:
    opened = ownbooks.Opened(PATHS["6616"], FIRST_DAY, "Checking", "Willowmere Trust", "6616")
    others, errors, _ = loader.load_string(
        f"""
2024-10-16 open Equity:Opening
2024-10-16 open {PATHS["6616"]}
2024-10-20 * "Opening"
  {PATHS["6616"]}  100.00 USD
  Equity:Opening
"""
    )
    assert errors == []
    others = [e for e in others if not isinstance(e, Open) or e.account != PATHS["6616"]]
    saved = ownbooks.Opened(PATHS["7717"], FIRST_DAY, "Savings", "Willowmere Trust", "7717")

    moved = books.awaiting([opened.entry(1), *others], date(2026, 10, 15))
    still = books.awaiting([opened.entry(1), saved.entry(2)], date(2026, 10, 15))

    assert moved == frozenset()
    assert still == {PATHS["6616"], PATHS["7717"]}


def test_an_inherited_account_nothing_has_moved_keeps_its_meaning(
    tmp_path: Path, clock: FakeClock
) -> None:
    ledger = tmp_path / "household.beancount"
    ledger.write_text(INHERITED + "2026-01-01 open Assets:Avery:Willowmere:Spare USD\n")

    fresh = first_day(tmp_path, clock, inherits=ledger)

    assert fresh.books().awaiting == frozenset()


# The warning about names a word apart says only what is true.


def test_two_new_names_a_word_apart_are_not_called_real_accounts(fresh: FirstDay) -> None:
    fresh.link("avery", "ins_willowmere")

    page = fresh.signed_in("avery").get("/connect/map").text

    assert 'id="near"' in page
    assert "both are real accounts" not in page
    assert "check each account is going where you mean" in page


def test_a_new_name_a_word_from_a_real_account_keeps_the_balance_warning(
    tmp_path: Path, clock: FakeClock
) -> None:
    ledger = tmp_path / "household.beancount"
    ledger.write_text(INHERITED)
    fresh = first_day(tmp_path, clock, inherits=ledger)
    conn = fresh.connect()
    try:
        accounts.register(
            conn,
            ledger_account="Assets:Avery:Willowmere:Checking",
            name="Checking",
            owners=[fresh.member_ids["avery"]],
        )
    finally:
        conn.close()
    fresh.link("avery", "ins_willowmere")

    page = fresh.signed_in("avery").get("/connect/map").text

    assert "both are real accounts" in page


SEPTEMBER_STARTS_LATE = """\
option "operating_currency" "USD"
2026-09-01 open Assets:Avery:Willowmere:Checking USD
2026-09-01 open Income:Salary USD
2026-09-01 open Expenses:Food USD
2026-09-25 * "Pay"
  Assets:Avery:Willowmere:Checking  1000.00 USD
  Income:Salary
2026-09-26 * "Sorrel Bakery"
  Expenses:Food  100.00 USD
  Assets:Avery:Willowmere:Checking
2026-10-02 * "Sorrel Bakery"
  Expenses:Food  10.00 USD
  Assets:Avery:Willowmere:Checking
"""
"""A checking account whose history starts on Sep 25, weeks into September: the month's
income and spending are only what the ledger holds of it, not the month."""


def test_a_month_not_known_in_full_has_no_savings_rate(tmp_path: Path, clock: FakeClock) -> None:
    ledger = tmp_path / "household.beancount"
    ledger.write_text(SEPTEMBER_STARTS_LATE)
    fresh = first_day(tmp_path, clock, inherits=ledger)
    conn = fresh.connect()
    try:
        accounts.register(
            conn,
            ledger_account="Assets:Avery:Willowmere:Checking",
            name="Checking",
            owners=[fresh.member_ids["avery"]],
        )
    finally:
        conn.close()
    fresh.bookkeeper.recompute()

    page = shown(fresh.signed_in("avery").get("/").text)

    saved = page[page.index("Left over in September") :][:400]
    assert "%" not in saved
    assert "Not known yet: hearth does not have all of September" in saved
