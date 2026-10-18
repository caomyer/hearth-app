"""In-memory stand-ins for the Keychain. They exist only here: the package has no fallback."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from keyring.backend import KeyringBackend
from keyring.errors import KeyringError, PasswordDeleteError

from hearth import db as hearth_db
from hearth.members import MemberId
from hearth.vault.errors import KeychainUnavailableError
from hearth.vault.secret import KeyMaterial, Secret
from hearth.vault.tokens import TokenVault

MEMBER_A, MEMBER_B, MEMBER_C = MemberId(1), MemberId(2), MemberId(3)

TOKEN = "access-production-00000000-synthetic-token-feedface"
OTHER_TOKEN = "access-production-11111111-synthetic-token-c0ffee00"


class MemoryKeyStore:
    """A `KeyStore` in memory, which can be told to fail like an unreachable Keychain."""

    def __init__(self) -> None:
        self.keys: dict[str, KeyMaterial] = {}
        self.available = True
        self.deleted: list[str] = []

    def versions(self) -> list[int]:
        """The master key versions held, whichever vault they belong to."""
        return sorted(int(name.rsplit("-v", 1)[1]) for name in self.keys)

    def _check(self) -> None:
        if not self.available:
            raise KeychainUnavailableError("the Keychain is locked")

    def get(self, name: str) -> KeyMaterial | None:
        self._check()
        return self.keys.get(name)

    def put(self, name: str, key: KeyMaterial) -> None:
        self._check()
        if name in self.keys:
            raise KeychainUnavailableError(f"master key {name} already exists")
        self.keys[name] = key

    def delete(self, name: str) -> None:
        self._check()
        self.deleted.append(name)
        del self.keys[name]


class MemoryBackend(KeyringBackend):
    """A keyring backend in memory, for testing `KeychainKeyStore` without the real Keychain."""

    priority = 1

    def __init__(self) -> None:
        super().__init__()  # type: ignore[no-untyped-call]
        self.passwords: dict[tuple[str, str], str] = {}
        self.failure: KeyringError | None = None

    def get_password(self, service: str, username: str) -> str | None:
        if self.failure is not None:
            raise self.failure
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.failure is not None:
            raise self.failure
        self.passwords[service, username] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.failure is not None:
            raise self.failure
        if self.passwords.pop((service, username), None) is None:
            raise PasswordDeleteError("not found")


def open_database() -> sqlite3.Connection:
    """A migrated in-memory app database with three members."""
    db = hearth_db.connect(Path(":memory:"))
    hearth_db.migrate(db)
    for member in (MEMBER_A, MEMBER_B, MEMBER_C):
        db.execute(
            "INSERT INTO members (id, username, display_name, password_hash, is_admin,"
            " must_change_password, created_at) VALUES (?, ?, ?, 'x', 0, 0, '2026-01-01')",
            (member, f"member-{member}", f"Member {member}"),
        )
    return db


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    db = open_database()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def keys() -> MemoryKeyStore:
    return MemoryKeyStore()


@pytest.fixture
def vault(connection: sqlite3.Connection, keys: MemoryKeyStore) -> TokenVault:
    return TokenVault(connection, keys)


@pytest.fixture
def token() -> Secret:
    return Secret(TOKEN)
