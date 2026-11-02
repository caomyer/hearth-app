# Reporting that answers questions, and lets you ask your own: a plan

This is an assessment, a design and a set of mockups for review, not an implementation.
No application behaviour changed.
The one application file touched is `src/hearth/static/design.css`, kept byte for byte equal to `design/hearth.css` as a test requires; it gains new classes only (section 14b), which no template uses yet.

Open `screens/reporting-household.html` first, then follow its links.
Every mockup is listed in `index.html` under "Reporting and Explore".

## The ask, once

The captain, after using hearth on three live connections and thirteen confirmed accounts: the reporting side is not helpful; show cash flow per month and spending by category, like fava; and let a member play around with it to get insight into their own finances.

That is three asks, and the third is the one that decides the design.
A fixed report answers the questions its designer thought of.
Playing around means asking one the designer did not, and getting an answer, and then asking the next one.
The audience is a household member who has never kept a ledger, so the words are household words, and the first visit cannot be a blank query box.

## Where the reporting is weak today

Grounded in three things I ran, and the code I read behind them.

- **The app, as three members.** `uv run hearth demo`, signed in as Avery (admin), Blake and Casey, on Nov 1, 2026, in Chrome.
  Then Casey turned every sharing switch off, and Avery turned on "Spending by category", and I looked again as Blake.
- **fava 1.30, on the same books.** The demo's `books.beancount` (invented), served by `uvx fava`; its income statement, an expense account's page and a payee filter.
- **The code.** `src/hearth/figures.py`, `src/hearth/disclosure/household.py` and `people.py`, the templates, `docs/dashboards.md`, `docs/reports.md`, `docs/privacy-boundary.md`.

### 1. The reporting only ever looks at the month in progress

Every spending and category figure on Household, You and a member's page is "this month, to today".
On Nov 1 that is one day.
As Avery, the Household page read "Spent, Nov 1–1: –, History incomplete", "Income, Nov 1–1: –", and the whole "Where November went" panel was one line: "History incomplete."
As Blake, whose own card was current, You read "Spent, Nov 1–1: $53, $34 over typical by the 1st": a single purchase on the first morning of the month, reported as overspending.
There is no way to look at October.
The only page in the app with a month picker is an account's own page (`/accounts/{id}?month=`), which lists that one account's transactions.

So the most useful moment to read a household's money, the week after a month closes, is exactly when the app shows least.

### 2. The data is computed and thrown away

The brief's hypothesis holds, and it is stronger than stated.
`household._spending` calls `figures.month_flows` for each of the last twelve full months, which returns spending by category and income by source for every part, and then keeps only `spent` per month, to draw twelve grey bars.
`statement.income_statement` (income, spending by category, net and savings rate, by month) is called by no page at all.
`typical`, `pace`, `fixed_categories` and `category_payments` exist, and are used only for the current month's panel.
What the household needs to answer "where did October go?" is already computed on every page load of Household, and discarded.

### 3. Cash flow is half drawn

The monthly chart draws money out and nothing else.
Money in is one figure for the month so far, and "Saved in October" is one percentage, on a different month from the figures beside it.
With defaults (income private), the savings figure read "–, Not shared by Blake and Casey" for Avery, and "34% of income, without Avery and Casey" for Blake: the same household, two different savings rates, neither of which is the household's.
"Are we saving anything?" has no page.

### 4. Nothing is a door

The category rows, the month bars and the net worth points are not links.
The bar chart's bars carry tooltips, but choosing December does not show December.
A question cannot lead to the next question, which is the one thing the captain named fava for.

### 5. Sharing a richer facet buys the viewer almost nothing

With Avery sharing "Spending by category", Blake's view of Avery's page was unchanged on Nov 1: the category panel renders only when the current month is covered and has rows (`member.html`, `m.totals.covered and (m.categories.rows or m.categories.fixed)`).
Even mid-month, it shows one month to date, with no typical and no "over" note unless Blake may also see Avery's transactions.
A member who chooses to share more gets nothing back for it on the other side.
The gap between owner and non-owner is real and right; but today the non-owner's side of it is nearly empty, even where sharing allows more.

