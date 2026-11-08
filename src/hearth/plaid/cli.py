"""``hearth plaid``: the household's connections, for whoever runs hearth on this Mac.

    hearth plaid items                     every connection and every allowance; no live call
    hearth plaid sync --approved           bring every working connection up to date, then
                                           recompute the books
    hearth plaid holdings --approved       store today's price of every connected brokerage's
                                           holdings, then recompute the books
    hearth plaid remove ITEM --approved    end one connection at Plaid, freeing its slot
    hearth plaid adopt --member USERNAME --token-file PATH --approved
                                           what an Item made outside hearth offers; nothing kept
    hearth plaid adopt ... --confirm ITEM  hold that Item as the member's own connection

This is the machine's operator's view, not a page: whoever can run commands as the user
hearth runs as can already read the database and the Keychain (``hearth.vault``), so it
lists every member's connections, which is what finding a leaked slot needs. The pages show
each member only their own.

A command that calls Plaid refuses, before anything is sent, unless given ``--approved``.

A command that brings anything in recomputes the books after it, as `hearth serve` does, and
says whether they now hold it; one whose recompute fails says what went wrong and exits
non-zero, and the books stay as they were (``hearth.books``). A `hearth serve` running beside it
takes the change into its pages within seconds (`books.Bookkeeper.recompute_on_change`).

An access token is read from a file, or from standard input given ``-``, never from the
command line, where it would be kept in the shell's history and shown to every process on the
machine. It is never printed.
"""

import argparse
import sqlite3
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from hearth import books, db, members
from hearth.books import Status
from hearth.clock import Clock, system_clock
from hearth.config import Settings
from hearth.disclosure import Disclosure, rulings_in
from hearth.plaid import feed
from hearth.plaid.activity import fetch_activity
from hearth.plaid.approval import Approval, ApprovalRequired, Operation
from hearth.plaid.client import PlaidError
from hearth.plaid.connector import (
    NotAdopted,
    Offer,
    Offered,
    Refusal,
    Refused,
    Unadoptable,
)
from hearth.plaid.credentials import CredentialStore
from hearth.plaid.feed import Outcome
from hearth.plaid.holdings import price_holdings
from hearth.plaid.registry import Registry
from hearth.plaid.setup import DAYS_REQUESTED, PlaidSetup
from hearth.plaid.sync import Failed, Skipped, SyncStoppedError
from hearth.refresh import sync_then_recompute
from hearth.valuation.pricestore import Recorded
from hearth.vault.errors import VaultError
from hearth.vault.secret import Secret
from hearth.vault.tokens import TokenVault

FAILED = 1
"""Something asked for was not done: a connection did not sync, or the books were not
recomputed, so they do not hold what was brought in."""
REFUSED = 2
STDIN = "-"


def run(arguments: argparse.Namespace, settings: Settings) -> int:
    db.initialise(settings.database)
    feed.initialise(settings.feed_database)
    match arguments.plaid_command:
        case "items":
            return items(settings)
        case "sync":
            if not arguments.approved:
                print(
                    "hearth plaid sync makes live Plaid calls against real accounts; pass"
                    " --approved once the captain has approved it. Nothing was sent."
                )
                return REFUSED
            return sync(
                settings,
                PlaidSetup(approval=Approval.of([Operation.READ])),
                system_clock,
                books.from_household(settings, rulings=rulings_in),
            )
        case "remove":
            return remove(settings, arguments.item_id, approved=arguments.approved)
        case "holdings":
            if not arguments.approved:
                print(
                    "hearth plaid holdings makes live Plaid calls, and the first one for a"
                    " brokerage adds Plaid's Investments product to its Item; pass --approved"
                    " once the captain has approved it. Nothing was sent."
                )
                return REFUSED
            return holdings(
                settings,
                PlaidSetup(approval=Approval.of([Operation.HOLDINGS])),
                system_clock,
                books.from_household(settings, rulings=rulings_in),
            )
        case "activity":
            if not arguments.approved:
                print(
                    "hearth plaid activity makes live Plaid calls, and the first one for a"
                    " brokerage adds Plaid's Investments Transactions subscription to its Item;"
                    " pass --approved once the captain has approved it. Nothing was sent."
                )
                return REFUSED
            return activity(
                settings,
                PlaidSetup(approval=Approval.of([Operation.ACTIVITY])),
                system_clock,
                books.from_household(settings, rulings=rulings_in),
            )
        case "adopt":
            if not arguments.approved:
                print(
                    "hearth plaid adopt makes live Plaid calls to read an Item; pass --approved"
                    " once the captain has approved it. Nothing was read or sent."
                )
                return REFUSED
            return adopt(
                settings,
                PlaidSetup(approval=Approval.of([Operation.READ])),
                system_clock,
                username=arguments.member,
                token_file=arguments.token_file,
                confirm=arguments.confirm,
            )
    return REFUSED


