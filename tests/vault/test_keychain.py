import base64

import pytest
from keyring.errors import KeyringLocked, PasswordSetError

from hearth.vault.errors import KeychainUnavailableError
from hearth.vault.keychain import SERVICE, KeychainKeyStore
from hearth.vault.secret import KeyMaterial
from tests.vault.conftest import MemoryBackend

KEY = KeyMaterial(bytes(range(32)))


def store() -> tuple[KeychainKeyStore, MemoryBackend]:
    backend = MemoryBackend()
    return KeychainKeyStore(backend), backend


def test_a_key_is_stored_under_its_version_and_read_back() -> None:
    keys, backend = store()

    keys.put("vault-x-master-key-v1", KEY)

    assert keys.get("vault-x-master-key-v1") == KEY
    assert keys.get("vault-x-master-key-v2") is None
    assert backend.passwords == {
        (SERVICE, "vault-x-master-key-v1"): base64.b64encode(KEY.reveal()).decode()
    }


def test_an_existing_key_is_never_overwritten() -> None:
    keys, _ = store()
    keys.put("vault-x-master-key-v1", KEY)

    with pytest.raises(KeychainUnavailableError, match="refusing to overwrite"):
        keys.put("vault-x-master-key-v1", KeyMaterial(b"\x00" * 32))
    assert keys.get("vault-x-master-key-v1") == KEY


def test_a_key_of_the_wrong_size_is_refused() -> None:
    keys, _ = store()

    with pytest.raises(ValueError, match="32 bytes"):
        keys.put("vault-x-master-key-v1", KeyMaterial(b"short"))


@pytest.mark.parametrize("stored", ["not base64!", base64.b64encode(b"short").decode()])
def test_a_malformed_key_in_the_keychain_is_refused(stored: str) -> None:
    keys, backend = store()
    backend.passwords[SERVICE, "vault-x-master-key-v1"] = stored

    with pytest.raises(KeychainUnavailableError, match="malformed"):
        keys.get("vault-x-master-key-v1")


@pytest.mark.parametrize("failure", [KeyringLocked("locked"), PasswordSetError("denied")])
def test_keychain_failures_are_reported_as_unavailable(failure: Exception) -> None:
    keys, backend = store()
    keys.put("vault-x-master-key-v1", KEY)
    backend.failure = failure  # type: ignore[assignment]

    name = type(failure).__name__
    with pytest.raises(KeychainUnavailableError, match=name):
        keys.get("vault-x-master-key-v1")
    with pytest.raises(KeychainUnavailableError, match=name):
        keys.put("vault-x-master-key-v2", KEY)
    with pytest.raises(KeychainUnavailableError, match=name):
        keys.delete("vault-x-master-key-v1")


def test_without_the_macos_keychain_there_is_no_store_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("platform.system", lambda: "Linux")

    with pytest.raises(KeychainUnavailableError, match="not available"):
        KeychainKeyStore()
