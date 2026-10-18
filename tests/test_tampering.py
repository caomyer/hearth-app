"""Attempts to change what someone else shares, or to act as an admin, must change nothing."""

import pytest

from hearth import sharing
from hearth.sharing import DEFAULT_SHARED, Facet
from tests.conftest import World


def snapshot(world: World) -> tuple[list[tuple[object, ...]], ...]:
    conn = world.connect()
    try:
        return tuple(
            [tuple(row) for row in conn.execute(query)]
            for query in (
                "SELECT * FROM members ORDER BY id",
                "SELECT * FROM sharing_preferences ORDER BY member_id, facet",
                "SELECT token_hash, revoked_at FROM sessions ORDER BY token_hash",
            )
        )
    finally:
        conn.close()


def shared_by(world: World, username: str) -> frozenset[Facet]:
    conn = world.connect()
    try:
        return sharing.load(conn)[world.member_ids[username]]
    finally:
        conn.close()


def test_a_member_changes_only_their_own_sharing_whatever_the_form_claims(world: World) -> None:
    casey = world.signed_in("casey")

    response = casey.post(
        "/settings/sharing/transactions",
        data={
            "csrf": world.csrf(casey),
            "shared": "true",
            "member": "blake",
            "username": "blake",
            "member_id": str(world.member_ids["blake"]),
        },
        params={"username": "blake", "member_id": str(world.member_ids["blake"])},
    )

    assert response.status_code == 303
    assert shared_by(world, "casey") == DEFAULT_SHARED | {Facet.TRANSACTIONS}
    assert shared_by(world, "blake") == DEFAULT_SHARED


def test_sharing_through_htmx_returns_only_the_changed_row(world: World) -> None:
    blake = world.signed_in("blake")

    response = blake.post(
        "/settings/sharing/account_balances",
        data={"shared": "true"},
        headers={"HX-Request": "true", "X-CSRF-Token": world.csrf(blake)},
    )

    assert response.status_code == 200
    assert response.text.lstrip().startswith('<li class="choice" id="share-account_balances">')
    assert 'aria-checked="true"' in response.text
    assert Facet.ACCOUNT_BALANCES in shared_by(world, "blake")


@pytest.mark.parametrize(
    ("method", "path", "form"),
    [
        ("POST", "/settings/sharing/transactions", {"shared": "true"}),
        ("POST", "/admin/members", {"username": "mallory", "display_name": "Mallory"}),
        ("POST", "/admin/members/blake/reset-password", {}),
        ("POST", "/settings/password", {"current": "x", "new": "y" * 12, "confirm": "y" * 12}),
        ("POST", "/sign-out", {}),
    ],
)
@pytest.mark.parametrize(
    "forgery",
    ["no token", "another session's token", "cross-site origin", "cross-site fetch"],
)
def test_a_forged_request_changes_nothing(
    world: World, method: str, path: str, form: dict[str, str], forgery: str
) -> None:
    avery = world.signed_in("avery")
    other = world.signed_in("blake")
    headers: dict[str, str] = {}
    match forgery:
        case "no token":
            pass
        case "another session's token":
            form = {**form, "csrf": world.csrf(other)}
        case "cross-site origin":
            form = {**form, "csrf": world.csrf(avery)}
            headers = {"Origin": "https://elsewhere.example"}
        case "cross-site fetch":
            form = {**form, "csrf": world.csrf(avery)}
            headers = {"Sec-Fetch-Site": "cross-site"}
    before = snapshot(world)

    response = avery.request(method, path, data=form, headers=headers)

    assert response.status_code == 403
    assert snapshot(world) == before


@pytest.mark.parametrize(
    ("path", "form"),
    [
        ("/admin/members", {"username": "mallory", "display_name": "Mallory"}),
        ("/admin/members/blake/reset-password", {}),
        ("/admin/members/avery/reset-password", {}),
    ],
)
def test_a_member_who_is_not_an_admin_cannot_administer(
    world: World, path: str, form: dict[str, str]
) -> None:
    casey = world.signed_in("casey")
    before = snapshot(world)

    response = casey.post(path, data={**form, "csrf": world.csrf(casey)})

    assert response.status_code == 404
    assert snapshot(world) == before


def test_an_admin_cannot_reset_their_own_password_behind_their_own_back(world: World) -> None:
    avery = world.signed_in("avery")
    before = snapshot(world)

    response = avery.post("/admin/members/avery/reset-password", data={"csrf": world.csrf(avery)})

    assert response.status_code == 404
    assert snapshot(world) == before


def test_reading_a_page_never_changes_anything(world: World) -> None:
    casey = world.signed_in("casey")
    before = snapshot(world)

    for path in (
        "/settings/sharing/transactions?shared=true",
        "/admin/members/blake/reset-password",
    ):
        assert casey.get(path).status_code == 404
    assert snapshot(world) == before


def test_an_unknown_facet_is_simply_not_there(world: World) -> None:
    casey = world.signed_in("casey")

    response = casey.post(
        "/settings/sharing/everything", data={"csrf": world.csrf(casey), "shared": "true"}
    )

    assert response.status_code == 404
    assert shared_by(world, "casey") == DEFAULT_SHARED


def test_the_app_database_holds_no_money(world: World) -> None:
    """Figures belong to the ledger; no column here is even named like one."""
    conn = world.connect()
    try:
        tables = [
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        ]
        columns = {
            (table, column[1])
            for table in tables
            for column in conn.execute(f"PRAGMA table_info({table})")
        }
    finally:
        conn.close()
    money_words = ("amount", "balance", "total", "value", "price", "worth")
    # A market price is not household money: the decisions keep price history in this
    # database, each price with its source and date (hearth.valuation). It is the one
    # column allowed a money word, and only in that table.
    market_data = {("price", "price")}
    assert not [
        (table, column)
        for table, column in columns
        if (table, column) not in market_data and any(word in column for word in money_words)
    ]
