"""Matching each connected account to the imported ledger's account it is, the way a household
meets it: a ledger imported as the base, banks connected through Link, and an admin running
``hearth match-accounts`` to see what the base says each connected account is, and to confirm
it. Every institution, account and figure is invented (``tests.ledgers``)."""

import itertools
import random
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from beancount import loader
from beancount.core.data import Directive

from hearth import accounts, books, cli, matching
from hearth.accounts import Account
from hearth.base import Base
from hearth.cli import main
from hearth.config import DATA_DIR_VARIABLE, LEDGER_VARIABLE, Settings
from hearth.connections import plaid_source, transaction_of
from hearth.disclosure import Disclosure, rulings_in
from hearth.ledger_import import import_ledger
from hearth.matching import (
    Candidate,
    Connected,
    Facing,
    How,
    Stretch,
    Unproposed,
    clearly,
    facing,
    most_pairs,
    placed,
    propose,
    read,
)
from hearth.members import Member, MemberId, find
from hearth.months import Month
from hearth.native.doorway import Doorway
from hearth.plaid.client import Transaction
from hearth.plaid.feed import Feed
from hearth.plaid.registry import LinkedAccount, Registry
from tests import ledgers
from tests.conftest import FakeClock
from tests.firstday import FirstDay, first_day
from tests.household import noon

TODAY = date(2026, 11, 22)
NOW = noon(TODAY)
"""The clock reads Nov 22, in whatever zone the tests run (`tests.household.noon`)."""
EVENING = datetime(2026, 11, 22, 18, 0, tzinfo=UTC)
"""Evening in Greenwich: already Nov 23 at UTC+14, still Nov 22 at UTC-11."""

PAYEES = (
    "Zq Grocer",
    "Zq Bookshop",
    "Zq Mortgage",
    "Zq Savings Interest",
    "Zq Hardware",
    "Zq Diner",
    "Zq Florist",
    "Zq Utilities",
    "Zq Bakery",
    "Payment Thank You",
    "Payment to Zq card",
    "Transfer from Zq Bank",
    "Invented",
)
AMOUNTS = ("41.10", "300.00", "18.25", "500.00", "7.77", "250.00", "1000.00", "1,000", "70.00")


@dataclass
class Household:
    """The invented household on the day it matches: its base imported, every bank connected,
    nothing matched yet."""

    fresh: FirstDay
    ids: dict[str, str]
    """Each connected account's Plaid id, by ``"<institution id>/<account key>"``."""
    rows: dict[str, list[Transaction]]
    removed: list[str]
    """The Plaid ids of the credit union's accounts on the connection removed before."""
    ledger: Path
    """The ledger the base was imported from, which a hearth may inherit instead."""

    @property
    def settings(self) -> Settings:
        return self.fresh.settings


def _remove(fresh: FirstDay, item_id: str) -> None:
    avery = fresh.signed_in("avery")
    response = avery.post(f"/connect/items/{item_id}/remove", data={"csrf": fresh.csrf(avery)})
    assert response.status_code == 303


def _connected(fresh: FirstDay) -> tuple[dict[str, str], dict[str, str]]:
    """Each working connection's Item by institution, and each account it reaches by key."""
    conn = fresh.connect()
    try:
        registry = Registry(conn)
        items, ids = {}, {}
        for item in registry.all_items():
            if not item.active:
                continue
            assert item.institution_id is not None
            items[item.institution_id] = item.item_id
            for linked in registry.accounts_of(item.item_id):
                key = linked.account_id.removeprefix(f"{item.item_id}-")
                ids[f"{item.institution_id}/{key}"] = linked.account_id
    finally:
        conn.close()
    return items, ids


def _rows(fresh: FirstDay, account_id: str) -> list[Transaction]:
    with Feed.open(fresh.settings.feed_database) as fed:
        return [r for r in fed.transactions(account_id) if not r.pending]


