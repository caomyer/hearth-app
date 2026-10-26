# Token vault

`hearth.vault` keeps each member's Plaid access tokens encrypted in the app database, under a master key held in the macOS login Keychain.
The threat model is stated in full in the package docstring, `src/hearth/vault/__init__.py`.
In one line: it protects the database file and its backups, and it does not protect against anyone who can run code as the macOS user hearth runs as.

## How a token is stored

- Each token is encrypted with AES-256-GCM under its own random data key.
- The data key is encrypted with AES-256-GCM under the current master key.
- Both bind the member id and the Item id as associated data, and the data key's binding also includes the master key version.
  A row copied onto another member, another Item or another key version fails authentication and is reported as tampered.
- Tokens and keys are held as `Secret` and `KeyMaterial`, whose repr, str and format are redacted and which refuse to be pickled.
  They are revealed only inside the expression that hands them to the cipher, so even a traceback that prints every frame's local variables shows nothing.
  `tests/vault/test_no_leak.py` triggers every failure and checks exactly that.

A token for an Item made outside hearth, taken in by `hearth plaid adopt` (`docs/connections.md`), is sealed the same way and by the same call as one from Link; there is no second path.

## Plaid keys

Each Plaid account's client id and secret are sealed the same way, in `vault_credential`, bound to the set they belong to (`member:<id>` or `household`) and their name, so one member's keys copied onto another's set fail to open.
Rotation re-encrypts their data keys with the tokens'.

## The master key

Each database has a random vault id, created on first use and kept in `vault_identity`.
Its master keys live in the login Keychain as generic passwords of service `dev.hearth.vault`, with account `vault-<id>-master-key-v<version>`.
Two hearth databases on one Mac, such as the demo household and the real one, therefore never share a key, and rotating one never touches the other's.
The first token stored creates master key v1.
A key already in the Keychain under this vault's name, left by a first use that was interrupted before the database recorded it, is adopted, never overwritten.

There is no fallback.
The Keychain backend is always macOS's own, never keyring's priority-chosen default, which on some machines is a plaintext file.
If the Keychain cannot be reached, or holds a malformed key, nothing is stored or read and the error says why.
If the database's key is missing from the Keychain, the vault refuses to create another, which would orphan every stored token.

keyring ignores any keychain other than the default one (keyring issue #623), so hearth always uses the user's login keychain.

## Rotation

`TokenVault.rotate()` creates the next master key version, re-encrypts every data key under it, tokens' and Plaid keys' alike, in one transaction, retires the old version, and then deletes the old key from the Keychain.
Token ciphertexts are untouched, so no institution needs linking again.
If any row fails to decrypt, the transaction rolls back and every token stays readable under the old key.
A backup taken before a rotation cannot be decrypted afterwards, which is the point of rotating.

## Moving to another Mac

The master key does not travel with the database.
Restoring the database on another Mac needs that vault's master key in that Mac's login Keychain under the same name, and hearth has no command to move it yet.
Without it, the vault reports the key missing, and members link their institutions again.
