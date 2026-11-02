"""What a brokerage account holds, in the books hearth keeps for a household with no ledger:
taken as its brokerage reports it, on the day it reports it, and proved against that report
as a bank account's balance is (``hearth.plaid.opening``).

Plaid sends a brokerage's holdings and the price it gives each, today's only, and no trades.
So the books hold what the account holds from the day it was asked, never before, and never
how it came to hold it. Every value is invented.
"""

import re
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from beancount.core.data import Balance, Commodity, Transaction

from hearth import accounts, figures, refresh, sharing
from hearth.books import Books
from hearth.months import Month
from hearth.plaid import feed
from hearth.plaid.fake import FakeAccount, FakeHolding, FakeInstitution, FakeTransaction
from hearth.plaid.feed import Feed
from hearth.plaid.opening import EQUITY, HELD, Unknown, anchors, held
from hearth.plaid.registry import Registry
from hearth.review.ids import plaid_review_id
from hearth.review.rulings import Action, RulingStore, Scope
from hearth.sharing import Facet
from hearth.valuation import institution
from hearth.valuation.networth import Freshness
from tests import household
from tests.conftest import FakeClock
from tests.firstday import FirstDay, first_day, shown, until_booked, written
from tests.test_from_zero_pages import UNESTABLISHED, readable

BROKERAGE = "Assets:Avery:Fernhill:Brokerage"
CHECKING = "Assets:Avery:Fernhill:Checking"

HOLDINGS = (
    FakeHolding("ZQGR", "Fernhill Growth", Decimal("37.0625"), Decimal("212.48")),
    FakeHolding("ZQBD", "Fernhill Bond", Decimal(150), Decimal("9.87")),
    FakeHolding(None, "Fernhill Stable Value Trust", Decimal("40"), Decimal("10.00")),
)
"""A fund held to four places, as brokerages hold them, another fund, and a trust fund with
no ticker. Worth $7,875.04, $1,480.50 and $400.00 at the brokerage's own prices."""

REPORTED = Decimal("10000.00")
"""What the brokerage says the account is worth: its holdings, and $244.46 of cash."""

TRANSFER = FakeTransaction(
    "Transfer to Fernhill Brokerage",
    Decimal("500.00"),
    date(2026, 9, 2),
    "TRANSFER_OUT_ACCOUNT_TRANSFER",
)

FERNHILL = FakeInstitution(
    "ins_fernhill",
    "Fernhill Investments",
    (
        FakeAccount(
            "chk",
            "Checking",
            "3131",
            "depository",
            "checking",
            Decimal("2500.00"),
            transactions=(TRANSFER,),
        ),
        FakeAccount(
            "brk", "Brokerage", "4242", "investment", "brokerage", REPORTED, holdings=HOLDINGS
        ),
    ),
)

TRUST = institution.commodity("sec-ins_fernhill-Fernhill Stable Value Trust", None)
"""What the trust is held as: its brokerage gives it no ticker."""


def connected(
    tmp_path: Path, clock: FakeClock, bank: FakeInstitution = FERNHILL, *, ready: bool = True
) -> FirstDay:
    """A household on its first day that connects the brokerage and files its accounts. Its
    holdings are asked for as it is connected, and ready then unless `ready` is false."""
    fresh = first_day(tmp_path, clock, institutions=(bank,))
    if not ready:
        fresh.plaid.fail_next("/investments/holdings/get", "PRODUCT_NOT_READY")
    fresh.link("avery", bank.id)
    fresh.map(fresh.signed_in("avery"), {"3131": CHECKING, "4242": BROKERAGE})
    until_booked(fresh, BROKERAGE)
    fresh.bookkeeper.recompute(wait=True)
    return fresh


@pytest.fixture
def fresh(tmp_path: Path, clock: FakeClock) -> FirstDay:
    return connected(tmp_path, clock)


def held_now(found: Books, account: str = BROKERAGE) -> dict[str, Decimal]:
    return {
        p.holding.commodity: p.holding.units
        for p in found.today.positions
        if p.holding.account == account
    }


