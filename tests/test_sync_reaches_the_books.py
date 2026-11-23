"""A sync, or a confirmed mapping, is for what it changes in the books: each ends with the books
holding what it brought, or with an honest account of why they do not.

`hearth plaid sync` run by hand recomputes as `hearth serve`'s morning sync does, through the
same path (``hearth.refresh``), and a serving hearth takes in what it brought without waiting
for the morning. A recompute that fails is said to have failed, by the command and by the
pages, and the previous books stay, labelled with their time. Everything here is invented.
"""

import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hearth import books
from hearth.books import Books
from hearth.disclosure import may_learn, rulings_in
from hearth.plaid import cli as plaid_cli
from hearth.plaid.fake import FakeTransaction
from hearth.plaid.feed import Feed
from hearth.plaid.registry import Registry
from hearth.review.ids import plaid_review_id
from hearth.review.rulings import Action, RulingStore, Scope
from tests.conftest import FakeClock
from tests.firstday import PATHS, WILLOWMERE, FirstDay, first_day, shown, until_booked
from tests.test_positions import BROKERAGE, CHECKING, FERNHILL

PAYEE = "Thistlewood Grocer"
"""An invented shop the bank posts a new row from, after the accounts were mapped."""


@pytest.fixture
def mapped(tmp_path: Path, clock: FakeClock) -> FirstDay:
    """A household that connected its bank and confirmed where each account goes, whose books
    hold it, a while before the bank posts anything new."""
    fresh = first_day(tmp_path, clock)
    fresh.link("avery", WILLOWMERE.id)
    fresh.map(fresh.signed_in("avery"), PATHS)
    until_booked(fresh, PATHS["6616"])
    fresh.bookkeeper.recompute(wait=True)
    clock.advance(timedelta(minutes=15))
    return fresh


@pytest.fixture
def watching(mapped: FirstDay) -> Iterator[FirstDay]:
    """The household's `hearth serve`, watching for a change made by another process."""
    stop = mapped.bookkeeper.recompute_on_change(every=0.01)
    try:
        yield mapped
    finally:
        stop.set()


def post(fresh: FirstDay, payee: str = PAYEE, amount: str = "18.40") -> None:
    """The bank posts a new row on checking, and reports the balance it leaves."""
    (item_id,) = fresh.plaid.items()
    fresh.plaid.change(
        item_id,
        "added",
        FakeTransaction(payee, Decimal(amount), fresh.clock().astimezone().date()),
        "chk",
    )
    fresh.plaid.report(item_id, "chk", Decimal("4130") - Decimal(amount))


def sync_by_hand(fresh: FirstDay) -> int:
    """`hearth plaid sync --approved`, as a member runs it beside the serving hearth: a process
    of its own, with books of its own, over the same two databases."""
    return plaid_cli.sync(
        fresh.settings,
        fresh.setup,
        fresh.clock,
        books.from_household(fresh.settings, rulings=rulings_in, learns=may_learn),
    )


def holding(found: Books | None, payee: str = PAYEE) -> bool:
    return found is not None and any(getattr(e, "payee", None) == payee for e in found.entries)


def until_holding(fresh: FirstDay, payee: str = PAYEE) -> Books:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        found = fresh.bookkeeper.status.books
        if holding(found, payee) and not fresh.bookkeeper.status.recomputing:
            assert found is not None
            return found
        time.sleep(0.01)
    raise AssertionError(f"{payee} never reached the serving hearth's books")


def test_a_sync_by_hand_leaves_the_books_holding_what_it_fetched(
    mapped: FirstDay, capsys: pytest.CaptureFixture[str]
) -> None:
    before = mapped.settings.books_file.read_text()
    post(mapped)

    assert sync_by_hand(mapped) == 0

    written = mapped.settings.books_file.read_text()
    assert written != before
    assert PAYEE in written
    said = capsys.readouterr().out
    assert ": complete, 1 page(s), +1 ~0 -0" in said
    at = mapped.clock().astimezone()
    assert f"The books hold what was fetched now: recomputed {at:%Y-%m-%d %H:%M}" in said
    assert str(mapped.settings.books_file) in said


def test_a_connection_whose_token_cannot_be_opened_is_said_not_to_have_synced(
    mapped: FirstDay, capsys: pytest.CaptureFixture[str]
) -> None:
    (item_id,) = mapped.plaid.items()
    conn = mapped.connect()
    try:
        conn.execute(
            "UPDATE vault_token SET ciphertext = zeroblob(64) WHERE item_id = ?", (item_id,)
        )
    finally:
        conn.close()
    post(mapped)

    assert sync_by_hand(mapped) == plaid_cli.FAILED

    said = capsys.readouterr().out
    assert f"{item_id}: could not be synced; hearth.log says why" in said
    assert "No working connection" not in said
    assert "The books hold what was fetched" not in said
    assert "Nothing was fetched, so the books hold nothing new: recomputed" in said
    assert PAYEE not in said
    assert PAYEE not in mapped.settings.books_file.read_text()


