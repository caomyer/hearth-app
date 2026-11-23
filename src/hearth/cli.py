"""Command-line entry point: ``hearth serve``, ``hearth add-member``, ``hearth add-account``,
``hearth accounts``, ``hearth import-ledger``, ``hearth match-accounts``, ``hearth plaid``,
``hearth compare``, ``hearth export``, ``hearth demo`` and ``hearth bridge``.

No command makes a live Plaid call unless it is approved on the command line: ``hearth serve
--approve-plaid OPERATION`` for the web pages, ``hearth bridge --approve-plaid OPERATION`` for
the native app, which starts it so from its own switches, and ``--approved`` for each ``hearth
plaid`` command that calls Plaid. Without it, they refuse before anything is sent.

``hearth serve``, ``hearth bridge`` and ``hearth plaid`` note what they ask of Plaid in
``hearth.log`` in the data directory (``hearth.runlog``).

``hearth bridge`` and ``hearth demo --bridge`` answer the native app over standard input and
output and open no port (``hearth.native``). They write nothing but replies to standard output.
``hearth bridge`` answers nothing about the household until the app signs in as a member over
that wire; ``hearth demo --bridge`` is signed in as the invented household's first member.
"""

import argparse
import sqlite3
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import uvicorn
from beancount.core.data import Open

