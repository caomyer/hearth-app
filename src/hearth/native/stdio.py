"""The bridge's wire: one JSON object a line, in and out.

A request is ``{"id": <number>, "ask": "<screen>"}``. Its reply is ``{"id": <the same>,
"answer": {...}}``, where the answer is the doorway's (``hearth.native.doorway``). A line that is
not such a request is answered ``{"id": null, "error": "bad request"}`` and the bridge carries
on: one malformed line must not take the engine down with the books in it.

Nothing else may be written to the stream the replies go to, so everything hearth prints for a
person goes to standard error while a bridge runs.
"""

import json
from collections.abc import Callable, Iterable

from hearth.native.doorway import Doorway

BAD_REQUEST = {"id": None, "error": "bad request"}


def serve(doorway: Doorway, lines: Iterable[str], write: Callable[[str], object]) -> None:
    """Answer each line of `lines` with one line to `write`, until `lines` ends."""
    for line in lines:
        if not line.strip():
            continue
        write(json.dumps(reply(doorway, line), separators=(",", ":")) + "\n")


def reply(doorway: Doorway, line: str) -> dict[str, object]:
    try:
        request = json.loads(line)
    except json.JSONDecodeError:
        return dict(BAD_REQUEST)
    if not isinstance(request, dict):
        return dict(BAD_REQUEST)
    identity, ask = request.get("id"), request.get("ask")
    # bool is an int in Python, and no request is numbered true.
    if not isinstance(identity, int) or isinstance(identity, bool) or not isinstance(ask, str):
        return dict(BAD_REQUEST)
    return {"id": identity, "answer": doorway.answer(ask)}
