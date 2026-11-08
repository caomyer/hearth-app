# Holdings, prices and net worth

`hearth.valuation` answers one question: what does the household own, and what is it worth on a given day?
Its defining property is that it never claims more than it knows.
A silently wrong net worth is the worst thing hearth could show, because its whole claim is that its numbers can be trusted.

## Where each part lives

| Fact | Lives in | Read by |
|---|---|---|
| Units held: cash, card balances, fund shares | The books: a ledger's, or hearth's own | `holdings.holdings_at` |
| Prices, with provenance | The app database, table `price` | `pricestore.PriceStore` |
| Where prices come from | A `quotes.PriceSource` implementation | `refresh.refresh_prices` |
| What it is all worth | Computed, never stored as a fact | `networth.value_holdings` |

### Holdings live in the books

The books are the source of truth for every financial fact, and the quantity of a fund someone owns is one.
A security is a beancount commodity named by its ticker.
In a ledger a household keeps, each purchase is a lot, a posting at cost on an `Assets` account; each sale reduces it.
The units held on a day are the sum of those postings up to and including that day, which is what `holdings_at` computes from entries loaded with beancount's loader.
Cash and liabilities are holdings of the operating currency and are worth exactly their units.

In hearth's own books, a brokerage account holds what its brokerage last reported, each security as units dated the day it was reported, as a bank account holds the balance its bank reports (`docs/books.md`, "What a brokerage holds").
No buy, sell or reinvestment is ever booked: Plaid's trades, when fetched at all, are kept in the feed for a ledger outside hearth and never read here, so the books say what is held from that day, never what was held before it or how it came to be.
Writing those entries is `hearth.plaid.opening`'s job; this module only reads.

The books record quantities and cost, never market value.
A price is a fact about the market on a day, not about the household, so prices live in the app database next to their provenance.

### Prices live in the app database, with provenance

Every stored price records:

- `symbol`, the ledger commodity it prices;
- `as_of`, the trading day it is the close of;
- `price`, the **raw** close, which for a mutual fund is its NAV, stored as decimal text;
- `currency`;
- `source`, the `PriceSource.name` that produced it;
- `source_ref`, the symbol as that source was asked for it (`FUNDA.US` for EODHD);
- `fetched_at`, in UTC.

A raw close is what a share was worth that day.
A dividend-adjusted close rewrites history every time a fund distributes, and would misstate past net worth, so adjusted figures are never read.

The table is append-only.
When a source sends a different price for a day it already priced, the newer row becomes current and the older one stays, so a wrong quote that once fed a figure remains visible (`PriceStore.revisions`).

## Honest valuation

`value_holdings` marks each position with a `Freshness` and keeps the evidence behind its value:

- **current**: valued at a price no more than two weekday sessions behind the valuation day.
  Fund NAVs publish in the evening, so yesterday's close is normally the newest; the two extra sessions absorb market holidays, which the module does not model.
- **stale**: the newest price lags more than that.
  The position is still valued, but it carries the price's date and age (`reason`, `price_age_days`) so it is never shown as current.
- **unpriced**: no price at all, or a price in a currency hearth cannot convert.
  Its value is `None`, never zero.

`NetWorth.complete` is false while anything is unpriced, and `NetWorth.missing` names each such position.
`NetWorth.total` raises `IncompleteValuationError` in that state; the partial sum is only available as `known_total`, a floor to be shown beside what it leaves out, never as the net worth.

## Whose net worth

`value_holdings` values whichever holdings it is given.
Whose they are is `hearth.figures`'s model: value a member's sole accounts (`figures.solo`) or one joint group's accounts (`figures.joint`), never a share of an account, and the household is every account once.

## Price sources

`quotes.PriceSource` is the whole contract: a stable `name`, `unavailable_reason()`, and `quotes(symbol, start, end)` returning raw closes.
Swapping sources means writing one class; stored history keeps its own `source`, so prices from different sources never mix.
The positions panel names each source behind the prices it shows, and when the newest was fetched.

`eodhd.EodhdSource` is the one implementation, chosen in the hearth-scout-1 report for its mutual fund coverage and a licence that allows keeping prices for private use.
With no API key it reports itself unavailable and is never called.
That is the normal state until a plan is bought, so `refresh_prices` records nothing and says why, and a fund no connected brokerage prices then values as unpriced.

