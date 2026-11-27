"""The household's own ledger in the app: checking a file, bringing it in once, and matching
each member's own connected accounts to it, through a bridge a member signed in to.

Every refusal the command line gives is reproduced here as the app is told it, and each place
a member could learn of what is not theirs is asked: another member's connections and the
accounts the ledger's evidence says are theirs, and what lives only in the ledger file checked.
Every name and figure is invented (``tests.ledgers``)."""

import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest

from hearth import accounts, base, books, ledger_import
from hearth.disclosure import may_learn, rulings_in
from hearth.disclosure.ledger import BroughtIn
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
def kept(tmp_path: Path) -> Path:
    """The invented ledger of every shape (``tests.ledgers``), in a folder of its own."""
    folder = tmp_path / "kept"
    folder.mkdir()
    return ledgers.write(folder)


def ask(door: Bridge, screen: str) -> dict[str, Any]:
    return door.answer(screen)


def check(door: Bridge, path: Path | str) -> dict[str, Any]:
    return door.answer("ledger_check", {"path": str(path)})


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

    assert check(door, missing)["refused"] == (
        f"{missing} cannot be read: No such file or directory."
    )
    assert check(door, tmp_path / "a-folder") == {
        "state": "ready",
        "found": None,
        "refused": f"{tmp_path / 'a-folder'} is not a file, so it is not a ledger.",
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
        "refused": f"{pipe} is not a file, so it is not a ledger.",
    }

    # Included by a glob, beside the ledger, it is refused before beancount would open it.
    os.mkfifo(kept.parent / "years" / "2026-3.beancount")
    globbed = _within(10, lambda: check(door, kept))
    assert isinstance(globbed, dict)
    assert globbed["refused"] == (
        f"{kept.parent / 'years' / '2026-3.beancount'} is not a file, so it is not a ledger."
    )


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
        "refused": (
            f"accounts.beancount, line 9, includes {outside} by its absolute path, which a"
            " frozen copy would still read from where it is. Make it relative to the file that"
            " includes it, and import again."
        ),
    }


def test_a_file_that_is_not_a_ledger_is_refused_and_never_echoed(
    fresh: Household, clock: FakeClock, tmp_path: Path
) -> None:
    door = bridge(fresh, clock, "avery")
    note = tmp_path / "notes.txt"
    note.write_text("Zq-secret-word 1,234.56 spent at Zq Hideaway\nnot a ledger at all\n")

    answer = check(door, note)

    assert answer["found"] is None
    refused = answer["refused"]
    assert isinstance(refused, str)
    assert refused.startswith("The ledger does not load cleanly.")
    for text in ("Zq-secret-word", "1,234.56", "1234.56", "Hideaway", "not a ledger"):
        assert text not in json.dumps(answer)


def test_a_file_larger_than_any_ledger_is_refused_unread(
    fresh: Household, clock: FakeClock, kept: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ledger_import, "LARGEST", 1024 * 1024)
    door = bridge(fresh, clock, "avery")
    prices = kept.parent / "prices/zqx.beancount"
    prices.write_text(ledgers.PRICES + "; padding\n" * 120_000)

    assert check(door, kept) == {
        "state": "ready",
        "found": None,
        "refused": f"{prices} is larger than 1 MB, which no ledger's text is.",
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
    assert answer["refused"] == (
        "A balance assertion fails: on Jul 2, 2026, at 2026-2.beancount, line 6, the postings"
        " do not add up to what it asserts."
    )
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
        assert str(answer["refused"]).startswith("HEARTH_LEDGER is set")
    standing = ask(door, "ledger")
    assert standing["inherited"] is True
    assert standing["may_bring_in"] is False
    assert str(standing["why_not"]).startswith("HEARTH_LEDGER is set")


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

    for path, named in (
        (written, written),
        (linked / "books.beancount", linked / "books.beancount"),
        (including, linked / "books.beancount"),
    ):
        answer = check(door, path)
        assert answer == {
            "state": "ready",
            "found": None,
            "refused": f"{named} is in hearth's own data directory, where it keeps its books,"
            " so it is not a ledger to import. Choose the ledger the household kept itself.",
        }
    assert door.act("bring_in_ledger", {"path": str(written)}) is Acted.STARTED
    importer = door.importer
    assert importer is not None
    importer.wait()
    assert base.current(fresh.settings.ledger_base) is None


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
    assert standing["why_not"] == (
        f"Your ledger was brought in on Nov {TODAY.day}, 2026, and a ledger is brought in once:"
        " hearth's books are the truth from then on."
    )

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
    fresh: Household, clock: FakeClock, monkeypatch: pytest.MonkeyPatch, stops: str
) -> None:
    """Stopped while the copy is staged, or once its folder is in place but before the
    manifest names it, the household holds no base, and a later import goes ahead whole."""
    door = bridge(fresh, clock, "avery")
    real_commit, real_load = base.commit, books.load_ledger

    def interrupted_commit(directory: Path, frozen: base.Base, **_: object) -> base.Imported:
        # The copy's folder goes where the manifest would name it, and then the power fails.
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
    assert not list(fresh.settings.ledger_base.glob(".importing-*"))
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
        return BroughtIn("refused", "held up")

    assert importer.start("avery", held_up)

    assert door.act("bring_in_ledger", {"path": str(fresh.ledger)}) is Acted.RUNNING
    assert ask(door, "ledger")["bringing_in"]["running"] is True
    release.set()
    importer.wait()


