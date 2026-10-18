import sqlite3
from contextlib import closing

import pytest

from hearth.members import MemberId
from hearth.vault.errors import (
    KeychainUnavailableError,
    MasterKeyMissingError,
    TokenNotFoundError,
    TokenTamperedError,
)
from hearth.vault.secret import Secret
from hearth.vault.tokens import TokenVault
from tests.vault.conftest import (
    MEMBER_A,
    MEMBER_B,
    OTHER_TOKEN,
    TOKEN,
    MemoryKeyStore,
    open_database,
)


def rows(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT member_id, item_id, key_version, wrapped_key, ciphertext FROM vault_token"
            " ORDER BY member_id, item_id"
        )
    ]


def test_a_stored_token_comes_back(vault: TokenVault, token: Secret) -> None:
    vault.put(MEMBER_A, "item-1", token)

    assert vault.get(MEMBER_A, "item-1") == token


def test_the_first_token_creates_master_key_v1_in_the_keychain(
    vault: TokenVault, keys: MemoryKeyStore, token: Secret
) -> None:
    assert keys.keys == {}

    vault.put(MEMBER_A, "item-1", token)

    assert keys.versions() == [1]


def test_two_databases_on_one_keychain_never_share_or_rotate_away_each_others_keys(
    connection: sqlite3.Connection, keys: MemoryKeyStore, token: Secret
) -> None:
    with closing(open_database()) as demo_database:
        demo = TokenVault(demo_database, keys)
        demo.put(MEMBER_A, "item-1", token)
        real = TokenVault(connection, keys)
        real.put(MEMBER_A, "item-1", Secret(OTHER_TOKEN))

        assert keys.versions() == [1, 1]
        demo.rotate()

        assert real.get(MEMBER_A, "item-1") == Secret(OTHER_TOKEN)
        assert demo.get(MEMBER_A, "item-1") == token
        assert keys.versions() == [1, 2]


def test_a_key_left_by_an_interrupted_first_use_is_adopted_not_overwritten(
    connection: sqlite3.Connection, keys: MemoryKeyStore, token: Secret
) -> None:
    vault = TokenVault(connection, keys)
    keys.available = False
    with pytest.raises(KeychainUnavailableError):
        vault.put(MEMBER_A, "item-1", token)
    keys.available = True
    vault.put(MEMBER_A, "item-1", token)
    (left,) = keys.keys.values()
    connection.execute("DELETE FROM vault_token")
    connection.execute("DELETE FROM vault_master_key")

    vault.put(MEMBER_A, "item-1", token)

    assert list(keys.keys.values()) == [left]
    assert vault.get(MEMBER_A, "item-1") == token


def test_the_database_holds_no_token_in_any_form(
    connection: sqlite3.Connection, vault: TokenVault, token: Secret
) -> None:
    vault.put(MEMBER_A, "item-1", token)

    dump = "\n".join(connection.iterdump()).encode()
    (row,) = rows(connection)

    assert TOKEN.encode() not in dump
    assert TOKEN.encode() not in row[3]  # type: ignore[operator]
    assert TOKEN.encode() not in row[4]  # type: ignore[operator]


def test_the_same_token_encrypts_differently_every_time(
    connection: sqlite3.Connection, vault: TokenVault, token: Secret
) -> None:
    vault.put(MEMBER_A, "item-1", token)
    vault.put(MEMBER_A, "item-2", token)

    first, second = rows(connection)

    assert first[4] != second[4]
    assert first[3] != second[3]


def test_members_and_items_are_kept_apart(vault: TokenVault) -> None:
    vault.put(MEMBER_A, "item-1", Secret(TOKEN))
    vault.put(MEMBER_B, "item-1", Secret(OTHER_TOKEN))

    assert vault.get(MEMBER_A, "item-1") == Secret(TOKEN)
    assert vault.get(MEMBER_B, "item-1") == Secret(OTHER_TOKEN)
    assert vault.item_ids(MEMBER_A) == ["item-1"]
    with pytest.raises(TokenNotFoundError):
        vault.get(MEMBER_A, "item-2")


@pytest.mark.parametrize(
    "move",
    [
        "UPDATE vault_token SET member_id = 2 WHERE member_id = 1",
        "UPDATE vault_token SET item_id = 'item-9' WHERE item_id = 'item-1'",
    ],
)
def test_a_ciphertext_moved_to_another_member_or_item_will_not_decrypt(
    connection: sqlite3.Connection, vault: TokenVault, token: Secret, move: str
) -> None:
    vault.put(MEMBER_A, "item-1", token)
    connection.execute(move)

    member, item = rows(connection)[0][:2]
    with pytest.raises(TokenTamperedError, match="failed authentication"):
        vault.get(MemberId(int(str(member))), str(item))


def test_a_ciphertext_swapped_between_two_members_will_not_decrypt(
    connection: sqlite3.Connection, vault: TokenVault
) -> None:
    vault.put(MEMBER_A, "item-1", Secret(TOKEN))
    vault.put(MEMBER_B, "item-2", Secret(OTHER_TOKEN))
    (_, _, _, wrapped_a, cipher_a), (_, _, _, wrapped_b, cipher_b) = rows(connection)
    connection.execute(
        "UPDATE vault_token SET wrapped_key = ?, ciphertext = ? WHERE member_id = 1",
        (wrapped_b, cipher_b),
    )
    connection.execute(
        "UPDATE vault_token SET wrapped_key = ?, ciphertext = ? WHERE member_id = 2",
        (wrapped_a, cipher_a),
    )

    for member, item in ((MEMBER_A, "item-1"), (MEMBER_B, "item-2")):
        with pytest.raises(TokenTamperedError):
            vault.get(member, item)


