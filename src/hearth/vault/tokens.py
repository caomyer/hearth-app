"""Per-member Plaid access tokens, envelope-encrypted in the app database.

Each token is encrypted with AES-256-GCM under its own random data key, and that data key is
encrypted under the current master key from the Keychain. Both encryptions bind the member and
the Item as associated data, and the data key's also binds the master key version, so a row
moved to another member, another Item or another key version fails authentication.

Rotating the master key re-encrypts only the data keys: every token's ciphertext stays as it
is, so rotation never needs an institution to be linked again.
"""

import os
import secrets
import sqlite3
from datetime import UTC, datetime

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from hearth.db import transaction
from hearth.members import MemberId
from hearth.vault.errors import MasterKeyMissingError, TokenNotFoundError, TokenTamperedError
from hearth.vault.keychain import KEY_BYTES, KeyStore
from hearth.vault.secret import KeyMaterial, Secret

NONCE_BYTES = 12
_CONTEXT = b"hearth.vault.v1"


def _aad(purpose: str, *parts: str | int) -> bytes:
    """Associated data with every part length-prefixed, so no two tuples encode the same."""
    encoded = [_CONTEXT, purpose.encode(), *(str(part).encode() for part in parts)]
    return b"".join(len(p).to_bytes(4, "big") + p for p in encoded)


# Plaintext tokens and keys are only ever held as Secret or KeyMaterial and revealed inside an
# expression, never bound to a plain local, so even a traceback that prints every frame's local
# variables (as error reporters do) shows nothing but redacted reprs.


def _reveal(value: Secret | KeyMaterial) -> bytes:
    return value.reveal().encode() if isinstance(value, Secret) else value.reveal()


def _seal(key: KeyMaterial, plaintext: Secret | KeyMaterial, aad: bytes) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(key.reveal()).encrypt(nonce, _reveal(plaintext), aad)


def _open(key: KeyMaterial, sealed: bytes, aad: bytes) -> bytes:
    return AESGCM(key.reveal()).decrypt(sealed[:NONCE_BYTES], sealed[NONCE_BYTES:], aad)


def _new_key() -> KeyMaterial:
    return KeyMaterial(AESGCM.generate_key(bit_length=KEY_BYTES * 8))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _check_ids(member_id: MemberId, item_id: str) -> None:
    if not isinstance(member_id, int) or isinstance(member_id, bool):
        raise ValueError("member_id must be a member's id")
    if not isinstance(item_id, str) or not item_id:
        raise ValueError("item_id must be a non-empty string")


