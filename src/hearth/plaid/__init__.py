"""Connecting a member's institutions through Plaid, and pulling their history.

No module here has routes or templates, and none decides who may see what: pages reach a
member's connections only through ``hearth.disclosure``, which builds the member's
``connector.Connector`` with their gate (``docs/connections.md``).

- ``approval``: which live calls this run may make. None, unless approved by name.
- ``client``: the few Plaid calls hearth makes, over an injectable transport.
- ``credentials``: each member's own Plaid account, or the household's, and its allowance.
- ``registry``: the Items hearth holds and the ledger account each of their accounts feeds.
- ``feed``: what Plaid sent, kept apart from the ledger and the app database.
- ``sync``: bringing an Item up to date, incrementally and resumably.
- ``mapping``: proposing ledger accounts, and naming two that differ by a single word.
- ``connector``: one member's connections, as the access policy lets them see and use them.
- ``fake``: the stand-in for Plaid that the demo and the tests run against.
- ``setup``: how this run reaches Plaid; ``cli``: ``hearth plaid``.
"""
