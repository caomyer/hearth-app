"""The household's own ledger in the app: checking a file, bringing it in once, and matching
each member's own connected accounts to it, through a bridge a member signed in to.

Every refusal the command line gives is reproduced here as the app is told it, and each place
a member could learn of what is not theirs is asked: another member's connections and the
accounts the ledger's evidence says are theirs, and what lives only in the ledger file checked.
Every name and figure is invented (``tests.ledgers``)."""

import contextlib
import errno
import fcntl
import json
import os
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from hearth import accounts, auth, base, books, ledger_child, ledger_import, matching
from hearth.config import Settings
from hearth.disclosure import Disclosure, may_learn, rulings_in
from hearth.disclosure.ledger import BroughtIn
from hearth.matching import ById, Stretch, Unproposed
from hearth.members import find
from hearth.native.bridge import Acted, Bridge, Outcome
from hearth.native.importer import Importer
from hearth.plaid.registry import Registry
from tests import ledgers
from tests.conftest import FakeClock, World, password_of
from tests.household import Seen
from tests.native.test_doorway import differences
from tests.test_matching import NOW, TODAY, Household, build

FIXTURES = Path(__file__).parent / "fixtures"


def _tree(folder: Path) -> dict[str, bytes]:
    """Every file under `folder` and its bytes."""
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def bridge(settings_of: Household | World, clock: FakeClock, username: str) -> Bridge:
    """A bridge over the household, signed in as `username`, that can bring a ledger in."""
    if isinstance(settings_of, World):
        settings, keeper = settings_of.settings, settings_of.bookkeeper
    else:
        settings = settings_of.settings
        keeper = books.Bookkeeper(
            books.from_household(settings, rulings=rulings_in, learns=may_learn), clock
        )
        keeper.recompute(wait=True)
    door = Bridge(settings, keeper, clock, importer=Importer(keeper, clock))
    assert door.sign_in(username, password_of(username)) is Outcome.SIGNED_IN
    return door


@pytest.fixture
def fresh(tmp_path: Path, clock: FakeClock) -> Household:
    """The matching household before its ledger is brought in: its banks connected, its
    ledger written beside its data directory, nothing imported and nothing claimed."""
    return build(tmp_path, clock, imported=False, claims={})


@pytest.fixture
def in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """The check and the import done in the test's own process, where what it patches holds:
    as the child process does them (``ledger_child.answer``), told the same, through JSON."""

    def here(
        settings: Settings,
        asked: dict[str, object],
        now: datetime,
        limit: float | None,
        *,
        handed: tuple[int, ...] = (),
    ) -> dict[str, Any]:
        request = json.loads(json.dumps(ledger_child._request(settings, asked, now)))
        told: dict[str, Any] = json.loads(json.dumps(ledger_child.answer(request)))
        return told

    monkeypatch.setattr(ledger_child, "_run", here)


@pytest.fixture
def kept(tmp_path: Path) -> Path:
    """The invented ledger of every shape (``tests.ledgers``), in a folder of its own."""
    folder = tmp_path / "kept"
    folder.mkdir()
    return ledgers.write(folder)


def settled(door: Bridge, screen: str, given: Mapping[str, object] | None = None) -> dict[str, Any]:
    """A screen's answer once it is worked out: a slow one is answered `loading` while it is,
    off the wire (``hearth.native.worker``), and asked again, as the app asks."""
    for _ in range(3000):
        answer = door.answer(screen, given)
        if answer["state"] != "loading":
            return answer
        time.sleep(0.01)
    raise AssertionError(f"{screen} was never worked out")


def ask(door: Bridge, screen: str) -> dict[str, Any]:
    return settled(door, screen)


def check(door: Bridge, path: Path | str) -> dict[str, Any]:
    return settled(door, "ledger_check", {"path": str(path)})


# Checking a file.


def test_the_check_says_what_the_ledger_holds_as_worked_out_by_hand(
    fresh: Household, clock: FakeClock, kept: Path
) -> None:
    door = bridge(fresh, clock, "avery")
    answer = check(door, kept)

    assert answer["state"] == "ready"
    assert answer["refused"] is None
    found = answer["found"]
    assert isinstance(found, dict)
    assert found["files"] == len(ledgers.FILES)
    assert found["entries"] == ledgers.ENTRIES
    assert len(found["accounts"]) == ledgers.ACCOUNT_COUNT
    assert (found["first"], found["last"]) == (ledgers.FIRST.isoformat(), ledgers.LAST.isoformat())
    assert found["assertions"] == {"count": ledgers.ASSERTIONS, "failing": 0}
    assert found["worth"]["value"] == format(ledgers.NET_WORTH, "f")
    assert found["worth"]["is_floor"] is False
    assert found["currencies"] == ["USD"]
    assert found["commodities"] == ["ZQX"]
    assert found["kinds"] == [
        {"kind": "Assets", "count": 2},
        {"kind": "Liabilities", "count": 1},
        {"kind": "Equity", "count": 1},
        {"kind": "Income", "count": 1},
        {"kind": "Expenses", "count": 1},
    ]
    assert [f"{u['file']}, line {u['line']}: {u['what']}" for u in found["unused"]] == (
        ledgers.UNUSED
    )
    assert found["unused_kinds"] == {"plugins": 2, "custom": 2, "documents": 1}


def test_a_check_writes_nothing_anywhere_and_says_the_same_when_asked_again(
    fresh: Household, clock: FakeClock, kept: Path
) -> None:
    door = bridge(fresh, clock, "avery")
    folder, data = _tree(kept.parent), _tree(fresh.settings.data_dir)

    first, again = check(door, kept), check(door, kept)

    assert first == again
    assert _tree(kept.parent) == folder
    assert _tree(fresh.settings.data_dir) == data
    assert not fresh.settings.ledger_base.exists()
    assert not (kept.parent / "meddled").exists(), "a plugin that is not beancount's own ran"


def test_the_check_never_repeats_the_path_it_was_asked(
    fresh: Household, clock: FakeClock, kept: Path
) -> None:
    door = bridge(fresh, clock, "avery")

    assert str(kept.parent) not in json.dumps(check(door, kept))


def test_only_an_admin_may_check_or_bring_in_a_ledger(
    fresh: Household, clock: FakeClock, kept: Path
) -> None:
    door = bridge(fresh, clock, "blake")

    assert check(door, kept) == {"state": "missing"}
    assert door.act("bring_in_ledger", {"path": str(kept)}) is Acted.MISSING
    standing = ask(door, "ledger")
    assert standing["may_bring_in"] is False
    assert standing["why_not"] is None
    assert standing["imported"] is None
    assert not fresh.settings.ledger_base.exists()


@pytest.mark.parametrize(
    "path",
    ["kept/main.beancount", "", "/" + "x" * 1024, "/zq/kept\0.beancount"],
    ids=["relative", "empty", "too long", "with a NUL"],
)
def test_a_path_that_is_not_an_absolute_one_is_missing(
    fresh: Household, clock: FakeClock, path: str
) -> None:
    door = bridge(fresh, clock, "avery")

    assert check(door, path) == {"state": "missing"}
    assert door.answer("ledger_check", {"path": "/x", "more": 1}) == {"state": "missing"}
    assert door.act("bring_in_ledger", {"path": path}) is Acted.MISSING


def test_a_file_that_is_not_there_or_not_a_file_is_refused_saying_so(
    fresh: Household, clock: FakeClock, tmp_path: Path
) -> None:
    door = bridge(fresh, clock, "avery")
    missing = tmp_path / "nowhere.beancount"
    (tmp_path / "a-folder").mkdir()

    assert check(door, missing)["refused"] == {
        "why": "unreadable",
        "said": "nowhere.beancount cannot be read: No such file or directory.",
    }
    assert check(door, tmp_path / "a-folder") == {
        "state": "ready",
        "found": None,
        "refused": {"why": "not_a_file", "said": "a-folder is not a file, so it is not a ledger."},
    }


def _within(seconds: float, work: object) -> object:
    """`work`'s result, or fail if it is still waiting after `seconds`: a file read that should
    never have been opened, a pipe, would wait for a writer for ever."""
    assert callable(work)
    found: list[object] = []
    thread = threading.Thread(target=lambda: found.append(work()), daemon=True)
    thread.start()
    thread.join(seconds)
    assert found, "it waited on a file it should never have opened"
    return found[0]


