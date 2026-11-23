"""Carrying a matched account on from the imported ledger, the way a household meets it: a
ledger imported as the base, a bank connected through Link, its accounts matched to the base's
with ``hearth match-accounts``, and the books recomputed from then on.

Every institution, account and figure is invented, and every figure below is worked out here by
hand from the rows and the base as written, never by hearth.

The bank, Zq Carry Bank, sends:

- Checking (0101): R1 Aug 3, 40.00 out, which the base holds by hearth's own `source`; R2 Aug
  10, 25.00 out, by an importer's bare `plaid-id`; R3 Aug 30, 100.00 paid to the card, which
  the base holds as one entry dated Aug 20 carrying R3's id; R4 Sep 1, 2,000.00 pay, which the
  base holds with no id at all, by content; and after the base stops, R5 Sep 5, 300.00 paid to
  the card, R6 Sep 12, 12.00 and R7 Oct 2, 30.00.
- Card (2202): K1 Aug 15, 50.00 charged, which the base dated Aug 13, the day it was made; K2
  Aug 31, R3 arriving; K3 Sep 3, 70.00, which the base dated Aug 29, before the card's
  cut-over; K4 Sep 6, R5 arriving; and K5 Sep 20, 15.00.
- A brokerage, which Plaid sends no transactions.

The base opens the checking account with 1,000.00 on Aug 1 and asserts 2,835.00 on Sep 2
(1,000 - 40 - 25 - 100 + 2,000), so the checking account's cut-over is Sep 2: its last
transaction is Sep 1 and its last assertion Sep 2. The card's last entry is K3's, Aug 29, so its
cut-over is Aug 30, and the base says it was 20.00 in credit then (-50 + 100 - 70). The
brokerage holds 1,000.00 from Aug 5.

Its bank says the checking account holds 2,493.00: 2,835 less R5, R6 and R7 (342). The card is
265.00 in credit: what the base says, plus K4's 300, less K5's 15. K2 is not booked again, as
the base holds the payment it is a side of; K3 is not, as the base holds it by content.
"""

import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from beancount.core.data import Balance, Directive, Posting, Transaction

from hearth import accounts, books, carrying, cli, matching
from hearth.books import Books
from hearth.cli import main
from hearth.config import DATA_DIR_VARIABLE, LEDGER_VARIABLE, Settings
from hearth.connections import plaid_source
from hearth.disclosure import Disclosure, rulings_in
from hearth.disclosure.balances import Standing
from hearth.ledger_import import ImportRefused, import_ledger
from hearth.members import find
from hearth.months import Month
from hearth.native.doorway import SCREENS, Doorway
from hearth.plaid.booking import _Legs, _legs
from hearth.plaid.client import Transaction as Row
from hearth.plaid.fake import FakeAccount, FakeInstitution, FakeTransaction
from hearth.plaid.feed import Feed
from hearth.plaid.opening import anchors as opening_anchor
from hearth.plaid.opening import reconciling
from hearth.plaid.registry import Registry
from hearth.review.ids import plaid_review_id, reconciling_review_id
from hearth.review.pile import LEDGER_BANK, RECONCILING, TRANSFER_PENDING
from hearth.review.rulings import RulingStore
from hearth.sharing import Facet
from tests.conftest import FakeClock, run_now
from tests.firstday import FirstDay, first_day
from tests.household import noon
from tests.test_privacy_boundary import readable_routes

TODAY = date(2026, 10, 15)
NOW = noon(TODAY)

CHECKING, CARD, BROKERAGE = "Assets:Zq:Checking", "Liabilities:Zq:Card", "Assets:Zq:Brokerage"
CHECKING_CUT, CARD_CUT, BROKERAGE_CUT = date(2026, 9, 2), date(2026, 8, 30), date(2026, 8, 6)

PAYMENT = "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"
R = {
    "R1": FakeTransaction(
        "Zq Grocer Avocet", Decimal("40.00"), date(2026, 8, 3), "FOOD_AND_DRINK_GROCERIES"
    ),
    "R2": FakeTransaction(
        "Zq Florist Bittern",
        Decimal("25.00"),
        date(2026, 8, 10),
        "GENERAL_MERCHANDISE_OTHER_GENERAL_MERCHANDISE",
    ),
    "R3": FakeTransaction(
        "Payment to Zq card ending 2202", Decimal("100.00"), date(2026, 8, 30), PAYMENT
    ),
    "R4": FakeTransaction(
        "Zq Payroll Curlew", Decimal("-2000.00"), date(2026, 9, 1), "INCOME_WAGES"
    ),
    "R5": FakeTransaction(
        "Payment to Zq card ending 2202", Decimal("300.00"), date(2026, 9, 5), PAYMENT
    ),
    "R6": FakeTransaction(
        "Zq Bakery Dunlin", Decimal("12.00"), date(2026, 9, 12), "FOOD_AND_DRINK_COFFEE"
    ),
    "R7": FakeTransaction(
        "Zq Diner Egret", Decimal("30.00"), date(2026, 10, 2), "FOOD_AND_DRINK_RESTAURANT"
    ),
}
K = {
    "K1": FakeTransaction(
        "Zq Bookshop Fulmar",
        Decimal("50.00"),
        date(2026, 8, 15),
        "GENERAL_MERCHANDISE_BOOKSTORES_AND_NEWSSTANDS",
    ),
    "K2": FakeTransaction("Payment Thank You", Decimal("-100.00"), date(2026, 8, 31), PAYMENT),
    "K3": FakeTransaction(
        "Zq Hardware Gannet", Decimal("70.00"), date(2026, 9, 3), "HOME_IMPROVEMENT_HARDWARE"
    ),
    "K4": FakeTransaction("Payment Thank You", Decimal("-300.00"), date(2026, 9, 6), PAYMENT),
    "K5": FakeTransaction(
        "Zq Hardware Heron", Decimal("15.00"), date(2026, 9, 20), "HOME_IMPROVEMENT_HARDWARE"
    ),
}
CHECKING_HELD = Decimal("2493.00")
CARD_CREDIT = Decimal("265.00")

BANK = FakeInstitution(
    "ins_zq_carry",
    "Zq Carry Bank",
    (
        FakeAccount(
            "chk",
            "ZQ CHECKING",
            "0101",
            "depository",
            "checking",
            CHECKING_HELD,
            transactions=tuple(R.values()),
        ),
        FakeAccount(
            "card",
            "ZQ CARD",
            "2202",
            "credit",
            "credit card",
            -CARD_CREDIT,
            transactions=tuple(K.values()),
        ),
        FakeAccount(
            "ira",
            "ZQ BROKERAGE",
            "0303",
            "investment",
            "brokerage",
            Decimal("1000"),
            transactions=(),
        ),
    ),
)

SECRETS = ("Avocet", "Bittern", "Curlew", "Dunlin", "Egret", "Fulmar", "Gannet", "Heron")
"""Words in Avery's payees, which nobody else may ever be shown."""


@dataclass
class Household:
    fresh: FirstDay
    item: str
    ids: dict[str, str]
    """Each bank row's Plaid id, by its name above."""
    accounts: dict[str, str]
    """Each connected account's Plaid id, by its key."""
    ledger: Path

    @property
    def settings(self) -> Settings:
        return self.fresh.settings

    def books(self) -> Books:
        self.fresh.bookkeeper.recompute(wait=True)
        return self.fresh.books()

    def sync(self) -> Books:
        """Press "Sync now", as Avery would, and wait for the books it brings."""
        avery = self.fresh.signed_in("avery")
        response = avery.post(
            f"/connect/items/{self.item}/sync", data={"csrf": self.fresh.csrf(avery)}
        )
        assert response.status_code == 303
        return self.books()

    def add(self, key: str, row: FakeTransaction) -> str:
        return self.fresh.plaid.change(self.item, "added", row, key)


