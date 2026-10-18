from datetime import date
from decimal import Decimal

import pytest
from beancount import loader
from beancount.core.data import Directive

from hearth.members import MemberId
from hearth.reports.flows import Flows, Owners, spending_by_category

ALEX = MemberId(1)
BLAIR = MemberId(2)
_OWNERS: dict[str, Owners] = {
    "Assets:Alex:Checking": frozenset({ALEX}),
    "Assets:Joint:Checking": frozenset({ALEX, BLAIR}),
    "Liabilities:Blair:Card": frozenset({BLAIR}),
    "Assets:Blair:Brokerage": frozenset({BLAIR}),
}
OWNERSHIP = _OWNERS.get


def nobody_owns(account: str) -> Owners | None:
    return None


SOLO_ALEX = frozenset({ALEX})
SOLO_BLAIR = frozenset({BLAIR})
JOINT = frozenset({ALEX, BLAIR})


def spending(entries: list[Directive], start: date, end: date) -> Flows:
    return spending_by_category(entries, OWNERSHIP, start=start, end=end, currency="USD")


@pytest.fixture
def january(entries: list[Directive]) -> Flows:
    return spending(entries, date(2026, 1, 1), date(2026, 1, 31))


@pytest.fixture
def year(entries: list[Directive]) -> Flows:
    return spending(entries, date(2026, 1, 1), date(2026, 12, 31))


def _sum(parts: list[dict[str, Decimal]]) -> dict[str, Decimal]:
    summed: dict[str, Decimal] = {}
    for part in parts:
        for category, number in part.items():
            summed[category] = summed.get(category, Decimal(0)) + number
    return summed


def test_spending_belongs_to_the_owners_of_the_account_that_paid(january: Flows) -> None:
    assert january.by_owners == {
        SOLO_ALEX: {"Expenses:Food": Decimal("40.10")},
        SOLO_BLAIR: {"Expenses:Food": Decimal("80.25")},
        JOINT: {"Expenses:Rent": Decimal("1500.00")},
    }
    assert january.unattributed == ()


def test_a_member_figure_holds_only_the_accounts_they_own_alone(january: Flows) -> None:
    alex = january.for_member(ALEX)

    assert alex.member == ALEX
    assert alex.by_category == {"Expenses:Food": Decimal("40.10")}
    assert alex.complete


def test_joint_spending_is_its_own_line_belonging_to_its_owners_together(
    january: Flows,
) -> None:
    assert january.joint == {JOINT: {"Expenses:Rent": Decimal("1500.00")}}


def test_no_joint_account_contributes_to_any_member_figure(year: Flows) -> None:
    joint_categories = {c for categories in year.joint.values() for c in categories}
    assert "Expenses:Rent" in joint_categories

    for member in (ALEX, BLAIR):
        assert "Expenses:Rent" not in year.for_member(member).by_category
        assert year.for_member(member).by_category == year.by_owners.get(frozenset({member}), {})


def test_the_household_counts_each_joint_account_exactly_once(year: Flows) -> None:
    parts = [year.for_member(m).by_category for m in (ALEX, BLAIR)]
    parts += list(year.joint.values())
    parts += [u.by_category for u in year.unattributed]

    assert _sum(parts) == year.household
    assert year.household["Expenses:Rent"] == Decimal("1500.00")


def test_the_check_above_catches_a_joint_account_counted_once_per_owner(year: Flows) -> None:
    # The double count the model removes: putting joint spending into each owner's figure.
    with_joint = [
        _sum([year.for_member(m).by_category, *year.joint.values()]) for m in (ALEX, BLAIR)
    ]
    parts = [*with_joint, *(u.by_category for u in year.unattributed)]

    assert _sum(parts) != year.household
    assert _sum(parts)["Expenses:Rent"] == 2 * year.household["Expenses:Rent"]


def test_the_household_counts_every_posting_once(january: Flows) -> None:
    assert january.household == {
        "Expenses:Food": Decimal("120.35"),
        "Expenses:Rent": Decimal("1500.00"),
    }


def test_spending_that_cannot_be_attributed_is_held_apart_with_its_reason(
    year: Flows,
) -> None:
    assert [(u.date, u.reason, u.by_category) for u in year.unattributed] == [
        (
            date(2026, 2, 10),
            "Assets:Unassigned:Savings has no owner",
            {"Expenses:Food": Decimal("5.00")},
        ),
        (
            date(2026, 3, 5),
            "paid from accounts with different owners",
            {"Expenses:Fees": Decimal("1.00")},
        ),
    ]


def test_a_member_figure_is_incomplete_while_anything_is_unattributed(year: Flows) -> None:
    assert not year.for_member(ALEX).complete
    assert not year.for_member(BLAIR).complete


def test_a_member_with_no_sole_accounts_has_an_empty_figure(january: Flows) -> None:
    assert january.for_member(BLAIR).by_category == {"Expenses:Food": Decimal("80.25")}
    assert (
        spending_by_category(
            [], OWNERSHIP, start=date(2026, 1, 1), end=date(2026, 1, 31), currency="USD"
        )
        .for_member(ALEX)
        .by_category
        == {}
    )


def test_spending_with_no_paying_account_is_unattributed() -> None:
    loaded, errors, _ = loader.load_string(
        "2026-01-01 open Expenses:Food\n2026-01-01 open Equity:Opening\n"
        '2026-01-02 * "Written off"\n  Expenses:Food  3.00 USD\n  Equity:Opening\n'
    )
    assert errors == []

    result = spending_by_category(
        loaded, nobody_owns, start=date(2026, 1, 1), end=date(2026, 1, 31), currency="USD"
    )

    (only,) = result.unattributed
    assert only.reason == "no Assets or Liabilities account paid"


def test_amounts_in_another_currency_are_listed_not_converted(year: Flows) -> None:
    assert "Expenses:Travel" not in year.household
    assert [str(e) for e in year.excluded] == ["100.00 EUR in Expenses:Travel on 2026-02-12"]
    assert not year.complete
    assert not year.for_member(ALEX).complete
