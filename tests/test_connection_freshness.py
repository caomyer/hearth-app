"""How current a connection is: what it tells the figures, and what it may tell anyone else.

The ledger cannot tell a card with no activity from a card that stopped reporting. A
connection can, for what it booked: Plaid either brought it up to date or did not. So an
account holding entries booked from its connection is complete through a month only once
that connection has synced after it, whatever its part's other accounts say; and the day it
stopped is the day that member's data stops, which nobody who may not see their transactions
may learn, from any page, on any day. A connection that has booked nothing vouches for nothing.
"""

from datetime import UTC, date, datetime, time, timedelta
from itertools import product

import pytest

from hearth import accounts, cli
from hearth.connections import plaid_source
from hearth.months import Month
from hearth.plaid.connector import NEW, Mapping
from hearth.plaid.registry import ItemState, Registry
from hearth.sharing import Facet
from tests.conftest import World
from tests.plaid.test_connect_pages import add_dana, form, save_keys, stand_in_tokens
from tests.test_disclosure import disclosure_for, household_for
from tests.test_real_ledger_shapes import WITHOUT_TRANSACTIONS

EVERYTHING = frozenset(Facet)
COARSE = "The accounts behind this figure end in October 2026."

CARD = "Liabilities:Zq:CaseyCard"
"""Casey's card: its only entry in the ledger is on Oct 3."""

OCT_3 = date(2026, 10, 3)


def _feed(world: World, username: str, plaid_account: str, ledger_account: str) -> None:
    """Confirm that one of `username`'s connected accounts feeds `ledger_account`."""
    with world.connector(username) as connector:
        row = next(r for r in connector.mapping().rows if r.account.name == plaid_account)
        connector.confirm(
            [Mapping(row.account.account_id, ledger_account, row.chosen_owners, confirm=True)]
        )


def _feed_caseys_card(world: World) -> None:
    _feed(world, "casey", "Chimera Credit", CARD)


def _last_synced(world: World, username: str, day: date, *, expired: bool) -> None:
    """Record `username`'s connection as last synced on `day`, and stopped there if `expired`."""
    conn = world.connect()
    try:
        registry = Registry(conn)
        item_id = world.item_ids[username]
        registry.synced(item_id, at=datetime.combine(day, time(12), UTC), history=None)
        if expired:
            registry.set_state(item_id, ItemState.LOGIN_REQUIRED, problem="ITEM_LOGIN_REQUIRED")
    finally:
        conn.close()


def _moves(world: World, *moves: tuple[date, str]) -> None:
    """One more dollar out of each account named, on the day given."""
    world.ledger.write_text(
        world.ledger.read_text()
        + "".join(
            f'\n{day} * "Payee Later"\n  {account}  -1.00 USD\n  Expenses:Kites\n'
            for day, account in moves
        )
    )


def _booked(world: World, username: str, *moves: tuple[date, str]) -> None:
    """One more dollar out of each account named, on the day given, booked from `username`'s
    connection as the import books it."""
    _sourced(world, plaid_source(world.item_ids[username], "txn"), *moves)


def _sourced(world: World, source: str, *moves: tuple[date, str]) -> None:
    world.ledger.write_text(
        world.ledger.read_text()
        + "".join(
            f'\n{day} * "Payee Booked"\n  source: "{source}-{n}"\n'
            f"  {account}  -1.00 USD\n  Expenses:Kites\n"
            for n, (day, account) in enumerate(moves)
        )
    )


LATER = (date(2026, 11, 10), date(2026, 12, 2))


def _everyone_else_current(world: World) -> None:
    """Every other part moves in November and December, so only Casey's accounts decide which
    of the household's months are complete."""
    _moves(
        world,
        *(
            (day, account)
            for day in LATER
            for account in ("Assets:Zq:AverySolo", "Assets:Zq:Joint", "Assets:Zq:BlakeSolo")
        ),
    )


def _today(world: World, day: date) -> None:
    world.clock.now = datetime.combine(day, world.clock.now.timetz())


def _covered(world: World, username: str) -> list[tuple[int, bool]]:
    return [(bar.month.month, bar.covered) for bar in household_for(world, username).spending.bars]