def _base(ids: dict[str, str], item: str) -> str:
    def entry(day: date, account: str, units: str, against: str, meta: str = "") -> str:
        return f'{day} * "Zq" "Invented"\n{meta}  {account}  {units} USD\n  {against}\n\n'

    def source(name: str) -> str:
        return f'  source: "{plaid_source(item, ids[name])}"\n'

    text = 'option "operating_currency" "USD"\n\n'
    for account in (CHECKING, CARD, BROKERAGE, "Equity:Zq:Opening", "Expenses:Zq:Spent"):
        text += f"2026-08-01 open {account} USD\n"
    text += "2026-08-01 open Income:Zq:Pay USD\n\n"
    text += entry(date(2026, 8, 1), CHECKING, "1000.00", "Equity:Zq:Opening")
    text += entry(date(2026, 8, 3), CHECKING, "-40.00", "Expenses:Zq:Spent", source("R1"))
    text += entry(date(2026, 8, 5), BROKERAGE, "1000.00", "Equity:Zq:Opening")
    text += entry(
        date(2026, 8, 10), CHECKING, "-25.00", "Expenses:Zq:Spent", f'  plaid-id: "{ids["R2"]}"\n'
    )
    text += entry(date(2026, 8, 13), CARD, "-50.00", "Expenses:Zq:Spent")
    text += entry(date(2026, 8, 20), CARD, "100.00", CHECKING, source("R3"))
    text += entry(date(2026, 8, 29), CARD, "-70.00", "Expenses:Zq:Spent")
    text += entry(date(2026, 9, 1), CHECKING, "2000.00", "Income:Zq:Pay")
    text += "2026-09-02 balance Assets:Zq:Checking  2835.00 USD\n"
    return text


def _rows(fresh: FirstDay, account_id: str) -> list[Row]:
    with Feed.open(fresh.settings.feed_database) as fed:
        return list(fed.transactions(account_id))


def build(
    tmp_path: Path,
    clock: FakeClock,
    *,
    match: tuple[str, ...] = ("chk", "card", "ira"),
    imported: bool = True,
    institution: FakeInstitution = BANK,
    extra: str = "",
    base: str | None = None,
    claims: tuple[str, ...] = (CHECKING, CARD, BROKERAGE),
) -> Household:
    """The household on Oct 15: its base imported, Avery's bank connected, every account of
    the base claimed by Avery, and the accounts in `match` matched to the base's."""
    clock.now = NOW
    fresh = first_day(tmp_path, clock, institutions=(institution,))
    item = fresh.link("avery", institution.id)
    conn = fresh.connect()
    try:
        linked = Registry(conn).accounts_of(item)
    finally:
        conn.close()
    ids_of = {a.account_id.removeprefix(f"{item}-"): a.account_id for a in linked}
    names = {**{n: ("chk", r) for n, r in R.items()}, **{n: ("card", r) for n, r in K.items()}}
    names = {n: (key, r) for n, (key, r) in names.items() if key in ids_of}
    ids = {}
    for name, (key, row) in names.items():
        found = [
            r for r in _rows(fresh, ids_of[key]) if r.day == row.day and r.amount == row.amount
        ]
        assert len(found) <= 1, name
        if found:
            ids[name] = found[0].transaction_id
    if institution is BANK:
        assert len(ids) == len(names), "the bank sent every row"
    folder = tmp_path / "kept-before"
    folder.mkdir()
    (folder / "main.beancount").write_text((_base(ids, item) if base is None else base) + extra)
    if imported:
        import_ledger(fresh.settings, folder / "main.beancount", now=NOW)
    conn = fresh.connect()
    try:
        for account in claims:
            accounts.claim(
                conn,
                ledger_account=account,
                name=account.rsplit(":", 1)[1],
                owners=[fresh.member_ids["avery"]],
                now=NOW,
            )
    finally:
        conn.close()
    household = Household(fresh, item, ids, ids_of, folder / "main.beancount")
    if imported and match:
        targets = {"chk": CHECKING, "card": CARD, "ira": BROKERAGE}
        matching.confirm(fresh.settings, [(ids_of[key], targets[key]) for key in match], now=NOW)
    return household


@pytest.fixture
def carried(tmp_path: Path, clock: FakeClock) -> Household:
    return build(tmp_path, clock)


@pytest.fixture
def shell(carried: Household, monkeypatch: pytest.MonkeyPatch) -> Household:
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(carried.settings.data_dir))
    monkeypatch.delenv(LEDGER_VARIABLE, raising=False)
    monkeypatch.setattr(cli, "system_clock", lambda: NOW)
    return carried


def _own(found: books.Books) -> list[Transaction]:
    """The transactions hearth booked, not the base's, and not a balance it derived."""
    from hearth import ownbooks
    from hearth.plaid.opening import anchors

    return [e for e in ownbooks.own(found.entries) if isinstance(e, Transaction) and not anchors(e)]


def _sources(entry: Transaction) -> set[str]:
    found = {str(p.meta["source"]) for p in entry.postings if p.meta and "source" in p.meta}
    return found or {str(entry.meta["source"])}


def _held(entries: tuple[Directive, ...], account: str, day: date) -> Decimal:
    """What `account` holds at the end of `day`, summed from the books by hand."""
    return sum(
        (
            p.units.number
            for e in entries
            if isinstance(e, Transaction) and e.date <= day
            for p in e.postings
            if p.account == account and p.units is not None and p.units.number is not None
        ),
        Decimal(0),
    )


def _number(posting: Posting) -> Decimal:
    assert posting.units is not None
    assert isinstance(posting.units.number, Decimal)
    return posting.units.number


def _standings(answer: Mapping[str, object]) -> dict[str, object]:
    """Each account the app lists, by name, with its standing."""
    groups = answer["groups"]
    assert isinstance(groups, list)
    return {a["name"]: a["standing"] for g in groups for a in g["accounts"]}


def _reconciling(found: books.Books) -> list[Transaction]:
    return [e for e in found.entries if isinstance(e, Transaction) and reconciling(e)]


# The cut-over, and which rows the base holds.


def _ledger(text: str) -> carrying.Held:
    from beancount import loader

    entries, errors, _ = loader.load_string(text)
    assert not errors, errors
    return carrying.read(entries)


@pytest.mark.parametrize(
    ("text", "day"),
    [
        # The day after its last transaction.
        (
            '2026-01-01 open Assets:A\n2026-03-04 * "x"\n  Assets:A  1 USD\n  Equity:E\n',
            date(2026, 3, 5),
        ),
        # A balance assertion later than that holds at the start of its day: its day is new.
        (
            '2026-01-01 open Assets:A\n2026-03-04 * "x"\n  Assets:A  1 USD\n  Equity:E\n'
            "2026-03-09 balance Assets:A  1 USD\n",
            date(2026, 3, 9),
        ),
        # A pad, and the assertion it fills up to.
        (
            "2026-01-01 open Assets:A\n2026-02-01 pad Assets:A Equity:E\n"
            "2026-02-03 balance Assets:A  5 USD\n",
            date(2026, 2, 3),
        ),
        # Nothing on it but its open.
        ("2026-01-01 open Assets:A\n", date(2026, 1, 1)),
    ],
)
def test_the_cut_over_is_where_the_bases_own_record_of_an_account_stops(
    text: str, day: date
) -> None:
    held = _ledger("2026-01-01 open Equity:E\n" + text)
    assert carrying.cut_over(held, "Assets:A") == day
    assert carrying.cut_over(held, "Assets:Elsewhere") is None


def test_rows_the_base_holds_are_found_by_both_ids_and_by_content(carried: Household) -> None:
    found = books.household_entries(carried.settings).carried
    checking, card = found[CHECKING], found[CARD]
    ids = carried.ids

    assert (checking.cut_over, card.cut_over) == (CHECKING_CUT, CARD_CUT)
    assert checking.by_id == {ids["R1"], ids["R2"], ids["R3"]}
    assert checking.by_content == {ids["R4"]}
    # K3 is dated after the card's cut-over by its bank, and before it by the base.
    assert card.by_content == {ids["K1"], ids["K3"]}
    assert card.by_id == frozenset()
    assert checking.before == card.before == frozenset()
    assert checking.opening == {"USD": Decimal("2835.00")}
    assert card.opening == {"USD": Decimal("-20.00")}
    assert BROKERAGE not in found


# What the books hold.


def test_only_the_rows_the_base_does_not_hold_are_booked_from_the_cut_over_on(
    carried: Household,
) -> None:
    found = carried.books()
    ids = carried.ids
    booked = {s.rsplit(":", 1)[1] for e in _own(found) for s in _sources(e)}

    assert booked == {ids[n] for n in ("R5", "R6", "R7", "K4", "K5")}
    by_source = {frozenset(_sources(e)): e for e in _own(found)}
    transfer = by_source[frozenset(plaid_source(carried.item, ids[n]) for n in ("R5", "K4"))]
    assert transfer.flag == "*"
    assert {p.account for p in transfer.postings} == {CHECKING, CARD}
    others = [e for e in _own(found) if e is not transfer]
    assert all(e.flag == "!" and "review-id" in e.meta for e in others)
    assert all(e.date >= CHECKING_CUT for e in _own(found) if e.postings[0].account == CHECKING)
    # No opening: the base says what each held.
    assert not [
        e for e in found.entries if isinstance(e, Transaction) and e.narration == "Opening balance"
    ]
    assert found.carried == {CHECKING: CHECKING_CUT, CARD: CARD_CUT}
    assert _reconciling(found) == []


