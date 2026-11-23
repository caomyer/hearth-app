"""A household whose suggestions could leak, for the privacy crawls of the pages and of the
native app's doorway (``tests/test_privacy_boundary.py``, ``tests/native/test_doorway.py``).

Avery and Blake each have a checking account; Avery's connection also feeds their joint
account. Their ledger, imported as the base, books two merchants they both buy from in
categories of their own: Avery's "Zq Avery Deli" as Marzipan, Blake's "Zq Shared Grocer" as
Falconry, and the joint "Zq Joint Market" as Hearthstone. Then each books a September row of
the other's merchant in a new category of their own, Avery as Pantry and Blake as Gryphonry,
and more rows of both merchants arrive on all three accounts.

So every way a suggestion could carry one member's category to the other is set up: by the
merchant, by the bank's category, by a ruling, from a member's own account onto the joint one,
and from one member's account onto the other's. Every name and figure is invented.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from hearth import accounts, auth, matching
from hearth.ledger_import import import_ledger
from hearth.plaid.fake import FakeAccount, FakeInstitution, FakeTransaction
from hearth.plaid.registry import Registry
from hearth.review.pile import PileItem
from hearth.sharing import Facet
from tests.conftest import FakeClock, password_of
from tests.firstday import FirstDay, first_day
from tests.household import noon

TODAY = date(2026, 10, 15)
DELI, GROCER, MARKET = "Zq Avery Deli", "Zq Shared Grocer", "Zq Joint Market"
RESTAURANT, GROCERIES = "FOOD_AND_DRINK_RESTAURANT", "FOOD_AND_DRINK_GROCERIES"
AVERY_CHK, JOINT, BLAKE_CHK = "Assets:Zq:AveryChk", "Assets:Zq:Joint", "Assets:Zq:BlakeChk"
OWNERS = {AVERY_CHK: ("avery",), JOINT: ("avery", "blake"), BLAKE_CHK: ("blake",)}


def _row(name: str, amount: str, day: int, month: int = 9) -> FakeTransaction:
    category = RESTAURANT if name == DELI else GROCERIES
    return FakeTransaction(name, Decimal(amount), date(2026, month, day), category)


HELD = {
    AVERY_CHK: (_row(DELI, "10.00", 3, 8), _row(DELI, "11.00", 5, 8), _row(DELI, "12.00", 7, 8)),
    JOINT: (_row(MARKET, "20.00", 4, 8),),
    BLAKE_CHK: (
        _row(GROCER, "30.00", 3, 8),
        _row(GROCER, "31.00", 5, 8),
        _row(GROCER, "32.00", 7, 8),
    ),
}
"""What the ledger holds of each bank's rows, in August."""

CATEGORY = {AVERY_CHK: "Marzipan", JOINT: "Hearthstone", BLAKE_CHK: "Falconry"}

FIRST = {
    AVERY_CHK: (_row(DELI, "13.00", 2), _row(GROCER, "14.00", 3)),
    JOINT: (_row(DELI, "15.00", 4), _row(MARKET, "21.00", 5)),
    BLAKE_CHK: (_row(GROCER, "33.00", 6), _row(DELI, "16.00", 7)),
}
"""What each bank sent after the ledger stops, on Aug 20, before anyone ruled."""

LATER = {
    AVERY_CHK: (_row(DELI, "17.00", 20), _row(GROCER, "22.00", 23), _row(MARKET, "24.00", 25)),
    JOINT: (_row(DELI, "18.00", 21), _row(GROCER, "19.00", 22)),
    BLAKE_CHK: (_row(GROCER, "23.50", 24),),
}
"""What arrives after Avery booked a row of the grocer's as Pantry and Blake one of the deli's
as Gryphonry."""

OPENING = Decimal("1000.00")


def _held(account: str) -> Decimal:
    return OPENING - sum((r.amount for r in HELD[account]), Decimal(0))


def _current(account: str, *rows: Mapping[str, tuple[FakeTransaction, ...]]) -> Decimal:
    return _held(account) - sum((r.amount for got in rows for r in got[account]), Decimal(0))


def _account(key: str, name: str, mask: str, account: str) -> FakeAccount:
    return FakeAccount(
        key,
        name,
        mask,
        "depository",
        "checking",
        _current(account, FIRST),
        transactions=(*HELD[account], *FIRST[account]),
    )


AVERY_BANK = FakeInstitution(
    "ins_zq_avery",
    "Zq Avery Bank",
    (
        _account("chk", "ZQ AVERY CHK", "1111", AVERY_CHK),
        _account("jnt", "ZQ JOINT", "3333", JOINT),
    ),
)
BLAKE_BANK = FakeInstitution(
    "ins_zq_blake", "Zq Blake Bank", (_account("chk", "ZQ BLAKE CHK", "2222", BLAKE_CHK),)
)
KEYS = {AVERY_CHK: ("avery", "chk"), JOINT: ("avery", "jnt"), BLAKE_CHK: ("blake", "chk")}


def _base() -> str:
    text = 'option "operating_currency" "USD"\n\n'
    for account in (*OWNERS, "Equity:Zq:Opening", *(f"Expenses:Zq:{c}" for c in CATEGORY.values())):
        text += f"2026-08-01 open {account} USD\n"
    for account in OWNERS:
        text += f'\n2026-08-01 * "Opening"\n  {account}  {OPENING} USD\n  Equity:Zq:Opening\n'
        for row in HELD[account]:
            text += f'\n{row.day} * "{row.name}" ""\n  {account}  -{row.amount} USD\n'
            text += f"  Expenses:Zq:{CATEGORY[account]}\n"
        text += f"\n2026-08-20 balance {account}  {_held(account)} USD\n"
    return text


