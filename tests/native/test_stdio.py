"""The bridge's wire: a line in, a line out, and a bad line never stops it."""

import json

from hearth.native import stdio
from hearth.native.bridge import Bridge
from tests.conftest import World, password_of


def answers(world: World, *lines: str) -> list[dict[str, object]]:
    written: list[str] = []
    stdio.serve(Bridge(world.settings, world.bookkeeper, world.clock), lines, written.append)
    assert all(line.endswith("\n") and line.count("\n") == 1 for line in written)
    return [json.loads(line) for line in written]


def sign_in(username: str, password: str, number: int = 1) -> str:
    request = {"id": number, "sign_in": {"username": username, "password": password}}
    return json.dumps(request) + "\n"


def test_each_request_is_answered_under_its_own_number(world: World) -> None:
    signed, first, second = answers(
        world,
        sign_in("avery", password_of("avery"), 6),
        '{"id": 7, "ask": "overview"}\n',
        '{"id": 8, "ask": "nothing-here"}\n',
    )

    assert signed == {"id": 6, "outcome": "signed_in"}
    assert first["id"] == 7
    assert isinstance(first["answer"], dict)
    assert first["answer"]["state"] == "ready"
    assert second == {"id": 8, "answer": {"state": "missing"}}


def test_signing_in_and_out_are_answered_with_their_outcome(world: World) -> None:
    replies = answers(
        world,
        sign_in("avery", "not the password", 1),
        sign_in("avery", password_of("avery"), 2),
        '{"id": 3, "sign_out": true}\n',
        '{"id": 4, "ask": "overview"}\n',
    )

    assert replies == [
        {"id": 1, "outcome": "rejected"},
        {"id": 2, "outcome": "signed_in"},
        {"id": 3, "outcome": "signed_out"},
        {"id": 4, "answer": {"state": "signed_out"}},
    ]


def test_a_line_that_is_not_a_request_is_refused_and_the_next_still_answered(
    world: World,
) -> None:
    replies = answers(
        world,
        "not json\n",
        "[1, 2]\n",
        '{"ask": "overview"}\n',
        '{"id": true, "ask": "overview"}\n',
        '{"id": 1, "ask": 5}\n',
        '{"id": 1}\n',
        '{"id": 1, "ask": "overview", "sign_out": true}\n',
        '{"id": 1, "sign_out": false}\n',
        '{"id": 1, "sign_in": {"username": "avery"}}\n',
        '{"id": 1, "sign_in": {"username": "avery", "password": 5}}\n',
        '{"id": 1, "sign_in": "avery"}\n',
        "\n",
        '{"id": 2, "ask": "overview"}\n',
    )

    assert replies[:-1] == [{"id": None, "error": "bad request"}] * 11
    assert replies[-1] == {"id": 2, "answer": {"state": "signed_out"}}


def test_a_refusal_never_repeats_the_line_it_refused(world: World) -> None:
    secret = password_of("avery")
    lines = [
        f'{{"id": 1, "sign_in": {{"username": "avery", "password": "{secret}"}}, "ask": "x"}}\n',
        f'{{"id": "{secret}", "sign_in": {{"username": "avery", "password": "{secret}"}}}}\n',
        f'{{"sign_in": {{"username": "avery", "password": "{secret}"}}\n',
    ]
    written: list[str] = []

    stdio.serve(Bridge(world.settings, world.bookkeeper, world.clock), lines, written.append)

    assert len(written) == 3
    assert secret not in "".join(written)


def test_an_ask_carries_what_narrows_it_with_it(world: World) -> None:
    signed, narrowed, malformed = answers(
        world,
        sign_in("avery", password_of("avery")),
        '{"id": 2, "ask": "transactions", "with": {"limit": 1}}\n',
        '{"id": 3, "ask": "transactions", "with": {"limit": "one"}}\n',
    )

    assert signed == {"id": 1, "outcome": "signed_in"}
    assert narrowed["id"] == 2
    assert isinstance(narrowed["answer"], dict)
    assert len(narrowed["answer"]["entries"]) == 1
    assert malformed == {"id": 3, "answer": {"state": "missing"}}


def test_with_is_an_object_beside_an_ask_or_the_line_is_not_a_request(world: World) -> None:
    replies = answers(
        world,
        '{"id": 1, "ask": "transactions", "with": [1]}\n',
        '{"id": 1, "ask": "transactions", "with": "limit=1"}\n',
        '{"id": 1, "ask": "transactions", "with": null}\n',
        '{"id": 1, "sign_out": true, "with": {}}\n',
        '{"id": 1, "sign_in": {"username": "avery", "password": "x"}, "with": {}}\n',
        '{"id": 2, "ask": "transactions", "with": {}}\n',
    )

    assert replies[:-1] == [{"id": None, "error": "bad request"}] * 5
    assert replies[-1] == {"id": 2, "answer": {"state": "signed_out"}}
