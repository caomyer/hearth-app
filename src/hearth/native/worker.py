"""Working out a slow answer off the wire, so the app's other asks keep being answered.

The bridge answers one line at a time. Reading a ledger file to check it, or weighing every
connected account against an imported ledger, can take seconds on a real household's books,
and the app's other screens would wait behind it. A screen marked slow is worked out here, in a
thread of its own: the first ask starts it and is answered ``loading``, and the app asks again
until the answer is there, which is handed out once, so the next ask works it out afresh. A
piece of work that never ends, a file that never finishes reading, holds only its own thread.
"""

import logging
import threading
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field

from hearth.native.common import MISSING, Answer

_log = logging.getLogger(__name__)

MOST = 32
"""The most pieces of work held at once; past it, the oldest finished ones nobody came back for
are let go."""


@dataclass(slots=True)
class _Job:
    result: Answer | None = None
    done: threading.Event = field(default_factory=threading.Event)


class Worker:
    """One bridge's slow answers, each worked out once in a thread of its own."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[Hashable, _Job] = {}

    def answer(self, key: Hashable, work: Callable[[], Answer]) -> Answer | None:
        """The answer `work` gives for `key`, once it is worked out, or None while it is: the
        first ask starts it. A finished answer is handed out once."""
        with self._lock:
            job = self._jobs.get(key)
            if job is not None and job.done.is_set():
                del self._jobs[key]
                return job.result
            if job is not None:
                return None
            self._forget()
            job = _Job()
            self._jobs[key] = job
        threading.Thread(target=self._run, args=(job, work), name="hearth-ask", daemon=True).start()
        return None

    def forget(self, viewer: str) -> None:
        """Let go of every answer worked out for `viewer`, finished or not, as an act of theirs
        may have changed what it says (each key starts with the viewer's username)."""
        with self._lock:
            for key in [k for k in self._jobs if isinstance(k, tuple) and k[:1] == (viewer,)]:
                del self._jobs[key]

    def wait(self, key: Hashable, timeout: float | None = None) -> None:
        """Return once the work for `key`, if any, has ended."""
        with self._lock:
            job = self._jobs.get(key)
        if job is not None:
            job.done.wait(timeout)

    def _run(self, job: _Job, work: Callable[[], Answer]) -> None:
        try:
            job.result = work()
        except Exception:
            _log.exception("a slow answer stopped on an unexpected error")
            job.result = dict(MISSING)
        finally:
            job.done.set()

    def _forget(self) -> None:
        if len(self._jobs) < MOST:
            return
        for key in [k for k, job in self._jobs.items() if job.done.is_set()]:
            del self._jobs[key]
            if len(self._jobs) < MOST:
                return