def build(
    tmp_path: Path,
    clock: FakeClock,
    *,
    imported: bool = True,
    claims: Mapping[str, tuple[str, ...]] = ledgers.CLAIMS,
) -> Household:
    """The household, its ledger imported as the base, or, unless `imported`, written beside
    its data directory and imported nowhere, for a hearth to inherit at HEARTH_LEDGER; each of
    the base's accounts in `claims` claimed by its owners."""
    clock.now = NOW
    fresh = first_day(tmp_path, clock, institutions=ledgers.MATCH_INSTITUTIONS)
    old = fresh.link("avery", "ins_zq_union")
    _, before_ids = _connected(fresh)
    removed = [before_ids["ins_zq_union/chk"]]
    before = _rows(fresh, removed[0])
    _remove(fresh, old)
    for username, institution in (
        ("avery", "ins_zq_union"),
        ("avery", "ins_zq_bank"),
        ("avery", "ins_zq_twin"),
        ("blake", "ins_zq_blake"),
    ):
        fresh.link(username, institution)
    items, ids = _connected(fresh)
    rows = {key: _rows(fresh, account_id) for key, account_id in ids.items()}
    assert all(rows[key] for key in ids if key != "ins_zq_bank/ira"), "every bank sent its rows"
    folder = tmp_path / "kept-before"
    folder.mkdir()
    (folder / "main.beancount").write_text(
        ledgers.matched_base(rows, items, before)["main.beancount"]
    )
    if imported:
        import_ledger(fresh.settings, folder / "main.beancount", now=NOW)
    conn = fresh.connect()
    try:
        for ledger_account, owners in claims.items():
            accounts.claim(
                conn,
                ledger_account=ledger_account,
                name=ledger_account.rsplit(":", 1)[1],
                owners=[fresh.member_ids[o] for o in owners],
                now=NOW,
            )
    finally:
        conn.close()
    return Household(fresh, ids, rows, removed, folder / "main.beancount")


@pytest.fixture
def household(tmp_path: Path, clock: FakeClock) -> Household:
    return build(tmp_path, clock)


@pytest.fixture
def shell(household: Household, monkeypatch: pytest.MonkeyPatch) -> Household:
    """An admin's shell over the household's data directory, with a clock that reads NOW."""
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(household.settings.data_dir))
    monkeypatch.delenv(LEDGER_VARIABLE, raising=False)
    monkeypatch.setattr(cli, "system_clock", lambda: NOW)
    return household


def _surveyed(household: Household) -> dict[str, matching.Surveyed]:
    by_id = {account_id: key for key, account_id in household.ids.items()}
    return {
        by_id[s.proposal.connected.account.account_id]: s
        for s in matching.survey(household.settings).accounts
    }


def _mapping(household: Household) -> dict[str, str | None]:
    """Where each connected account is filed, by key: what the mapping page records."""
    conn = household.fresh.connect()
    try:
        names = {a.id: a.ledger_account for a in accounts.all_accounts(conn)}
        registry = Registry(conn)
        return {
            key: names.get(linked.hearth_account or "")
            for key, account_id in household.ids.items()
            if (linked := registry.account(account_id)) is not None
        }
    finally:
        conn.close()


def _ownership(household: Household) -> list[tuple[str, frozenset[MemberId]]]:
    conn = household.fresh.connect()
    try:
        return sorted((a.ledger_account, a.owners) for a in accounts.all_accounts(conn))
    finally:
        conn.close()


def _said(day: date) -> str:
    return f"{day:%b} {day.day}, {day.year}"


# What the base proposes, from the ledger alone.


def test_each_connected_account_is_proposed_from_the_ledger_itself(household: Household) -> None:
    found = {
        key: (s.proposal.proposed, s.proposal.how, s.proposal.unproposed)
        for key, s in _surveyed(household).items()
    }

    assert found == {
        # Hearth's own `source`.
        "ins_zq_bank/chk": ("Assets:Zq:Checking", How.ID, None),
        "ins_zq_bank/card": ("Liabilities:Zq:Card", How.ID, None),
        # An importer's bare `plaid-id`, with no Item: proposed though nobody claimed it yet.
        "ins_zq_bank/loan": ("Liabilities:Zq:Mortgage", How.ID, None),
        "ins_zq_blake/chk": ("Assets:Zq:Blake:Checking", How.ID, None),
        # Joint, so Blake's connection may be matched to it.
        "ins_zq_blake/joint": ("Assets:Zq:Joint", How.ID, None),
        # Connected again under new ids, and kept from statements with no id at all.
        "ins_zq_union/chk": ("Assets:Zq:Union", How.CONTENT, None),
        "ins_zq_blake/card": ("Liabilities:Zq:Blake:Card", How.CONTENT, None),
        # Plaid sends a brokerage no transactions; the base never kept the savings.
        "ins_zq_bank/ira": (None, None, Unproposed.NOTHING_SENT),
        "ins_zq_bank/sav": (None, None, Unproposed.UNCLEAR),
        # Two of the base's accounts hold its rows nearly alike.
        "ins_zq_twin/card": (None, None, Unproposed.UNCLEAR),
        # Blake's alone, though Avery's login reaches it.
        "ins_zq_bank/blakes": (None, How.ID, Unproposed.NOT_THEIRS),
    }
    assert household.removed[0] not in household.ids.values()