from hearth import (
    accounts,
    auth,
    books,
    comparison,
    db,
    demo,
    ledger_import,
    matching,
    members,
    runlog,
)
from hearth.accounts import AccountRejectedError
from hearth.app import create_app
from hearth.clock import system_clock
from hearth.config import DATABASE_FILE, Settings, data_dir_from_environment
from hearth.disclosure import rulings_in
from hearth.members import MemberRejectedError, normalise_username
from hearth.native import stdio
from hearth.native.bridge import Bridge, Outcome
from hearth.plaid import booking, export, feed
from hearth.plaid import cli as plaid_cli
from hearth.plaid.approval import DESCRIPTIONS, Approval, Operation
from hearth.plaid.setup import PlaidSetup
from hearth.refresh import ASKING, Refresher
from hearth.reports.common import is_balance_sheet


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="hearth", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="serve the household on this machine")
    _add_address(serve)
    serve.add_argument(
        "--approve-plaid",
        action="append",
        default=[],
        choices=[operation.value for operation in Operation],
        metavar="OPERATION",
        help="approve one kind of live Plaid call for this run: "
        + "; ".join(f"{op.value}: {DESCRIPTIONS[op]}" for op in Operation),
    )
    serve.add_argument(
        "--tls-cert",
        type=Path,
        metavar="PEM",
        help="serve over HTTPS with this certificate for localhost, with --tls-key, which also"
        " lets Plaid bring a member back from a bank that signs in on its own page",
    )
    serve.add_argument("--tls-key", type=Path, metavar="PEM", help="the certificate's key")

    plaid = commands.add_parser("plaid", help="the household's Plaid connections")
    plaid_commands = plaid.add_subparsers(dest="plaid_command", required=True)
    plaid_commands.add_parser(
        "items", help="list every connection and each Plaid account's allowance (no live call)"
    )
    sync = plaid_commands.add_parser("sync", help="bring every connection up to date (a live call)")
    sync.add_argument("--approved", action="store_true", help="the captain approved this live call")
    held = plaid_commands.add_parser(
        "holdings",
        help="store today's price of every connected brokerage's holdings (a live call; the"
        " first adds Plaid's Investments product to the brokerage's Item)",
    )
    held.add_argument("--approved", action="store_true", help="the captain approved this live call")
    activity = plaid_commands.add_parser(
        "activity",
        help="keep every connected brokerage's trades, dividends and other activity (a live"
        " call; the first adds Plaid's Investments Transactions subscription to the Item)",
    )
    activity.add_argument(
        "--approved", action="store_true", help="the captain approved this live call"
    )
    remove = plaid_commands.add_parser(
        "remove", help="remove one connection at Plaid, freeing its slot (live and irreversible)"
    )
    remove.add_argument("item_id")
    remove.add_argument("--approved", action="store_true", help="the captain approved this call")
    adopt = plaid_commands.add_parser(
        "adopt",
        help="say what a Plaid Item made outside hearth offers and, with --confirm, hold it as a"
        " member's connection without using a slot (live reads)",
    )
    adopt.add_argument("--member", required=True, metavar="USERNAME", help="whose it becomes")
    adopt.add_argument(
        "--token-file",
        required=True,
        metavar="PATH",
        help="a file holding the Item's access token, or - to read it from standard input;"
        " never the token itself, which the shell's history would keep",
    )
    adopt.add_argument(
        "--confirm",
        metavar="ITEM_ID",
        help="adopt the Item a run without it showed; without it nothing is written",
    )
    adopt.add_argument("--approved", action="store_true", help="the captain approved these reads")

    add = commands.add_parser("add-member", help="add a member and print their one-time password")
    add.add_argument("username")
    add.add_argument("--name", required=True, help="the name the household sees")
    add.add_argument(
        "--admin", action="store_true", help="let them add members and reset passwords"
    )

    claim = commands.add_parser(
        "add-account",
        help="say who owns one of an inherited ledger's accounts; nobody sees it until then",
    )
    claim.add_argument("ledger_account", help="as the ledger names it, e.g. Assets:Bank:Checking")
    claim.add_argument("--name", required=True, help="the name the household sees")
    claim.add_argument(
        "--owner",
        action="append",
        required=True,
        metavar="USERNAME",
        help="a member who owns it; give it once per owner for a joint account",
    )
    commands.add_parser(
        "accounts", help="list the books' accounts and who owns each (never writes a ledger)"
    )

    bring = commands.add_parser(
        "import-ledger",
        help="import a beancount ledger kept before hearth, once: hearth keeps a frozen copy of"
        " it in the data directory and reads that from then on (never writes the ledger)",
    )
    bring.add_argument("ledger", type=Path, help="the file the ledger is opened from")

    match = commands.add_parser(
        "match-accounts",
        help="say which of the imported ledger's accounts each connected account is, with the"
        " evidence, and with --confirm record it (no live call; books nothing differently)",
    )
    match.add_argument(
        "--confirm",
        action="append",
        default=[],
        metavar="PLAID_ACCOUNT[=LEDGER_ACCOUNT]",
        help="match a connected account to the account proposed for it, or to the imported"
        " ledger's account named; give it once per account",
    )

    against = commands.add_parser(
        "compare",
        help="hold hearth's own books against a ledger the household kept before, account by"
        " account (reads only, prints only)",
    )
    against.add_argument("ledger", type=Path, help="the ledger to compare with, read only")
    against.add_argument(
        "--cycles",
        type=Path,
        metavar="RUN_JSON",
        help="a beancount_nextgen import's run.json: compare at the close of every statement"
        " cycle it reconciled, and the day before each opens",
    )
    against.add_argument(
        "--monthly",
        action="store_true",
        help="compare at every month end both books reach (the default without --cycles)",
    )
    against.add_argument(
        "--account",
        action="append",
        default=[],
        metavar="ACCOUNT",
        help="narrow to an account under investigation, once a full run shows where the books"
        " differ; give it once per account",
    )

    handover = commands.add_parser(
        "export",
        help="write one member's accounts and posted rows to a file a ledger kept outside hearth"
        " can import from (no live call; reads no ledger)",
    )
    handover.add_argument("--member", required=True, metavar="USERNAME")
    handover.add_argument(
        "--out",
        type=Path,
        metavar="PATH",
        help="where to write it; by default export.json in the data directory",
    )

    try_it = commands.add_parser(
        "demo", help="serve an invented household from a throwaway directory"
    )
    _add_address(try_it)
    try_it.add_argument(
        "--first-review",
        action="store_true",
        help="two years of history, none of it reviewed yet, as on the day of a first import",
    )
    try_it.add_argument(
        "--from-zero",
        action="store_true",
        help="a household on its first day: members only, no ledger, banks to connect",
    )

    try_it.add_argument(
        "--bridge",
        action="store_true",
        help="answer the native app over standard input and output, as its first member,"
        " and open no port",
    )

    _add_asking(
        try_it,
        "with --bridge, let the app sync the invented household's banks, every one of them"
        " hearth's stand-in for Plaid: ",
    )

    bridge = commands.add_parser(
        "bridge",
        help="answer the native app over standard input and output, once it signs in as a"
        " member, and open no port",
    )
    _add_asking(bridge, "approve one kind of live Plaid call the app's syncs may make: ")

    arguments = parser.parse_args(argv)
    match arguments.command:
        case "serve":
            if (arguments.tls_cert is None) != (arguments.tls_key is None):
                parser.error("--tls-cert and --tls-key go together")
            tls = (arguments.tls_cert, arguments.tls_key) if arguments.tls_cert else None
            _serve(arguments.host, arguments.port, Approval.of(arguments.approve_plaid), tls)
        case "plaid":
            settings = Settings.from_environment()
            runlog.configure(settings.data_dir)
            try:
                code = plaid_cli.run(arguments, settings)
            finally:
                runlog.stop(settings.data_dir)
            sys.exit(code)
        case "add-member":
            _add_member(arguments.username, arguments.name, is_admin=arguments.admin)
        case "add-account":
            _add_account(arguments.ledger_account, arguments.name, arguments.owner)
        case "accounts":
            _list_accounts()
        case "import-ledger":
            _import_ledger(arguments.ledger)
        case "match-accounts":
            _match_accounts(arguments.confirm)
        case "compare":
            _compare(
                arguments.ledger,
                arguments.cycles,
                monthly=arguments.monthly or arguments.cycles is None,
                accounts=arguments.account,
            )
        case "export":
            _export(arguments.member, arguments.out)
        case "demo":
            if arguments.bridge and arguments.from_zero:
                parser.error("--bridge does not go with --from-zero")
            if arguments.approve_plaid and not arguments.bridge:
                parser.error("--approve-plaid goes with --bridge")
            if arguments.from_zero:
                _demo_from_zero(arguments.host, arguments.port)
            elif arguments.bridge:
                approval = Approval.of(arguments.approve_plaid)
                _demo_bridge(approval, first_review=arguments.first_review)
            else:
                _demo(arguments.host, arguments.port, first_review=arguments.first_review)
        case "bridge":
            _bridge(Approval.of(arguments.approve_plaid))


