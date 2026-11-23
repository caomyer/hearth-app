"""The bridge's wire: a line in, a line out, and a bad line never stops it."""

import json

from hearth.native import stdio
from hearth.native.doorway import Doorway
from tests.conftest import World


def answers(world: World, *lines: str) -> list[dict[str, object]]:
    written: list[str] = []
    stdio.serve(Doorway(world.settings, world.bookkeeper, "avery"), lines, written.append)
    assert all(line.endswith("\n") and line.count("\n") == 1 for line in written)
    return [json.loads(line) for line in written]


def test_each_request_is_answered_under_its_own_number(world: World) -> None:
    first, second = answers(
        world, '{"id": 7, "ask": "overview"}\n', '{"id": 8, "ask": "nothing-here"}\n'
    )

    assert first["id"] == 7
    assert isinstance(first["answer"], dict)
    assert first["answer"]["state"] == "ready"
    assert second == {"id": 8, "answer": {"state": "missing"}}


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
        "\n",
        '{"id": 2, "ask": "overview"}\n',
    )

    assert replies[:-1] == [{"id": None, "error": "bad request"}] * 5
    assert replies[-1]["id"] == 2
