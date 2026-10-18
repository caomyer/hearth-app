"""Plaid access tokens, encrypted at rest under a master key held in the macOS login Keychain.

What this protects against: someone who reads the database file, a copy of it, or a backup of
it. Every token in the database is AES-256-GCM ciphertext, and the key that opens it is not in
the database, beside it, or anywhere in the data directory; it is in the Keychain, which is
itself encrypted with the user's login password. A Time Machine copy of the data directory, a
synced folder, or a database attached to a bug report gives away no token.

Each ciphertext is also bound to the member and the Plaid Item it belongs to, so a row copied
onto another member or Item fails to decrypt instead of granting one member another's access.

What this does NOT protect against: anyone who can run code as the macOS user hearth runs as.
The Keychain hands the master key to that user's processes, so malware, a malicious package,
or a person at the unlocked, logged-in Mac can read every token. Nor does it keep tokens from
the household's admin: hearth must decrypt every member's token to sync without them present,
so the boundary between members is enforced by the server, not by cryptography.

There is no fallback. If the Keychain cannot be reached, nothing is stored and nothing is read;
no code path in this package writes a key or a token anywhere else.
"""
