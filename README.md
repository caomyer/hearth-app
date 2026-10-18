# hearth

Local-first household finance: net worth, income statement and expense review for a household, over a [beancount](https://beancount.github.io/) ledger.

Runs on one machine. Each household member signs in with a username and password, connects their own institutions, and controls what the rest of the household can see of their finances.

## Principles

**The ledger is the source of truth.** Every figure traces to a ledger entry. Nothing is a black box, and the numbers reconcile against real statements.

**Privacy is a server-enforced boundary, not a display preference.** Data a viewer may not see is never rendered into a response. The application is server-rendered for exactly this reason: a leak would require actively rendering forbidden data, rather than merely forgetting a check.

**Nothing is booked that nobody checked.** Automatic categorisation proposes; a person decides. Decisions are durable and keyed to a transaction's content, so re-importing never re-asks a settled question.

## Status

Early.
Members, sign-in, sharing and the privacy boundary are in place ([docs/privacy-boundary.md](docs/privacy-boundary.md)), and so are the dashboards over the beancount ledger: the household, each member's own page, other members' shared pages, net worth with positions and price provenance, review, and sharing settings ([docs/dashboards.md](docs/dashboards.md)).
Every imported transaction is in the books at once, flagged until a member reviews it, and every total says how much rests on unreviewed rows ([docs/review.md](docs/review.md)).
Institution links come next.
The calls made along the way are in [docs/foundation-decisions.md](docs/foundation-decisions.md) and [docs/dashboards.md](docs/dashboards.md); `design/` holds the approved design.

## Development

```sh
uv sync
uv run hearth demo          # an invented household in a throwaway directory
uv run hearth demo --first-review  # the same, the day its two-year first import lands
uv run pytest
```

To run for real, create the first admin, point hearth at the household's ledger, then serve on this machine only:

```sh
uv run hearth add-member <username> --name "<name>" --admin
export HEARTH_LEDGER="$HOME/path/to/household.beancount"
uv run hearth serve
```

Hearth only reads the ledger. It recomputes at start-up, every morning at 6:00 and on request, never on page load.

State lives in `$HEARTH_DATA_DIR`, by default `~/.local/share/hearth`, outside any cloud-synced folder.
Set `HEARTH_HTTPS=1` when serving behind TLS so the session cookie is marked secure.

If this repository lives under a cloud-synced folder such as `~/Documents` with iCloud Drive, put the virtual environment outside it:

```sh
export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/hearth"
```

iCloud sets the macOS hidden flag on files inside `.venv`, and Python silently skips hidden `.pth` files, which leaves an editable install inert while the test suite still passes.

## Data

No financial data, credentials or personal identifiers belong in this repository. The ledger, statements and the application database live outside it; see `.gitignore`.
