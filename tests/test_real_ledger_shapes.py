"""Shapes a real household's ledger has that the invented demo never exercised.

A ledger imported from statements starts part way through its accounts' lives and stops at
its period's end; categories from different branches share a last name; a member may own
nothing alone. The invented household's ledger starts on Aug 1 and its newest entry is
Oct 3, while the clock reads Oct 15.
"""

from datetime import date, timedelta
from decimal import Decimal
from itertools import product

from hearth import accounts, figures
from hearth.disclosure.household import category_names
from hearth.months import Month
from hearth.sharing import Facet
from hearth.valuation.pricestore import PriceStore
from hearth.valuation.quotes import Quote
from tests import household
from tests.conftest import World
from tests.test_disclosure import disclosure_for, household_for

EVERYTHING = frozenset(Facet)


def test_the_books_are_as_of_today_not_the_ledgers_last_day(world: World) -> None:
    books = disclosure_for(world, "avery").books

    assert books is not None
    assert books.as_of == date(2026, 10, 15)


def _history_from_august(world: World) -> None:
    """Accounts opened years before the ledger's history of them starts, as a statement
    import that begins in the middle of their life leaves them."""
    text = world.ledger.read_text()
    world.ledger.write_text(text.replace(f"{household.OPENED} open", "2020-01-01 open"))
    _price_on(world, date(2026, 8, 31))
    world.recompute()


def _price_on(world: World, day: date) -> None:
    conn = world.connect()
    try:
        store = PriceStore(conn)
        for symbol, (_, _, price) in household.SECURITIES.items():
            store.record(Quote(symbol, day, Decimal(price), "USD", "plaid", "test", world.clock()))
    finally:
        conn.close()


def test_net_worth_history_starts_where_the_ledger_does(world: World) -> None:
    _history_from_august(world)

    history = household_for(world, "avery").history

    assert [point.day for point in history] == [
        date(2026, 8, 31),
        date(2026, 9, 30),
        date(2026, 10, 15),
    ]


def test_no_change_is_claimed_over_months_the_ledger_does_not_hold(world: World) -> None:
    _history_from_august(world)

    head = household_for(world, "avery").headline

    assert head.since_month_start is not None
    assert head.over_year is None


STOPS = "accounts behind this stop at Oct 3, so it is only as current as that."
COARSE = "The accounts behind this figure end in October 2026."


def test_every_figure_page_says_where_its_data_stops(world: World) -> None:
    world.clock.advance(timedelta(days=30))
    world.recompute()
    avery = world.signed_in("avery")

    for path, stop in (
        ("/", COARSE),
        ("/you", STOPS),
        ("/net-worth", COARSE),
        ("/members/blake", COARSE),
    ):
        assert stop in avery.get(path).text, path
    assert "Your accounts" not in avery.get("/").text


def test_months_the_ledger_has_not_reached_read_as_incomplete_not_as_nothing(
    world: World,
) -> None:
    """Every account's data stops on Oct 3, and the clock reads Nov 14: neither October, cut
    short, nor November, not reached at all, is a month whose figures are whole."""
    world.clock.advance(timedelta(days=30))
    world.recompute()

    view = household_for(world, "avery")

    assert [(bar.month.month, bar.covered) for bar in view.spending.bars[-3:]] == [
        (9, True),
        (10, False),
        (11, False),
    ]
    assert view.headline.since_month_start is None
    assert [point.day for point in view.history] == [date(2026, 9, 30), date(2026, 11, 14)]
    assert "History incomplete" in world.signed_in("avery").get("/").text


def _claim(world: World, ledger_account: str, owners: tuple[str, ...], entries: str) -> None:
    """Open `ledger_account` for `owners` alone, so it is a part of its own, with `entries`."""
    world.ledger.write_text(world.ledger.read_text() + entries)
    conn = world.connect()
    try:
        accounts.register(
            conn,
            ledger_account=ledger_account,
            name="Lemur Account",
            owners=[world.member_ids[owner] for owner in owners],
        )
    finally:
        conn.close()
    world.recompute()


def _transfer(day: str, account: str, amount: str = "100.00") -> str:
    return f'\n{day} * "Payee Transfer"\n  {account}  {amount} USD\n  Assets:Zq:Orphan\n'


