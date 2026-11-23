"""Importing a ledger kept before hearth: the check it prints, the frozen copy it keeps, the
books it computes, and every case in which it writes nothing."""

import json
import os
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from beancount import loader
from beancount.core.data import Custom, Directive, Document, Price
from beancount.parser import printer

from hearth import base, books, cli, db, members
from hearth.cli import main
from hearth.config import DATA_DIR_VARIABLE, LEDGER_VARIABLE, Settings
from hearth.disclosure import rulings_in
from hearth.ledger_import import describe, import_ledger
from hearth.plaid import feed
from hearth.review.rulings import Action, RulingStore, Scope
from tests import ledgers
from tests.conftest import FakeClock, World

NOW = datetime(2026, 11, 22, 18, 0, tzinfo=UTC)
"""Evening in Greenwich: already Nov 23 at UTC+14, still Nov 22 at UTC-11."""


def _today() -> date:
    """NOW's day where hearth runs, by the rule every figure follows (``books.compute``)."""
    return NOW.astimezone().date()


def _said(day: date) -> str:
    return f"{day:%b} {day.day}, {day.year}"


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """The invented ledger, in a folder of its own, outside any data directory."""
    folder = tmp_path / "kept-before"
    folder.mkdir()
    return ledgers.write(folder)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    found = Settings.in_directory(tmp_path / "data")
    db.initialise(found.database)
    feed.initialise(found.feed_database)
    return found


@pytest.fixture
def shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An admin's shell: a data directory, no HEARTH_LEDGER, and a clock that reads NOW."""
    data = tmp_path / "data"
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(data))
    monkeypatch.delenv(LEDGER_VARIABLE, raising=False)
    monkeypatch.setattr(cli, "system_clock", lambda: NOW)
    return data