class TokenVault:
    """Stores and returns Plaid access tokens by member and Item.

    Each database names its master keys in the Keychain by a random vault id of its own,
    created on first use, so two hearth databases on one Mac never share a key and rotating
    one never touches the other's. The first token stored creates master key v1; a key that
    is already in the Keychain under this vault's name, left by an interrupted first use, is
    adopted rather than overwritten. If the Keychain cannot be reached, nothing is stored.
    """

    def __init__(self, connection: sqlite3.Connection, keys: KeyStore) -> None:
        self._db = connection
        self._keys = keys
        if connection.autocommit is not True:
            raise ValueError("open the connection with hearth.db.connect, in autocommit mode")

    def __repr__(self) -> str:
        return "TokenVault()"

    def put(self, member_id: MemberId, item_id: str, token: Secret) -> None:
        """Store `token` for the member's Item, replacing any token stored for it before."""
        _check_ids(member_id, item_id)
        if not isinstance(token, Secret):
            raise TypeError("a token must be passed as a Secret")
        version, master = self._active_key()
        data_key = _new_key()
        ciphertext = _seal(data_key, token, _aad("token", member_id, item_id))
        wrapped = _seal(master, data_key, _aad("data-key", member_id, item_id, version))
        with transaction(self._db):
            self._db.execute(
                "INSERT OR REPLACE INTO vault_token"
                " (member_id, item_id, key_version, wrapped_key, ciphertext, stored_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (member_id, item_id, version, wrapped, ciphertext, _now()),
            )

    def get(self, member_id: MemberId, item_id: str) -> Secret:
        _check_ids(member_id, item_id)
        row = self._db.execute(
            "SELECT key_version, wrapped_key, ciphertext FROM vault_token"
            " WHERE member_id = ? AND item_id = ?",
            (member_id, item_id),
        ).fetchone()
        if row is None:
            raise TokenNotFoundError(f"no token is stored for member {member_id}, Item {item_id}")
        version, wrapped, ciphertext = row
        data_key = self._data_key(member_id, item_id, version, wrapped)
        try:
            return Secret(_open(data_key, ciphertext, _aad("token", member_id, item_id)).decode())
        except InvalidTag, UnicodeDecodeError, ValueError:
            raise TokenTamperedError(
                f"the token stored for member {member_id}, Item {item_id} failed authentication"
            ) from None

    def delete(self, member_id: MemberId, item_id: str) -> None:
        _check_ids(member_id, item_id)
        with transaction(self._db):
            self._db.execute(
                "DELETE FROM vault_token WHERE member_id = ? AND item_id = ?",
                (member_id, item_id),
            )

    def item_ids(self, member_id: MemberId) -> list[str]:
        rows = self._db.execute(
            "SELECT item_id FROM vault_token WHERE member_id = ? ORDER BY item_id", (member_id,)
        ).fetchall()
        return [item_id for (item_id,) in rows]

    def rotate(self) -> int:
        """Replace the master key and return the new version. No token is re-linked.

        Every data key is re-encrypted under a new master key in one transaction, after which
        the old key is deleted from the Keychain. A backup taken before the rotation can then
        no longer be decrypted, which is the point of rotating.
        """
        old_version, _ = self._active_key()
        new_version = self._next_version()
        new_key = self._obtain(new_version)
        with transaction(self._db):
            rows = self._db.execute(
                "SELECT member_id, item_id, key_version, wrapped_key FROM vault_token"
            ).fetchall()
            self._db.execute(
                "INSERT INTO vault_master_key (version, created_at) VALUES (?, ?)",
                (new_version, _now()),
            )
            self._db.executemany(
                "UPDATE vault_token SET wrapped_key = ?, key_version = ?"
                " WHERE member_id = ? AND item_id = ?",
                [
                    (
                        _seal(
                            new_key,
                            self._data_key(member_id, item_id, version, wrapped),
                            _aad("data-key", member_id, item_id, new_version),
                        ),
                        new_version,
                        member_id,
                        item_id,
                    )
                    for member_id, item_id, version, wrapped in rows
                ],
            )
            self._db.execute(
                "UPDATE vault_master_key SET retired_at = ? WHERE version = ?",
                (_now(), old_version),
            )
        self._keys.delete(self._key_name(old_version))
        return new_version

    def _data_key(
        self, member_id: MemberId, item_id: str, version: int, wrapped: bytes
    ) -> KeyMaterial:
        master = self._keys.get(self._key_name(version))
        if master is None:
            raise MasterKeyMissingError(
                f"tokens are encrypted under master key v{version}, which the Keychain does not"
                " hold; restore it from the machine that created them, or link again"
            )
        try:
            return KeyMaterial(
                _open(master, wrapped, _aad("data-key", member_id, item_id, version))
            )
        except InvalidTag, ValueError:
            raise TokenTamperedError(
                f"the token stored for member {member_id}, Item {item_id} failed authentication"
            ) from None

    def _active_key(self) -> tuple[int, KeyMaterial]:
        with transaction(self._db):
            row = self._db.execute(
                "SELECT max(version) FROM vault_master_key WHERE retired_at IS NULL"
            ).fetchone()
            if row[0] is None:
                version = self._next_version()
                key = self._obtain(version)
                self._db.execute(
                    "INSERT INTO vault_master_key (version, created_at) VALUES (?, ?)",
                    (version, _now()),
                )
                return version, key
        version = row[0]
        stored = self._keys.get(self._key_name(version))
        if stored is None:
            raise MasterKeyMissingError(
                f"the database's master key v{version} is not in the Keychain; refusing to"
                " create another, which would orphan every stored token"
            )
        return version, stored

    def _key_name(self, version: int) -> str:
        """The Keychain account of this vault's master key `version`."""
        row = self._db.execute("SELECT id FROM vault_identity").fetchone()
        if row is None:
            self._db.execute(
                "INSERT INTO vault_identity (id)"
                " SELECT ? WHERE NOT EXISTS (SELECT 1 FROM vault_identity)",
                (secrets.token_hex(8),),
            )
            row = self._db.execute("SELECT id FROM vault_identity").fetchone()
        return f"vault-{row[0]}-master-key-v{version}"

    def _next_version(self) -> int:
        (latest,) = self._db.execute("SELECT max(version) FROM vault_master_key").fetchone()
        return 1 if latest is None else latest + 1

    def _obtain(self, version: int) -> KeyMaterial:
        """The Keychain's key for `version`, created there first if it does not exist yet."""
        name = self._key_name(version)
        existing = self._keys.get(name)
        if existing is not None:
            return existing
        self._keys.put(name, _new_key())
        stored = self._keys.get(name)
        if stored is None:
            raise MasterKeyMissingError(f"master key v{version} did not persist in the Keychain")
        return stored
