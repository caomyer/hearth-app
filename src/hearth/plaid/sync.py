"""Bringing an Item up to date: incremental, resumable, and recorded.

Each sync asks Plaid only for what changed since the Item's stored cursor (from the beginning
for a new Item), stores each page with the cursor it moves to in one transaction, and records
the run: when, how many pages, how many transactions added, modified and removed, and how it
ended. A run that stops part way, for any reason, resumes on the next sync from the last page
it stored, and the stopped run is recorded as interrupted rather than silently forgotten.

Plaid sends a new Item's history after linking, not with it: the first sync usually finds
nothing, the last month arrives next, and the rest of the two years later still. Plaid
announces each step with its SYNC_UPDATES_AVAILABLE webhook, but hearth runs on its
household's own machine with no public address, so it can never receive a webhook. It asks
instead: after a link or a new sign-in it syncs again on the waits `check_waits` gives,
until Plaid says the history is complete or the waits run out, after which the daily sync
collects the rest.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from hearth.clock import Clock
from hearth.plaid.client import (
    LOGIN_REQUIRED,
    MUTATED_DURING_PAGINATION,
    PlaidClient,
    PlaidError,
    PlaidRejectedError,
)
from hearth.plaid.feed import Feed, Outcome, Run
from hearth.plaid.registry import Item, ItemState, Registry
from hearth.vault.secret import Secret

_log = logging.getLogger(__name__)

MAX_PAGES = 2000
"""A guard against a cursor that never stops: well beyond two years of any account's history."""

MAX_RESTARTS = 3

FIRST_CHECK = timedelta(minutes=1)
LONGEST_WAIT = timedelta(minutes=30)
"""Plaid limits how often an Item may be synced; no wait between checks is longer than this,
and none is shorter than `FIRST_CHECK`."""
CHECKING_FOR = timedelta(hours=6)
"""How long history still arriving is asked for before it is left to the daily sync."""


def check_waits() -> Iterator[timedelta]:
    """The waits between syncs while history is arriving: doubling from `FIRST_CHECK` up to
    `LONGEST_WAIT`, and adding up to no more than `CHECKING_FOR`."""
    wait, spent = FIRST_CHECK, timedelta(0)
    while spent + wait <= CHECKING_FOR:
        yield wait
        spent += wait
        wait = min(wait * 2, LONGEST_WAIT)


class SyncStoppedError(RuntimeError):
    """Plaid kept sending pages past `MAX_PAGES`; the run stops where it is and resumes later."""


class SyncBusyError(RuntimeError):
    """Another sync holds the Item (`Feed.start_run`), so this one did not run."""


@dataclass(frozen=True, slots=True)
class Synced:
    item_id: str
    run: Run


@dataclass(frozen=True, slots=True)
class Skipped:
    """An Item a sync of the whole household left alone, and why."""

    item_id: str
    reason: str


def sync_item(
    item: Item,
    *,
    client: PlaidClient,
    access_token: Secret,
    registry: Registry,
    feed: Feed,
    clock: Clock,
) -> Synced:
    """Sync one Item and say how it went. Raises nothing Plaid causes: that is the outcome.

    Raises `SyncBusyError`, having sent nothing, if another sync of the Item is running."""
    run = feed.start_run(item.item_id, now=clock())
    if run is None:
        raise SyncBusyError(item.item_id)
    try:
        accounts = client.accounts(access_token)
        registry.record_accounts(item.item_id, accounts)
        feed.record_balances(item.item_id, accounts, now=clock())
        _pages(item, client, access_token, feed, run, clock)
    except PlaidRejectedError as error:
        if error.error_code == LOGIN_REQUIRED:
            registry.set_state(item.item_id, ItemState.LOGIN_REQUIRED, problem=error.error_code)
            return Synced(item.item_id, feed.finish(run, Outcome.LOGIN_REQUIRED, now=clock()))
        return _failed(item, registry, feed, run, clock, str(error))
    except PlaidError as error:
        return _failed(item, registry, feed, run, clock, str(error))
    except SyncStoppedError as error:
        return Synced(
            item.item_id, feed.finish(run, Outcome.INTERRUPTED, now=clock(), problem=str(error))
        )
    except BaseException:
        # Recorded as interrupted rather than left running, so the record never claims a run
        # is in progress that is not; the cursor already stored is where the next one resumes.
        feed.finish(run, Outcome.INTERRUPTED, now=clock(), problem="stopped on an unexpected error")
        raise
    finished = feed.finish(run, Outcome.COMPLETE, now=clock())
    registry.synced(item.item_id, at=finished.finished_at or clock(), history=finished.history)
    return Synced(item.item_id, finished)


def _pages(
    item: Item, client: PlaidClient, access_token: Secret, feed: Feed, run: Run, clock: Clock
) -> None:
    start = feed.cursor(item.item_id)
    restarts = 0
    pages = 0
    while True:
        try:
            page = client.sync_page(access_token, feed.cursor(item.item_id))
        except PlaidRejectedError as error:
            # Plaid's data changed between pages. Its instruction is to discard what this
            # loop brought and start it again from the cursor it began at.
            if error.error_code != MUTATED_DURING_PAGINATION or restarts >= MAX_RESTARTS:
                raise
            restarts += 1
            pages = 0
            feed.restart_from(run, item.item_id, start, now=clock())
            continue
        feed.apply(run, item.item_id, page, now=clock())
        pages += 1
        if not page.has_more:
            return
        if pages >= MAX_PAGES:
            raise SyncStoppedError(f"Plaid sent more than {MAX_PAGES} pages; stopping here")


def _failed(
    item: Item, registry: Registry, feed: Feed, run: Run, clock: Clock, problem: str
) -> Synced:
    _log.warning("sync of a connection failed: %s", problem)
    registry.set_state(item.item_id, item.state, problem=problem)
    return Synced(item.item_id, feed.finish(run, Outcome.FAILED, now=clock(), problem=problem))


def last_success(runs: list[Run]) -> datetime | None:
    return next((r.finished_at for r in runs if r.outcome is Outcome.COMPLETE), None)