def test_ids_say_how_many_agree_and_that_none_dissent(household: Household) -> None:
    found = _surveyed(household)
    checking = found["ins_zq_bank/chk"].proposal.ids
    card = found["ins_zq_bank/card"].proposal.ids

    # Nine of the ten grocery rows, the base lacking one, and the payment to the card.
    assert checking.on == {"Assets:Zq:Checking": Stretch(10, date(2026, 2, 3), ledgers.CARD_PAID)}
    assert (checking.unplaced, checking.dissent) == (0, 0)
    assert card.on == {"Liabilities:Zq:Card": Stretch(6, date(2026, 2, 10), date(2026, 5, 21))}
    assert found["ins_zq_bank/blakes"].proposal.account == "Assets:Zq:Blake:Savings"


def test_a_card_payment_carrying_one_sides_id_is_that_side_alone(household: Household) -> None:
    """The entry holds the card's posting and the checking account's, of opposite signs: the
    checking account's row finds the checking account's, never the card's, and the card's own
    row, whose id the entry does not carry, is held by content."""
    held = read(books.load_ledger(_base(household)).entries)
    payment = next(r for r in household.rows["ins_zq_bank/chk"] if r.name == ledgers.PAYMENT.name)
    received = next(r for r in household.rows["ins_zq_bank/card"] if r.amount < 0)

    assert [p.account for p in placed(held, payment)] == ["Assets:Zq:Checking"]
    assert placed(held, received) == ()
    card = facing(held, "Liabilities:Zq:Card", household.rows["ins_zq_bank/card"])
    assert (card.by_id, card.by_content) == (6, 1)


def test_a_bank_connected_again_is_proposed_by_what_its_rows_hold(household: Household) -> None:
    union = _surveyed(household)["ins_zq_union/chk"].proposal

    assert union.ids.found == 0
    assert union.content[0] == Candidate("Assets:Zq:Union", 8, 8)
    assert all(c.matched == 0 for c in union.content[1:])


def test_two_accounts_holding_its_rows_nearly_alike_propose_neither(household: Household) -> None:
    twin = _surveyed(household)["ins_zq_twin/card"].proposal

    assert [(c.account, c.matched) for c in twin.content[:2]] == [
        ("Liabilities:Zq:CardA", 6),
        ("Liabilities:Zq:CardB", 5),
    ]
    assert twin.proposed is None


# What carrying each account on will meet.


def test_what_carrying_on_will_meet_is_counted_against_the_base(household: Household) -> None:
    held = read(books.load_ledger(_base(household)).entries)
    checking = facing(held, "Assets:Zq:Checking", household.rows["ins_zq_bank/chk"])
    since = ledgers.SINCE

    assert checking == Facing(
        "Assets:Zq:Checking",
        rows=Stretch(14, date(2026, 2, 3), since[-1].day),
        by_id=10,
        by_content=0,
        missing=Stretch(1, ledgers.LACKED.day, ledgers.LACKED.day),
        after=Stretch(3, since[0].day, since[-1].day),
        last_entry=ledgers.CARD_PAID + timedelta(days=1),
        last_balance=ledgers.LAST_ASSERTED,
        last_pad=ledgers.PADDED_ON,
    )


def test_a_ledger_kept_from_statements_is_read_on_the_day_each_row_posted(
    household: Household,
) -> None:
    """The base books each charge two days before it posted, saying when it posted: every row
    is held, though the last the bank sent is dated after the base's last entry."""
    held = read(books.load_ledger(_base(household)).entries)
    rows = household.rows["ins_zq_blake/card"]
    card = facing(held, "Liabilities:Zq:Blake:Card", rows)

    assert (card.by_id, card.by_content) == (0, len(rows))
    assert card.last_entry == rows[-1].day - timedelta(days=2)
    assert (card.missing, card.after) == (Stretch(), Stretch())