# Matching.


@pytest.fixture
def matched(tmp_path: Path, clock: FakeClock) -> Household:
    """The matching household with its ledger imported and its accounts claimed
    (``tests.test_matching``)."""
    return build(tmp_path, clock)


def _accounts(answer: dict[str, Any]) -> dict[str, dict[str, Any]]:
    found = answer["accounts"]
    assert isinstance(found, list)
    return {a["id"]: a for a in found}


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
    # Nobody has claimed the mortgage; the ledger records the connection's ids on it.
    assert proposed("ins_zq_bank/loan") == ("Liabilities:Zq:Mortgage", "unclaimed")
    assert proposed("ins_zq_bank/sav") == (None, "unclear")
    # Blake's alone, though Avery's login reaches it: never named to Avery.
    assert proposed("ins_zq_bank/blakes") == (None, "not_yours")
    assert "Assets:Zq:Blake" not in json.dumps(answer)
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


def test_a_match_is_recorded_as_the_member_and_carried_on(
    matched: Household, clock: FakeClock
) -> None:
    door = bridge(matched, clock, "avery")
    chk = matched.ids["ins_zq_bank/chk"]

    assert door.act("match_account", {"account": chk, "to": "Assets:Zq:Checking"}) is (
        Acted.MATCHED
    )

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
        assert avery.act("match_account", {"account": account, "to": to}) is Acted.MISSING
    assert avery.act("match_account", {"account": ids["ins_zq_bank/chk"]}) is Acted.MISSING
    conn = matched.fresh.connect()
    try:
        assert Registry(conn).confirmed() == []
    finally:
        conn.close()


def test_an_account_hearth_keeps_moves_onto_one_nobody_claimed_only_by_the_ledgers_ids(
    matched: Household, clock: FakeClock
) -> None:
    """Filed as accounts hearth keeps itself: the mortgage, whose ids the ledger records on an
    account nobody claimed, moves onto it, the claim going with it; the savings, which only an
    unclaimed account with nothing to say for it could take, is offered nothing of the kind."""
    avery_id = matched.fresh.member_ids["avery"]
    conn = matched.fresh.connect()
    try:
        for key, name in (
            ("ins_zq_bank/loan", "Liabilities:Avery:Zq:Mortgage"),
            ("ins_zq_bank/sav", "Assets:Avery:Zq:Savings"),
        ):
            account = accounts.register(conn, ledger_account=name, name=key, owners=[avery_id])
            Registry(conn).confirm(matched.ids[key], account.id, by=avery_id, now=NOW)
    finally:
        conn.close()
    door = bridge(matched, clock, "avery")
    found = _accounts(ask(door, "matching"))
    loan, sav = found[matched.ids["ins_zq_bank/loan"]], found[matched.ids["ins_zq_bank/sav"]]
    assert loan["state"] == "kept"
    # The one nobody claimed that its ids are on, first; then Avery's own liabilities.
    assert [c["account"] for c in loan["choices"]] == [
        "Liabilities:Zq:Mortgage",
        "Liabilities:Zq:Card",
        "Liabilities:Zq:CardA",
    ]
    assert loan["why_not"] is None
    assert "Assets:Zq:Elsewhere" not in json.dumps(sav)

    assert (
        door.act(
            "match_account",
            {"account": matched.ids["ins_zq_bank/loan"], "to": "Liabilities:Zq:Mortgage"},
        )
        is Acted.MATCHED
    )
    conn = matched.fresh.connect()
    try:
        owned = {a.ledger_account: a.owners for a in accounts.all_accounts(conn)}
    finally:
        conn.close()
    assert owned["Liabilities:Zq:Mortgage"] == frozenset({avery_id})
    assert "Liabilities:Avery:Zq:Mortgage" not in owned


def test_a_refusal_is_told_ahead_by_the_check_that_makes_it(
    matched: Household, clock: FakeClock
) -> None:
    """The union's account is offered to the checking account too; matched there already,
    the checking account's own is refused as taken, by the code the command line's refusal
    carries."""
    door = bridge(matched, clock, "avery")
    ids = matched.ids
    assert door.act(
        "match_account", {"account": ids["ins_zq_union/chk"], "to": "Assets:Zq:Union"}
    ) is (Acted.MATCHED)

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
            answers.append(str(door.act("match_account", {"account": account, "to": to})))
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
    }


@pytest.mark.parametrize("screen", ["ledger", "ledger_check", "matching"])
def test_each_ledger_screen_has_the_shape_the_app_decodes(
    every_screen: dict[str, object], screen: str
) -> None:
    """Each example in ``fixtures`` is what the app's own tests decode (``apple/HearthKit``)."""
    example = json.loads((FIXTURES / f"{screen}.json").read_text())

    assert differences(every_screen[screen], example["answer"]) == []