def test_a_card_whose_connection_stopped_leaves_its_months_incomplete_however_current_the_rest(
    world: World,
) -> None:
    """Casey's checking moves into December; Casey's card's connection last worked on the day
    it was connected, Oct 15, after booking an Oct 3 entry, and the bank now asks Casey to sign
    in again. October onwards is incomplete: until a sync after a month, nobody knows what the
    card did in it."""
    _everyone_else_current(world)
    _feed_caseys_card(world)
    _booked(world, "casey", (OCT_3, CARD))
    _moves(world, (date(2026, 11, 10), "Assets:Zq:CaseyChecking"))
    _moves(world, (date(2026, 12, 2), "Assets:Zq:CaseyChecking"))
    _today(world, date(2026, 12, 5))
    world.plaid.require_login(world.item_ids["casey"])
    with world.connector("casey") as connector:
        connector.resync(world.item_ids["casey"])
    world.recompute()

    assert _covered(world, "casey")[-4:] == [(9, True), (10, False), (11, False), (12, False)]
    totals = disclosure_for(world, "casey").you().totals
    assert not totals.covered
    assert totals.over_typical is None


def test_how_complete_a_month_is_is_the_same_answer_for_everyone(world: World) -> None:
    """Coverage is no one's view of the data: whoever asks, and whatever they are shared, each
    month of Casey's figures is complete or not alike."""
    _everyone_else_current(world)
    _feed_caseys_card(world)
    _booked(world, "casey", (OCT_3, CARD))
    _moves(world, (date(2026, 11, 10), "Assets:Zq:CaseyChecking"))
    _last_synced(world, "casey", date(2026, 10, 20), expired=True)
    _today(world, date(2026, 12, 5))
    world.recompute()
    expected = [(10, False), (11, False), (12, False)]

    for shared in (frozenset(), *WITHOUT_TRANSACTIONS, EVERYTHING):
        world.choose("casey", shared | {Facet.MONTHLY_SPENDING_TOTAL})
        for viewer in ("avery", "blake", "casey"):
            assert _covered(world, viewer)[-3:] == expected, (viewer, sorted(shared))
            assert not disclosure_for(world, viewer).person("casey").totals.covered, viewer


def test_a_connection_that_booked_nothing_vouches_for_nothing(world: World) -> None:
    """Casey's card last moved on Oct 3 by statement, and its connection synced this morning,
    but nothing the connection brought is in the books: every month is exactly as complete as
    with no connection at all. Here Casey's checking carries the part, by the ledger's own
    limitation for statement-fed data; the connection adds nothing to it."""
    _everyone_else_current(world)
    _moves(world, (date(2026, 11, 10), "Assets:Zq:CaseyChecking"))
    _moves(world, (date(2026, 12, 2), "Assets:Zq:CaseyChecking"))
    _today(world, date(2026, 12, 5))
    world.recompute()
    unconnected = _covered(world, "casey")

    _feed_caseys_card(world)
    _last_synced(world, "casey", date(2026, 12, 5), expired=False)
    world.recompute()

    assert _covered(world, "casey") == unconnected
    assert _covered(world, "casey")[-4:] == [(9, True), (10, True), (11, True), (12, True)]
    books = world.bookkeeper.status.books
    assert books is not None
    assert books.synced == {}


def test_a_current_connection_never_claims_months_the_books_do_not_hold(world: World) -> None:
    """Casey's card synced this morning, but nothing of Casey's is in the ledger after Oct 3,
    and nothing in it came from the connection: the sync vouches for nothing, so those months
    are still incomplete."""
    _everyone_else_current(world)
    _feed_caseys_card(world)
    _last_synced(world, "casey", date(2026, 12, 5), expired=False)
    _today(world, date(2026, 12, 5))
    world.recompute()

    assert _covered(world, "casey")[-3:] == [(10, False), (11, False), (12, False)]
    books = world.bookkeeper.status.books
    assert books is not None
    assert books.synced == {}


def test_a_sync_after_the_booked_entries_holds_the_months_through_it(world: World) -> None:
    """Casey's card's booked entries stop on Oct 3 and nothing else of Casey's moves after;
    its connection synced on Dec 5. Plaid brought everything there was, so the card was
    quiet, and every month through December is complete."""
    _everyone_else_current(world)
    _feed_caseys_card(world)
    _booked(world, "casey", (OCT_3, CARD))
    _last_synced(world, "casey", date(2026, 12, 5), expired=False)
    _today(world, date(2026, 12, 5))
    world.recompute()

    assert _covered(world, "casey")[-3:] == [(10, True), (11, True), (12, True)]


