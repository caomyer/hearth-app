"""Signing in, sessions, passwords and the audit trail they leave."""

import re
from datetime import timedelta

from fastapi.testclient import TestClient

from hearth import sessions, throttle
from hearth.web.deps import SESSION_COOKIE
from tests.conftest import World, password_of


def sign_in(client: TestClient, username: str, password: str) -> int:
    response = client.post("/sign-in", data={"username": username, "password": password})
    return response.status_code


def is_signed_in(client: TestClient) -> bool:
    return client.get("/").status_code == 200


def one_time_password(page: str) -> str:
    match = re.search(r'<p class="one-time-password[^"]*"[^>]*>([^<]+)</p>', page)
    assert match, page
    return match[1]


def test_the_session_cookie_is_httponly_samesite_and_signed(world: World) -> None:
    response = world.anonymous().post(
        "/sign-in", data={"username": "casey", "password": password_of("casey")}
    )

    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie


def test_usernames_are_not_case_sensitive(world: World) -> None:
    assert sign_in(world.anonymous(), "  Casey ", password_of("casey")) == 303


def test_passwords_are_stored_as_argon2id_hashes(world: World) -> None:
    conn = world.connect()
    try:
        hashes = [row[0] for row in conn.execute("SELECT password_hash FROM members")]
    finally:
        conn.close()

    assert hashes
    assert all(h.startswith("$argon2id$") for h in hashes)


def test_a_cookie_that_was_not_issued_here_is_refused(world: World) -> None:
    casey = world.signed_in("casey")
    real = casey.cookies[SESSION_COOKIE]
    token, _, mac = real.rpartition(".")
    forgeries = [
        f"{token}.{'0' * len(mac)}",
        f"{token}x.{mac}",
        sessions.CookieSigner(b"another key entirely").sign(token),
        token,
        "",
    ]

    for forgery in forgeries:
        client = world.anonymous()
        client.cookies.set(SESSION_COOKIE, forgery)
        assert not is_signed_in(client), forgery


def test_a_stolen_database_cannot_be_replayed_as_a_cookie(world: World) -> None:
    casey = world.signed_in("casey")
    conn = world.connect()
    try:
        (stored,) = conn.execute("SELECT token_hash FROM sessions").fetchone()
    finally:
        conn.close()

    thief = world.anonymous()
    thief.cookies.set(
        SESSION_COOKIE, sessions.CookieSigner(world.settings.signing_key).sign(stored)
    )
    assert is_signed_in(casey)
    assert not is_signed_in(thief)


def test_signing_out_revokes_the_session_on_the_server(world: World) -> None:
    casey = world.signed_in("casey")
    cookie = casey.cookies[SESSION_COOKIE]

    casey.post("/sign-out", data={"csrf": world.csrf(casey)})

    replay = world.anonymous()
    replay.cookies.set(SESSION_COOKIE, cookie)
    assert not is_signed_in(replay)


def test_a_session_expires_after_its_lifetime(world: World) -> None:
    casey = world.signed_in("casey")

    world.clock.advance(sessions.LIFETIME - timedelta(seconds=1))
    assert is_signed_in(casey)
    world.clock.advance(timedelta(seconds=1))
    assert not is_signed_in(casey)


def test_repeated_failures_lock_a_username_out_even_with_the_right_password(
    world: World,
) -> None:
    client = world.anonymous()
    for _ in range(throttle.FAILURES_PER_USERNAME):
        assert sign_in(client, "casey", "not-the-password") == 400

    assert sign_in(client, "casey", password_of("casey")) == 429
    world.clock.advance(throttle.WINDOW)
    assert sign_in(client, "casey", password_of("casey")) == 303


def test_one_client_cannot_spray_guesses_across_many_usernames(world: World) -> None:
    client = world.anonymous()
    for attempt in range(throttle.FAILURES_PER_CLIENT):
        assert sign_in(client, f"guess{attempt}", "not-the-password") == 400

    assert sign_in(client, "casey", password_of("casey")) == 429


