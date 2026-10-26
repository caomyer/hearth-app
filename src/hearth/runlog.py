"""The runtime log hearth writes to its data directory: what it asked of Plaid, and how it went.

``hearth.log`` holds each Plaid call's path, environment and outcome.
An outcome names Plaid's error type and code, the HTTP status, and Plaid's request id.
It holds each new connection, linked or adopted, by member id, institution and Item id.
It holds each return from a bank's own sign-in.
It holds each time Link closed without connecting, with Plaid's codes and the step reached.
Such a line also names the institution and carries Plaid's identifiers.
It never holds a request or response body, nor an access, public or Link token.
It never holds the client id or secret, a balance, an amount, an account number or name, a payee.
The Plaid client logs only the path it called, the status and two identifiers Plaid sent back.
Every value from a browser is checked against Plaid's shape for it before it is written.
``tests/plaid/test_runlog.py`` holds the log to all of this.
It is owner-only (0600, rotated files included) inside the 0700 data directory.
That is acceptable: the same directory holds ``books.beancount``, written 0600.
That file has every transaction the household has, with payees and amounts.
So a log naming institutions, member ids and Item ids is strictly less sensitive than it.
It adds nothing a reader of that directory could not already have.
``docs/connections.md`` says what is in it.
"""

import io
import logging
import logging.handlers
import os
import re
from pathlib import Path

LOG_FILE = "hearth.log"
_MAX_BYTES = 1024 * 1024
_KEPT = 3

_handlers: dict[Path, logging.Handler] = {}

_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_STATUS = re.compile(r"[a-z][a-z_]{0,63}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_NAME_TOO_LONG = 80


class _OwnerOnly(logging.handlers.RotatingFileHandler):
    """Opens every file it writes, the first and each after a rollover, readable only by its
    owner, whatever the umask; a rollover renames, so the kept files stay owner-only too."""

    def _open(self) -> io.TextIOWrapper:
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        return os.fdopen(fd, "a", encoding=self.encoding, errors=self.errors)


def configure(data_dir: Path) -> Path:
    """Send hearth's own log records to ``hearth.log`` in `data_dir`, readable only by the user
    hearth runs as. Configuring the same directory again changes nothing."""
    path = data_dir / LOG_FILE
    if path in _handlers:
        return path
    handler = _OwnerOnly(path, maxBytes=_MAX_BYTES, backupCount=_KEPT, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger = logging.getLogger("hearth")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    _handlers[path] = handler
    return path


def stop(data_dir: Path) -> None:
    """Stop writing to `data_dir`'s log, as when a throwaway directory is about to go."""
    handler = _handlers.pop(data_dir / LOG_FILE, None)
    if handler is not None:
        logging.getLogger("hearth").removeHandler(handler)
        handler.close()


def code(value: object) -> str | None:
    """A Plaid error type or code, such as ``ITEM_LOGIN_REQUIRED``, or None for anything else."""
    return value if isinstance(value, str) and _CODE.fullmatch(value) else None


def status(value: object) -> str | None:
    """A Plaid Link status or event name in lower case, such as ``requires_oauth``."""
    return value if isinstance(value, str) and _STATUS.fullmatch(value) else None


def identifier(value: object) -> str | None:
    """An opaque identifier Plaid issues, such as a request id or an institution id."""
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def name(value: object) -> str | None:
    """An institution's name as Link reports it, on one line and of a bounded length."""
    if not isinstance(value, str):
        return None
    printable = "".join(c for c in value if c.isprintable()).strip()
    return printable[:_NAME_TOO_LONG] or None