def test_the_books_are_said_to_hold_only_what_the_connections_that_synced_fetched(
    tmp_path: Path, clock: FakeClock, capsys: pytest.CaptureFixture[str]
) -> None:
    fresh = first_day(tmp_path, clock, institutions=(WILLOWMERE, FERNHILL))
    unopenable = fresh.link("avery", FERNHILL.id)
    fresh.link("avery", WILLOWMERE.id)
    fresh.map(fresh.signed_in("avery"), {**PATHS, "3131": CHECKING, "4242": BROKERAGE})
    until_booked(fresh, PATHS["6616"])
    clock.advance(timedelta(minutes=15))
    conn = fresh.connect()
    try:
        conn.execute(
            "UPDATE vault_token SET ciphertext = zeroblob(64) WHERE item_id = ?", (unopenable,)
        )
    finally:
        conn.close()
    (synced,) = [item for item in fresh.plaid.items() if item != unopenable]
    fresh.plaid.change(
        synced,
        "added",
        FakeTransaction(PAYEE, Decimal("18.40"), clock().astimezone().date()),
        "chk",
    )
    fresh.plaid.report(synced, "chk", Decimal("4111.60"))

    assert sync_by_hand(fresh) == plaid_cli.FAILED

    said = capsys.readouterr().out
    assert f"{unopenable}: could not be synced; hearth.log says why" in said
    assert f"{synced}: complete, 1 page(s), +1 ~0 -0" in said
    assert "The books hold what was fetched from the connections that synced now" in said
    assert PAYEE in fresh.settings.books_file.read_text()


def test_the_serving_hearth_shows_what_a_sync_by_hand_fetched(watching: FirstDay) -> None:
    post(watching)
    assert not holding(watching.bookkeeper.status.books)

    sync_by_hand(watching)

    until_holding(watching)
    assert PAYEE in shown(watching.signed_in("avery").get("/you").text)


def test_a_change_the_serving_hearth_made_and_recomputed_is_not_recomputed_again(
    watching: FirstDay,
) -> None:
    established: list[datetime] = []
    establish = books.from_household(watching.settings, rulings=rulings_in, learns=may_learn)

    def counted(now: datetime) -> Books:
        established.append(now)
        return establish(now)

    counting = books.Bookkeeper(
        counted, watching.clock, standing=lambda: books.standing(watching.settings)
    )
    counting.recompute(wait=True)
    # Looks far enough apart that the stand-in's sync, which writes in steps a few
    # milliseconds apart, is never seen settled half-written.
    every = 0.25
    stop = counting.recompute_on_change(every=every)
    try:
        time.sleep(3 * every)
        assert len(established) == 1
        post(watching)
        sync_by_hand(watching)
        deadline = time.monotonic() + 10
        while not holding(counting.status.books) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert holding(counting.status.books)
        time.sleep(3 * every)
        assert len(established) == 2
    finally:
        stop.set()


@pytest.fixture
def brokerage(tmp_path: Path, clock: FakeClock) -> FirstDay:
    """A household with a bank account and a brokerage, both mapped and in the books."""
    fresh = first_day(tmp_path, clock, institutions=(FERNHILL,))
    fresh.link("avery", FERNHILL.id)
    fresh.map(fresh.signed_in("avery"), {"3131": CHECKING, "4242": BROKERAGE})
    until_booked(fresh, BROKERAGE)
    fresh.bookkeeper.recompute(wait=True)
    clock.advance(timedelta(minutes=15))
    return fresh


def move_the_transfer_onto_the_brokerage(fresh: FirstDay) -> None:
    """A ruling that counts checking's transfer onto the brokerage, whose holdings already
    hold it: the books no longer add up to what the brokerage reported, so no recompute can
    trust them (``tests.test_positions``). Recorded as the desk recorded such a ruling before it
    refused one, with no recompute after it."""
    conn = fresh.connect()
    try:
        registry = Registry(conn)
        (checking,) = [
            a.account_id
            for item in registry.all_items()
            for a in registry.accounts_of(item.item_id)
            if a.mask == "3131"
        ]
    finally:
        conn.close()
    with Feed.open(fresh.settings.feed_database) as found:
        (row,) = found.transactions(checking)
    review_id = plaid_review_id(row.transaction_id)
    (item,) = [i for i in fresh.books().pile if i.review_id == review_id]
    conn = fresh.connect()
    try:
        RulingStore(conn).record(
            member_id=fresh.member_ids["avery"],
            scope=Scope.ROW,
            group=None,
            action=Action.ACCOUNT,
            account=BROKERAGE,
            note=None,
            rows={review_id: item.proposed},
            says={review_id: item.says},
            at=fresh.clock(),
        )
    finally:
        conn.close()