def test_an_account_that_only_takes_transfers_never_leaves_spending_incomplete(
    world: World,
) -> None:
    """A retirement account last paid into in August, with no spending or income through it,
    leaves September's and October's spending whole; its net worth still stops in August."""
    _claim(
        world,
        "Assets:Zq:Ira",
        ("avery", "casey"),
        "\n2026-08-15 open Assets:Zq:Ira USD\n" + _transfer("2026-08-15", "Assets:Zq:Ira"),
    )

    view = household_for(world, "avery")

    assert [(bar.month.month, bar.covered) for bar in view.spending.bars[-3:]] == [
        (8, True),
        (9, True),
        (10, True),
    ]
    assert view.spending.covered
    assert view.spending.typical is not None
    assert [point.day for point in view.history] == [date(2026, 10, 15)]


def test_a_quiet_card_still_leaves_its_months_incomplete(world: World) -> None:
    _claim(
        world,
        "Liabilities:Zq:Lemur",
        ("avery", "casey"),
        "\n2026-08-10 open Liabilities:Zq:Lemur USD\n"
        '\n2026-08-12 * "Payee Lemur"\n  Liabilities:Zq:Lemur  -5.00 USD\n  Expenses:Kites\n',
    )

    view = household_for(world, "avery")

    assert [(bar.month.month, bar.covered) for bar in view.spending.bars[-3:]] == [
        (8, False),
        (9, False),
        (10, False),
    ]
    assert not view.spending.covered
    assert "History incomplete" in world.signed_in("avery").get("/").text


def test_a_month_missing_from_the_middle_of_the_history_is_no_move_at_all(world: World) -> None:
    """An account opened Sep 1 is first paid into on Sep 20, so September is not whole, and on
    Aug 31 the part it is in held nothing hearth knows of, not nothing: no change is measured
    from either, so none is passed off as October's."""
    _history_from_august(world)
    _claim(
        world,
        "Assets:Zq:Ira",
        ("avery", "casey"),
        "\n2026-09-01 open Assets:Zq:Ira USD\n"
        + _transfer("2026-09-20", "Assets:Zq:Ira")
        + _transfer("2026-10-02", "Assets:Zq:Ira"),
    )

    moves = disclosure_for(world, "avery").net_worth().moves

    assert [(move.month_end, move.saved, move.market) for move in moves] == []


def _statements_close_on_sep_30(world: World, *, asserted: bool) -> None:
    """Every entry dated by Sep 30, as a statement import closing that day leaves the ledger,
    with the Oct 1 balance assertion such an import closes each account with, if `asserted`."""
    text = world.ledger.read_text().replace(str(household.OCT), "2026-09-29")
    if asserted:
        text += "".join(
            f"\n2026-10-01 balance {account}  {cash} USD\n"
            for account, cash in household.CASH.items()
        )
    world.ledger.write_text(text)
    world.clock.now = world.clock.now.replace(day=18)
    world.recompute()


def test_a_statement_closing_on_the_last_day_of_a_month_leaves_that_month_whole(
    world: World,
) -> None:
    """The Oct 1 assertion makes September whole; October, which holds no transaction yet,
    reads as incomplete, never as a real month in which nothing was spent."""
    _statements_close_on_sep_30(world, asserted=True)

    view = household_for(world, "avery")

    *past, october = view.spending.bars
    september = past[-1]
    assert september.month == Month(2026, 9)
    assert september.covered
    assert view.spending.typical == figures.typical([bar.spent for bar in past if bar.covered])
    assert date(2026, 9, 30) in [point.day for point in view.history]
    assert view.headline.since_month_start is not None
    assert october.month == Month(2026, 10)
    assert not october.covered
    page = world.signed_in("avery").get("/").text
    assert "History incomplete" in page
    assert "Oct, to the 18th (history incomplete)" in page
    assert "October so far is" not in page
    assert "hearth does not have all of October's transactions" in page


def test_a_members_own_month_the_ledger_has_not_reached_is_never_a_real_nothing(
    world: World,
) -> None:
    """The same statement import, read on a member's own page and on another's: October's
    spent and earned are not figures of $0, and nothing is set against a typical October."""
    _statements_close_on_sep_30(world, asserted=True)

    for viewer, username in (("avery", "avery"), ("avery", "blake")):
        totals = disclosure_for(world, viewer).person(username).totals
        assert not totals.covered
        assert totals.over_typical is None
    you = world.signed_in("avery").get("/you").text
    assert "History incomplete" in you
    assert "typical by the 18th" not in you
    assert "a typical October at this point" not in you
    assert "hearth does not have all of October's transactions" in you
    assert "(today)" not in you
    assert 'id="pace-t"' not in you
    assert "No spending yet this month" not in you
    member = world.signed_in("avery").get("/members/blake").text
    assert "History incomplete" in member