def test_a_pipe_is_never_opened_named_or_included(
    fresh: Household, clock: FakeClock, kept: Path, tmp_path: Path
) -> None:
    door = bridge(fresh, clock, "avery")
    pipe = tmp_path / "waiting.beancount"
    os.mkfifo(pipe)

    named = _within(10, lambda: check(door, pipe))
    assert named == {
        "state": "ready",
        "found": None,
        "refused": {
            "why": "not_a_file",
            "said": "waiting.beancount is not a file, so it is not a ledger.",
        },
    }

    # Included by a glob, beside the ledger, it is refused before beancount would open it.
    os.mkfifo(kept.parent / "years" / "2026-3.beancount")
    globbed = _within(10, lambda: check(door, kept))
    assert isinstance(globbed, dict)
    assert globbed["refused"] == {
        "why": "not_a_file",
        "said": "years/2026-3.beancount is not a file, so it is not a ledger.",
    }


def test_an_include_by_absolute_path_is_refused_before_it_is_followed(
    fresh: Household, clock: FakeClock, tmp_path: Path
) -> None:
    """The file it names is a pipe: following it would wait for ever."""
    door = bridge(fresh, clock, "avery")
    outside = tmp_path / "outside.beancount"
    os.mkfifo(outside)
    folder = tmp_path / "absolute"
    folder.mkdir()
    files = {
        **ledgers.FILES,
        "accounts.beancount": ledgers.ACCOUNTS.replace('"prices/zqx.beancount"', f'"{outside}"'),
    }
    ledger = ledgers.write(folder, files)

    answer = _within(10, lambda: check(door, ledger))

    assert answer == {
        "state": "ready",
        "found": None,
        "refused": {
            "why": "absolute_include",
            "said": "accounts.beancount, line 9, includes a file by its full path, which"
            " Hearth's copy would still read from where it is. Make the include relative to the"
            " file that has it, then choose the ledger again.",
        },
    }


def test_a_file_that_is_not_a_ledger_is_refused_and_never_echoed(
    fresh: Household, clock: FakeClock, tmp_path: Path
) -> None:
    door = bridge(fresh, clock, "avery")
    note = tmp_path / "notes.txt"
    note.write_text("Zq-secret-word 1,234.56 spent at Zq Hideaway\nnot a ledger at all\n")

    answer = check(door, note)

    assert answer["found"] is None
    assert answer["refused"]["why"] == "not_clean"
    assert answer["refused"]["said"].startswith("It does not load cleanly.")
    for text in ("Zq-secret-word", "1,234.56", "1234.56", "Hideaway", "not a ledger"):
        assert text not in json.dumps(answer)


def test_a_file_larger_than_any_ledger_is_refused_unread(
    fresh: Household,
    clock: FakeClock,
    kept: Path,
    monkeypatch: pytest.MonkeyPatch,
    in_process: None,
) -> None:
    monkeypatch.setattr(ledger_import, "LARGEST", 1024 * 1024)
    door = bridge(fresh, clock, "avery")
    prices = kept.parent / "prices/zqx.beancount"
    prices.write_text(ledgers.PRICES + "; padding\n" * 120_000)

    assert check(door, kept) == {
        "state": "ready",
        "found": None,
        "refused": {
            "why": "too_large",
            "said": "The ledger and the files it includes come to more than 1 MB, which no"
            " ledger's text does.",
        },
    }


def test_a_failing_assertion_is_said_where_beside_what_the_ledger_holds(
    fresh: Household, clock: FakeClock, tmp_path: Path
) -> None:
    door = bridge(fresh, clock, "avery")
    folder = tmp_path / "failing"
    folder.mkdir()
    second = ledgers.SECOND_HALF.replace("1954.50 USD", "1999.99 USD")
    ledger = ledgers.write(folder, {**ledgers.FILES, "years/2026-2.beancount": second})

    answer = check(door, ledger)

    found = answer["found"]
    assert isinstance(found, dict)
    assert found["assertions"] == {"count": ledgers.ASSERTIONS, "failing": 1}
    assert answer["refused"] == {
        "why": "assertion",
        "said": "A balance check in it fails: on Jul 2, 2026, at 2026-2.beancount, line 6, its"
        " transactions do not add up to what it says.",
    }
    assert "1999.99" not in json.dumps(answer)


def test_a_household_that_inherits_its_ledger_is_told_so_and_nothing_of_any_file(
    world: World, clock: FakeClock, kept: Path
) -> None:
    """Its own ledger, where it is, holds every member's accounts: read again for an admin,
    it would tell them what hearth tells each member only of their own."""
    door = bridge(world, clock, "avery")
    assert world.settings.ledger is not None

    for path in (kept, world.settings.ledger):
        answer = check(door, path)
        assert answer["found"] is None
        assert answer["refused"] == {
            "why": "inherited",
            "said": "Hearth reads your ledger where it is, so another cannot be brought in.",
        }
    standing = ask(door, "ledger")
    assert standing["inherited"] is True
    assert standing["may_bring_in"] is False
    assert standing["why_not"]["why"] == "inherited"


def test_nothing_hearth_keeps_is_read_as_a_ledger_to_bring_in(
    fresh: Household, clock: FakeClock, tmp_path: Path
) -> None:
    """Its books, written on every recompute, hold every member's rows; a folder linked into
    its data directory is its data directory still."""
    door = bridge(fresh, clock, "avery")
    written = fresh.settings.data_dir / "books.beancount"
    assert written.exists()
    linked = tmp_path / "linked"
    linked.symlink_to(fresh.settings.data_dir)
    folder = tmp_path / "including"
    folder.mkdir()
    including = folder / "main.beancount"
    including.write_text('include "../linked/books.beancount"\n')

    hard = tmp_path / "hard.beancount"
    os.link(written, hard)
    for path in (written, linked / "books.beancount", including, hard):
        answer = check(door, path)
        assert answer == {
            "state": "ready",
            "found": None,
            "refused": {
                "why": "hearths_own",
                "said": "That is one of the files Hearth keeps its own books in, not a ledger."
                " Choose the ledger your household kept itself.",
            },
        }, path
    assert door.act("bring_in_ledger", {"path": str(written)}) is Acted.STARTED
    importer = door.importer
    assert importer is not None
    importer.wait()
    assert base.current(fresh.settings.ledger_base) is None


def test_a_file_swapped_for_a_pipe_once_read_is_never_opened_again(
    fresh: Household,
    clock: FakeClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    in_process: None,
) -> None:
    """Each file is read once and the copy of those bytes is what loads: a file swapped for a
    pipe after it was read cannot make the check, or the bridge behind it, wait."""
    door = bridge(fresh, clock, "avery")
    folder = tmp_path / "swap"
    folder.mkdir()
    main = folder / "main.beancount"
    main.write_text('option "operating_currency" "USD"\ninclude "part.beancount"\n')
    part = folder / "part.beancount"
    part.write_text("2020-01-01 open Assets:Zq:A USD\n")
    real = ledger_import._gathered

    def gathered_then_swapped(source: Path, keeping: Path) -> ledger_import.Gathered:
        found = real(source, keeping)
        part.unlink()
        os.mkfifo(part)
        return found

    monkeypatch.setattr(ledger_import, "_gathered", gathered_then_swapped)

    answer = _within(10, lambda: check(door, main))

    assert isinstance(answer, dict)
    assert answer["refused"] is None
    assert [a["name"] for a in answer["found"]["accounts"]] == ["Assets:Zq:A"]


def test_an_include_from_the_home_folder_is_said_as_what_beancount_reads(
    fresh: Household, clock: FakeClock, tmp_path: Path
) -> None:
    """Beancount reads `~/x` as a folder named ~ beside the file, never the home folder."""
    door = bridge(fresh, clock, "avery")
    folder = tmp_path / "tilde"
    folder.mkdir()
    main = folder / "main.beancount"
    main.write_text('option "operating_currency" "USD"\ninclude "~/zq-nope.beancount"\n')

    assert check(door, main)["refused"] == {
        "why": "home_include",
        "said": "main.beancount, line 2, includes a file starting with ~, which beancount reads"
        " as a folder named ~ beside the file, not your home folder. Make the include relative"
        " to the file that has it, then choose the ledger again.",
    }