def test_each_carried_account_is_proved_against_its_bank_on_every_recompute(
    carried: Household,
) -> None:
    found = carried.books()
    asserted = {
        e.account: e.amount.number
        for e in found.entries
        if isinstance(e, Balance) and e.meta.get("balance") == "current"
    }

    assert asserted == {CHECKING: CHECKING_HELD, CARD: CARD_CREDIT}
    assert _held(found.entries, CHECKING, TODAY) == CHECKING_HELD
    assert _held(found.entries, CARD, TODAY) == CARD_CREDIT
    assert found.reconciling == frozenset()
    assert carried.books().entries == found.entries


def test_the_books_are_those_of_the_same_history_kept_wholly_by_hearth(
    carried: Household, tmp_path: Path, clock: FakeClock
) -> None:
    """The same bank, kept by hearth from nothing: each month end agrees to the cent, by the
    figures worked out by hand, wherever both books date every row alike. The card's August
    differs only by K3, which the base dated Aug 29 and its bank Sep 3."""
    ours = carried.books()
    fresh = first_day(tmp_path / "kept", clock, institutions=(BANK,))
    fresh.link("avery", BANK.id)
    fresh.map(fresh.signed_in("avery"), {"0101": CHECKING, "2202": CARD})
    fresh.bookkeeper.recompute(wait=True)
    theirs = fresh.books()
    by_hand = {
        (CHECKING, date(2026, 8, 31)): Decimal("835.00"),
        (CHECKING, date(2026, 9, 30)): Decimal("2523.00"),
        (CHECKING, TODAY): CHECKING_HELD,
        (CARD, date(2026, 9, 30)): CARD_CREDIT,
        (CARD, TODAY): CARD_CREDIT,
    }

    for (account, day), figure in by_hand.items():
        assert _held(ours.entries, account, day) == figure, (account, day)
        assert _held(theirs.entries, account, day) == figure, (account, day)
    assert _held(ours.entries, CARD, date(2026, 8, 31)) == Decimal("-20.00")
    assert _held(theirs.entries, CARD, date(2026, 8, 31)) == Decimal("50.00")


# Rows the base lacks, and where the two disagree.


def test_a_row_before_the_cut_over_the_base_lacks_is_counted_and_never_booked(
    shell: Household, capsys: pytest.CaptureFixture[str]
) -> None:
    lacked = FakeTransaction("Zq Kiosk Ibis", Decimal("10.00"), date(2026, 8, 25))
    shell.add("chk", lacked)
    shell.fresh.plaid.report(shell.item, "chk", CHECKING_HELD - Decimal("10.00"))
    found = shell.sync()

    assert all(
        "Ibis" not in (e.narration or "")
        for e in found.entries
        if isinstance(e, Transaction) and not reconciling(e)
    )
    assert all("Ibis" not in item.narration for item in found.pile)
    assert len(books.household_entries(shell.settings).carried[CHECKING].before) == 1
    # The ledger and the bank now disagree by exactly that row, as of the cut-over.
    (entry,) = _reconciling(found)
    assert (entry.date, _number(entry.postings[0])) == (CHECKING_CUT, Decimal("-10.00"))

    main(["match-accounts"])
    out = capsys.readouterr().out
    assert "    Carried on from Sep 2, 2026: before then the base's, from then on the bank's" in out
    assert "    Before then and not in the base, so never brought in: 1, Aug 25, 2026" in out
    assert "Ibis" not in out
    assert "10.00" not in out


def test_where_the_ledger_and_the_bank_disagree_one_entry_says_by_how_much(
    carried: Household,
) -> None:
    """The bank counts two rows, 3.00 and 5.00, that have not reached hearth yet: the books
    hold one reconciling entry for the 8.00, the same on every recompute, which shrinks as each
    row arrives and is gone once both have."""
    carried.fresh.plaid.report(carried.item, "chk", CHECKING_HELD - Decimal("8.00"))
    found = carried.sync()
    (entry,) = _reconciling(found)
    review_id = reconciling_review_id(CHECKING, CHECKING_CUT)

    assert entry.flag == "!"
    assert entry.date == CHECKING_CUT
    assert [(p.account, _number(p)) for p in entry.postings] == [
        (CHECKING, Decimal("-8.00")),
        (LEDGER_BANK, Decimal("8.00")),
    ]
    assert entry.meta["review-id"] == review_id
    assert entry.meta["review-group"] == RECONCILING
    assert "Zq Carry Bank disagree by this much as of Sep 2, 2026" in (entry.narration or "")
    assert found.reconciling == {CHECKING}
    assert [i.review_id for i in found.pile if i.reconciling] == [review_id]
    again = carried.books()
    assert _reconciling(again) == [entry]

    carried.add("chk", FakeTransaction("Zq Kiosk Jacana", Decimal("3.00"), date(2026, 10, 5)))
    (shrunk,) = _reconciling(carried.sync())
    assert _number(shrunk.postings[0]) == Decimal("-5.00")
    assert shrunk.meta["review-id"] == review_id

    carried.add("chk", FakeTransaction("Zq Kiosk Kestrel", Decimal("5.00"), date(2026, 10, 6)))
    fixed = carried.sync()
    assert _reconciling(fixed) == []
    assert fixed.reconciling == frozenset()


def test_a_difference_shows_in_review_and_is_confirmed_there(carried: Household) -> None:
    carried.fresh.plaid.report(carried.item, "chk", CHECKING_HELD - Decimal("8.00"))
    carried.sync()
    avery = carried.fresh.signed_in("avery")
    page = avery.get("/review", params={"view": "rows"}).text
    review_id = reconciling_review_id(CHECKING, CHECKING_CUT)

    assert "The imported ledger and Zq Carry Bank disagree by this much as of Sep 2, 2026" in page
    assert "Confirm it once you know why they differ" in page
    # Its bank's balance is not shown as matching while it waits.
    answer = Doorway(carried.settings, carried.fresh.bookkeeper, "avery").answer("accounts")
    standings = _standings(answer)
    assert standings["Checking"] == Standing.UNCHECKED.value
    assert standings["Card"] == Standing.MATCHES.value

    response = avery.post(
        "/review/rows/book",
        data={
            "csrf": carried.fresh.csrf(avery),
            "review_id": review_id,
            "shown": LEDGER_BANK,
            "account": LEDGER_BANK,
            "back": "",
            "focus": "",
        },
    )
    assert response.status_code == 303
    found = carried.books()
    (entry,) = _reconciling(found)
    assert entry.flag == "*"
    assert found.reconciling == frozenset()
    answer = Doorway(carried.settings, carried.fresh.bookkeeper, "avery").answer("accounts")
    standings = _standings(answer)
    assert standings["Checking"] == Standing.MATCHES.value

    # A confirmation is of that difference: another one is a question again.
    carried.fresh.plaid.report(carried.item, "chk", CHECKING_HELD - Decimal("9.00"))
    assert carried.sync().reconciling == {CHECKING}


def test_a_difference_is_never_settled_as_a_group_nor_excluded(carried: Household) -> None:
    from hearth.review.desk import BanksRow, NotOneDecision

    carried.fresh.plaid.report(carried.item, "chk", CHECKING_HELD - Decimal("8.00"))
    carried.sync()
    conn = carried.fresh.connect()
    try:
        viewer = find(conn, "avery")
        assert viewer is not None
        desk = Disclosure.load(conn, viewer, carried.fresh.bookkeeper.status).review_desk(
            RulingStore(conn), carried.fresh.clock
        )
        review_id = reconciling_review_id(CHECKING, CHECKING_CUT)
        with pytest.raises(NotOneDecision):
            desk.accept_group(RECONCILING, {review_id: LEDGER_BANK})
        with pytest.raises(BanksRow):
            desk.reject(review_id)
    finally:
        conn.close()


# A brokerage, which is not carried.