def test_each_unmatched_account_is_described_with_what_its_proposal_would_meet(
    household: Household,
) -> None:
    found = _surveyed(household)

    assert found["ins_zq_union/chk"].facing is not None
    assert found["ins_zq_union/chk"].facing.by_content == 8
    assert found["ins_zq_twin/card"].facing is None
    assert found["ins_zq_bank/ira"].facing is None


# The command.


def test_the_command_says_what_the_base_proposes_in_counts_and_days_alone(
    shell: Household, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["match-accounts"])
    out = capsys.readouterr().out

    chk = shell.ids["ins_zq_bank/chk"]
    assert f"Avery, Zq Bank: ZQ CHECKING ending 0101 (checking), {chk}" in out
    assert "  Proposed: Assets:Zq:Checking, by id" in out
    assert (
        "    10 of the 14 transactions the bank sent are in the base by their Plaid ids, all on"
        " Assets:Zq:Checking, Feb 3, 2026 to Jun 10, 2026"
    ) in out
    assert (
        "    Not in the base, on or before its last entry there on Jun 11, 2026: 1, Mar 31, 2026"
        in out
    )
    assert "  Proposed: Assets:Zq:Union, by content" in out
    assert "  Nothing proposed: the evidence points to Assets:Zq:Blake:Savings, which Avery" in out
    assert (
        f"  Name its account with --confirm {shell.ids['ins_zq_twin/card']}=<ledger account>" in out
    )
    loan = shell.ids["ins_zq_bank/loan"]
    assert (
        "  Nobody has claimed Liabilities:Zq:Mortgage yet: claim it with hearth add-account"
        " Liabilities:Zq:Mortgage --name <name> --owner avery, then confirm it with --confirm"
        f" {loan}"
    ) in out
    # In the order they are listed, and the mortgage, which nobody has claimed, left out.
    proposed = ("bank/card", "bank/chk", "union/chk", "blake/card", "blake/chk", "blake/joint")
    every = " ".join(f"--confirm {shell.ids[f'ins_zq_{key}']}" for key in proposed)
    assert f"To confirm every proposal: hearth match-accounts {every}" in out
    for secret in (*PAYEES, *AMOUNTS):
        assert secret not in out


