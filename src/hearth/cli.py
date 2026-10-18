"""Command-line entry point: ``hearth serve``, ``hearth add-member`` and ``hearth demo``."""

import argparse
import sys
import tempfile
from pathlib import Path

import uvicorn

from hearth import auth, books, db, demo
from hearth.app import create_app
from hearth.clock import system_clock
from hearth.config import Settings
from hearth.connections import Connections
from hearth.members import MemberRejectedError


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="hearth", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="serve the household on this machine")
    _add_address(serve)

    add = commands.add_parser("add-member", help="add a member and print their one-time password")
    add.add_argument("username")
    add.add_argument("--name", required=True, help="the name the household sees")
    add.add_argument(
        "--admin", action="store_true", help="let them add members and reset passwords"
    )

    try_it = commands.add_parser(
        "demo", help="serve an invented household from a throwaway directory"
    )
    _add_address(try_it)

    arguments = parser.parse_args(argv)
    match arguments.command:
        case "serve":
            _serve(arguments.host, arguments.port)
        case "add-member":
            _add_member(arguments.username, arguments.name, is_admin=arguments.admin)
        case "demo":
            _demo(arguments.host, arguments.port)


def _add_address(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)


def _serve(host: str, port: int) -> None:
    settings = Settings.from_environment()
    _run(settings, books.from_ledger(settings.ledger, settings.database), host, port)


def _run(
    settings: Settings,
    establish: books.Establish,
    host: str,
    port: int,
    connections: Connections | None = None,
) -> None:
    """Serve, recomputing at start-up and then every morning (decision 6)."""
    bookkeeper = books.Bookkeeper(establish, system_clock)
    bookkeeper.recompute_daily()
    app = create_app(settings, bookkeeper, connections=connections)
    uvicorn.run(app, host=host, port=port)


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


def _demo(host: str, port: int) -> None:
    with tempfile.TemporaryDirectory(prefix="hearth-demo-") as directory:
        ledger = Path(directory) / "household.beancount"
        settings = Settings.in_directory(Path(directory), ledger=ledger)
        db.initialise(settings.database)
        conn = db.connect(settings.database)
        try:
            now = system_clock()
            passwords = demo.seed(conn, ledger, now=now)
            connections = demo.connections(conn, now.date())
        finally:
            conn.close()
        print("An invented household. Sign in as any of them:")
        for username, password in passwords.items():
            print(f"  {username:<8} {password}", flush=True)
        establish = books.from_ledger(ledger, settings.database, sources=(demo.PRICE_SOURCE,))
        _run(settings, establish, host, port, connections)
