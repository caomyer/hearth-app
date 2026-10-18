"""A Plaid client over the stand-in, and the stores a sync writes, for the plaid package's tests."""

import subprocess
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from hearth.members import MemberId
from hearth.plaid import feed as feed_store
from hearth.plaid import owner
from hearth.plaid.client import Credentials, Environment, PlaidClient
from hearth.plaid.fake import DEMO_INSTITUTIONS, FakePlaid
from hearth.plaid.feed import Feed
from hearth.plaid.registry import Item, Registry
from hearth.plaid.setup import DAYS_REQUESTED, STAND_IN_APPROVAL
from hearth.vault.secret import Secret
from tests.vault.conftest import open_database

TODAY = date(2026, 10, 18)
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
CLIENT_ID = "client-synthetic-0001"
SLEEP = "/bin/sleep"
SECRET = "secret-synthetic-feedface"


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def other_process() -> Iterator[str]:
    """A live process other than this one, named as a lease names its owner."""
    child = subprocess.Popen([SLEEP, "60"])  # noqa: S603 - a fixed program
    try:
        yield f"{child.pid}@{owner.started(child.pid)}"
    finally:
        child.kill()
        child.wait()


@pytest.fixture
def gone_process() -> str:
    """A process that has ended, named as a lease names its owner."""
    child = subprocess.Popen([SLEEP, "60"])  # noqa: S603 - a fixed program
    name = f"{child.pid}@{owner.started(child.pid)}"
    child.kill()
    child.wait()
    return name


def held_by(feed: Feed, item_id: str, process: str, *, at: datetime) -> None:
    """A run of `item_id` begun at `at`, still running, owned by `process`."""
    assert feed.start_run(item_id, now=at) is not None
    feed._db.execute(
        "UPDATE feed_run SET owner = ? WHERE item_id = ? AND outcome = 'running'",
        (process, item_id),
    )


@pytest.fixture
def fake() -> FakePlaid:
    stand_in = FakePlaid(DEMO_INSTITUTIONS, today=lambda: TODAY)
    stand_in.register_client(CLIENT_ID, SECRET)
    return stand_in


@pytest.fixture
def client(fake: FakePlaid) -> PlaidClient:
    credentials = Credentials(Secret(CLIENT_ID), Secret(SECRET), Environment.DEMO)
    return PlaidClient(credentials, STAND_IN_APPROVAL, fake)


@pytest.fixture
def feed(tmp_path: Path) -> Iterator[Feed]:
    path = tmp_path / "feed.sqlite3"
    feed_store.initialise(path)
    with Feed.open(path) as opened:
        yield opened


@pytest.fixture
def registry() -> Iterator[Registry]:
    conn = open_database()
    try:
        yield Registry(conn)
    finally:
        conn.close()


def linked(
    client: PlaidClient,
    fake: FakePlaid,
    registry: Registry,
    institution: str,
    *,
    ready: bool = True,
) -> tuple[Item, Secret]:
    """An Item at `institution`, created through Link and recorded as hearth records it.

    Its whole history is ready unless `ready` is false, when it arrives as Plaid sends it."""
    link = client.create_link_token(user="hearth-member-1", days_requested=DAYS_REQUESTED)
    exchanged = client.exchange(Secret(fake.public_token(link.token, institution)))
    item = registry.add(
        exchanged.item_id,
        member=MemberId(1),
        credentials="member:1",
        institution=client.institution(exchanged.access_token),
        fallback_name="",
        days_requested=DAYS_REQUESTED,
        now=NOW,
    )
    if ready:
        fake.history_ready(item.item_id)
    return item, exchanged.access_token