def _add_address(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)


def _add_asking(parser: argparse.ArgumentParser, what: str) -> None:
    """``--approve-plaid`` for the native app's bridge: only what a sync asks, never linking a
    bank or removing a connection, which the app does not do."""
    parser.add_argument(
        "--approve-plaid",
        action="append",
        default=[],
        choices=sorted(operation.value for operation in ASKING),
        metavar="OPERATION",
        help=what + "; ".join(f"{op.value}: {DESCRIPTIONS[op]}" for op in sorted(ASKING)),
    )


type Tls = tuple[Path, Path]
"""A certificate and its key, both PEM files."""


def redirect_uri(port: int) -> str:
    """Where Plaid brings a member back after a bank's own sign-in, exactly as it must be
    allowed in the Plaid dashboard. Always ``localhost``, so the address to register never
    depends on how a page was reached. Port 443 is left out, as a browser leaves it out of the
    address it reports."""
    address = "localhost" if port == 443 else f"localhost:{port}"
    return f"https://{address}/connect/oauth"


def _serve(host: str, port: int, approval: Approval, tls: Tls | None = None) -> None:
    settings = Settings.from_environment()
    if tls is not None:
        settings = replace(settings, secure_cookies=True)
    approved = ", ".join(sorted(op.value for op in approval.operations)) or "none"
    print(f"Live Plaid calls approved for this run: {approved}.", flush=True)
    # Plaid's production returns only to an https address, so only HTTPS offers a way back.
    uri = redirect_uri(port) if tls is not None else None
    setup = PlaidSetup(approval=approval, redirect_uri=uri)
    if uri is not None:
        print(
            f"Open Hearth at {uri.removesuffix('/connect/oauth')}. Plaid brings members back from"
            f" a bank's own sign-in to {uri}, which must be one of the Plaid account's allowed"
            " redirect URIs.",
            flush=True,
        )
    else:
        print(
            "Not served over HTTPS: Plaid opens each bank's own sign-in in a window of its own"
            " and offers no way back to a bank that takes over the tab. --tls-cert and --tls-key"
            " offer one (docs/connections.md).",
            flush=True,
        )
    _run(settings, books.from_household(settings, rulings=rulings_in), host, port, setup, tls)


