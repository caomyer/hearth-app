# Ledger reports

`hearth.reports` answers household-wide questions of the ledger: the income statement, whether each account's balance reproduces its statements, how much history each account holds, and the statements the Mac app shows (`statements`, below).
No page shows `statement.income_statement`: it adds up every account at once, and every dashboard figure is a sum of parts, each asked of the gate on its own, so the pages read income and spending through `figures.month_flows`, which keeps each part apart (`docs/dashboards.md`).
`statements` adds up only the accounts it is given, which disclosure decides.
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
| What came in and went out, what was held, and why it changed, over the accounts given | `statements.flows`, `statements.sheets`, `statements.bridge` | a holding has no price (`Held.at_cost`) or neither price nor cost (`Held.unvalued`); an amount cannot be converted (`Flows.unconverted`, `Bridge.difference`) |

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

## Statements

`statements` reads the books the way a business reads its own, over the accounts it is given and nothing else.
Who may see which accounts is decided before it is asked (`hearth.disclosure.statements`, and "Statements" in `docs/privacy-boundary.md`).
It has three parts that share one set of numbers, so they always agree:

- `flows`: every income and expense account's money over a period, and the money that moved what the accounts hold without being earned or spent;
- `sheets`: what each account held at the end of a day, at cost and at market value;
- `bridge`: why what the accounts hold changed between two sheets.

### What each account holds

- Money is worth its units.
- A lot is held at its cost, and valued at the latest price on or before the day (`Books.prices`, the prices the recompute read, held in memory).
- A holding with no price at all is valued at its cost and named (`Held.at_cost`), never left out and never valued at zero.
- A holding booked without a lot, as hearth books what a brokerage reports, costs what it was worth on the day it was booked.
  Less of one gives up its average cost, whatever it went for.
- A holding with neither a price nor a cost is left out of both values and named (`Held.unvalued`).
- One booked when nothing said what it was worth, that has a price now, costs what it is worth now and is named (`Held.cost_unknown`).

### What came in and went out

A transaction whose balance-sheet postings are all among the accounts is read in full:

- its income and expense postings by account, each as the kind of money its account is (`hearth.categories`);
- a posting to `Equity:Review:` as money not placed yet, which counts in no income or spending;
- any other equity posting as an opening balance or adjustment: what an account held before its history, a pad, a difference reconciling an imported ledger with a bank.

A transaction that also moves an account outside them is a move across their edge.
What it did to the accounts inside is one amount, and none of its categories is counted, as nothing says whose they were.

### The summary's figures

| Figure | What it is |
|---|---|
| Take-home pay | Income of kind pay. Pay is counted as it reached the bank, so the statement starts from take-home pay. |
| Tax | Expenses of kind tax. |
| Spending | Expenses of kind spending, by the branch of `Expenses` each is under: the six largest by name, the rest as "Other spending", so every category is in one line. |
| Saved from work | Take-home pay less tax and spending. |
| Savings rate | Saved from work over take-home pay less tax; none when that is not more than nothing. |
| Not counted as spending | Expenses of kind not spending, such as a retirement contribution: they left the accounts, but were kept, not spent. |
| Investment income | Income of kind investment income, realised gains among it. |
| Other income | Income of kind other income, which is every income account no rule recognises. |
| Net worth | What the accounts hold at the end of the period's last day, at market value or at cost. |
| Market movement | The change in gain not yet realised (value less cost), less the gain that moves across the accounts' edge carried in or out. |

### Why net worth changed

```
start + saved from work + investment income + market movement + other = end
```

"Other" is never one hidden number: each part is its own line (`statements.Other`).
Other income, less what was not counted as spending, plus opening balances and adjustments, money not placed yet, moves to and from accounts outside the statement, gains on holdings with no lot given up for more or less than their average cost, which the books booked as no income, and what entries leave over within the tolerance beancount balances them to, such as a lot whose units times its cost is not to the cent of what was paid.

It adds up because every transaction balances.
What the accounts hold at cost changes by exactly what each transaction's other postings weigh, which are the flows above, plus what a holding without a lot gave up beyond what it weighs, which is its own line.
At market value the change is the change at cost plus the change in gain not yet realised, which is market movement plus the gain moves carried.
A holding that arrives with a gain it had already made, booked against an opening balance or moved in from outside, brings that gain with it: it is part of the move that brought it, not of the period's market movement.

`Bridge.difference` is whatever is left, shown as its own line, never hidden.
It can only come from an amount with no price to convert it.
`tests/reports/test_statements.py` proves it nothing, to the cent, at cost and at market value, for every period between two of many days of a ledger with a pad, lots at cost, prices, a lot opened at a gain, a lot with no price, a sale, holdings booked without lots and one sold, moves out, a row not placed and a reconciling entry; and on `tests/household.py` and `tests/ledgers.py`.

### Comparing, and colouring a change

A period is set against the same days a year before, so part of a year is never set against a whole one; a full year is set against the one before.
A period is `covered` when the books hold every month it touches in full, by `figures.flows_cover`.
A figure from a period the books do not hold in full is not set against anything: its earlier figure is none, never zero.

A change is coloured only when it is both over 1,000 in the operating currency and over 10% of what it is set against (`statements.MATERIAL`, one named pair).
The colour says whether it is good or bad for the household (`Tone`), never up or down: more spending or tax is unfavourable, more pay, saved or investment income favourable.
The savings rate, net worth's other parts and other income are never coloured.

### The other views

- The income statement is the income and expense account tree for the period, with a cell for each calendar month, quarter or year the period touches, cut to it, and a total; each column says what of it rests on rows nobody has reviewed yet, at their suggested category, and how many nothing has placed.
- The balance sheet is every account at the start of the period beside its end, grouped Cash, Investments and Other assets, then Credit and Other liabilities, with cost and gain not yet realised as Holdings gives them, from the same lots (`valuation.holdings.Holding.cost`): none where a security is held with no lot, and no gain where it has no price; an account whose history begins inside the period says from when.
- Year by year is net worth, take-home pay, spending, saved from work and the savings rate for every year the books hold.
  A year is sparse when the books hold fewer than half its months in full, of those it has had so far for the year in progress (`SPARSE_SHARE`).