def test_confirming_records_the_match_and_books_nothing_differently(
    shell: Household, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = shell.settings
    before = books.household_entries(settings).entries
    owners = _ownership(shell)
    chk, union = shell.ids["ins_zq_bank/chk"], shell.ids["ins_zq_union/chk"]
    joint = shell.ids["ins_zq_blake/joint"]

    main(
        [
            "match-accounts",
            "--confirm",
            chk,
            "--confirm",
            f"{union}=Assets:Zq:Union",
            "--confirm",
            joint,
        ]
    )
    out = capsys.readouterr().out

    mapping = _mapping(shell)
    assert mapping["ins_zq_bank/chk"] == "Assets:Zq:Checking"
    assert mapping["ins_zq_union/chk"] == "Assets:Zq:Union"
    assert mapping["ins_zq_blake/joint"] == "Assets:Zq:Joint"
    assert sum(1 for v in mapping.values() if v) == 3
    assert books.household_entries(settings).entries == before
    assert _ownership(shell) == owners
    assert f"  Matched to Assets:Zq:Checking, confirmed {_said(TODAY)}" in out


def test_a_match_can_be_changed_until_carrying_begins(
    shell: Household, capsys: pytest.CaptureFixture[str]
) -> None:
    chk = shell.ids["ins_zq_bank/chk"]
    main(["match-accounts", "--confirm", f"{chk}=Assets:Zq:Union"])
    assert _mapping(shell)["ins_zq_bank/chk"] == "Assets:Zq:Union"

    main(["match-accounts", "--confirm", chk])

    assert _mapping(shell)["ins_zq_bank/chk"] == "Assets:Zq:Checking"
    capsys.readouterr()


@pytest.mark.parametrize(
    ("given", "said"),
    [
        (["loan"], "Nobody has claimed Liabilities:Zq:Mortgage yet"),
        (["blakes=Assets:Zq:Blake:Savings"], "Assets:Zq:Blake:Savings is not avery's"),
        (["chk=Liabilities:Zq:Card"], "Liabilities:Zq:Card is not under Assets"),
        (["chk=Assets:Zq:Nowhere"], "The imported ledger opens no account Assets:Zq:Nowhere."),
        (["twin"], "Nothing is proposed for"),
        (["chk", "chk"], "is named twice"),
        (["chk", "union=Assets:Zq:Checking"], "Assets:Zq:Checking is named for two"),
        (["chk", "gone"], "No working connection reaches an account"),
    ],
)
def test_a_match_hearth_cannot_honour_is_refused_and_nothing_is_written(
    shell: Household, given: list[str], said: str
) -> None:
    keys = {
        "loan": "ins_zq_bank/loan",
        "blakes": "ins_zq_bank/blakes",
        "chk": "ins_zq_bank/chk",
        "twin": "ins_zq_twin/card",
        "union": "ins_zq_union/chk",
    }
    flags = []
    for one in given:
        name, _, target = one.partition("=")
        account_id = shell.removed[0] if name == "gone" else shell.ids[keys[name]]
        flags += ["--confirm", f"{account_id}={target}" if target else account_id]

    with pytest.raises(SystemExit) as refused:
        main(["match-accounts", *flags])

    assert said in str(refused.value.code)
    assert str(refused.value.code).endswith("Nothing was matched.")
    assert not any(_mapping(shell).values())


def test_an_account_another_connected_account_is_matched_to_is_not_matched_again(
    shell: Household, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["match-accounts", "--confirm", shell.ids["ins_zq_bank/chk"]])
    union = shell.ids["ins_zq_union/chk"]

    with pytest.raises(SystemExit, match="matched to another connected account already"):
        main(["match-accounts", "--confirm", f"{union}=Assets:Zq:Checking"])
    capsys.readouterr()


def test_an_account_a_member_named_is_not_matched_to_a_connection(shell: Household) -> None:
    conn = shell.fresh.connect()
    try:
        avery = shell.fresh.member_ids["avery"]
        accounts.name_account(
            conn,
            ledger_account="Assets:Zq:Elsewhere",
            name="Elsewhere",
            kind=accounts.Kind.BANK,
            institution=None,
            mask=None,
            owners=[avery],
            by=avery,
            now=NOW,
        )
    finally:
        conn.close()

    with pytest.raises(SystemExit, match="an account a member named"):
        main(["match-accounts", "--confirm", f"{shell.ids['ins_zq_bank/sav']}=Assets:Zq:Elsewhere"])
    assert not any(_mapping(shell).values())


def test_an_account_filed_as_one_hearth_keeps_is_not_matched_to_the_base(
    shell: Household, capsys: pytest.CaptureFixture[str]
) -> None:
    """Filed on the Connect page as a new account, its rows are in the books there: matching
    it to the base would take them out, which a match never does."""
    sav = shell.ids["ins_zq_bank/sav"]
    conn = shell.fresh.connect()
    try:
        avery = shell.fresh.member_ids["avery"]
        kept = accounts.register(
            conn, ledger_account="Assets:Avery:Zq:Savings", name="Savings", owners=[avery]
        )
        Registry(conn).confirm(sav, kept.id, by=avery, now=NOW)
    finally:
        conn.close()

    main(["match-accounts"])
    assert (
        "  Feeds Assets:Avery:Zq:Savings, an account the base does not open"
        in capsys.readouterr().out
    )
    with pytest.raises(SystemExit, match="an account hearth keeps itself"):
        main(["match-accounts", "--confirm", f"{sav}=Assets:Zq:Elsewhere"])
    assert _mapping(shell)["ins_zq_bank/sav"] == "Assets:Avery:Zq:Savings"


def test_without_an_imported_ledger_there_is_nothing_to_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(tmp_path / "data"))
    ledger = tmp_path / "inherited.beancount"
    ledger.write_text("2026-01-01 open Assets:Zq:Checking USD\n")

    with pytest.raises(SystemExit, match="No ledger has been imported"):
        main(["match-accounts"])
    monkeypatch.setenv(LEDGER_VARIABLE, str(ledger))
    with pytest.raises(SystemExit, match="No ledger has been imported"):
        main(["match-accounts"])


