"""``hearth plaid``: the household's connections, for whoever runs hearth on this Mac.

    hearth plaid items                     every connection and every allowance; no live call
    hearth plaid sync --approved           bring every working connection up to date
    hearth plaid remove ITEM --approved    end one connection at Plaid, freeing its slot

This is the machine's operator's view, not a page: whoever can run commands as the user
hearth runs as can already read the database and the Keychain (``hearth.vault``), so it
lists every member's connections, which is what finding a leaked slot needs. The pages show
each member only their own.

A command that calls Plaid refuses, before anything is sent, unless given ``--approved``.
"""

import argparse
import sqlite3
from datetime import UTC, datetime

from hearth import db, members
from hearth.clock import system_clock
from hearth.config import Settings
from hearth.plaid import feed
from hearth.plaid.approval import Approval, ApprovalRequired, Operation
from hearth.plaid.client import PlaidError
from hearth.plaid.connector import sync_all
from hearth.plaid.credentials import CredentialStore
from hearth.plaid.registry import Registry
from hearth.plaid.setup import PlaidSetup
from hearth.plaid.sync import Skipped
from hearth.vault.errors import VaultError
from hearth.vault.tokens import TokenVault

REFUSED = 2


def run(arguments: argparse.Namespace, settings: Settings) -> int:
    db.initialise(settings.database)
    feed.initialise(settings.feed_database)
    match arguments.plaid_command:
        case "items":
            return items(settings)
        case "sync":
            return sync(settings, approved=arguments.approved)
        case "remove":
            return remove(settings, arguments.item_id, approved=arguments.approved)
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


def sync(settings: Settings, *, approved: bool) -> int:
    if not approved:
        print(
            "hearth plaid sync makes live Plaid calls against real accounts; pass --approved"
            " once the captain has approved it. Nothing was sent."
        )
        return REFUSED
    setup = PlaidSetup(approval=Approval.of([Operation.READ]))
    conn = db.connect(settings.database)
    try:
        for synced in sync_all(conn, settings.feed_database, setup, system_clock):
            if isinstance(synced, Skipped):
                print(f"{synced.item_id}: skipped, {synced.reason}")
                continue
            run = synced.run
            print(
                f"{synced.item_id}: {run.outcome.value}, {run.pages} page(s),"
                f" +{run.added} ~{run.modified} -{run.removed}"
                + (f" ({run.problem})" if run.problem else "")
            )
    finally:
        conn.close()
    return 0


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