def _tree(folder: Path) -> dict[str, tuple[int, int, bytes]]:
    """Every file under `folder`, with its mode, modification time and bytes."""
    return {
        str(path.relative_to(folder)): (
            path.stat().st_mode,
            path.stat().st_mtime_ns,
            path.read_bytes(),
        )
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def _shape(entries: tuple[Directive, ...], folder: Path | None = None) -> list[str]:
    """The entries as beancount prints them, with each document's file named relative to
    `folder`, the one the ledger was opened from, as its text names it."""
    shaped = []
    for entry in entries:
        if isinstance(entry, Document) and folder is not None:
            entry = entry._replace(filename=os.path.relpath(entry.filename, folder))
        shaped.append(printer.format_entry(entry))
    return shaped


def _imported(settings: Settings) -> base.Imported:
    found = base.current(settings.ledger_base)
    assert found is not None
    return found


# The check.


def test_the_check_says_what_the_ledger_holds_as_worked_out_by_hand(
    settings: Settings, source: Path
) -> None:
    check = import_ledger(settings, source, now=NOW).check

    assert check.files == len(ledgers.FILES)
    assert check.entries == ledgers.ENTRIES
    assert check.accounts == ledgers.ACCOUNT_COUNT
    assert (check.first, check.last) == (ledgers.FIRST, ledgers.LAST)
    assert check.assertions == ledgers.ASSERTIONS
    assert check.worth.complete
    assert check.worth.as_of == _today()
    assert check.worth.currency == "USD"
    assert check.worth.total == ledgers.NET_WORTH
    assert check.runs == ("beancount.plugins.implicit_prices",)


def test_the_command_prints_the_check_and_every_line_it_keeps_but_does_not_use(
    shell: Path, source: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["import-ledger", str(source)])

    lines = capsys.readouterr().out.splitlines()
    digest = base.digest(
        "main.beancount", {name: text.encode() for name, text in ledgers.FILES.items()}
    )
    assert lines == [
        f"Imported {source}, 5 files, as hearth's base.",
        "  Entries: 20, from Jan 1, 2026 to Jul 2, 2026",
        "  Accounts: 6",
        "  Balance assertions: 4, all of which pass",
        f"  Net worth today: 3,054.50 USD on {_said(_today())}, at the ledger's own prices",
        "  Beancount's own plugins it runs: beancount.plugins.implicit_prices",
        "Kept but not used, 5 lines:",
        *(f"  {line}" for line in ledgers.UNUSED),
        f"The base is in {shell.absolute() / 'ledger-base' / digest}, digest {digest}.",
        "Hearth reads the base from now on, never the original, which it did not change.",
    ]


def test_a_holding_the_ledger_has_no_price_for_leaves_net_worth_a_floor_that_says_so(
    settings: Settings, tmp_path: Path
) -> None:
    files = {**ledgers.FILES, "prices/zqx.beancount": ""}
    folder = tmp_path / "unpriced"
    folder.mkdir()
    files["main.beancount"] = files["main.beancount"].replace(
        'plugin "beancount.plugins.implicit_prices"\n', ""
    )

    outcome = import_ledger(settings, ledgers.write(folder, files), now=NOW)

    assert not outcome.check.worth.complete
    assert outcome.check.worth.known_total == ledgers.NET_WORTH - 10 * 110
    (worth,) = (line for line in describe(outcome) if "Net worth" in line)
    assert worth.startswith("  Net worth today: at least 1,954.50 USD on ")
    assert worth.endswith("without what it has no price for in USD: ZQX in Assets:Zq:Brokerage")


# The base.


def test_every_file_is_kept_byte_for_byte_at_its_own_path(settings: Settings, source: Path) -> None:
    import_ledger(settings, source, now=NOW)

    frozen = _imported(settings).base
    assert frozen.root == "main.beancount"
    assert frozen.files == tuple(sorted(ledgers.FILES))
    for name in ledgers.FILES:
        assert (frozen.directory / name).read_bytes() == (source.parent / name).read_bytes()
    assert b"\r\n" in (frozen.directory / "years/2026-2.beancount").read_bytes()
    # Only what the ledger reads is kept: not the plugin beside it, nor the document it names.
    assert sorted(str(p.relative_to(frozen.directory)) for p in frozen.directory.rglob("*.*")) == (
        sorted(ledgers.FILES)
    )


def test_the_manifest_says_what_was_imported_and_from_where(
    settings: Settings, source: Path
) -> None:
    import_ledger(settings, source, now=NOW)

    manifest = json.loads((settings.ledger_base / "manifest.json").read_text())
    assert manifest["schema"] == "hearth.base.v1"
    assert manifest["source"] == str(source)
    assert manifest["root"] == "main.beancount"
    assert manifest["imported_at"] == NOW.isoformat()
    assert manifest["rulings_through"] == 0
    assert [f["path"] for f in manifest["files"]] == sorted(ledgers.FILES)
    assert [f["bytes"] for f in manifest["files"]] == [
        len(ledgers.FILES[name].encode()) for name in sorted(ledgers.FILES)
    ]
    assert "books.beancount" in manifest["note"]


def test_a_ledger_whose_files_reach_above_its_folder_keeps_their_places(
    settings: Settings, tmp_path: Path
) -> None:
    """A ledger opened from a folder of its own that includes a sibling folder's file: the
    copy keeps both, at their places relative to the folder that holds them both."""
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "prices.beancount").write_text(ledgers.PRICES)
    folder = tmp_path / "household"
    folder.mkdir()
    files = {
        **ledgers.FILES,
        "accounts.beancount": ledgers.ACCOUNTS.replace(
            '"prices/zqx.beancount"', '"../../shared/prices.beancount"'
        ),
    }
    del files["prices/zqx.beancount"]
    nested = folder / "ledger"
    nested.mkdir()

    import_ledger(settings, ledgers.write(nested, files), now=NOW)

    frozen = _imported(settings).base
    assert frozen.root == "household/ledger/main.beancount"
    assert "shared/prices.beancount" in frozen.files
    assert len(books.load_entries(frozen)) == ledgers.ENTRIES


