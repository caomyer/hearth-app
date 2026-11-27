"""Reading a ledger file in a process of its own, for the Mac app's engine.

Beancount's parser is C that holds Python's interpreter lock while it parses, so a file read in
a thread of the bridge still stops every other ask the app makes until it is done, and a
file chosen by mistake can take minutes. The app's check of a ledger, and its import, run here
instead: in a child process of the same Python, given what to do on its standard input and
answering on its standard output, killed if reading takes longer than `LIMIT`, and refused then
in words of its own (``ledger_import.Refusal.TOO_SLOW``). A check the app no longer wants, as
one a newer ask superseded or the viewer's act let go (``hearth.native.worker``), is killed too
(``hearth.stopping``).

The child does exactly what ``hearth.ledger_import`` does in the bridge's own process, nothing
else: it is handed the data directory and the path, inherits no approval for Plaid (it makes no
call of any kind) and only the environment a process needs, and writes nowhere but the folder
its caller made and holds for the copy it loads (``base.checking``), which the caller takes away
however the child ends. An import is handed the base's lock, which its caller took first, so
the time it waits on another import is never counted against the time it may read; killed, its
caller sweeps what it staged under the lock (``base.sweep``).

The child never outlives the bridge. It is handed one end of a pipe whose other end only the
bridge holds, and a guard forked from the child before it reads the ledger watches that pipe:
the moment the bridge ends, however it ends, the pipe closes, and the guard kills the child,
takes away the folder it was staging in, and goes. A guard, not a thread of the child's,
because the parser holds the child's interpreter lock while it parses. A folder left all the
same is held by nobody, and swept by the next check, import or start (``base.sweep_checks``).

The command line imports in its own process already, and does it there.
"""

import contextlib
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from hearth import base, ledger_import, stopping
from hearth.config import Settings
from hearth.ledger_import import ImportRefused, Refusal, Summary

LIMIT = 120.0
"""How many seconds a check or an import of a ledger may read before it is stopped: many times
what a household's ledger of ten years takes, and what any file within the caps takes
(``ledger_import.LARGEST``, ``ledger_import.LONGEST_LINE``)."""

STEP = 0.1
"""How often, in seconds, the bridge looks whether a child it waits on is still wanted."""

_KEPT_ENVIRONMENT = ("HOME", "TMPDIR", "LANG", "LC_ALL", "TZ")
"""What the child is given of the bridge's environment: where it may write a temporary file,
how text is read, and the zone, so the household's own day is the same in both ("today" is the
local day, ``hearth.clock``)."""

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
    """Check the ledger opened from `source` in a process of its own (``check_ledger``).
    Raises ``stopping.Stopped``, having killed it, once the check is no longer wanted."""
    with base.checking(settings.data_dir) as (staging, holding):
        answer = _run(
            settings,
            {"do": "check", "path": str(source), "staging": str(staging)},
            now,
            limit,
            handed=(holding,),
        )
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
    (``import_ledger``). The base's lock is waited on here (``base.WAIT``), before the child
    starts, and handed to it, so `limit` times the reading alone."""
    try:
        with ledger_import.locked(settings) as holding:
            answer = _run(
                settings,
                {"do": "import", "path": str(source), "held": True},
                now,
                limit,
                handed=(holding,),
            )
            if isinstance(answer, ImportRefused):
                # Killed while it held the lock with this process: what it staged goes now.
                base.sweep(settings.data_dir)
    except ImportRefused as waited:
        return Done("refused", waited)
    if isinstance(answer, ImportRefused):
        return Done("refused", answer)
    refused = _refusal(answer.get("refused"))
    return Done("refused", refused) if refused is not None else Done(str(answer["outcome"]))


def _run(
    settings: Settings,
    asked: dict[str, object],
    now: datetime,
    limit: float | None,
    *,
    handed: tuple[int, ...] = (),
) -> dict[str, Any] | ImportRefused:
    """Run the child on `asked` and read its answer, handing it the descriptors in `handed`,
    the holds its caller took, and the end of a pipe whose other end only this process holds.
    Killed, with its guard, past `limit` or once no longer wanted (``hearth.stopping``)."""
    limit = LIMIT if limit is None else limit
    paths = [p for p in sys.path if p and Path(p).is_dir()]
    environment = {k: os.environ[k] for k in _KEPT_ENVIRONMENT if k in os.environ}
    lifeline, kept_here = os.pipe()
    try:
        child = subprocess.Popen(  # noqa: S603 - this very Python, running hearth's own code
            [sys.executable, "-I", "-B", "-c", _START, json.dumps(paths)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            start_new_session=True,
            pass_fds=(lifeline, *handed),
        )
    except BaseException:
        os.close(kept_here)
        raise
    finally:
        os.close(lifeline)
    try:
        sent = {**_request(settings, asked, now), "lifeline": lifeline}
        request: bytes | None = json.dumps(sent).encode()
        deadline = time.monotonic() + limit
        while True:
            try:
                out, _ = child.communicate(
                    request, timeout=max(0.0, min(STEP, deadline - time.monotonic()))
                )
                break
            except subprocess.TimeoutExpired:
                request = None  # sent, or being sent: a later call goes on with it
                if stopping.asked():
                    _kill(child)
                    raise stopping.Stopped from None
                if time.monotonic() >= deadline:
                    _kill(child)
                    return _too_slow(limit)
    finally:
        os.close(kept_here)
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


def _kill(child: subprocess.Popen[bytes]) -> None:
    """Kill the child and its guard, the whole session it leads, and wait until it is gone."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(child.pid, signal.SIGKILL)
    child.communicate()


