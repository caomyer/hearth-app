# hearth

Local-first household finance: net worth, income statement and expense review for a household, over a [beancount](https://beancount.github.io/) ledger.

Runs on one machine. Each household member signs in with a username and password, connects their own institutions, and controls what the rest of the household can see of their finances.

## Principles

**The ledger is the source of truth.** Every figure traces to a ledger entry, in a beancount file anyone can open and check. Nothing is a black box, and the numbers reconcile against real statements.

**Privacy is a server-enforced boundary, not a display preference.** Data a viewer may not see is never rendered into a response. The application is server-rendered for exactly this reason: a leak would require actively rendering forbidden data, rather than merely forgetting a check.

**Nothing unchecked passes as checked.** Every transaction is in the books the moment it arrives, flagged until a person reviews it, and every total says how much rests on rows nobody has looked at. Automatic categorisation proposes; a person decides. Decisions are durable and keyed to a transaction's content, so re-importing never re-asks a settled question.

## Status

Early.
Members, sign-in, sharing and the privacy boundary are in place ([docs/privacy-boundary.md](docs/privacy-boundary.md)), and so are the dashboards over the beancount ledger: the household, each member's own page, other members' shared pages, net worth with positions and price provenance, review, and sharing settings ([docs/dashboards.md](docs/dashboards.md)).
Every imported transaction is in the books at once, flagged until a member reviews it, and every total says how much rests on unreviewed rows ([docs/review.md](docs/review.md)).
Each is suggested a category from what its owners decided before for the same merchant, from the household's imported ledger, or from the bank, and says why in words; a suggestion is never a decision.
Spending totals leave out money booked as an expense that is not spending, such as a retirement contribution, which is in what was left over instead ([docs/reports.md](docs/reports.md)).
Members connect their institutions through Plaid, each through a Plaid account of their own by default, and hearth pulls two years of history into a staging store, checked per account for how much each bank actually sent ([docs/connections.md](docs/connections.md)).
A household needs no ledger of its own: the accounts its members connect become the chart of accounts of books hearth keeps itself, written out on every recompute for anyone to open in fava ([docs/books.md](docs/books.md)).
A bank's payment to a card the household also connects is paired from its two sides into one transfer, so the import can book it once and never as spending and income at once ([docs/pairing.md](docs/pairing.md)).
Every posted transaction a connected bank sends is booked into those books, flagged until a member reviews it, and every connected account opens at the balance its bank reports, proved against that balance on every recompute ([docs/books.md](docs/books.md)).
The calls made along the way are in [docs/foundation-decisions.md](docs/foundation-decisions.md) and [docs/dashboards.md](docs/dashboards.md); `design/` holds the approved design.

## Mac app

A native Mac app over the same engine is being built in `apple/` ([apple/README.md](apple/README.md)).
It carries hearth's Python inside it and asks it for each screen ([docs/native.md](docs/native.md)); so far it shows the invented household's net worth.

## Development

```sh
uv sync
uv run hearth demo          # an invented household in a throwaway directory
uv run hearth demo --first-review  # the same, the day its two-year first import lands
uv run hearth demo --from-zero     # a household on its first day: members, no ledger, banks to connect
uv run pytest
```

To run for real, create the first admin and serve on this machine only; each member then connects their own banks from the site:

```sh
uv run hearth add-member <username> --name "<name>" --admin
uv run hearth serve --approve-plaid link --approve-plaid read
```

A household that already keeps a beancount ledger imports it once: hearth keeps a frozen copy of every file it reads in its data directory, prints the check to hold against the ledger's own figures, and reads the copy from then on ([docs/real-ledger.md](docs/real-ledger.md)).
Then say who owns each of its accounts:

```sh
uv run hearth import-ledger "$HOME/path/to/household.beancount"
uv run hearth add-account <ledger account> --name "<name>" --owner <username>
```

Once each connected account is matched to the imported ledger's account it is (`hearth match-accounts`), hearth carries it on: its history is the ledger's up to where the ledger stops, and the bank's from then on, checked against the balance the bank reports, with any difference between the two shown in Review ([docs/books.md](docs/books.md#carried-on-from-the-base)).
Inheriting the ledger where it is, through `HEARTH_LEDGER`, still works for now, but not beside an imported one.

A household that kept a ledger before connecting its banks can hold hearth's own books against it, account by account, at each statement's close or each month end ([docs/books.md](docs/books.md#checking-against-a-ledger-kept-before)).
It reads both, writes nothing, and prints where they differ and which rows make the difference:

```sh
uv run hearth compare <ledger.beancount> --cycles <run.json> --monthly
```

A household that keeps its own ledger in fava can take the connections and rows from hearth and nothing else ([docs/connections.md](docs/connections.md#handing-rows-to-a-ledger-kept-elsewhere)):

```sh
uv run hearth export --member <username>
```

Hearth never writes a ledger it imports or inherits, nor anything beside it.
It recomputes at start-up, every morning at 6:00 and on request, never on page load, and writes its books to `books.beancount` in its data directory each time: an output it never reads, unlike the imported ledger in `ledger-base/` beside it.

No live Plaid call is made unless approved for this run, operation by operation:

```sh
uv run hearth serve --approve-plaid read                 # sync existing connections, daily
uv run hearth serve --approve-plaid read --approve-plaid holdings  # and what each brokerage holds, at its prices
uv run hearth serve --approve-plaid link --approve-plaid read --approve-plaid remove
uv run hearth plaid items                                # every connection; no live call
```

A bank that signs a member in on its own page opens in a window of its own; for one that takes over the whole tab instead, Plaid's production brings the member back only to an `https://` address, so serve Hearth over HTTPS and register the redirect URI `hearth serve` prints in the Plaid dashboard ([docs/connections.md](docs/connections.md#banks-that-sign-in-on-their-own-page)):

```sh
mkcert -install && mkcert localhost      # brew install mkcert
uv run hearth serve --tls-cert localhost.pem --tls-key localhost-key.pem --approve-plaid link --approve-plaid read
```

State lives in `$HEARTH_DATA_DIR`, by default `~/.local/share/hearth`, outside any cloud-synced folder, with `hearth.log`, which records what Hearth asked of Plaid and how it went and never a token, key, balance, account or payee.
Set `HEARTH_HTTPS=1` when serving behind TLS so the session cookie is marked secure; `--tls-cert` does that itself.

If this repository lives under a cloud-synced folder such as `~/Documents` with iCloud Drive, put the virtual environment outside it:

```sh
export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/hearth"
```

iCloud sets the macOS hidden flag on files inside `.venv`, and Python silently skips hidden `.pth` files, which leaves an editable install inert while the test suite still passes.

## Data

No financial data, credentials or personal identifiers belong in this repository. The ledger, statements and the application database live outside it; see `.gitignore`.

## License

Hearth is free software, licensed under the GNU General Public License, version 2 or (at your option) any later version; see [LICENSE](LICENSE).
It builds on [beancount](https://github.com/beancount/beancount), which is licensed under the GNU General Public License, version 2 only, so a copy of hearth distributed together with beancount, as the Mac app is, is distributed under version 2.