def test_the_digest_changes_with_any_byte_any_path_and_which_file_is_opened() -> None:
    files = {"main.beancount": b'include "a.beancount"\n', "a.beancount": b"; zq\n"}
    named = base.digest("main.beancount", files)

    for name, content in files.items():
        for at in range(len(content)):
            changed = bytearray(content)
            changed[at] ^= 0x01
            assert base.digest("main.beancount", {**files, name: bytes(changed)}) != named
        assert base.digest("main.beancount", {**files, name: content + b" "}) != named
    renamed = {"main.beancount": files["main.beancount"], "b.beancount": files["a.beancount"]}
    assert base.digest("main.beancount", renamed) != named
    assert base.digest("a.beancount", files) != named
    # Where one file ends and the next begins is part of what is named.
    moved = {"main.beancount": files["main.beancount"] + b";", "a.beancount": b" zq\n"}
    assert base.digest("main.beancount", moved) != named
    assert base.digest("main.beancount", dict(reversed(files.items()))) == named


def test_a_base_changed_on_disk_is_refused_never_read(settings: Settings, source: Path) -> None:
    import_ledger(settings, source, now=NOW)
    kept = _imported(settings).base.directory / "years/2026-1.beancount"
    kept.chmod(0o600)
    kept.write_bytes(kept.read_bytes().replace(b"45.50", b"45.51"))

    with pytest.raises(books.LedgerError, match="not as it was imported"):
        books.household_entries(settings)


# The books.


def test_books_from_the_base_are_the_books_of_the_same_ledger_inherited(
    world: World, clock: FakeClock
) -> None:
    """The invented household, rulings, connections and named accounts and all: imported, its
    books are exactly the books it had inheriting the same ledger through HEARTH_LEDGER."""
    inheriting = books.from_household(world.settings, rulings=rulings_in)(clock())
    owning = replace(world.settings, ledger=None)

    import_ledger(owning, world.ledger, now=clock())
    imported = books.from_household(owning, rulings=rulings_in)(clock())

    assert _shape(imported.entries) == _shape(inheriting.entries)
    assert replace(imported, entries=(), pile=()) == replace(inheriting, entries=(), pile=())
    assert [p.review_id for p in imported.pile] == [p.review_id for p in inheriting.pile]


def test_a_ledger_of_every_shape_computes_the_same_books_imported_or_inherited(
    tmp_path: Path, source: Path
) -> None:
    now = datetime(2026, 11, 22, 9, tzinfo=UTC)
    inheriting = Settings.in_directory(tmp_path / "inheriting", ledger=source)
    owning = Settings.in_directory(tmp_path / "owning")
    for each in (inheriting, owning):
        db.initialise(each.database)
        feed.initialise(each.feed_database)
    import_ledger(owning, source, now=now)

    theirs = books.from_household(inheriting, rulings=rulings_in)(now)
    ours = books.from_household(owning, rulings=rulings_in)(now)

    assert _shape(ours.entries, _imported(owning).base.root_path.parent) == _shape(
        theirs.entries, source.parent
    )
    assert replace(ours, entries=()) == replace(theirs, entries=())
    assert ours.today.known_total == ledgers.NET_WORTH - 10 * 110  # no stored price for ZQX


def test_the_books_file_includes_the_base_and_says_it_is_the_imported_one(
    settings: Settings, source: Path
) -> None:
    import_ledger(settings, source, now=NOW)

    books.from_household(settings, rulings=rulings_in)(NOW)

    written = settings.books_file.read_text()
    assert "; Imported: the household's ledger as it was imported, frozen" in written
    root = _imported(settings).base.root_path.resolve()
    assert f'include "{root}"' in written
    assert str(source) not in written
    entries, errors, _ = loader._load([(str(settings.books_file), True)], None, None, None)
    # Beancount opens it: the document's file is not in the base, the one complaint.
    assert entries
    assert [type(e).__name__ for e in errors] == ["DocumentError"]