def test_a_sync_before_the_newest_booked_entry_vouches_for_nothing(world: World) -> None:
    """Casey's card holds an entry booked from its connection on Nov 20, after the sync the
    books know of, on Nov 10: that sync cannot vouch for the card, which is judged with its
    part as if it had no connection."""
    _everyone_else_current(world)
    _moves(world, (date(2026, 12, 2), "Assets:Zq:CaseyChecking"))
    _booked(world, "casey", (date(2026, 11, 20), CARD))
    _today(world, date(2026, 12, 5))
    world.recompute()
    unconnected = _covered(world, "casey")

    _feed_caseys_card(world)
    _last_synced(world, "casey", date(2026, 11, 10), expired=False)
    world.recompute()

    books = world.bookkeeper.status.books
    assert books is not None
    assert CARD not in books.synced
    assert _covered(world, "casey") == unconnected
    assert _covered(world, "casey")[-3:] == [(10, True), (11, True), (12, True)]


@pytest.mark.parametrize("elsewhere", ["statement", "another-item", "unlabelled"])
def test_only_entries_booked_from_the_connection_are_its_data(world: World, elsewhere: str) -> None:
    """Casey's card's Oct 3 entry came from a statement, from some other connection, or from
    nowhere hearth can name; its own connection synced on Dec 5. None of it is that
    connection's data, so the sync vouches for nothing and November and December, which the
    ledger does not reach for Casey, stay incomplete."""
    _everyone_else_current(world)
    _feed_caseys_card(world)
    if elsewhere == "statement":
        _sourced(world, "casey-card-2026-10.csv:4", (OCT_3, CARD))
    elif elsewhere == "another-item":
        _sourced(world, plaid_source(world.item_ids["avery"], "txn"), (OCT_3, CARD))
    else:
        _moves(world, (OCT_3, CARD))
    _last_synced(world, "casey", date(2026, 12, 5), expired=False)
    _today(world, date(2026, 12, 5))
    world.recompute()

    assert _covered(world, "casey")[-3:] == [(10, False), (11, False), (12, False)]


def test_a_card_no_connection_feeds_is_judged_with_its_part_as_before(world: World) -> None:
    """The limitation that stays: with no connection, the ledger cannot tell Casey's quiet card
    from one whose statements stopped, so the checking account's later entries hold its
    months. Only a balance assertion from the card's statement could say more."""
    _everyone_else_current(world)
    _moves(world, (date(2026, 11, 10), "Assets:Zq:CaseyChecking"))
    _today(world, date(2026, 12, 5))
    world.recompute()

    assert _covered(world, "casey")[-3:] == [(10, True), (11, False), (12, False)]


def test_an_account_carries_its_own_entries_past_where_its_connection_stopped(
    world: World,
) -> None:
    """Statements imported for Casey's card run into November, after its connection, which
    booked the card's Oct 3 entry, stopped in October: the card's own entries vouch for
    October, whatever the connection says."""
    _everyone_else_current(world)
    _feed_caseys_card(world)
    _booked(world, "casey", (OCT_3, CARD))
    _moves(world, (date(2026, 11, 10), "Assets:Zq:CaseyChecking"), (date(2026, 11, 12), CARD))
    _last_synced(world, "casey", date(2026, 10, 20), expired=True)
    _today(world, date(2026, 12, 5))
    world.recompute()

    assert _covered(world, "casey")[-3:] == [(10, True), (11, False), (12, False)]


