# hearth

Local-first household finance: net worth, income statement and expense review for a household, over a [beancount](https://beancount.github.io/) ledger.

Runs on one machine. Each household member signs in with a username and password, connects their own institutions, and controls what the rest of the household can see of their finances.

## Principles

**The ledger is the source of truth.** Every figure traces to a ledger entry. Nothing is a black box, and the numbers reconcile against real statements.

**Privacy is a server-enforced boundary, not a display preference.** Data a viewer may not see is never rendered into a response. The application is server-rendered for exactly this reason: a leak would require actively rendering forbidden data, rather than merely forgetting a check.

**Nothing is booked that nobody checked.** Automatic categorisation proposes; a person decides. Decisions are durable and keyed to a transaction's content, so re-importing never re-asks a settled question.

## Status

Early. See `design/` for the approved interaction design.

## Development

```sh
uv sync
uv run hearth
```

If this repository lives under a cloud-synced folder such as `~/Documents` with iCloud Drive, put the virtual environment elsewhere:

```sh
export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/hearth"
```

iCloud sets the macOS hidden flag on files inside `.venv`, and Python silently skips hidden `.pth` files, which leaves an editable install inert while the test suite still passes.

## Data

No financial data, credentials or personal identifiers belong in this repository. The ledger, statements and the application database live outside it; see `.gitignore`.