def test_an_admin_reset_gives_a_one_time_password_and_signs_the_member_out(
    world: World,
) -> None:
    blake = world.signed_in("blake")
    avery = world.signed_in("avery")

    page = avery.post("/admin/members/blake/reset-password", data={"csrf": world.csrf(avery)})
    temporary = one_time_password(page.text)

    assert not is_signed_in(blake)
    assert sign_in(world.anonymous(), "blake", password_of("blake")) == 400
    fresh = world.anonymous()
    response = fresh.post("/sign-in", data={"username": "blake", "password": temporary})
    assert response.headers["location"] == "/settings/password"
    # Until they choose their own password, a one-time password opens nothing else.
    for path in ("/", "/members/blake", "/settings/sharing", "/activity"):
        assert fresh.get(path).headers["location"] == "/settings/password"

    new_password = "blake-chose-this-one"
    response = fresh.post(
        "/settings/password",
        data={
            "csrf": world.csrf(fresh),
            "current": temporary,
            "new": new_password,
            "confirm": new_password,
        },
    )
    assert response.headers["location"] == "/"
    assert is_signed_in(fresh)
    assert sign_in(world.anonymous(), "blake", temporary) == 400


def test_every_password_reset_is_on_the_record_for_the_admin_and_the_member(
    world: World,
) -> None:
    avery = world.signed_in("avery")
    page = avery.post("/admin/members/blake/reset-password", data={"csrf": world.csrf(avery)})
    blake = world.anonymous()
    blake.post("/sign-in", data={"username": "blake", "password": one_time_password(page.text)})
    new_password = "blake-chose-this-one"
    blake.post(
        "/settings/password",
        data={
            "csrf": world.csrf(blake),
            "current": one_time_password(page.text),
            "new": new_password,
            "confirm": new_password,
        },
    )

    reset = "Avery reset Blake\N{RIGHT SINGLE QUOTATION MARK}s password"
    assert reset in avery.get("/activity").text
    assert reset in blake.get("/activity").text
    assert reset not in world.signed_in("casey").get("/activity").text


def test_a_member_reads_only_what_happened_to_them(world: World) -> None:
    blake = world.signed_in("blake")
    blake.post("/settings/sharing/transactions", data={"csrf": world.csrf(blake), "shared": "true"})

    event = "Blake now shares transactions"
    assert event in blake.get("/activity").text
    assert event in world.signed_in("avery").get("/activity").text
    casey_activity = world.signed_in("casey").get("/activity").text
    assert event not in casey_activity
    assert "Blake signed in" not in casey_activity
    assert "Casey signed in" in casey_activity


def test_changing_ones_password_signs_out_every_other_session(world: World) -> None:
    laptop = world.signed_in("casey")
    phone = world.signed_in("casey")
    new_password = "casey-new-passphrase"

    response = laptop.post(
        "/settings/password",
        data={
            "csrf": world.csrf(laptop),
            "current": password_of("casey"),
            "new": new_password,
            "confirm": new_password,
        },
    )

    assert response.status_code == 303
    assert is_signed_in(laptop)
    assert not is_signed_in(phone)


def test_a_password_change_needs_the_current_password(world: World) -> None:
    casey = world.signed_in("casey")

    response = casey.post(
        "/settings/password",
        data={"csrf": world.csrf(casey), "current": "wrong", "new": "x" * 12, "confirm": "x" * 12},
    )

    assert response.status_code == 400
    assert "Your current password is not right." in response.text
    assert sign_in(world.anonymous(), "casey", password_of("casey")) == 303


def test_an_admin_adds_a_member_who_must_choose_a_password(world: World) -> None:
    avery = world.signed_in("avery")

    page = avery.post(
        "/admin/members",
        data={"csrf": world.csrf(avery), "username": "dana", "display_name": "Dana"},
    )

    temporary = one_time_password(page.text)
    dana = world.anonymous()
    response = dana.post("/sign-in", data={"username": "dana", "password": temporary})
    assert response.headers["location"] == "/settings/password"
    assert "Avery added Dana" in avery.get("/activity").text


def test_a_taken_username_is_refused(world: World) -> None:
    avery = world.signed_in("avery")

    page = avery.post(
        "/admin/members",
        data={"csrf": world.csrf(avery), "username": "Blake", "display_name": "Another"},
    )

    assert page.status_code == 400
    assert "That username is taken." in page.text