def test_a_matched_brokerage_is_not_carried_and_says_where_its_data_ends(
    carried: Household,
) -> None:
    found = carried.books()

    assert BROKERAGE not in found.carried
    assert not [e for e in _own(found) if any(p.account == BROKERAGE for p in e.postings)]
    # Its data ends where the base's does: nothing its connection sends is in the books, so
    # its connection vouches for nothing of it. The checking account and the card, which
    # hearth books, are vouched for through the last sync.
    assert BROKERAGE not in found.synced
    assert found.account_ends[BROKERAGE] == BROKERAGE_CUT - timedelta(days=1)
    assert found.synced[CHECKING] == found.synced[CARD] == TODAY


# Pairing across the cut-over.


@pytest.mark.parametrize(
    ("other", "carried", "other_carried", "legs"),
    [
        (carrying.Status.NEW, True, True, _Legs.TRANSFER),
        (carrying.Status.NEW, False, True, _Legs.TRANSFER),
        (carrying.Status.HELD, True, True, _Legs.HELD),
        # The base holds the other side against an account of its own, never this one.
        (carrying.Status.HELD, False, True, _Legs.ALONE),
        (carrying.Status.BEFORE, True, True, _Legs.ALONE),
        (carrying.Status.BEFORE, False, True, _Legs.ALONE),
    ],
)
def test_a_pair_across_the_cut_over_is_booked_by_what_the_base_holds_of_it(
    other: carrying.Status, carried: bool, other_carried: bool, legs: _Legs
) -> None:
    assert _legs(other, carried, other_carried) is legs


def test_a_payment_the_base_holds_is_not_booked_again_from_either_side(
    carried: Household,
) -> None:
    """R3 and K2 pair; the base holds the payment by R3's id, though K2, dated after the
    card's cut-over, is nowhere in it by content: neither is booked, and the card still
    agrees with its bank to the cent."""
    found = carried.books()
    booked = {s.rsplit(":", 1)[1] for e in _own(found) for s in _sources(e)}

    assert carried.ids["K2"] not in booked
    assert carried.ids["R3"] not in booked
    assert not [e for e in _own(found) if any(p.account == TRANSFER_PENDING for p in e.postings)]


def test_a_leg_whose_partner_falls_before_its_cut_over_is_booked_alone(
    carried: Household,
) -> None:
    """A payment the base lacks on the checking account, made the day before its cut-over and
    arriving on the card after the card's: only the card's side is in the books from now on,
    against the transfer still pending."""
    carried.add(
        "chk",
        FakeTransaction(
            "Payment to Zq card ending 2202", Decimal("40.00"), date(2026, 9, 1), PAYMENT
        ),
    )
    received = carried.add(
        "card", FakeTransaction("Payment Thank You", Decimal("-40.00"), date(2026, 9, 2), PAYMENT)
    )
    carried.fresh.plaid.report(carried.item, "chk", CHECKING_HELD - Decimal("40.00"))
    carried.fresh.plaid.report(carried.item, "card", -CARD_CREDIT - Decimal("40.00"))
    found = carried.sync()

    (alone,) = [e for e in _own(found) if plaid_source(carried.item, received) in _sources(e)]
    assert [p.account for p in alone.postings] == [CARD, TRANSFER_PENDING]
    assert alone.flag == "!"
    # The checking account's side is one the ledger never kept: the two disagree by it.
    (entry,) = _reconciling(found)
    assert entry.postings[0] == entry.postings[0]._replace(account=CHECKING)
    assert _number(entry.postings[0]) == Decimal("-40.00")


# Plaid revising and withdrawing a row, and rulings.


def test_a_row_revised_or_withdrawn_after_the_cut_over_is_booked_as_it_now_stands(
    carried: Household,
) -> None:
    ids = carried.ids
    plaid = carried.fresh.plaid
    plaid.change(
        carried.item,
        "modified",
        replace(R["R6"], amount=Decimal("13.50")),
        "chk",
        transaction_id=ids["R6"],
    )
    plaid.change(carried.item, "removed", R["R7"], "chk", transaction_id=ids["R7"])
    plaid.report(carried.item, "chk", CHECKING_HELD - Decimal("1.50") + Decimal("30.00"))
    found = carried.sync()
    by_id = {s.rsplit(":", 1)[1]: e for e in _own(found) for s in _sources(e)}

    assert _number(by_id[ids["R6"]].postings[0]) == Decimal("-13.50")
    assert ids["R7"] not in by_id
    assert _reconciling(found) == []


def test_a_ruling_on_a_carried_row_holds_through_recompute_and_sync(carried: Household) -> None:
    carried.books()
    avery = carried.fresh.signed_in("avery")
    review_id = plaid_review_id(carried.ids["R6"])
    response = avery.post(
        "/review/rows/book",
        data={
            "csrf": carried.fresh.csrf(avery),
            "review_id": review_id,
            "shown": "",
            "account": "Expenses:Treats",
            "back": "",
            "focus": "",
        },
    )
    assert response.status_code == 303

    for found in (carried.books(), carried.sync(), carried.books()):
        (row,) = [e for e in _own(found) if e.meta.get("review-id") == review_id]
        assert (row.flag, row.postings[1].account) == ("*", "Expenses:Treats")
    assert carried.books().begun == {CHECKING}


# A connected account hearth kept itself while the base held its rows too.


def _filed(tmp_path: Path, clock: FakeClock) -> Household:
    """The checking account filed on the Connect page as an account hearth keeps itself,
    before the base was imported, with a ruling on one of its rows; the card matched."""
    household = build(tmp_path, clock, match=("card",))
    conn = household.fresh.connect()
    try:
        kept = accounts.register(
            conn,
            ledger_account="Assets:Avery:Zq:Checking",
            name="Everyday",
            owners=[household.fresh.member_ids["avery"]],
        )
        Registry(conn).confirm(household.accounts["chk"], kept.id, by=None, now=NOW)
    finally:
        conn.close()
    household.books()
    avery = household.fresh.signed_in("avery")
    response = avery.post(
        "/review/rows/book",
        data={
            "csrf": household.fresh.csrf(avery),
            "review_id": plaid_review_id(household.ids["R6"]),
            "shown": "",
            "account": "Expenses:Treats",
            "back": "",
            "focus": "",
        },
    )
    assert response.status_code == 303
    return household


def test_an_account_hearth_kept_moves_onto_the_bases_when_the_base_holds_the_rest(
    tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    household = _filed(tmp_path, clock)
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(household.settings.data_dir))
    monkeypatch.delenv(LEDGER_VARIABLE, raising=False)
    monkeypatch.setattr(cli, "system_clock", lambda: NOW)
    before = household.books()
    assert _held(before.entries, "Assets:Avery:Zq:Checking", TODAY) == CHECKING_HELD

    main(["match-accounts", "--confirm", f"{household.accounts['chk']}={CHECKING}"])
    found = household.books()

    conn = household.fresh.connect()
    try:
        names = {a.ledger_account for a in accounts.all_accounts(conn)}
    finally:
        conn.close()
    assert "Assets:Avery:Zq:Checking" not in names
    assert found.carried == {CHECKING: CHECKING_CUT, CARD: CARD_CUT}
    assert _held(found.entries, CHECKING, TODAY) == CHECKING_HELD
    review_id = plaid_review_id(household.ids["R6"])
    (row,) = [e for e in _own(found) if e.meta.get("review-id") == review_id]
    assert (row.flag, row.postings[0].account, row.postings[1].account) == (
        "*",
        CHECKING,
        "Expenses:Treats",
    )


def test_an_account_hearth_kept_is_not_moved_when_the_base_lacks_a_row_of_it(
    tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    household = _filed(tmp_path, clock)
    household.add("chk", FakeTransaction("Zq Kiosk Ibis", Decimal("10.00"), date(2026, 8, 25)))
    household.fresh.plaid.report(household.item, "chk", CHECKING_HELD - Decimal("10.00"))
    household.sync()
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(household.settings.data_dir))
    monkeypatch.delenv(LEDGER_VARIABLE, raising=False)

    with pytest.raises(SystemExit) as refused:
        main(["match-accounts", "--confirm", f"{household.accounts['chk']}={CHECKING}"])

    said = str(refused.value.code)
    assert "1 of the transactions hearth has booked on Assets:Avery:Zq:Checking is dated" in said
    assert "before Sep 2, 2026" in said
    assert household.books().carried == {CARD: CARD_CUT}