### 6. One stopped card blanks the household for everyone

Avery's Cedar Card stopped updating after Oct 20 (the demo's expired connection).
As Avery and as Blake, the Household spending panel read "Not known yet" for both October and November, and the category panel had nothing at all.
Blake's own and joint accounts were complete for October.
Coverage is right to be strict: a month the books have not reached must never read as a quiet month.
But the household view has only one figure per month, so one part's gap takes every other part's complete month with it.

### 7. A member who shares nothing is handled well; one who shares a little is handled badly

Casey with every switch off read, on Blake's Household page, "Household net worth, without Casey ... + Casey, not shared", and on Casey's own page a single clear "private" block.
That is the house pattern working.
The weakness is in between: there is no surface where a viewer meets the parts of another member's money that member chose to share, except the current month's top line.

### 8. What fava does on the same books, and why it is not the answer either

fava on the demo's books showed the whole household's September at once: income `-9300.00` (beancount's sign convention), "Net Profit `-4463.35`" (which is money left over, shown negative), and every member's salary line by name.
Its Dining page charts a running "balance" of dining since January, rising to $5,052: true in the ledger's terms and meaningless to a household.
A payee filter for "Lantern" matched both "Lantern Noodle Bar" and "Paper Lantern Goods", across every member's rows.
fava is excellent at the thing the captain asked for, the next question; it also assumes one viewer who may see everything, and speaks the ledger's language.

### In one sentence

Hearth's figures are right, private and honest, and almost none of them can be read for a whole month, compared, or followed.

## What the reporting should answer

Ranked by how often a household asks, how much a good answer changes what they do, and whether hearth can answer it honestly from what it has.
A design that answers everything answers nothing, so only the first five get a place on Household.

| Rank | The question, in a member's words | Answered by | Today |
|---|---|---|---|
| 1 | Where did our money go last month? | Household, "Where October went"; Explore by category | Only the month in progress |
| 2 | Are we saving anything? | Household, "Left over in October", the first panel; Explore, money in and out | One percentage, partial |
| 3 | What changed? Is anything unusual? | Household, "What changed"; every row's typical tick | Current month only, and only with transactions |
| 4 | What is about to be charged? | "What is about to be charged"; Household, "Coming up" | Nothing |
| 5 | How are we doing this month? | Household, "November so far", from the 8th | Yes, too early and too prominent |
| 6 | How much do we spend on X, and is it going up? | Explore, any category over time, by who and by place | Nothing |
| 7 | Where exactly did that go? Which payments? | Explore, the end of each drill | One account's month only |
| 8 | Can we afford this? | "Before your next pay"; a typical month's left over | Nothing |
| 9 | Are we richer than a year ago? | Net worth, unchanged | Yes |

"Can we afford this?" is ranked eighth on purpose.
Hearth knows what has happened, the bills that recur, the next expected pay and the balance of the viewer's own accounts.
It does not know what else will be spent, so it answers the honest part ("$195 of bills before your pay on Nov 13, against $3,412.60 in checking") and never shows a "safe to spend" figure it would have to invent.

## The proposal

Three changes, in the order they matter:

1. **Household reads the last complete month, and every figure is a door.**
2. **Explore: a question written as a sentence with blanks**, every answer drillable, every part of the household in at the detail its owner shares.
3. **What is about to be charged**: recurring bills, found from past payments.

### Household, redesigned (`screens/reporting-household.html`)

From top to bottom:

- **Household net worth**, the hero, unchanged in substance from the approved design: the one figure at display size, its change, its as-of time and who holds what.
- **Left over in October**, the first panel directly beneath it: money in, less money out, equals what was left, with the delta from a typical month.
  All three cover the same parts, so they add up and say "without Jordan" once, because Jordan keeps income private.
  It is a panel, not a second hero, so the month's answer is high on the page without a display-size figure that, under default sharing, usually reads "without" someone (decision 2, below).
- **Money in and out, each month**: twelve months of paired bars, with what was left over as a line, the chosen month outlined.
  Every month is a link into Explore.
- **Where October went**: categories for the whole month, each against a typical whole month (the middle of the six complete months before), with "above a typical month" notes.
  Each row is a link.
  A member who shares only a total is one hatched row, as today.
- **What changed**: up to four sentences, each with its delta and its reason, each a link to the view that proves it.
  Places and payments are named only on parts whose transactions the viewer may see.
- **Coming up, the next 14 days**: regular bills on accounts the viewer can see into.
- **November so far**: small, and no comparison with typical before the 8th.

**The month that leads.**
The last complete month leads every day of the month, not only in its first week.
A household reads its money like a statement; the month in progress is an "is anything going wrong?" glance, which is what the small panel is for.

### Explore (`screens/explore.html` and the states)

The interaction model, stated once.

**The question is a sentence.**
"Show [Money out] for [Everyone you can see] (on [Dining]) by [Category] over [The last 12 months]", with "Compare with [A typical month]" and "Show [Months | Quarters | Years]" beneath it.
Each blank is a short, fixed list in household words:

| Blank | Choices |
|---|---|
| Show (what) | Money out, Money in, Money in and out, Left over |
| For (whose) | Everyone you can see, You, You and joint, each member by name, Joint accounts |
| On (filter) | A category, a place, an account; added by drilling, or chosen |
| By (group) | Category, Who, Place, Account, Month |
| Over (when) | The last 12 months, this year, last year, the last 3 months, any single month |
| Show (grain) | Months, Quarters, Years |
| Compare with | A typical month, The month before, A year earlier, Nothing |

It is a GET form.
The address is the question, so Back works, a view can be bookmarked, and a member can send one to another member, who sees it at their own grain.
There is no JSON: the server renders the answer.

**What a member can do.**

- **Slice** by whose money: everyone, one member, joint, "you and joint".
- **Filter** to a category, a place or an account, alone or together.
- **Pivot** by changing "by": the same money by category, by who, by place, by account or by month.
- **Change the time** to any month, quarter or year the books hold, or the last 3 or 12 months.
- **Compare** with a typical month, the month before, or the same month a year earlier.
- **Drill** by choosing any figure: a month bar narrows "over" to that month; a category row narrows "on" to that category; a pivot cell narrows both; a place narrows to that place.
- **Undo** a step: each step is a chip in the trail, and its "×" takes back that step and every step after it.
- **Start from a question**: four starter links under the sentence ("Where did October go?", "What do we spend eating out?", "Are we saving anything?", "What is about to be charged?"), so the first visit is never a blank form.

**What happens at the end of each drill.**
Drilling narrows in a fixed order, the order a person asks in: everything, then a category, then a month, then a place, then its payments.

1. `screens/explore.html`: money out, everyone, by category, by month.
   A chart by who above a table of categories against months, shaded by how unusual each month was for that category.
2. `states/explore-2-category.html`: one category over the year, by who, with its places beside it.
3. `states/explore-3-month.html`: that category in one month, by place, each place against its own typical month.
4. `states/explore-4-rows.html`: the payments.
   **Every drill ends at payments**, and only for payments the viewer may see.
   There the member can recategorise a payment through the review desk, and see that place's own history.
   For anyone else, the drill ends one step earlier, at a hatched row carrying the amount they may see; the payments page, asked for by address, answers exactly like a page that does not exist.

The other states: `states/explore-cash-flow.html` (money in and out), `states/explore-as-sam.html` (the same surface as another member, and a member who shares nothing), `states/explore-incomplete.html` (a stopped card, seen by its owner and by Sam, and a member too new for a typical).

**Who is in this view.**
Every Explore page carries a panel naming each part of the household and the detail it is in at: "Every category, place and payment", "By category", "One row: by category only", "Not in this view", "Shares nothing with you".
Its words depend only on what each person shares, never on the data.

### What is about to be charged (`screens/coming-up.html`)

Regular bills, found without setup: the same place at about the same amount on a steady cadence (monthly or yearly), at least three times.
The next 30 days, a typical month's total, "before your next pay", and what changed this year (a price rise, a new subscription, one that stopped).
Only on accounts the viewer can see into: a bill's day and amount is a transaction.

### What I took from fava, and what I refused

| fava | Taken | Why |
|---|---|---|
| A filter bar at the top of every report, whose parts compose | Yes, as the sentence | The single best idea in fava: every report answers the same question, narrowed the same way |
| Every chart and table entry is a link that narrows the filter | Yes | It is what makes a question lead to the next one |
| Monthly "Changes" table: accounts down, intervals across | Yes, as the pivot | The densest honest view of a year; shaded per row so a steady bill stays pale and a big month stands out |
| Interval switch (yearly, quarterly, monthly, weekly, daily) | Months, quarters and years only | Weeks and days are when money went out; for another member's part that is transaction detail |
| The URL is the state | Yes | Back, bookmarks and sending a view to another member all work, with no client state |
| The journal at the end of a drill | Yes, for payments the viewer may see | The end of a drill should be the evidence |
| Query (BQL) | Refused | An arbitrary query can be narrowed until it isolates one transaction, and no gate can check a query it did not anticipate |
| Account filter by regular expression | Refused | It names ledger accounts, which are private to their owners and are accounting vocabulary |
| "Filter by tag, payee, ..." expression syntax | Refused, replaced by choosing | A syntax is a wall for someone who has never kept a ledger; tags do not exist in hearth; a payee is transaction detail |
| Income negative, "Net Profit" negative | Refused | Money in is positive and "left over" is what a household calls it |
| A running balance chart for an expense category | Refused | "Dining balance: $5,052" is true of the ledger and means nothing to a household |
| Treemap and sunburst of expenses | Refused | Area is poor at comparing close values, and the system already left pies behind |
| Trial balance, equity, at cost or at market | Refused | Ledger mechanics, not household questions; Net worth already values at market with provenance |
| One viewer who sees every member's rows | Refused | It is the assumption hearth exists to replace |

## Disclosure

This is the whole difficulty, and it is where an exploration surface leaks: the member choosing the slice is also choosing what the aggregate reveals, and a free pivot over a shared total can be narrowed until it isolates one private transaction.

### The rule: a lens is resolved per part, before anything is added up

I weighed three approaches.

1. **Refuse a slice whose result set is too small to be an aggregate** (a minimum count, k-anonymity style).
2. **A fixed vocabulary of named slices**, rather than arbitrary query.
3. **Gate each dimension as its own facet.**

I chose 2 and 3 together, and rejected 1.

**Every dimension and every filter in the vocabulary needs a facet, and the facet is asked of each part separately.**

| Asked for | Needs, on each part |
|---|---|
| Money out, by month | Monthly spending total |
| Money in, by month | Monthly income total |
| Money in and out, left over | Both totals, on the same part |
| By who | The measure's total |
| On or by category | Spending by category |
| On or by place | Transactions |
| On or by account | Transactions |
| By quarter or year | The measure's facet (a sum of whole months) |
| The month in progress, to today | The measure's monthly total facet, as today |
| Any other span shorter than a month, or by week or day | Transactions |
| Money in by where it came from | Transactions |
| The payments at the end of a drill | Transactions |
| Typical for a whole month (category or total) | The facet of what is typical |
| Typical by this date, a fixed bill's paid day | Transactions, as today |
| Regular bills, and their changes | Transactions |

Each part then enters the view in exactly one of three ways, decided by a mechanical rule that looks only at the facets:

- **In full**, when the viewer may see every facet the lens needs on that part.
- **As one labelled row**, when the viewer may see every facet the lens's *filters* need, but not what its *grouping* needs: Sam, who shares categories, filtered to Dining and grouped by place, is one row, "Sam, by category only", carrying Sam's Dining total, which the category facet already shares.
- **Out, and named**, when the viewer may not see what a filter or the measure needs: Jordan, who shares a total, filtered to Dining, is "Not in this view"; a member who shares nothing is "without Jordan" on every total.

Only then does disclosure add the parts up.

**Why this makes isolation structurally impossible.**
Every number on an Explore page is a sum of cells, and every cell is one part's figure at a grain the viewer may see on that part alone.
Any combination of figures a viewer can see, including two slices subtracted from each other, is therefore a combination of cells they could already see one by one.
No choice of slice, filter or pivot can produce a cell finer than the owner shares, because the cell is never computed: the gate is asked before the figure, as everywhere else in `hearth.disclosure`.
An aggregate is never more disclosable than its least disclosable component, because each component is checked on its own before it can be in an aggregate at all.

**Why not refuse small results (approach 1).**
A refusal is itself an answer: "too few to show" tells the viewer the slice holds fewer than k payments, which is information about private data, and its wording would depend on the data.
A threshold does not compose: two permitted slices of size k can differ by one payment.
In a household of two or three people, any k large enough to matter would refuse most honest questions.
And it is the wrong model: sharing in hearth is a choice of what to share, not a promise of statistical noise.

**Why a fixed vocabulary on its own is not enough.**
A named slice such as "Dining by place" still needs deciding per part; without per-part gating, a fixed slice over a household total would mix Sam's place detail in.
The vocabulary keeps the surface small enough that every route can be taught to the privacy crawl; the per-part gate is what makes each one safe.

### Each new observable

| Observable | Who sees what | Notes |
|---|---|---|
| A whole past month's spending, income and left over per part | Whoever may see that total, for every month the books hold | Totals per month are already drawn for twelve months today |
| A past month's categories per part | Whoever may see that part's categories | Approved: sharing categories reaches the history, and the sharing page says so (decision 1, below) |
| A whole-month typical, per category | Same as the category | It is a function of whole-month category figures only |
| "Above a typical month" on a whole month | Same as the category | Unlike typical by this date, it reads nothing off when money went out |
| What changed, by category | Same as the category | Its reason sentence names a place or payment only on parts whose transactions the viewer may see; otherwise it names the category alone |
| By place, by account, money in by source | Transactions on that part | Otherwise one labelled row with its amount, or out |
| A payment list at the end of a drill | Transactions on that part | Anything else answers `Concealed`, like a missing page |
| Regular bills, next expected day and amount, price changes | Transactions on that part | The footer names whose bills are not there, in words that depend only on sharing |
| Before your next pay | The viewer's own parts only | Uses the viewer's own balance and pay; never another's |
| The options in each blank | Built from what the viewer may see | The category list from parts whose categories the viewer may see; place suggestions from parts whose transactions they may see; the member list from membership, which Household already shows |
| Who is in this view | Facets only | Same words whatever the data; whom it names depends only on membership and sharing |
| The address (query parameters) | Recomputed from the gate on every request | A tampered address asking for a grain a part does not share gets that part at the grain it does share; an invented category or place gets the same answer as one that exists only in a hidden part |
| Unreviewed share of a slice | As today | Shown for parts the viewer may review or see the transactions of; otherwise the fixed caveat |
| Coverage of a slice | Month-level, viewer-independent, per part | See below |

### Coverage, and what changes about it

Coverage stays month-level and the same for every viewer (`figures.covers`, `flows_cover`).
A month not covered is drawn as an outline at the height of what is known, labelled "Not known", with no value in its tooltip, and is never a short bar.

What changes is that a household figure no longer has to go dark because one part is not whole.
Explore and Household show each part's month by name: whole parts with their figures, the part that is not whole as "Not known yet", the total as "Not known yet".
Naming whose month is incomplete goes with that part's spending total: Avery's member page already tells Blake that Avery's month's history is incomplete, so this discloses nothing new.
The day and the reason ("Rewards card stops Oct 20") go only to the owner, through `may_see_why_unknown`, as today.
`states/explore-incomplete.html` shows both views.

### Where it is enforced

- A new module, `hearth.disclosure.explore`, receives the gate like the others, resolves a lens to one of the three entries per part, and asks `figures` only for cells it has been allowed.
  No page queries the ledger; pages render the view it returns.
- A new helper in `hearth.access`, asked only through the gate: the facets a lens needs, from its dimension and filters, so the mapping above lives in one place.
- `tests/test_privacy_boundary.py` learns the new routes (`/explore`, `/bills`), with every value of every blank, tampered and invented values, and every viewer; it must fail if any response carries a datum the viewer may not see, and if any datum the viewer may see never appears.
- `tests/test_concealment.py` gains: a payments page for another member's place is byte-identical to a missing one; a filter on a category only a hidden part has answers byte-identically to an invented category.
- A new adversarial test, the differencing attack: for every pair of permitted lenses, the difference of their totals is a sum of cells the viewer may see on their own.

## What this reuses and what is new

| Piece | Reuses | New computation |
|---|---|---|
| Household's last-month report, in, out, left over | `month_flows` (already computed for 12 months on every load), `flows_cover`, `typical` | Keep the categories it already computes; left over over the same parts |
| Whole-month typical per category | `typical`, `past_months`, `_covers` | None beyond calling them for a whole month |
| What changed | `month_flows`, `typical`, `CategoryRow.over_by` thresholds | A ranking of the largest whole-month changes; a reason sentence from the largest payment or new place, on visible parts |
| Explore, by category, who, month | `month_flows`, `spending_by_category`, `income_by_source`, `parts`, `Unreviewed` | The lens resolver; quarter and year sums of months |
| Explore, by place and account | `lines` | Grouping `lines` by payee and account, for visible parts |
| The payments at the end of a drill | `lines`, `days`, the review desk | A filter of `lines` by category, month and place |
| Coverage per part on household figures | `covers`, `flows_cover`, `may_see_why_unknown` | Showing parts by name instead of one figure |
| Regular bills | `category_payments`, `fixed_categories` (monthly, by category) | Recurrence by place: cadence, usual amount, next expected day, changes, stopped |
| Before your next pay | Account balances, income lines | Next expected pay, from recurrence of income lines |
| Pages, forms, charts | `design/hearth.css` components | Section 14b classes: `.lens`, `.trail`, `.chip`, `.inview`, `.pivot`, `.changes`, `.inout`, chart fills |

Nothing here needs a new kind of figure from the books, a new store, or a change to how books are recomputed.
It is all within one recompute's entries, read through disclosure.
Recurrence is the only genuinely new analysis.

Performance is a real cost.
Today a page computes at most thirteen `month_flows`; Explore over a year by place is a scan of a year of `lines`, every request.
Each request is still one viewer, one lens, over in-memory entries, so I expect it to be fast enough at household scale; if not, per-recompute caching of per-part monthly cells is the obvious step, and it keeps the gate where it is because cells are cached per part, not per viewer.

## Stages

Each stage ships on its own, is useful on its own, and teaches the privacy crawl its new routes in the same change.

1. **Household reads last month.**
   Net worth stays the hero; beneath it, "Left over in October" with money in and out, "Where October went" against a typical whole month, and "November so far" with no comparison before the 8th.
   Category history for other members begins here, and it is approved: sharing "Spending by category" covers every month the books hold (decision 1).
   The sharing page's description of that facet must state the span in the same change, for example "Every month hearth holds, back to August 2025".
2. **Explore by category, who and month, over whole months.**
   The sentence, the trail, the pivot, "who is in this view", links from Household into it.
   Only the totals and category facets are involved, so the per-part resolver is exercised in its simplest form.
3. **The drill to places and payments.**
   By place, by account, the payments at the end, recategorising from there; the labelled row and "not in this view" for parts that do not share transactions.
   The differencing test lands here.
4. **Money in and out, left over, sources.**
   The cash flow view, money in by source on visible parts.
5. **What is about to be charged.**
   Recurrence by place, the bills page, "Coming up" on Household, "What changed" gaining price rises and stopped bills.
6. **Coverage by part.**
   Household and Explore show whole parts by name when one part is not whole.

## Decisions, recorded

The four questions this proposal raised, with the answer given and who gave it.

1. **Sharing "Spending by category" covers its history: approved by the captain, as proposed.**
   Today the facet is only ever drawn for the month in progress; this design draws it for every month the books hold.
   Someone who checked daily could already collect each month's final figures, and monthly totals already reach twelve months back, so it reveals nothing in principle that the facet did not already allow over time.
   But a member who turns categories on today shows two years of backfill tomorrow, so the sharing page's description of the facet must say so ("Every month hearth holds, back to August 2025").
   The alternative, "from the day it was turned on", was not chosen.
2. **The Household hero: declined by the captain; net worth stays the hero.**
   The proposal made "left over last month" the hero.
   Household net worth stays the one figure at display size, as in the approved design, and "Left over in October" is the first panel directly beneath it.
3. **Income stays private by default: settled by firstmate on the captain's behalf, as proposed.**
   With default sharing, the household's left-over figure covers only the members who share income and the joint accounts, and says "without" each member who does not.
   The default does not change.
4. **The month that leads: settled by firstmate on the captain's behalf, as proposed.**
   The last complete month leads Household every day of the month, and the month in progress is compared with typical only from the 8th.

## What I rejected, and why

- **A query box (fava's BQL, or a simpler filter language).**
  It is the most powerful exploration there is and it cannot be gated: a query the gate did not anticipate is a query it cannot check, and a filter expression can be narrowed to one row.
  It is also a wall for the reader this is designed for.
- **A minimum-size refusal.**
  Rejected above: the refusal is itself disclosure, it does not survive differencing, and in a household of two any useful threshold refuses honest questions.
- **Treemaps or sunbursts for categories (fava's income statement).**
  Area does not compare close values, and the eye cannot tell $480 from $570; bars against a typical tick can.
- **A budget page.**
  Budgets are the most common household finance feature, and they ask a new user to predict their spending before seeing it.
  "Above a typical month" gives most of the value with no setup, from what actually happened; a budget can come later on top of it.
- **Showing a part's figure as a range, or rounded, so it can be mixed into a finer slice.**
  A range or a rounding of a private figure still discloses it, and mixes an uncertain figure into a sum that then cannot say what it is.
  The labelled row is exact and discloses only what is shared.
- **A separate "Reports" section of fixed reports (income statement, balance sheet).**
  It answers the designer's questions, which is the captain's complaint; and its vocabulary is the ledger's.
- **Making Explore the household's front page.**
  A blank question is a poor first screen for someone new; Household answers the top five questions and every answer opens Explore.

## The weakest points of this proposal

1. **The pivot is the least household-friendly thing in it.**
   Fifteen rows by twelve months is the right tool for the captain and may be too much for a member who has never kept a ledger.
   It is below the chart and the finding, not instead of them, and the four starter questions route around it; whether that is enough needs a real member in front of it.
2. **"Typical" is still doing a lot of work on a baseline nobody has validated** (`rationale.md`, weakest point 1).
   This design leans on it harder: every category, place and bill compares with it.
   A whole-month median is more robust than typical-by-date, but annual bills and a single trip still distort it, and three months is a thin minimum.
3. **Recurrence detection is new analysis with no measured accuracy.**
   On the captain's real feed, how often a "bill" is a coincidence (two coffees at the same price) or a real bill is missed (a utility that varies by 40%) is unknown.
   It needs the same treatment transfer pairing got: measured read-only on the real feed before it ships (stage 5).
4. **A labelled row can still carry a surprising amount of meaning.**
   "Sam, by category only: $186.40" in a Dining-by-place view is exactly Sam's shared category figure, so it is not a leak.
   But a viewer who sees it every month next to their own places learns Sam's dining trend in a form Sam may not have pictured when turning categories on.
   It is the same information as the category facet; it is a more vivid presentation of it, and the sharing page's preview should show it.
5. **The household view still has no figure for a month one part has not reached.**
   Showing the other parts by name helps, but the household total for that month stays "Not known yet" until the connection is fixed.
   That is the right answer, and it will still feel like a gap.
6. **Performance at two years of history by place is unmeasured** (see "What this reuses").

## Found along the way

- The demo's Household on the 1st of a month shows almost nothing below the net worth panel, for every member (weakness 1); this is what the captain would have seen on any 1st.
- Blake's You page on Nov 1 said "$34 over typical by the 1st" for a single $53 purchase: typical by date is too eager in a month's first days.
  Stage 1 proposes no comparison before the 8th.
- "Saved in October" on Household sits beside figures for November, and reads differently for each viewer under default sharing (weakness 3).
- `statement.income_statement` is unused by any page (`docs/reports.md` describes it as what "the dashboards ask of the ledger").
- `design/hearth.css` still lacks `.comp > .who-3` and still has the unreachable `.chart .hit:hover + .bar` (`docs/dashboards.md` already reports both); not changed here.
