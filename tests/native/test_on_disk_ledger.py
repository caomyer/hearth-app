"""The household the Mac app's end-to-end test brings a ledger into (``tests.native.on_disk``),
read as ``hearth bridge`` reads it: its ledger checked, brought in, and every account matched,
it is worth what the test expects, and nothing waits in Review that did not before."""

from pathlib import Path
from typing import Any

from hearth import books
from hearth.disclosure import may_learn, rulings_in
from hearth.native.bridge import Acted, Bridge, Outcome
from hearth.native.importer import Importer
from tests.conftest import FakeClock
from tests.native import on_disk
from tests.native.test_ledger import settled


def test_the_on_disk_household_brings_its_ledger_in_and_matches_every_account(
    tmp_path: Path, clock: FakeClock
) -> None:
    settings = on_disk.build(tmp_path / "data", "fixture-passphrase", now=clock(), history=True)
    ledger = on_disk.write_ledger(settings, tmp_path / "kept")
    keeper = books.Bookkeeper(
        books.from_household(settings, rulings=rulings_in, learns=may_learn), clock
    )
    keeper.recompute(wait=True)
    importer = Importer(keeper, clock)
    door = Bridge(settings, keeper, clock, importer=importer)
    assert door.sign_in("avery", "fixture-passphrase") is Outcome.SIGNED_IN

    checked = settled(door, "ledger_check", {"path": str(ledger)})
    assert checked["refused"] is None
    assert checked["found"]["files"] == 2
    assert checked["found"]["unused_kinds"] == {"plugins": 1, "custom": 1, "documents": 0}
    assert door.act("bring_in_ledger", {"path": str(ledger)}) is Acted.STARTED
    importer.wait()
    standing: dict[str, Any] = door.answer("ledger")
    assert standing["bringing_in"]["outcome"] == "imported"

    matching = settled(door, "matching")
    proposed = {a["name"]: a["proposed"]["account"] for a in matching["accounts"]}
    assert proposed == {
        name: on_disk.LEDGER_ACCOUNTS_BEFORE[mask]
        for name, mask in (
            ("Everyday checking", "4471"),
            ("Savings", "9035"),
            ("Rewards card", "6620"),
        )
    }
    for account in matching["accounts"]:
        assert account["state"] == "kept"
        first = account["choices"][0]
        assert first["proposed"] is True
        assert first["refused"] is None, first
        done = door.act("match_accounts", {"matches": {account["id"]: first["account"]}})
        assert done is Acted.MATCHED
    keeper.recompute(wait=True)
    assert keeper.status.failure is None, keeper.status.failure

    after = settled(door, "matching")
    assert after["totals"]["matched"] == 3
    overview: dict[str, Any] = door.answer("overview")
    assert overview["net_worth"]["value"] == format(on_disk.NET_WORTH, "f")
    assert door.answer("ledger")["imported"]["unclaimed"] == 1  # type: ignore[index]
