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


STATEMENT_FACETS = frozenset(Facet)
"""What a part must show a viewer to be in their statements: every facet. A statement reads the
books the way a business reads its own, by account and by category, day by day, with what is
unreviewed and what each holds, so it needs everything a member can share."""


def may_read_statements(
    viewer: Member, owners: Collection[MemberId], sharing: SharingChoices
) -> bool:
    """Whether the accounts ``owners`` own are in ``viewer``'s statements.

    Only in full: when the viewer may see every one of `STATEMENT_FACETS` of them, which an
    owner always may. A part seen only in total is left out of every statement, named, never
    added in at a coarser grain, so no figure of one statement, nor the difference of two, nor
    a figure at cost against one at market value, holds anything of it. What nobody owns is in
    nobody's statements.
    """
    return all(may_see(viewer, facet, owners, sharing) for facet in STATEMENT_FACETS)


def may_review(member: MemberId, owners: Collection[MemberId]) -> bool:
    """Whether ``member`` may review, and rule on, set-aside rows of an account ``owners`` own.

    Only an owner. Sharing grants nothing here: a member who shares their transactions shares
    what has been booked, not the right to decide what gets booked. Being an admin grants
    nothing. What nobody owns, nobody reviews.
    """
    return member in owners


def may_learn(
    author: MemberId | None, owners: Collection[MemberId], source: Collection[MemberId]
) -> bool:
    """Whether what was decided for a row of an account ``source`` own may suggest a category
    for a row of an account ``owners`` own: a member's ruling, by ``author``, or the
    household's own ledger's booking, with no author (``hearth.proposals``).

    Only when the two accounts have exactly the same owners. A suggested category is shown as
    the row's category to everyone the row's transactions are shared with, so it may come only
    from accounts whose detail is shared on exactly the same terms: a member's own rows learn
    from accounts that member owns alone, never from a joint account, whose detail one owner
    cannot share without the other; a joint account's rows learn only from accounts owned by
    exactly the same members together. A ruling counts only when its author is one of the
    row's owners. Sharing grants nothing here and neither does being an admin. What nobody
    owns learns from nothing.
    """
    if not owners or set(owners) != set(source):
        return False
    return author is None or author in owners


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


def may_see_why_unknown(viewer: Member, owners: Collection[MemberId]) -> bool:
    """Whether ``viewer`` may be told why hearth does not know what an account ``owners`` own
    holds: that its bank reported no balance, that a sync of it did not finish, that it was
    connected again; or why the balance its bank reported is not what its transactions add
    up to, and as of which day. Only an owner. Each is what a connection or a review did, and
    a connection is its member's alone (``may_use_connection``), as is reviewing
    (``may_review``); a member who shares their balances shares the figures, not the state of
    what is behind them, so anyone else is told only that the balance is not known, or that
    the figures wait on one member's records. What nobody owns, nobody is told about."""
    return viewer.id in owners


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


def may_name_other_side(viewer: Member, owners: Collection[MemberId]) -> bool:
    """Whether ``viewer``, looking at a move into or out of an account hearth does not see, may
    be told which account ``owners`` own was on the other side of it.

    Only an owner of that account. A row a member moves from their own account into a joint
    account hearth does not see is shown to the other owner as the joint account's side
    alone: the day, the amount, and that it came from another account. Which account, and the
    bank's words for the row, are that account's transaction detail; sharing transactions
    shares what is in an account, not what one member says about where its money went, as a
    paired card payment never repeats either bank's words. What nobody owns, nobody is told.
    """
    return viewer.id in owners


def may_bring_in_ledger(viewer: Member) -> bool:
    """Bringing the household's own ledger into hearth, and reading a ledger file to see what
    it holds before it is brought in, is an admin's.

    The base it becomes is every member's books from then on, so bringing it in is a household
    act, not one member's, as entering the household's Plaid account is
    (``may_manage_household_credentials``). On the command line it is whoever runs hearth on
    its machine; among the household's members, the nearest to that is an admin. A file is
    read as the engine's own process, which is the person's at the Mac, so reading one tells
    an admin nothing they could not open themselves."""
    return viewer.is_admin


def may_carry_on(viewer: Member, owners: Collection[MemberId], *, recorded: bool) -> bool:
    """Whether ``viewer`` may move an account hearth keeps for ``owners`` onto an account of
    the imported ledger that nobody has claimed yet, whose claim then becomes that account's,
    so its owners see its whole history in the ledger (``hearth.matching``).

    Only an owner of the account moved, and only where ``recorded``: the ledger itself holds
    the connection's own transaction ids on that account, so it is that bank account, kept
    before hearth. An account that only looks like it by its rows could be anyone's, and
    moving onto it would hand its history to whoever chose it, which ``may_feed`` refuses for
    every account a member does not own. Being an admin grants nothing."""
    return viewer.id in owners and recorded


def may_administer(viewer: Member) -> bool:
    """Admins manage members and passwords. That is all being an admin means."""
    return viewer.is_admin


def may_read_event(viewer: Member, event: AuditEvent) -> bool:
    """Admins read the whole audit log; every member reads what happened to them."""
    return viewer.is_admin or event.subject_id == viewer.id
