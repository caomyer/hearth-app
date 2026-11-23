"""Asks a bundled engine for the sample household's overview and checks it answers.

    <engine>/python/bin/python3.14 -I scripts/check-engine.py <engine>

Run with the engine's own Python and an empty environment, as the app runs it, so it proves the
bundle stands on its own: no uv, no checkout and no other Python on the machine. Exits 0 when
the engine reports a net worth, and 1 saying what it said instead.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

WITHIN = 120.0
"""Seconds a first recompute of the sample household may take on a slow, cold machine."""


def main(engine: Path) -> int:
    command = [engine / "python/bin/python3.14", "-I", "-B", engine / "main.py", "demo", "--bridge"]
    process = subprocess.Popen(  # noqa: S603 - the engine this script was pointed at
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"HOME": "/var/empty", "TMPDIR": "/tmp", "LC_ALL": "en_US.UTF-8"},  # noqa: S108
    )
    assert process.stdin is not None  # noqa: S101
    assert process.stdout is not None  # noqa: S101
    assert process.stderr is not None  # noqa: S101
    started = time.monotonic()
    answer: dict[str, object] = {}
    number = 0
    while time.monotonic() - started < WITHIN:
        number += 1
        process.stdin.write(json.dumps({"id": number, "ask": "overview"}) + "\n")
        process.stdin.flush()
        line = process.stdout.readline()
        if not line:
            print(f"check-engine: the engine exited:\n{process.stderr.read()}", file=sys.stderr)
            return 1
        answer = json.loads(line)["answer"]
        if answer["state"] != "loading":
            break
        time.sleep(0.25)
    process.stdin.close()
    code = process.wait(timeout=30)
    worth = answer.get("net_worth")
    if answer.get("state") != "ready" or not isinstance(worth, dict) or not worth.get("is_known"):
        print(f"check-engine: no net worth, the engine said {answer}", file=sys.stderr)
        return 1
    if code != 0:
        print(f"check-engine: the engine exited {code} when its input closed", file=sys.stderr)
        return 1
    seconds = time.monotonic() - started
    print(f"check-engine: net worth {worth['value']} in {seconds:.1f}s, and a clean exit")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]).resolve()))
