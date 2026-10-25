# Design rationale

The approved mock settled what Hearth does.
This design is about how it reads: hierarchy, density, the legibility of numbers, and the states a real household hits.
Every figure and name in these files is invented.

## What I chose

**One hero per screen, in the platform's own sans.**
Household net worth on the household page, your net worth on yours, the count waiting on the review page.
It is the only thing at display size, so the eye lands on it in under a second.
The approved mock gave four KPI cards equal weight, which is the most common way a dashboard loses its hierarchy.

**Precision that matches use.**
Totals are whole dollars; balances, transactions and anything that reconciles keep cents.
Tabular figures wherever numbers stack; proportional figures on the hero, where tabular digits look loose.
A true minus sign everywhere, never a hyphen.
The full rule table is in `system.html`, under Numbers.

**Gain and loss carried four ways.**
Sign, arrow, words, then colour.
Colour means favourable or unfavourable rather than up or down, so rising spending is red and rising net worth is green, and the words ("over typical", "since Oct 1") remove the ambiguity.
Ordinary spending is not red: most rows are spending, and painting them all red would make red meaningless.
The design system page shows the same rows with colour removed, and they still read.

**Every chart answers a question a household asks, and says the answer above it.**
"Are we on track this month?" is a cumulative pace line against a typical band.
"Where did it go, and what is unusual?" is a set of category bars, each with a tick for typical by this date.
"Are we richer because we saved or because markets rose?" is a stacked saved-versus-market bar per month.
Each has a finding written above it as a sentence with the number in it, and a table twin one click away.

**Privacy is drawn, not implied.**
On your own pages every section carries "Household sees" or "Only you".
On someone else's page, "What Sam shares" uses the exact six items from Sam's own settings, and one block says what is private, that it was never sent to your browser, and what to ask for.
Inside household charts, a member who shares a total but not its parts appears as a hatched, labelled row rather than being silently folded in or left out.

**Every absence looks different.**
Withheld by its owner is violet with a hatched edge and a lock.
Not there yet is dashed grey with the action that fills it.
Loading is a skeleton in the shape of the content.
Missing or out-of-date history is amber with a clock and a date.
Broken is a red rule with what happened and what to do.
Nothing to do is a green check and an outcome ("All caught up").

**Flat, quiet, warm.**
Warm neutral greys, one blue for interaction, panels separated by a hairline rather than shadows, a 4px spacing grid, three radii.
No web fonts, because the app runs offline on the household machine.
Dark mode is its own measured palette, not an inversion.

## What I took from the best financial products, and what I left

- From the Stripe dashboard: tabular density, muted chrome, a single accent colour that only means "act here".
- From Copilot Money: review as an inbox you clear, and grouping so one decision covers many transactions.
- From Monarch: transactions grouped under day headers, income marked and spending left plain.
- From Kubera: an as-of time and a source on every valuation.
- From the Financial Times and Datawrapper: chart titles that state the finding rather than name the axes.
- From Mercury: calm type and whole-number summaries.
- Left behind: donut and pie charts, which are poor at comparing close values.
- Left behind: financial health scores, gauges and other gamification, which turn a number the household can check into one it has to trust.
- Left behind: KPI grids of identical cards, the shape of the approved mock's dashboards.
- Left behind: numbers that count up on load, which make the most important figure unreadable for its first second.
- Left behind: confidence percentages in the review queue; a reason ("Like 2 past rulings", "New merchant") tells you what to check, "62%" does not.
- Left behind: a green brand colour, as in the approved mock, because green already means money in.
- Left behind: blurred placeholder rows for private data, as in the approved mock, because they imply the data is present and obscured, which is the opposite of how the server enforces privacy.

## Where this design departs from, or adds to, the approved behaviour

Each of these is flagged here so firstmate can overturn it in one line.

1. **Decision 3, withdrawn net worth shown as a range: amended by firstmate on 2026-10-18 to the known part.**
   An honest range has to be computed from the withheld figure, and subtracting the known part from it reveals that figure to within the range's width.
   A range not computed from it is not honest.
   The design instead shows the known part at hero size with a hatched "+ Sam, not shared" chip attached, under the label "without Sam", so it can never read as complete.
   Decision 3 was amended by firstmate on 2026-10-18 under authority the captain delegated in writing ("you can decide on my behalf because I am going to sleep").
   The captain's original criterion asked for a range.
   A range bounds the withheld value from BOTH sides and therefore reveals more about the member who withdrew than a labelled partial does, so the amendment makes the product more private, not less.
   The captain has not yet reviewed this amendment; it is listed for him in the overnight brief and he may overturn it.
   The labelled partial is strictly more private and no less clear, which is why the design differs from the recorded decision text.
   The range is never the primary presentation; `states/sharing-withdrawn.html` keeps it only as a secondary side-by-side comparison.