def test_a_ledger_of_more_files_than_any_or_kept_in_icloud_is_refused_unread(
    fresh: Household,
    clock: FakeClock,
    kept: Path,
    monkeypatch: pytest.MonkeyPatch,
    in_process: None,
) -> None:
    door = bridge(fresh, clock, "avery")
    monkeypatch.setattr(ledger_import, "MOST_FILES", 2)
    assert check(door, kept)["refused"]["why"] == "too_many"

    monkeypatch.setattr(ledger_import, "MOST_FILES", 1000)
    # Stands in for SF_DATALESS, which only the system sets, by a flag a test may set; only
    # the Mac's file systems, where iCloud keeps files, have flags at all.
    flag: Callable[[Path, int], None] | None = getattr(os, "chflags", None)
    if flag is None:
        pytest.skip("this system's files carry no flags, so none is kept in iCloud")
    monkeypatch.setattr(ledger_import, "DATALESS", stat.UF_HIDDEN)
    flag(kept.parent / "accounts.beancount", stat.UF_HIDDEN)
    assert check(door, kept)["refused"] == {
        "why": "not_downloaded",
        "said": "accounts.beancount is kept in iCloud and is not downloaded to this Mac. Download"
        " it in Finder, then choose the ledger again.",
    }


def test_a_slow_check_never_holds_up_the_apps_other_asks(
    fresh: Household,
    clock: FakeClock,
    kept: Path,
    monkeypatch: pytest.MonkeyPatch,
    in_process: None,
) -> None:
    """Worked out off the wire: while a check waits on a file, the overview is answered, and
    the check is answered `loading` until it is done, then once."""
    door = bridge(fresh, clock, "avery")
    release = threading.Event()
    real = ledger_import.check_ledger

    def waiting(*args: Any, **kwargs: Any) -> ledger_import.Checked:
        release.wait(20)
        return real(*args, **kwargs)

    monkeypatch.setattr(ledger_import, "check_ledger", waiting)
    given = {"path": str(kept)}

    assert door.answer("ledger_check", given) == {"state": "loading"}
    started = time.perf_counter()
    assert door.answer("overview")["state"] == "ready"
    assert door.answer("ledger_check", given) == {"state": "loading"}
    assert time.perf_counter() - started < 1
    release.set()
    assert settled(door, "ledger_check", given)["refused"] is None


# Bringing it in.


def _brought_in(door: Bridge, path: Path) -> dict[str, Any]:
    assert door.act("bring_in_ledger", {"path": str(path)}) is Acted.STARTED
    importer = door.importer
    assert importer is not None
    importer.wait()
    told = ask(door, "ledger")["bringing_in"]
    assert isinstance(told, dict)
    return told


def test_bringing_in_freezes_the_base_once_and_the_books_then_hold_it(
    fresh: Household, clock: FakeClock
) -> None:
    door = bridge(fresh, clock, "avery")
    before = _tree(fresh.ledger.parent)

    told = _brought_in(door, fresh.ledger)

    assert told == {
        "running": False,
        "outcome": "imported",
        "refused": None,
        "finished_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "books_failed": False,
    }
    imported = base.current(fresh.settings.ledger_base)
    assert imported is not None
    assert imported.source == fresh.ledger
    assert _tree(fresh.ledger.parent) == before, "the ledger brought in was changed"
    keeper = door.bookkeeper
    assert keeper.status.books is not None
    assert keeper.status.failure is None
    # The books were recomputed from the base, through the one path, before it was told ended.
    assert books.inherited(fresh.settings) == imported.base
    standing = ask(door, "ledger")
    assert standing["imported"] == {
        "on": TODAY.isoformat(),
        "files": 1,
        "unclaimed": standing["imported"]["unclaimed"],
    }
    assert standing["may_bring_in"] is False
    assert standing["why_not"] == {
        "why": "once",
        "said": f"Your ledger was brought in on Nov {TODAY.day}, 2026. A ledger is brought in"
        " once, and Hearth's books are the truth from then on.",
    }

    # Once: the same ledger, or any other, is refused, and nothing is written.
    again = _brought_in(door, fresh.ledger)
    assert again["outcome"] == "refused"
    assert again["refused"] == standing["why_not"]
    assert base.current(fresh.settings.ledger_base) == imported


def test_the_command_line_and_the_app_import_alike(
    fresh: Household, clock: FakeClock, tmp_path: Path
) -> None:
    """One function imports for both: the app's base is the command line's, byte for byte."""
    door = bridge(fresh, clock, "avery")
    _brought_in(door, fresh.ledger)
    by_app = base.current(fresh.settings.ledger_base)
    assert by_app is not None

    other = build(tmp_path / "other", clock, imported=False, claims={})
    by_hand = ledger_import.import_ledger(other.settings, fresh.ledger, now=NOW)

    assert by_hand.imported.base.digest == by_app.base.digest
    assert _tree(by_hand.imported.base.directory) == _tree(by_app.base.directory)


def test_an_import_another_member_started_is_never_told_to_them(
    fresh: Household, clock: FakeClock
) -> None:
    door = bridge(fresh, clock, "avery")
    _brought_in(door, fresh.ledger)

    assert door.sign_in("blake", password_of("blake")) is Outcome.SIGNED_IN
    assert ask(door, "ledger")["bringing_in"] is None


@pytest.mark.parametrize("stops", ["loading the copy", "naming it in the manifest"])
def test_an_import_that_stops_part_way_leaves_no_half_base(
    fresh: Household,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    stops: str,
    in_process: None,
) -> None:
    """Stopped while the copy is staged, or once its folder is in place but before the
    manifest names it, the household holds no base, and a later import goes ahead whole."""
    door = bridge(fresh, clock, "avery")
    real_commit, real_load = base.commit, books.load_ledger

    def interrupted_commit(directory: Path, frozen: base.Base, **_: object) -> base.Imported:
        # The copy's folder goes where the manifest would name it, and then the power fails.
        directory.mkdir(parents=True, exist_ok=True)
        frozen.directory.rename(directory / frozen.digest)
        raise RuntimeError("the power failed")

    def interrupted_load(ledger: Path | base.Base) -> books.Loaded:
        if isinstance(ledger, base.Base):
            raise RuntimeError("the power failed")
        return real_load(ledger)

    if stops == "loading the copy":
        monkeypatch.setattr(ledger_import, "_load", interrupted_load)
    else:
        monkeypatch.setattr(base, "commit", interrupted_commit)

    told = _brought_in(door, fresh.ledger)

    assert told["outcome"] == "failed"
    assert base.current(fresh.settings.ledger_base) is None
    assert not (fresh.settings.ledger_base / base.MANIFEST).exists()
    assert not list(fresh.settings.data_dir.glob(".importing-*"))
    assert books.inherited(fresh.settings) is None

    monkeypatch.setattr(base, "commit", real_commit)
    monkeypatch.setattr(ledger_import, "_load", real_load)
    assert _brought_in(door, fresh.ledger)["outcome"] == "imported"
    assert base.current(fresh.settings.ledger_base) is not None


def test_an_import_is_one_at_a_time(fresh: Household, clock: FakeClock) -> None:
    door = bridge(fresh, clock, "avery")
    importer = door.importer
    assert importer is not None
    release = threading.Event()

    def held_up() -> BroughtIn:
        release.wait(10)
        return BroughtIn("failed")

    assert importer.start("avery", held_up)

    assert door.act("bring_in_ledger", {"path": str(fresh.ledger)}) is Acted.RUNNING
    assert ask(door, "ledger")["bringing_in"]["running"] is True
    assert ask(door, "ledger")["importing"] is True
    # Another admin signed in to the same engine is told one is being brought in, not whose;
    # a member who may not bring one in is told nothing of it.
    conn = fresh.fresh.connect()
    try:
        auth.create_member(
            conn,
            actor=None,
            username="casey",
            display_name="Casey",
            is_admin=True,
            password=password_of("casey"),
            now=NOW,
        )
    finally:
        conn.close()
    assert door.sign_in("casey", password_of("casey")) is Outcome.SIGNED_IN
    other = ask(door, "ledger")
    assert (other["importing"], other["bringing_in"]) == (True, None)
    assert door.act("bring_in_ledger", {"path": str(fresh.ledger)}) is Acted.RUNNING
    assert door.sign_in("blake", password_of("blake")) is Outcome.SIGNED_IN
    assert ask(door, "ledger")["importing"] is False
    release.set()
    importer.wait()
    assert ask(door, "ledger")["importing"] is False