def test_books_given_both_a_base_and_hearth_ledger_are_refused_saying_why(
    settings: Settings, source: Path
) -> None:
    import_ledger(settings, source, now=NOW)

    with pytest.raises(books.LedgerError) as refused:
        books.household_entries(replace(settings, ledger=source))

    assert refused.value.summary == books.BOTH


def test_standing_notices_an_import_made_by_another_process(
    settings: Settings, source: Path, tmp_path: Path
) -> None:
    before = books.standing(settings)

    import_ledger(settings, source, now=NOW)
    imported = books.standing(settings)
    changed = tmp_path / "changed"
    changed.mkdir()
    files = {**ledgers.FILES, "prices/zqx.beancount": "2026-07-31 price ZQX 120.00 USD\n"}
    import_ledger(settings, ledgers.write(changed, files), now=NOW)

    assert len({before, imported, books.standing(settings)}) == 3


def test_a_serving_hearth_takes_up_an_import_made_beside_it(
    settings: Settings, source: Path
) -> None:
    keeper = books.Bookkeeper(
        books.from_household(settings, rulings=rulings_in),
        lambda: NOW,
        standing=lambda: books.standing(settings),
    )
    keeper.recompute()
    assert keeper.status.books is not None
    assert keeper.status.books.entries == ()
    stop = keeper.recompute_on_change(every=0.01)
    try:
        import_ledger(settings, source, now=NOW)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            found = keeper.status.books
            if found is not None and found.entries:
                break
            threading.Event().wait(0.02)
    finally:
        stop.set()

    found = keeper.status.books
    assert found is not None
    assert len(found.entries) == ledgers.ENTRIES


# The plugin rule.


