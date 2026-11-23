# Ledger reports

`hearth.reports` answers household-wide questions of the ledger: the income statement, whether each account's balance reproduces its statements, and how much history each account holds.
No page shows the income statement: it adds up every account at once, and every dashboard figure is a sum of parts, each asked of the gate on its own, so the pages read income and spending through `figures.month_flows`, which keeps each part apart (`docs/dashboards.md`).
Figures attributed to a member or to a joint account are `hearth.figures`'s, which owns that model: a member's figures cover the accounts they own alone, and each joint account is figured on its own (`docs/privacy-boundary.md`).
Every function takes beancount entries loaded with beancount's loader and the explicit date or period it answers for.
None of them reads a clock.

The discipline is the same as for valuation (`docs/valuation.md`): a figure that cannot be computed completely says so and names what it leaves out, instead of returning a plausible number.

## The reports

| Question | Function | Says it is incomplete when |
|---|---|---|
| Income, expenses by category, net and savings rate, by month | `statement.income_statement` | an income or expense amount is in another currency (`excluded`); entries flagged `!` are counted as `unreviewed` |
| Each account's balance, checked against the statements | `balances.account_balances` | the postings do not reproduce one of the account's balance assertions (`disagreements`) |
| How much history each account holds, and from where | `coverage.account_coverage` | (it is the measure of completeness itself) |

Amounts are `Decimal` throughout and are never converted between currencies.
The savings rate is `None` in a month with no income, because any rate there would be invented.
A month cut by the period's start or end is marked `partial`.

## Spending is what was spent

Every income and expense account is a kind of money (`hearth.categories`, `docs/real-ledger.md`), and each recompute holds every account's kind in `Books.kinds`, as a person set it or as its name says.
Every figure that means spending counts only spending and tax, never "not spending", such as a retirement contribution booked as an expense: the spending totals and their typical months, "Where the month went" and its categories, the month's pace, its fixed bills, and each day's spending on a member's page (`Books.spends`, `figures.month_flows`, `reports.flows.spending_by_category`).
Nothing is lost by it, and the figures still add up: money in less money out is what was left over, so an amount moved somewhere else rather than spent is part of what was left over, not of what went out.
`MonthFlows.kept` holds what was left out of spending, by part and category, so spending and it together are every expense.
Wherever a spending figure leaves some out, one plain line beside it says how much and that it is not counted as spending, and a day's line on a member's page says so of each such row; the amount is said only to whoever may see the categories or transactions of the parts behind the figure, as which categories a part books to is its detail.
Income totals count every income kind: pay, investment income and other income alike.
The income statement (`statement.income_statement`) is not yet told kinds, and adds up every expense.

## Balances agree with the statements

A beancount `balance` assertion gives an account's balance, sub-accounts included, at the start of its day, usually copied from a statement.
`balances.check_balance_assertions` recomputes every assertion from the postings, exactly and independently of beancount's own check.
`account_balances` reports, per account, the latest assertion its postings reproduce (`last_verified`) and every one they do not (`disagreements`).
A balance with a disagreement is not shown as though it were trustworthy.

## Coverage, so a discontinuity is visible

A ledger built from statement downloads for one stretch and a bank feed for another can look seamless while it is missing weeks.
`account_coverage` reports, for every Assets and Liabilities account opened by the as-of day:

- the first and last transaction;
- one `Span` per origin, from its first to its last transaction, so the dashboard can say "statements until March, bank feed from June";
- every `Gap` of more than 31 days between consecutive transactions (the threshold is a parameter).

The origin of an entry defaults to its `source` metadata up to the first colon, which is how bcng's statement import labels entries.
Entries without it are `unlabelled`.
Pass another `origin` function when feed-imported entries carry a different label.