def test_without_the_closing_assertion_the_month_cannot_be_told_whole(world: World) -> None:
    _statements_close_on_sep_30(world, asserted=False)

    view = household_for(world, "avery")

    september = next(bar for bar in view.spending.bars if bar.month == Month(2026, 9))
    assert not september.covered
    assert view.headline.since_month_start is None


def test_whose_transactions_everyone_shares_is_told_the_day_it_stops(world: World) -> None:
    world.clock.advance(timedelta(days=30))
    world.recompute()
    for username in ("blake", "casey"):
        world.choose(username, EVERYTHING)
    avery = world.signed_in("avery")

    assert "Your accounts stop at Oct 3, so" in avery.get("/").text
    assert STOPS in avery.get("/members/blake").text


def _casey_stops_early(world: World) -> None:
    """Everyone else's accounts run to Nov 10; Casey's still stop at Oct 3."""
    late = "".join(
        f'2026-11-10 * "Payee Late"\n  {account}  -1.00 USD\n  {category}\n\n'
        for account, category in (
            ("Assets:Zq:AverySolo", "Expenses:Avocados"),
            ("Assets:Zq:AveryInvest", "Expenses:Avocados"),
            ("Assets:Zq:BlakeSolo", "Expenses:Bonsai"),
            ("Assets:Zq:Joint", "Expenses:Jointery"),
        )
    )
    world.ledger.write_text(world.ledger.read_text() + "\n" + late)
    world.clock.now = world.clock.now.replace(month=11, day=14)
    world.recompute()


def test_a_total_is_as_current_as_its_stalest_part_named_only_to_who_may_see_it(
    world: World,
) -> None:
    _casey_stops_early(world)

    shared_totals_only = world.signed_in("avery").get("/").text
    world.choose("casey", EVERYTHING)
    casey_shares_everything = world.signed_in("avery").get("/").text
    world.choose("blake", EVERYTHING)
    everyone_shares_everything = world.signed_in("avery").get("/").text

    for page in (shared_totals_only, casey_shares_everything):
        assert COARSE in page
        assert "Casey\u2019s accounts stop" not in page
    named = "Casey\u2019s accounts stop at Oct 3, so it is only as current as that."
    assert named in everyone_shares_everything


LAST_DAYS = (date(2026, 10, 3), date(2026, 10, 15), date(2026, 10, 28))
"""Days within one month that the other members' data may stop on."""

WITHOUT_TRANSACTIONS = (
    frozenset({Facet.NET_WORTH_TOTAL, Facet.MONTHLY_SPENDING_TOTAL}),
    frozenset({Facet.NET_WORTH_TOTAL, Facet.MONTHLY_SPENDING_TOTAL, Facet.ACCOUNT_BALANCES}),
)
"""Sharing levels at which another member's transactions stay private."""


def _last_moves(original: str, avery: date, blake: date, casey: date) -> str:
    """The ledger with one more dollar out of each member's own account, on the day given.

    The amounts never change, only the days: a total shared day by day does tell which days
    the sharer's money moved, and docs/privacy-boundary.md says so as an accepted property of
    sharing a total, not an oversight. Varying amounts here would test that property, not the
    one this test holds, so do not.
    """
    moves = (
        (avery, "Assets:Zq:AverySolo", "Expenses:Avocados"),
        (blake, "Assets:Zq:BlakeSolo", "Expenses:Bonsai"),
        (casey, "Liabilities:Zq:CaseyCard", "Expenses:Kites"),
    )
    return original + "".join(
        f'\n{day} * "Payee Last"\n  {account}  -1.00 USD\n  {category}\n'
        for day, account, category in moves
    )


def test_whose_transactions_are_private_gives_no_day_away_on_any_page(world: World) -> None:
    """Avery may not see Blake's or Casey's transactions. On every recompute day, whichever
    days of October their data stops on, every figure page Avery can open is byte for byte the
    same; only Avery's own last day, which Avery may see, is held fixed per comparison."""
    original = world.ledger.read_text()
    paths = ("/", "/net-worth", "/members/blake", "/members/casey")
    for own, day in product(
        (date(2026, 10, 10), date(2026, 10, 20)),
        (date(2026, 10, 28) + timedelta(days=n) for n in range(24)),
    ):
        world.clock.now = world.clock.now.replace(month=day.month, day=day.day)
        avery = world.signed_in("avery")
        seen: dict[frozenset[Facet], set[tuple[str, ...]]] = {}
        for blake, casey in product(LAST_DAYS, LAST_DAYS):
            world.ledger.write_text(_last_moves(original, own, blake, casey))
            world.recompute()
            for shared in WITHOUT_TRANSACTIONS:
                for other in ("blake", "casey"):
                    world.choose(other, shared)
                responses = [avery.get(path) for path in paths]
                assert all(r.status_code == 200 for r in responses), (own, day)
                pages = tuple(r.text for r in responses)
                seen.setdefault(shared, set()).add(pages)
        for shared, variants in seen.items():
            assert len(variants) == 1, (own, day, sorted(shared))
            (pages,) = variants
            for path, page in zip(paths, pages, strict=True):
                assert COARSE in page, (own, day, sorted(shared), path)