def test_an_account_the_ledger_has_not_opened_holds_nothing_back(world: World) -> None:
    """Casey's connection booked Casey's checking through Dec 2, and Casey files another of its
    accounts into a new ledger account the books do not hold yet. That account holds nothing
    the connection booked, so the connection says nothing about it, and nothing of it is in
    any figure: it holds nothing back."""
    _everyone_else_current(world)
    _feed(world, "casey", "Capybara Current", "Assets:Zq:CaseyChecking")
    with world.connector("casey") as connector:
        row = next(r for r in connector.mapping().rows if r.account.name == "Chimera Credit")
        new = next(t.ledger_account for t in row.targets if t.is_new)
        connector.confirm(
            [Mapping(row.account.account_id, NEW, row.chosen_owners, confirm=True, path=new)]
        )
    _booked(world, "casey", (date(2026, 12, 2), "Assets:Zq:CaseyChecking"))
    _last_synced(world, "casey", date(2026, 12, 5), expired=False)
    _today(world, date(2026, 12, 5))
    world.recompute()

    books = world.bookkeeper.status.books
    assert books is not None
    assert set(books.synced) == {"Assets:Zq:CaseyChecking"}
    assert _covered(world, "casey")[-2:] == [(11, True), (12, True)]
    assert "only as current as that" not in world.signed_in("casey").get("/you").text


def _caseys_card_stopped_on_oct_9(world: World) -> None:
    _everyone_else_current(world)
    _feed_caseys_card(world)
    _booked(world, "casey", (OCT_3, CARD))
    _moves(world, (date(2026, 12, 2), "Assets:Zq:CaseyChecking"))
    _last_synced(world, "casey", date(2026, 10, 9), expired=True)
    _today(world, date(2026, 12, 5))
    world.recompute()


def test_whoever_may_see_the_transactions_is_told_the_day_a_connection_stopped(
    world: World,
) -> None:
    _caseys_card_stopped_on_oct_9(world)
    world.choose("casey", EVERYTHING)
    world.choose("blake", EVERYTHING)

    casey = world.signed_in("casey").get("/you").text
    assert "The accounts behind this stop at Oct 9, so it is only as current as that." in casey
    assert "Kraken Federal stopped updating after Oct 9." in casey
    avery = world.signed_in("avery").get("/").text
    assert "Casey\u2019s accounts stop at Oct 9, so it is only as current as that." in avery
    assert "Kraken Federal" not in avery


def test_anyone_else_reads_the_month_alone_and_no_lag_notice_at_all(world: World) -> None:
    _caseys_card_stopped_on_oct_9(world)
    avery = world.signed_in("avery")

    for path in ("/", "/you", "/net-worth", "/members/casey"):
        page = avery.get(path).text
        assert "Oct 9" not in page, path
        assert "not updated since" not in page, path
        assert "Kraken Federal" not in page, path
    for path in ("/", "/net-worth", "/members/casey"):
        assert COARSE in avery.get(path).text, path


EXPIRED_HELP = (
    "An expired connection brings in nothing until you reconnect it; your figures carry on from"
    " what is already in your books, and each says where the data behind it stops. Nobody else is"
    " told that a connection stopped or which one: members you share transactions with see where"
    " your accounts' data stops, and everyone else sees only the month."
)


def test_only_the_holder_is_told_what_an_expired_connection_means(world: World) -> None:
    _caseys_card_stopped_on_oct_9(world)

    casey = world.signed_in("casey").get("/settings/connections").text
    assert EXPIRED_HELP in casey
    assert "how many accounts are behind" not in casey
    assert "last balance it brought in" not in casey
    avery = world.signed_in("avery").get("/settings/connections").text
    assert "Kraken Federal" not in avery
    assert EXPIRED_HELP not in avery


def test_a_sync_started_from_a_page_brings_the_figures_up_to_date_with_it(world: World) -> None:
    """The books note how current each connection is when they are recomputed, so a sync that
    finishes recomputes them; the figures never wait for the next morning to see it."""
    _feed_caseys_card(world)
    _booked(world, "casey", (OCT_3, CARD))
    world.recompute()
    _today(world, date(2026, 11, 2))
    casey = world.signed_in("casey")

    response = casey.post(
        f"/connect/items/{world.item_ids['casey']}/sync", data={"csrf": world.csrf(casey)}
    )

    assert response.status_code == 303
    books = world.bookkeeper.status.books
    assert books is not None
    assert books.synced[CARD] == date(2026, 11, 2)


def test_the_morning_sync_brings_the_figures_up_to_date_with_it(world: World) -> None:
    """The morning's recompute and sync start together, so the sync recomputes once it is done:
    on the 1st, last month is complete only once the morning's sync has run."""
    _feed_caseys_card(world)
    _booked(world, "casey", (OCT_3, CARD))
    world.recompute()
    _today(world, date(2026, 11, 1))

    cli.sync_then_recompute(world.settings, world.setup, world.bookkeeper, world.clock)

    books = world.bookkeeper.status.books
    assert books is not None
    assert books.synced[CARD] == date(2026, 11, 1)
    assert books.computed_at == world.clock()


