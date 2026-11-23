"""An architecture fitness check: the shape that keeps the boundary in one place.

This is erosion prevention, not proof. The behavioural crawl in ``tests/test_privacy_boundary.py``
is what enforces the privacy boundary: it attacks every route and checks every response. This
file answers a different question: whether someone has added a second place that decides, or
a page that reads the books directly, which the crawl only catches if its fixtures happen to
exercise the new path. That is how boundaries like this one actually erode.

The rules, for the package as it is now:

- ``hearth.access`` is imported by exactly one module, ``hearth.disclosure.gate``, and every
  call into it is made there.
- Only modules of the ``hearth.disclosure`` package turn the ledger into figures.
- Pages (``hearth.web``, and the native app's doorway ``hearth.native``) import no ledger
  reader and touch none of the books' raw data.
- Only ``hearth.disclosure`` builds a member's ``Connector``, the one way a page reaches
  connections, so every connection a page shows went past the gate.
- Only ``hearth.native.bridge`` builds the native app's ``Doorway``, so the app is answered
  only as the member signed in to the bridge.

What it cannot catch, so nobody mistakes it for proof: an alias or re-export of an ``access``
function, a ``getattr`` or ``importlib`` call, or any other dynamic import. It reads import
statements and attribute names, which are a proxy for the structure, not the behaviour.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

import hearth

PACKAGE = Path(hearth.__file__).parent
GATE = "hearth.disclosure.gate"
PAGES = ("hearth.web", "hearth.native")
"""Everything that shows household data to someone."""

LEDGER_READERS = (
    "hearth.figures",
    "hearth.reports",
    "hearth.accounts",
    "hearth.valuation.networth",
    "hearth.valuation.holdings",
    "hearth.review.pile",
    "hearth.plaid.feed",
    "hearth.plaid.registry",
    "hearth.plaid.pairing",
    "hearth.plaid.booking",
    "hearth.plaid.opening",
    "hearth.comparison",
    "hearth.ledger_import",
    "hearth.matching",
)
"""Modules that read household data. Pages get it only through hearth.disclosure."""

RAW_BOOKS = (
    "entries",
    "positions",
    "month_ends",
    "pile",
    "coverage",
    "account_ends",
    "account_reaches",
    "synced",
)
"""The books' raw contents. A page that names one of these is reading past disclosure."""


def modules() -> Iterator[tuple[str, ast.Module]]:
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE.parent).with_suffix("")
        name = ".".join(relative.parts).removesuffix(".__init__")
        yield name, ast.parse(path.read_text(), filename=str(path))


def imported_by(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


def _imports(module: str, target: str) -> bool:
    return module == target or module.startswith(f"{target}.")


def importers_of(target: str) -> set[str]:
    return {
        name
        for name, tree in modules()
        if name != target and any(_imports(m, target) for m in imported_by(tree))
    }


def test_only_the_gate_consults_the_access_policy() -> None:
    assert importers_of("hearth.access") == {GATE}


def test_every_call_into_the_access_policy_is_made_by_the_gate() -> None:
    callers = {
        name
        for name, tree in modules()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "access"
    }
    assert callers == {GATE}


def test_only_disclosure_turns_the_ledger_into_figures() -> None:
    outside = {
        name for name in importers_of("hearth.figures") if not _imports(name, "hearth.disclosure")
    }
    assert outside == set()


def _is_page(name: str) -> bool:
    """Whether a module shows household data to someone: a web page, or the native app's
    doorway (``hearth.native``), which is held to everything a page is."""
    return any(_imports(name, package) for package in PAGES)


@pytest.mark.parametrize("reader", LEDGER_READERS)
def test_pages_import_no_ledger_reader(reader: str) -> None:
    pages = {name for name in importers_of(reader) if _is_page(name)}
    assert pages == set()


def test_pages_touch_none_of_the_books_raw_data() -> None:
    reaching = sorted(
        (name, node.attr)
        for name, tree in modules()
        if _is_page(name)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in RAW_BOOKS
    )
    assert reaching == []


def builders_of(cls: str) -> set[str]:
    """The modules that call `cls` by name, as a constructor is called."""
    return {
        name
        for name, tree in modules()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == cls)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == cls)
        )
    }


def test_only_disclosure_builds_a_members_connector() -> None:
    assert builders_of("Connector") == {"hearth.disclosure"}


def test_only_the_bridge_opens_the_native_doorway() -> None:
    """The doorway answers as whichever member it is handed. Only the bridge, which hands it
    the member signed in to a live session, may hand it one: a second place that built a
    doorway could answer the app as someone nobody signed in as."""
    assert builders_of("Doorway") == {"hearth.native.bridge"}
    assert importers_of("hearth.native.doorway") == {"hearth.native.bridge"}
