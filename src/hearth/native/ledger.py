"""The household's own ledger in the app: bringing it in, and matching accounts to it.

Four screens and three actions, each written from the member's own view of the ledger
(``Disclosure.ledger_desk``, ``hearth.disclosure.ledger``) and nothing else, as every screen is:

- ``ledger``: where the household's ledger stands, and an import this engine runs.
- ``ledger_check``: what a ledger file holds, read where it is and written nowhere, and why it
  would not be brought in, if it would not. Safe to ask again and again.
- ``matching``: each of the member's own connected accounts, and which of the imported ledger's
  accounts it is.
- ``bring_in_ledger``: bring a ledger in, once, in the background; ``ledger`` follows it.
- ``match_accounts``: match connected accounts, each to one of the accounts ``matching``
  offered it, all at once.
- ``ledger_owners``: each of the imported ledger's accounts nobody owns yet, by name, and every
  member, for an admin to say whose each is.
- ``assign_owners``: say whose each of those accounts is, all at once.

None of them is of the books, so each is answered before the first recompute ends. What the
app sends is read strictly (``hearth.native.common``), and anything the member may not see or
do is answered as missing.
"""

from collections.abc import Hashable
from pathlib import Path

from hearth import base
from hearth.config import Settings
from hearth.disclosure import Disclosure
from hearth.disclosure.ledger import (
    Choice,
    ConnectedAccount,
    Facing,
    LedgerFound,
    Proposed,
    Said,
)
from hearth.native.common import (
    LONGEST_TEXT,
    MOST_ROWS,
    Acted,
    Answer,
    Around,
    Given,
    Malformed,
    path_in,
    table_in,
)
from hearth.native.common import day as _day
from hearth.native.common import moment as _moment
from hearth.native.common import money as _money


def read_path(given: Given) -> Path:
    """A ledger file, by its absolute path, and nothing else."""
    if given.keys() != {"path"}:
        raise Malformed
    return Path(path_in(given, "path"))


def may_check(view: Disclosure, around: Around) -> bool:
    """Whether the viewer may ask what a ledger file holds (``access.may_bring_in_ledger``)."""
    return view.ledger_desk(around.settings, around.clock).may_bring_in


def check_stands(settings: Settings) -> Hashable:
    """Where what a check of a ledger file is told stands, beside the file itself: whether the
    household inherits its ledger, and the base it holds, which are all of the household a check
    reads (``ledger_import.household_allows``, asked as the app imports, once). Never the books,
    which change as a sync runs while the app asks again and again for the same check."""
    try:
        held = base.standing(settings.ledger_base)
    except base.BaseError:
        held = "unreadable"
    return (settings.ledger is not None, held)


def ledger_screen(view: Disclosure, _asked: object, around: Around) -> Answer:
    """Where the household's ledger stands, and the import this engine last ran for the
    viewer, if it did."""
    standing = view.ledger_desk(around.settings, around.clock).standing()
    importing = around.importer.status if around.importer is not None else None
    if importing is not None and importing.by != around.viewer:
        importing = None
    done = importing.done if importing is not None else None
    return {
        "imported": (
            None
            if standing.imported_on is None
            else {
                "on": _day(standing.imported_on),
                "files": standing.files,
                "unclaimed": standing.unclaimed,
            }
        ),
        "inherited": standing.inherited,
        # Whether a ledger is being brought in on this engine now, by whoever: told to anyone
        # who may bring one in, so they wait on it, and never whose it is.
        "importing": bool(standing.may_bring_in or standing.why_not is not None)
        and around.importer is not None
        and around.importer.running,
        "may_bring_in": standing.may_bring_in,
        "why_not": _said(standing.why_not),
        "bringing_in": (
            None
            if importing is None
            else {
                "running": importing.running,
                "outcome": done.outcome if done is not None else None,
                "refused": _said(done.refused) if done is not None else None,
                "finished_at": (
                    _moment(importing.finished_at) if importing.finished_at is not None else None
                ),
                "books_failed": importing.books_failed,
            }
        ),
    }