LAG_DAYS = (date(2026, 10, 2), date(2026, 10, 15), date(2026, 10, 27))
"""Days within one month that another member's connection may last have synced on."""

STOPPED = ((), ("blake",), ("casey",), ("blake", "casey"))
"""Whose connections have stopped, needing a new sign-in; the others have simply not run."""

BLAKES_CARD = "Liabilities:Zq:BlakeCard"


def _open_blakes_card(world: World) -> None:
    """A card of Blake's own beside Blake's brokerage, as Casey has a card beside checking."""
    world.ledger.write_text(
        world.ledger.read_text() + f'\n2026-08-01 open {BLAKES_CARD}\n2026-08-02 * "Payee Basalt"\n'
        f"  {BLAKES_CARD}  -1.00 USD\n  Expenses:Bonsai\n"
    )
    conn = world.connect()
    try:
        accounts.register(
            conn, ledger_account=BLAKES_CARD, name="Tapir Card", owners=[world.member_ids["blake"]]
        )
    finally:
        conn.close()
    world.recompute()


def test_whose_connection_stopped_gives_no_day_away_on_any_page(world: World) -> None:
    """Avery may not see Blake's or Casey's transactions. Their connections last synced on some
    day in October, after booking their cards' entries, then either stopped or simply have not
    run since. On every day from the
    end of October through most of November, whichever days those were, whichever stopped,
    and whatever else they share, every figure page Avery can open is byte for byte the same.

    Every part has an entry on Nov 1, so once November starts the ledger holds October whole
    and only the connections decide it. The ledger is otherwise held fixed, so only the
    connections move: what the ledger itself gives away is held by `test_real_ledger_shapes`.
    """
    _open_blakes_card(world)
    _feed(world, "blake", "Basilisk Card", BLAKES_CARD)
    _feed_caseys_card(world)
    _booked(world, "blake", (date(2026, 10, 1), BLAKES_CARD))
    _booked(world, "casey", (date(2026, 10, 1), CARD))
    _moves(
        world,
        *(
            (date(2026, 11, 1), account)
            for account in (
                "Assets:Zq:AverySolo",
                "Assets:Zq:Joint",
                "Assets:Zq:BlakeSolo",
                "Assets:Zq:CaseyChecking",
            )
        ),
    )
    paths = ("/", "/you", "/net-worth", "/members/blake", "/members/casey")
    for day in (date(2026, 10, 28) + timedelta(days=n) for n in range(24)):
        _today(world, day)
        avery = world.signed_in("avery")
        seen: dict[frozenset[Facet], set[tuple[str, ...]]] = {}
        for blake, casey, stopped in product(LAG_DAYS, LAG_DAYS, STOPPED):
            _last_synced(world, "blake", blake, expired="blake" in stopped)
            _last_synced(world, "casey", casey, expired="casey" in stopped)
            world.recompute()
            for shared in WITHOUT_TRANSACTIONS:
                for other in ("blake", "casey"):
                    world.choose(other, shared)
                responses = [avery.get(path) for path in paths]
                assert all(r.status_code == 200 for r in responses), day
                seen.setdefault(shared, set()).add(tuple(r.text for r in responses))
        for shared, variants in seen.items():
            assert len(variants) == 1, (day, sorted(shared))
            (pages,) = variants
            for path, page in zip(paths, pages, strict=True):
                if path != "/you":
                    assert COARSE in page, (day, sorted(shared), path)
        books = world.bookkeeper.status.books
        assert books is not None
        assert set(books.synced) == {BLAKES_CARD, CARD}
        assert {Month.of(d) for d in books.synced.values()} == {Month(2026, 10)}


