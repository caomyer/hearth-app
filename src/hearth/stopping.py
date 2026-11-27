"""Asking work in progress to stop, from another thread.

A slow answer the Mac app no longer wants (``hearth.native.worker``), one superseded by
another or let go after the viewer acted, is asked to stop: the thread working it out runs
`stoppable` around it, and work that can stop part way, a check of a ledger file in a process
of its own (``hearth.ledger_child``), asks `asked` as it waits and raises `Stopped`.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_asked: ContextVar[threading.Event | None] = ContextVar("hearth_stopping", default=None)


class Stopped(Exception):  # noqa: N818 - it names an outcome
    """The work was asked to stop, and did, giving no answer."""


@contextmanager
def stoppable(event: threading.Event) -> Iterator[None]:
    """Run the block as work `event` asks to stop once it is set."""
    token = _asked.set(event)
    try:
        yield
    finally:
        _asked.reset(token)


def asked() -> bool:
    """Whether the work this thread is doing has been asked to stop."""
    event = _asked.get()
    return event is not None and event.is_set()
