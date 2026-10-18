"""Nothing a member may not see can be told apart from something that does not exist.

Each test asks for something that exists but is private and for something that
does not exist, and requires the two answers to be identical: same status, same
headers, same bytes. Any difference would confirm that the private thing exists.
"""

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from tests import household
from tests.conftest import World

UNSTABLE_HEADERS = {"date", "content-length"}


def comparable(response: Response) -> tuple[int, dict[str, str], str]:
    headers = {k: v for k, v in response.headers.items() if k not in UNSTABLE_HEADERS}
    return response.status_code, headers, response.text


def assert_indistinguishable(first: Response, second: Response) -> None:
    assert comparable(first) == comparable(second)


@pytest.mark.parametrize("private", ["avery_solo", "blake_solo", "joint", "unowned"])
@pytest.mark.parametrize("month", [None, household.THIS_MONTH, "garbage"])
def test_a_private_account_answers_exactly_like_a_missing_one(
    world: World, private: str, month: str | None
) -> None:
    casey = world.signed_in("casey")
    query = f"?month={month}" if month else ""

    hidden = casey.get(f"/accounts/{world.account_ids[private]}{query}")
    missing = casey.get(f"/accounts/AAAAAAAAAAAAAAAA{query}")

    assert hidden.status_code == 404
    assert_indistinguishable(hidden, missing)


def test_a_private_account_answers_like_a_missing_one_even_to_the_admin(world: World) -> None:
    avery = world.signed_in("avery")

    hidden = avery.get(f"/accounts/{world.account_ids['blake_solo']}")
    missing = avery.get("/accounts/AAAAAAAAAAAAAAAA")

    assert hidden.status_code == 404
    assert_indistinguishable(hidden, missing)


def test_a_malformed_month_answers_like_a_missing_page(world: World) -> None:
    casey = world.signed_in("casey")

    assert_indistinguishable(
        casey.get("/members/blake?month=2026-99"), casey.get("/members/nobody")
    )


@pytest.mark.parametrize(
    "path",
    [
        "/admin/members",
        "/members/nobody",
        "/accounts/nonexistent",
        "/no/such/page",
        "/openapi.json",
    ],
)
def test_every_dead_end_looks_the_same_to_a_member(world: World, path: str) -> None:
    casey = world.signed_in("casey")

    assert_indistinguishable(casey.get(path), casey.get("/members/nobody"))


def test_admin_actions_against_real_and_invented_members_look_the_same_to_a_member(
    world: World,
) -> None:
    casey = world.signed_in("casey")
    form = {"csrf": world.csrf(casey)}

    real = casey.post("/admin/members/blake/reset-password", data=form)
    invented = casey.post("/admin/members/nobody/reset-password", data=form)

    assert real.status_code == 404
    assert_indistinguishable(real, invented)


def anonymous_redirect(client: TestClient, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 303
    location = response.headers["location"]
    return location.replace(quote(path, safe=""), "<requested>")


def test_signing_in_is_asked_for_identically_whether_or_not_the_account_exists(
    world: World,
) -> None:
    client = world.anonymous()

    real = anonymous_redirect(client, f"/accounts/{world.account_ids['blake_solo']}")
    invented = anonymous_redirect(client, "/accounts/AAAAAAAAAAAAAAAA")
    member = anonymous_redirect(client, "/members/blake")
    nobody = anonymous_redirect(client, "/members/nobody")

    assert real == invented == member == nobody == "/sign-in?next=<requested>"


def test_continuing_after_sign_in_to_a_private_account_lands_on_not_found(world: World) -> None:
    hidden_path = f"/accounts/{world.account_ids['blake_solo']}"
    for target in (hidden_path, "/accounts/AAAAAAAAAAAAAAAA"):
        client = world.anonymous()
        response = client.post(
            "/sign-in",
            data={
                "username": "casey",
                "password": "casey-invented-passphrase",
                "destination": target,
            },
        )
        assert response.headers["location"] == target
    casey = world.signed_in("casey")
    assert_indistinguishable(casey.get(hidden_path), casey.get("/accounts/AAAAAAAAAAAAAAAA"))


@pytest.mark.parametrize(
    "destination",
    [
        "https://elsewhere.example/",
        "//elsewhere.example/",
        "/\\elsewhere.example/",
        "javascript:alert(1)",
        "/members/blake\r\nSet-Cookie: x=y",
    ],
)
def test_sign_in_never_redirects_off_site(world: World, destination: str) -> None:
    response = world.anonymous().post(
        "/sign-in",
        data={
            "username": "casey",
            "password": "casey-invented-passphrase",
            "destination": destination,
        },
    )

    assert response.headers["location"] == "/"


def test_a_wrong_password_and_an_unknown_username_are_answered_the_same(world: World) -> None:
    wrong_password = world.anonymous().post(
        "/sign-in", data={"username": "blake", "password": "not-the-password"}
    )
    unknown_user = world.anonymous().post(
        "/sign-in", data={"username": "nobody", "password": "not-the-password"}
    )

    assert wrong_password.status_code == 400
    assert_indistinguishable(
        wrong_password,
        Response(
            unknown_user.status_code,
            headers=unknown_user.headers,
            text=unknown_user.text.replace("nobody", "blake"),
        ),
    )


def test_throttling_treats_real_and_unknown_usernames_alike(world: World) -> None:
    def attempts_until_throttled(username: str) -> int:
        client = world.anonymous()
        for attempt in range(1, 20):
            response = client.post(
                "/sign-in", data={"username": username, "password": "not-the-password"}
            )
            if response.status_code == 429:
                return attempt
        raise AssertionError("never throttled")

    assert attempts_until_throttled("blake") == attempts_until_throttled("nobody") == 6
