# How the backend modules fit together

Four packages sit behind the pages: `valuation`, `reports`, `vault` and `review`.
None of them has routes or templates, and none decides who may see what: pages reach them through `hearth.disclosure`, which asks `hearth.access` (`docs/privacy-boundary.md`).
Each has its own document; this one says which module answers which question.

## Which module answers which question

| Question | Module | Entry point | Reads |
|---|---|---|---|
| What does the household hold, and on which account? | `valuation.holdings` | `holdings_at(entries, as_of)` | ledger |
| What is it worth, and how far can that be trusted? | `valuation.networth` | `value_holdings(...)` | holdings, `price` table |
| Where do prices come from, and when are they refreshed? | `valuation.eodhd`, `valuation.refresh` | `EodhdSource`, `refresh_prices(...)` | EODHD, once a key exists |
| Income, expenses and savings rate by month | `reports.statement` | `income_statement(...)` | ledger |
| Whose accounts make up a member's or a joint account's figures | `hearth.figures` | `figures.solo(...)`, `figures.joint(...)` | `accounts`, `account_owners` |
| Each account's balance, and whether it matches the statements | `reports.balances` | `account_balances(...)` | ledger |
| How much history each account holds, and from which source | `reports.coverage` | `account_coverage(...)` | ledger |
| A member's Plaid tokens | `vault.tokens` | `TokenVault` | `vault_*` tables, Keychain |
| What an import set aside, and what members ruled | `review.desk` | `Disclosure.review_desk(...)`, `disclosure.review_decisions(...)` | pile file, `review_*` tables |

## What each one owns

- **The ledger owns every figure about the household's money.**
  `valuation` and `reports` only read it, as beancount entries loaded with beancount's loader, and always for an explicit date or period.
- **The app database owns everything else**, created by `hearth.db`'s migrations: who owns which account, market prices with their provenance, encrypted tokens, and review rulings.
  It holds no balance or amount; the one money-named column is `price.price`, a market price.
- **The Keychain owns the vault's master keys**, one set per database.

## Rules every caller can rely on

- **Member figures are sole accounts only, and that model has one implementation: `hearth.figures`.**
  Each joint account is its own line, belonging to its owners together, and the household counts every account exactly once.
  `valuation` values whichever accounts `figures.solo` or `figures.joint` names; nothing else groups accounts by owner.
- **Nothing incomplete poses as complete.**
  An unpriced holding is worth `None`, never zero; `NetWorth.total` raises while anything is unpriced, and `known_total` is the labelled floor.
  Stale prices carry their age.
  Amounts in another currency and failed balance assertions are listed, never dropped.
- **Nothing is booked that nobody ruled on.**
  A group ruling covers only the rows its author saw; there is no auto-accept.
- **No token or key is ever printed**, in any repr, log, exception or traceback.

## What is not wired yet

- `hearth.figures` reads through the `Ledger` protocol, which has only an in-memory implementation; a beancount-backed `Ledger` is next, and its balances should be the ones `reports.balances` verifies against the statements.
- Prices: no EODHD key exists until the plan is bought, so every fund values as unpriced; the daily refresh schedule and the two-year backfill wait on the key (`docs/valuation.md`).
- Review: the pile is read from beancount_nextgen's `pending.beancount`; a pile fed by Plaid will need its importer to assign review ids by the same contract (`review.ids`).
