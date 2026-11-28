"""The gate every decision passes through, and the values that stand in for what it stops.

This is the only module in the application that imports ``hearth.access``: every "may this
member see or do that" is asked here, and ``tests/test_architecture.py`` fails the build if
anything else asks. Each page module in this package receives a gate and asks it before
computing anything, so a figure the viewer may not see is never computed, let alone rendered.
"""

from collections.abc import Callable, Collection
from typing import final

from hearth import access
from hearth.audit import AuditEvent
from hearth.members import Member, MemberId
from hearth.sharing import Facet, SharingChoices


class Concealed(Exception):  # noqa: N818 - it names an outcome, not a fault
    """Not there, or not for this viewer. Deliberately one and the same."""


@final
class Withheld:
    """Stands in for a figure the viewer may not see. It carries no value at all."""

    def __repr__(self) -> str:
        return "WITHHELD"


WITHHELD = Withheld()

type Disclosed[T] = T | Withheld


class Gate:
    """Decides, for one viewer, which facets of whose finances they may see."""

    def __init__(self, viewer: Member, choices: SharingChoices) -> None:
        self.viewer = viewer
        self._choices = choices

    def may(self, facet: Facet, owners: Collection[MemberId]) -> bool:
        return access.may_see(self.viewer, facet, owners, self._choices)

    def disclose[T](
        self, facet: Facet, owners: Collection[MemberId], compute: Callable[[], T]
    ) -> Disclosed[T]:
        """Compute a figure only once it is known the viewer may see it."""
        return compute() if self.may(facet, owners) else WITHHELD

    def may_read_statements(self, owners: Collection[MemberId]) -> bool:
        return access.may_read_statements(self.viewer, owners, self._choices)

    def may_see_review_state(self, owners: Collection[MemberId]) -> bool:
        return access.may_see_review_state(self.viewer, owners, self._choices)

    def shared_by(self, member: MemberId) -> frozenset[Facet]:
        """What a member shares with the household, in their own words. Not a decision."""
        return self._choices.get(member, frozenset())

    def may_read(self, event: AuditEvent) -> bool:
        return access.may_read_event(self.viewer, event)

    def may_enter_price(self, holders: Collection[Collection[MemberId]]) -> bool:
        return access.may_enter_price(self.viewer, holders)

    def may_use_connection(self, holder: MemberId) -> bool:
        return access.may_use_connection(self.viewer, holder)

    def may_see_why_unknown(self, owners: Collection[MemberId]) -> bool:
        return access.may_see_why_unknown(self.viewer, owners)

    def may_use_credentials(self, owner: MemberId | None) -> bool:
        return access.may_use_credentials(self.viewer, owner)

    def may_manage_household_credentials(self) -> bool:
        return access.may_manage_household_credentials(self.viewer)

    def may_feed(self, owners: Collection[MemberId]) -> bool:
        return access.may_feed(self.viewer, owners)

    def may_bring_in_ledger(self) -> bool:
        return access.may_bring_in_ledger(self.viewer)

    def may_assign_owners(self) -> bool:
        return access.may_assign_owners(self.viewer)

    def may_carry_on(self, owners: Collection[MemberId], *, recorded: bool) -> bool:
        return access.may_carry_on(self.viewer, owners, recorded=recorded)

    def may_name_other_side(self, owners: Collection[MemberId]) -> bool:
        return access.may_name_other_side(self.viewer, owners)

    def may_review(self, owners: Collection[MemberId]) -> bool:
        return access.may_review(self.viewer.id, owners)


def may_administer(member: Member) -> bool:
    """Whether `member` may manage members and passwords; it shows them nobody's money."""
    return access.may_administer(member)


def may_review(member: MemberId, owners: Collection[MemberId]) -> bool:
    """Whether `member` may rule on set-aside rows of an account `owners` own."""
    return access.may_review(member, owners)


def may_learn(
    author: MemberId | None, owners: Collection[MemberId], source: Collection[MemberId]
) -> bool:
    """Whether a decision by `author` (None for the household's own ledger) on a row of an
    account `source` own may suggest a category for a row of an account `owners` own."""
    return access.may_learn(author, owners, source)
