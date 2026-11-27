"""Bringing a ledger in for the app, in the background, as a sync is (``hearth.refresh``).

The import itself is ``hearth.ledger_import``'s, asked through the member's own view
(``hearth.disclosure.ledger``); this only runs it off the wire, one at a time, and then
recomputes the books through the one recompute path, so an import the app is told ended is
one the books hold, or say why they do not. The ``ledger`` screen follows it.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from hearth.books import Bookkeeper
from hearth.clock import Clock
from hearth.disclosure.ledger import BroughtIn

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Importing:
    """Where the last import this bridge started stands, and whose it was."""

    by: str
    """The username of the member who started it, who alone is told of it."""
    running: bool
    done: BroughtIn | None
    finished_at: datetime | None
    books_failed: bool
    """Whether the recompute after it failed, so the books do not hold what it brought in."""


class Importer:
    """One bridge's imports: one at a time, each in the background."""

    def __init__(self, bookkeeper: Bookkeeper, clock: Clock) -> None:
        self._bookkeeper = bookkeeper
        self._clock = clock
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last: Importing | None = None

    @property
    def status(self) -> Importing | None:
        with self._lock:
            return self._last

    @property
    def running(self) -> bool:
        """Whether an import is running now, whoever started it."""
        with self._lock:
            return self._thread is not None

    def start(self, by: str, work: Callable[[], BroughtIn]) -> bool:
        """Run `work` in the background for `by`, unless an import is running already; False
        when one is."""
        with self._lock:
            if self._thread is not None:
                return False
            self._last = Importing(by, True, None, None, books_failed=False)
            self._thread = threading.Thread(
                target=self._run, args=(by, work), name="hearth-import", daemon=True
            )
            self._thread.start()
        return True

    def wait(self, timeout: float | None = None) -> None:
        """Return once the import running now, if any, has ended."""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _run(self, by: str, work: Callable[[], BroughtIn]) -> None:
        done = BroughtIn("failed")
        failed = False
        try:
            done = work()
            if done.outcome == "imported":
                self._bookkeeper.recompute(wait=True)
                failed = self._bookkeeper.status.failure is not None
        except Exception:
            _log.exception("bringing a ledger in stopped before its recompute")
        finally:
            with self._lock:
                self._thread = None
                self._last = Importing(by, False, done, self._clock(), books_failed=failed)
