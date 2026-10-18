"""Holdings, prices and what they are worth, reported honestly.

Units come from the ledger, prices from a swappable quote source with provenance kept for every
price, and a valuation never presents a position it could not price as worth zero, or a stale
price as current. See `docs/valuation.md`.
"""