def items(settings: Settings) -> int:
    say = print
    conn = db.connect(settings.database)
    try:
        names = {m.id: m.username for m in members.all_members(conn)}
        registry = Registry(conn)
        store = CredentialStore(conn, None)
        scopes = sorted({i.credentials for i in registry.all_items()} | _saved(conn))
        for scope in scopes:
            capacity = store.capacity(scope)
            if capacity is None:
                say(f"{scope}: no longer saved")
                continue
            say(
                f"{scope}: {capacity.remaining} of {capacity.limit} connections remaining"
                f" ({capacity.in_hearth} in hearth, {capacity.used_elsewhere} elsewhere)"
            )
        for item in registry.all_items():
            synced = (
                item.last_synced_at.astimezone().strftime("%Y-%m-%d %H:%M")
                if item.last_synced_at
                else "never"
            )
            say(
                f"{item.item_id}  {item.institution}  member={names.get(item.member, '?')}"
                f"  accounts={len(registry.accounts_of(item.item_id))}"
                f"  linked={item.linked_at.astimezone():%Y-%m-%d}  last-sync={synced}"
                f"  state={item.state.value}  through={item.credentials}"
            )
    finally:
        conn.close()
    return 0


def _saved(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT scope FROM plaid_credentials")}


def sync(settings: Settings, setup: PlaidSetup, clock: Clock, establish: books.Establish) -> int:
    """Bring every working connection up to date, then recompute the books, through the same
    path as `hearth serve`'s morning (``hearth.refresh``), and say how both went."""
    refreshed = sync_then_recompute(settings, setup, books.Bookkeeper(establish, clock), clock)
    code = 0
    if refreshed.synced is None:
        print("hearth: the sync stopped on an unexpected error; hearth.log says where.")
        code = FAILED
    elif not refreshed.synced:
        print("No working connection reaches anything to sync.")
    for synced in refreshed.synced or ():
        if isinstance(synced, Skipped):
            print(f"{synced.item_id}: skipped, {synced.reason}")
            continue
        if isinstance(synced, Failed):
            print(f"{synced.item_id}: could not be synced; hearth.log says why")
            code = FAILED
            continue
        run = synced.run
        print(
            f"{synced.item_id}: {run.outcome.value}, {run.pages} page(s),"
            f" +{run.added} ~{run.modified} -{run.removed}"
            + (f" ({run.problem})" if run.problem else "")
        )
        if run.outcome is not Outcome.COMPLETE:
            code = FAILED
    # A connection that could not be synced brought nothing, so the books are said to hold
    # only what the others fetched, or, when none fetched anything, nothing new.
    brought: str | None = "what was fetched"
    if any(isinstance(synced, Failed) for synced in refreshed.synced or ()):
        fetched = any(not isinstance(s, Failed | Skipped) for s in refreshed.synced or ())
        brought = "what was fetched from the connections that synced" if fetched else None
    return _recomputed(refreshed.status, settings, brought) or code


def _recomputed(status: Status, settings: Settings, brought: str | None) -> int:
    """Say whether the books hold what was `brought` in, and how to exit for it; None when
    nothing was brought in.

    A failed recompute establishes nothing: the books file stays as it was last written, so it
    is named with the time it was, beside what went wrong."""
    failure = status.failure
    if failure is None and status.books is not None:
        at = status.books.computed_at.astimezone()
        held = (
            f"The books hold {brought} now"
            if brought is not None
            else "Nothing was fetched, so the books hold nothing new"
        )
        print(
            f"{held}: recomputed {at:%Y-%m-%d %H:%M}, written to"
            f" {settings.books_file}. A running hearth serve shows it within seconds."
        )
        return 0
    summary = failure.summary if failure is not None else "nothing was established."
    if brought is None:
        print(f"hearth: nothing was fetched, and the books were not recomputed: {summary}")
    else:
        print(f"hearth: the books were not recomputed, so they do not hold {brought}: {summary}")
    try:
        written = datetime.fromtimestamp(settings.books_file.stat().st_mtime).astimezone()
    except FileNotFoundError:
        kept = "No books have been written yet."
    else:
        kept = f"{settings.books_file} stays as it was written {written:%Y-%m-%d %H:%M}."
    told = (
        " What was brought in is kept, and is in the books from the first recompute that"
        " succeeds; the pages say"
        if brought is not None
        else " The pages say"
    )
    print(f"{kept}{told} what went wrong to whoever may see it.")
    return FAILED


def holdings(
    settings: Settings, setup: PlaidSetup, clock: Clock, establish: books.Establish
) -> int:
    """Store the price each connected brokerage gives each of its holdings today, then
    recompute the books, which hold what each brokerage said."""
    conn = db.connect(settings.database)
    try:
        priced = price_holdings(conn, settings.feed_database, setup, clock)
    finally:
        conn.close()
    if not priced.asked:
        print("No working connection reaches an investment account, so none was asked.")
        return 0
    for asked in priced.asked:
        if asked.problem is None:
            print(f"{asked.item_id} ({asked.institution}): {_count(asked.holdings, 'holding')}")
        else:
            print(f"{asked.item_id} ({asked.institution}): not read, {asked.problem}")
    recorded = priced.recorded
    print(
        f"Prices stored: {recorded[Recorded.NEW]} new, {recorded[Recorded.CORRECTED]} corrected,"
        f" {recorded[Recorded.UNCHANGED]} already stored; fetched"
        f" {priced.fetched_at.astimezone():%Y-%m-%d %H:%M}."
    )
    for unpriceable in priced.unpriceable:
        print(f"Not priced: {unpriceable.security_id}, {unpriceable.reason}.")
    print("Plaid gives today's price only, so a day nobody asks has no price, for good.")
    bookkeeper = books.Bookkeeper(establish, clock)
    bookkeeper.recompute(wait=True)
    code = _recomputed(bookkeeper.status, settings, "what each brokerage said it holds")
    return code or (FAILED if any(a.problem is not None for a in priced.asked) else 0)


def activity(
    settings: Settings, setup: PlaidSetup, clock: Clock, establish: books.Establish
) -> int:
    """Keep every connected brokerage's investment activity since it was last asked, then
    recompute the books, as every fetch does (``hearth.plaid.activity``)."""
    conn = db.connect(settings.database)
    try:
        fetched = fetch_activity(conn, settings.feed_database, setup, clock)
    finally:
        conn.close()
    if not fetched.asked:
        print("No working connection reaches an investment account, so none was asked.")
        return 0
    for asked in fetched.asked:
        days = f"{asked.start:%Y-%m-%d} to {asked.end:%Y-%m-%d}"
        if asked.problem is None:
            print(
                f"{asked.item_id} ({asked.institution}):"
                f" {_count(asked.activity, 'activity row')} from {days}"
            )
        else:
            print(f"{asked.item_id} ({asked.institution}): not read for {days}, {asked.problem}")
    bookkeeper = books.Bookkeeper(establish, clock)
    bookkeeper.recompute(wait=True)
    kept = "what they held before, as activity is kept for a ledger outside hearth, not booked,"
    code = _recomputed(bookkeeper.status, settings, kept)
    return code or (FAILED if any(a.problem is not None for a in fetched.asked) else 0)


def remove(settings: Settings, item_id: str, *, approved: bool) -> int:
    """End one connection at Plaid. Transactions already brought in are kept."""
    if not approved:
        print(
            "hearth plaid remove calls Plaid's /item/remove, which cannot be undone; pass"
            " --approved once the captain has approved removing this Item. Nothing was sent."
        )
        return REFUSED
    setup = PlaidSetup(approval=Approval.of([Operation.REMOVE]))
    conn = db.connect(settings.database)
    try:
        registry = Registry(conn)
        item = registry.item(item_id)
        if item is None or not item.active:
            print(f"hearth: no connection {item_id} is held")
            return REFUSED
        vault = TokenVault(conn, setup.keys())
        client = setup.client(CredentialStore(conn, vault).credentials(item.credentials))
        failure: str | None = None
        try:
            client.remove(vault.get(item.member, item.item_id))
        except (ApprovalRequired, PlaidError, VaultError) as error:
            failure = str(error)
        if failure is not None:
            print(f"hearth: {failure}")
            return REFUSED
        vault.delete(item.member, item.item_id)
        registry.remove(item.item_id, at=datetime.now(UTC))
        print(f"{item.item_id} ({item.institution}) removed at Plaid; its transactions are kept.")
    finally:
        conn.close()
    return 0


def adopt(
    settings: Settings,
    setup: PlaidSetup,
    clock: Clock,
    *,
    username: str,
    token_file: str,
    confirm: str | None,
) -> int:
    """Say what an Item made outside hearth offers, and, given `confirm`, hold it as
    `username`'s own connection, through the Plaid account their next connection would use.

    The first run writes nothing: the member decides from what it says, since the history an
    Item reaches back is fixed when it is made. The second names the Item they chose."""
    try:
        token = _read_token(token_file)
    except ValueError as error:
        print(f"hearth: {error}")
        return REFUSED
    conn = db.connect(settings.database)
    try:
        member = members.find(conn, username)
        if member is None:
            print(f"hearth: there is no member {username}")
            return REFUSED
        connector = Disclosure.load(conn, member, Status(None, None, recomputing=False)).connector(
            conn,
            database=settings.database,
            feed_path=settings.feed_database,
            data_dir=settings.data_dir,
            setup=setup,
            clock=clock,
            runner=_never,
        )
        try:
            offer = connector.offer(token)
            _report(offer, clock().astimezone().date())
            if offer.reached:
                print(_already_reached(offer))
                return REFUSED
            if confirm is None:
                print(
                    f"Nothing was written. To adopt it as {member.username}'s connection, run"
                    f" this again with --confirm {offer.item_id}."
                )
                return 0
            synced = connector.adopt(token, confirm)
        except NotAdopted as refused:
            print(f"hearth: {_unadoptable(refused, member.username)}")
            return REFUSED
        except Refused as refused:
            print(f"hearth: {_refusal(refused)}")
            return REFUSED
        except (VaultError, SyncStoppedError) as error:
            print(f"hearth: {error}")
            return REFUSED
    finally:
        conn.close()
    print(
        f"{offer.item_id} ({offer.name}) is {member.username}'s connection now; no slot was used."
    )
    if isinstance(synced, Skipped):
        print(f"Its first sync did not run: {synced.reason}.")
    else:
        run = synced.run
        print(
            f"First sync: {run.outcome.value}, {run.pages} page(s), +{run.added}"
            + (f" ({run.problem})" if run.problem else "")
        )
    print(
        "Confirm where each of its accounts goes on Connect, then it is in the books from the"
        f" next recompute. To free its slot later: hearth plaid remove {offer.item_id} --approved."
    )
    return 0


def _read_token(path: str) -> Secret:
    """The token in the file at `path`, or on standard input for ``-``, without surrounding
    space. A failure is raised outside the handler: what was read must not travel with it."""
    problem: str | None = None
    try:
        return Secret(
            (sys.stdin.read() if path == STDIN else Path(path).read_text(encoding="utf-8")).strip()
        )
    except OSError as error:
        problem = f"the token file cannot be read ({error.strerror})"
    except ValueError:
        problem = "the token file holds no token"
    raise ValueError(problem)


def _never(work: object) -> None:
    """Adopting syncs where it stands; nothing is left to run after it."""
    raise AssertionError("nothing runs in the background from the command line")


def _report(offer: Offer, today: date) -> None:
    made = f", made at Plaid {offer.created_at:%Y-%m-%d}" if offer.created_at else ""
    print(f"{offer.name}: Item {offer.item_id}{made}")
    for offered in offer.accounts:
        print(f"  {_account(offered)}")
    earliest = offer.earliest
    if earliest is None:
        print("Plaid has no transactions for it.")
    else:
        days = (today - earliest).days + 1
        print(
            f"Earliest transaction: {earliest:%Y-%m-%d}, {_count(days, 'day')} back; a"
            f" connection made through hearth asks for {DAYS_REQUESTED} days."
        )
    print(
        "These are what Plaid holds for this Item now, read from Plaid, not what any tool kept"
        " from it. Plaid does not say how many days the Item was linked with, so its window"
        " starts on or before the earliest transaction: an account quiet at first reaches"
        " back further than its first transaction."
    )
    if not offer.complete:
        print("Plaid says it is still preparing this Item's history, so it may reach further.")
    print(
        "An Item's history is fixed when it is made: adopting keeps what it has, and reaching"
        " further means linking the bank again, which uses another connection."
    )
    if offer.investments:
        print("Its holdings can be read without linking again (investments).")


def _count(n: int, thing: str) -> str:
    return f"{n} {thing}" if n == 1 else f"{n} {thing}s"


def _account(offered: Offered) -> str:
    account = offered.account
    kind = account.subtype or account.type
    ending = f" ending {account.mask}" if account.mask else ""
    held = offered.held
    if held.earliest and held.latest:
        seen = (
            f"{_count(held.transactions, 'transaction')},"
            f" {held.earliest:%Y-%m-%d} to {held.latest:%Y-%m-%d}"
        )
    elif account.type == "investment":
        seen = "no transactions; its trades and holdings are Plaid's investments data"
    else:
        seen = "no transactions"
    line = f"{account.name} ({kind}{ending}): {seen}"
    reached = offered.reached
    if reached is None:
        return line
    if not reached.yours:
        return f"{line}; hearth already reaches it through another connection"
    covered = reached.held
    since = (
        f", {_count(covered.transactions, 'transaction')} since {covered.earliest:%Y-%m-%d}"
        if covered and covered.earliest
        else ""
    )
    return f"{line}; hearth already reaches it through {reached.item_id}{since}"


def _already_reached(offer: Offer) -> str:
    """Why hearth will not adopt an Item reaching an account a connection it holds reaches."""
    why = (
        f"hearth will not adopt it: a connection hearth holds already reaches"
        f" {_count(len(offer.reached), 'of its account')}, and every transaction would come"
        " twice."
    )
    kept = (
        " Nothing was written; the Item still holds a slot outside hearth until whatever holds"
        " its token removes it."
    )
    if not all(a.reached is not None and a.reached.yours for a in offer.reached):
        return why + kept
    further = [a for a in offer.reached if _reaches(a.held) < _reaches(_own(a))]
    shorter = [a for a in offer.reached if _reaches(_own(a)) < _reaches(a.held)]
    new = [a for a in offer.accounts if a.reached is None]
    bounds = (
        " Each date is an account's earliest transaction, so its history starts on or before it."
    )
    if not further and not new:
        how = f", further back on {_listed(shorter)}" if shorter else " as far back"
        return (
            f"{why} hearth's own reaches every one of its accounts{how}: hearth already covers"
            f" it {'better' if shorter else 'as well'}, so keep that one.{bounds}{kept}"
        )
    if further and not shorter and not offer.beyond:
        also = f", and also reaches {_offered(new)}" if new else ""
        held = sorted({a.reached.item_id for a in offer.reached if a.reached and a.reached.item_id})
        return (
            f"{why} This Item reaches every account hearth's own does, further back on"
            f" {_listed(further)}{also}. To keep the longer history, remove hearth's"
            f" ({', '.join(held)}) with hearth plaid remove first, then adopt this one; what it"
            f" brought in is kept.{bounds}{kept}"
        )
    parts = [
        f"{lead} {listed}"
        for lead, listed in (
            ("This Item reaches further back on", _listed(further)),
            ("hearth's own reaches further back on", _listed(shorter)),
            ("Only this Item reaches", _offered(new)),
            ("Only hearth's own reaches", "; ".join(_named(a.name, a.mask) for a in offer.beyond)),
        )
        if listed
    ]
    return f"{why} {'. '.join(parts)}. Which to keep is the member's choice.{bounds}{kept}"


def _own(offered: Offered) -> feed.Held | None:
    return offered.reached.held if offered.reached else None


def _reaches(held: feed.Held | None) -> date:
    """How far back `held` reaches, for comparing; with no transactions, not at all."""
    return held.earliest if held and held.earliest else date.max


def _listed(accounts: list[Offered]) -> str:
    return "; ".join(
        f"{_named(a.account.name, a.account.mask)} (this Item {_since(a.held)},"
        f" hearth's own {_since(_own(a))})"
        for a in accounts
    )


def _offered(accounts: list[Offered]) -> str:
    return "; ".join(_named(a.account.name, a.account.mask) for a in accounts)


def _named(name: str, mask: str | None) -> str:
    return f"{name} ending {mask}" if mask else name


def _since(held: feed.Held | None) -> str:
    return f"from {held.earliest:%Y-%m-%d}" if held and held.earliest else "no transactions"


def _unadoptable(refused: NotAdopted, username: str) -> str:
    using = refused.using.value if refused.using else "its"
    match refused.reason:
        case Unadoptable.NOT_A_TOKEN:
            return "that is not a Plaid access token (one begins access-production-)"
        case Unadoptable.WRONG_ENVIRONMENT:
            return (
                f"that token was made in Plaid's {refused.made_in} environment, and the Plaid"
                f" account {username} connects through is in {using}. A token works only in"
                " the environment that made it. Nothing was sent."
            )
        case Unadoptable.NOT_KNOWN:
            return (
                f"Plaid does not know that token under the Plaid account {username} connects"
                f" through ({using}): another Plaid account made it, or its Item was removed. A"
                " token works only with the client id and environment that made it."
            )
        case Unadoptable.ALREADY_HELD:
            return "hearth already holds that Item. Nothing was written."
        case Unadoptable.ALREADY_REACHED:
            assert refused.offer is not None  # noqa: S101 - raised with what it reaches
            return _already_reached(refused.offer)
        case Unadoptable.NOT_SHOWN:
            return (
                "that token is not for the Item named by --confirm. Run it without --confirm"
                " to see which Item it is. Nothing was written."
            )


def _refusal(refused: Refused) -> str:
    match refused.reason:
        case Refusal.NO_PLAID_ACCOUNT:
            return (
                "that member has no Plaid account to connect through: save their own, or"
                " choose the household's, on Connect first"
            )
        case Refusal.NOT_APPROVED:
            return "reading an Item is a live Plaid call that was not approved; nothing was sent"
    code = f" ({refused.plaid_code})" if refused.plaid_code else ""
    return f"Plaid did not complete that{code}. Nothing was written."