def test_the_connect_page_never_moves_a_kept_account_onto_the_bases(
    tmp_path: Path, clock: FakeClock
) -> None:
    from hearth.plaid.connector import Mapping, Refusal, Refused

    household = _filed(tmp_path, clock)
    conn = household.fresh.connect()
    try:
        member = find(conn, "avery")
        assert member is not None
        mine = Disclosure.load(conn, member, household.fresh.bookkeeper.status).connector(
            conn,
            database=household.settings.database,
            feed_path=household.settings.feed_database,
            data_dir=household.settings.data_dir,
            setup=household.fresh.setup,
            clock=household.fresh.clock,
            runner=run_now,
        )
        sent = [Mapping(household.accounts["chk"], CHECKING, "", confirm=True)]
        near = mine.mapping(sent).near
        with pytest.raises(Refused) as refused:
            mine.confirm(sent, near_seen=near)
    finally:
        conn.close()
    assert refused.value.reason is Refusal.CARRY_BY_COMMAND


# Fixed once a member has ruled on a carried row, and correctable until then.


def _rule(household: Household, review_id: str, account: str, shown: str = "") -> None:
    """Avery decides one row in Review, as the page sends it."""
    household.books()
    avery = household.fresh.signed_in("avery")
    response = avery.post(
        "/review/rows/book",
        data={
            "csrf": household.fresh.csrf(avery),
            "review_id": review_id,
            "shown": shown,
            "account": account,
            "back": "",
            "focus": "",
        },
    )
    assert response.status_code == 303


def test_rows_booked_alone_fix_nothing(shell: Household) -> None:
    """Hearth has booked rows on both carried accounts, but nobody has ruled on one: nothing
    is fixed yet, since every one of them is derived afresh on the next recompute."""
    found = shell.books()
    assert _own(found)
    assert found.begun == frozenset()
    assert books.begun_in(shell.settings) == frozenset()


def test_a_match_and_the_base_are_fixed_once_a_carried_row_is_ruled_on(shell: Household) -> None:
    _rule(shell, plaid_review_id(shell.ids["R6"]), "Expenses:Treats")
    assert shell.books().begun == {CHECKING}

    with pytest.raises(SystemExit) as refused:
        main(["match-accounts", "--confirm", f"{shell.accounts['chk']}={BROKERAGE}"])
    said = str(refused.value.code)
    assert "a decision has been made in Review on its transactions there" in said
    assert "Its match is fixed from then on" in said
    with pytest.raises(ImportRefused, match="is not replaced"):
        import_ledger(shell.settings, shell.ledger, now=NOW)


def _wrongly_matched(tmp_path: Path, clock: FakeClock) -> Household:
    """The checking account matched by mistake to the base's brokerage account, an account
    under Assets nobody else feeds: its rows after the brokerage's cut-over are booked there,
    and the brokerage's 1,000.00 and those rows disagree with the bank's 2,493.00."""
    household = build(tmp_path, clock, match=("card",))
    matching.confirm(household.settings, [(household.accounts["chk"], BROKERAGE)], now=NOW)
    return household


def test_a_wrong_match_with_rows_booked_is_put_right_and_leaves_nothing_behind(
    tmp_path: Path, clock: FakeClock
) -> None:
    household = _wrongly_matched(tmp_path, clock)
    wrong = household.books()
    assert wrong.carried == {BROKERAGE: BROKERAGE_CUT, CARD: CARD_CUT}
    assert [e for e in _own(wrong) if any(p.account == BROKERAGE for p in e.postings)]
    # The card, whose payments now pair with rows on the wrong account, disagrees too.
    assert {e.postings[0].account for e in _reconciling(wrong)} == {BROKERAGE, CARD}
    assert BROKERAGE in wrong.synced
    assert wrong.begun == frozenset()

    matching.confirm(household.settings, [(household.accounts["chk"], CHECKING)], now=NOW)
    found = household.books()

    assert found.carried == {CHECKING: CHECKING_CUT, CARD: CARD_CUT}
    assert not [e for e in _own(found) if any(p.account == BROKERAGE for p in e.postings)]
    assert not [
        e
        for e in found.entries
        if isinstance(e, Balance) and e.account == BROKERAGE and opening_anchor(e)
    ]
    assert _reconciling(found) == []
    assert BROKERAGE not in found.synced
    assert not [i for i in found.pile if i.account == BROKERAGE]
    # The brokerage holds what the base says, and the checking account what its bank says.
    assert _held(found.entries, BROKERAGE, TODAY) == Decimal("1000.00")
    assert _held(found.entries, CHECKING, TODAY) == CHECKING_HELD
    assert books.household_entries(household.settings).carried.keys() == {CHECKING, CARD}


def test_confirming_a_wrong_matchs_difference_fixes_the_match(
    tmp_path: Path, clock: FakeClock
) -> None:
    """A ruling on the reconciling entry is a decision on the account's transactions too."""
    household = _wrongly_matched(tmp_path, clock)
    household.books()
    (entry,) = [
        e for e in _reconciling(household.fresh.books()) if e.postings[0].account == BROKERAGE
    ]
    _rule(household, str(entry.meta["review-id"]), LEDGER_BANK, LEDGER_BANK)
    assert household.books().begun == {BROKERAGE}

    with pytest.raises(matching.MatchRefused, match="a decision has been made in Review"):
        matching.confirm(household.settings, [(household.accounts["chk"], CHECKING)], now=NOW)


def test_a_match_may_change_until_carrying_begins(tmp_path: Path, clock: FakeClock) -> None:
    """Matched to the brokerage's account first by mistake, with nothing booked there: it may
    be put right."""
    household = build(tmp_path, clock, match=())
    matching.confirm(household.settings, [(household.accounts["ira"], CHECKING)], now=NOW)
    assert household.books().begun == frozenset()

    matching.confirm(household.settings, [(household.accounts["ira"], BROKERAGE)], now=NOW)
    matching.confirm(household.settings, [(household.accounts["chk"], CHECKING)], now=NOW)
    assert household.books().carried == {CHECKING: CHECKING_CUT}


# What does not change.


def test_a_ledger_inherited_where_it_is_is_left_wholly_to_itself(
    tmp_path: Path, clock: FakeClock
) -> None:
    household = build(tmp_path, clock, imported=False)
    settings = replace(household.settings, ledger=household.ledger)
    conn = household.fresh.connect()
    try:
        registry = Registry(conn)
        claimed = {a.ledger_account: a for a in accounts.all_accounts(conn)}
        registry.confirm(household.accounts["chk"], claimed[CHECKING].id, by=None, now=NOW)
    finally:
        conn.close()

    found = books.from_household(settings, rulings=rulings_in)(NOW)

    assert found.carried == {}
    assert not [e for e in _own(found) if any(p.account == CHECKING for p in e.postings)]


# Nobody else learns anything of it.


@pytest.mark.parametrize("shared", [frozenset(), frozenset(Facet)], ids=["nothing", "everything"])
def test_nobody_else_sees_a_carried_account_or_its_difference_with_its_bank(
    carried: Household, shared: frozenset[Facet]
) -> None:
    carried.fresh.plaid.report(carried.item, "chk", CHECKING_HELD - Decimal("8.00"))
    carried.sync()
    conn = carried.fresh.connect()
    try:
        avery = carried.fresh.member_ids["avery"]
        for facet in Facet:
            conn.execute(
                "INSERT INTO sharing_preferences (member_id, facet, shared, updated_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT (member_id, facet)"
                " DO UPDATE SET shared = excluded.shared",
                (avery, facet.value, facet in shared, "2026-01-01"),
            )
        account_ids = [a.id for a in accounts.all_accounts(conn)]
    finally:
        conn.close()
    blake = carried.fresh.signed_in("blake")
    values = {"account_id": account_ids, "item_id": [carried.item], "username": ["avery"]}
    seen = []
    for route in readable_routes():
        paths = [route.path]
        for name in route.param_convertors:
            paths = [p.replace(f"{{{name}}}", v) for p in paths for v in values.get(name, [""])]
        for path in paths:
            for query in ({}, {"view": "rows"}, {"group": RECONCILING}):
                seen.append(blake.get(path, params=query).text)
    door = Doorway(carried.settings, carried.fresh.bookkeeper, "blake")
    for screen in SCREENS:
        for given in ({}, *({"id": i} for i in account_ids)):
            seen.append(repr(door.answer(screen, given)))
    everything = "\n".join(seen)

    assert "disagree by this much" not in everything
    assert "Ledger-Bank-Difference" not in everything
    if not shared:
        for secret in SECRETS:
            assert secret not in everything