@pytest.mark.parametrize("column", ["wrapped_key", "ciphertext"])
def test_a_flipped_bit_is_detected(
    connection: sqlite3.Connection, vault: TokenVault, token: Secret, column: str
) -> None:
    vault.put(MEMBER_A, "item-1", token)
    (blob,) = connection.execute(f"SELECT {column} FROM vault_token").fetchone()  # noqa: S608
    tampered = bytes([blob[0] ^ 1]) + blob[1:]
    connection.execute(f"UPDATE vault_token SET {column} = ?", (tampered,))  # noqa: S608

    with pytest.raises(TokenTamperedError):
        vault.get(MEMBER_A, "item-1")


def test_an_unreachable_keychain_stores_nothing(
    connection: sqlite3.Connection, vault: TokenVault, keys: MemoryKeyStore, token: Secret
) -> None:
    keys.available = False

    with pytest.raises(KeychainUnavailableError):
        vault.put(MEMBER_A, "item-1", token)

    assert rows(connection) == []
    assert connection.execute("SELECT count(*) FROM vault_master_key").fetchone()[0] == 0


def test_a_missing_master_key_is_reported_and_never_replaced(
    vault: TokenVault, keys: MemoryKeyStore, token: Secret
) -> None:
    vault.put(MEMBER_A, "item-1", token)
    keys.keys.clear()

    with pytest.raises(MasterKeyMissingError, match="v1"):
        vault.get(MEMBER_A, "item-1")
    with pytest.raises(MasterKeyMissingError, match="refusing to create another"):
        vault.put(MEMBER_A, "item-2", token)
    assert keys.keys == {}


def test_storing_again_replaces_the_token(vault: TokenVault) -> None:
    vault.put(MEMBER_A, "item-1", Secret(TOKEN))
    vault.put(MEMBER_A, "item-1", Secret(OTHER_TOKEN))

    assert vault.get(MEMBER_A, "item-1") == Secret(OTHER_TOKEN)


def test_a_deleted_token_is_gone(vault: TokenVault, token: Secret) -> None:
    vault.put(MEMBER_A, "item-1", token)

    vault.delete(MEMBER_A, "item-1")

    assert vault.item_ids(MEMBER_A) == []
    with pytest.raises(TokenNotFoundError):
        vault.get(MEMBER_A, "item-1")


@pytest.mark.parametrize(
    ("member", "item", "complaint"),
    [
        ("member-a", "item-1", "a member's id"),
        (True, "item-1", "a member's id"),
        (1, "", "non-empty"),
    ],
)
def test_ids_must_be_named(
    vault: TokenVault, token: Secret, member: object, item: str, complaint: str
) -> None:
    with pytest.raises(ValueError, match=complaint):
        vault.put(member, item, token)  # type: ignore[arg-type]


def test_a_token_must_arrive_as_a_secret(vault: TokenVault) -> None:
    with pytest.raises(TypeError, match="Secret"):
        vault.put(MEMBER_A, "item-1", TOKEN)  # type: ignore[arg-type]


def test_rotation_rewraps_every_token_without_touching_its_ciphertext(
    connection: sqlite3.Connection, vault: TokenVault, keys: MemoryKeyStore
) -> None:
    vault.put(MEMBER_A, "item-1", Secret(TOKEN))
    vault.put(MEMBER_B, "item-2", Secret(OTHER_TOKEN))
    before = rows(connection)

    assert vault.rotate() == 2

    after = rows(connection)
    assert [r[4] for r in after] == [r[4] for r in before]
    assert all(a[3] != b[3] for a, b in zip(after, before, strict=True))
    assert {r[2] for r in after} == {2}
    assert vault.get(MEMBER_A, "item-1") == Secret(TOKEN)
    assert vault.get(MEMBER_B, "item-2") == Secret(OTHER_TOKEN)
    assert keys.versions() == [2]
    assert [name.rsplit("-v", 1)[1] for name in keys.deleted] == ["1"]


def test_tokens_stored_after_rotation_use_the_new_key(
    connection: sqlite3.Connection, vault: TokenVault, token: Secret
) -> None:
    vault.put(MEMBER_A, "item-1", token)
    vault.rotate()
    vault.rotate()

    vault.put(MEMBER_A, "item-2", token)

    assert {r[2] for r in rows(connection)} == {3}
    assert vault.get(MEMBER_A, "item-1") == token
    assert vault.get(MEMBER_A, "item-2") == token


def test_a_rotation_that_fails_leaves_every_token_readable(
    connection: sqlite3.Connection, vault: TokenVault, keys: MemoryKeyStore, token: Secret
) -> None:
    vault.put(MEMBER_A, "item-1", token)
    vault.put(MEMBER_B, "item-1", token)
    connection.execute(
        "UPDATE vault_token SET ciphertext = x'00', wrapped_key = x'00' WHERE member_id = 2"
    )

    with pytest.raises(TokenTamperedError):
        vault.rotate()

    assert vault.get(MEMBER_A, "item-1") == token
    assert {r[2] for r in rows(connection)} == {1}
    assert [
        tuple(row)
        for row in connection.execute(
            "SELECT version, retired_at FROM vault_master_key ORDER BY version"
        )
    ] == [(1, None)]
    # The new key was made before the transaction failed; a retried rotation adopts it.
    assert keys.versions() == [1, 2]


def test_a_connection_outside_autocommit_mode_is_refused(keys: MemoryKeyStore) -> None:
    # Python's `with connection:` is a no-op in autocommit mode and implicit in legacy mode;
    # the vault needs hearth.db's explicit transactions, so it takes only hearth.db's kind.
    with (
        closing(sqlite3.connect(":memory:")) as legacy,
        pytest.raises(ValueError, match="autocommit"),
    ):
        TokenVault(legacy, keys)
