# The dashboards: how they are built, and the calls made along the way

The pages follow `design/` as a specification of behaviour and of look.
This document records how they get their figures and every decision the design did not settle, each one easy to overturn.

## Where the figures come from

`hearth.books` recomputes the household's books, hearth's own over a ledger it inherits if there is one (`docs/books.md`): at start-up, every morning at 6:00, when someone presses "Recompute now", when a member confirms where a connected account goes, and after every sync, the morning's or one run by hand with `hearth plaid sync` (`hearth.refresh`).
A serving hearth also recomputes within seconds of a change it did not make to what its books are computed from, such as a sync or holdings fetched by `hearth plaid` in another process, which cannot reach the books the pages show, only the two databases both read (`books.standing`, `Bookkeeper.recompute_on_change`).
A page never recomputes, so every figure has an as-of time, which every page states.

A recompute that cannot trust the ledger establishes nothing.
Beancount reporting an error, or a balance assertion the postings do not reproduce, keeps the previous books on screen, labelled with their time, beside what went wrong.
The page a member lands on once every account is confirmed says whether the books took them in: while the recompute runs it says so, and when it fails it shows the failure and the time of the figures still shown, never that the accounts are in the figures.
Before the first recompute finishes, pages show skeletons in the shape of what is coming and poll quietly until it lands.
A household with no account in the books yet sees no figure at all on Household and Net worth, but a page that says nothing is connected and offers to connect a bank: a total over nothing is not a total of zero.
A figure that includes an account whose balance the books do not hold reads "Not known yet" rather than counting it as zero (`docs/books.md`).
A connected account's balance is what its bank reports from the day it is filed; while its history is still arriving, its months read as not known, its balance does not.

Prices come from the stored price table (`docs/valuation.md`), from the source and from members who entered one by hand.
A position with no price is never valued at zero: every total that includes it says "at least", and the gap is named.
A stale price is used, and marked in amber with its close day and "may be off", three ways and never by colour alone.

## Calls made while building them

**Income is private by default.**
Firstmate reversed its earlier acceptance on 2026-10-18: the captain approved "the top two shared, everything else private", and a third default-shared facet exceeds that, for the most sensitive number after net worth, and would apply silently to every member added later.
Household income, and what the household left over, are labelled "without" each member who has not turned income on, through the same partial figures as net worth.

**Partial household figures are shown, labelled "without".**
Decision 3, as firstmate amended it on 2026-10-18, shows the known part with the withheld part attached to the number, never a range.
Every part of such a figure is one the viewer may see on its own, so it reveals nothing that is not already on the page.

**Spending nobody can be said to have made is counted in nobody's figure.**
Spending through an account no member owns, or through several owners' accounts in one transaction, belongs to no part.
Showing its amount could show part of someone's private spending, so the household panel says only that such spending exists and is not counted.

**Day-level detail needs the transactions facet.**
The day-by-day pace chart and "typical by the 18th" show when money went out, which is finer than "one number a month".
On someone else's page they appear only when that member shares transactions.
The category panel of the month in progress follows the same rule: per-category typical ticks, "over typical" notes and fixed bills with their paid day appear only when the viewer may see the transactions of every part the panel covers, and otherwise each category shows only its amount so far.
A whole past month against a typical whole month reads nothing off when money went out, so "Where October went" draws its ticks and "above a typical month" notes for anyone who may see the categories (`design/reporting.md`, decision 1).
Firstmate made this call on 2026-10-18: a single payment's day and amount is an individual transaction, which the category facet promises not to show.

**"Typical" needs three complete months.**
It is the median of up to six covered months, and is not shown at all with fewer than three.
A month whose history starts late is not covered, so a new account or a gap in the books never drags typical down.
Nor is a month that ended before the history of every account that could move the figure began (`figures.flows_cover`): the books hold nothing of it, which is not a month of $0, so it is drawn as "Not known yet" and never taken into typical.
A category paid once a month at a steady amount, such as a mortgage, is shown as a fixed line with the day it was paid, not as a bar against a pace, so paying it on the 1st is never "over typical".
A fixed category paid more or less than once this month is shown as an ordinary category row, so its spending is always counted.

**Coverage is shown per account, by source.**
The accounts table lists each source's span ("Statements: Jan 3 to Jun 5"), so a ledger whose history changes source part way through the year shows the seam instead of smoothing it.
Money in and out for months the books do not fully hold is drawn in outline, off the left-over line, and labelled "Not known yet", with no figure in its tooltip.