def _too_slow(limit: float) -> ImportRefused:
    return ImportRefused(
        f"Reading the ledger took longer than {int(limit)} seconds, so it was stopped.",
        Refusal.TOO_SLOW,
        "Reading it took too long, so Hearth stopped. A ledger is quick to read, so this"
        " is likely not one.",
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
    """The child: do what standard input asks, and answer on standard output, guarded so it
    ends with the bridge (`_guarded`)."""
    asked = json.loads(sys.stdin.read())
    _guarded(asked)
    sys.stdout.write(json.dumps(answer(asked)))


def _guarded(asked: dict[str, Any]) -> None:
    """Fork the guard that kills this process if the bridge ends before it does."""
    lifeline = asked.get("lifeline")
    if not isinstance(lifeline, int):
        return
    gone, alive = os.pipe()
    if os.fork() == 0:
        try:
            os.close(alive)
            _guard(lifeline, gone, os.getppid(), asked)
        finally:
            os._exit(0)
    os.close(gone)
    os.close(lifeline)
    # `alive` stays open as long as this process lives: the guard sees its end close.


def _guard(lifeline: int, gone: int, child: int, asked: dict[str, Any]) -> None:
    """The guard: wait until the child ends, and go; or until the bridge ends first, then kill
    the child, take away what it was staging, and go."""
    quiet = os.open(os.devnull, os.O_RDWR)
    for descriptor in (0, 1, 2):  # never hold the child's answer open
        os.dup2(quiet, descriptor)
    ready, _, _ = select.select([lifeline, gone], [], [])
    if gone in ready:
        return
    with contextlib.suppress(ProcessLookupError):
        os.kill(child, signal.SIGKILL)
    select.select([gone], [], [], 5.0)  # until the child is gone
    staging = asked.get("staging")
    if isinstance(staging, str):
        shutil.rmtree(staging, ignore_errors=True)
    if asked.get("do") == "import":
        # The guard holds the base's lock the bridge handed down, so nothing stages meanwhile.
        base.sweep(Path(asked["data_dir"]))


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
        # The bridge took the base's lock first, and handed it to this process.
        held = asked.get("held") is True
        try:
            done = ledger_import.import_ledger(settings, source, now=now, once=True, held=held)
        except ImportRefused as refused:
            answer = {"refused": _said(refused)}
        else:
            answer = {"outcome": "unchanged" if done.unchanged else "imported"}
    return answer
