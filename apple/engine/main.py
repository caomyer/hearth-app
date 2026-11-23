"""Starts hearth inside the app bundle.

The app runs this with the Python that sits beside it, in isolated mode, so nothing on the
Mac it runs on (no environment variable, no user site-packages, no other Python) can change
what is imported: only the bundle's own copies of hearth and what it depends on.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE / "app"), str(HERE / "site-packages")]

from hearth.cli import main  # noqa: E402 - the path above has to be set first

main()
