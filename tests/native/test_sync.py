"""The app asks hearth to sync, and is told how fresh the viewer's own banks are.

A sync goes through the one path every sync takes (``hearth.refresh``), and only when the
bridge was started approved to ask something of Plaid, which the app decides by how it starts
the bridge. What the app is told of how it went is the viewer's own connections alone
(``Disclosure.banks``): the leak crawl in ``tests/native/test_doorway.py`` asks the ``banks``
screen as every member too.
"""

import json
import threading
from dataclasses import replace
from datetime import UTC, timedelta
from decimal import Decimal

import pytest

from hearth.native import stdio
from hearth.native.bridge import ACTIONS, Acted, Bridge
from hearth.plaid.approval import Approval, Operation
from hearth.plaid.client import PlaidClient
from hearth.plaid.fake import FakePlaid, FakeTransaction
from hearth.refresh import Begun, Refresher
from tests import household
from tests.conftest import World, password_of
from tests.native.test_doorway import FIXTURES, differences
from tests.vault.conftest import MemoryKeyStore

READING = Approval.of([Operation.READ])


def refresher(world: World, approval: Approval = READING) -> Refresher:
    return Refresher(
        world.settings, replace(world.setup, approval=approval), world.bookkeeper, world.clock
    )


def signed_in(world: World, username: str, syncs: Refresher | None) -> Bridge:
    bridge = Bridge(world.settings, world.bookkeeper, world.clock, syncs)
    bridge.sign_in(username, password_of(username))
    return bridge


def institution(username: str) -> str:
    return next(p.institution for p in household.PLAID if p.username == username)


def banks(bridge: Bridge) -> dict[str, object]:
    answer = bridge.answer("banks")
    assert answer["state"] == "ready", answer
    return answer


def own(answer: dict[str, object]) -> dict[str, object]:
    """The one bank in a member's answer: each member of the household connected one."""
    found = answer["banks"]
    assert isinstance(found, list)
    assert len(found) == 1, found
    bank = found[0]
    assert isinstance(bank, dict)
    return bank


def test_a_bridge_nobody_signed_in_to_does_nothing_and_says_nothing(world: World) -> None:
    calls_before = len(world.plaid.calls)
    syncs = refresher(world)
    bridge = Bridge(world.settings, world.bookkeeper, world.clock, syncs)

    for action in [*ACTIONS, "nothing-here", ""]:
        assert bridge.act(action) is Acted.SIGNED_OUT
    assert bridge.answer("banks") == {"state": "signed_out"}
    assert not syncs.status.running
    assert len(world.plaid.calls) == calls_before


def test_an_action_there_is_not_is_missing(world: World) -> None:
    bridge = signed_in(world, "avery", refresher(world))

    assert bridge.act("link") is Acted.MISSING
    assert bridge.act("remove") is Acted.MISSING
    assert bridge.act("Sync") is Acted.MISSING