@pytest.fixture(params=["Pacific/Kiritimati", "UTC", "Pacific/Pago_Pago"])
def zone(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("TZ", request.param)
    time.tzset()
    yield request.param
    monkeypatch.undo()
    time.tzset()


def test_a_match_is_dated_by_the_households_own_day_in_every_zone(
    zone: str,
    shell: Household,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A match confirmed on Greenwich's evening of Nov 22 is dated by the household's own day,
    worked out here by hand for each zone."""
    day = {"Pacific/Kiritimati": date(2026, 11, 23)}.get(zone, date(2026, 11, 22))
    monkeypatch.setattr(cli, "system_clock", lambda: EVENING)

    main(["match-accounts", "--confirm", shell.ids["ins_zq_bank/chk"]])

    assert f"  Matched to Assets:Zq:Checking, confirmed {_said(day)}" in capsys.readouterr().out


# The rules, one at a time.


def _base(household: Household) -> Base:
    found = books.inherited(household.settings)
    assert isinstance(found, Base)
    return found


def _ledger(text: str) -> list[Directive]:
    entries, errors, _ = loader.load_string(text)
    assert errors == []
    return entries


def _row(transaction_id: str, day: date, amount: str, account: str = "acct") -> Transaction:
    return Transaction(
        transaction_id, account, day, None, Decimal(amount), "USD", None, None, None, None,
        pending=False, pending_transaction_id=None,
    )  # fmt: skip


AVERY = Member(MemberId(1), "avery", "Avery", is_admin=True, must_change_password=False)
BLAKE = MemberId(2)


def _connected_to(kind: str, rows: list[Transaction]) -> Connected:
    linked = LinkedAccount("acct", "item", "ACCOUNT", None, "0001", kind, None, None, None)
    return Connected(linked, AVERY, "Zq Bank", tuple(rows))


def test_each_side_of_a_transfer_hearth_booked_is_found_by_its_own_id() -> None:
    """Hearth books a card payment as one entry, each posting carrying its own row's source."""
    bank, card = plaid_source("item-a", "pay-out"), plaid_source("item-b", "pay-in")
    held = read(
        _ledger(
            "2026-01-01 open Assets:Zq:Checking USD\n"
            "2026-01-01 open Liabilities:Zq:Card USD\n"
            f'2026-03-02 * "Card payment"\n  source: "{bank}"\n'
            f'  Assets:Zq:Checking  -300.00 USD\n    source: "{bank}"\n'
            f'  Liabilities:Zq:Card  300.00 USD\n    source: "{card}"\n'
        )
    )

    out, received = (
        _row("pay-out", date(2026, 3, 1), "300.00"),
        _row("pay-in", date(2026, 3, 2), "-300.00"),
    )
    assert [p.account for p in placed(held, out)] == ["Assets:Zq:Checking"]
    assert [p.account for p in placed(held, received)] == ["Liabilities:Zq:Card"]
    assert transaction_of(plaid_source("item-a", "balance")) is None


def test_ids_that_disagree_propose_nothing_and_a_strict_majority_proposes() -> None:
    def entry(n: int, account: str) -> str:
        return (
            f'2026-03-{n:02} * "x"\n  plaid-id: "t{n}"\n  {account}  -1{n}.00 USD\n  Expenses:Zq\n'
        )

    opens = "".join(f"2026-01-01 open {a}\n" for a in ("Assets:Zq:A", "Assets:Zq:B", "Expenses:Zq"))
    rows = [_row(f"t{n}", date(2026, 3, n), f"1{n}.00") for n in range(1, 5)]
    split = read(
        _ledger(
            opens
            + "".join(entry(n, "Assets:Zq:A" if n < 3 else "Assets:Zq:B") for n in range(1, 5))
        )
    )
    most = read(
        _ledger(
            opens
            + "".join(entry(n, "Assets:Zq:A" if n < 4 else "Assets:Zq:B") for n in range(1, 5))
        )
    )

    tied = propose(split, _connected_to("depository", rows), {}, {})
    won = propose(most, _connected_to("depository", rows), {}, {})
    assert (tied.proposed, tied.unproposed) == (None, Unproposed.IDS_DISAGREE)
    assert (won.proposed, won.how, won.ids.dissent) == ("Assets:Zq:A", How.ID, 1)


def test_ids_on_the_other_side_from_the_accounts_kind_propose_nothing() -> None:
    held = read(
        _ledger(
            "2026-01-01 open Assets:Zq:A\n2026-01-01 open Expenses:Zq\n"
            '2026-03-01 * "x"\n  plaid-id: "t1"\n  Assets:Zq:A  -10.00 USD\n  Expenses:Zq\n'
        )
    )

    found = propose(held, _connected_to("credit", [_row("t1", date(2026, 3, 1), "10.00")]), {}, {})

    assert (found.account, found.unproposed) == ("Assets:Zq:A", Unproposed.WRONG_SIDE)


def test_an_account_matched_to_another_connected_account_is_not_proposed() -> None:
    held = read(
        _ledger(
            "2026-01-01 open Assets:Zq:A\n2026-01-01 open Expenses:Zq\n"
            '2026-03-01 * "x"\n  plaid-id: "t1"\n  Assets:Zq:A  -10.00 USD\n  Expenses:Zq\n'
        )
    )
    connected = _connected_to("depository", [_row("t1", date(2026, 3, 1), "10.00")])
    theirs = {"Assets:Zq:A": Account("x", "Assets:Zq:A", "A", frozenset({BLAKE}))}
    joint = {"Assets:Zq:A": Account("x", "Assets:Zq:A", "A", frozenset({BLAKE, AVERY.id}))}

    assert propose(held, connected, {}, {"Assets:Zq:A": "other"}).unproposed is Unproposed.TAKEN
    assert propose(held, connected, {}, {"Assets:Zq:A": "acct"}).proposed == "Assets:Zq:A"
    assert propose(held, connected, theirs, {}).unproposed is Unproposed.NOT_THEIRS
    assert propose(held, connected, joint, {}).proposed == "Assets:Zq:A"


@pytest.mark.parametrize(
    ("best", "runner_up", "proposed"),
    [
        (Candidate("A", 5, 10), None, True),  # the fewest, and exactly half of its stretch
        (Candidate("A", 4, 4), None, False),  # one too few, however alone
        (Candidate("A", 5, 11), None, False),  # under half of what its stretch holds
        (Candidate("A", 6, 6), Candidate("B", 3, 6), True),  # exactly twice the runner-up's
        (Candidate("A", 6, 6), Candidate("B", 4, 6), False),  # under twice
        (Candidate("A", 6, 6), Candidate("B", 6, 6), False),  # two alike
    ],
)
def test_content_proposes_the_best_only_when_it_clearly_beats_the_rest(
    best: Candidate, runner_up: Candidate | None, proposed: bool
) -> None:
    ranked = [best] if runner_up is None else [best, runner_up]

    assert (clearly(ranked) == best) is proposed
    assert matching.MINIMUM == 5
    assert matching.MARGIN == 2


def test_rows_are_paired_one_to_one_as_many_as_can_be() -> None:
    """Held against every way of pairing a few rows by hand: never fewer pairs than the most
    there are, and never a pair a week apart or of two amounts."""
    chooser = random.Random(20261122)  # noqa: S311 - invented rows; nothing secret
    start = date(2026, 3, 1)
    for _ in range(300):
        ours = [
            (start + timedelta(days=chooser.randrange(30)), Decimal(chooser.choice("12")))
            for _ in range(chooser.randrange(6))
        ]
        theirs = [
            (start + timedelta(days=chooser.randrange(30)), Decimal(chooser.choice("12")))
            for _ in range(chooser.randrange(6))
        ]
        pairs = most_pairs(ours, theirs)
        assert len({i for i, _ in pairs}) == len({j for _, j in pairs}) == len(pairs)
        assert all(
            ours[i][1] == theirs[j][1] and abs(ours[i][0] - theirs[j][0]) <= timedelta(7)
            for i, j in pairs
        )
        assert len(pairs) == _most_by_hand(ours, theirs)


def _most_by_hand(ours: list[tuple[date, Decimal]], theirs: list[tuple[date, Decimal]]) -> int:
    """The most pairs, by trying every way of giving each of the fewer rows a partner."""
    if len(ours) > len(theirs):
        ours, theirs = theirs, ours

    def fits(i: int, j: int) -> bool:
        return ours[i][1] == theirs[j][1] and abs(ours[i][0] - theirs[j][0]) <= timedelta(7)

    return max(
        (
            sum(1 for i, j in enumerate(chosen) if fits(i, j))
            for chosen in itertools.permutations(range(len(theirs)), len(ours))
        ),
        default=0,
    )


# What a matched account's connection vouches for, until hearth carries it on.

BASE_ENDS = ledgers.CARD_PAID + timedelta(days=1)
"""The base's last day on the checking account: the card payment, on Jun 11, after which its
last balance assertion, on Jun 12, says nothing more."""


def _match(household: Household, *keys: str) -> None:
    """Match each account to the base's account proposed for it, as the admin does."""
    matching.confirm(household.settings, [(household.ids[key], None) for key in keys], now=NOW)


def _covered_after_the_base(household: Household, username: str) -> list[tuple[int, bool]]:
    """Whether each month after the base's last month reads as whole on `username`'s
    household page, this month last."""
    conn = household.fresh.connect()
    try:
        viewer = find(conn, username)
        assert viewer is not None
        view = Disclosure.load(conn, viewer, household.fresh.bookkeeper.status).household()
    finally:
        conn.close()
    months = [(m.month, m.covered) for m in view.left_over.months]
    months.append((view.so_far.month, view.so_far.covered))
    last = Month.of(BASE_ENDS)
    return [(month.month, covered) for month, covered in months if month > last]


def _history_ends(household: Household, username: str) -> date:
    """The last month end the native app draws the household's net worth at, before today's
    point."""
    answer = Doorway(household.settings, household.fresh.bookkeeper, username).answer("overview")
    assert answer["state"] == "ready"
    history = answer["history"]
    assert isinstance(history, list)
    days = [date.fromisoformat(point["day"]) for point in history]
    return max(day for day in days if day.isoformat() != answer["as_of"])


def test_a_matched_account_is_vouched_for_only_through_the_bases_last_day_on_it(
    tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Avery's checking, the only account Avery owns here, is matched, and the base's entries
    on it name the very connection that still syncs today; but hearth books nothing into it
    after the base, so its months after June are not whole, on the page and in the app,
    however current its connection is."""
    household = build(tmp_path, clock, claims={"Assets:Zq:Checking": ("avery",)})
    _match(household, "ins_zq_bank/chk")
    household.fresh.bookkeeper.recompute(wait=True)

    assert household.fresh.books().synced == {"Assets:Zq:Checking": BASE_ENDS}
    after = _covered_after_the_base(household, "avery")
    assert after
    assert not any(covered for _, covered in after)
    assert _history_ends(household, "avery") <= BASE_ENDS

    # Without the cap the same connection would vouch through its last sync, and those months
    # would read as whole: the fixture is no straw man.
    monkeypatch.setattr(books, "vouched_within_base", lambda synced, frozen: dict(synced))
    household.fresh.bookkeeper.recompute(wait=True)
    assert household.fresh.books().synced["Assets:Zq:Checking"] > BASE_ENDS
    assert all(covered for _, covered in _covered_after_the_base(household, "avery")[:-1])
    assert _history_ends(household, "avery") > BASE_ENDS


def test_a_base_kept_by_bare_plaid_ids_is_vouched_for_by_no_connection(
    household: Household,
) -> None:
    """Blake's checking is kept by an importer's bare `plaid-id`, which names no connection, so
    its connection vouches for none of it, matched or not, exactly as before."""
    household.fresh.bookkeeper.recompute(wait=True)
    before = household.fresh.books().synced
    _match(household, "ins_zq_blake/chk")
    household.fresh.bookkeeper.recompute(wait=True)

    assert household.fresh.books().synced == before == {}


def test_a_live_ledger_hearth_inherits_is_vouched_for_through_the_last_sync(
    tmp_path: Path, clock: FakeClock
) -> None:
    """The same ledger inherited at HEARTH_LEDGER, which an import outside hearth may still be
    adding to: its connection vouches for it through its last sync, as it always has."""
    household = build(tmp_path, clock, imported=False)
    settings = replace(household.settings, ledger=household.ledger)
    conn = household.fresh.connect()
    try:
        registry = Registry(conn)
        checking = next(
            a for a in accounts.all_accounts(conn) if a.ledger_account == "Assets:Zq:Checking"
        )
        linked = registry.account(household.ids["ins_zq_bank/chk"])
        assert linked is not None
        registry.confirm(linked.account_id, checking.id, by=None, now=NOW)
        item = registry.item(linked.item_id)
        assert item is not None
        assert item.last_synced_at is not None
    finally:
        conn.close()

    found = books.from_household(settings, rulings=rulings_in)(NOW)

    # Both days on the household's own calendar, by the rule every figure follows.
    synced = min(item.last_synced_at.astimezone().date(), TODAY)
    assert synced > BASE_ENDS
    assert found.synced == {"Assets:Zq:Checking": synced}
