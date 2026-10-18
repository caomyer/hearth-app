"""Questions the dashboards ask of the ledger, answered with an explicit date and honestly.

Every function takes loaded beancount entries and the date or period it answers for. A figure
that cannot be computed completely says so, and names what it leaves out, rather than returning
a plausible number. Figures attributed to members and joint accounts are `hearth.figures`'s;
these are the household-wide questions it does not answer. See `docs/reports.md`.
"""