def check_screen(view: Disclosure, asked: object, around: Around) -> Answer:
    """What the ledger file asked for holds, and whether it would be brought in. Never says
    the path it was asked for."""
    assert isinstance(asked, Path)  # noqa: S101 - read by `read_path`
    checked = view.ledger_desk(around.settings, around.clock).check(asked)
    return {
        "found": None if checked.found is None else _found(checked.found),
        "refused": _said(checked.refused),
    }


def matching_screen(view: Disclosure, _asked: object, around: Around) -> Answer:
    """The viewer's own connected accounts, each with which of the imported ledger's accounts
    it is, and every account of it they may match it to."""
    found = view.ledger_desk(around.settings, around.clock).matching()
    return {
        "imported": found.imported,
        "unsure": found.unsure,
        "accounts": [_connected(account) for account in found.accounts],
        "totals": {
            "matched": found.matched,
            "of": len(found.accounts),
            "in_ledger": found.in_ledger,
            "booked": found.booked,
        },
    }


def bring_in(view: Disclosure, given: Given, around: Around) -> Acted:
    """Bring the ledger at the path given in, in the background, once."""
    path = read_path(given)
    desk = view.ledger_desk(around.settings, around.clock)
    if not desk.may_bring_in or around.importer is None:
        return Acted.MISSING
    if not around.importer.start(around.viewer, lambda: desk.bring_in(path)):
        return Acted.RUNNING
    return Acted.STARTED


def match(view: Disclosure, given: Given, around: Around) -> Acted:
    """Match the viewer's connected accounts to the accounts each is offered, all at once:
    `matches` holds each connected account's Plaid id with the account it is to be."""
    if given.keys() != {"matches"}:
        raise Malformed
    chosen = table_in(given, "matches")
    desk = view.ledger_desk(around.settings, around.clock)
    return Acted.MATCHED if desk.match(chosen) else Acted.REFUSED


def owners_screen(view: Disclosure, _asked: object, around: Around) -> Answer:
    """Every member, and each of the imported ledger's accounts nobody owns yet, by name, for
    an admin to say whose each is. Missing to anyone else, whatever the books are doing: the
    gate is asked before anything is said of them, so only an admin is told they are loading
    or failed, as the accounts are read from them."""
    found = view.ledger_desk(around.settings, around.clock).owners()
    if view.books is None:
        notice = view.notice()
        if notice is None:
            return {"state": "loading"}
        return {"state": "failed", "summary": notice.summary}
    return {
        "imported": found.imported,
        "you": around.viewer,
        "members": [
            {"username": m.username, "name": m.display_name, "hue": m.hue} for m in found.members
        ],
        "choices": [list(owners) for owners in found.choices],
        "accounts": [
            {
                "account": one.account,
                "kind": one.kind,
                "opened": _day(one.opened),
                "closed": _day(one.closed),
                "name": one.name,
            }
            for one in found.accounts
        ],
    }


LONGEST_ACCOUNT = 1024
"""The longest name of a ledger's account the app may send: far longer than any ledger's."""


def read_owners(given: Given) -> dict[str, tuple[str, ...]]:
    """Whose each account named is: `owners`, an object of the ledger's account names to the
    usernames of its owners, never empty, each with one owner at least and none twice."""
    if given.keys() != {"owners"}:
        raise Malformed
    value = given["owners"]
    if not isinstance(value, dict) or not 0 < len(value) <= MOST_ROWS:
        raise Malformed
    found: dict[str, tuple[str, ...]] = {}
    for account, usernames in value.items():
        if not (isinstance(account, str) and 0 < len(account) <= LONGEST_ACCOUNT):
            raise Malformed
        if not isinstance(usernames, list) or not 0 < len(usernames) <= MOST_ROWS:
            raise Malformed
        if not all(isinstance(u, str) and 0 < len(u) <= LONGEST_TEXT for u in usernames):
            raise Malformed
        if len(set(usernames)) != len(usernames):
            raise Malformed
        found[account] = tuple(usernames)
    return found