def _run(
    settings: Settings,
    establish: books.Establish,
    host: str,
    port: int,
    plaid: PlaidSetup,
    tls: Tls | None = None,
) -> None:
    """Serve, recomputing at start-up and then every morning (decision 6), and whenever what
    the books are computed from changes without this process, as when `hearth plaid sync` is
    run by hand (`books.Bookkeeper.recompute_on_change`).

    The app is created first, as that brings both databases up to date, so the first
    recompute and the first sync never find a database part way through being created.
    """
    log = runlog.configure(settings.data_dir)
    print(f"Hearth notes what it asks of Plaid in {log}", flush=True)
    try:
        bookkeeper = books.Bookkeeper(
            establish, system_clock, standing=lambda: books.standing(settings)
        )
        app = create_app(settings, bookkeeper, plaid=plaid)
        bookkeeper.recompute_daily()
        bookkeeper.recompute_on_change()
        # At start-up and every morning, only when this run was approved to read banks, their
        # holdings or their activity (`hearth.refresh.Refresher`).
        Refresher(settings, plaid, bookkeeper, system_clock).daily()
        cert, key = tls if tls is not None else (None, None)
        uvicorn.run(app, host=host, port=port, ssl_certfile=cert, ssl_keyfile=key)
    finally:
        runlog.stop(settings.data_dir)


def _add_member(username: str, name: str, *, is_admin: bool) -> None:
    settings = Settings.from_environment()
    db.initialise(settings.database)
    conn = db.connect(settings.database)
    try:
        member, password = auth.create_member(
            conn,
            actor=None,
            username=username,
            display_name=name,
            is_admin=is_admin,
            now=system_clock(),
        )
    except MemberRejectedError as error:
        sys.exit(f"hearth: {error}")
    finally:
        conn.close()
    print(f"Added {member.display_name}. One-time password for {member.username}: {password}")


def _ledger_accounts(settings: Settings) -> list[str]:
    """The books' balance-sheet accounts: hearth's own, and an inherited ledger's, read
    through the same read-only path as serving."""
    db.initialise(settings.database)
    feed.initialise(settings.feed_database)
    try:
        entries = books.household_entries(settings).entries
    except books.LedgerError as error:
        sys.exit(f"hearth: {error.summary}")
    return sorted(
        entry.account
        for entry in entries
        if isinstance(entry, Open) and is_balance_sheet(entry.account)
    )


def _add_account(ledger_account: str, name: str, usernames: list[str]) -> None:
    settings = Settings.from_environment()
    if ledger_account not in _ledger_accounts(settings):
        sys.exit(f"hearth: the books open no Assets or Liabilities account {ledger_account}.")
    db.initialise(settings.database)
    conn = db.connect(settings.database)
    try:
        owners = []
        for username in usernames:
            member = members.find(conn, members.normalise_username(username))
            if member is None:
                sys.exit(f"hearth: there is no member {username}.")
            if member not in owners:
                owners.append(member)
        accounts.claim(
            conn,
            ledger_account=ledger_account,
            name=name,
            owners=[member.id for member in owners],
            now=system_clock(),
        )
    except AccountRejectedError as error:
        sys.exit(f"hearth: {error}")
    finally:
        conn.close()
    print(f"{name} is {' and '.join(m.display_name for m in owners)}'s.")


def _list_accounts() -> None:
    settings = Settings.from_environment()
    in_ledger = _ledger_accounts(settings)
    db.initialise(settings.database)
    conn = db.connect(settings.database)
    try:
        names = {m.id: m.username for m in members.all_members(conn)}
        claimed = {a.ledger_account: a for a in accounts.all_accounts(conn)}
    finally:
        conn.close()
    for ledger_account in in_ledger:
        account = claimed.get(ledger_account)
        owners = ", ".join(sorted(names[o] for o in account.owners)) if account else ""
        print(f"{ledger_account}  {account.name if account else '-'}  {owners or 'unclaimed'}")


def _import_ledger(ledger: Path) -> None:
    """Import `ledger` as the household's base and print the check a person needs before
    trusting it (``hearth.ledger_import``), or say why nothing was imported."""
    settings = Settings.from_environment()
    try:
        outcome = ledger_import.import_ledger(settings, ledger, now=system_clock())
    except ledger_import.ImportRefused as refused:
        sys.exit(f"hearth: {refused} Nothing was imported.")
    for line in ledger_import.describe(outcome):
        print(line)


def _match_accounts(confirmations: list[str]) -> None:
    """Print which of the imported ledger's accounts each connected account is, recording
    each match `confirmations` names first (``hearth.matching``), or say why nothing was
    matched."""
    settings = Settings.from_environment()
    wanted = []
    for given in confirmations:
        account_id, _, ledger_account = given.partition("=")
        wanted.append((account_id.strip(), ledger_account.strip() or None))
    try:
        if wanted:
            found = matching.confirm(settings, wanted, now=system_clock())
        else:
            found = matching.survey(settings)
    except matching.MatchRefused as refused:
        sys.exit(f"hearth: {refused} Nothing was matched." if wanted else f"hearth: {refused}")
    for line in matching.describe(found):
        print(line)