def test_a_brokerage_holds_what_it_reported_and_is_worth_the_balance_it_reports(
    fresh: FirstDay,
) -> None:
    found = fresh.books()

    assert held_now(found) == {
        "ZQGR": Decimal("37.0625"),
        "ZQBD": Decimal(150),
        TRUST: Decimal(40),
        "USD": Decimal("244.46"),
    }
    assert found.unknown == {}
    assert BROKERAGE not in found.unanchored
    valued = [p for p in found.today.positions if p.holding.account == BROKERAGE]
    assert all(p.freshness is Freshness.CURRENT for p in valued)
    assert sum(p.value for p in valued if p.value is not None) == REPORTED
    assert found.today.complete
    assert found.today.total == REPORTED + Decimal("2500.00")


def test_units_are_never_read_through_a_float(fresh: FirstDay) -> None:
    """A float would have made 37.0625 shares something else by the fourth place."""
    with Feed.open(fresh.settings.feed_database) as feed:
        report = feed.holdings(_linked(fresh, "4242"))
    assert report is not None
    assert Decimal("37.0625") in {p.quantity for p in report.positions}
    assert all(isinstance(p.quantity, Decimal) for p in report.positions)


def test_the_books_say_what_it_holds_on_the_day_it_was_reported_and_where_that_came_from(
    fresh: FirstDay,
) -> None:
    found = fresh.books()
    today = household.NOW.astimezone().date()
    (opening,) = [e for e in found.entries if isinstance(e, Transaction) and held(e)]
    asserted = [e for e in found.entries if isinstance(e, Balance) and held(e)]

    assert opening.date == today
    assert opening.narration == HELD
    assert opening.meta["reported-by"] == "Fernhill Investments"
    assert opening.meta["balance"] == "holdings"
    assert {p.account for p in opening.postings} == {BROKERAGE, EQUITY}
    assert {(a.date, a.amount.currency, a.amount.number) for a in asserted} == {
        (today + timedelta(days=1), "ZQGR", Decimal("37.0625")),
        (today + timedelta(days=1), "ZQBD", Decimal(150)),
        (today + timedelta(days=1), TRUST, Decimal(40)),
        (today + timedelta(days=1), "USD", Decimal("244.46")),
    }
    assert anchors(opening)
    assert all(anchors(e) for e in asserted)
    assert found.positioned == {BROKERAGE: today}


def test_the_books_file_loads_as_written_and_names_each_security(fresh: FirstDay) -> None:
    entries, errors = written(fresh.settings)

    assert errors == []
    names = {e.currency: e.meta["name"] for e in entries if isinstance(e, Commodity)}
    assert names == {
        "ZQGR": "Fernhill Growth",
        "ZQBD": "Fernhill Bond",
        TRUST: "Fernhill Stable Value Trust",
    }
    assert 'balance: "holdings"' in fresh.settings.books_file.read_text()