2. **Income is shared by its own switch, "Monthly income total", beside "Monthly spending total". Accepted by firstmate; its default reversed by firstmate on 2026-10-18.**
   The approved household dashboard shows household income and a savings rate, which cannot be computed unless each member shares income.
   The mock's sharing list had no income item, so one is added as a separate, independently controlled item rather than folded into the spending total.
   It was first accepted defaulting on, matching the approved dashboard.
   Firstmate reversed that on 2026-10-18: the captain approved "the top two shared, everything else private", and a third default-shared facet exceeds that, for the most sensitive number after net worth, and would apply silently to every member added later.
   Income now defaults off, so household income and the savings rate read "without" each member until they turn it on, never as a complete figure.
   No switch ever covers more than its label says, and there are now six shareable items.
3. **A member chooses their own password at first sign-in, on by default. Accepted by firstmate.**
   Decision 1 accepts that an admin can reset a password; it does not require the admin to keep knowing one.
4. **Joint accounts are their own part of the household total. Accepted by firstmate as the data model.**
   Member net worth excludes joint accounts, and the household total is members plus joint, so nobody has to decide how a joint account splits between its owners.
   It also structurally prevents a non-owner from deriving a joint balance by subtracting one member's figure from another's.
   Wherever a member total is shown it is labelled "excluding joint", with the joint line shown separately.
5. **Auto-accept is removed (decision 5) and "Accept all suggested" is kept but demoted.**
   It is secondary, states its count and what it leaves open, and the empty queue keeps an undo list for a week.
6. **Errors respect the privacy boundary.**
   A failed balance check names an account and a balance, so only that account's owner sees the details; everyone else sees one sentence that reveals nothing.
7. **Typical-by-date ticks and fixed bills' paid days are transactions-level detail. Changed by firstmate on 2026-10-18.**
   The household screen first drew "Fixed: Housing, paid Oct 1" and a typical-by-date tick on every category for anyone who shares categories.
   A single payment's day and amount is an individual transaction, and typical by a date reads off when money went out, which the category facet promises not to show.
   They now appear only for parts whose transactions the viewer can see; anyone else sees each category's amount so far, a fixed bill included as an ordinary row.

8. **Money hearth sees one side of: a proposal, not yet approved.**
   A transfer whose other side is at a bank not connected, a loan, or a person used to have no honest answer in Review: a category counted it as spending, and every other door was refused.
   The proposal names the other side once, as an account hearth does not see, someone else, or someone in the household; see `screens/where-it-went.html`.
   It is asked after the import, inside step 4, because only then can hearth show the transfers instead of asking the member to remember their accounts.
   A member's net worth then shows the known part, labelled with what it leaves out, with a dashed grey chip: the "not there yet" absence, deliberately not the violet of "withheld", since nobody withheld anything.
   Today one unknown balance turns the whole figure into "Not known yet"; that stays for an account about to sync, and the known part is used only for accounts hearth will never be told the balance of.
   No balance is asked for when an account is named, because a typed balance goes stale the next day and hearth cannot see it change.
   The full plan, with what each choice does to net worth, is `money-between-accounts.md`.

## The three places this design is still weakest

1. **"Typical" is doing a lot of work on a baseline nobody has validated.**
   The spending charts compare against a median of past months and a range around it.
   A new member has no past months, annual bills and holidays distort a median, and "typical by the 18th" assumes spending is spread the same way every month.
   With real data, this comparison could mislead as often as it helps.
   It needs testing against the household's actual ledger before it is trusted, and a defined fallback for the first months.
2. **The first big review is designed only at its doorway.**
   Onboarding ends with "1,118 ready, in 34 groups", but the review screen is drawn with 23 items in four groups.
   A two-year backfill is the review queue's hardest case: groups by merchant over two years, very large groups, and keeping someone motivated through a thousand decisions.
   Transaction detail, CSV upload, hand-entered prices and the "Find the $100.00" flow are also named but not drawn.
3. **Charts and phone tables are the least robust parts of the accessibility floor.**
   Chart values reach the keyboard and screen readers only through the table twin, not point by point.
   Charts are drawn at two fixed widths, so axis text scales slightly between breakpoints.
   On a phone, review rows restyle table rows as grids, which some screen readers flatten; this needs testing in VoiceOver and TalkBack.
   The member identity hues also cap at three: aqua is below 3:1 on white and relies on direct labels, and no fourth hue passes the colour-vision checks in both modes.

## How these files are built

Every screen is a standalone HTML file that links `hearth.css` and `design.js` and opens straight from disk.
`hearth.css` holds every token and component; `system.html` explains them.
`design.js` adds chart tooltips and the review-only theme switch; the sharing page has a few lines of its own to drive its live preview.
Each screen ends with numbered design notes giving the reasoning for that screen.