def test_two_imports_at_once_take_turns_and_the_second_is_refused(
    fresh: Household, clock: FakeClock, tmp_path: Path, in_process: None
) -> None:
    """Two bridges bring two ledgers in at the same moment: one is the base, the other is told
    a ledger was brought in once, and the base is never replaced underneath the first."""
    folders = []
    for word in ("a", "b"):
        folder = tmp_path / word
        folder.mkdir()
        ledgers.write(folder)
        (folder / "main.beancount").write_text(
            (folder / "main.beancount").read_text() + f"; {word}\n"
        )
        folders.append(folder / "main.beancount")
    first = bridge(fresh, clock, "avery")
    second = Bridge(
        fresh.settings, first.bookkeeper, clock, importer=Importer(first.bookkeeper, clock)
    )
    assert second.sign_in("avery", password_of("avery")) is Outcome.SIGNED_IN
    real = base.staged
    both_inside = threading.Barrier(2, timeout=2)

    @contextlib.contextmanager
    def slow_staged(*args: Any, **kwargs: Any) -> Iterator[base.Base]:
        # Whoever gets this far first waits for the other, which the lock holds back.
        with contextlib.suppress(threading.BrokenBarrierError):
            both_inside.wait()
        with real(*args, **kwargs) as staged:
            yield staged

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(base, "staged", slow_staged)
        assert first.act("bring_in_ledger", {"path": str(folders[0])}) is Acted.STARTED
        assert second.act("bring_in_ledger", {"path": str(folders[1])}) is Acted.STARTED
        for door in (first, second):
            assert door.importer is not None
            door.importer.wait()

    outcomes = [ask(door, "ledger")["bringing_in"]["outcome"] for door in (first, second)]
    assert sorted(outcomes) == ["imported", "refused"]
    imported = base.current(fresh.settings.ledger_base)
    assert imported is not None
    winner = folders[outcomes.index("imported")]
    assert imported.source == winner


def test_what_an_import_that_died_left_is_swept_at_the_next(
    fresh: Household, clock: FakeClock
) -> None:
    folder = fresh.settings.data_dir / f"{base.IMPORTING}left"
    folder.mkdir(parents=True)
    (folder / "main.beancount").write_text("; half a copy\n")
    door = bridge(fresh, clock, "avery")

    assert _brought_in(door, fresh.ledger)["outcome"] == "imported"
    assert not folder.exists()


# Matching.


@pytest.fixture
def matched(tmp_path: Path, clock: FakeClock) -> Household:
    """The matching household with its ledger imported and its accounts claimed
    (``tests.test_matching``)."""
    return build(tmp_path, clock)


@pytest.fixture
def unclaimed(tmp_path: Path, clock: FakeClock) -> Household:
    """The matching household with its ledger imported and nothing of it claimed, as an
    import from the app leaves one."""
    return build(tmp_path, clock, claims={})


def _accounts(answer: dict[str, Any]) -> dict[str, dict[str, Any]]:
    found = answer["accounts"]
    assert isinstance(found, list)
    return {a["id"]: a for a in found}


def match(door: Bridge, chosen: dict[str, str]) -> Acted:
    """Match connected accounts as the app does: every one the step chose, in one act."""
    done = door.act("match_accounts", {"matches": chosen})
    assert isinstance(done, Acted)
    return done


def keep(household: Household, key: str, name: str, *owners: str) -> None:
    """File a connected account as an account hearth keeps itself, as the Connect page does,
    owned by `owners`."""
    ids = [household.fresh.member_ids[o] for o in owners]
    conn = household.fresh.connect()
    try:
        account = accounts.register(conn, ledger_account=name, name=key, owners=ids)
        Registry(conn).confirm(household.ids[key], account.id, by=ids[0], now=NOW)
    finally:
        conn.close()


def owners_of(household: Household) -> dict[str, frozenset[str]]:
    names = {v: k for k, v in household.fresh.member_ids.items()}
    conn = household.fresh.connect()
    try:
        return {
            a.ledger_account: frozenset(names[o] for o in a.owners)
            for a in accounts.all_accounts(conn)
        }
    finally:
        conn.close()


def test_a_member_is_shown_their_own_connected_accounts_and_nobody_elses(
    matched: Household, clock: FakeClock
) -> None:
    ids = matched.ids
    avery = _accounts(ask(bridge(matched, clock, "avery"), "matching"))
    blake = _accounts(ask(bridge(matched, clock, "blake"), "matching"))

    assert set(avery) == {v for k, v in ids.items() if not k.startswith("ins_zq_blake/")}
    assert set(blake) == {v for k, v in ids.items() if k.startswith("ins_zq_blake/")}


def test_each_is_proposed_as_the_ledger_says_naming_only_what_its_member_owns(
    matched: Household, clock: FakeClock
) -> None:
    ids = matched.ids
    answer = ask(bridge(matched, clock, "avery"), "matching")
    found = _accounts(answer)

    def proposed(key: str) -> tuple[object, object]:
        one = found[ids[key]]
        said = one["proposed"]
        return (said["account"] if isinstance(said, dict) else None, one["why_not"])

    assert proposed("ins_zq_bank/chk") == ("Assets:Zq:Checking", None)
    assert proposed("ins_zq_union/chk") == ("Assets:Zq:Union", None)
    # Nobody has claimed the mortgage, so nobody sees it: it reads as no evidence at all.
    assert proposed("ins_zq_bank/loan") == (None, "unclear")
    assert proposed("ins_zq_bank/sav") == (None, "unclear")
    # Blake's alone, though Avery's login reaches it: it reads as no evidence at all.
    assert proposed("ins_zq_bank/blakes") == (None, "unclear")
    for unseen in ("Assets:Zq:Blake", "Liabilities:Zq:Mortgage", "Liabilities:Zq:CardB"):
        assert unseen not in json.dumps(answer)
    chk = found[ids["ins_zq_bank/chk"]]
    choices = chk["choices"]
    assert isinstance(choices, list)
    # Proposed first, then each of Avery's own the ledger opens under Assets.
    assert [c["account"] for c in choices] == [
        "Assets:Zq:Checking",
        "Assets:Zq:Joint",
        "Assets:Zq:Union",
    ]
    assert choices[0]["proposed"] is True
    assert choices[0]["refused"] is None
    facing = choices[0]["facing"]
    assert facing["cut_over"] == ledgers.LAST_ASSERTED.isoformat()
    assert facing["in_ledger"] == {"count": 10, "by_id": 10, "by_content": 0}
    assert facing["before"] == {
        "count": 1,
        "from": ledgers.LACKED.day.isoformat(),
        "to": ledgers.LACKED.day.isoformat(),
    }
    assert facing["booked"]["count"] == 3


