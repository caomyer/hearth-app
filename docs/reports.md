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
| What came in and went out, what was held, and why it changed, over the accounts given | `statements.Ledger.flows`, `Ledger.held`, `statements.bridge` | a lot has no price (`Held.unpriced`, a floor) or a holding neither price nor cost (`Held.unvalued`); an amount cannot be converted (`Flows.unconverted`, `Bridge.difference`) |

Amounts are `Decimal` throughout and are never converted between currencies.
The savings rate is `None` in a month with no income, because any rate there would be invented.
A month cut by the period's start or end is marked `partial`.

## Spending is what was spent

Every income and expense account is a kind of money (`hearth.categories`, `docs/real-ledger.md`), and each recompute holds every account's kind in `Books.kinds`, as a person set it or as its name says.
Every figure that means spending counts only expenses of kind spending, never tax and never "not spending", such as a retirement contribution booked as an expense: the spending totals and their typical months, "Where the month went" and its categories, the month's pace, its fixed bills, each day's spending on a member's page, the Mac app's Overview and every statement (`Books.spends`, `figures.month_flows`, `reports.flows.spending_by_category`).
Nothing is lost by it, and the figures still add up: money in less tax and less spending is what was left over, so an amount moved somewhere else rather than spent is part of what was left over, not of what went out.
`MonthFlows.kept` holds what was left out of spending as not spending, and `MonthFlows.taxed` what was tax, by part and category, so spending and they together are every expense.
Wherever a spending figure leaves some out, one plain line beside it says how much and that it is not counted as spending, and a day's line on a member's page says so of each such row; the amount is said only to whoever may see the categories or transactions of the parts behind the figure, as which categories a part books to is its detail.

### Tax is its own figure

Tax is money out, but no choice anyone made at a till, so it is never spending: wherever spending is broken out, tax is its own line beside it, so income, tax, spending and what was left over still add up.
On the household page that is the left-over panel: money in, less tax, less money out, is what was left over, as a share of money in.
Its tax is of the parts whose categories or transactions the viewer may see (`Books.taxes`, `Unreviewed.tax`), as what was booked as not spending is; how much of the tax rests on unreviewed rows is said beside it, as of spending.
The Mac app may count tax as spending instead, by one switch the person turns on, which the engine never hears: every figure that changes with it is sent both ways by the engine (`docs/native.md`), and the app shows one or the other whole.
The switch is offered only where the viewer's statements hold any tax at all (`statements.taxed`: any posting to a tax category, even one a refund netted to nothing), judged on what their statements read and nothing else, so it says nothing of any account outside them.
A part whose categories or transactions the viewer may not see has its tax inside its spending total, as one number, as every spending total was before tax was its own figure (`Household.spent`): so money out, what was left over and its share of money in are the same figures whoever looks, and nothing about the part's categories is told; the panel says, naming nobody, that any tax of those whose categories are not shared is in money out.
A refund larger than the tax paid in a period is tax refunded, said as such, never "less tax" of a negative amount.
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
Each recompute reads the books into one `Ledger` (`Books.ledger`), so a statement over any accounts and days is a few lookups, never a pass over the history; a statement asked of books without one is an error, never a quiet replay.
It answers three things that share one set of numbers, so they always agree:

- `Ledger.flows`: every income and expense account's money over a period, and every other amount that moved what the accounts hold;
- `Ledger.held`: what each account held at the end of a day, at cost and at market value;
- `bridge`: why what the accounts hold changed between two days.

### What each account holds

- Money is worth its units.
- A lot is held at its cost, and at market value at the latest price on or before the day in the operating currency (`Books.prices`, the prices the recompute read, held compactly in memory as `valuation.pricetable.PriceTable`, which keeps any price exactly, and one too long or too large to pack as it is).
  A price only ever quoted in another currency is no price in this one, and its first price is the first in this one.
- A lot with no price is not valued at market value, as Overview, Accounts and Holdings do not value it: it is named (`Held.unpriced`), and every market value it is in is a floor ("at least").
  At cost it is its cost.
- A holding booked without a lot, as hearth books what a brokerage reports, costs what it was worth on the day it was booked.
  One booked before anything priced it, as a brokerage's report can be, stays on its own day and is converted at its first price, and so is money in a currency received before its first rate, with its income or spending in its own month; each figure that holds such an amount says how many (`Flows.later`), as a stale price is said.
  At market value such a holding is not valued until its first price, which is then said on the unpriced line.
