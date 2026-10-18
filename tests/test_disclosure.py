"""Member figures cover only what a member owns alone; joint accounts are their own line."""

from decimal import Decimal

from hearth.accounts import Account
from hearth.disclosure import WITHHELD, Disclosed, Disclosure, HouseholdView
from hearth.ledger import InMemoryLedger
from hearth.members import Member, MemberId
from hearth.months import Month
from hearth.sharing import DEFAULT_SHARED, Facet, SharingChoices

DANA, ELI, FINN = MemberId(1), MemberId(2), MemberId(3)
MEMBERS = [Member(m, f"m{m}", f"M{m}", False, False) for m in (DANA, ELI, FINN)]
JOINT = Account("joint", "Assets:Zq:Joint", "Joint", frozenset({DANA, ELI}))
ELI_SOLO = Account("eli", "Assets:Zq:Eli", "Eli solo", frozenset({ELI}))
MONTH = Month(2026, 10)
DEFAULTS = dict.fromkeys((DANA, ELI, FINN), DEFAULT_SHARED)


def household(
    viewer: MemberId, choices: SharingChoices, joint_balance: str = "800.00"
) -> HouseholdView:
    ledger = InMemoryLedger(
        {JOINT.ledger_account: Decimal(joint_balance), ELI_SOLO.ledger_account: Decimal(100)}, {}
    )
    return Disclosure(
        MEMBERS[viewer - 1],
        household=MEMBERS,
        accounts=[JOINT, ELI_SOLO],
        choices=choices,
        ledger=ledger,
    ).household(MONTH)


def net_worths(view: HouseholdView) -> list[Disclosed[Decimal]]:
    return [summary.net_worth for summary in view.members]


def test_a_member_whose_only_account_is_joint_has_nothing_of_their_own() -> None:
    view = household(FINN, DEFAULTS)

    assert net_worths(view) == [Decimal(0), Decimal(100), Decimal(0)]
    assert [line.net_worth for line in view.joint] == [Decimal(800)]
    assert view.net_worth == Decimal(900)


def test_no_member_figure_moves_when_only_a_joint_account_does() -> None:
    before = household(DANA, DEFAULTS)
    after = household(DANA, DEFAULTS, joint_balance="1234.56")

    assert net_worths(before) == net_worths(after)
    assert after.joint[0].net_worth == Decimal("1234.56")


def test_a_joint_line_needs_every_owner_to_share_it() -> None:
    choices = {**DEFAULTS, DANA: DEFAULT_SHARED - {Facet.NET_WORTH_TOTAL}}

    assert household(FINN, choices).joint[0].net_worth is WITHHELD
    assert household(FINN, choices).net_worth is WITHHELD
    assert household(ELI, choices).joint[0].net_worth == Decimal(800)
