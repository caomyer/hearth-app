"""No token and no key may ever reach a log, an exception, a repr or a traceback.

Every failure the vault can raise is triggered here, and each is rendered the way the most
indiscreet error reporter would: the full chain, including suppressed context, with every
frame's local variables. The token, and every key in any encoding, must appear in none of it.
"""

import base64
import functools
import logging
import sqlite3
import traceback
from collections.abc import Callable, Iterator

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from hearth.vault.errors import VaultError
from hearth.vault.keychain import KeychainKeyStore
from hearth.vault.secret import KeyMaterial, Secret
from hearth.vault.tokens import TokenVault
from tests.vault.conftest import MEMBER_A, MEMBER_B, MEMBER_C, TOKEN, MemoryBackend, MemoryKeyStore


def render(error: BaseException) -> str:
    """The exception chain with every frame's locals, suppressed context included."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.extend(
            traceback.TracebackException.from_exception(current, capture_locals=True).format()
        )
        parts.append(repr(current))
        current = current.__cause__ or current.__context__
    return "".join(parts)


def forbidden(keys: MemoryKeyStore) -> list[str]:
    values = [TOKEN, TOKEN.encode().hex(), base64.b64encode(TOKEN.encode()).decode()]
    for key in keys.keys.values():
        raw = key.reveal()
        values += [raw.hex(), base64.b64encode(raw).decode(), repr(raw)[2:-1]]
    return values


@pytest.fixture
def seen(caplog: pytest.LogCaptureFixture) -> Iterator[list[str]]:
    collected: list[str] = []
    with caplog.at_level(logging.DEBUG):
        yield collected
    collected.append(caplog.text)


def attempt(seen: list[str], action: Callable[[], object]) -> None:
    with pytest.raises(VaultError) as raised:
        action()
    seen.append(render(raised.value))


def test_no_failure_reveals_a_token_or_a_key(
    connection: sqlite3.Connection,
    keys: MemoryKeyStore,
    seen: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = TokenVault(connection, keys)
    token = Secret(TOKEN)

    # The Keychain is unreachable on first use.
    keys.available = False
    attempt(seen, lambda: vault.put(MEMBER_A, "item-1", token))
    keys.available = True

    vault.put(MEMBER_A, "item-1", token)
    vault.put(MEMBER_B, "item-1", token)
    seen += [repr(vault), repr(token), str(token), f"{token}", repr(keys.keys)]

    # Nothing stored for the Item.
    attempt(seen, lambda: vault.get(MEMBER_A, "item-2"))

    # A row moved to another member, and a ciphertext altered.
    connection.execute("UPDATE vault_token SET member_id = 3 WHERE rowid = 2")
    attempt(seen, lambda: vault.get(MEMBER_C, "item-1"))
    connection.execute(
        "UPDATE vault_token SET ciphertext = CAST(x'00' || substr(ciphertext, 2) AS BLOB)"
    )
    attempt(seen, lambda: vault.get(MEMBER_A, "item-1"))

    # Rotation over a tampered row, and rotation with the Keychain gone.
    attempt(seen, vault.rotate)
    keys.available = False
    attempt(seen, vault.rotate)
    keys.available = True

    # The cipher itself failing mid-encryption, with the token in flight. The stand-in is a
    # builtin, so like the real cipher, which is compiled, it has no Python frame of its own:
    # what is rendered is hearth's frames alone.
    with monkeypatch.context() as patch:
        patch.setattr(AESGCM, "encrypt", functools.partial(int, "cipher failure"))
        with pytest.raises(TypeError) as raised:
            vault.put(MEMBER_A, "item-3", token)
        seen.append(render(raised.value))

    # The master key gone from the Keychain.
    stolen = dict(keys.keys)
    keys.keys.clear()
    attempt(seen, lambda: vault.get(MEMBER_B, "item-1"))
    attempt(seen, lambda: vault.put(MEMBER_A, "item-4", token))
    keys.keys.update(stolen)

    # A keychain-backed store whose backend fails or holds a malformed key.
    backend = MemoryBackend()
    keychain = KeychainKeyStore(backend)
    keychain.put("vault-x-master-key-v1", KeyMaterial(b"\x07" * 32))
    seen.append(repr(keychain))
    backend.passwords[next(iter(backend.passwords))] = "not base64!"
    attempt(seen, lambda: keychain.get("vault-x-master-key-v1"))

    output = capsys.readouterr()
    seen += [output.out, output.err]
    secrets = [*forbidden(keys), base64.b64encode(b"\x07" * 32).decode()]
    for text in seen:
        for secret in secrets:
            assert secret not in text


def test_a_token_passed_without_its_wrapper_is_refused_without_echoing_it(
    vault: TokenVault,
) -> None:
    # The caller's own variable holds the raw string here, so only the message is ours to keep
    # clean; the frame's locals are the caller's mistake, which is why `put` refuses a str.
    with pytest.raises(TypeError) as raised:
        vault.put(MEMBER_A, "item-1", TOKEN)  # type: ignore[arg-type]

    assert TOKEN not in str(raised.value)
