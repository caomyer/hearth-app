"""The one place that decides what a member may see.

Every page that shows household data builds it through ``hearth.disclosure``,
and every visibility decision ``disclosure`` makes is a call into this module.
If you are about to write a "may this viewer see that" check anywhere else, add
it here instead: a second place that decides is how a boundary like this leaks.
"""

from collections.abc import Collection

from hearth.audit import AuditEvent
from hearth.members import Member, MemberId
from hearth.sharing import Facet, SharingChoices


def may_see(
    viewer: Member, facet: Facet, owners: Collection[MemberId], sharing: SharingChoices
) -> bool:
    """Whether ``viewer`` may see ``facet`` of something that ``owners`` own.

    An owner always sees what they own, so every owner of a joint account sees
    its detail. Anyone else sees a facet only when every owner shares it: one
    owner of a joint account cannot expose the other owner's detail alone.
    Being an admin grants nothing here. What nobody owns, nobody sees.
    """
    if not owners:
        return False
    if viewer.id in owners:
        return True
    return all(facet in sharing.get(owner, frozenset()) for owner in owners)


def may_review(member: MemberId, owners: Collection[MemberId]) -> bool:
    """Whether ``member`` may review, and rule on, set-aside rows of an account ``owners`` own.

    Only an owner. Sharing grants nothing here: a member who shares their transactions shares
    what has been booked, not the right to decide what gets booked. Being an admin grants
    nothing. What nobody owns, nobody reviews.
    """
    return member in owners


def may_enter_price(viewer: Member, holders: Collection[Collection[MemberId]]) -> bool:
    """Whether ``viewer`` may record a price by hand for a security.

    A price changes the value of every holding of that security, so only someone who holds it
    in an account they own may enter one; it is recorded as theirs, never as the source's.
    """
    return any(viewer.id in owners for owners in holders)


def may_use_connection(viewer: Member, holder: MemberId) -> bool:
    """A connection to an institution is its member's alone, joint accounts included: nobody
    else sees it, reconnects it, or reads through it. Being an admin grants nothing."""
    return viewer.id == holder


def may_administer(viewer: Member) -> bool:
    """Admins manage members and passwords. That is all being an admin means."""
    return viewer.is_admin


def may_read_event(viewer: Member, event: AuditEvent) -> bool:
    """Admins read the whole audit log; every member reads what happened to them."""
    return viewer.is_admin or event.subject_id == viewer.id
