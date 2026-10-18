# How the backend modules fit together

Five packages sit behind the pages: `valuation`, `reports`, `vault`, `review` and `plaid`.
None of them has routes or templates, and none decides who may see what: pages reach them through `hearth.disclosure`, which asks `hearth.access` (`docs/privacy-boundary.md`).
Each has its own document; this one says which module answers which question.

## Which module answers which question

| Question | Module | Entry point | Reads |
|---|---|---|---|
| What does the household hold, and on which account? | `valuation.holdings` | `holdings_at(entries, as_of)` | ledger |
| What is it worth, and how far can that be trusted? | `valuation.networth` | `net_worth_by_owners(...)`, `value_holdings(...)` | holdings, `price` table |
| Where do prices come from, and when are they refreshed? | `valuation.eodhd`, `valuation.refresh` | `EodhdSource`, `refresh_prices(...)` | EODHD, once a key exists |
| Income, expenses and savings rate by month | `reports.statement` | `income_statement(...)` | ledger |
| Spending and income by category, per member, per joint account, for the household | `reports.flows` | `spending_by_category(...)`, `income_by_source(...)` | ledger, ownership |
| Each account's balance, owners, and whether it matches the statements | `reports.balances` | `account_balances(...)` | ledger, ownership |
| How much history each account holds, and from which source | `reports.coverage` | `account_coverage(...)` | ledger |
| Who owns which account | `reports.ownership` | `Ownership.from_accounts(accounts)` | `accounts`, `account_owners` |
| A member's Plaid tokens | `vault.tokens` | `TokenVault` | `vault_*` tables, Keychain |
| What waits for review, and what members ruled | `review.desk`, `review.queue` | `Disclosure.review_desk(...)`, `disclosure.rulings_in(...)` | ledger `!` rows, `review_*` tables |
| A member's connections, their allowance, coverage and mapping | `plaid.connector` | `Disclosure.connector(...)` | `plaid_*` tables, feed store, vault |
| What Plaid sent, and every sync | `plaid.feed` | `Feed` | `feed.sqlite3` |

## What each one owns

- **The ledger owns every figure about the household's money.**
  `valuation` and `reports` only read it, as beancount entries loaded with beancount's loader, and always for an explicit date or period.
- **The app database owns everything else**, created by `hearth.db`'s migrations: who owns which account, market prices with their provenance, encrypted tokens, and review rulings.
  It holds no balance or amount; the one money-named column is `price.price`, a market price.
- **The feed store owns what Plaid sent**, in `feed.sqlite3`, as a staging store for the import (`docs/connections.md`).
- **The Keychain owns the vault's master keys**, one set per database.

## Rules every caller can rely on

- **Member figures are sole accounts only.**
  Each joint account is its own line, belonging to its owners together, and the household counts every account exactly once.
  This holds for spending and income (`Flows.for_member`, `.joint`, `.household`) and net worth (`OwnedNetWorth.solo`, `.joint`, `.household`).
- **Nothing incomplete poses as complete.**
  An unpriced holding is worth `None`, never zero; `NetWorth.total` raises while anything is unpriced, and `known_total` is the labelled floor.
  Stale prices carry their age.
  Amounts in another currency, unattributed spending, unassigned accounts and failed balance assertions are listed, never dropped.
- **Nothing unreviewed poses as checked.**
  Every imported row is in the books, flagged `!` until a member rules on it, and every spending and income total says how much rests on such rows.
  An accept is bound to the proposal its author saw; there is no auto-accept.
- **No token or key is ever printed**, in any repr, log, exception or traceback.

## What is not wired yet

- `hearth.figures` (foundation) and `hearth.reports` both compute member spending: `figures` over the `Ledger` protocol, which has only an in-memory implementation, and `reports` over beancount entries.
  The beancount-backed `Ledger` should delegate to `reports`, so each figure has one implementation.
- Prices: the household values holdings at each institution's own price (`valuation.institution`), a reader awaiting its producer. The holdings fetch is not implemented. It needs Plaid's Investments product, which the captain's brokerage Item is not consented to and which is separately billed. Until that decision is made and the fetch is built, the `plaid` price source yields nothing and every fund values as unpriced (`docs/valuation.md`).
- Review: rulings are laid over the ledger at each recompute and never written back to the ledger file.
  Plaid snapshots go through the same `bcng import` stage as statements, so their rows carry review ids by the same contract (`review.ids`), a proposed account and the categoriser's confidence, and rulings follow them.
