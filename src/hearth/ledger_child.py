"""Reading a ledger file in a process of its own, for the Mac app's engine.

Beancount's parser is C that holds Python's interpreter lock while it parses, so a file read in
a thread of the bridge still stops every other ask the app makes until it is done, and a
file chosen by mistake can take minutes. The app's check of a ledger, and its import, run here
instead: in a child process of the same Python, given what to do on its standard input and
answering on its standard output, killed if it takes longer than `LIMIT`, and refused then in
words of its own (``ledger_import.Refusal.TOO_SLOW``).

The child does exactly what ``hearth.ledger_import`` does in the bridge's own process, nothing
else: it is handed the data directory and the path, inherits no approval for Plaid (it makes no
call of any kind) and only the environment a process needs, and writes nowhere but the folder
its caller made for the copy it loads, which the caller takes away however the child ends. An
import holds the base's lock in the child; killed, it lets the lock go, and the caller sweeps
what it staged under the lock (``base.sweep``).

The command line imports in its own process already, and does it there.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from hearth import base, ledger_import
from hearth.config import Settings
from hearth.ledger_import import ImportRefused, Refusal, Summary

LIMIT = 120.0
"""How many seconds a check or an import of a ledger may take before it is stopped: many times
what a household's ledger of ten years takes."""

_KEPT_ENVIRONMENT = ("HOME", "TMPDIR", "LANG", "LC_ALL")

_START = (
    "import json, sys\n"
    "sys.path[:0] = json.loads(sys.argv[1])\n"
    "from hearth.ledger_child import main\n"
    "main()\n"
)


@dataclass(frozen=True, slots=True)
class Reported:
    """What a check found, if the file loads, and why it would not be brought in, if not."""

    found: Summary | None
    refused: ImportRefused | None


@dataclass(frozen=True, slots=True)
class Done:
    """How an import ended: `imported` or `unchanged`, or refused, why."""

    outcome: str
    refused: ImportRefused | None = None


def check(
    settings: Settings, source: Path, *, now: datetime, limit: float | None = None
) -> Reported:
    """Check the ledger opened from `source` in a process of its own (``check_ledger``)."""
    staging = Path(tempfile.mkdtemp(prefix=base.CHECKING, dir=settings.data_dir))
    try:
        answer = _run(
            settings, {"do": "check", "path": str(source), "staging": str(staging)}, now, limit
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if isinstance(answer, ImportRefused):
        return Reported(None, answer)
    found = answer.get("found")
    return Reported(
        Summary.unwire(found) if found is not None else None, _refusal(answer.get("refused"))
    )


def bring_in(
    settings: Settings, source: Path, *, now: datetime, limit: float | None = None
) -> Done:
    """Import the ledger opened from `source`, once, in a process of its own
    (``import_ledger``)."""
    answer = _run(settings, {"do": "import", "path": str(source)}, now, limit)
    if isinstance(answer, ImportRefused):
        if answer.why is Refusal.TOO_SLOW:
            # Killed, it let the lock go; what it staged is taken away under the lock.
            try:
                with base.locked(settings.ledger_base):
                    base.sweep(settings.data_dir)
            except base.LockTimedOut, base.LockUnavailable:
                pass
        return Done("refused", answer)
    refused = _refusal(answer.get("refused"))
    return Done("refused", refused) if refused is not None else Done(str(answer["outcome"]))


def _run(
    settings: Settings, asked: dict[str, object], now: datetime, limit: float | None
) -> dict[str, Any] | ImportRefused:
    limit = LIMIT if limit is None else limit
    request = _request(settings, asked, now)
    paths = [p for p in sys.path if p and Path(p).is_dir()]
    environment = {k: os.environ[k] for k in _KEPT_ENVIRONMENT if k in os.environ}
    child = subprocess.Popen(  # noqa: S603 - this very Python, running hearth's own code
        [sys.executable, "-I", "-B", "-c", _START, json.dumps(paths)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=environment,
        start_new_session=True,
    )
    try:
        out, _ = child.communicate(json.dumps(request).encode(), timeout=limit)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.communicate()
        return ImportRefused(
            f"Reading the ledger took longer than {int(limit)} seconds, so it was stopped.",
            Refusal.TOO_SLOW,
            "Reading it took too long, so Hearth stopped. A ledger is quick to read, so this"
            " is likely not one.",
        )
    try:
        answer = json.loads(out)
        assert isinstance(answer, dict)  # noqa: S101 - checked, as the child wrote it
        return answer
    except ValueError, AssertionError:
        return ImportRefused(
            "Reading the ledger stopped on an unexpected error.",
            Refusal.FAILED,
            "Hearth stopped while reading it, on an error of its own.",
        )


def _request(settings: Settings, asked: dict[str, object], now: datetime) -> dict[str, object]:
    """What the child is told: what to do, and the household it does it for."""
    return {
        **asked,
        "data_dir": str(settings.data_dir),
        "ledger": str(settings.ledger) if settings.ledger is not None else None,
        "now": now.isoformat(),
    }


def _refusal(said: dict[str, str] | None) -> ImportRefused | None:
    if said is None:
        return None
    return ImportRefused(said["said"], Refusal(said["why"]), said["plain"])


def _said(refused: ImportRefused | None) -> dict[str, str] | None:
    if refused is None:
        return None
    return {"why": refused.why.value, "said": str(refused), "plain": refused.plain}


def main() -> None:
    """The child: do what standard input asks, and answer on standard output."""
    sys.stdout.write(json.dumps(answer(json.loads(sys.stdin.read()))))


def answer(asked: dict[str, Any]) -> dict[str, object]:
    """What the child does with what it was asked, and the answer it writes."""
    ledger = asked["ledger"]
    settings = Settings.in_directory(
        Path(asked["data_dir"]), ledger=Path(ledger) if ledger is not None else None
    )
    now = datetime.fromisoformat(asked["now"])
    source = Path(asked["path"])
    answer: dict[str, object]
    if asked["do"] == "check":
        checked = ledger_import.check_ledger(
            settings, source, now=now, staging=Path(asked["staging"])
        )
        found = Summary.of(checked)
        answer = {
            "found": found.wire() if found is not None else None,
            "refused": _said(checked.refused),
        }
    else:
        try:
            done = ledger_import.import_ledger(settings, source, now=now, once=True)
        except ImportRefused as refused:
            answer = {"refused": _said(refused)}
        else:
            answer = {"outcome": "unchanged" if done.unchanged else "imported"}
    return answer
