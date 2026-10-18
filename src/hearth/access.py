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


def may_see_review_state(
    viewer: Member, owners: Collection[MemberId], sharing: SharingChoices
) -> bool:
    """Whether ``viewer`` may know which of ``owners``' rows nobody has reviewed yet.

    Their reviewers may, and so may anyone the rows themselves are shared with: a shared
    transaction is shown as it stands in the books, unreviewed flag and all, so its total's
    unreviewed part tells them nothing new. Anyone else sees only that a total of theirs may
    rest on unreviewed rows, said the same way whether it does or not, so another member's
    pile is never counted, not even by subtracting from a household total.
    """
    return may_review(viewer.id, owners) or may_see(viewer, Facet.TRANSACTIONS, owners, sharing)


def may_enter_price(viewer: Member, holders: Collection[Collection[MemberId]]) -> bool:
    """Whether ``viewer`` may record a price by hand for a security.

    A price changes the value of every holding of that security, so only someone who holds it
    in an account they own may enter one; it is recorded as theirs, never as the source's.
    """
    return any(viewer.id in owners for owners in holders)


def may_use_connection(viewer: Member, holder: MemberId) -> bool:
    """A connection to an institution is its member's alone, joint accounts included: nobody
    else sees it, reconnects it, or reads through it. Being an admin grants nothing.

    For a ledger account one co-owns, one may see that it is already fed by another
    connection. The feeding member is never named and neither is the institution. On an
    account with exactly two owners, a co-owner who knows they did not connect it can infer
    that the other owner did. That inference is accepted: both members own the account, and
    hiding the fed state would make the second owner re-link the same bank and spend one of
    the household's few connections to create the duplicate feed that refusing a second
    feed exists to prevent."""
    return viewer.id == holder


def may_use_credentials(viewer: Member, owner: MemberId | None) -> bool:
    """Whether ``viewer`` may link through a Plaid account, and see how much of it is left.

    A member's own Plaid account is theirs alone: nobody else links through it, sees its
    allowance, or is told it exists. Being an admin grants nothing. The one fact others may
    see is that a ledger account they co-own is already fed by another connection, which
    names neither member nor institution (``may_use_connection`` says what that lets a
    co-owner infer). The household's Plaid account (no owner) is open to every member to
    choose; choosing it means seeing how many of its connections are left, never whose
    connections use the rest.
    """
    return owner is None or viewer.id == owner


def may_manage_household_credentials(viewer: Member) -> bool:
    """Entering or replacing the household's Plaid account is managing institutions: an admin's."""
    return viewer.is_admin


def may_feed(viewer: Member, owners: Collection[MemberId]) -> bool:
    """Whether ``viewer`` may file a connection's account into an account ``owners`` own.

    Only into an account they own themselves, or a new one they will own. Filing into anyone
    else's would let one member's connection write another's books, and claiming an account
    already in the ledger would hand over its whole history. What nobody owns, nobody feeds.

    An account offered is shown as already fed when another connection feeds it, naming
    neither member nor institution (see ``may_use_connection`` for what a co-owner may infer
    from that); an account not offered shows nothing.
    """
    return viewer.id in owners


def may_administer(viewer: Member) -> bool:
    """Admins manage members and passwords. That is all being an admin means."""
    return viewer.is_admin


def may_read_event(viewer: Member, event: AuditEvent) -> bool:
    """Admins read the whole audit log; every member reads what happened to them."""
    return viewer.is_admin or event.subject_id == viewer.id