def test_a_members_own_figures_carry_their_own_last_day(world: World) -> None:
    _casey_stops_early(world)

    assert "only as current as that" not in world.signed_in("avery").get("/you").text
    assert STOPS in world.signed_in("casey").get("/you").text


def test_data_that_is_up_to_date_says_nothing_about_stopping(world: World) -> None:
    """To a viewer who may see every part's transactions, the note says the day, and only once
    it is long enough ago to say so."""
    for username in ("blake", "casey"):
        world.choose(username, EVERYTHING)
    world.clock.now = world.clock.now.replace(day=5)
    world.recompute()

    for path in ("/", "/you", "/net-worth", "/members/casey"):
        assert "only as current as that" not in world.signed_in("avery").get(path).text, path

    world.clock.now = world.clock.now.replace(day=11)
    world.recompute()

    assert "Your accounts stop at Oct 3, so" in world.signed_in("avery").get("/").text
    assert STOPS in world.signed_in("avery").get("/members/casey").text


def test_categories_that_share_a_name_keep_the_parents_that_tell_them_apart() -> None:
    names = category_names(
        [
            "Expenses:Living:Transport:Other",
            "Expenses:Travel:Other",
            "Expenses:Living:Food",
            "Expenses:Fees:Other",
            "Expenses:Gifts:Food",
        ]
    )

    assert names == {
        "Expenses:Living:Transport:Other": "Transport: Other",
        "Expenses:Travel:Other": "Travel: Other",
        "Expenses:Living:Food": "Living: Food",
        "Expenses:Fees:Other": "Fees: Other",
        "Expenses:Gifts:Food": "Gifts: Food",
    }


def test_a_category_is_never_just_other_which_is_the_leftover_row() -> None:
    assert category_names(["Expenses:Misc:Other", "Expenses:Dining"]) == {
        "Expenses:Misc:Other": "Misc: Other",
        "Expenses:Dining": "Dining",
    }
    assert category_names(["Expenses:Other", "Expenses:Dining"]) == {
        "Expenses:Other": "Expenses: Other",
        "Expenses:Dining": "Dining",
    }


def test_a_member_whose_accounts_are_all_joint_is_pointed_at_them(world: World) -> None:
    conn = world.connect()
    try:
        conn.execute("DELETE FROM account_owners WHERE member_id = ?", (world.member_ids["casey"],))
        conn.execute(
            "INSERT INTO account_owners (account_id, member_id) VALUES (?, ?)",
            (world.account_ids["joint"], world.member_ids["casey"]),
        )
    finally:
        conn.close()

    page = world.signed_in("casey").get("/you").text

    assert "Connect a bank to get started" in page
    assert "Joint accounts with Avery and Blake are on" in page


def test_net_worth_history_starts_at_the_first_month_end_with_prices(world: World) -> None:
    view = household_for(world, "avery")

    assert [point.day for point in view.history] == [date(2026, 9, 30), date(2026, 10, 15)]
    assert view.priced_from == date(2026, 9, 30)
    for path in ("/", "/net-worth"):
        page = world.signed_in("avery").get(path).text
        assert "Starts on Sep 30: the holdings have no price before then." in page, path


def test_a_holding_with_no_price_even_today_never_holds_back_the_others_history(
    world: World,
) -> None:
    """One brokerage priced its fund only from today; another fund has no price at all. Each
    month end before the first fund's price is left out, never drawn at the cash alone with a
    rise on the day the price arrived; the fund with no price is a floor at every point alike."""
    conn = world.connect()
    try:
        conn.execute("DELETE FROM price WHERE symbol = 'ZQBB'")
        conn.execute(
            "DELETE FROM price WHERE symbol = 'ZQAA' AND as_of < ?", (household.PRICED.isoformat(),)
        )
        conn.commit()
    finally:
        conn.close()
    world.recompute()

    view = household_for(world, "avery")

    assert [point.day for point in view.history] == [date(2026, 10, 15)]
    assert view.priced_from == date(2026, 10, 15)
    page = world.signed_in("avery").get("/").text
    assert "Starts on Oct 15: the holdings have no price before then." in page