def test_an_account_somebody_else_claims_changes_nothing_a_member_is_told(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Blake's savings, which Avery's login reaches, while nobody has claimed it and once
    Blake has: Avery is told the same either way, so learns neither its name nor the claim."""
    claims = {k: v for k, v in ledgers.CLAIMS.items() if k != "Assets:Zq:Blake:Savings"}
    household = build(tmp_path, clock, claims=claims)
    blakes = household.ids["ins_zq_bank/blakes"]
    before = _accounts(ask(bridge(household, clock, "avery"), "matching"))[blakes]
    conn = household.fresh.connect()
    try:
        accounts.claim(
            conn,
            ledger_account="Assets:Zq:Blake:Savings",
            name="Savings",
            owners=[household.fresh.member_ids["blake"]],
            now=NOW,
        )
    finally:
        conn.close()
    after = _accounts(ask(bridge(household, clock, "avery"), "matching"))[blakes]

    assert before == after
    assert before["proposed"] is None
    assert before["why_not"] == "unclear"
    assert "Blake" not in json.dumps(before)


def test_evidence_on_accounts_a_member_may_not_name_reads_as_none(
    matched: Household, clock: FakeClock
) -> None:
    """Ids split between Avery's account and Blake's say nothing of Blake's to Avery: no
    disagreement, no count of ids elsewhere; split between Avery's own, they are said."""
    door = bridge(matched, clock, "avery")
    weighed = matching.weigh(matched.settings, TODAY)
    one = next(
        s
        for s in weighed.survey.accounts
        if s.proposal.connected.account.account_id == matched.ids["ins_zq_bank/chk"]
    )
    conn = matched.fresh.connect()
    try:
        avery = find(conn, "avery")
        assert avery is not None
        view = Disclosure.load(conn, avery, door.bookkeeper.status)
        claimed = {a.ledger_account: a for a in accounts.all_accounts(conn)}
    finally:
        conn.close()
    desk = view.ledger_desk(matched.settings, clock)

    def why(on: dict[str, int], unproposed: Unproposed, account: str | None = None) -> object:
        ids = ById({a: Stretch(n) for a, n in on.items()})
        proposal = replace(one.proposal, account=account, ids=ids, unproposed=unproposed)
        return desk._why_not(proposal, claimed, set(), None)

    mine = {"Assets:Zq:Checking": 3, "Assets:Zq:Union": 3}
    theirs = {"Assets:Zq:Checking": 3, "Assets:Zq:Blake:Savings": 3}
    assert why(mine, Unproposed.IDS_DISAGREE) == "ids_disagree"
    assert why(theirs, Unproposed.IDS_DISAGREE) == "unclear"
    assert why({}, Unproposed.WRONG_SIDE, "Liabilities:Zq:Card") == "wrong_side"
    assert why({}, Unproposed.WRONG_SIDE, "Liabilities:Zq:Blake:Card") == "unclear"
    assert why({}, Unproposed.WRONG_SIDE, "Liabilities:Zq:Mortgage") == "unclear"


def test_a_match_is_recorded_as_the_member_and_carried_on(
    matched: Household, clock: FakeClock
) -> None:
    door = bridge(matched, clock, "avery")
    chk = matched.ids["ins_zq_bank/chk"]

    assert match(door, {chk: "Assets:Zq:Checking"}) is Acted.MATCHED

    conn = matched.fresh.connect()
    try:
        (by,) = conn.execute(
            "SELECT confirmed_by FROM plaid_accounts WHERE account_id = ?", (chk,)
        ).fetchone()
        assert by == matched.fresh.member_ids["avery"]
    finally:
        conn.close()
    assert "Assets:Zq:Checking" in books.household_entries(matched.settings).carried
    answer = ask(door, "matching")
    one = _accounts(answer)[chk]
    assert one["state"] == "matched"
    assert one["matched"] == {"account": "Assets:Zq:Checking", "on": TODAY.isoformat()}
    assert answer["totals"] == {"matched": 1, "of": 8, "in_ledger": 10, "booked": 3}


def test_several_accounts_are_matched_in_one_act_or_none_of_them(
    matched: Household, clock: FakeClock
) -> None:
    door = bridge(matched, clock, "avery")
    ids = matched.ids
    both = {
        ids["ins_zq_bank/chk"]: "Assets:Zq:Checking",
        ids["ins_zq_union/chk"]: "Assets:Zq:Union",
    }

    # One named for an account the other is named for too: refused whole, nothing written.
    twice = {ids["ins_zq_bank/chk"]: "Assets:Zq:Union", ids["ins_zq_union/chk"]: "Assets:Zq:Union"}
    assert match(door, twice) is Acted.REFUSED
    assert not any(owners for owners in _confirmed(matched))
    assert match(door, both) is Acted.MATCHED
    assert sorted(_confirmed(matched)) == ["Assets:Zq:Checking", "Assets:Zq:Union"]


def _confirmed(household: Household) -> list[str]:
    conn = household.fresh.connect()
    try:
        names = {a.id: a.ledger_account for a in accounts.all_accounts(conn)}
        return [names[a.hearth_account or ""] for a in Registry(conn).confirmed()]
    finally:
        conn.close()


def test_a_match_to_what_was_not_offered_or_on_anothers_connection_is_missing(
    matched: Household, clock: FakeClock
) -> None:
    ids = matched.ids
    avery = bridge(matched, clock, "avery")
    tries = [
        (ids["ins_zq_bank/blakes"], "Assets:Zq:Blake:Savings"),
        (ids["ins_zq_bank/chk"], "Assets:Zq:Blake:Checking"),
        (ids["ins_zq_bank/chk"], "Assets:Zq:Nowhere"),
        (ids["ins_zq_bank/loan"], "Liabilities:Zq:Mortgage"),
        (ids["ins_zq_bank/sav"], "Assets:Zq:Elsewhere"),
        (ids["ins_zq_blake/chk"], "Assets:Zq:Blake:Checking"),
        (ids["ins_zq_blake/joint"], "Assets:Zq:Joint"),
        ("no-such-account", "Assets:Zq:Checking"),
    ]
    for account, to in tries:
        assert match(avery, {account: to}) is Acted.MISSING
        # Beside a match that would be recorded, it is missing too, and neither is.
        alongside = {ids["ins_zq_union/chk"]: "Assets:Zq:Union", account: to}
        assert match(avery, alongside) is Acted.MISSING
    assert avery.act("match_accounts", {"matches": {}}) is Acted.MISSING
    assert avery.act("match_accounts", {"account": ids["ins_zq_bank/chk"]}) is Acted.MISSING
    assert _confirmed(matched) == []


# Moving an account hearth keeps onto one nobody claimed (``access.may_carry_on``).


def test_rows_alone_never_hand_over_an_account_nobody_claimed(
    unclaimed: Household, clock: FakeClock
) -> None:
    """The credit union's checking, filed as an account hearth keeps itself, whose rows the
    ledger's union account clearly holds by content alone: nobody has claimed that account,
    and its rows could be anyone's, so it is neither named nor offered, and a match to it is
    missing."""
    keep(unclaimed, "ins_zq_union/chk", "Assets:Avery:Zq:Union", "avery")
    door = bridge(unclaimed, clock, "avery")

    one = _accounts(ask(door, "matching"))[unclaimed.ids["ins_zq_union/chk"]]

    assert one["state"] == "kept"
    assert one["proposed"] is None
    assert one["why_not"] == "unclear"
    assert "Assets:Zq:Union" not in json.dumps(one)
    assert match(door, {unclaimed.ids["ins_zq_union/chk"]: "Assets:Zq:Union"}) is Acted.MISSING
    assert "Assets:Zq:Union" not in owners_of(unclaimed)


@pytest.mark.parametrize("owners", [("avery",), ("avery", "blake")], ids=["alone", "joint"])
def test_the_ledgers_own_ids_hand_an_account_nobody_claimed_to_its_owners(
    unclaimed: Household, clock: FakeClock, owners: tuple[str, ...]
) -> None:
    """The card, filed as an account hearth keeps, whose six charges the ledger records by
    their Plaid ids on its card account, up to its last month there: offered, and moved onto,
    its owners becoming that account's, a co-owner's with them."""
    card = unclaimed.ids["ins_zq_bank/card"]
    keep(unclaimed, "ins_zq_bank/card", "Liabilities:Avery:Zq:Card", *owners)
    door = bridge(unclaimed, clock, "avery")

    one = _accounts(ask(door, "matching"))[card]

    assert one["proposed"]["account"] == "Liabilities:Zq:Card"
    assert one["why_not"] is None
    assert one["choices"][0] == {
        **one["choices"][0],
        "account": "Liabilities:Zq:Card",
        "refused": None,
    }
    assert match(door, {card: "Liabilities:Zq:Card"}) is Acted.MATCHED
    owned = owners_of(unclaimed)
    assert owned["Liabilities:Zq:Card"] == frozenset(owners)
    assert "Liabilities:Avery:Zq:Card" not in owned


def test_an_account_kept_for_someone_else_is_not_handed_over_by_whoever_holds_its_bank(
    unclaimed: Household, clock: FakeClock
) -> None:
    """The card's connection is Avery's, but the account hearth keeps it as is Blake's alone:
    Avery may not move it, and give Blake a history, any more than take one."""
    card = unclaimed.ids["ins_zq_bank/card"]
    keep(unclaimed, "ins_zq_bank/card", "Liabilities:Blake:Zq:Card", "blake")
    door = bridge(unclaimed, clock, "avery")

    one = _accounts(ask(door, "matching"))[card]

    assert "Liabilities:Zq:Card" not in [c["account"] for c in one["choices"]]
    assert one["proposed"] is None
    assert match(door, {card: "Liabilities:Zq:Card"}) is Acted.MISSING
    assert "Liabilities:Zq:Card" not in owners_of(unclaimed)


@pytest.mark.parametrize("short", ["too few", "too long ago"])
def test_ids_too_few_or_too_old_hand_nothing_over(
    unclaimed: Household, clock: FakeClock, monkeypatch: pytest.MonkeyPatch, short: str
) -> None:
    """Four instalments of the mortgage by their ids are fewer than content alone needs; the
    card's charges, held to end within a day of its last entry, stop three weeks short."""
    if short == "too few":
        key, kept, base_account = (
            "ins_zq_bank/loan",
            "Liabilities:Avery:Zq:Loan",
            "Liabilities:Zq:Mortgage",
        )
    else:
        monkeypatch.setattr(matching, "RECENT", timedelta(days=1))
        key, kept, base_account = (
            "ins_zq_bank/card",
            "Liabilities:Avery:Zq:Card",
            "Liabilities:Zq:Card",
        )
    keep(unclaimed, key, kept, "avery")
    door = bridge(unclaimed, clock, "avery")

    one = _accounts(ask(door, "matching"))[unclaimed.ids[key]]

    assert base_account not in [c["account"] for c in one["choices"]]
    assert match(door, {unclaimed.ids[key]: base_account}) is Acted.MISSING
    assert base_account not in owners_of(unclaimed)


def test_a_refusal_is_told_ahead_by_the_check_that_makes_it(
    matched: Household, clock: FakeClock
) -> None:
    """The union's account is offered to the checking account too; matched there already,
    the checking account's own is refused as taken, by the code the command line's refusal
    carries."""
    door = bridge(matched, clock, "avery")
    ids = matched.ids
    assert match(door, {ids["ins_zq_union/chk"]: "Assets:Zq:Union"}) is Acted.MATCHED

    chk = _accounts(ask(door, "matching"))[ids["ins_zq_bank/chk"]]
    refused = {c["account"]: c["refused"] for c in chk["choices"]}

    assert refused == {
        "Assets:Zq:Checking": None,
        "Assets:Zq:Joint": None,
        "Assets:Zq:Union": {"why": "taken", "count": None, "day": None},
    }


def test_without_an_imported_ledger_there_is_nothing_to_match(
    fresh: Household, clock: FakeClock
) -> None:
    answer = ask(bridge(fresh, clock, "avery"), "matching")

    assert answer == {
        "state": "ready",
        "imported": False,
        "unsure": None,
        "accounts": [],
        "totals": {"matched": 0, "of": 0, "in_ledger": 0, "booked": 0},
    }


def test_how_many_accounts_nobody_sees_is_told_to_an_admin_alone(
    unclaimed: Household, clock: FakeClock
) -> None:
    told = {who: ask(bridge(unclaimed, clock, who), "ledger")["imported"] for who in USERS}

    assert isinstance(told["avery"]["unclaimed"], int)
    assert told["avery"]["unclaimed"] > 0
    assert told["blake"]["unclaimed"] is None
    assert {k: v for k, v in told["blake"].items() if k != "unclaimed"} == {
        k: v for k, v in told["avery"].items() if k != "unclaimed"
    }


USERS = ("avery", "blake")


# What a member learns.


def _secrets_of_blake() -> list[str]:
    """What only Blake's connections and accounts say."""
    return [
        "Zq Savings Bank",
        "Assets:Zq:Blake:Checking",
        "Assets:Zq:Blake:Savings",
        "Liabilities:Zq:Blake:Card",
        "Zq Florist",
        "Zq Bakery",
    ]


def test_no_ledger_screen_or_act_tells_avery_what_is_blakes(
    matched: Household, clock: FakeClock, kept: Path
) -> None:
    door = bridge(matched, clock, "avery")
    blakes = [v for k, v in matched.ids.items() if k.startswith("ins_zq_blake/")]
    answers: list[object] = [door.answer(ask) for ask in ("ledger", "matching")]
    answers.append(check(door, kept))
    for account in [*blakes, *matched.ids.values()]:
        for to in ("Assets:Zq:Blake:Checking", "Assets:Zq:Joint", "Liabilities:Zq:Blake:Card"):
            answers.append(str(match(door, {account: to})))
    seen = Seen("\n".join(json.dumps(a) for a in answers))

    leaked = [text for text in [*_secrets_of_blake(), *blakes] if text in seen]
    assert not leaked, leaked


# One shape, checked from both sides.


@pytest.fixture
def every_screen(matched: Household, clock: FakeClock, kept: Path) -> dict[str, object]:
    door = bridge(matched, clock, "avery")
    return {
        "ledger": ask(door, "ledger"),
        "ledger_check": check(door, kept),
        "matching": ask(door, "matching"),
        "ledger_owners": ask(door, "ledger_owners"),
    }


@pytest.mark.parametrize("screen", ["ledger", "ledger_check", "matching", "ledger_owners"])
def test_each_ledger_screen_has_the_shape_the_app_decodes(
    every_screen: dict[str, object], screen: str
) -> None:
    """Each example in ``fixtures`` is what the app's own tests decode (``apple/HearthKit``)."""
    example = json.loads((FIXTURES / f"{screen}.json").read_text())

    assert differences(every_screen[screen], example["answer"]) == []


# What a ledger may read: never past itself (the second review).


def _escaping(tmp_path: Path, include: str, *extra: str) -> Path:
    """A ledger two folders down from `tmp_path`, beside the data directory, that includes
    `include`; `extra` are files written beside it, by their path from `tmp_path`."""
    folder = tmp_path / "x" / "a"
    folder.mkdir(parents=True)
    for name in extra:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("2020-01-02 open Assets:Zq:Beside USD\n")
    ledger = folder / "main.beancount"
    ledger.write_text(
        f'option "operating_currency" "USD"\n2020-01-01 open Assets:Zq:A USD\ninclude "{include}"\n'
    )
    return ledger


@pytest.mark.parametrize(
    ("include", "extra", "why"),
    [
        # Matches nothing beside the ledger, but in a copy kept in the data directory, the very
        # same glob finds hearth's own books there.
        ("../../*.beancount", (), "include_missing"),
        # Matches a file beside the ledger, by a way that climbs above the folder that holds
        # every file the ledger reads, as no copy of that folder could follow.
        ("../../x/b/y.beancount", ("x/b/y.beancount",), "include_outside"),
        ("*/../../../*.beancount", ("x/a/c/q.beancount", "z.beancount"), "include_outside"),
    ],
    ids=["matching nothing", "climbing out and back", "climbing after a wildcard"],
)
@pytest.mark.parametrize("by", ["check", "the app's import", "the command line's import"])
def test_no_include_ever_reads_past_the_ledger_or_into_hearths_own_books(
    fresh: Household,
    clock: FakeClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    in_process: None,
    include: str,
    extra: tuple[str, ...],
    why: str,
    by: str,
) -> None:
    """The reviewer's case: a ledger beside the data directory whose include, copied into it,
    would read hearth's books, every member's. Refused before any copy is loaded: beancount
    reads no file but the ledger's own copy, and no answer names the data directory."""
    ledger = _escaping(tmp_path, include, *extra)
    door = bridge(fresh, clock, "avery")
    read: list[str] = []
    real = books._read

    def spy(path: Path) -> Any:
        found = real(path)
        read.extend(found[2]["include"])
        return found

    monkeypatch.setattr(books, "_read", spy)
    if by == "check":
        answer: Any = check(door, ledger)
        refused = answer["refused"]
    elif by == "the app's import":
        answer = _brought_in(door, ledger)
        refused = answer["refused"]
    else:
        with pytest.raises(ledger_import.ImportRefused) as caught:
            ledger_import.import_ledger(fresh.settings, ledger, now=NOW)
        answer = {"why": caught.value.why.value, "said": caught.value.plain}
        refused = answer

    assert refused["why"] == why
    assert read == [], "beancount read files while a ledger that leaves itself was refused"
    assert str(fresh.settings.data_dir) not in json.dumps(answer)
    assert "books.beancount" not in json.dumps(answer)
    assert base.current(fresh.settings.ledger_base) is None


def test_a_check_that_takes_too_long_is_stopped_and_leaves_nothing(
    fresh: Household, clock: FakeClock, kept: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run in a process of its own, a check that runs past its limit is killed, refused in
    words of its own, and the copy it was loading is taken away."""
    monkeypatch.setattr(ledger_child, "LIMIT", 0.01)
    door = bridge(fresh, clock, "avery")

    assert check(door, kept)["refused"]["why"] == "too_slow"
    assert not list(fresh.settings.data_dir.glob(f"{base.CHECKING}*"))


def test_a_file_of_one_endless_line_is_refused_promptly_and_holds_nothing_up(
    fresh: Household, clock: FakeClock, tmp_path: Path
) -> None:
    """The reviewer's case: 8 MB on one line, which beancount's parser takes a minute over,
    holding Python's interpreter lock the while. Refused before it is parsed, in a process of
    its own, and every other ask the app makes meanwhile is answered at once."""
    door = bridge(fresh, clock, "avery")
    line = tmp_path / "one-line.json"
    line.write_bytes(b";" * (8 << 20) + b"\n")
    given = {"path": str(line)}
    started = time.perf_counter()

    assert door.answer("ledger_check", given) == {"state": "loading"}
    slowest = 0.0
    while (told := door.answer("ledger_check", given))["state"] == "loading":
        asked = time.perf_counter()
        assert door.answer("overview")["state"] == "ready"
        slowest = max(slowest, time.perf_counter() - asked)
        time.sleep(0.02)

    assert told["refused"] == {
        "why": "line_too_long",
        "said": "one-line.json has a line longer than 64 KB, which no ledger has, so it is not a"
        " ledger.",
    }
    assert time.perf_counter() - started < 10
    assert slowest < 0.5, f"an ask waited {slowest:.2f}s on the check"


def test_an_import_that_waits_too_long_on_another_is_refused_in_words(
    fresh: Household, clock: FakeClock, monkeypatch: pytest.MonkeyPatch, in_process: None
) -> None:
    """A holder of the base's lock that never lets go: the import ends as a refusal of its own,
    not `running` for ever."""
    monkeypatch.setattr(base, "WAIT", 0.2)
    door = bridge(fresh, clock, "avery")

    with base.locked(fresh.settings.ledger_base):
        told = _brought_in(door, fresh.ledger)

    assert told["outcome"] == "refused"
    assert told["refused"]["why"] == "busy"
    assert base.current(fresh.settings.ledger_base) is None


def test_a_file_system_that_cannot_lock_is_refused_in_words(
    fresh: Household, clock: FakeClock, monkeypatch: pytest.MonkeyPatch, in_process: None
) -> None:
    def cannot(descriptor: int, operation: int) -> None:
        raise OSError(errno.ENOLCK, os.strerror(errno.ENOLCK))

    monkeypatch.setattr(fcntl, "flock", cannot)
    door = bridge(fresh, clock, "avery")

    told = _brought_in(door, fresh.ledger)

    assert told["refused"]["why"] == "lock_unavailable"


# Slow answers, worked out off the wire, are always as things stand (the second review).


def test_an_answer_worked_out_before_the_viewers_own_act_is_never_handed_out_after_it(
    matched: Household, clock: FakeClock
) -> None:
    door = bridge(matched, clock, "avery")
    chk = matched.ids["ins_zq_bank/chk"]
    assert door.answer("matching") == {"state": "loading"}

    assert match(door, {chk: "Assets:Zq:Checking"}) is Acted.MATCHED

    assert _accounts(ask(door, "matching"))[chk]["state"] == "matched"


def test_an_answer_finished_before_somebody_else_changed_things_is_worked_out_again(
    matched: Household, clock: FakeClock
) -> None:
    """Finished, and not yet picked up, when the mortgage nobody had claimed is claimed for
    Avery elsewhere, as an admin's shell does: the next answer is as things stand after it."""
    door = bridge(matched, clock, "avery")
    loan = matched.ids["ins_zq_bank/loan"]
    assert _accounts(ask(door, "matching"))[loan]["proposed"] is None
    assert door.answer("matching") == {"state": "loading"}
    time.sleep(1)  # finished by now, and not picked up
    conn = matched.fresh.connect()
    try:
        accounts.claim(
            conn,
            ledger_account="Liabilities:Zq:Mortgage",
            name="Mortgage",
            owners=[matched.fresh.member_ids["avery"]],
            now=NOW,
        )
    finally:
        conn.close()

    after = _accounts(ask(door, "matching"))[loan]

    assert after["proposed"]["account"] == "Liabilities:Zq:Mortgage"


def test_one_members_slow_answer_never_reaches_another(
    matched: Household, clock: FakeClock, kept: Path
) -> None:
    door = bridge(matched, clock, "avery")
    # Both admins, so nothing but who is asking tells their answers apart.
    conn = matched.fresh.connect()
    try:
        conn.execute("UPDATE members SET is_admin = 1 WHERE username = 'blake'")
        conn.commit()
    finally:
        conn.close()
    assert door.answer("matching") == {"state": "loading"}
    time.sleep(1)

    assert door.sign_in("blake", password_of("blake")) is Outcome.SIGNED_IN
    blakes = {v for k, v in matched.ids.items() if k.startswith("ins_zq_blake/")}

    assert set(_accounts(ask(door, "matching"))) == blakes


def test_an_admin_who_is_one_no_longer_is_told_nothing_worked_out_while_they_were(
    matched: Household, clock: FakeClock, kept: Path
) -> None:
    door = bridge(matched, clock, "avery")
    given = {"path": str(kept)}
    assert door.answer("ledger_check", given) == {"state": "loading"}
    time.sleep(1)
    conn = matched.fresh.connect()
    try:
        conn.execute("UPDATE members SET is_admin = 0 WHERE username = 'avery'")
        conn.commit()
    finally:
        conn.close()

    assert settled(door, "ledger_check", given) == {"state": "missing"}


def test_ids_elsewhere_are_counted_only_on_accounts_the_viewer_may_name(
    matched: Household, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checking's ids, said to land three on Blake's savings and three on Avery's own union
    account besides: only Avery's three are counted elsewhere."""
    real = matching.weigh
    chk = matched.ids["ins_zq_bank/chk"]

    def weighed(settings: Settings, today: date | None = None) -> matching.Weighing:
        found = real(settings, today)
        one = found.survey.find(chk)
        assert one is not None
        ids = one.proposal.ids
        on = {
            **ids.on,
            "Assets:Zq:Blake:Savings": Stretch(3),
            "Assets:Zq:Union": Stretch(3),
        }
        changed = replace(one, proposal=replace(one.proposal, ids=replace(ids, on=on)))
        accounts_ = tuple(changed if s is one else s for s in found.survey.accounts)
        return replace(found, survey=replace(found.survey, accounts=accounts_))

    monkeypatch.setattr(matching, "weigh", weighed)
    door = bridge(matched, clock, "avery")

    proposed = _accounts(ask(door, "matching"))[chk]["proposed"]

    assert proposed["account"] == "Assets:Zq:Checking"
    assert proposed["elsewhere"] == 3


def test_any_act_of_the_viewers_lets_go_of_what_was_worked_out_for_them(
    matched: Household, clock: FakeClock
) -> None:
    """Even one that changes nothing: the next slow answer is worked out afresh, never one from
    before it."""
    door = bridge(matched, clock, "avery")
    assert door.answer("matching") == {"state": "loading"}
    time.sleep(1)  # finished by now, and not picked up

    assert match(door, {"no-such-account": "Assets:Zq:Checking"}) is Acted.MISSING

    assert door.answer("matching") == {"state": "loading"}


# The children a check runs in (the third review).


@pytest.fixture
def slow(tmp_path: Path) -> Path:
    """A file within every cap that takes a check seconds to read: as many of the longest lines
    a ledger may have as fit."""
    line = b";" * (ledger_import.LONGEST_LINE - 1) + b"\n"
    path = tmp_path / "slow.beancount"
    head = b'option "operating_currency" "USD"\n2020-01-01 open Assets:Zq:A USD\n'
    path.write_bytes(head + line * ((ledger_import.LARGEST - len(head)) // len(line)))
    return path


@pytest.fixture
def children(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every child process a check or an import starts, as it is started, with what it is
    given."""
    started: list[Any] = []
    real = subprocess.Popen

    def spy(*args: Any, **kwargs: Any) -> Any:
        child: Any = real(*args, **kwargs)
        child.given = kwargs
        started.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", spy)
    return started


def _running(started: list[Any]) -> int:
    return sum(1 for child in started if child.poll() is None)


def _claim_elsewhere(household: Household, account: str) -> None:
    """A claim made by another process, as a sync or an admin's shell changes the books."""
    conn = household.fresh.connect()
    try:
        accounts.claim(
            conn,
            ledger_account=account,
            name="Zq",
            owners=[household.fresh.member_ids["avery"]],
            now=NOW,
        )
    finally:
        conn.close()


def test_asking_again_while_the_books_change_runs_one_check_not_one_an_ask(
    fresh: Household, clock: FakeClock, slow: Path, children: list[Any]
) -> None:
    """The reviewer's case: the app polls a slow check while a sync changes the books between
    polls. A check reads nothing of the books, so the same check goes on, alone, and answers."""
    door = bridge(fresh, clock, "avery")
    given = {"path": str(slow)}
    most = 0
    n = 0
    while (told := door.answer("ledger_check", given))["state"] == "loading":
        _claim_elsewhere(fresh, f"Assets:Zq:Poll{n}")
        n += 1
        most = max(most, _running(children))
        assert n < 150, "started over at every ask, the check never answered"
        time.sleep(0.2)

    assert n > 3, "the check was over before the books changed"
    assert told["state"] == "ready"
    assert len(children) == 1
    assert most <= 1


def test_a_check_no_longer_wanted_is_killed_and_the_next_waits_for_it(
    fresh: Household, clock: FakeClock, slow: Path, kept: Path, children: list[Any]
) -> None:
    """A check superseded by a check of another file, or let go when the viewer acts, has its
    child killed, never left to run on: one viewer never has two checks running at once."""
    door = bridge(fresh, clock, "avery")
    assert door.answer("ledger_check", {"path": str(slow)}) == {"state": "loading"}
    _until(lambda: _running(children) == 1)

    most = 0
    while (told := door.answer("ledger_check", {"path": str(kept)}))["state"] == "loading":
        most = max(most, _running(children))
        time.sleep(0.02)

    assert told["found"] is not None, "the check of the other file answered"
    assert most <= 1
    assert children[0].returncode == -9, "the superseded check was killed"
    started = len(children)
    assert door.answer("ledger_check", {"path": str(slow)}) == {"state": "loading"}
    _until(lambda: len(children) == started + 1)

    assert match(door, {"no-such-account": "Assets:Zq:Checking"}) is Acted.MISSING

    _until(lambda: _running(children) == 0, within=2.0)
    assert children[-1].returncode == -9, "the check let go of was killed"
    assert not list(fresh.settings.data_dir.glob(f"{base.CHECKING}*"))


def _until(condition: Callable[[], bool], within: float = 10.0) -> None:
    deadline = time.monotonic() + within
    while not condition():
        assert time.monotonic() < deadline, "it never came about"
        time.sleep(0.02)


def test_a_check_ends_with_the_process_that_asked_for_it_and_leaves_nothing(
    fresh: Household, slow: Path
) -> None:
    """The reviewer's case: the bridge is killed while its check runs. The check's child dies
    with it, and the copy it was loading in the data directory goes with them."""
    code = (
        "import sys\n"
        "from datetime import UTC, datetime\n"
        "from pathlib import Path\n"
        "from hearth import ledger_child\n"
        "from hearth.config import Settings\n"
        "real = ledger_child.subprocess.Popen\n"
        "def told(*args, **kwargs):\n"
        "    child = real(*args, **kwargs)\n"
        "    print(child.pid, flush=True)\n"
        "    return child\n"
        "ledger_child.subprocess.Popen = told\n"
        "settings = Settings.in_directory(Path(sys.argv[1]))\n"
        "ledger_child.check(settings, Path(sys.argv[2]), now=datetime.now(UTC))\n"
    )
    asking = subprocess.Popen(  # noqa: S603 - this very Python, running the test's own code
        [sys.executable, "-c", code, str(fresh.settings.data_dir), str(slow)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert asking.stdout is not None
    child = int(asking.stdout.readline())
    data = fresh.settings.data_dir
    _until(lambda: any(data.glob(f"{base.CHECKING}*/*/*")), within=10.0)

    asking.kill()
    asking.wait()
    asking.stdout.close()

    _until(lambda: not _alive(child), within=3.0)
    _until(lambda: not list(data.glob(f"{base.CHECKING}*")), within=3.0)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_an_import_waiting_on_another_is_busy_however_long_reading_may_take(
    fresh: Household, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's case: another process holds the base's lock past the time an import may
    wait on it. The wait is the bridge's, before the child starts, and never counted against
    the time the child may read: so it is refused as busy, not as too slow to read."""
    holder = subprocess.Popen(  # noqa: S603 - this very Python, running hearth's own code
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            "from pathlib import Path\n"
            "from hearth import base\n"
            "with base.locked(Path(sys.argv[1])):\n"
            "    print('held', flush=True)\n"
            "    time.sleep(60)\n",
            str(fresh.settings.ledger_base),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    try:
        assert holder.stdout.readline().strip() == "held"
        monkeypatch.setattr(base, "WAIT", 1.0)
        monkeypatch.setattr(ledger_child, "LIMIT", 0.5)
        door = bridge(fresh, clock, "avery")

        told = _brought_in(door, fresh.ledger)
    finally:
        holder.kill()
        holder.wait()
        holder.stdout.close()

    assert told["refused"]["why"] == "busy"
    assert base.current(fresh.settings.ledger_base) is None


def test_a_check_past_its_limit_is_killed_at_once_not_waited_on(
    fresh: Household,
    clock: FakeClock,
    slow: Path,
    children: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The child, and every process of its session, is killed at its limit: the refusal comes
    then, not when the reading would have ended."""
    monkeypatch.setattr(ledger_child, "LIMIT", 0.3)
    door = bridge(fresh, clock, "avery")
    started = time.monotonic()

    assert check(door, slow)["refused"]["why"] == "too_slow"

    assert time.monotonic() - started < 1.5
    assert [child.returncode for child in children] == [-9]


def test_the_child_is_given_nothing_of_the_bridges_environment_but_what_it_needs(
    fresh: Household,
    clock: FakeClock,
    kept: Path,
    children: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No variable of hearth's, the data directory, a ledger, a key, nor anything else the
    bridge was started with, reaches the child that reads a file."""
    for name in ("HEARTH_DATA_DIR", "HEARTH_EODHD_API_KEY", "HEARTH_ZQ_APPROVE_PLAID", "ZQ_TOKEN"):
        monkeypatch.setenv(name, "zq-not-for-the-child")
    door = bridge(fresh, clock, "avery")

    assert check(door, kept)["found"] is not None

    (given,) = [child.given for child in children]
    assert set(given["env"]) <= {"HOME", "TMPDIR", "LANG", "LC_ALL", "TZ"}
    assert "zq-not-for-the-child" not in json.dumps(given["env"])


@pytest.mark.parametrize(
    ("include", "extra"),
    [
        ("../../x/b/y.beancount", ("x/b/y.beancount",)),
        ("*/../../../*.beancount", ("x/a/c/q.beancount", "z.beancount")),
    ],
    ids=["climbing out and back", "climbing after a wildcard"],
)
@pytest.mark.parametrize("alone", ["_contained", "_staged_holds"])
def test_either_wall_alone_keeps_a_copy_from_reading_past_itself(
    fresh: Household,
    clock: FakeClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    in_process: None,
    include: str,
    extra: tuple[str, ...],
    alone: str,
) -> None:
    """With the other taken down, each of the two walls on its own refuses a ledger whose
    include would lead its copy out of itself, before beancount reads a file."""
    other = {"_contained": "_staged_holds", "_staged_holds": "_contained"}[alone]
    monkeypatch.setattr(ledger_import, other, lambda *_: None)
    read: list[str] = []
    real = books._read

    def spy(path: Path) -> Any:
        found = real(path)
        read.extend(found[2]["include"])
        return found

    monkeypatch.setattr(books, "_read", spy)
    ledger = _escaping(tmp_path, include, *extra)
    door = bridge(fresh, clock, "avery")

    assert check(door, ledger)["refused"]["why"] == "include_outside"
    assert read == []
