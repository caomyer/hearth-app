"""Who the app asks as is decided by hearth: a bridge says nothing until a member signs in.

Signing in is the pages' own sign-in (``hearth.auth.sign_in``), so a wrong password and an
unknown username are one outcome, the same throttle applies, and a one-time password is
changed in the pages before the app may use it.
"""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from hearth import auth, books, db, members, sessions, throttle
from hearth.disclosure import rulings_in
from hearth.native import doorway as asks
from hearth.native.bridge import SIGNED_OUT, Acted, Bridge, Outcome
from hearth.native.doorway import Doorway
from tests.conftest import FakeClock, World, password_of
from tests.native import on_disk


def bridge(world: World) -> Bridge:
    return Bridge(world.settings, world.bookkeeper, world.clock)


def live_sessions(world: World, username: str) -> int:
    conn = world.connect()
    try:
        (count,) = conn.execute(
            "SELECT count(*) FROM sessions WHERE member_id = ? AND revoked_at IS NULL",
            (world.member_ids[username],),
        ).fetchone()
    finally:
        conn.close()
    return int(count)


def test_a_bridge_nobody_has_signed_in_to_answers_nothing_about_the_household(
    world: World,
) -> None:
    door = bridge(world)

    for ask in [*asks.SCREENS, "nothing-here", ""]:
        assert door.answer(ask) == SIGNED_OUT


def test_a_member_who_signs_in_is_answered_with_their_own_view(world: World) -> None:
    door = bridge(world)

    assert door.sign_in("Avery ", password_of("avery")) is Outcome.SIGNED_IN

    own = Doorway(world.settings, world.bookkeeper, "avery")
    for ask in [*asks.SCREENS, "nothing-here"]:
        assert door.answer(ask) == own.answer(ask)
    assert door.answer("overview")["state"] == "ready"


def test_a_wrong_password_and_an_unknown_username_are_told_alike(world: World) -> None:
    door = bridge(world)

    wrong = door.sign_in("avery", "not avery's password")
    unknown = door.sign_in("mallory", "not avery's password")

    assert wrong is unknown is Outcome.REJECTED
    assert door.answer("overview") == SIGNED_OUT


def test_a_failed_sign_in_signs_out_whoever_was_signed_in(world: World) -> None:
    door = bridge(world)
    door.sign_in("avery", password_of("avery"))

    assert door.sign_in("blake", "not blake's password") is Outcome.REJECTED

    assert door.answer("overview") == SIGNED_OUT
    assert live_sessions(world, "avery") == 0


@pytest.mark.parametrize("username", ["avery", "mallory"])
def test_too_many_failures_throttle_a_username_whether_or_not_it_is_a_member(
    world: World, username: str
) -> None:
    door = bridge(world)
    for _ in range(throttle.FAILURES_PER_USERNAME):
        assert door.sign_in(username, "a guess at the password") is Outcome.REJECTED

    assert door.sign_in(username, password_of("avery")) is Outcome.THROTTLED
    assert door.answer("overview") == SIGNED_OUT

    world.clock.advance(throttle.WINDOW + timedelta(seconds=1))
    expected = Outcome.SIGNED_IN if username == "avery" else Outcome.REJECTED
    assert door.sign_in(username, password_of("avery")) is expected


def test_the_app_is_throttled_as_one_client_across_usernames(world: World) -> None:
    door = bridge(world)
    for n in range(throttle.FAILURES_PER_CLIENT):
        door.sign_in(f"guess{n}", "a guess at the password")

    assert door.sign_in("blake", password_of("blake")) is Outcome.THROTTLED


def test_a_one_time_password_is_changed_in_the_pages_before_the_app_signs_in(
    world: World,
) -> None:
    conn = world.connect()
    try:
        admin = members.find(conn, "avery")
        blake = members.find(conn, "blake")
        assert admin is not None
        assert blake is not None
        temporary = auth.reset_password(conn, admin, blake, now=world.clock())
    finally:
        conn.close()
    door = bridge(world)

    assert door.sign_in("blake", temporary) is Outcome.MUST_CHANGE_PASSWORD

    assert door.answer("overview") == SIGNED_OUT
    assert live_sessions(world, "blake") == 0


def test_a_password_reset_elsewhere_signs_the_app_out(world: World) -> None:
    door = bridge(world)
    door.sign_in("blake", password_of("blake"))
    assert door.answer("overview")["state"] == "ready"

    conn = world.connect()
    try:
        admin = members.find(conn, "avery")
        blake = members.find(conn, "blake")
        assert admin is not None
        assert blake is not None
        auth.reset_password(conn, admin, blake, now=world.clock())
    finally:
        conn.close()

    assert door.answer("overview") == SIGNED_OUT


def test_a_session_that_has_run_its_course_signs_the_app_out(world: World) -> None:
    door = bridge(world)
    door.sign_in("casey", password_of("casey"))

    world.clock.advance(sessions.LIFETIME)

    assert door.answer("overview") == SIGNED_OUT


def test_signing_out_ends_the_session_it_held(world: World) -> None:
    door = bridge(world)
    door.sign_in("avery", password_of("avery"))
    assert live_sessions(world, "avery") == 1

    assert door.sign_out() is Outcome.SIGNED_OUT

    assert live_sessions(world, "avery") == 0
    assert door.answer("overview") == SIGNED_OUT
    assert door.sign_out() is Outcome.SIGNED_OUT


def test_every_outcome_is_one_the_app_decodes() -> None:
    """``outcomes.json`` is what the app's own tests decode (``apple/HearthKit``)."""
    told = json.loads((Path(__file__).parent / "fixtures" / "outcomes.json").read_text())

    assert told == [outcome.value for outcome in Outcome]


def test_every_outcome_of_an_action_is_one_the_app_decodes() -> None:
    """``acted.json`` is what the app's own tests decode (``apple/HearthKit``)."""
    told = json.loads((Path(__file__).parent / "fixtures" / "acted.json").read_text())

    assert told == [outcome.value for outcome in Acted]


def test_the_household_the_mac_app_is_tested_on_is_worth_what_its_test_expects(
    tmp_path: Path, clock: FakeClock
) -> None:
    """The household ``tests.native.on_disk`` writes for the Mac app's end-to-end test, read
    as ``hearth bridge`` reads it: from its own books, with no ledger."""
    settings = on_disk.build(tmp_path / "data", "fixture-passphrase", now=clock())
    keeper = books.Bookkeeper(books.from_household(settings, rulings=rulings_in), clock)
    keeper.recompute(wait=True)
    assert keeper.status.failure is None, keeper.status.failure
    door = Bridge(settings, keeper, clock)

    assert door.answer("overview") == SIGNED_OUT
    assert door.sign_in("avery", "fixture-passphrase") is Outcome.SIGNED_IN
    answer = door.answer("overview")

    assert answer["state"] == "ready"
    worth = answer["net_worth"]
    assert isinstance(worth, dict)
    assert worth["value"] == format(on_disk.NET_WORTH, "f")
    assert worth["is_known"] is True
    assert worth["is_partial"] is False
    conn = db.connect(settings.database)
    try:
        assert [m.username for m in members.all_members(conn)] == list(on_disk.USERNAMES)
    finally:
        conn.close()