def test_history_with_a_price_for_every_month_end_says_nothing_of_prices(world: World) -> None:
    _price_on(world, date(2026, 8, 31))
    world.recompute()

    view = household_for(world, "avery")

    assert view.history[0].day == date(2026, 8, 31)
    assert view.priced_from is None
    assert "the holdings have no price before" not in world.signed_in("avery").get("/").text


def test_history_never_joins_a_month_end_across_months_left_out_for_want_of_prices(
    world: World,
) -> None:
    """A holding bought in September, with no price before October: Aug 31, from before it was
    bought, is priced, but September is not, so history starts after September, never jumping
    from Aug 31 straight to today."""
    text = world.ledger.read_text().replace(
        f'{household.OPENED} * "Opening position"\n  Assets:Zq:BlakeSolo',
        '2026-09-02 * "Opening position"\n  Assets:Zq:BlakeSolo',
    )
    world.ledger.write_text(text)
    conn = world.connect()
    try:
        conn.execute("DELETE FROM price WHERE symbol = 'ZQBB' AND as_of = '2026-09-30'")
        conn.commit()
        quote = Quote(
            "ZQAA", date(2026, 8, 31), Decimal("1234.56"), "USD", "plaid", "test", world.clock()
        )
        PriceStore(conn).record(quote)
    finally:
        conn.close()
    world.recompute()

    view = household_for(world, "avery")

    assert [point.day for point in view.history] == [date(2026, 10, 15)]
    assert view.priced_from == date(2026, 10, 15)
    assert "Starts on Aug 31" not in world.signed_in("avery").get("/").text


def _blakes_history_starts_on_oct_14(world: World) -> None:
    """Blake's account opened on Oct 1 and first moved on Oct 14, so the ledger does not hold
    the start of October for it, though it holds Blake's October spending since."""
    text = world.ledger.read_text().replace(
        f"{household.OPENED} open Assets:Zq:BlakeSolo", "2026-10-01 open Assets:Zq:BlakeSolo"
    )
    for payee in ("Opening", "Opening position"):
        text = text.replace(
            f'{household.OPENED} * "{payee}"\n  Assets:Zq:BlakeSolo',
            f'2026-10-14 * "{payee}"\n  Assets:Zq:BlakeSolo',
        )
    for payee in ("Payee Bittern Nursery", "Payee Bittern Wages"):
        text = text.replace(f'{household.OCT} * "{payee}"', f'2026-10-14 * "{payee}"')
    world.ledger.write_text(text)
    world.recompute()


def test_a_month_the_ledger_has_not_reached_shows_no_categories_on_any_page(
    world: World,
) -> None:
    """Neither the household's categories nor a member's read as a whole October: what the
    ledger holds of it is shown nowhere as where the month went."""
    _blakes_history_starts_on_oct_14(world)
    world.choose("blake", EVERYTHING)

    assert not household_for(world, "avery").spending.covered
    page = world.signed_in("avery").get("/").text
    assert "Bonsai" not in page
    assert "Total only, not by category" not in page
    assert not disclosure_for(world, "avery").person("blake").totals.covered
    member = world.signed_in("avery").get("/members/blake").text
    assert "History incomplete" in member
    assert "By category" not in member


def test_the_sharing_preview_shows_no_categories_for_a_month_the_ledger_has_not_reached(
    world: World,
) -> None:
    """The preview of what the household sees reads as the member's page does: October's
    categories and transactions are not presented as the month's, nor as a month of nothing."""
    _blakes_history_starts_on_oct_14(world)
    world.choose("blake", EVERYTHING)

    preview = world.signed_in("blake").get("/settings/sharing").text
    assert "Bonsai" not in preview
    assert "Nothing spent yet" not in preview
    assert "this month, with merchants" not in preview
    assert preview.count("History incomplete") == 4


def test_no_change_is_measured_from_before_any_account_opened(world: World) -> None:
    """The household's books open on Aug 1, so a year ago it held nothing hearth knows of, not
    nothing: twelve months' change would be the whole of today's figure, read as growth."""
    page = world.signed_in("avery").get("/").text

    assert "in 12 months" not in page
    assert "since Oct 1" in page  # a change it can measure is still there
