"""Where master keys live: the macOS login Keychain, and nowhere else."""

import base64
import binascii
from typing import Protocol

from keyring.backend import KeyringBackend
from keyring.backends import macOS
from keyring.errors import KeyringError

from hearth.vault.errors import KeychainUnavailableError
from hearth.vault.secret import KeyMaterial

SERVICE = "dev.hearth.vault"
KEY_BYTES = 32


class KeyStore(Protocol):
    """Holds master keys by name. `put` never overwrites an existing key."""

    def get(self, name: str) -> KeyMaterial | None: ...

    def put(self, name: str, key: KeyMaterial) -> None: ...

    def delete(self, name: str) -> None: ...


def _macos_backend() -> KeyringBackend:
    # Always the macOS Keychain itself. keyring's own default chooses a backend by priority,
    # and on a machine with a plaintext file backend installed that choice would quietly put
    # the master key in a file, which is exactly what this package exists to prevent.
    try:
        macOS.Keyring.priority  # noqa: B018 - raises unless the Security API is usable
    except RuntimeError as error:
        raise KeychainUnavailableError(f"the macOS Keychain is not available: {error}") from None
    # keyring leaves the constructor unannotated.
    return macOS.Keyring()  # type: ignore[no-untyped-call]


class KeychainKeyStore:
    """Master keys as generic passwords in the login Keychain, base64 encoded.

    Each key is the password of service `dev.hearth.vault` and an account named by the vault.
    """

    def __init__(self, backend: KeyringBackend | None = None) -> None:
        self._backend = backend if backend is not None else _macos_backend()

    def get(self, name: str) -> KeyMaterial | None:
        try:
            stored = self._backend.get_password(SERVICE, name)
        except KeyringError as error:
            raise KeychainUnavailableError(
                f"could not read master key {name} from the Keychain: {type(error).__name__}"
            ) from None
        if stored is None:
            return None
        malformed = KeychainUnavailableError(f"master key {name} in the Keychain is malformed")
        try:
            key = KeyMaterial(base64.b64decode(stored, validate=True))
        except binascii.Error, ValueError:
            raise malformed from None
        if len(key.reveal()) != KEY_BYTES:
            raise malformed
        return key

    def put(self, name: str, key: KeyMaterial) -> None:
        if len(key.reveal()) != KEY_BYTES:
            raise ValueError(f"a master key is {KEY_BYTES} bytes")
        if self.get(name) is not None:
            raise KeychainUnavailableError(
                f"master key {name} already exists in the Keychain; refusing to overwrite it"
            )
        try:
            self._backend.set_password(
                SERVICE, name, base64.b64encode(key.reveal()).decode("ascii")
            )
        except KeyringError as error:
            raise KeychainUnavailableError(
                f"could not store master key {name} in the Keychain: {type(error).__name__}"
            ) from None

    def delete(self, name: str) -> None:
        try:
            self._backend.delete_password(SERVICE, name)
        except KeyringError as error:
            raise KeychainUnavailableError(
                f"could not delete master key {name} from the Keychain: {type(error).__name__}"
            ) from None
