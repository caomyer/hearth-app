"""Which process holds a sync, so a lease can outlive nobody.

A sync of an Item holds a lease on it in the feed store, because two syncs of one Item at once
would page from the same cursor and undo each other's work. A lease that said only that
something holds the Item could not survive the process that took it: a `hearth serve` killed
mid-sync leaves its run looking held until the lease lapses. So a lease names its owner: the
process id, and the time the system says that process started, which tells a reused process
id apart from the process that took the lease.
"""

import functools
import os
import subprocess

PS = "/bin/ps"


def started(pid: int) -> str | None:
    """When the process `pid` started, as the system reports it; None if there is none."""
    result = subprocess.run(  # noqa: S603 - a fixed program, and a number
        [PS, "-o", "lstart=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
    )
    found = result.stdout.strip()
    return found or None


@functools.cache
def this_process() -> str:
    """This process, as a lease names its owner."""
    pid = os.getpid()
    return f"{pid}@{started(pid)}"


def is_alive(owner: str | None) -> bool:
    """Whether the process that took a lease is still running: the same id, started at the
    same time. A lease without an owner has none alive."""
    if owner is None:
        return False
    pid, _, since = owner.partition("@")
    return pid.isdigit() and started(int(pid)) == since
