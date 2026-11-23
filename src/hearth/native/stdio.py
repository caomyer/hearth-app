"""The bridge's wire: one JSON object a line, in and out.

Each request carries a number and exactly one of four things:

- ``{"id": 1, "sign_in": {"username": "...", "password": "..."}}``, told
  ``{"id": 1, "outcome": "signed_in"}``, or ``rejected``, ``throttled`` or
  ``must_change_password`` (``hearth.native.bridge.Outcome``).
- ``{"id": 2, "sign_out": true}``, told ``{"id": 2, "outcome": "signed_out"}``.
- ``{"id": 3, "ask": "<screen>"}``, told ``{"id": 3, "answer": {...}}``, the doorway's answer
  (``hearth.native.doorway``), or ``{"state": "signed_out"}`` while nobody is signed in. An ask
  may also carry ``"with": {...}``, what narrows it, which the doorway reads. A ``with`` that
  is not an object, or one beside anything but an ask, makes the line not a request.
- ``{"id": 4, "act": "sync"}``, told ``{"id": 4, "outcome": "started"}``, or ``running``,
  ``not_allowed``, ``missing`` or ``signed_out`` (``hearth.native.bridge.Acted``): asking
  hearth to do something rather than to show something. It takes nothing beside its name.

A line that is not such a request is answered ``{"id": null, "error": "bad request"}`` and the
bridge carries on: one malformed line must not take the engine down with the books in it. A
refusal never repeats the line, which may hold a password.

Nothing else may be written to the stream the replies go to, so everything hearth prints for a
person goes to standard error while a bridge runs.
"""

import json
from collections.abc import Callable, Iterable

from hearth.native.bridge import Bridge

BAD_REQUEST = {"id": None, "error": "bad request"}

_KINDS = ("ask", "act", "sign_in", "sign_out")


def serve(bridge: Bridge, lines: Iterable[str], write: Callable[[str], object]) -> None:
    """Answer each line of `lines` with one line to `write`, until `lines` ends."""
    for line in lines:
        if not line.strip():
            continue
        write(json.dumps(reply(bridge, line), separators=(",", ":")) + "\n")


def reply(bridge: Bridge, line: str) -> dict[str, object]:
    try:
        request = json.loads(line)
    except json.JSONDecodeError:
        return dict(BAD_REQUEST)
    if not isinstance(request, dict):
        return dict(BAD_REQUEST)
    identity = request.get("id")
    # bool is an int in Python, and no request is numbered true.
    if not isinstance(identity, int) or isinstance(identity, bool):
        return dict(BAD_REQUEST)
    kinds = [kind for kind in _KINDS if kind in request]
    if len(kinds) != 1 or ("with" in request and kinds[0] != "ask"):
        return dict(BAD_REQUEST)
    match kinds[0], request[kinds[0]], request.get("with", {}):
        case "ask", str(ask), dict(given):
            return {"id": identity, "answer": bridge.answer(ask, given)}
        case "act", str(action), _:
            return {"id": identity, "outcome": bridge.act(action).value}
        case "sign_in", {"username": str(username), "password": str(password)}, _:
            return {"id": identity, "outcome": bridge.sign_in(username, password).value}
        case "sign_out", True, _:
            return {"id": identity, "outcome": bridge.sign_out().value}
    return dict(BAD_REQUEST)
