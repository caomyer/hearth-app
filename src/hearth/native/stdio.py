"""The bridge's wire: one JSON object a line, in and out.

Each request carries a number and exactly one of four things:

- ``{"id": 1, "sign_in": {"username": "...", "password": "..."}}``, told
  ``{"id": 1, "outcome": "signed_in"}``, or ``rejected``, ``throttled`` or
  ``must_change_password`` (``hearth.native.bridge.Outcome``).
- ``{"id": 2, "sign_out": true}``, told ``{"id": 2, "outcome": "signed_out"}``.
- ``{"id": 3, "ask": "<screen>"}``, told ``{"id": 3, "answer": {...}}``, the doorway's answer
  (``hearth.native.doorway``), or ``{"state": "signed_out"}`` while nobody is signed in. An ask
  may also carry ``"with": {...}``, what narrows it, which the doorway reads. A ``with`` that
  is not an object, or one beside anything but an ask or an act, makes the line not a
  request.
- ``{"id": 4, "act": "sync"}``, told ``{"id": 4, "outcome": "started"}``, or ``running``,
  ``not_allowed``, ``missing`` or ``signed_out`` (``hearth.native.bridge.Acted``): asking
  hearth to do something rather than to show something. An act may carry ``"with": {...}``
  too, what it is done to, as a review decision does; a decision's reply also says which
  ruling an Undo would take back, and how many rows it decided and left waiting
  (``hearth.native.common.Decided``).

A line that is not such a request is answered ``{"id": null, "error": "bad request"}`` and the
bridge carries on: one malformed line must not take the engine down with the books in it. A
refusal never repeats the line, which may hold a password. An object that names a key twice is
not a request either, rather than one whose last word wins unseen.

A request that stops on an error hearth did not expect is answered as failed, an ask
``{"state": "failed", ...}`` and anything else ``"outcome": "failed"``, saying nothing of the
error, which is noted in ``hearth.log`` by its kind alone (``hearth.runlog``); and the bridge
carries on.

Nothing else may be written to the stream the replies go to, so everything hearth prints for a
person goes to standard error while a bridge runs.
"""

import json
import logging
from collections.abc import Callable, Iterable

from hearth import runlog
from hearth.native.bridge import Acted, Bridge

_log = logging.getLogger(__name__)

BAD_REQUEST = {"id": None, "error": "bad request"}

_KINDS = ("ask", "act", "sign_in", "sign_out")

FAILED_ANSWER = {
    "state": "failed",
    "summary": "Hearth stopped on an error of its own. Quit Hearth and open it again.",
}
"""What an ask that stopped on an unexpected error is answered: nothing of the error."""


class _RepeatedKeyError(ValueError):
    """An object named a key twice."""


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    found = dict(pairs)
    if len(found) != len(pairs):
        raise _RepeatedKeyError
    return found


def serve(bridge: Bridge, lines: Iterable[str], write: Callable[[str], object]) -> None:
    """Answer each line of `lines` with one line to `write`, until `lines` ends."""
    for line in lines:
        if not line.strip():
            continue
        write(json.dumps(reply(bridge, line), separators=(",", ":")) + "\n")


def reply(bridge: Bridge, line: str) -> dict[str, object]:
    try:
        request = json.loads(line, object_pairs_hook=_unique)
    except ValueError:
        # Not JSON (`JSONDecodeError` is a `ValueError`), or a key named twice.
        return dict(BAD_REQUEST)
    if not isinstance(request, dict):
        return dict(BAD_REQUEST)
    identity = request.get("id")
    # bool is an int in Python, and no request is numbered true.
    if not isinstance(identity, int) or isinstance(identity, bool):
        return dict(BAD_REQUEST)
    kinds = [kind for kind in _KINDS if kind in request]
    if len(kinds) != 1 or ("with" in request and kinds[0] not in {"ask", "act"}):
        return dict(BAD_REQUEST)
    try:
        return _answered(bridge, identity, kinds[0], request)
    except Exception as error:
        # Never the error's words, which may hold a figure or a name: its kind alone, as the
        # log takes an identifier, and what was asked, by its name if it is one.
        asked = request[kinds[0]] if kinds[0] in ("ask", "act") else kinds[0]
        # Not `exception`: a traceback would carry values.
        _log.error(
            "a request stopped on an unexpected error: %s, %s",
            runlog.identifier(type(error).__name__) or "unknown",
            runlog.identifier(asked) or "unnamed",
        )
        if kinds[0] == "ask":
            return {"id": identity, "answer": dict(FAILED_ANSWER)}
        return {"id": identity, "outcome": Acted.FAILED.value}


def _answered(
    bridge: Bridge, identity: int, kind: str, request: dict[str, object]
) -> dict[str, object]:
    match kind, request[kind], request.get("with", {}):
        case "ask", str(ask), dict(given):
            return {"id": identity, "answer": bridge.answer(ask, given)}
        case "act", str(action), dict(given):
            done = bridge.act(action, given)
            if isinstance(done, Acted):
                return {"id": identity, "outcome": done.value}
            return {"id": identity, **done.wire()}
        case "sign_in", {"username": str(username), "password": str(password)}, _:
            return {"id": identity, "outcome": bridge.sign_in(username, password).value}
        case "sign_out", True, _:
            return {"id": identity, "outcome": bridge.sign_out().value}
    return dict(BAD_REQUEST)