def test_an_unapproved_bridge_never_reaches_the_plaid_client(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an unapproved bridge made a Plaid client")

    monkeypatch.setattr(PlaidClient, "__init__", refused)
    calls_before = len(world.plaid.calls)
    syncs = Refresher(world.settings, world.setup, world.bookkeeper, world.clock)
    bridge = signed_in(world, "avery", syncs)

    assert bridge.act("sync") is Acted.NOT_ALLOWED
    syncs.daily().set()
    syncs.wait()

    answer = banks(bridge)
    assert answer["asking"] == []
    assert answer["running"] is False
    assert answer["finished_at"] is None
    assert own(answer)["added"] is None
    assert len(world.plaid.calls) == calls_before
    # A bridge given no way to sync at all answers alike.
    assert signed_in(world, "avery", None).act("sync") is Acted.NOT_ALLOWED


def test_linking_and_removing_are_never_part_of_what_a_sync_may_ask(world: World) -> None:
    everything = Approval.of(Operation)

    assert refresher(world, everything).asking == {
        Operation.READ,
        Operation.HOLDINGS,
        Operation.ACTIVITY,
    }


def test_an_approved_sync_brings_the_viewers_new_transactions_into_the_books(
    world: World,
) -> None:
    item = world.item_ids["avery"]
    for n in range(3):
        posted = FakeTransaction(f"Corner shop {n}", Decimal("5.00"), household.NOW.date())
        world.plaid.change(item, "added", posted, "chk")
    computed_before = world.bookkeeper.status.books
    assert computed_before is not None
    world.clock.advance(timedelta(hours=1))
    an_hour_on = (household.NOW + timedelta(hours=1)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    syncs = refresher(world)
    bridge = signed_in(world, "avery", syncs)

    assert bridge.act("sync") is Acted.STARTED
    syncs.wait()

    answer = banks(bridge)
    assert answer["asking"] == ["read"]
    assert answer["running"] is False
    assert answer["finished_at"] == an_hour_on
    assert answer["stopped"] is None
    assert own(answer) == {
        "institution": institution("avery"),
        "synced_at": an_hour_on,
        "needs_sign_in": False,
        "added": 3,
        "trouble": None,
    }
    # The fetch ended in a recompute: the books after it are newer than those before it.
    computed = world.bookkeeper.status.books
    assert computed is not None
    assert computed.computed_at > computed_before.computed_at


def test_each_member_is_told_of_their_own_bank_alone(world: World) -> None:
    world.plaid.require_login(world.item_ids["avery"])
    syncs = refresher(world)
    signed_in(world, "blake", syncs).act("sync")
    syncs.wait()

    for username in household.USERNAMES:
        answer = banks(signed_in(world, username, syncs))
        told = json.dumps(answer)
        bank = own(answer)
        assert bank["institution"] == institution(username)
        others = [p.institution for p in household.PLAID if p.username != username]
        assert not any(name in told for name in others), told
        if username == "avery":
            assert bank["needs_sign_in"] is True
            assert bank["trouble"] == "sign_in"
        else:
            assert bank["needs_sign_in"] is False
            assert bank["trouble"] is None


def test_a_bank_plaid_refuses_is_said_to_its_member(world: World) -> None:
    world.plaid.fail_next("/accounts/get", "INTERNAL_SERVER_ERROR")
    syncs = refresher(world)
    bridge = signed_in(world, "avery", syncs)

    bridge.act("sync")
    syncs.wait()

    # The stand-in fails the first connection asked, whichever member's it is: only its
    # member is told.
    troubles = {
        username: own(banks(signed_in(world, username, syncs)))["trouble"]
        for username in household.USERNAMES
    }
    assert [t for t in troubles.values() if t is not None] == ["refused"]


def test_plaid_out_of_reach_is_said_as_such(world: World) -> None:
    world.plaid.unreachable = True
    syncs = refresher(world)
    bridge = signed_in(world, "casey", syncs)

    bridge.act("sync")
    syncs.wait()

    bank = own(banks(bridge))
    assert bank["trouble"] == "unreachable"
    assert bank["added"] == 0
    assert bank["synced_at"] is not None, "a sync that failed does not unsay the last one"


def test_keys_the_keychain_cannot_give_are_said_as_such(world: World) -> None:
    keys = world.setup.keys()
    assert isinstance(keys, MemoryKeyStore)
    keys.available = False
    syncs = refresher(world)
    bridge = signed_in(world, "blake", syncs)

    bridge.act("sync")
    syncs.wait()

    answer = banks(bridge)
    assert own(answer)["trouble"] == "keys"
    assert answer["stopped"] is None, "one connection's keys stop only that connection"


def test_asked_again_while_a_sync_runs_it_says_so_and_still_answers(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked, release = threading.Event(), threading.Event()
    answer_plaid = FakePlaid.__call__

    def slow(self: FakePlaid, url: str, body: bytes) -> tuple[int, bytes]:
        if url.endswith("/transactions/sync"):
            asked.set()
            assert release.wait(10), "the test never let the sync go on"
        return answer_plaid(self, url, body)

    monkeypatch.setattr(FakePlaid, "__call__", slow)
    syncs = refresher(world)
    bridge = signed_in(world, "avery", syncs)
    other = signed_in(world, "blake", syncs)

    replies: list[str] = []
    lines = [
        '{"id": 1, "act": "sync"}\n',
        '{"id": 2, "act": "sync"}\n',
        '{"id": 3, "ask": "overview"}\n',
        '{"id": 4, "ask": "banks"}\n',
    ]
    try:
        stdio.serve(bridge, lines[:1], replies.append)
        assert asked.wait(10), "the sync never reached the bank"
        stdio.serve(bridge, lines[1:], replies.append)
        assert other.act("sync") is Acted.RUNNING
    finally:
        release.set()
        syncs.wait()

    started, again, overview, status = (json.loads(line) for line in replies)
    assert started == {"id": 1, "outcome": "started"}
    assert again == {"id": 2, "outcome": "running"}
    assert overview["answer"]["state"] == "ready"
    assert status["answer"]["running"] is True
    assert banks(bridge)["running"] is False
    # A finished sync leaves the way open for the next.
    assert syncs.start() is Begun.STARTED
    syncs.wait()


def test_an_act_beside_anything_else_is_not_a_request(world: World) -> None:
    written: list[str] = []
    lines = [
        '{"id": 1, "act": "sync", "with": {}}\n',
        '{"id": 1, "act": 5}\n',
        '{"id": 1, "act": "sync", "ask": "banks"}\n',
        '{"id": 1, "act": true}\n',
    ]

    calls_before = len(world.plaid.calls)
    syncs = refresher(world)

    stdio.serve(signed_in(world, "avery", syncs), lines, written.append)

    assert [json.loads(line) for line in written] == [{"id": None, "error": "bad request"}] * 4
    assert not syncs.status.running
    assert len(world.plaid.calls) == calls_before


def test_the_banks_screen_has_the_shape_the_app_decodes(world: World) -> None:
    """``banks.json`` is what the app's own tests decode (``apple/HearthKit``)."""
    example = json.loads((FIXTURES / "banks.json").read_text())
    syncs = refresher(world)
    bridge = signed_in(world, "avery", syncs)
    bridge.act("sync")
    syncs.wait()

    told = banks(bridge)

    assert differences(told, example["answer"]) == []
    assert all(differences(own(told), bank) == [] for bank in example["answer"]["banks"])


def test_the_banks_screen_needs_no_books(world: World) -> None:
    from hearth import books

    waiting = books.Bookkeeper(world.bookkeeper_establish, world.clock)
    bridge = Bridge(world.settings, waiting, world.clock, refresher(world))
    bridge.sign_in("avery", password_of("avery"))

    assert bridge.answer("overview") == {"state": "loading"}
    assert own(banks(bridge))["institution"] == institution("avery")


def test_a_household_without_its_vault_key_says_the_keys_could_not_be_read(
    world: World,
) -> None:
    keys = world.setup.keys()
    assert isinstance(keys, MemoryKeyStore)
    keys.keys.clear()
    syncs = refresher(world)
    bridge = signed_in(world, "avery", syncs)

    bridge.act("sync")
    syncs.wait()

    answer = banks(bridge)
    assert own(answer)["trouble"] == "keys"
    assert answer["stopped"] is None


@pytest.mark.parametrize(
    "argv",
    [
        ["bridge", "--approve-plaid", "link"],
        ["bridge", "--approve-plaid", "remove"],
        ["demo", "--bridge", "--approve-plaid", "link"],
        ["demo", "--approve-plaid", "read"],
    ],
)
def test_a_bridge_cannot_be_started_approved_to_link_or_remove(argv: list[str]) -> None:
    from hearth import cli

    with pytest.raises(SystemExit) as refused:
        cli.main(argv)

    assert refused.value.code == 2