def assign(view: Disclosure, given: Given, around: Around) -> Acted:
    """Say whose each of the ledger's accounts named is, all at once."""
    chosen = read_owners(given)
    desk = view.ledger_desk(around.settings, around.clock)
    return Acted.ASSIGNED if desk.assign(chosen) else Acted.REFUSED


def _said(said: Said | None) -> Answer | None:
    """Why something cannot be done: the engine's word, and a plain sentence for the app."""
    return None if said is None else {"why": said.why, "said": said.plain}


def _found(found: LedgerFound) -> Answer:
    worth = found.worth
    return {
        "files": found.files,
        "entries": found.entry_count,
        "first": _day(found.first),
        "last": _day(found.last),
        "accounts": [
            {
                "name": opened.account,
                "kind": opened.kind,
                "opened": _day(opened.opened),
                "closed": _day(opened.closed),
            }
            for opened in found.accounts
        ],
        "kinds": [{"kind": kind, "count": count} for kind, count in found.kinds],
        "assertions": {"count": found.assertions, "failing": found.failing},
        "currencies": list(found.currencies),
        "commodities": list(found.commodities),
        "worth": {
            "value": _money(worth.value),
            "currency": worth.currency,
            "as_of": _day(worth.as_of),
            "is_floor": worth.is_floor,
            "unpriced": [
                {"commodity": commodity, "account": account}
                for commodity, account in worth.unpriced
            ],
        },
        "runs": list(found.runs),
        "unused": [{"file": u.file, "line": u.line, "what": u.what} for u in found.unused],
        "unused_kinds": {
            "plugins": found.plugins,
            "custom": found.custom,
            "documents": found.documents,
        },
    }


def _connected(account: ConnectedAccount) -> Answer:
    return {
        "id": account.id,
        "institution": account.institution,
        "name": account.name,
        "mask": account.mask,
        "kind": account.kind,
        "state": account.state,
        "matched": (
            None
            if account.matched is None
            else {"account": account.matched, "on": _day(account.matched_on)}
        ),
        "fixed": account.fixed,
        "kept": account.kept,
        "proposed": _proposed(account.proposed),
        "why_not": account.why_not,
        "choices": [_choice(choice) for choice in account.choices],
        "facing": _facing(account.facing),
        "reconciling": account.reconciling,
    }


def _proposed(proposed: Proposed | None) -> Answer | None:
    if proposed is None:
        return None
    return {
        "account": proposed.account,
        "how": proposed.how,
        "agreeing": proposed.agreeing,
        "elsewhere": proposed.elsewhere,
        "of": proposed.of,
    }


def _choice(choice: Choice) -> Answer:
    refused = choice.refused
    return {
        "account": choice.account,
        "proposed": choice.proposed,
        "facing": _facing(choice.facing),
        "refused": (
            None
            if refused is None
            else {"why": refused.why, "count": refused.count, "day": _day(refused.day)}
        ),
    }


def _facing(facing: Facing | None) -> Answer | None:
    if facing is None:
        return None
    return {
        "account": facing.account,
        "cut_over": _day(facing.cut_over),
        "carried": facing.carried,
        "postponed": facing.postponed,
        "rows": facing.rows,
        "in_ledger": {
            "count": facing.by_id + facing.by_content,
            "by_id": facing.by_id,
            "by_content": facing.by_content,
        },
        "before": {
            "count": facing.before.count,
            "from": _day(facing.before.first),
            "to": _day(facing.before.last),
        },
        "booked": {
            "count": facing.booked.count,
            "from": _day(facing.booked.first),
            "to": _day(facing.booked.last),
        },
        "last_entry": _day(facing.last_entry),
        "last_balance": _day(facing.last_balance),
        "last_pad": _day(facing.last_pad),
    }
