"""Working out a slow answer off the wire, so the app's other asks keep being answered.

The bridge answers one line at a time. Reading a ledger file to check it, or weighing every
connected account against an imported ledger, can take seconds on a real household's books,
and the app's other screens would wait behind it. A screen marked slow is worked out here, in a
thread of its own: the first ask starts it and is answered ``loading``, and the app asks again
until the answer is there, which is handed out once, so the next ask works it out afresh. A
piece of work that never ends, a file that never finishes reading, holds only its own thread.

Each piece of work belongs to a group, one viewer's asks of one screen. A new piece of work in
a group supersedes the one before it, which is asked to stop (``hearth.stopping``), and starts
only once that one has ended: so one viewer never has two checks of a ledger running at once,
however often the app asks while things change. Letting go of a viewer's work (`forget`) asks
it to stop too.
"""

import logging
import threading
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field

from hearth import stopping
from hearth.native.common import MISSING, Answer

_log = logging.getLogger(__name__)

MOST = 32
"""The most pieces of work held at once; past it, the oldest finished ones nobody came back for
are let go."""


@dataclass(slots=True)
class _Job:
    group: tuple[str, str]
    after: _Job | None = None
    """The piece of work in its group before it, which ends before this one starts."""
    result: Answer | None = None
    done: threading.Event = field(default_factory=threading.Event)
    stop: threading.Event = field(default_factory=threading.Event)


class Worker:
    """One bridge's slow answers, each worked out once in a thread of its own."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[Hashable, _Job] = {}
        self._latest: dict[tuple[str, str], _Job] = {}

    def answer(
        self, key: Hashable, work: Callable[[], Answer], group: tuple[str, str]
    ) -> Answer | None:
        """The answer `work` gives for `key`, once it is worked out, or None while it is: the
        first ask starts it, superseding the work of `group`, the viewer and the screen, under
        any other key. A finished answer is handed out once."""
        with self._lock:
            job = self._jobs.get(key)
            if job is not None and job.done.is_set():
                del self._jobs[key]
                return job.result
            if job is not None:
                return None
            self._stop(lambda other: other.group == group)
            self._forget()
            job = _Job(group, after=self._latest.get(group))
            self._latest[group] = job
            self._jobs[key] = job
        threading.Thread(target=self._run, args=(job, work), name="hearth-ask", daemon=True).start()
        return None

    def forget(self, viewer: str) -> None:
        """Let go of every answer worked out for `viewer`, finished or not, asking any still
        being worked out to stop, as an act of theirs may have changed what it says."""
        with self._lock:
            self._stop(lambda job: job.group[0] == viewer)

    def wait(self, key: Hashable, timeout: float | None = None) -> None:
        """Return once the work for `key`, if any, has ended."""
        with self._lock:
            job = self._jobs.get(key)
        if job is not None:
            job.done.wait(timeout)

    def _stop(self, which: Callable[[_Job], bool]) -> None:
        for key in [k for k, job in self._jobs.items() if which(job)]:
            self._jobs.pop(key).stop.set()

    def _run(self, job: _Job, work: Callable[[], Answer]) -> None:
        try:
            if job.after is not None:
                job.after.done.wait()
                job.after = None
            if job.stop.is_set():
                job.result = dict(MISSING)
                return
            with stopping.stoppable(job.stop):
                job.result = work()
        except stopping.Stopped:
            job.result = dict(MISSING)
        except Exception:
            _log.exception("a slow answer stopped on an unexpected error")
            job.result = dict(MISSING)
        finally:
            job.done.set()
            with self._lock:
                if self._latest.get(job.group) is job:
                    del self._latest[job.group]

    def _forget(self) -> None:
        if len(self._jobs) < MOST:
            return
        for key in [k for k, job in self._jobs.items() if job.done.is_set()]:
            del self._jobs[key]
            if len(self._jobs) < MOST:
                return
