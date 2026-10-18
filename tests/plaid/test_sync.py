"""Sync is incremental, resumes where it stopped, and records every run, however it ended."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from hearth.plaid.client import LOGIN_REQUIRED, MUTATED_DURING_PAGINATION, PlaidClient
from hearth.plaid.fake import FakePlaid, FakeTransaction
from hearth.plaid.feed import LEASE, Coverage, Feed, Outcome
from hearth.plaid.registry import History, ItemState, Registry
from hearth.plaid.sync import SyncBusyError, sync_item
from tests.plaid.conftest import NOW, TODAY, Clock, held_by, linked


def transaction_count(feed: Feed, registry: Registry, item_id: str) -> int:
    return sum(feed.held(a.account_id).transactions for a in registry.accounts_of(item_id))


def test_the_first_sync_brings_everything_and_the_next_only_what_changed(
    client: PlaidClient, fake: FakePlaid, registry: Registry, feed: Feed
) -> None:
    item, token = linked(client, fake, registry, "ins_lakeside")
    fake.page_size = 50

    first = sync_item(
        item, client=client, access_token=token, registry=registry, feed=feed, clock=Clock()
    )
    held = transaction_count(feed, registry, item.item_id)
    fake.change(
        item.item_id, "added", FakeTransaction("Invented Stall", Decimal("3.10"), TODAY), "chk"
    )
    second = sync_item(
        item, client=client, access_token=token, registry=registry, feed=feed, clock=Clock()
    )

    assert first.run.outcome is Outcome.COMPLETE
    assert first.run.from_start
    assert first.run.pages > 1
    assert first.run.added == held
    assert (second.run.from_start, second.run.pages, second.run.added) == (False, 1, 1)
    assert transaction_count(feed, registry, item.item_id) == held + 1
    stored = registry.item(item.item_id)
    assert stored is not None
    assert stored.history is History.COMPLETE
    assert stored.last_synced_at is not None


def test_a_sync_that_fails_part_way_resumes_from_the_last_page_it_stored(
    client: PlaidClient, fake: FakePlaid, registry: Registry, feed: Feed
) -> None:
    item, token = linked(client, fake, registry, "ins_lakeside")
    fake.page_size = 50
    pages = 0

    def flaky(url: str, body: bytes) -> tuple[int, bytes]:
        nonlocal pages
        if url.endswith("/transactions/sync"):
            pages += 1
            if pages == 3:
                raise ConnectionResetError("the line dropped")
        return fake(url, body)

    flaky_client = PlaidClient(client._credentials, client._approval, flaky)
    stopped = sync_item(
        item, client=flaky_client, access_token=token, registry=registry, feed=feed, clock=Clock()
    )
    kept = transaction_count(feed, registry, item.item_id)
    resumed = sync_item(
        item, client=client, access_token=token, registry=registry, feed=feed, clock=Clock()
    )

    assert stopped.run.outcome is Outcome.FAILED
    assert stopped.run.pages == 2
    assert kept == 100
    assert not resumed.run.from_start
    total = transaction_count(feed, registry, item.item_id)
    assert resumed.run.added == total - kept
    everything = sync_item(
        item, client=client, access_token=token, registry=registry, feed=feed, clock=Clock()
    )
    assert everything.run.added == 0


def test_a_sync_killed_mid_run_is_recorded_as_interrupted_not_running(
    client: PlaidClient, fake: FakePlaid, registry: Registry, feed: Feed
) -> None:
    item, token = linked(client, fake, registry, "ins_lakeside")
    fake.page_size = 50
    pages = 0

    def killed(url: str, body: bytes) -> tuple[int, bytes]:
        nonlocal pages
        if url.endswith("/transactions/sync"):
            pages += 1
            if pages == 2:
                raise KeyboardInterrupt
        return fake(url, body)

    killing = PlaidClient(client._credentials, client._approval, killed)
    with pytest.raises(KeyboardInterrupt):
        sync_item(
            item, client=killing, access_token=token, registry=registry, feed=feed, clock=Clock()
        )

    (run,) = feed.runs(item.item_id, now=NOW)
    assert run.outcome is Outcome.INTERRUPTED
    assert run.pages == 1


def test_a_run_left_running_by_a_dead_process_is_closed_as_interrupted(
    client: PlaidClient, fake: FakePlaid, registry: Registry, feed: Feed
) -> None:
    item, token = linked(client, fake, registry, "ins_pinecone")
    feed.start_run(item.item_id, now=NOW - LEASE - timedelta(minutes=1))

    sync_item(item, client=client, access_token=token, registry=registry, feed=feed, clock=Clock())

    newest, abandoned = feed.runs(item.item_id, now=NOW)
    assert (newest.outcome, abandoned.outcome) == (Outcome.COMPLETE, Outcome.INTERRUPTED)


def test_an_item_another_sync_holds_is_not_synced_again_until_its_lease_runs_out(
    client: PlaidClient, fake: FakePlaid, registry: Registry, feed: Feed
) -> None:
    item, token = linked(client, fake, registry, "ins_pinecone")
    feed.start_run(item.item_id, now=NOW - LEASE + timedelta(minutes=1))
    sent = len(fake.calls)

    with pytest.raises(SyncBusyError):
        sync_item(
            item, client=client, access_token=token, registry=registry, feed=feed, clock=Clock()
        )

    assert len(fake.calls) == sent
    (held,) = feed.runs(item.item_id, now=NOW)
    assert held.outcome is Outcome.RUNNING
    later = Clock()
    later.now = NOW + timedelta(minutes=2)
    synced = sync_item(
        item, client=client, access_token=token, registry=registry, feed=feed, clock=later
    )
    assert synced.run.outcome is Outcome.COMPLETE


def test_a_run_whose_process_has_gone_is_reclaimed_at_once(
    client: PlaidClient, fake: FakePlaid, registry: Registry, feed: Feed, gone_process: str
) -> None:
    item, token = linked(client, fake, registry, "ins_pinecone")
    held_by(feed, item.item_id, gone_process, at=NOW)

    (shown,) = feed.runs(item.item_id, now=NOW)
    synced = sync_item(
        item, client=client, access_token=token, registry=registry, feed=feed, clock=Clock()
    )

    assert shown.outcome is Outcome.INTERRUPTED
    assert synced.run.outcome is Outcome.COMPLETE
    newest, abandoned = feed.runs(item.item_id, now=NOW)
    assert (newest.outcome, abandoned.outcome) == (Outcome.COMPLETE, Outcome.INTERRUPTED)


def test_a_run_another_live_process_owns_is_respected_until_its_lease_lapses(
    client: PlaidClient, fake: FakePlaid, registry: Registry, feed: Feed, other_process: str
) -> None:
    item, token = linked(client, fake, registry, "ins_pinecone")
    held_by(feed, item.item_id, other_process, at=NOW)

    with pytest.raises(SyncBusyError):
        sync_item(
            item, client=client, access_token=token, registry=registry, feed=feed, clock=Clock()
        )
    (shown,) = feed.runs(item.item_id, now=NOW)
    assert shown.outcome is Outcome.RUNNING

    lapsed = Clock()
    lapsed.now = NOW + LEASE + timedelta(minutes=1)
    synced = sync_item(
        item, client=client, access_token=token, registry=registry, feed=feed, clock=lapsed
    )
    assert synced.run.outcome is Outcome.COMPLETE


def test_a_process_id_reused_by_another_process_is_not_taken_for_the_owner(
    client: PlaidClient, fake: FakePlaid, registry: Registry, feed: Feed, other_process: str
) -> None:
    item, token = linked(client, fake, registry, "ins_pinecone")
    pid, _, _ = other_process.partition("@")
    held_by(feed, item.item_id, f"{pid}@Thu Jan  1 00:00:00 1970", at=NOW)

    synced = sync_item(
        item, client=client, access_token=token, registry=registry, feed=feed, clock=Clock()
    )

    assert synced.run.outcome is Outcome.COMPLETE


def test_data_changing_between_pages_restarts_the_loop_without_duplicates(
    client: PlaidClient, fake: FakePlaid, registry: Registry, feed: Feed
) -> None:
    item, token = linked(client, fake, registry, "ins_lakeside")
    fake.page_size = 100
    pages = 0

    def mutating(url: str, body: bytes) -> tuple[int, bytes]:
        nonlocal pages
        if url.endswith("/transactions/sync"):
            pages += 1
            if pages == 3:
                fake.fail_next("/transactions/sync", MUTATED_DURING_PAGINATION)
        return fake(url, body)

    restarting = PlaidClient(client._credentials, client._approval, mutating)
    synced = sync_item(
        item, client=restarting, access_token=token, registry=registry, feed=feed, clock=Clock()
    )
    direct = len(fake._items[item.item_id].log)

    assert synced.run.outcome is Outcome.COMPLETE
    assert transaction_count(feed, registry, item.item_id) == direct


def test_a_restarted_loop_keeps_nothing_the_aborted_one_brought(
    client: PlaidClient, fake: FakePlaid, registry: Registry, feed: Feed
) -> None:
    item, token = linked(client, fake, registry, "ins_lakeside")
    fake.page_size = 100
    log = fake._items[item.item_id].log
    pages = 0
    replaced = ""

    def replacing(url: str, body: bytes) -> tuple[int, bytes]:
        nonlocal pages, replaced
        if url.endswith("/transactions/sync"):
            pages += 1
            if pages == 2:
                # The bank re-keys a transaction page 1 brought: replayed from the start, the
                # old id is neither added nor removed.
                kind, raw = log[0]
                replaced = raw["transaction_id"]
                log[0] = (kind, {**raw, "transaction_id": "tx-rekeyed"})
                fake.fail_next("/transactions/sync", MUTATED_DURING_PAGINATION)
        return fake(url, body)

    restarting = PlaidClient(client._credentials, client._approval, replacing)
    synced = sync_item(
        item, client=restarting, access_token=token, registry=registry, feed=feed, clock=Clock()
    )

    kept = {
        t.transaction_id
        for a in registry.accounts_of(item.item_id)
        for t in feed.transactions(a.account_id)
    }
    assert synced.run.outcome is Outcome.COMPLETE
    assert replaced
    assert replaced not in kept
    assert "tx-rekeyed" in kept
    assert len(kept) == synced.run.added == len(log)


def test_a_connection_removed_while_it_syncs_stays_removed(
    client: PlaidClient, fake: FakePlaid, registry: Registry, feed: Feed
) -> None:
    item, token = linked(client, fake, registry, "ins_lakeside")
    fake.page_size = 50
    pages = 0

    def removed_part_way(url: str, body: bytes) -> tuple[int, bytes]:
        nonlocal pages
        if url.endswith("/transactions/sync"):
            pages += 1
            if pages == 2:
                client.remove(token)
                registry.remove(item.item_id, at=Clock()())
        return fake(url, body)

    removing = PlaidClient(client._credentials, client._approval, removed_part_way)
    synced = sync_item(
        item, client=removing, access_token=token, registry=registry, feed=feed, clock=Clock()
    )

    assert synced.run.outcome is Outcome.FAILED
    stored = registry.item(item.item_id)
    assert stored is not None
    assert stored.state is ItemState.REMOVED


def test_a_bank_wanting_a_new_sign_in_marks_the_connection_and_keeps_its_history(
    client: PlaidClient, fake: FakePlaid, registry: Registry, feed: Feed
) -> None:
    item, token = linked(client, fake, registry, "ins_pinecone")
    sync_item(item, client=client, access_token=token, registry=registry, feed=feed, clock=Clock())
    held = transaction_count(feed, registry, item.item_id)
    fake.require_login(item.item_id)

    synced = sync_item(
        item, client=client, access_token=token, registry=registry, feed=feed, clock=Clock()
    )

    assert synced.run.outcome is Outcome.LOGIN_REQUIRED
    stored = registry.item(item.item_id)
    assert stored is not None
    assert stored.state is ItemState.LOGIN_REQUIRED
    assert stored.problem == LOGIN_REQUIRED
    assert transaction_count(feed, registry, item.item_id) == held


def test_coverage_is_what_each_account_actually_came_with(
    client: PlaidClient, fake: FakePlaid, registry: Registry, feed: Feed
) -> None:
    card, card_token = linked(client, fake, registry, "ins_pinecone")
    bank, bank_token = linked(client, fake, registry, "ins_lakeside")
    for item, token in ((card, card_token), (bank, bank_token)):
        sync_item(
            item, client=client, access_token=token, registry=registry, feed=feed, clock=Clock()
        )
    asked_from = TODAY - timedelta(days=729)

    def coverage(item_id: str) -> Coverage:
        (account, *_) = registry.accounts_of(item_id)
        return Coverage(account.account_id, asked_from, feed.held(account.account_id), True)

    short, full = coverage(card.item_id), coverage(bank.item_id)
    assert short.is_short
    assert short.held.earliest is not None
    assert short.held.earliest >= TODAY - timedelta(days=90)
    assert not full.is_short
    assert full.held.earliest == asked_from


def test_history_arrives_after_linking_and_is_never_taken_for_short_history(
    client: PlaidClient, fake: FakePlaid, registry: Registry, feed: Feed
) -> None:
    item, token = linked(client, fake, registry, "ins_lakeside", ready=False)
    asked_from = TODAY - timedelta(days=729)
    seen = []
    for _ in range(4):
        sync_item(
            item, client=client, access_token=token, registry=registry, feed=feed, clock=Clock()
        )
        stored = registry.item(item.item_id)
        assert stored is not None
        (account, *_) = registry.accounts_of(item.item_id)
        held = feed.held(account.account_id)
        coverage = Coverage(
            account.account_id, asked_from, held, stored.history is History.COMPLETE
        )
        seen.append(
            (stored.history, held.transactions > 0, coverage.complete and coverage.is_short)
        )

    assert seen == [
        (History.NOT_READY, False, False),
        (History.INITIAL, True, False),
        (History.INITIAL, True, False),
        (History.COMPLETE, True, False),
    ]
    assert feed.held(account.account_id).earliest == date(2024, 10, 19)
