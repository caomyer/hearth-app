"""Command-line entry point: ``hearth serve``, ``hearth add-member``, ``hearth add-account``,
``hearth accounts``, ``hearth plaid`` and ``hearth demo``.

No command makes a live Plaid call unless it is approved on the command line: ``hearth serve
--approve-plaid OPERATION`` for the web pages, and ``--approved`` for each ``hearth plaid``
command that calls Plaid. Without it, they refuse before anything is sent.
"""

import argparse
import logging
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import uvicorn
from beancount.core.data import Open

from hearth import accounts, auth, books, db, demo, members
from hearth.accounts import AccountRejectedError
from hearth.app import create_app
from hearth.clock import Clock, system_clock
from hearth.config import LEDGER_VARIABLE, Settings
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

    add = commands.add_parser("add-member", help="add a member and print their one-time password")
    add.add_argument("username")
    add.add_argument("--name", required=True, help="the name the household sees")
    add.add_argument(
        "--admin", action="store_true", help="let them add members and reset passwords"
    )

    claim = commands.add_parser(
        "add-account", help="say who owns one of the ledger's accounts; nobody sees it until then"
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
        "accounts", help="list the ledger's accounts and who owns each (reads the ledger only)"
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
        case "demo":
            _demo(arguments.host, arguments.port, first_review=arguments.first_review)


def _add_address(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)


def _serve(host: str, port: int, approval: Approval) -> None:
    settings = Settings.from_environment()
    approved = ", ".join(sorted(op.value for op in approval.operations)) or "none"
    print(f"Live Plaid calls approved for this run: {approved}.", flush=True)
    setup = PlaidSetup(approval=approval)
    _run(
        settings,
        books.from_ledger(settings.ledger, settings.database, rulings=rulings_in),
        host,
        port,
        setup,
    )


def _run(
    settings: Settings, establish: books.Establish, host: str, port: int, plaid: PlaidSetup
) -> None:
    """Serve, recomputing at start-up and then every morning (decision 6)."""
    bookkeeper = books.Bookkeeper(establish, system_clock)
    bookkeeper.recompute_daily()
    if plaid.approval.allows(Operation.READ):
        _sync_daily(settings, plaid, bookkeeper)
    app = create_app(settings, bookkeeper, plaid=plaid)
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
    """The ledger's balance-sheet accounts, read through the same read-only path as serving."""
    if settings.ledger is None:
        sys.exit(f"hearth: set {LEDGER_VARIABLE} to the household's ledger first.")
    try:
        entries = books.load_entries(settings.ledger)
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
        sys.exit(f"hearth: the ledger opens no Assets or Liabilities account {ledger_account}.")
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
        establish = books.from_ledger(
            ledger, settings.database, rulings=rulings_in, sources=(demo.PRICE_SOURCE,)
        )
        _run(settings, establish, host, port, setup)