PANTRY, GRYPHONRY = "Expenses:Zq:Pantry", "Expenses:Zq:Gryphonry"

JOINTLY = "Expenses:Zq:Jointly"

SECRETS: dict[str, tuple[str, ...]] = {
    "Falconry": ("blake",),
    "Gryphonry": ("blake",),
    "Marzipan": ("avery",),
    "Pantry": ("avery",),
    "Hearthstone": ("avery", "blake"),
    "Jointly": ("avery", "blake"),
}
"""Each category only one account's owners book, by whose it is: anyone else may be shown it
only through what every one of its owners shares of their transactions or their categories,
never through a suggestion. Casey owns nothing here, and is shown each only so."""

USERNAMES = ("avery", "blake", "casey")


def may_see(viewer: str, owners: tuple[str, ...], shared: Mapping[str, frozenset[Facet]]) -> bool:
    """The contract for an account's category, stated independently of hearth: its owners', or
    shared with everyone once every owner shares their transactions or their category
    breakdown."""
    detail = {Facet.TRANSACTIONS, Facet.CATEGORY_BREAKDOWN}
    return viewer in owners or all(
        bool(shared.get(owner, frozenset()) & detail) for owner in owners
    )


@dataclass
class Learned:
    fresh: FirstDay
    items: dict[str, str]
    """Each member's connection's Item id."""
    account_ids: dict[str, str]
    """Each ledger account's hearth account id."""

    def pile(self) -> list[PileItem]:
        self.fresh.bookkeeper.recompute(wait=True)
        return list(self.fresh.books().pile)

    def row(self, account: str, name: str, amount: str) -> PileItem:
        (found,) = [
            i
            for i in self.pile()
            if i.account == account and i.payee == name and i.amount == -Decimal(amount)
        ]
        return found

    def path_values(self) -> dict[str, list[str]]:
        return {
            "username": [*USERNAMES, "nobody"],
            "item_id": [*self.items.values(), "nonexistent"],
            "account_id": [*self.account_ids.values(), "nonexistent"],
        }

    def choose(self, username: str, shared: frozenset[Facet]) -> None:
        conn = self.fresh.connect()
        try:
            for facet in Facet:
                conn.execute(
                    "INSERT INTO sharing_preferences (member_id, facet, shared, updated_at)"
                    " VALUES (?, ?, ?, ?) ON CONFLICT (member_id, facet)"
                    " DO UPDATE SET shared = excluded.shared",
                    (self.fresh.member_ids[username], facet.value, facet in shared, "2026-01-01"),
                )
        finally:
            conn.close()


def build(tmp_path: Path, clock: FakeClock) -> Learned:
    clock.now = noon(TODAY)
    fresh = first_day(tmp_path, clock, institutions=(AVERY_BANK, BLAKE_BANK))
    conn = fresh.connect()
    try:
        casey, _ = auth.create_member(
            conn,
            actor=None,
            username="casey",
            display_name="Casey",
            is_admin=False,
            password=password_of("casey"),
            now=clock(),
        )
    finally:
        conn.close()
    fresh.member_ids["casey"] = casey.id
    items = {
        "avery": fresh.link("avery", AVERY_BANK.id),
        "blake": fresh.link("blake", BLAKE_BANK.id),
    }
    folder = tmp_path / "kept"
    folder.mkdir()
    (folder / "main.beancount").write_text(_base())
    import_ledger(fresh.settings, folder / "main.beancount", now=clock())
    conn = fresh.connect()
    try:
        ids = {
            account: accounts.claim(
                conn,
                ledger_account=account,
                name=account.rsplit(":", 1)[1],
                owners=[fresh.member_ids[o] for o in owners],
                now=clock(),
            ).id
            for account, owners in OWNERS.items()
        }
        linked = {a.account_id for i in items.values() for a in Registry(conn).accounts_of(i)}
    finally:
        conn.close()
    plaid = {account: f"{items[holder]}-{key}" for account, (holder, key) in KEYS.items()}
    assert set(plaid.values()) == linked
    matching.confirm(fresh.settings, [(plaid[a], a) for a in OWNERS], now=clock())
    learned = Learned(fresh, items, ids)
    learned.pile()

    # A while later, each books a September row of the other's merchant in a category of
    # their own, on their own account.
    clock.advance(timedelta(hours=1))
    _book(learned, "avery", learned.row(AVERY_CHK, GROCER, "14.00"), PANTRY)
    _book(learned, "blake", learned.row(BLAKE_CHK, DELI, "16.00"), GRYPHONRY)
    # And Avery books the joint account's deli row in a category only the joint account has.
    _book(learned, "avery", learned.row(JOINT, DELI, "15.00"), JOINTLY)

    # And a while after that, more rows of both arrive, everywhere.
    clock.advance(timedelta(hours=1))
    for account, rows in LATER.items():
        holder, key = KEYS[account]
        for row in rows:
            fresh.plaid.change(items[holder], "added", row, key)
        fresh.plaid.report(items[holder], key, _current(account, FIRST, LATER))
    for holder in ("avery", "blake"):
        client = fresh.signed_in(holder)
        response = client.post(
            f"/connect/items/{items[holder]}/sync", data={"csrf": fresh.csrf(client)}
        )
        assert response.status_code == 303
    learned.pile()
    return learned


def _book(learned: Learned, username: str, item: PileItem, account: str) -> None:
    client = learned.fresh.signed_in(username)
    response = client.post(
        "/review/rows/book",
        data={"csrf": learned.fresh.csrf(client), "review_id": item.review_id, "account": account},
    )
    assert response.status_code == 303, response.text
    assert "outcome=booked" in response.headers["location"]