def test_a_joint_owner_never_learns_of_the_holders_connection(world: World) -> None:
    """Avery's connection feeds the joint account Blake co-owns. When it stops, Blake learns of
    it only as the joint accounts' data stopping, the way any joint transaction is Blake's to
    see; never the connection, its institution, its banner, or anything where the banner
    would have been, on any page Blake can open."""
    _everyone_else_current(world)
    _feed(world, "avery", "Aardvark Everyday", "Assets:Zq:Joint")
    _booked(world, "avery", (OCT_3, "Assets:Zq:Joint"))
    _today(world, date(2026, 12, 5))
    _last_synced(world, "avery", date(2026, 10, 9), expired=False)
    world.recompute()
    books = world.bookkeeper.status.books
    assert books is not None
    assert set(books.synced) == {"Assets:Zq:Joint"}
    paths = ("/", "/you", "/net-worth", "/members/avery", "/review", "/settings/connections")
    blake = world.signed_in("blake")
    working = [blake.get(path).text for path in paths]

    _last_synced(world, "avery", date(2026, 10, 9), expired=True)
    stopped = [blake.get(path).text for path in paths]

    assert stopped == working
    for page in stopped:
        assert "Gryphon Mutual" not in page
        assert "stopped updating" not in page
    assert "Gryphon Mutual stopped updating after Oct 9." in world.signed_in("avery").get("/").text


def test_each_sync_while_history_arrives_brings_the_figures_up_to_date(world: World) -> None:
    """Plaid sends a new connection's history over hours, and hearth asks again and again while
    it arrives. Each of those syncs recomputes the books, so the figures follow the history in
    as it lands, while the member is watching, not only once it has all arrived."""
    dana = add_dana(world)
    save_keys(world, dana)
    computed: list[datetime] = []

    def time_passes(seconds: float) -> None:
        books = world.bookkeeper.status.books
        assert books is not None
        computed.append(books.computed_at)
        world.clock.advance(timedelta(seconds=seconds))

    world.waits.then = time_passes
    opened = dana.post("/connect/link", data=form(world, dana))
    tokens = stand_in_tokens(opened.text)
    dana.post(
        "/connect/exchange",
        data=form(world, dana, public_token=tokens["Gryphon Mutual"], institution="ignored"),
    )

    assert len(computed) >= 2
    assert computed == sorted(set(computed))
    books = world.bookkeeper.status.books
    assert books is not None
    assert books.computed_at > computed[-1]


def test_rows_booked_from_a_connection_read_as_the_bank_feed_they_came_from(
    world: World,
) -> None:
    _feed_caseys_card(world)
    _booked(world, "casey", (OCT_3, CARD))
    world.recompute()

    page = world.signed_in("casey").get(f"/accounts/{world.account_ids['casey_card']}").text

    assert "; bank feed from Oct 3 to Oct 3." in page
    assert "plaid" not in page.lower().split("<main", 1)[1]
    world.choose("casey", EVERYTHING)
    for username, path in (("casey", "/you"), ("avery", "/members/casey")):
        page = world.signed_in(username).get(path).text
        assert "Bank feed: Oct 3 to Oct 3" in page, path
        assert "plaid" not in page.lower().split("<main", 1)[1], path


def test_a_payment_another_connection_booked_never_revives_a_stopped_card(
    world: World,
) -> None:
    """Casey's card's connection booked its Oct 3 entry and stopped on Oct 9. On Nov 25 a
    payment to the card arrives through another connection's feed, as a checking account's
    feed books a card payment. It proves one transaction on the card, not that the card's own
    feed is complete: October onwards stays incomplete and Casey's data still stops on Oct 9."""
    _everyone_else_current(world)
    _feed_caseys_card(world)
    _booked(world, "casey", (OCT_3, CARD))
    _moves(world, (date(2026, 12, 2), "Assets:Zq:CaseyChecking"))
    world.ledger.write_text(
        world.ledger.read_text() + f'\n2026-11-25 * "Card payment"\n'
        f'  source: "{plaid_source(world.item_ids["avery"], "payment")}"\n'
        f"  Assets:Zq:CaseyChecking  -1.00 USD\n  {CARD}  1.00 USD\n"
    )
    _last_synced(world, "casey", date(2026, 10, 9), expired=True)
    _today(world, date(2026, 12, 5))
    world.recompute()

    books = world.bookkeeper.status.books
    assert books is not None
    assert books.synced[CARD] == date(2026, 10, 9)
    assert _covered(world, "casey")[-3:] == [(10, False), (11, False), (12, False)]
    ends = disclosure_for(world, "casey").you().ends
    assert ends is not None
    assert ends.day == date(2026, 10, 9)
