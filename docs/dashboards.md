# The dashboards: how they are built, and the calls made along the way

The pages follow `design/` as a specification of behaviour and of look.
This document records how they get their figures and every decision the design did not settle, each one easy to overturn.

## Where the figures come from

`hearth.books` recomputes the household's books from the beancount ledger at `HEARTH_LEDGER`: at start-up, every morning at 6:00, and when someone presses "Recompute now".
A page never recomputes, so every figure has an as-of time, which every page states.

A recompute that cannot trust the ledger establishes nothing.
Beancount reporting an error, or a balance assertion the postings do not reproduce, keeps the previous books on screen, labelled with their time, beside what went wrong.
Before the first recompute finishes, pages show skeletons in the shape of what is coming and poll quietly until it lands.

Prices come from the stored price table (`docs/valuation.md`), from the source and from members who entered one by hand.
A position with no price is never valued at zero: every total that includes it says "at least", and the gap is named.
A stale price is used, and marked in amber with its close day and "may be off", three ways and never by colour alone.

## Calls made while building them

**Income is private by default.**
Firstmate reversed its earlier acceptance on 2026-10-18: the captain approved "the top two shared, everything else private", and a third default-shared facet exceeds that, for the most sensitive number after net worth, and would apply silently to every member added later.
Household income and the savings rate are labelled "without" each member who has not turned income on, through the same partial figures as net worth.

**Partial household figures are shown, labelled "without".**
Decision 3, as firstmate amended it on 2026-10-18, shows the known part with the withheld part attached to the number, never a range.
Every part of such a figure is one the viewer may see on its own, so it reveals nothing that is not already on the page.

**Spending nobody can be said to have made is counted in nobody's figure.**
Spending through an account no member owns, or through several owners' accounts in one transaction, belongs to no part.
Showing its amount could show part of someone's private spending, so the household panel says only that such spending exists and is not counted.

**Day-level detail needs the transactions facet.**
The day-by-day pace chart and "typical by the 18th" show when money went out, which is finer than "one number a month".
On someone else's page they appear only when that member shares transactions.
The category panel follows the same rule: per-category typical ticks, "over typical" notes and fixed bills with their paid day appear only when the viewer may see the transactions of every part the panel covers, and otherwise each category shows only its amount so far.
Firstmate made this call on 2026-10-18: a single payment's day and amount is an individual transaction, which the category facet promises not to show.

**"Typical" needs three complete months.**
It is the median of up to six covered months, and is not shown at all with fewer than three.
A month whose history starts late is not covered, so a new account or a gap in the books never drags typical down.
A category paid once a month at a steady amount, such as a mortgage, is shown as a fixed line with the day it was paid, not as a bar against a pace, so paying it on the 1st is never "over typical".
A fixed category paid more or less than once this month is shown as an ordinary category row, so its spending is always counted.

**Coverage is shown per account, by source.**
The accounts table lists each source's span ("Statements: Jan 3 to Jun 5"), so a ledger whose history changes source part way through the year shows the seam instead of smoothing it.
Spending bars for months the books do not fully hold are drawn in outline and labelled "history incomplete".

**Connections are an interface until bank linking lands.**
`hearth.connections` describes each member's connections and whether they still update.
Until the Plaid work, `hearth serve` has none, so nothing has expired; the demo describes an expired card so the state can be seen.
Reconnecting is drawn but says it arrives with bank linking, rather than offering a button that does nothing.

**Review rows can be decided one at a time.**
Groups with one suggestion are accepted in one decision, as designed.
A group with no suggestion can be settled row by row: accept or change the category, or exclude it as not a transaction.
The category list offered comes from the viewer's own rows only, so it never names another member's categories.
The queue at backfill size, its orders, filters, keyboard and reminders are in `docs/review.md`.

**The household page shows the members' totals, not their account counts.**
The design shows "3 institutions, 5 accounts" for everyone; a count of someone's accounts is shown only to those who may see that member's balances.

## Where the design is followed but something was added

`src/hearth/static/app.css` holds the few additions the design names but does not draw, each following the system's own rules: an amber "no price" chip in the absence colour for missing data, an outlined bar for an incomplete month, the third identity hue on the composition bar, and a bottom-margin utility.

Two things in `design/` look like slips, reported rather than changed here:

- `.comp > .who-3` is missing from `design/hearth.css`, so a third member's share would draw in the first member's colour; `app.css` adds it.
- `.chart .hit:hover + .bar` never matches, because the hit area follows its bar in the markup, so bars do not highlight on hover.