**Connections come from the Plaid connections hearth holds.**
`hearth.connections` describes each member's connections and whether they still update, read from `hearth.plaid`'s registry (`docs/connections.md`).
How current each connection is decides which months its accounts' figures are complete for, and where their data stops (`docs/real-ledger.md`).
The demo connects its members through the stand-in for Plaid, with one card that wants its member to sign in again, so the state can be seen: its accounts' bank-feed rows are booked from their connection, so that card's data stops on the day it stopped.

**Review rows can be decided one at a time.**
Groups with one suggestion are accepted in one decision, as designed.
A group with no suggestion can be settled row by row: accept or change the category, or exclude it as not a transaction.
The category list offered comes from the viewer's own rows only, so it never names another member's categories.
The queue at backfill size, its orders, filters, keyboard and reminders are in `docs/review.md`.

**The household page shows the members' totals, not their account counts.**
The design shows "3 institutions, 5 accounts" for everyone; a count of someone's accounts is shown only to those who may see that member's balances.

**Household reads last month (`design/reporting.md`, stage 1).**
The last complete month leads every day of the month (decision 4), beneath the net worth hero (decision 2): "Left over in October" with money in, money out and its difference from a typical month, then "Where October went" and a small "November so far".
Each is built in `disclosure.household` from the twelve months of `figures.month_flows` the page already computed, each part's figures asked of the gate before they are added.
The calls the plan and its mock did not settle:

- Money in, money out and left over, and the chart of each month beside them, cover the parts whose spending and income totals the viewer may both see; the twelve months are drawn over the same parts as the month that leads, so the chart never mixes in a part the figures leave out.
  A part sharing only its spending is named once as "without", and its spending is under "Where October went".
  Money out is spending; tax is never spending, and is its own figure between money in and money out, of the parts whose categories or transactions the viewer may see.
  Any other part's tax stays inside its money out, unlabelled, as before tax was its own figure, so money out, what was left and its share of money in are the same whoever looks; the panel says, naming nobody, that any tax of those whose categories are not shared is in money out.
  The chart's bar of money out is then tax and spending together, so its three still add up.
- A typical left over, and a typical whole month for a category, is the median of the covered months among the six before the month that leads, as everywhere else, with "above a typical month" at the same thresholds as the month in progress ($50 and a quarter).
  The "Other" row, everything past the eighth category, has a typical of its own, the median of what those categories added up to.
- A whole month has no fixed-bill line: a bill paid once is an ordinary row of the whole month, and its paid day is transaction detail.
- Coverage stays one answer per figure, the same for every viewer: when any part on "Where October went" is not whole for the month, the panel reads "Not known yet", whoever is looking (stage 6 names parts instead).
- Nothing in the month in progress is set against typical before the 8th, on Household or on You (`figures.COMPARE_FROM_DAY`); from the 8th, Household compares spending so far with what a whole month typically totals, a sum of monthly totals, so it needs no finer facet.
- The mock draws the net worth hero without its month-end line; the line stays, because "Net worth stays the hero" is the captain's decision, the plan calls the hero unchanged in substance, and nothing else in the app draws net worth over time.
- The twelve grey spending bars and "Saved in October" are gone: what was left, as a share of money in, is the third figure of the left-over panel, over the same parts and month as the other two.
- Rows and months are not links yet: Explore, which they open, is stage 2.
- `reports.statement.income_statement` is not used: it adds up the whole household at once, where every figure here must be a sum of parts the viewer may see one by one; `month_flows` already gives the same figures by part.

**The sharing page says how far back categories reach.**
Its description of "Spending by category" names the first month the books hold of any account the member owns, alone or jointly (`Disclosure.history_from`), so turning it on says it shares that whole history.

**Static files carry a digest of their contents.**
Each page links its stylesheets and scripts with `?v=` and the start of their SHA-256 (`templating.asset`), so a browser that cached an earlier hearth's styles fetches the new ones rather than drawing new markup with old classes.

## Where the design is followed but something was added

`src/hearth/static/app.css` holds the few additions the design names but does not draw, each following the system's own rules: an amber "no price" chip in the absence colour for missing data, an outlined bar for an incomplete month, the third identity hue on the composition bar, and a bottom-margin utility.

Two things in `design/` look like slips, reported rather than changed here:

- `.comp > .who-3` is missing from `design/hearth.css`, so a third member's share would draw in the first member's colour; `app.css` adds it.
- `.chart .hit:hover + .bar` never matches, because the hit area follows its bar in the markup, so bars do not highlight on hover.
