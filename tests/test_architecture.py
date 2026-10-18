"""An architecture fitness check that keeps the boundary in one place.

Visibility is decided only in ``hearth.access``, consulted only by
``hearth.disclosure``, and pages reach household data only through the views
``disclosure`` builds. This resists erosion: someone adding a second call site
that the crawl in ``test_privacy_boundary`` does not cover fails here first.

It is not proof of the boundary; the crawl is. It reads the import graph and
attribute names, so it cannot catch aliasing, re-export, ``getattr`` or dynamic
import.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

import hearth

PACKAGE = Path(hearth.__file__).parent


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


def importers_of(target: str) -> set[str]:
    return {
        name
        for name, tree in modules()
        if name != target
        and any(m == target or m.startswith(f"{target}.") for m in imported_by(tree))
    }


def test_only_disclosure_and_the_admin_gate_consult_the_access_policy() -> None:
    assert importers_of("hearth.access") == {"hearth.disclosure", "hearth.web.deps"}


def test_only_disclosure_turns_the_ledger_into_figures() -> None:
    assert importers_of("hearth.figures") == {"hearth.disclosure"}


@pytest.mark.parametrize("module", ["hearth.figures", "hearth.accounts", "hearth.ledger"])
def test_pages_cannot_reach_household_data_except_through_disclosure(module: str) -> None:
    web = {name for name in importers_of(module) if name.startswith("hearth.web")}
    # deps only carries the ledger to Disclosure.load; it reads nothing from it.
    allowed = {"hearth.web.deps"} if module == "hearth.ledger" else set()
    assert web <= allowed


def test_visibility_is_decided_by_one_function() -> None:
    callers = {
        name
        for name, tree in modules()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "may_see"
    }
    assert callers == {"hearth.disclosure"}
