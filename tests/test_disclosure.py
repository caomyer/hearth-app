"""Member figures cover only what a member owns alone; joint accounts are their own line.

These read the views disclosure builds, for the invented household in ``tests/household.py``.
"""

from decimal import Decimal

from hearth import db
from hearth.disclosure import WITHHELD, Disclosure, HouseholdView, Worth
from hearth.disclosure.household import MemberRow
from hearth.members import find
from hearth.sharing import DEFAULT_SHARED, Facet
from tests import household
from tests.conftest import World

EVERYTHING = frozenset(Facet)


def household_for(world: World, username: str) -> HouseholdView:
    return disclosure_for(world, username).household()


def disclosure_for(world: World, username: str) -> Disclosure:
    conn = db.connect(world.settings.database)
    try:
        viewer = find(conn, username)
        assert viewer is not None
        return Disclosure.load(conn, viewer, world.bookkeeper.status)
    finally:
        conn.close()


def amount(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def worth(row: MemberRow) -> Decimal:
    assert isinstance(row.net_worth, Worth)
    return row.net_worth.value


def test_at_the_defaults_everyone_sees_net_worth_and_spending(world: World) -> None:
    """The family dashboard as approved: a member who owns nothing of someone's still sees
    their net worth and spending, the joint line and those household totals. Income is
    private until each member shares it, so household income leaves the others out."""
    view = household_for(world, "casey")

    assert [worth(row) for row in view.members] == [
        Decimal(household.NET_WORTH[u].replace(",", "")) for u in household.USERNAMES
    ]
    assert [worth(row) for row in view.joint] == [Decimal("20000.42")]
    assert not view.headline.is_partial
    assert view.headline.value == amount(household.HOUSEHOLD_NET_WORTH)
    assert view.so_far.spent == amount(household.HOUSEHOLD_SPENDING)
    assert not view.so_far.spent_without
    assert view.so_far.earned == amount(household.INCOME["casey"])
    assert [p.name for p in view.so_far.earned_without] == ["Avery", "Blake", "Joint accounts"]
    assert [p.name for p in view.left_over.without] == ["Avery", "Blake", "Joint accounts"]


def test_household_income_is_whole_only_once_everyone_shares_it(world: World) -> None:
    for username in household.USERNAMES:
        world.choose(username, DEFAULT_SHARED | {Facet.MONTHLY_INCOME_TOTAL})

    view = household_for(world, "casey")

    assert view.so_far.earned == amount(household.HOUSEHOLD_INCOME)
    assert not view.so_far.earned_without
    assert not view.left_over.without


def test_no_member_figure_moves_when_only_a_joint_account_does(world: World) -> None:
    before = household_for(world, "casey")
    world.ledger.write_text(
        world.ledger.read_text().replace(
            '"Payee Jacana Hall" ""\n  Assets:Zq:Joint  -1640.84 USD',
            '"Payee Jacana Hall" ""\n  Assets:Zq:Joint  -10640.84 USD',
        )
    )
    world.recompute()
    after = household_for(world, "casey")

    assert [worth(row) for row in after.members] == [worth(row) for row in before.members]
    assert worth(after.joint[0]) == worth(before.joint[0]) - 9000


def test_a_member_whose_only_account_is_joint_has_nothing_of_their_own(world: World) -> None:
    world.choose("casey", DEFAULT_SHARED)
    conn = world.connect()
    try:
        conn.execute("DELETE FROM account_owners WHERE member_id = ?", (world.member_ids["casey"],))
        conn.execute(
            "INSERT INTO account_owners (account_id, member_id) VALUES (?, ?)",
            (world.account_ids["joint"], world.member_ids["casey"]),
        )
    finally:
        conn.close()

    view = household_for(world, "avery")

    casey = next(r for r in view.members if r.part.owners[0].username == "casey")
    assert not casey.connected
    assert all(part.part.owners[0].username != "casey" for part in view.composition)


def test_a_joint_line_needs_every_owner_to_share_it(world: World) -> None:
    world.choose("avery", DEFAULT_SHARED - {Facet.NET_WORTH_TOTAL})

    casey = household_for(world, "casey")
    blake = household_for(world, "blake")

    assert casey.joint[0].net_worth is WITHHELD
    assert casey.headline.is_partial
    assert {p.name for p in casey.headline.without} == {"Avery", "Joint accounts"}
    assert worth(blake.joint[0]) == Decimal("20000.42")


def test_the_joint_balance_cannot_be_had_by_subtraction(world: World) -> None:
    """Blake shares everything, Avery keeps the joint line private: nothing Casey sees adds
    up to, or can be taken apart into, the joint account's balance or spending."""
    world.choose("blake", EVERYTHING)
    world.choose("avery", DEFAULT_SHARED - {Facet.NET_WORTH_TOTAL, Facet.MONTHLY_SPENDING_TOTAL})

    view = household_for(world, "casey")
    blake = disclosure_for(world, "casey").person("blake")

    assert isinstance(blake.worth, Worth)
    assert blake.worth.value == Decimal("73519.27")
    assert view.headline.is_partial
    assert view.headline.value == Decimal("73519.27") + Decimal("4505.37")
    assert view.so_far.spent == amount(household.SPENDING["blake"]) + amount(
        household.SPENDING["casey"]
    )
    assert blake.joint == ()