- Less of a holding with no lot gives up its average cost, whatever it went for; moved between two accounts in one entry, it keeps what it cost.
- A split is recognised by its shape, never by its other leg: in one commodity, with no lot and no price, an entry that takes the old position out of an account and puts the new one in the same account changes units and nothing else, no cost, no value, no opening, whatever else balances it.
  Units added to a holding against an opening or a reconciling entry alone are never a split: they came from somewhere, and are an opening at what they were worth.
  A split of lots, the old lots taken out at their cost and the new ones put in at theirs, needs no rule: its cost is the same, and the gap between its marks and its lots' cost is what the market did.
- A holding with neither a price nor a cost is left out of both values and named (`Held.unvalued`).
- Each statement says how many of the positions it values are at a stale price (`Held.stale`), as Overview does.

### What came in and went out

A transaction whose balance-sheet postings are all among the accounts is read in full:

- its income and expense postings by account, each as the kind of money its account is (`hearth.categories`);
- a posting to `Equity:Review:` as money not placed yet, which counts in no income or spending;
- any other equity posting as an opening balance or adjustment: what an account held before its history, a pad, a difference reconciling an imported ledger with a bank.

A transaction that also moves an account outside them, in any currency it moves, is a move across their edge.
What it did to the accounts inside is one amount, and none of its categories is counted, as nothing says whose they were.
The journal of a statement's rows (`within: "statements"`) reads every entry by the same rule, so each line's figure is what the rows it opens onto add up to, each by its part (tested over the household and the sample).

### The summary's figures

| Figure | What it is |
|---|---|
| Take-home pay, or Pay before tax | Income of kind pay, as the books hold it. Pay is counted as it reached the bank, so the statement starts from take-home pay; a ledger that books gross pay with what was withheld beside it in the same entry shows the gross here and what was withheld under tax, and saved from work is the same either way. So where the statement's books hold any tax (`statements.taxed`) the line, and Year by year's column, is called "Pay before tax", and "Take-home pay" only where they hold none. |
| Tax | Expenses of kind tax. |
| Spending | Expenses of kind spending, by the branch of `Expenses` each is under: the six largest by name, the rest as "Other spending", so every category is in one line. With tax counted as spending it is "Spending and tax", and tax is the first line under it, still its own. |
| Saved from work | Pay less tax and spending; the same whether tax is counted as spending or not. |
| Savings rate | Saved from work over pay less tax; none when that is not more than nothing. Where the statement holds any tax it says "after tax"; with tax counted as spending it is saved from work over pay, "with tax". |
| Not counted as spending | Expenses of kind not spending, such as a retirement contribution: they left the accounts, but were kept, not spent. |
| Investment income | Income of kind investment income, realised gains among it. |
| Other income | Income of kind other income, which is every income account no rule recognises. |
| Net worth | What the accounts hold at the end of the period's last day, at market value or at cost. |
| Market movement | What prices did to what was held over the period (below); nothing when prices did not move. |

#### Spending here and on the dashboard

The dashboard's spending (`Books.spends`, `figures.month_flows`) counts expenses of kind spending, as the statement does, and both keep tax as its own figure.
For the same parts and month the two agree: the dashboard's spending is the statement's spending, and its tax the statement's tax (`tests/native/test_statements.py`).
They differ where their rules do:

- an amount in another currency is left out of the dashboard's figures, and converted here at the latest rate on or before its day, or left out and counted (`Flows.unconverted`) when there is none;
- a row paid from accounts of different owners is in no part on the dashboard, so in nobody's spending; here it is counted when every account it moves is in the statement, and is one amount at the edge when one is not.

### Why net worth changed

```
start + saved from work + investment income + market movement + other = end
```

"Other" is never one hidden number: each part is its own line (`statements.Other`):