### The institution's own price, through Plaid

The household chose to value holdings at each brokerage's own daily price (`plaid-only`), with no market data feed.
`valuation.institution` reads a parsed `/investments/holdings/get` response, takes each holding's `institution_price` and `institution_price_as_of`, and stores it under source `plaid`, with the Plaid `security_id` as `source_ref` and the time it was fetched.
A holding is matched to a ledger commodity by its security's ticker, or, with no ticker beancount can name it by, by the name hearth's own books hold it as, made from its Plaid security (`institution.commodity`): a match, never a guess.
One with no price, no date or no ISO currency is listed with its reason, never stored on a guess; two holdings of one ticker that disagree on a day are both listed and neither stored.
The response must be parsed with `parse_float=Decimal`; a float is refused.

A Plaid security is matched by its `security_id` and its ticker; its CUSIP is never used, as Plaid sends it only under a licence.
An institution that gives the moment a price was current rather than its day (`institution_price_datetime`) is read at that moment's day.
Two brokerages pricing one ticker differently on a day are both listed and neither stored, like two holdings within one.

It is today-only: Plaid keeps no history of it, so a day nobody asked is a day with no price, and nothing before the first ask can ever be had.
Nothing in `valuation` calls Plaid: `hearth.plaid.holdings` asks every connected brokerage each morning, when `holdings` is approved, and hands every answer to `institution.read_many` (`docs/connections.md`, "Holdings").
An Item whose institution offers Investments lists it among its available products and needs no new sign-in or purchase decision to be asked; the first ask adds the product to the Item, which is why it is approved on its own.
It is holdings only: a brokerage's trades and dividends come from another call, approved apart as `activity`, which nothing here reads.

Net worth history therefore starts after the last month end at which a holding priced today had no price, and both charts say so, rather than drawing earlier months at what the cash alone was worth and a rise on the day the first price arrived.
A holding with no price even today has had none at any month end, so it is left out of every point alike, each a labelled floor, and never holds back the months the rest can be valued at.

## Verifying EODHD before trusting it

EODHD's public quote page shows a fund NAV to four decimal places, while fund companies publish to the cent.
Before its prices are relied on, compare one full month for every fund the household holds against the NAV each fund company publishes itself.

1. Buy one month of EODHD's EOD plan and put the key in `HEARTH_EODHD_API_KEY` for this shell only.
2. For each fund held, download a month of daily NAV history from the fund company's own website, not from an aggregator.
3. Write them to a CSV outside the repository, with exactly the header `symbol,date,nav`, one row per fund per trading day, dates as `YYYY-MM-DD`, and the NAV exactly as published.
4. Run `uv run python -m hearth.valuation.verify <that file>`.
   It fetches the same symbols over the same days and compares day by day.
5. Read the verdict.
   It prints `MISMATCH` for any day more than half a cent (`0.005`) from the published NAV, `MISSING` for a published day EODHD has no price for, and `EXTRA` for an EODHD day with no published NAV, then `PASS` or `FAIL`.
   Exit status is 0 on pass, 1 on fail, 2 if it could not run.

A `PASS` keeps EODHD.
A `MISMATCH` or `MISSING` on any fund moves hearth to the runner-up, Tiingo, whose documentation states fund NAV handling explicitly.
That also means accepting Tiingo's licence term that all stored Tiingo prices, backups included, are deleted if the subscription ends.
An `EXTRA` usually means the CSV is short a day; complete it and run again before drawing a conclusion.

The same `verify.compare` is the daily cross-check against Plaid: an institution's `institution_price` and `institution_price_as_of` for a holding are a reference price for one day.
They cover holdings with no public ticker, but they are today-only with no history, so they check a source rather than replace one.

## What waits on the purchase decision

Everything above runs without a key.
Until an EODHD plan is bought:

- no market price is ever fetched, so a fund or ETF no connected brokerage prices values as unpriced and net worth is reported incomplete, naming each one;
- the verification above cannot run;
- the 2-year price backfill for net worth over time cannot run.

Once a key exists, the remaining work is wiring: storing the key with hearth's other secrets, a daily refresh schedule, and the backfill.
