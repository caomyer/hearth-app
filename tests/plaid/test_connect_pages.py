"""Connecting institutions, end to end through the pages, against the stand-in for Plaid.

Every live step is checked before it is taken: a member out of connections, or on a hearth not
approved for the call, is told so and nothing reaches Plaid. A member reaches only their own
connections; anyone else's answers exactly like one that does not exist.
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from hearth import db, members
from hearth.members import MemberId
from hearth.plaid.approval import Approval, Operation
from hearth.plaid.fake import DEMO_INSTITUTIONS
from hearth.plaid.feed import Feed, Outcome
from hearth.plaid.registry import ItemState, Registry
from hearth.vault.errors import TokenNotFoundError
from hearth.vault.secret import Secret
from hearth.vault.tokens import TokenVault
from tests import household
from tests.conftest import World, link
from tests.plaid.conftest import held_by
from tests.plaid.linking import add_dana, form, save_keys, stand_in_tokens
from tests.test_concealment import assert_indistinguishable

LINK_CALL = "/link/token/create"


def test_the_connection_count_comes_from_the_members_own_plaid_account(world: World) -> None:
    avery = world.signed_in("avery")

    page = avery.get("/connect").text

    # 10 allowed, 4 in use outside hearth, 1 here.
    assert "5 of 10 connections remaining" in page
    assert "5 of 10 connections remaining" in avery.get("/settings/connections").text


def test_a_new_member_chooses_a_plaid_account_before_anything_can_open(world: World) -> None:
    dana = add_dana(world)

    page = dana.get("/connect").text
    refused = dana.post("/connect/link", data=form(world, dana))

    assert "Choose how to connect" in page
    assert "Recommended" in page
    assert "Connect a bank</button>" not in page
    assert refused.headers["location"] == "/connect?outcome=no-plaid-account"
    assert LINK_CALL not in world.plaid.calls[-1:]


def test_the_households_account_is_used_only_once_chosen_and_counts_only_its_own(
    world: World,
) -> None:
    avery = world.signed_in("avery")
    save_keys(world, avery, scope="household", back="settings", used_elsewhere="3")
    dana = add_dana(world)

    before = dana.get("/connect").text
    dana.post("/connect/household", data=form(world, dana, chosen="true"))
    after = dana.get("/connect").text

    assert "Or use the household's Plaid account" in before
    assert "Registered by Avery: 7 of 10 connections remaining" in before
    assert "The household&#39;s Plaid account" in after
    assert "7 of 10 connections remaining" in after


def test_a_member_with_no_connection_left_is_told_before_plaid_is_asked(world: World) -> None:
    avery = world.signed_in("avery")
    save_keys(
        world,
        avery,
        client_id="client-avery",
        secret="secret-avery",
        used_elsewhere="9",
    )
    calls = len(world.plaid.calls)

    page = avery.get("/connect").text
    refused = avery.post("/connect/link", data=form(world, avery))

    assert "0 of 10 connections remaining" in page
    assert "No connections left on your Plaid account." in page
    assert "Remove a connection you no longer use" in page
    assert 'action="/connect/link"' not in page
    assert refused.headers["location"] == "/connect?outcome=no-slot"
    assert world.plaid.calls[calls:] == []


def test_a_live_call_this_hearth_was_not_approved_for_sends_nothing(world: World) -> None:
    sent: list[str] = []

    def network(url: str, body: bytes) -> tuple[int, bytes]:
        sent.append(url)
        raise AssertionError("no request may leave without approval")

    hearth = world.app.state.hearth
    world.app.state.hearth = replace(hearth, plaid=replace(hearth.plaid, transport=network))
    dana = add_dana(world)
    save_keys(world, dana, environment="production")

    page = dana.get("/connect").text
    refused = dana.post("/connect/link", data=form(world, dana))

    assert "Connecting a bank is not switched on for this hearth yet." in page
    assert refused.headers["location"] == "/connect?outcome=not-approved"
    assert sent == []


def test_approving_reads_does_not_approve_creating_a_connection(world: World) -> None:
    sent: list[str] = []

    def network(url: str, body: bytes) -> tuple[int, bytes]:
        sent.append(url)
        raise AssertionError("no request may leave without approval")

    hearth = world.app.state.hearth
    plaid = replace(hearth.plaid, transport=network, approval=Approval.of([Operation.READ]))
    world.app.state.hearth = replace(hearth, plaid=plaid)
    dana = add_dana(world)
    save_keys(world, dana, environment="production")

    refused = dana.post("/connect/link", data=form(world, dana))

    assert refused.headers["location"] == "/connect?outcome=not-approved"
    assert sent == []


def test_connecting_asks_for_730_days_and_shows_what_each_account_came_with(
    world: World,
) -> None:
    dana = add_dana(world)
    save_keys(world, dana)

    opened = dana.post("/connect/link", data=form(world, dana))
    tokens = stand_in_tokens(opened.text)
    connected = dana.post(
        "/connect/exchange",
        data=form(world, dana, public_token=tokens["Gryphon Mutual"], institution="ignored"),
    )
    page = dana.get("/connect").text

    assert opened.status_code == 200
    assert connected.headers["location"] == "/connect?outcome=connected"
    days = [requested for _, requested, _ in world.plaid._link_tokens.values()]
    assert days
    assert set(days) == {730}
    assert "Gryphon Mutual" in page
    assert "9 of 10 connections remaining" in page
    # The invented bank sent one transaction from today: far short of two years, and said so.
    assert "Gryphon Mutual sent 1 day," in page or "1 day only" in page
    assert "Upload CSV statements" in page


def test_the_link_page_alone_may_load_plaids_script(world: World) -> None:
    from hearth.web.routes.connect import LINK_POLICY

    avery = world.signed_in("avery")

    assert "cdn.plaid.com" not in avery.get("/connect").headers["Content-Security-Policy"]
    assert "cdn.plaid.com" in LINK_POLICY


def test_mapping_files_an_account_only_where_its_member_may(world: World) -> None:
    blake = world.signed_in("blake")
    checking = next(a.account_id for a in _accounts(world, "blake") if a.type == "depository")

    forged = blake.post(
        "/connect/map",
        data=form(
            world,
            blake,
            account=checking,
            **{
                f"target-{checking}": "Assets:Zq:AverySolo",
                f"owners-{checking}": str(world.member_ids["blake"]),
                f"confirm-{checking}": "1",
            },
        ),
    )

    assert forged.headers["location"] == "/connect/map?outcome=not-understood"
    assert all(a.hearth_account is None for a in _accounts(world, "blake"))


def test_one_ledger_account_is_fed_by_one_connection(world: World) -> None:
    def confirm_into_joint(username: str) -> str:
        client = world.signed_in(username)
        checking = next(a.account_id for a in _accounts(world, username) if a.type == "depository")
        response = client.post(
            "/connect/map",
            data=form(
                world,
                client,
                account=checking,
                **{
                    f"target-{checking}": "Assets:Zq:Joint",
                    f"owners-{checking}": "ignored for an existing account",
                    f"confirm-{checking}": "1",
                },
            ),
        )
        return str(response.headers["location"])

    first = confirm_into_joint("blake")
    second = confirm_into_joint("avery")

    assert first == "/connect/map?outcome=partly-confirmed"
    assert second == "/connect/map?outcome=target-taken"


def test_a_co_owner_is_shown_a_shared_account_is_fed_without_naming_whose_or_which_bank(
    world: World,
) -> None:
    blake = world.signed_in("blake")
    checking = next(a.account_id for a in _accounts(world, "blake") if a.type == "depository")
    blake.post(
        "/connect/map",
        data=form(
            world,
            blake,
            account=checking,
            **{f"target-{checking}": "Assets:Zq:Joint", f"confirm-{checking}": "1"},
        ),
    )

    page = world.signed_in("avery").get("/connect/map").text

    assert (
        '<option value="Assets:Zq:Joint" disabled>Assets:Zq:Joint'
        " (another connected account already goes here)</option>"
    ) in page
    blakes = next(p for p in household.PLAID if p.username == "blake")
    for detail in (blakes.institution, blakes.checking, blakes.checking_mask, blakes.card):
        assert detail not in page


def test_someone_not_owning_a_fed_account_learns_nothing_of_it(world: World) -> None:
    casey = world.signed_in("casey")
    before = casey.get("/connect/map").text
    blake = world.signed_in("blake")
    checking = next(a.account_id for a in _accounts(world, "blake") if a.type == "depository")
    blake.post(
        "/connect/map",
        data=form(
            world,
            blake,
            account=checking,
            **{f"target-{checking}": "Assets:Zq:Joint", f"confirm-{checking}": "1"},
        ),
    )

    after = casey.get("/connect/map").text

    assert after == before
    assert "Assets:Zq:Joint" not in after
    assert "another connection" not in after


def test_a_connection_whose_institution_plaid_cannot_name_is_still_kept(world: World) -> None:
    world.plaid.fail_next("/item/get", "INTERNAL_SERVER_ERROR")

    with world.connector("avery") as connector:
        start = connector.start_link(served_at="http://testserver")
        token = world.plaid.public_token(start.link_token, "ins_avery")
        item_id = connector.finish_link(Secret(token), "Gryphon Mutual")

    conn = world.connect()
    try:
        item = Registry(conn).item(item_id)
        assert item is not None
        assert (item.state, item.institution, item.last_synced_at is not None) == (
            ItemState.WORKING,
            "Gryphon Mutual",
            True,
        )
        TokenVault(conn, world.setup.keys()).get(world.member_ids["avery"], item_id)
    finally:
        conn.close()
    avery = world.signed_in("avery")
    assert "4 of 10 connections remaining" in avery.get("/connect").text
    assert f"/connect/items/{item_id}/remove" in avery.get("/settings/connections").text


def test_a_connection_neither_plaid_nor_link_names_is_called_your_bank(world: World) -> None:
    world.plaid.fail_next("/item/get", "INTERNAL_SERVER_ERROR")

    item_id = link(world, "avery", "ins_avery")

    avery = world.signed_in("avery")
    imported = avery.get("/connect/import")
    assert imported.status_code == 200
    assert "Your " in imported.text
    conn = world.connect()
    try:
        item = Registry(conn).item(item_id)
        assert item is not None
        assert item.institution == "Your bank"
    finally:
        conn.close()
    assert "Your bank" in avery.get("/settings/connections").text


def test_history_plaid_sends_later_is_asked_for_on_widening_waits_until_it_lands(
    world: World,
) -> None:
    dana = add_dana(world)
    save_keys(world, dana)
    seen: list[tuple[float, int, str, str]] = []

    def look(seconds: float) -> None:
        seen.append(
            (
                seconds,
                _held_by(world, "dana"),
                dana.get("/connect/import").text,
                dana.get("/connect").text,
            )
        )

    world.waits.then = look
    link(world, "dana", "ins_avery")

    (first, *_) = seen
    assert first[1] == 0
    assert "Plaid has not sent all of it yet. Hearth asks again at" in first[2]
    assert "keeps asking if you leave this page" in first[2]
    assert 'hx-get="/connect/import"' in first[2]
    assert "Nothing in yet." in first[3]
    waits = [seconds for seconds, *_ in seen]
    assert waits == sorted(waits)
    assert waits[0] < waits[-1]
    assert _held_by(world, "dana") > 0
    imported = dana.get("/connect/import").text
    assert "Every bank has sent what it gives" in imported
    assert 'hx-get="/connect/import"' not in imported
    assert "730 days" in dana.get("/connect").text


def test_update_now_while_history_is_followed_starts_no_second_follower(world: World) -> None:
    from hearth.plaid.sync import check_waits

    dana = add_dana(world)
    save_keys(world, dana)
    pressed: list[str] = []

    def press_update_now(seconds: float) -> None:
        if pressed:
            return
        item_id = next(i.item_id for i in _items_of(world, "dana"))
        before = world.plaid.calls.count("/transactions/sync")
        response = dana.post(f"/connect/items/{item_id}/sync", data=form(world, dana))
        assert response.status_code == 303
        pressed.append(str(world.plaid.calls.count("/transactions/sync") - before))

    world.waits.then = press_update_now
    world.plaid.slow_history = True
    link(world, "dana", "ins_avery")

    assert pressed == ["0"]
    assert world.waits.seconds == [w.total_seconds() for w in check_waits()]


def test_a_household_sync_leaves_an_item_another_sync_holds(world: World) -> None:
    from hearth.plaid.connector import sync_all
    from hearth.plaid.sync import Skipped, Synced

    casey = world.item_ids["casey"]
    with Feed.open(world.settings.feed_database) as feed:
        assert feed.start_run(casey, now=world.clock()) is not None
        conn = world.connect()
        try:
            done = sync_all(conn, world.settings.feed_database, world.setup, world.clock)
        finally:
            conn.close()
        runs = feed.runs(casey, now=world.clock())

    assert Skipped(casey, "another sync is running") in done
    assert {s.item_id for s in done if isinstance(s, Synced)} == {
        world.item_ids["avery"],
        world.item_ids["blake"],
    }
    newest, *_ = runs
    assert newest.outcome is Outcome.RUNNING


def test_a_run_left_by_a_process_that_has_gone_neither_holds_the_item_nor_the_pages(
    world: World, gone_process: str
) -> None:
    from hearth.plaid.connector import sync_all
    from hearth.plaid.sync import Synced

    casey = world.item_ids["casey"]
    client = world.signed_in("casey")
    with Feed.open(world.settings.feed_database) as feed:
        held_by(feed, casey, gone_process, at=world.clock())
    imported = client.get("/connect/import").text

    assert 'hx-get="/connect/import"' not in imported
    assert "Fetching history" not in imported
    assert "Asking Plaid for it now" not in imported
    conn = world.connect()
    try:
        done = sync_all(conn, world.settings.feed_database, world.setup, world.clock)
    finally:
        conn.close()
    assert any(isinstance(s, Synced) and s.item_id == casey for s in done)

    with Feed.open(world.settings.feed_database) as feed:
        held_by(feed, casey, gone_process, at=world.clock())
    before = _runs(world, casey)
    response = client.post(f"/connect/items/{casey}/sync", data=form(world, client))
    assert response.status_code == 303
    with Feed.open(world.settings.feed_database) as feed:
        newest, *_ = feed.runs(casey, now=world.clock())
    assert _runs(world, casey) == before + 1
    assert newest.outcome is Outcome.COMPLETE


def test_a_household_sync_leaves_an_item_whose_history_is_being_followed(world: World) -> None:
    from hearth.plaid.connector import sync_all
    from hearth.plaid.sync import Skipped

    dana = add_dana(world)
    save_keys(world, dana)
    world.plaid.slow_history = True
    found: list[list[object]] = []

    def daily_sync(seconds: float) -> None:
        if found:
            return
        conn = world.connect()
        try:
            (item,) = Registry(conn).items_of(world_member(world, "dana"))
            before = _runs(world, item.item_id)
            done = sync_all(conn, world.settings.feed_database, world.setup, world.clock)
        finally:
            conn.close()
        found.append([s for s in done if s.item_id == item.item_id])
        assert _runs(world, item.item_id) == before

    world.waits.then = daily_sync
    item_id = link(world, "dana", "ins_avery")

    assert found == [[Skipped(item_id, "history is being followed")]]
    conn = world.connect()
    try:
        Registry(conn).next_check(item_id, world.clock() + timedelta(minutes=5))
        done = sync_all(conn, world.settings.feed_database, world.setup, world.clock)
    finally:
        conn.close()
    assert Skipped(item_id, "history is being followed") in done


def test_the_pages_keep_saying_hearth_is_asking_until_the_follower_stops(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hearth.plaid import connector as connector_module

    dana = add_dana(world)
    save_keys(world, dana)
    pages: list[tuple[str, str]] = []
    real_sync = connector_module._sync

    def look_then_sync(*args: object, **kwargs: object) -> object:
        pages.append((dana.get("/connect/import").text, dana.get("/connect").text))
        return real_sync(*args, **kwargs)  # type: ignore[arg-type]

    def time_passes(seconds: float) -> None:
        world.clock.advance(timedelta(seconds=seconds + 1))
        pages.append((dana.get("/connect/import").text, dana.get("/connect").text))

    monkeypatch.setattr(connector_module, "_sync", look_then_sync)
    world.waits.then = time_passes
    link(world, "dana", "ins_avery")

    assert len(pages) > 2
    for imported, connect in pages:
        assert 'hx-get="/connect/import"' in imported
        assert "stopped asking" not in imported
        assert 'hx-get="/connect"' in connect
        assert "The daily update collects the rest." not in connect
    done = dana.get("/connect/import").text
    assert "Every bank has sent what it gives" in done
    assert 'hx-get="/connect/import"' not in done


def test_history_still_arriving_is_left_to_the_daily_sync_after_a_bounded_wait(
    world: World,
) -> None:
    from hearth.plaid.sync import CHECKING_FOR, LONGEST_WAIT, check_waits

    dana = add_dana(world)
    save_keys(world, dana)
    world.plaid.slow_history = True

    link(world, "dana", "ins_avery")

    assert world.waits.seconds == [w.total_seconds() for w in check_waits()]
    assert max(world.waits.seconds) <= LONGEST_WAIT.total_seconds()
    assert sum(world.waits.seconds) <= CHECKING_FOR.total_seconds()
    imported = dana.get("/connect/import").text
    assert "Hearth has stopped asking for now" in imported
    assert "The daily update collects the rest each morning." in imported
    assert 'hx-get="/connect/import"' not in imported


def test_removing_a_connection_plaid_no_longer_has_still_removes_it(world: World) -> None:
    casey = world.signed_in("casey")
    item_id = world.item_ids["casey"]
    world.plaid.fail_next("/item/remove", "ITEM_NOT_FOUND")

    removed = casey.post(f"/connect/items/{item_id}/remove", data=form(world, casey))

    assert removed.headers["location"] == "/settings/connections?outcome=removed"
    conn = world.connect()
    try:
        item = Registry(conn).item(item_id)
        assert item is not None
        assert item.state is ItemState.REMOVED
        with pytest.raises(TokenNotFoundError):
            TokenVault(conn, world.setup.keys()).get(world.member_ids["casey"], item_id)
    finally:
        conn.close()
    assert "6 of 10 connections remaining" in casey.get("/settings/connections").text


def test_two_targets_a_word_apart_are_named_before_confirming(world: World) -> None:
    lakeside = next(i for i in DEMO_INSTITUTIONS if i.id == "ins_lakeside")
    world.plaid.institutions[lakeside.id] = lakeside
    dana = add_dana(world)
    save_keys(world, dana)
    link(world, "dana", lakeside.id)

    page = dana.get("/connect/map").text

    assert "Two targets differ by one word." in page
    assert (
        "<code>Assets:Dana:Lakeside:Checking</code> and <code>Assets:Dana:Lakeside:Savings</code>"
        in page
    )
    assert page.count("panel map is-warn") == 2


def test_removing_frees_the_slot_and_keeps_every_transaction(world: World) -> None:
    casey = world.signed_in("casey")
    item_id = world.item_ids["casey"]
    held = _held(world, "casey")

    confirm = casey.get(f"/connect/items/{item_id}/remove").text
    removed = casey.post(f"/connect/items/{item_id}/remove", data=form(world, casey))

    assert "Remove the connection to Kraken Federal?" in confirm
    assert "Plaid cannot undo it" in confirm
    assert "remove first, then connect" in confirm
    assert removed.headers["location"] == "/settings/connections?outcome=removed"
    assert world.plaid.calls[-1] == "/item/remove"
    assert _held(world, "casey") == held > 0
    conn = world.connect()
    try:
        item = Registry(conn).item(item_id)
        assert item is not None
        assert item.state is ItemState.REMOVED
        with pytest.raises(TokenNotFoundError):
            TokenVault(conn, world.setup.keys()).get(world.member_ids["casey"], item_id)
    finally:
        conn.close()
    assert "6 of 10 connections remaining" in casey.get("/settings/connections").text


def test_removing_without_approval_sends_nothing_and_keeps_the_connection(world: World) -> None:
    dana = add_dana(world)
    save_keys(world, dana)
    item_id = link(world, "dana", "ins_avery")
    sent: list[str] = []

    def network(url: str, body: bytes) -> tuple[int, bytes]:
        sent.append(url)
        raise AssertionError("no request may leave without approval")

    conn = world.connect()
    try:
        conn.execute("UPDATE plaid_credentials SET environment = 'production'")
    finally:
        conn.close()
    hearth = world.app.state.hearth
    world.app.state.hearth = replace(hearth, plaid=replace(hearth.plaid, transport=network))

    page = dana.get(f"/connect/items/{item_id}/remove").text
    refused = dana.post(f"/connect/items/{item_id}/remove", data=form(world, dana))

    assert "Removing is not switched on for this hearth yet." in page
    assert refused.headers["location"] == "/settings/connections?outcome=not-approved"
    assert sent == []
    assert item_id in dana.get("/settings/connections").text


@pytest.mark.parametrize("path", ["/connect/items/{}/remove"])
def test_someone_elses_connection_answers_exactly_like_a_missing_one(
    world: World, path: str
) -> None:
    avery = world.signed_in("avery")

    hidden = avery.get(path.format(world.item_ids["blake"]))
    missing = avery.get(path.format("item-demo-000000000000"))

    assert hidden.status_code == 404
    assert_indistinguishable(hidden, missing)


@pytest.mark.parametrize("action", ["remove", "sync"])
def test_nobody_acts_on_someone_elses_connection(world: World, action: str) -> None:
    avery = world.signed_in("avery")
    blakes = world.item_ids["blake"]
    calls = len(world.plaid.calls)

    response = avery.post(f"/connect/items/{blakes}/{action}", data=form(world, avery))
    reconnect = avery.post("/connect/link", data=form(world, avery, item_id=blakes))
    upload = avery.post(
        f"/connect/accounts/{_accounts(world, 'blake')[0].account_id}/statements",
        data=form(world, avery),
        files={"statement": ("s.csv", b"date,amount\n", "text/csv")},
    )

    assert (response.status_code, reconnect.status_code, upload.status_code) == (404, 404, 404)
    assert world.plaid.calls[calls:] == []
    conn = world.connect()
    try:
        item = Registry(conn).item(blakes)
        assert item is not None
        assert item.state is ItemState.WORKING
    finally:
        conn.close()


def test_a_statement_is_kept_for_the_import_and_only_readable_by_its_owner(world: World) -> None:
    casey = world.signed_in("casey")
    account = _accounts(world, "casey")[0].account_id

    kept = casey.post(
        f"/connect/accounts/{account}/statements",
        data=form(world, casey),
        files={
            "statement": ("../../etc/statement.csv", b"date,amount\n2025-01-01,1.00\n", "text/csv")
        },
    )
    unreadable = casey.post(
        f"/connect/accounts/{account}/statements",
        data=form(world, casey),
        files={"statement": ("x.csv", b"\xff\xfe\x00binary", "text/csv")},
    )

    assert kept.headers["location"] == "/connect?outcome=statement"
    assert unreadable.headers["location"] == "/connect?outcome=statement-unreadable"
    stored = list((world.settings.data_dir / "statements").rglob("*.csv"))
    assert len(stored) == 1
    assert stored[0].parent.parent == world.settings.data_dir / "statements"
    assert stored[0].stat().st_mode & 0o777 == 0o600


def _accounts(world: World, username: str) -> list:  # type: ignore[type-arg]
    conn = db.connect(world.settings.database)
    try:
        return Registry(conn).accounts_of(world.item_ids[username])
    finally:
        conn.close()


def world_member(world: World, username: str) -> MemberId:
    conn = world.connect()
    try:
        member = members.find(conn, username)
        assert member is not None
        return member.id
    finally:
        conn.close()


def _runs(world: World, item_id: str) -> int:
    with Feed.open(world.settings.feed_database) as feed:
        return len(feed.runs(item_id, now=world.clock()))


def _items_of(world: World, username: str) -> list:  # type: ignore[type-arg]
    conn = db.connect(world.settings.database)
    try:
        member = members.find(conn, username)
        assert member is not None
        return Registry(conn).items_of(member.id)
    finally:
        conn.close()


def _held_by(world: World, username: str) -> int:
    conn = db.connect(world.settings.database)
    try:
        registry = Registry(conn)
        member = world.member_ids.get(username) or next(
            m.id for m in [members.find(conn, username)] if m is not None
        )
        accounts = [a for i in registry.items_of(member) for a in registry.accounts_of(i.item_id)]
    finally:
        conn.close()
    with Feed.open(world.settings.feed_database) as feed:
        return sum(feed.held(a.account_id).transactions for a in accounts)


def _held(world: World, username: str) -> int:
    with Feed.open(world.settings.feed_database) as feed:
        return sum(feed.held(a.account_id).transactions for a in _accounts(world, username))
