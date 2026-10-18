# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Check everything with `uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest`; CI runs the same (`.github/workflows/ci.yml`).
- Never commit real account identifiers, balances, payees or personal names, anywhere. Fixtures and the demo (`src/hearth/demo.py`, `tests/household.py`) are invented.
- The privacy boundary is the product. Visibility is decided only in `src/hearth/access.py` and applied only in `src/hearth/disclosure.py`; pages render views from `Disclosure` and nothing else. The behavioural crawl in `tests/test_privacy_boundary.py` enforces the boundary; `tests/test_architecture.py` only prevents erosion. See `docs/privacy-boundary.md`.
- Architecture fitness tests of the import graph (`tests/test_architecture.py`) are a deliberate, allowed category here: they catch a second call site the crawl might not cover before it ships. They are not proof of behaviour and cannot see aliasing, re-exports, `getattr` or dynamic imports.
- A new route must be added to `ROUTERS` in `src/hearth/web/routes/__init__.py` and taught to `tests/test_privacy_boundary.py`, which fails the build otherwise. Anything private must answer exactly like something missing: raise `Concealed`.
- Server-rendered only: never add an endpoint that returns data as JSON.
- `design/` belongs to the design crew; do not edit it from app work.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