def test_a_security_an_inherited_ledger_declares_is_left_to_it(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Beancount allows one declaration of a commodity: declaring the ledger's fund again would
    leave the books unservable, so the ledger's stands."""
    ledger = tmp_path / "household.beancount"
    ledger.write_text('2020-01-02 commodity ZQGR\n  name: "Growth, as the household calls it"\n')
    fresh = first_day(tmp_path, clock, inherits=ledger, institutions=(FERNHILL,))
    fresh.link("avery", FERNHILL.id)
    fresh.map(fresh.signed_in("avery"), {"3131": CHECKING, "4242": BROKERAGE})
    until_booked(fresh, BROKERAGE)
    fresh.bookkeeper.recompute(wait=True)

    assert fresh.bookkeeper.status.failure is None
    found = fresh.books()
    assert held_now(found)["ZQGR"] == Decimal("37.0625")
    (declared,) = [e for e in found.entries if isinstance(e, Commodity) and e.currency == "ZQGR"]
    assert declared.meta["name"] == "Growth, as the household calls it"


def test_no_day_before_the_holdings_were_reported_reads_as_known(fresh: FirstDay) -> None:
    """Plaid gave what the account holds today and no trades: at every month end before, the
    books hold nothing in it, which nobody said. So no month before is whole for it, and no
    history is drawn through it."""
    found = fresh.books()
    brokerage = _account(fresh, BROKERAGE)

    for valuation in found.month_ends:
        assert not [p for p in valuation.positions if p.holding.account == BROKERAGE]
    assert not figures.covers(found, [brokerage], Month.of(found.month_ends[-1].as_of))


def test_cash_a_bank_sent_rows_for_is_derived_as_a_banks_balance_and_known_from_the_report(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Some brokerages send their cash's moves as transactions. Its cash before them is what
    it reports less those rows, as a bank account's opening is, so the books still add up to
    the report; and the rows do not make the months before the report whole."""
    deposit = FakeTransaction(
        "Deposit from Fernhill Checking", Decimal("-500.00"), date(2026, 9, 2), "TRANSFER_IN"
    )
    brokerage = replace(FERNHILL.accounts[1], transactions=(deposit,))
    bank = replace(FERNHILL, accounts=(FERNHILL.accounts[0], brokerage))
    fresh = connected(tmp_path, clock, bank)
    found = fresh.books()

    assert fresh.bookkeeper.status.failure is None
    assert held_now(found)["USD"] == Decimal("244.46")
    (opening,) = [e for e in found.entries if isinstance(e, Transaction) and held(e)]
    (cash,) = [
        p.units
        for p in opening.postings
        if p.account == BROKERAGE and p.units is not None and p.units.currency == "USD"
    ]
    assert cash.number == Decimal("244.46") - Decimal("500.00")
    september = Month(2026, 9)
    assert not figures.covers(found, [_account(fresh, BROKERAGE)], september)


def test_a_brokerage_that_has_not_said_what_it_holds_is_not_known_never_empty(
    tmp_path: Path, clock: FakeClock
) -> None:
    fresh = connected(tmp_path, clock, ready=False)
    found = fresh.books()

    assert found.unknown == {BROKERAGE: Unknown.INVESTMENT}
    assert BROKERAGE in found.unanchored
    assert held_now(found) == {}
    page = shown(fresh.signed_in("avery").get(f"/accounts/{_account(fresh, BROKERAGE).id}").text)
    assert "Not known yet." in page
    assert "its brokerage has not said what they are yet" in page

    refresh.sync_then_recompute(fresh.settings, fresh.setup, fresh.bookkeeper, fresh.clock)

    assert held_now(fresh.books())["ZQGR"] == Decimal("37.0625")


def test_each_morning_the_books_hold_the_latest_holdings_and_every_report_is_kept(
    fresh: FirstDay,
) -> None:
    """A brokerage's holdings are today's only, so every report is kept: a day not kept could
    never be had again. The books hold the latest, as of the day it was reported."""
    (item_id,) = fresh.plaid.items()
    fresh.plaid.hold(item_id, "brk", (replace(HOLDINGS[0], quantity=Decimal(30)),))
    fresh.clock.now += timedelta(days=1)

    refresh.sync_then_recompute(fresh.settings, fresh.setup, fresh.bookkeeper, fresh.clock)

    found = fresh.books()
    tomorrow = (household.NOW + timedelta(days=1)).astimezone().date()
    assert held_now(found) == {
        "ZQGR": Decimal(30),
        "USD": REPORTED - Decimal("6374.40"),
    }
    assert found.positioned == {BROKERAGE: tomorrow}
    conn = feed.connect(fresh.settings.feed_database)
    try:
        (kept,) = conn.execute(
            "SELECT count(*) FROM feed_holdings_report WHERE account_id = ?",
            (_linked(fresh, "4242"),),
        ).fetchone()
    finally:
        conn.close()
    assert kept == 2


def test_a_brokerage_that_reports_holding_nothing_holds_nothing(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Nothing, as its brokerage said: a figure of zero somebody established."""
    empty = replace(FERNHILL.accounts[1], current=Decimal(0), holdings=())
    fresh = connected(tmp_path, clock, replace(FERNHILL, accounts=(FERNHILL.accounts[0], empty)))
    found = fresh.books()

    assert found.unknown == {}
    assert BROKERAGE not in found.unanchored
    assert held_now(found) == {}
    assert found.today.complete


def test_the_page_lists_each_security_by_what_it_is_called_and_the_cash_last(
    tmp_path: Path, clock: FakeClock
) -> None:
    holdings = (
        FakeHolding("ZQYR", "Yarrow Total Market", Decimal(2), Decimal(100)),
        FakeHolding("ZQAB", "Aster Bond", Decimal(1), Decimal(50)),
    )
    brokerage = replace(FERNHILL.accounts[1], holdings=holdings)
    fresh = connected(
        tmp_path, clock, replace(FERNHILL, accounts=(FERNHILL.accounts[0], brokerage))
    )

    page = shown(fresh.signed_in("avery").get("/net-worth").text)

    assert re.search(
        r"Aster Bond\s*ZQAB.*Yarrow Total Market\s*ZQYR.*Cash\s*Uninvested.*\$9,750\.00",
        page,
        re.DOTALL,
    )


def test_a_member_whose_only_account_is_a_brokerage_is_told_nothing_nobody_established(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Plaid sends a brokerage no transactions, so a member with nothing else connected has
    no spending or income anyone established: not $0, not "nothing paid in", not a month
    without income. What they hold is a figure; what they spent is not known."""
    alone = replace(FERNHILL, accounts=(FERNHILL.accounts[1],))
    fresh = first_day(tmp_path, clock, institutions=(alone,))
    fresh.link("avery", alone.id)
    fresh.map(fresh.signed_in("avery"), {"4242": BROKERAGE})
    until_booked(fresh, BROKERAGE)
    fresh.bookkeeper.recompute(wait=True)
    brokerage = f"/accounts/{_account(fresh, BROKERAGE).id}"

    for viewer in ("avery", "blake"):
        client = fresh.signed_in(viewer)
        for path in ("/", "/you", "/net-worth", "/members/avery", brokerage):
            response = client.get(path)
            if response.status_code == 404:
                continue
            # The one zero somebody established: Avery owes nothing, holding no card or loan.
            page = readable(response.text).replace("$0.00 owed", "")
            assert not UNESTABLISHED.search(page), (viewer, path, UNESTABLISHED.search(page))
            assert "No income that month" not in page, (viewer, path)
    you = " ".join(readable(fresh.signed_in("avery").get("/you").text).split())
    assert "every account of yours here is a brokerage account, so nothing shows" in you
    assert "Brokerage accounts only" in you
    assert "Plaid does not send a brokerage's transactions. Connect" in you
    assert "hearth does not see" not in you
    assert "$10,000" in you


def test_a_brokerage_alone_holds_back_nobody_elses_spending_for_whoever_sees_it(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Avery's only account is a brokerage, which adds nothing to the household's spending:
    so for Blake, who may see Avery's balances and so what kind of account it is, Blake's
    bank decides it, and Avery's total is listed as not known rather than as $0."""
    fresh = _avery_and_blake(tmp_path, clock, (_ALONE,))
    _shares(fresh, {Facet.MONTHLY_SPENDING_TOTAL, Facet.ACCOUNT_BALANCES})

    page = " ".join(readable(fresh.signed_in("blake").get("/").text).split())

    assert re.search(r"Spent, Oct 1.15 \$\s*\d", page)
    assert "History incomplete" not in page
    assert "Avery Total only, not by category Not known" in page


_ALONE = FakeAccount(
    "brk", "Brokerage", "4242", "investment", "brokerage", Decimal("6000.00"), holdings=()
)
"""A brokerage holding $6,000 of cash and nothing else."""

_QUIET = FakeAccount(
    "chk", "Checking", "3131", "depository", "checking", Decimal("6000.00"), transactions=()
)
"""A checking account holding the same, that has sent no history yet."""

_HALVES = (
    replace(_ALONE, current=Decimal("3000.00")),
    replace(_QUIET, current=Decimal("3000.00")),
)

KINDS = {"a brokerage": (_ALONE,), "an account with no history yet": (_QUIET,), "both": _HALVES}
"""What Avery may have connected, each worth the same: they differ only in what kind of
accounts they are."""

LEVELS = {
    "spending": {Facet.MONTHLY_SPENDING_TOTAL},
    "the default": set(sharing.DEFAULT_SHARED),
    "with income": {*sharing.DEFAULT_SHARED, Facet.MONTHLY_INCOME_TOTAL},
    "with categories": {
        *sharing.DEFAULT_SHARED,
        Facet.MONTHLY_INCOME_TOTAL,
        Facet.CATEGORY_BREAKDOWN,
    },
    "all but balances": set(Facet) - {Facet.ACCOUNT_BALANCES},
}
"""What Avery may share with Blake, never balances, which what kind each account is goes with."""


def test_what_kind_of_accounts_a_member_has_never_reaches_whoever_may_not_see_their_balances(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Blake may see Avery's spending total, not Avery's balances. Whether Avery's accounts are
    a brokerage, one with no history yet, or both, Blake's household page reads the same, to
    the byte: Avery's spending is not known yet, and nothing says why in terms of accounts.
    So no line appears, moves, or is reworded by what kind Avery's accounts are."""
    households = {
        kind: _avery_and_blake(tmp_path / str(i), clock, connected)
        for i, (kind, connected) in enumerate(KINDS.items())
    }
    for level, facets in LEVELS.items():
        pages = {}
        for kind, fresh in households.items():
            _shares(fresh, facets)
            pages[kind] = _scrubbed(fresh.signed_in("blake").get("/").text)
        first, *rest = pages.values()
        assert all(page == first for page in rest), level
        text = " ".join(readable(first).split())
        assert "shows hearth what was spent" not in text, level
        assert "Brokerage" not in text, level
        assert re.search(r"Not known yet: hearth does not have all of October", text), level


def test_an_interest_row_on_a_brokerages_cash_leaves_the_months_before_its_report_spent(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Interest on a brokerage's cash makes it an account that moves income, so its months are
    judged for spending and income. Its holdings being known only from the day of the report
    says nothing of what was spent or earned before it, so September stays whole for those;
    its value before the report is still not known, so September's net worth is not."""
    interest = FakeTransaction(
        "Interest earned", Decimal("-1.15"), date(2026, 9, 2), "INCOME_INTEREST_EARNED"
    )
    brokerage = replace(FERNHILL.accounts[1], transactions=(interest,))
    fresh = connected(
        tmp_path, clock, replace(FERNHILL, accounts=(FERNHILL.accounts[0], brokerage))
    )
    found = fresh.books()
    both = [_account(fresh, CHECKING), _account(fresh, BROKERAGE)]
    september = Month(2026, 9)

    assert BROKERAGE in found.flowing
    assert figures.flows_cover(found, both, september)
    assert not figures.covers(found, both, september)


def test_a_deposit_after_the_latest_report_is_added_to_what_it_reported(
    fresh: FirstDay,
) -> None:
    """The brokerage's report says what it held the day it was taken, so a row after that is
    not in it: the deposit is money on top of the report's cash, not taken out of the cash it
    held before, and the report stays asserted as of its own day."""
    reported_on = household.NOW.astimezone().date()
    (item_id,) = fresh.plaid.items()
    fresh.clock.now += timedelta(days=2)
    deposit = FakeTransaction(
        "Deposit from Fernhill Checking",
        Decimal("-1000.00"),
        reported_on + timedelta(days=1),
        "TRANSFER_IN",
    )
    fresh.plaid.change(item_id, "added", deposit, "brk")
    fresh.plaid.fail_next("/investments/holdings/get", "PRODUCT_NOT_READY")

    refresh.sync_then_recompute(fresh.settings, fresh.setup, fresh.bookkeeper, fresh.clock)

    assert fresh.bookkeeper.status.failure is None
    found = fresh.books()
    assert found.positioned == {BROKERAGE: reported_on}
    assert held_now(found)["USD"] == Decimal("244.46") + Decimal("1000.00")
    asserted = [e for e in found.entries if isinstance(e, Balance) and held(e)]
    assert {a.date for a in asserted} == {reported_on + timedelta(days=1)}


def _avery_and_blake(
    tmp_path: Path, clock: FakeClock, connected: tuple[FakeAccount, ...]
) -> FirstDay:
    """Avery with `connected`, at Fernhill, and Blake with a bank account whose month is known."""
    avery = replace(FERNHILL, accounts=connected)
    bank = FakeInstitution("ins_hazel", "Hazel Bank", (FERNHILL.accounts[0],))
    fresh = first_day(tmp_path, clock, institutions=(avery, bank))
    fresh.link("avery", avery.id)
    names = {"4242": BROKERAGE, "3131": CHECKING}
    fresh.map(fresh.signed_in("avery"), {a.mask: names[a.mask] for a in connected})
    fresh.link("blake", bank.id)
    fresh.map(fresh.signed_in("blake"), {"3131": "Assets:Blake:Hazel:Checking"}, owner="blake")
    for account in [*(names[a.mask] for a in connected), "Assets:Blake:Hazel:Checking"]:
        until_booked(fresh, account)
    fresh.bookkeeper.recompute(wait=True)
    return fresh


def _shares(fresh: FirstDay, facets: set[Facet]) -> None:
    """Have Avery share exactly `facets`."""
    conn = fresh.connect()
    try:
        for facet in Facet:
            sharing.choose(
                conn, fresh.member_ids["avery"], facet, shared=facet in facets, now=fresh.clock()
            )
    finally:
        conn.close()


def _scrubbed(page: str) -> str:
    """A page as a reader gets it, less the session's own form token."""
    page = re.sub(r'"X-CSRF-Token": "[0-9a-f]*"', '"X-CSRF-Token": ""', page)
    return re.sub(r'name="csrf" value="[^"]*"', 'name="csrf"', page)


@pytest.mark.parametrize("ready", [True, False], ids=["reported", "not reported yet"])
def test_no_ruling_moves_a_row_onto_a_brokerage_whose_holdings_it_reports(
    tmp_path: Path, clock: FakeClock, ready: bool
) -> None:
    """Its cash is what its brokerage says, and the books assert it: money moved into it by a
    ruling would be counted twice, so the desk refuses the move, as it does for a bank's. So
    it does while the holdings are not in yet, as a ruling made meanwhile would be there when
    they are."""
    fresh = connected(tmp_path, clock, ready=ready)
    avery = fresh.signed_in("avery")

    moved = avery.post(
        "/review/rows/book",
        data={"csrf": fresh.csrf(avery), "review_id": _transfer(fresh), "account": BROKERAGE},
    )

    assert "outcome=banks-account" in moved.headers["location"]


def test_money_moved_onto_it_before_its_holdings_came_fails_the_check_and_says_so(
    fresh: FirstDay,
) -> None:
    """A ruling made before the desk refused it, when the account was not the brokerage's to
    fill: the books would count the transfer twice, so they do not add up to what it reported,
    and its owner is told whose report it is rather than shown a figure that is wrong."""
    before = fresh.books()
    review_id = _transfer(fresh)
    (item,) = [i for i in before.pile if i.review_id == review_id]
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

    fresh.bookkeeper.recompute(wait=True)

    status = fresh.bookkeeper.status
    assert status.books == before
    assert status.failure is not None
    assert status.failure.summary.startswith("The holdings Fernhill Investments reported for")
    assert status.failure.summary.endswith("are not what its transactions add up to.")
    assert status.failure.disagreement is not None
    assert status.failure.disagreement.account == BROKERAGE


def _transfer(fresh: FirstDay) -> str:
    """The review id of checking's transfer to the brokerage."""
    with Feed.open(fresh.settings.feed_database) as found:
        (row,) = found.transactions(_linked(fresh, "3131"))
    return plaid_review_id(row.transaction_id)


def _account(fresh: FirstDay, ledger_account: str) -> accounts.Account:
    conn = fresh.connect()
    try:
        return next(a for a in accounts.all_accounts(conn) if a.ledger_account == ledger_account)
    finally:
        conn.close()


def _linked(fresh: FirstDay, mask: str) -> str:
    conn = fresh.connect()
    try:
        registry = Registry(conn)
        return next(
            a.account_id
            for item in registry.all_items()
            for a in registry.accounts_of(item.item_id)
            if a.mask == mask
        )
    finally:
        conn.close()
