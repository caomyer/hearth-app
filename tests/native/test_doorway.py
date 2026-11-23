"""The native app's doorway answers with disclosure's views, and with nothing else.

The figures are the ones worked out by hand in ``tests/household.py``. The last test is the
doorway's share of the privacy boundary: whatever a viewer asks, under every sharing scenario
the page crawl uses, no datum they may not see is in any answer.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from hearth import books
from hearth.books import LedgerError
from hearth.native import doorway as asks
from hearth.native.doorway import Doorway
from hearth.sharing import DEFAULT_SHARED, Facet
from tests import household
from tests.conftest import World
from tests.test_privacy_boundary import SCENARIOS

FIXTURES = Path(__file__).parent / "fixtures"


def doorway(world: World, viewer: str) -> Doorway:
    return Doorway(world.settings, world.bookkeeper, viewer)


def amount(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def test_the_overview_is_the_households_net_worth_to_the_cent(world: World) -> None:
    answer = doorway(world, "avery").answer("overview")

    assert answer["state"] == "ready"
    assert answer["currency"] == "USD"
    assert answer["as_of"] == household.NOW.date().isoformat()
    assert answer["connected"] is True
    worth = answer["net_worth"]
    assert isinstance(worth, dict)
    assert Decimal(worth["value"]) == amount(household.HOUSEHOLD_NET_WORTH)
    assert worth["is_known"] is True
    assert worth["is_partial"] is False
    assert worth["without"] == []


def test_money_crosses_as_a_decimal_string_never_a_float(world: World) -> None:
    answer = doorway(world, "avery").answer("overview")

    def floats(value: object) -> list[float]:
        if isinstance(value, float):
            return [value]
        if isinstance(value, dict):
            return [f for item in value.values() for f in floats(item)]
        if isinstance(value, list):
            return [f for item in value for f in floats(item)]
        return []

    assert floats(answer) == []
    history = answer["history"]
    assert isinstance(history, list)
    assert history
    assert Decimal(history[-1]["value"]) == amount(household.HOUSEHOLD_NET_WORTH)


def test_a_total_that_leaves_a_part_out_says_which(world: World) -> None:
    world.choose("avery", DEFAULT_SHARED - {Facet.NET_WORTH_TOTAL})

    worth = doorway(world, "casey").answer("overview")["net_worth"]

    assert isinstance(worth, dict)
    assert worth["is_partial"] is True
    assert set(worth["without"]) == {"Avery", "Joint accounts"}
    assert Decimal(worth["value"]) == amount(household.NET_WORTH["blake"]) + amount(
        household.NET_WORTH["casey"]
    )


def test_a_screen_that_does_not_exist_is_missing(world: World) -> None:
    assert doorway(world, "avery").answer("everything") == {"state": "missing"}


def test_someone_who_is_not_a_member_is_told_what_a_missing_screen_is(world: World) -> None:
    assert doorway(world, "mallory").answer("overview") == doorway(world, "avery").answer("nope")


def test_before_the_first_recompute_the_books_are_loading(world: World) -> None:
    waiting = books.Bookkeeper(world.bookkeeper_establish, world.clock)

    assert Doorway(world.settings, waiting, "avery").answer("overview") == {"state": "loading"}


def test_a_failed_first_recompute_says_so_in_one_sentence(world: World) -> None:
    def establish(now: datetime) -> books.Books:
        raise LedgerError("The ledger could not be read.")

    failed = books.Bookkeeper(establish, world.clock)
    failed.recompute(wait=True)

    assert Doorway(world.settings, failed, "avery").answer("overview") == {
        "state": "failed",
        "summary": "The ledger could not be read.",
    }


def test_moments_are_utc_to_the_second_in_one_shape(world: World) -> None:
    answer = doorway(world, "avery").answer("overview")

    assert answer["computed_at"] == "2026-10-15T12:00:00Z"
    assert datetime(2026, 10, 15, 12, 0, tzinfo=UTC) == household.NOW


def test_the_overview_has_the_shape_the_app_decodes(world: World) -> None:
    """``overview.json`` is what the app's own tests decode (``apple/HearthKit``): a change of
    shape here that the app has not been taught fails on this side first."""
    example = json.loads((FIXTURES / "overview.json").read_text())

    assert differences(doorway(world, "avery").answer("overview"), example["answer"]) == []


def differences(told: object, example: object, at: str = "answer") -> list[str]:
    """Where `told` has another shape than `example`: other keys, or another kind of value.
    A null fits anything, as it is how a figure that is not known is written, and an empty
    list fits any list."""
    if told is None or example is None:
        return []
    if isinstance(told, dict) and isinstance(example, dict):
        if told.keys() != example.keys():
            return [f"{at}: keys {sorted(told)} != {sorted(example)}"]
        return [d for key in told for d in differences(told[key], example[key], f"{at}.{key}")]
    if isinstance(told, list) and isinstance(example, list):
        return differences(told[0], example[0], f"{at}[0]") if told and example else []
    return [] if type(told) is type(example) else [f"{at}: {type(told).__name__}"]


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("viewer", household.USERNAMES)
def test_no_answer_holds_what_its_viewer_may_not_see(
    world: World, scenario: str, viewer: str
) -> None:
    choices = dict.fromkeys(household.USERNAMES, DEFAULT_SHARED)
    choices.update(SCENARIOS[scenario])
    for username, shared in choices.items():
        world.choose(username, shared)

    door = doorway(world, viewer)
    # The doorway writes money without the thousands separators a page shows it with.
    seen = "\n".join(json.dumps(door.answer(ask)) for ask in asks.SCREENS)

    leaked = [
        f"{secret.what}: {text!r}"
        for secret in household.secrets(world.account_ids)
        if not secret.visible(viewer, choices)
        for text in secret.texts
        if text.replace(",", "") in seen
    ]
    assert not leaked, f"{viewer} could see private data: {leaked}"