def test_a_sync_whose_recompute_fails_says_so_and_leaves_the_books_as_they_were(
    brokerage: FirstDay, capsys: pytest.CaptureFixture[str]
) -> None:
    fresh = brokerage
    before = fresh.settings.books_file.read_text()
    written_at = fresh.settings.books_file.stat().st_mtime
    move_the_transfer_onto_the_brokerage(fresh)
    post_to_fernhill(fresh)

    assert sync_by_hand(fresh) == plaid_cli.FAILED

    assert fresh.settings.books_file.read_text() == before
    said = capsys.readouterr().out
    assert ": complete, 1 page(s), +1 ~0 -0" in said
    assert "The books hold" not in said
    assert "hearth: the books were not recomputed, so they do not hold what was fetched:" in said
    assert "are not what its transactions add up to." in said
    stamp = datetime.fromtimestamp(written_at).astimezone()
    assert f"{fresh.settings.books_file} stays as it was written {stamp:%Y-%m-%d %H:%M}." in said


def test_the_serving_hearth_labels_its_previous_books_when_a_sync_by_hand_cannot_be_booked(
    brokerage: FirstDay,
) -> None:
    fresh = brokerage
    computed_at = fresh.books().computed_at
    stop = fresh.bookkeeper.recompute_on_change(every=0.01)
    try:
        move_the_transfer_onto_the_brokerage(fresh)
        post_to_fernhill(fresh)
        sync_by_hand(fresh)
        deadline = time.monotonic() + 10
        while fresh.bookkeeper.status.failure is None and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        stop.set()

    status = fresh.bookkeeper.status
    assert status.failure is not None
    assert status.books is not None
    assert status.books.computed_at == computed_at
    page = shown(fresh.signed_in("avery").get("/you").text)
    assert "Today's figures could not be computed. Everything below is from" in page
    assert PAYEE not in page


def post_to_fernhill(fresh: FirstDay) -> None:
    (item_id,) = fresh.plaid.items()
    fresh.plaid.change(
        item_id,
        "added",
        FakeTransaction(PAYEE, Decimal("18.40"), fresh.clock().astimezone().date()),
        "chk",
    )
    fresh.plaid.report(item_id, "chk", Decimal("2481.60"))


def test_confirming_where_an_account_goes_puts_it_in_the_books_and_says_so(
    tmp_path: Path, clock: FakeClock
) -> None:
    fresh = first_day(tmp_path, clock, institutions=(FERNHILL,))
    fresh.link("avery", FERNHILL.id)
    avery = fresh.signed_in("avery")
    fresh.map(avery, {"3131": CHECKING})
    until_booked(fresh, CHECKING)
    fresh.bookkeeper.recompute(wait=True)
    assert BROKERAGE not in fresh.settings.books_file.read_text()

    fresh.map(avery, {"4242": BROKERAGE})

    until_booked(fresh, BROKERAGE)
    fresh.bookkeeper.recompute(wait=True)
    assert BROKERAGE in fresh.settings.books_file.read_text()
    page = shown(avery.get("/connect/import").text)
    assert "In your figures and in Review" in page
    assert "could not be computed" not in page


def test_a_confirmed_account_the_books_cannot_take_in_is_never_said_to_be_in_them(
    tmp_path: Path, clock: FakeClock
) -> None:
    """The page a member lands on once every account is confirmed says whether the books took
    them in: when the recompute that follows fails, it says so, and that the figures are the
    previous ones, rather than that the accounts are in the figures."""
    fresh = first_day(tmp_path, clock, institutions=(FERNHILL,))
    fresh.link("avery", FERNHILL.id)
    avery = fresh.signed_in("avery")
    fresh.map(avery, {"3131": CHECKING})
    until_booked(fresh, CHECKING)
    fresh.bookkeeper.recompute(wait=True)
    before = fresh.settings.books_file.read_text()
    move_the_transfer_onto_the_brokerage(fresh)

    fresh.map(avery, {"4242": BROKERAGE})

    fresh.bookkeeper.recompute(wait=True)
    assert fresh.bookkeeper.status.failure is not None
    assert fresh.settings.books_file.read_text() == before
    page = shown(avery.get("/connect/import").text)
    assert "In your figures and in Review" not in page
    assert "Not in your figures yet" in page
    assert "the accounts you confirmed are not in them yet" in page
    assert "Today's figures could not be computed. Your figures are still from" in page