def _export(username: str, out: Path | None) -> None:
    """Write `username`'s accounts and posted rows for a ledger kept outside hearth, and say
    where (``hearth.plaid.export``)."""
    settings = Settings.from_environment()
    path = out or export.default_path(settings.data_dir)
    db.initialise(settings.database)
    feed.initialise(settings.feed_database)
    conn = db.connect(settings.database)
    try:
        found = members.find(conn, normalise_username(username))
        if found is None:
            sys.exit(f"hearth: no member called {username}.")
        with feed.Feed.open(settings.feed_database) as held:
            document = export.export(conn, held, found.id, now=system_clock())
    except booking.Refused as refused:
        sys.exit(f"hearth: {refused}")
    finally:
        conn.close()
    export.write(path, document)
    print(path)


def _compare(ledger: Path, cycles: Path | None, *, monthly: bool, accounts: list[str]) -> None:
    """Print how hearth's own books, as they would be with no ledger to inherit, compare with
    `ledger` (``hearth.comparison``).

    Nothing of the household's is written: the books are computed from a copy of its two
    databases in a throwaway directory, the ledger is read through the read-only path, and
    the books file is not written.
    """
    data_dir = data_dir_from_environment()
    try:
        steps = comparison.statement_cycles(cycles) if cycles else []
        # Beancount reports a ledger it cannot open as an error in it, which names no cause.
        ledger.open("rb").close()
        theirs = books.load_entries(ledger)
    except comparison.ComparisonError as error:
        sys.exit(f"hearth: {error}")
    except books.LedgerError as error:
        sys.exit(f"hearth: {ledger}: {error.summary}")
    except OSError as error:
        sys.exit(f"hearth: {ledger} cannot be read: {error.strerror}.")
    with tempfile.TemporaryDirectory(prefix="hearth-compare-") as directory:
        snapshot = _snapshot(data_dir, Path(directory))
        try:
            household = books.household_entries(snapshot)
            conn = db.connect(snapshot.database)
            try:
                ours = books.compute(
                    household.entries,
                    lambda symbol, day: None,
                    now=system_clock(),
                    decide=rulings_in(conn),
                    unknown=household.unknown,
                )
            finally:
                conn.close()
        except books.LedgerError as error:
            sys.exit(f"hearth: hearth's own books cannot be computed: {error.summary}")
    try:
        found = comparison.compare(
            ours.entries,
            theirs,
            through=ours.as_of,
            cycles=steps,
            monthly=monthly,
            accounts=accounts,
        )
    except comparison.ComparisonError as error:
        sys.exit(f"hearth: {error}")
    for line in comparison.describe(found, str(ledger)):
        print(line)


def _snapshot(data_dir: Path, directory: Path) -> Settings:
    """Settings over a copy of the household's two databases in `data_dir` in `directory`,
    with no ledger to inherit: what the household's own books are made of, opened read-only
    and copied, so that nothing done with them can change the household's, nor anything be
    made in `data_dir` where there is no household."""
    snapshot = Settings.in_directory(directory)
    copies = (snapshot.database, snapshot.feed_database)
    for copy in copies:
        if not (data_dir / copy.name).exists():
            sys.exit(f"hearth: there is no household in {data_dir}: {copy.name} is missing.")
    for copy in copies:
        source = sqlite3.connect(f"{(data_dir / copy.name).as_uri()}?mode=ro", uri=True)
        target = sqlite3.connect(copy)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
    db.initialise(snapshot.database)
    feed.initialise(snapshot.feed_database)
    return snapshot


@dataclass(frozen=True, slots=True)
class _DemoHousehold:
    settings: Settings
    establish: books.Establish
    plaid: PlaidSetup
    passwords: dict[str, str]
    """Each invented member's password, by username, the admin first."""


@contextmanager
def _demo_household(*, first_review: bool) -> Iterator[_DemoHousehold]:
    """An invented household in a throwaway directory, gone when the block ends."""
    with tempfile.TemporaryDirectory(prefix="hearth-demo-") as directory:
        ledger = Path(directory) / "household.beancount"
        settings = Settings.in_directory(Path(directory), ledger=ledger)
        db.initialise(settings.database)
        conn = db.connect(settings.database)
        try:
            now = system_clock()
            passwords = demo.seed(conn, ledger, now=now, first_review=first_review)
        finally:
            conn.close()
        setup = demo.plaid(lambda: system_clock().astimezone().date(), ledger)
        feed.initialise(settings.feed_database)
        demo.connect(settings, setup, now=now)
        establish = books.from_household(settings, rulings=rulings_in, sources=(demo.PRICE_SOURCE,))
        yield _DemoHousehold(settings, establish, setup, passwords)