- other income, less what was not counted as spending;
- opening balances and adjustments, at market value with any gain a holding came in with;
- money not placed yet;
- moves to and from accounts outside the statement, at market value with the gain the holdings moved carried with them;
- at market value, gains already counted in value, booked as income: when an entry gives up a lot and books income, investment income says the gain, which the lot's value already held, and this line takes back the gap between what the lot given up was worth that day and what it cost, so it is not counted twice; a gap on any other posting, a buy or a reinvested dividend off the day's close, is market movement;
- at market value, holdings with no price: what was paid for lots nothing prices, which no screen values, and what a lot was worth when its first price came;
- at cost, gains on holdings with no lot given up for more or less than their average cost, which the books booked as no income;
- what entries leave over within the tolerance beancount balances them to.

It adds up because every transaction balances.
At cost, what the accounts hold changes by exactly what each transaction's other postings weigh, plus what a holding without a lot gave up beyond what it weighs.
At market value every posting is marked at its day's price as well: market movement is the market value at the end, less at the start, less every posting's value on its day and each lot's value on its first price, so it is what prices did to what was held between those days, and the gap between a posting's mark and what it was booked at is said where it belongs.
A sale a little off the day's close is the market's, and is in market movement.

`Bridge.difference` is whatever is left, shown as its own line, never hidden.
It can only come from an amount nothing can price.
`tests/reports/test_statements.py` proves it nothing, to the cent, at cost and at market value, for every period between two of many days of a ledger with a pad, lots at cost, prices, a lot opened at a gain, a lot with no price, a sale, holdings booked without lots and one sold, moves out, a row not placed and a reconciling entry.
It works by hand, line by line, a holding moved between two accounts while its price rose, a split, a gain made before the period and taken in it, money in another currency spent, and a holding and money booked before anything priced them; and it checks `tests/household.py`, `tests/ledgers.py`, and the sample with its prices in `tests/native/test_statements.py`.

### Comparing, and colouring a change

A period is set against the same days a year before, so part of a year is never set against a whole one; a full year is set against the one before.
A period is `covered` when the books hold every month it touches in full, by `figures.flows_cover`.
A figure from a period the books do not hold in full is not set against anything: its earlier figure is none, never zero.

A brokerage's holdings are known from the day it reported them (`books.positioned`), and nothing says what it held before.
So a net worth on any day before that has no figure, and is set against nothing: net worth's start, and its end for a period that ended before; the account's start on the balance sheet; and the year in Year by year.
Why net worth changed is then worked out without the account, and names it.

A change is coloured only when it is both over 1,000 in the operating currency and over 10% of what it is set against (`statements.MATERIAL`, one named pair).
The colour says whether it is good or bad for the household (`Tone`), never up or down: more spending or tax is unfavourable, more pay, saved or investment income favourable.
The savings rate, net worth's other parts and other income are never coloured.

No statement starts before the year the viewer's statements begin in, nor before 1900 (`disclosure.statements.FLOOR`): such an ask is missing, as is one that starts after the books' own day.
The floor is of what the viewer reads, so whether a statement is there says nothing of when anyone else's history begins.

### The other views

- The income statement is the income and expense account tree for the period, with a cell for each calendar month, quarter or year the period touches, cut to it, and a total, and beneath it spending and tax by kind, column by column (`IncomeStatement.totals`); a category that comes to nothing over a span is left out of it; each column says what of it rests on rows nobody has reviewed yet, at their suggested category, and how many nothing has placed.
- The balance sheet is every account at the start of the period beside its end, grouped Cash, Investments and Other assets, then Credit and Other liabilities, with cost and gain not yet realised as Holdings gives them, from the same lots (`valuation.holdings.Holding.cost`): none where a security is held with no lot, and no gain where it has no price; an account whose history begins inside the period says from when.
  Its net worth at market value is Overview's, for the same parts and day.
- Year by year is net worth, pay, tax, spending, saved from work and the savings rate for every year the books hold.
  A year is sparse when the books hold fewer than half its months in full, of those it has had so far for the year in progress (`SPARSE_SHARE`).

### What it costs

The ledger is read once a recompute, in `books.compute`; every ask reads it, and none reads the entries.

Measured over ten invented years, 18,500 transactions and 104,360 daily prices of forty securities, on a laptop: the price table takes 0.28 s to read and 3.2 MB to hold, and reading the ledger 0.23 s and 6.7 MB, each once a recompute.
Then a summary's year so far takes under a millisecond, a 35-column income statement 5 ms, and year by year over eleven years 3 ms.
On the sample (972 entries), reading the ledger takes 11 ms a recompute, and each statement asked through the doorway 1 to 2 ms.
