"""Each member links through their own Plaid account by default, and its allowance is honest."""

import sqlite3
from collections.abc import Iterator

import pytest

from hearth.plaid.client import Environment, Institution
from hearth.plaid.credentials import HOUSEHOLD, CredentialsInUseError, CredentialStore, personal
from hearth.plaid.registry import Registry
from hearth.vault.errors import TokenTamperedError
from hearth.vault.secret import Secret
from hearth.vault.tokens import TokenVault
from tests.plaid.conftest import NOW
from tests.vault.conftest import MEMBER_A, MEMBER_B, MemoryKeyStore, open_database


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    db = open_database()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def keys() -> MemoryKeyStore:
    return MemoryKeyStore()


@pytest.fixture
def store(conn: sqlite3.Connection, keys: MemoryKeyStore) -> CredentialStore:
    return CredentialStore(conn, TokenVault(conn, keys))


def save(
    store: CredentialStore,
    scope: str,
    owner: object,
    client: str,
    *,
    used: int = 0,
    environment: Environment = Environment.PRODUCTION,
) -> None:
    store.save(
        scope,
        owner=owner,  # type: ignore[arg-type]
        registered_by=MEMBER_A,
        client_id=Secret(client),
        secret=Secret(f"{client}-secret"),
        environment=environment,
        item_limit=10,
        used_elsewhere=used,
        now=NOW,
    )


def connect(conn: sqlite3.Connection, scope: str, item_id: str) -> None:
    Registry(conn).add(
        item_id,
        member=MEMBER_A,
        credentials=scope,
        institution=Institution("ins_x", "Invented Bank"),
        fallback_name="",
        days_requested=730,
        now=NOW,
    )


def test_a_member_links_through_their_own_account_and_never_silently_the_households(
    store: CredentialStore,
) -> None:
    save(store, HOUSEHOLD, None, "household-client")

    assert store.scope_for(MEMBER_B) is None
    store.choose_household(MEMBER_B, chosen=True, now=NOW)
    assert store.scope_for(MEMBER_B) == HOUSEHOLD
    save(store, personal(MEMBER_B), MEMBER_B, "b-client")
    assert store.scope_for(MEMBER_B) == personal(MEMBER_B)
    assert store.scope_for(MEMBER_A) is None


def test_capacity_counts_what_is_used_here_and_elsewhere_on_that_account_only(
    conn: sqlite3.Connection, store: CredentialStore
) -> None:
    save(store, personal(MEMBER_A), MEMBER_A, "a-client", used=4)
    save(store, HOUSEHOLD, None, "household-client")
    connect(conn, personal(MEMBER_A), "item-a1")
    connect(conn, personal(MEMBER_A), "item-a2")
    connect(conn, HOUSEHOLD, "item-h1")
    Registry(conn).remove("item-a2", at=NOW)

    mine, household = store.capacity(personal(MEMBER_A)), store.capacity(HOUSEHOLD)

    assert mine is not None
    assert (mine.used, mine.remaining) == (5, 5)
    assert household is not None
    assert (household.used, household.remaining) == (1, 9)


def test_an_account_with_connections_keeps_its_client_but_may_change_its_secret(
    conn: sqlite3.Connection, store: CredentialStore
) -> None:
    save(store, personal(MEMBER_A), MEMBER_A, "a-client")
    connect(conn, personal(MEMBER_A), "item-a1")

    save(store, personal(MEMBER_A), MEMBER_A, "a-client")
    with pytest.raises(CredentialsInUseError):
        save(store, personal(MEMBER_A), MEMBER_A, "another-client")
    with pytest.raises(CredentialsInUseError):
        store.remove(personal(MEMBER_A))

    Registry(conn).remove("item-a1", at=NOW)
    store.remove(personal(MEMBER_A))
    assert store.get(personal(MEMBER_A)) is None


def test_an_account_with_connections_keeps_its_environment(
    conn: sqlite3.Connection, store: CredentialStore
) -> None:
    save(store, personal(MEMBER_A), MEMBER_A, "a-client", environment=Environment.SANDBOX)
    connect(conn, personal(MEMBER_A), "item-a1")

    with pytest.raises(CredentialsInUseError):
        save(store, personal(MEMBER_A), MEMBER_A, "a-client", environment=Environment.PRODUCTION)

    stored = store.get(personal(MEMBER_A))
    assert stored is not None
    assert stored.environment is Environment.SANDBOX
    Registry(conn).remove("item-a1", at=NOW)
    save(store, personal(MEMBER_A), MEMBER_A, "a-client", environment=Environment.PRODUCTION)
    stored = store.get(personal(MEMBER_A))
    assert stored is not None
    assert stored.environment is Environment.PRODUCTION


def test_keys_are_sealed_to_their_own_set(conn: sqlite3.Connection, store: CredentialStore) -> None:
    save(store, personal(MEMBER_A), MEMBER_A, "a-client")
    save(store, personal(MEMBER_B), MEMBER_B, "b-client")
    columns = [row[1] for row in conn.execute("PRAGMA table_info(vault_credential)")]
    assert not {"client_id", "secret", "value", "plaintext"} & set(columns)

    conn.execute("DELETE FROM vault_credential WHERE scope = ?", (personal(MEMBER_B),))
    conn.execute(
        "UPDATE vault_credential SET scope = ? WHERE scope = ?",
        (personal(MEMBER_B), personal(MEMBER_A)),
    )

    with pytest.raises(TokenTamperedError):
        store.credentials(personal(MEMBER_B))


def test_rotating_the_master_key_keeps_every_credential_readable(
    conn: sqlite3.Connection, store: CredentialStore, keys: MemoryKeyStore
) -> None:
    save(store, personal(MEMBER_A), MEMBER_A, "a-client")
    vault = TokenVault(conn, keys)
    vault.put(MEMBER_A, "item-a1", Secret("access-synthetic"))

    vault.rotate()

    assert keys.versions() == [2]
    assert store.credentials(personal(MEMBER_A)).client_id == Secret("a-client")
    assert vault.get(MEMBER_A, "item-a1") == Secret("access-synthetic")