@pytest.fixture(params=["Pacific/Kiritimati", "Pacific/Pago_Pago"])
def zone(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("TZ", request.param)
    time.tzset()
    yield request.param
    monkeypatch.undo()
    time.tzset()


def test_carrying_reads_the_same_in_every_zone(zone: str, tmp_path: Path, clock: FakeClock) -> None:
    found = build(tmp_path, clock).books()

    assert found.carried == {CHECKING: CHECKING_CUT, CARD: CARD_CUT}
    assert _held(found.entries, CHECKING, TODAY) == CHECKING_HELD
    assert found.reconciling == frozenset()


def test_the_import_says_from_when_each_matched_account_is_carried_on(
    tmp_path: Path, clock: FakeClock
) -> None:
    """The checking account filed under the very name the ledger uses before the ledger was
    imported: the import's check says it is carried on, from which day, and how many of the
    bank's transactions before then the ledger does not hold."""
    from hearth.ledger_import import describe

    household = build(tmp_path, clock, imported=False)
    conn = household.fresh.connect()
    try:
        claimed = {a.ledger_account: a for a in accounts.all_accounts(conn)}
        Registry(conn).confirm(household.accounts["chk"], claimed[CHECKING].id, by=None, now=NOW)
    finally:
        conn.close()
    household.add("chk", FakeTransaction("Zq Kiosk Ibis", Decimal("10.00"), date(2026, 8, 25)))
    household.sync()

    lines = describe(import_ledger(household.settings, household.ledger, now=NOW))

    assert (
        "  Carried on from Sep 2, 2026: Assets:Zq:Checking; 1 of the bank's transactions before"
        " then is not in it and never brought in"
    ) in lines
    assert not any("Ibis" in line or "10.00" in line for line in lines)


# No sequence of actions hearth allows makes a recompute fail because of carrying.


def _moved_onto_the_card(tmp_path: Path, clock: FakeClock) -> Household:
    """The checking account kept by hearth itself, and one of its rows, R7, moved in Review
    onto the base's card account, which nothing feeds yet, as a member may."""
    household = build(tmp_path, clock, match=())
    conn = household.fresh.connect()
    try:
        kept = accounts.register(
            conn,
            ledger_account="Assets:Avery:Zq:Checking",
            name="Everyday",
            owners=[household.fresh.member_ids["avery"]],
        )
        Registry(conn).confirm(household.accounts["chk"], kept.id, by=None, now=NOW)
    finally:
        conn.close()
    _rule(household, plaid_review_id(household.ids["R7"]), CARD)
    found = household.books()
    (row,) = [
        e for e in _own(found) if e.meta.get("review-id") == plaid_review_id(household.ids["R7"])
    ]
    assert row.postings[1].account == CARD
    return household


def test_an_account_rows_were_moved_onto_is_never_carried_on(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Carried on, the card's bank balance would be asserted beside a row its bank never
    sent, and every recompute would fail: the match is refused, saying why."""
    household = _moved_onto_the_card(tmp_path, clock)

    with pytest.raises(matching.MatchRefused, match="Rows are moved into Liabilities:Zq:Card"):
        matching.confirm(household.settings, [(household.accounts["card"], CARD)], now=NOW)

    assert household.books().carried == {}
    assert household.fresh.bookkeeper.status.failure is None


def test_the_connect_page_never_files_a_connection_into_an_account_rows_were_moved_onto(
    tmp_path: Path, clock: FakeClock
) -> None:
    from hearth.plaid.connector import Mapping, Refusal, Refused

    household = _moved_onto_the_card(tmp_path, clock)
    conn = household.fresh.connect()
    try:
        member = find(conn, "avery")
        assert member is not None
        mine = Disclosure.load(conn, member, household.fresh.bookkeeper.status).connector(
            conn,
            database=household.settings.database,
            feed_path=household.settings.feed_database,
            data_dir=household.settings.data_dir,
            setup=household.fresh.setup,
            clock=household.fresh.clock,
            runner=run_now,
        )
        sent = [Mapping(household.accounts["card"], CARD, "", confirm=True)]
        near = mine.mapping(sent).near
        with pytest.raises(Refused) as refused:
            mine.confirm(sent, near_seen=near)
    finally:
        conn.close()

    assert refused.value.reason is Refusal.MOVED_INTO
    assert household.books().carried == {}


def test_no_row_may_be_moved_onto_a_carried_account(carried: Household) -> None:
    """Whether or not its bank's balance is in yet, a carried account is its bank's to fill."""
    from hearth.review.desk import BanksAccount

    carried.books()
    conn = carried.fresh.connect()
    try:
        viewer = find(conn, "avery")
        assert viewer is not None
        desk = Disclosure.load(conn, viewer, carried.fresh.bookkeeper.status).review_desk(
            RulingStore(conn), carried.fresh.clock
        )
        with pytest.raises(BanksAccount):
            desk.set_account(plaid_review_id(carried.ids["R6"]), CARD)
    finally:
        conn.close()


def test_a_name_moved_twice_or_moved_onto_a_name_moved_before_keeps_every_ruling(
    tmp_path: Path,
) -> None:
    """A kept account's name, reused by an account filed after the first was moved, is moved
    again; and a name moved onto one moved before: each earlier name still counts."""
    from hearth import db

    path = tmp_path / "hearth.sqlite3"
    db.initialise(path)
    conn = db.connect(path)
    try:
        store = RulingStore(conn)
        store.carry_over("Assets:Avery:Checking", CHECKING, at=NOW)
        store.carry_over("Assets:Avery:Checking", "Assets:Zq:Savings", at=NOW)
        store.carry_over("Assets:Avery:Old", "Assets:Avery:Checking", at=NOW)
        assert store.formerly() == {
            CHECKING: ("Assets:Avery:Checking", "Assets:Avery:Old"),
            "Assets:Zq:Savings": ("Assets:Avery:Checking", "Assets:Avery:Old"),
            "Assets:Avery:Checking": ("Assets:Avery:Old",),
        }
    finally:
        conn.close()


# Content matching.


def _bank_row(transaction_id: str, day: date, amount: str, currency: str = "USD") -> Row:
    return Row(
        transaction_id, "acct", day, None, Decimal(amount), currency, None, None, None, None,
        pending=False, pending_transaction_id=None,
    )  # fmt: skip


def test_a_row_is_held_by_content_only_in_its_own_currency() -> None:
    held = _ledger(
        "2026-01-01 open Equity:E\n2026-01-01 open Assets:A\n"
        '2026-03-04 * "x"\n  Assets:A  -12.00 EUR\n  Equity:E\n'
        '2026-03-05 * "x"\n  Assets:A  -7.00 USD\n  Equity:E\n'
    )
    plan = carrying.carry(
        held,
        "Assets:A",
        [_bank_row("eur", date(2026, 3, 4), "12.00"), _bank_row("usd", date(2026, 3, 5), "7.00")],
    )

    assert plan.by_content == {"usd"}
    assert plan.before == {"eur"}


SPARE = (
    '2026-09-01 * "Zq" "A charge and its refund the bank never posted"\n'
    "  Assets:Zq:Checking  -25.00 USD\n  Expenses:Zq:Spent\n\n"
    '2026-09-01 * "Zq" "Refund"\n  Assets:Zq:Checking  25.00 USD\n  Expenses:Zq:Spent\n\n'
)
"""Two entries the base holds that are no bank row of the checking account's."""


def test_a_frequent_charge_held_by_the_wrong_entry_shows_in_the_difference(
    tmp_path: Path, clock: FakeClock
) -> None:
    """A 25.00 charge the bank posts on Sep 5, after the cut-over, falls within a week of a
    25.00 entry of the base's that no bank row claims, and content holds it wrongly: it is not
    booked, and the books are short of it. That is never silent: the checking account's
    difference with its bank is exactly that charge, in Review."""
    household = build(tmp_path, clock, match=("chk", "card"), extra=SPARE)
    household.add("chk", FakeTransaction("Zq Florist Bittern", Decimal("25.00"), date(2026, 9, 5)))
    household.fresh.plaid.report(household.item, "chk", CHECKING_HELD - Decimal("25.00"))
    found = household.sync()

    (entry,) = _reconciling(found)
    assert (entry.date, _number(entry.postings[0])) == (CHECKING_CUT, Decimal("-25.00"))
    assert [i.review_id for i in found.pile if i.reconciling] == [entry.meta["review-id"]]


# A cut-over that has not come yet.


EVENING = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
"""Evening in Greenwich on Oct 15: already Oct 16 at UTC+14, still Oct 15 at UTC-11."""

FORECAST = "2026-10-16 balance Assets:Zq:Checking  2835.00 USD\n"
"""A balance the ledger forecast for Oct 16, which puts the checking account's cut-over there."""


def test_a_cut_over_after_today_is_not_carried_on_until_it_comes(
    zone: str, tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    """On the household's own day, by hand: Oct 16 at UTC+14, where the cut-over has come and
    the account is carried on; Oct 15 at UTC-11, where it has not, so the account is the
    base's alone, its history ending on Oct 15, and the command says so."""
    household = build(tmp_path, clock, extra=FORECAST)
    household.fresh.clock.now = EVENING
    found = household.books()
    come = zone == "Pacific/Kiritimati"

    assert (CHECKING in found.carried) is come
    if come:
        assert found.carried[CHECKING] == date(2026, 10, 16)
    else:
        assert household.books().carried.keys() == {CARD}
        assert not [e for e in _own(found) if any(p.account == CHECKING for p in e.postings)]
        assert not [e for e in _reconciling(found) if e.postings[0].account == CHECKING]
        monkeypatch.setenv(DATA_DIR_VARIABLE, str(household.settings.data_dir))
        monkeypatch.delenv(LEDGER_VARIABLE, raising=False)
        monkeypatch.setattr(cli, "system_clock", lambda: EVENING)
        main(["match-accounts"])
        assert (
            "    Not carried on yet: the base has an entry or a balance assertion on it dated"
            " after today, so carrying it on would begin on Oct 16, 2026"
        ) in capsys.readouterr().out


def test_the_import_says_when_a_matched_account_is_not_carried_on_yet(
    tmp_path: Path, clock: FakeClock
) -> None:
    from hearth.ledger_import import describe

    household = build(
        tmp_path,
        clock,
        imported=False,
        extra="2026-12-01 balance Assets:Zq:Checking  2835.00 USD\n",
    )
    conn = household.fresh.connect()
    try:
        claimed = {a.ledger_account: a for a in accounts.all_accounts(conn)}
        Registry(conn).confirm(household.accounts["chk"], claimed[CHECKING].id, by=None, now=NOW)
    finally:
        conn.close()

    lines = describe(import_ledger(household.settings, household.ledger, now=NOW))

    assert (
        "  Not carried on yet: Assets:Zq:Checking, which the ledger has an entry or a balance"
        " assertion on dated after today; carrying it on would begin on Dec 1, 2026"
    ) in lines
    assert CHECKING not in household.books().carried


def test_a_carried_account_its_bank_cannot_prove_is_not_known(carried: Household) -> None:
    """Its bank stops reporting a balance: like an account hearth keeps itself in the same
    state, its balance is not known, says why to its owner, and no total shows it as known."""
    from hearth.plaid.opening import Unknown

    carried.fresh.plaid.report(carried.item, "chk", None)
    found = carried.sync()

    assert found.unknown[CHECKING] is Unknown.NO_BALANCE
    assert CHECKING in found.unanchored
    assert not [
        e
        for e in found.entries
        if isinstance(e, Balance) and e.account == CHECKING and opening_anchor(e)
    ]
    answer = Doorway(carried.settings, carried.fresh.bookkeeper, "avery").answer("accounts")
    groups = answer["groups"]
    assert isinstance(groups, list)
    checking = next(a for g in groups for a in g["accounts"] if a["name"] == "Checking")
    assert (checking["standing"], checking["why"]) == (Standing.NOT_KNOWN.value, "no-balance")
    assert checking["balance"]["is_known"] is False
    # It is still its bank's to fill: no row may be moved onto it meanwhile.
    assert CARD in found.carried


# A base that stops before the bank's history begins.

EARLY = (
    'option "operating_currency" "USD"\n\n'
    + "".join(
        f"2026-05-01 open {a} USD\n"
        for a in (CHECKING, CARD, BROKERAGE, "Equity:Zq:Opening", "Expenses:Zq:Spent")
    )
    + '\n2026-05-01 * "Zq" "Opening"\n  Assets:Zq:Checking  1000.00 USD\n  Equity:Zq:Opening\n'
)
"""A base that knows the checking account only on May 1, when it held 1,000.00: its cut-over
is May 2, and the bank's history, beginning Aug 3, says nothing of May, June or July."""


def test_months_neither_the_base_nor_the_bank_holds_never_read_as_complete(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Its bank says the checking account holds 50.00 more than the base's 1,000.00 and every
    row it sent: money that moved between May 2 and Aug 2, which neither holds. Those months
    are incomplete on the pages and in the app, the account's page says where the gap is, and
    the difference says it includes them."""
    household = build(tmp_path, clock, match=("chk",), base=EARLY, claims=(CHECKING,))
    household.fresh.plaid.report(household.item, "chk", CHECKING_HELD + Decimal("50.00"))
    found = household.sync()

    assert found.carried == {CHECKING: date(2026, 5, 2)}
    assert found.unseen_between == {CHECKING: (date(2026, 5, 2), date(2026, 8, 2))}
    (entry,) = _reconciling(found)
    assert _number(entry.postings[0]) == Decimal("50.00")
    assert (
        "including what moved from May 2, 2026 to Aug 2, 2026, which neither the ledger nor Zq"
        " Carry Bank holds"
    ) in (entry.narration or "")

    conn = household.fresh.connect()
    try:
        viewer = find(conn, "avery")
        assert viewer is not None
        view = Disclosure.load(conn, viewer, household.fresh.bookkeeper.status).household()
        account_id = next(a.id for a in accounts.all_accounts(conn) if a.ledger_account == CHECKING)
    finally:
        conn.close()
    covered = {m.month: m.covered for m in view.left_over.months}
    assert covered[Month(2026, 6)] is False
    assert covered[Month(2026, 7)] is False
    assert covered[Month(2026, 9)] is True

    answer = Doorway(household.settings, household.fresh.bookkeeper, "avery").answer("overview")
    history = answer["history"]
    assert isinstance(history, list)
    days = {date.fromisoformat(point["day"]) for point in history}
    assert date(2026, 6, 30) not in days
    assert date(2026, 7, 31) not in days
    assert date(2026, 9, 30) in days

    page = household.fresh.signed_in("avery").get(f"/accounts/{account_id}").text
    assert (
        "Neither the imported ledger nor its bank holds what moved from May 2, 2026 to Aug 2,"
        " 2026, so those months read as incomplete"
    ) in page


# Second review: what a ruling decides now, not what one ever said.


def test_an_account_rows_were_moved_off_again_is_matched(tmp_path: Path, clock: FakeClock) -> None:
    """R7, moved onto the card in Review and then decided again as spending: nothing is on the
    card any more, so the card is matched and carried, though the first ruling stays on
    record for good."""
    household = _moved_onto_the_card(tmp_path, clock)
    _rule(household, plaid_review_id(household.ids["R7"]), "Expenses:Dining")

    matching.confirm(household.settings, [(household.accounts["card"], CARD)], now=NOW)

    found = household.books()
    assert CARD in found.carried
    assert household.fresh.bookkeeper.status.failure is None


def test_the_refusal_names_the_rows_a_ruling_moves_onto_the_account(
    tmp_path: Path, clock: FakeClock
) -> None:
    household = _moved_onto_the_card(tmp_path, clock)
    review_id = plaid_review_id(household.ids["R7"])

    with pytest.raises(matching.MatchRefused) as refused:
        matching.confirm(household.settings, [(household.accounts["card"], CARD)], now=NOW)

    said = str(refused.value)
    assert f"They are {review_id}, on Assets:Avery:Zq:Checking, Oct 2, 2026" in said
    assert "Decide them again in Review" in said
    assert "Zq Diner" not in said


def _record(household: Household, review_id: str, action: str, account: str | None) -> None:
    """A ruling on record, written as the store keeps it, as one made long ago would be."""
    from hearth.review.rulings import Action, Scope

    found = household.books()
    (item,) = [i for i in found.pile if i.review_id == review_id]
    conn = household.fresh.connect()
    try:
        RulingStore(conn).record(
            member_id=household.fresh.member_ids["avery"],
            scope=Scope.ROW,
            group=None,
            action=Action(action),
            account=account,
            note=None,
            rows={review_id: None},
            says={review_id: item.says},
            at=NOW,
        )
    finally:
        conn.close()


def test_a_ruling_that_would_put_a_row_on_a_carried_account_leaves_it_unreviewed(
    tmp_path: Path, clock: FakeClock
) -> None:
    """However a ruling comes to decide again, as one superseded once its bank revises the
    row, it never puts a posting on a carried account nor takes one of its bank's rows away:
    the row stays unreviewed and says why, and every recompute passes."""
    from hearth.review.decision import KEPT_OFF, State

    household = build(tmp_path, clock, match=("card",))
    conn = household.fresh.connect()
    try:
        kept = accounts.register(
            conn,
            ledger_account="Assets:Avery:Zq:Checking",
            name="Everyday",
            owners=[household.fresh.member_ids["avery"]],
        )
        Registry(conn).confirm(household.accounts["chk"], kept.id, by=None, now=NOW)
    finally:
        conn.close()
    moved, rejected = plaid_review_id(household.ids["R7"]), plaid_review_id(household.ids["K5"])
    _record(household, moved, "account", CARD)
    _record(household, rejected, "reject", None)

    found = household.books()
    assert household.fresh.bookkeeper.status.failure is None
    (row,) = [e for e in _own(found) if e.meta.get("review-id") == moved]
    assert (row.flag, row.postings[1].account) == ("!", row.postings[1].account)
    assert row.postings[1].account != CARD
    assert [e for e in _own(found) if e.meta.get("review-id") == rejected]
    conn = household.fresh.connect()
    try:
        viewer = find(conn, "avery")
        assert viewer is not None
        desk = Disclosure.load(conn, viewer, household.fresh.bookkeeper.status).review_desk(
            RulingStore(conn), household.fresh.clock
        )
        for review_id in (moved, rejected):
            decision = desk.item(review_id).decision
            assert (decision.state, decision.reason) == (State.UNREVIEWED, KEPT_OFF)
    finally:
        conn.close()


def test_an_account_whose_cut_over_has_not_come_is_its_banks_in_review(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Matched, and held back by a balance its ledger dated next year: no row may be moved
    onto it meanwhile, as its bank's balance will be asserted against it once carried."""
    from hearth.review.desk import BanksAccount

    household = build(tmp_path, clock, extra=AHEAD)
    found = household.books()
    assert CHECKING not in found.carried
    conn = household.fresh.connect()
    try:
        viewer = find(conn, "avery")
        assert viewer is not None
        desk = Disclosure.load(conn, viewer, household.fresh.bookkeeper.status).review_desk(
            RulingStore(conn), household.fresh.clock
        )
        with pytest.raises(BanksAccount):
            desk.set_account(plaid_review_id(household.ids["K5"]), CHECKING)
    finally:
        conn.close()


AHEAD = "2027-03-01 balance Assets:Zq:Checking  2835.00 USD\n"
"""A balance the ledger forecast for next March, which holds the checking account back."""


def test_an_account_held_back_by_its_ledger_never_reads_as_current(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Its connection still syncs, but its rows after the base are in neither book: the base
    knows the checking account through Sep 1 today, so September does not read as whole, and
    its page says what holds it back."""
    household = build(tmp_path, clock, match=("chk",), extra=AHEAD, claims=(CHECKING,))
    found = household.books()

    assert found.held_back == {CHECKING: (date(2026, 9, 1), date(2027, 3, 1))}
    assert found.synced.get(CHECKING, date.min) <= date(2026, 9, 1)
    conn = household.fresh.connect()
    try:
        viewer = find(conn, "avery")
        assert viewer is not None
        view = Disclosure.load(conn, viewer, household.fresh.bookkeeper.status).household()
        account_id = next(a.id for a in accounts.all_accounts(conn) if a.ledger_account == CHECKING)
    finally:
        conn.close()
    covered = {m.month: m.covered for m in view.left_over.months}
    assert covered[Month(2026, 9)] is False
    assert covered[Month(2026, 8)] is True
    page = household.fresh.signed_in("avery").get(f"/accounts/{account_id}").text
    assert (
        "Its imported ledger holds an entry on it dated ahead, on Mar 1, 2027, so Hearth brings"
        " in its bank's transactions only from then. Until then its data ends on Sep 1, 2026"
    ) in page


def test_a_row_that_names_no_currency_is_held_by_its_amount_alone() -> None:
    held = _ledger(
        "2026-01-01 open Equity:E\n2026-01-01 open Assets:A\n"
        '2026-03-04 * "x"\n  Assets:A  -12.00 USD\n  Equity:E\n'
    )
    row = Row(
        "bare", "acct", date(2026, 3, 5), None, Decimal("12.00"), None, None, None, None, None,
        pending=False, pending_transaction_id=None,
    )  # fmt: skip

    assert carrying.carry(held, "Assets:A", [row]).by_content == {"bare"}


def test_the_connect_page_reads_rulings_as_they_stand_when_the_form_is_sent(
    carried: Household,
) -> None:
    """A ruling on a carried row made after the books were last worked out still fixes the
    account's match: the page asks the rulings themselves, not the books."""
    from hearth.plaid.connector import Mapping, Refusal, Refused

    carried.books()
    _record(carried, plaid_review_id(carried.ids["R6"]), "account", "Expenses:Treats")
    assert carried.fresh.books().begun == frozenset()
    conn = carried.fresh.connect()
    try:
        accounts.register(
            conn,
            ledger_account="Assets:Avery:Zq:Spare",
            name="Spare",
            owners=[carried.fresh.member_ids["avery"]],
        )
    finally:
        conn.close()
    conn = carried.fresh.connect()
    try:
        member = find(conn, "avery")
        assert member is not None
        mine = Disclosure.load(conn, member, carried.fresh.bookkeeper.status).connector(
            conn,
            database=carried.settings.database,
            feed_path=carried.settings.feed_database,
            data_dir=carried.settings.data_dir,
            setup=carried.fresh.setup,
            clock=carried.fresh.clock,
            runner=run_now,
        )
        sent = [Mapping(carried.accounts["chk"], "Assets:Avery:Zq:Spare", "", confirm=True)]
        near = mine.mapping(sent).near
        with pytest.raises(Refused) as refused:
            mine.confirm(sent, near_seen=near)
    finally:
        conn.close()
    assert refused.value.reason is Refusal.CARRIED


LATE = FakeInstitution(
    "ins_zq_carry",
    "Zq Carry Bank",
    (
        FakeAccount(
            "chk", "ZQ CHECKING", "0101", "depository", "checking", Decimal("-12.00"),
            transactions=(
                FakeTransaction("Zq Grocer Avocet", Decimal("12.00"), date(2026, 9, 8)),
            ),
        ),
    ),
)  # fmt: skip
"""A checking account whose first charge comes a week after the base opened it."""

EMPTY = 'option "operating_currency" "USD"\n\n' + "".join(
    f"2026-09-01 open {a} USD\n" for a in (CHECKING, CARD, BROKERAGE)
)
"""A base that opens the checking account on Sep 1 and holds nothing on it."""


def test_a_quiet_week_after_the_cut_over_is_quiet_not_unseen(
    tmp_path: Path, clock: FakeClock
) -> None:
    household = build(
        tmp_path, clock, match=("chk",), base=EMPTY, institution=LATE, claims=(CHECKING,)
    )
    found = household.books()

    assert found.carried == {CHECKING: date(2026, 9, 1)}
    assert found.unseen_between == {}
    assert _reconciling(found) == []


def test_a_leg_booked_alone_becomes_a_transfer_once_its_partner_is_carried(
    tmp_path: Path, clock: FakeClock
) -> None:
    """R5, on the checking account hearth keeps itself, pays a card nothing feeds yet, so it
    is booked alone and a member says where it went. Matching the card later pairs it with
    the card's own row: it is one transfer, as both banks say, and the ruling stays on record,
    deciding nothing, as for any leg whose other side arrives later (``docs/pairing.md``)."""
    household = build(tmp_path, clock, match=())
    conn = household.fresh.connect()
    try:
        kept = accounts.register(
            conn,
            ledger_account="Assets:Avery:Zq:Checking",
            name="Everyday",
            owners=[household.fresh.member_ids["avery"]],
        )
        Registry(conn).confirm(household.accounts["chk"], kept.id, by=None, now=NOW)
    finally:
        conn.close()
    review_id = plaid_review_id(household.ids["R5"])
    _rule(household, review_id, "Expenses:Card")
    assert household.books().begun == frozenset()

    matching.confirm(household.settings, [(household.accounts["card"], CARD)], now=NOW)
    found = household.books()

    assert household.fresh.bookkeeper.status.failure is None
    assert not [e for e in _own(found) if e.meta.get("review-id") == review_id]
    (transfer,) = [
        e for e in _own(found) if plaid_source(household.item, household.ids["R5"]) in _sources(e)
    ]
    assert transfer.flag == "*"
    assert {p.account for p in transfer.postings} == {"Assets:Avery:Zq:Checking", CARD}
