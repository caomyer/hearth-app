"""Command-line entry point: ``hearth serve``, ``hearth add-member``, ``hearth add-account``,
``hearth accounts``, ``hearth plaid``, ``hearth compare`` and ``hearth demo``.

No command makes a live Plaid call unless it is approved on the command line: ``hearth serve
--approve-plaid OPERATION`` for the web pages, and ``--approved`` for each ``hearth plaid``
command that calls Plaid. Without it, they refuse before anything is sent.
"""

import argparse
import logging
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import uvicorn
from beancount.core.data import Open

from hearth import accounts, auth, books, comparison, db, demo, members
from hearth.accounts import AccountRejectedError
from hearth.app import create_app
from hearth.clock import Clock, system_clock
from hearth.config import Settings, data_dir_from_environment
from hearth.disclosure import rulings_in
from hearth.members import MemberRejectedError
from hearth.plaid import cli as plaid_cli
from hearth.plaid import feed
from hearth.plaid.approval import DESCRIPTIONS, Approval, Operation
from hearth.plaid.connector import sync_all
from hearth.plaid.setup import PlaidSetup
from hearth.reports.common import is_balance_sheet

_log = logging.getLogger(__name__)


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

    plaid = commands.add_parser("plaid", help="the household's Plaid connections")
    plaid_commands = plaid.add_subparsers(dest="plaid_command", required=True)
    plaid_commands.add_parser(
        "items", help="list every connection and each Plaid account's allowance (no live call)"
    )
    sync = plaid_commands.add_parser("sync", help="bring every connection up to date (a live call)")
    sync.add_argument("--approved", action="store_true", help="the captain approved this live call")
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

    arguments = parser.parse_args(argv)
    match arguments.command:
        case "serve":
            _serve(arguments.host, arguments.port, Approval.of(arguments.approve_plaid))
        case "plaid":
            sys.exit(plaid_cli.run(arguments, Settings.from_environment()))
        case "add-member":
            _add_member(arguments.username, arguments.name, is_admin=arguments.admin)
        case "add-account":
            _add_account(arguments.ledger_account, arguments.name, arguments.owner)
        case "accounts":
            _list_accounts()
        case "compare":
            _compare(
                arguments.ledger,
                arguments.cycles,
                monthly=arguments.monthly or arguments.cycles is None,
                accounts=arguments.account,
            )
        case "demo":
            if arguments.from_zero:
                _demo_from_zero(arguments.host, arguments.port)
            else:
                _demo(arguments.host, arguments.port, first_review=arguments.first_review)


def _add_address(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)


def _serve(host: str, port: int, approval: Approval) -> None:
    settings = Settings.from_environment()
    approved = ", ".join(sorted(op.value for op in approval.operations)) or "none"
    print(f"Live Plaid calls approved for this run: {approved}.", flush=True)
    setup = PlaidSetup(approval=approval)
    _run(settings, books.from_household(settings, rulings=rulings_in), host, port, setup)


def _run(
    settings: Settings, establish: books.Establish, host: str, port: int, plaid: PlaidSetup
) -> None:
    """Serve, recomputing at start-up and then every morning (decision 6).

    The app is created first, as that brings both databases up to date, so the first
    recompute and the first sync never find a database part way through being created.
    """
    bookkeeper = books.Bookkeeper(establish, system_clock)
    app = create_app(settings, bookkeeper, plaid=plaid)
    bookkeeper.recompute_daily()
    if plaid.approval.allows(Operation.READ):
        _sync_daily(settings, plaid, bookkeeper)
    uvicorn.run(app, host=host, port=port)


def _sync_daily(settings: Settings, plaid: PlaidSetup, bookkeeper: books.Bookkeeper) -> None:
    """Bring every working connection up to date at start-up and every morning, only when this
    run was started with reading approved (`sync_then_recompute`)."""

    def run() -> None:
        while True:
            sync_then_recompute(settings, plaid, bookkeeper, system_clock)
            threading.Event().wait(books.seconds_until(books.DAILY_AT, datetime.now()))

    threading.Thread(target=run, name="hearth-daily-sync", daemon=True).start()


def sync_then_recompute(
    settings: Settings, plaid: PlaidSetup, bookkeeper: books.Bookkeeper, clock: Clock
) -> None:
    """Sync every working connection, then recompute the books, which record how current each
    connection is: the morning's recompute starts with the sync, so it may finish first."""
    conn = db.connect(settings.database)
    try:
        sync_all(conn, settings.feed_database, plaid, clock)
    except Exception:
        _log.exception("the daily sync stopped")
    finally:
        conn.close()
    bookkeeper.recompute()


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


def _demo(host: str, port: int, *, first_review: bool = False) -> None:
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
        print("An invented household. Sign in as any of them:")
        for username, password in passwords.items():
            print(f"  {username:<8} {password}", flush=True)
        establish = books.from_household(settings, rulings=rulings_in, sources=(demo.PRICE_SOURCE,))
        _run(settings, establish, host, port, setup)


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
        establish = books.from_household(settings, rulings=rulings_in, sources=(demo.PRICE_SOURCE,))
        _run(settings, establish, host, port, setup)