def test_beancounts_own_plugins_run_and_no_other_is_run_or_imported(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(sys.modules, "zq_meddler", raising=False)

    entries = books.load_entries(source)

    prices = [(e.date, e.amount.number) for e in entries if isinstance(e, Price)]
    assert ledgers.IMPLICIT_PRICE in prices
    assert "zq_meddler" not in sys.modules
    assert not (source.parent / "meddled").exists()
    assert not [e for e in entries if isinstance(e, Custom)]


def test_beancount_itself_would_run_the_plugin_hearth_keeps_inert(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fixture is no straw man: loaded as beancount loads it, the plugin runs."""
    monkeypatch.delitem(sys.modules, "zq_meddler", raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))

    loader._load([(str(source), True)], None, None, None)

    assert (source.parent / "meddled").read_text() == "ran"
    monkeypatch.delitem(sys.modules, "zq_meddler")


# Writing nothing.


def test_nothing_is_written_at_or_beside_the_ledger(
    settings: Settings, source: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "kept-before"
    before = _tree(outside)
    folder_before = sorted(p.name for p in tmp_path.iterdir())

    import_ledger(settings, source, now=NOW)
    books.from_household(settings, rulings=rulings_in)(NOW)

    assert _tree(outside) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == folder_before


def test_the_same_ledger_imported_again_writes_nothing(
    shell: Path, source: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["import-ledger", str(source)])
    kept = _tree(shell / "ledger-base")
    capsys.readouterr()

    main(["import-ledger", str(source)])

    said = capsys.readouterr().out.splitlines()
    assert (
        said[0]
        == f"{source} is hearth's base already, imported on {_said(_today())}: nothing was written."
    )
    assert _tree(shell / "ledger-base") == kept


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        (
            {"accounts.beancount": ledgers.ACCOUNTS.replace("prices/zqx", "prices/zqy")},
            "The ledger does not load cleanly. The ledger has 1 error hearth cannot read past.",
        ),
        (
            {
                "prices/zqx.beancount": ledgers.PRICES
                + '2026-06-30 note Assets:Zq:Checking "Caf\xe9 receipt"\n'
            },
            "The ledger does not load cleanly. The ledger has 1 error hearth cannot read past,"
            " the first at zqx.beancount, line 2.",
        ),
        (
            {
                "years/2026-2.beancount": ledgers.SECOND_HALF.replace(
                    "Checking  1954.50", "Checking  1954.49"
                )
            },
            "A balance assertion fails: on Jul 2, 2026, at 2026-2.beancount, line 6, the"
            " postings do not add up to what it asserts.",
        ),
        (
            {"main.beancount": ledgers.MAIN + "2026-13-01 open Assets:Zq:Typo\n"},
            "The ledger does not load cleanly. The ledger has 1 error hearth cannot read past,"
            " the first at main.beancount, line 10.",
        ),
    ],
    ids=["a missing include", "an include not in UTF-8", "a failing assertion", "a typo"],
)
def test_a_ledger_that_does_not_load_cleanly_is_refused_and_nothing_is_written(
    shell: Path, tmp_path: Path, change: dict[str, str], refusal: str
) -> None:
    folder = tmp_path / "broken"
    folder.mkdir()
    ledger = ledgers.write(folder, {**ledgers.FILES, **change})
    if "prices/zqx.beancount" in change:
        # Written as a ledger kept on an old Windows machine would have it: in Latin-1.
        prices = folder / "prices/zqx.beancount"
        prices.write_bytes(change["prices/zqx.beancount"].encode("latin-1"))
    before = _tree(folder)

    with pytest.raises(SystemExit) as refused:
        main(["import-ledger", str(ledger)])

    assert refused.value.code == f"hearth: {refusal} Nothing was imported."
    assert not (shell / "ledger-base").exists()
    assert not (shell / "hearth.sqlite3").exists()
    assert _tree(folder) == before


def test_an_include_by_absolute_path_is_refused_as_one_a_copy_would_still_read(
    shell: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "absolute"
    folder.mkdir()
    prices = folder / "prices/zqx.beancount"
    files = {
        **ledgers.FILES,
        "accounts.beancount": ledgers.ACCOUNTS.replace('"prices/zqx.beancount"', f'"{prices}"'),
    }
    ledger = ledgers.write(folder, files)

    with pytest.raises(SystemExit) as refused:
        main(["import-ledger", str(ledger)])

    assert refused.value.code == (
        f"hearth: accounts.beancount, line 9, includes {prices} by its absolute path, which a"
        " frozen copy would still read from where it is. Make it relative to the file that"
        " includes it, and import again. Nothing was imported."
    )
    assert sorted(p.name for p in (shell / "ledger-base").iterdir()) == []


def test_a_ledger_that_is_not_there_is_refused(shell: Path, tmp_path: Path) -> None:
    missing = tmp_path / "nowhere.beancount"

    with pytest.raises(SystemExit) as refused:
        main(["import-ledger", str(missing)])

    assert refused.value.code == (
        f"hearth: {missing} cannot be read: No such file or directory. Nothing was imported."
    )


def test_importing_while_hearth_ledger_is_set_is_refused(
    shell: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LEDGER_VARIABLE, str(source))

    with pytest.raises(SystemExit, match="HEARTH_LEDGER is set") as refused:
        main(["import-ledger", str(source)])

    assert str(refused.value.code).endswith("Nothing was imported.")
    assert not (shell / "ledger-base").exists()


# Importing again.


def _changed(tmp_path: Path, name: str) -> Path:
    folder = tmp_path / name
    folder.mkdir()
    files = {**ledgers.FILES, "prices/zqx.beancount": "2026-07-31 price ZQX 120.00 USD\n"}
    return ledgers.write(folder, files)


def test_importing_again_before_any_ruling_replaces_the_base_and_keeps_the_old_one(
    settings: Settings, source: Path, tmp_path: Path
) -> None:
    first = import_ledger(settings, source, now=NOW).imported

    again = import_ledger(settings, _changed(tmp_path, "changed"), now=NOW)

    assert again.replaced == first
    assert _imported(settings) == again.imported
    assert again.imported.base.digest != first.base.digest
    assert first.base.directory.is_dir()
    first.base.verify()
    assert again.check.worth.total == ledgers.NET_WORTH + 10 * 10
    assert describe(again)[-2] == (
        f"It replaces the base imported on {_said(_today())}, which stays in"
        f" {first.base.directory}."
    )


def test_importing_again_after_a_ruling_is_refused_saying_why(
    shell: Path, source: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["add-member", "avery", "--name", "Avery", "--admin"])
    main(["import-ledger", str(source)])
    kept = _tree(shell / "ledger-base")
    conn = db.connect(shell / "hearth.sqlite3")
    try:
        avery = members.find(conn, "avery")
        assert avery is not None
        RulingStore(conn).record(
            member_id=avery.id,
            scope=Scope.ROW,
            group=None,
            action=Action.REJECT,
            account=None,
            note=None,
            rows={"zq-row": None},
            says={"zq-row": "zq-says"},
            at=NOW,
        )
    finally:
        conn.close()

    with pytest.raises(SystemExit) as refused:
        main(["import-ledger", str(_changed(tmp_path, "changed"))])

    assert refused.value.code == (
        f"hearth: The ledger imported on {_said(_today())} is not replaced: 1 review ruling"
        " has been made since, and rulings rest on the ledger as it was imported. Nothing was"
        " imported."
    )
    assert _tree(shell / "ledger-base") == kept


def test_rulings_made_before_the_first_import_do_not_hold_it_back(
    world: World, clock: FakeClock, tmp_path: Path
) -> None:
    """The invented household has rulings from before it imported anything: they were made on
    the ledger it inherited, which is the ledger it imports, so the import and a second one
    before any new ruling go ahead."""
    owning = replace(world.settings, ledger=None)

    first = import_ledger(owning, world.ledger, now=clock())
    changed = tmp_path / "changed.beancount"
    changed.write_text(world.ledger.read_text() + "\n; a comment\n")
    again = import_ledger(owning, changed, now=clock())

    assert first.imported.rulings_through > 0
    assert again.replaced == first.imported


def test_a_refused_import_names_no_amount_and_no_payee(shell: Path, tmp_path: Path) -> None:
    folder = tmp_path / "failing"
    folder.mkdir()
    files = {
        **ledgers.FILES,
        "years/2026-2.beancount": ledgers.SECOND_HALF.replace("1954.50", "1954.49"),
    }

    with pytest.raises(SystemExit) as refused:
        main(["import-ledger", str(ledgers.write(folder, files))])

    said = str(refused.value.code)
    for secret in ("1954", "1,954", "Zq Card Co", "Zq Grocer", "Assets:Zq:Checking"):
        assert secret not in said


@pytest.fixture(params=["Pacific/Kiritimati", "UTC", "Pacific/Pago_Pago"])
def zone(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Hearth's machine at UTC+14, in Greenwich, and at UTC-11, where NOW is two days."""
    monkeypatch.setenv("TZ", request.param)
    time.tzset()
    yield request.param
    monkeypatch.undo()
    time.tzset()


LOCAL_DAY = {
    "Pacific/Kiritimati": date(2026, 11, 23),
    "UTC": date(2026, 11, 22),
    "Pacific/Pago_Pago": date(2026, 11, 22),
}


def test_the_import_dates_everything_by_the_households_own_day_in_every_zone(
    zone: str, shell: Path, source: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Today, and the day a base was imported, are the clock's moment on the household's own
    calendar, as every figure hearth shows is: worked out here by hand for each zone."""
    day = LOCAL_DAY[zone]
    assert _today() == day

    main(["import-ledger", str(source)])
    first = capsys.readouterr().out.splitlines()
    main(["import-ledger", str(_changed(tmp_path, "changed"))])
    again = capsys.readouterr().out

    assert f"  Net worth today: 3,054.50 USD on {_said(day)}, at the ledger's own prices" in first
    assert f"It replaces the base imported on {_said(day)}, which stays in" in again
    manifest = json.loads((shell / "ledger-base" / "manifest.json").read_text())
    assert datetime.fromisoformat(manifest["imported_at"]) == NOW