def _demo(host: str, port: int, *, first_review: bool = False) -> None:
    with _demo_household(first_review=first_review) as household:
        print("An invented household. Sign in as any of them:")
        for username, password in household.passwords.items():
            print(f"  {username:<8} {password}", flush=True)
        _run(household.settings, household.establish, host, port, household.plaid)


def _demo_bridge(approval: Approval, *, first_review: bool = False) -> None:
    """The demo's bridge. Its connections are all to hearth's stand-in for Plaid, which reaches
    no bank; `approval` decides only whether the app's syncs ask it, as it would for a real
    household's banks."""
    with _demo_household(first_review=first_review) as household:
        username, password = next(iter(household.passwords.items()))
        _answer_the_app(
            household.settings,
            household.establish,
            replace(household.plaid, approval=approval),
            (username, password),
        )


def _bridge(approval: Approval) -> None:
    data_dir = data_dir_from_environment()
    if not (data_dir / DATABASE_FILE).is_file():
        # Signing in to a household that is not there must not make an empty one.
        sys.exit(f"hearth: there is no household in {data_dir}.")
    settings = Settings.from_environment()
    db.initialise(settings.database)
    feed.initialise(settings.feed_database)
    establish = books.from_household(settings, rulings=rulings_in)
    _answer_the_app(settings, establish, PlaidSetup(approval=approval))


def _answer_the_app(
    settings: Settings,
    establish: books.Establish,
    plaid: PlaidSetup,
    signed_in_as: tuple[str, str] | None = None,
) -> None:
    """Answer the native app until it closes its end, recomputing as ``hearth serve`` does: at
    start-up, every morning, and whenever another process changes what the books are computed
    from. The first recompute runs in the background, so the app is told the books are loading
    instead of waiting on a silent process.

    It syncs as ``hearth serve`` does too, at start-up and every morning, and whenever the app
    asks, only when `plaid` approves something a sync asks (``hearth.refresh.Refresher``).

    The bridge is signed out until the app signs in over the wire, unless `signed_in_as` names
    a member and their password, which the demo's bridge does for its invented household."""
    runlog.configure(settings.data_dir)
    approved = ", ".join(sorted(op.value for op in plaid.approval.operations)) or "none"
    print(f"Live Plaid calls approved for this run: {approved}.", file=sys.stderr, flush=True)
    bookkeeper = books.Bookkeeper(
        establish, system_clock, standing=lambda: books.standing(settings)
    )
    bookkeeper.recompute_daily()
    watching = bookkeeper.recompute_on_change()
    refresher = Refresher(settings, plaid, bookkeeper, system_clock)
    bridge = Bridge(settings, bookkeeper, system_clock, refresher)
    syncing = refresher.daily()
    try:
        if signed_in_as is not None and bridge.sign_in(*signed_in_as) is not Outcome.SIGNED_IN:
            sys.exit("hearth: the demo household's first member could not sign in.")
        stdio.serve(bridge, sys.stdin, _write_reply)
    finally:
        syncing.set()
        bridge.sign_out()
        watching.set()
        runlog.stop(settings.data_dir)


def _write_reply(line: str) -> None:
    sys.stdout.write(line)
    sys.stdout.flush()


def _demo_from_zero(host: str, port: int) -> None:
    with tempfile.TemporaryDirectory(prefix="hearth-demo-") as directory:
        settings = Settings.in_directory(Path(directory))
        db.initialise(settings.database)
        conn = db.connect(settings.database)
        try:
            passwords = demo.seed_from_zero(conn, now=system_clock())
        finally:
            conn.close()
        setup = demo.plaid_from_zero(lambda: system_clock().astimezone().date())
        print("A household on its first day: no ledger, nothing connected. Sign in as:")
        for username, password in passwords.items():
            print(f"  {username:<8} {password}", flush=True)
        print(f"Hearth writes its books to {settings.books_file}", flush=True)
        # Nothing is priced until a brokerage connected through the stand-in gives its prices.
        establish = books.from_household(settings, rulings=rulings_in)
        _run(settings, establish, host, port, setup)
