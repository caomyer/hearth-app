"""Plaid Link coming back from a bank that signs a member in on its own page (OAuth).

Link opens such a bank one of two ways and decides which itself: in a window of its own, or by
handing the bank the whole tab and bringing the member back to ``/connect/oauth``. Both must
finish, or the bank's sign-in creates an Item at Plaid, holding one of the account's few slots,
that hearth never receives. These run against the stand-in, posing as Plaid's production: no
request leaves the process.
"""

from dataclasses import replace

import pytest

from hearth.cli import redirect_uri
from hearth.web.routes.connect import LINK_OPENER, LINK_POLICY
from tests.conftest import World
from tests.plaid.linking import RETURN, SERVED_AT, Relay, as_production, dana_at, opened_token


@pytest.fixture
def relay(world: World) -> Relay:
    return as_production(world)


def test_every_link_token_carries_the_way_back_hearth_serves(world: World, relay: Relay) -> None:
    dana = dana_at(world, SERVED_AT)

    opened = dana.post("/connect/link", data={"csrf": world.csrf(dana)})

    assert opened.status_code == 200
    (sent,) = relay.link_requests()
    assert (
        sent
        == [
            {
                "client_name": "Hearth",
                "language": "en",
                "country_codes": ["US"],
                "user": sent["user"],
                "redirect_uri": RETURN,
                "products": ["transactions"],
                "optional_products": ["investments"],
                "transactions": {"days_requested": 730},
            }
        ][0]
    )
    page = opened.text
    assert opened_token(page).startswith("link-demo-")
    assert 'data-expires="' in page
    assert 'id="exit-form"' in page
    assert 'action="/connect/exited"' in page


def test_the_link_pages_keep_the_bank_window_they_open_and_no_other_page_does(
    world: World, relay: Relay
) -> None:
    dana = dana_at(world, SERVED_AT)

    opened = dana.post("/connect/link", data={"csrf": world.csrf(dana)})
    returned = dana.get("/connect/oauth?oauth_state_id=0f0f0f0f-synthetic")
    elsewhere = [dana.get(path) for path in ("/connect", "/connect/map", "/you")]

    for page in (opened, returned):
        assert page.headers["Cross-Origin-Opener-Policy"] == "same-origin-allow-popups"
        assert LINK_OPENER == "same-origin-allow-popups"
        assert page.headers["Content-Security-Policy"] == LINK_POLICY
    for page in elsewhere:
        assert page.headers["Cross-Origin-Opener-Policy"] == "same-origin"
        assert "cdn.plaid.com" not in page.headers["Content-Security-Policy"]


def test_the_way_back_carries_link_on_and_holds_no_token(world: World, relay: Relay) -> None:
    dana = dana_at(world, SERVED_AT)
    dana.post("/connect/link", data={"csrf": world.csrf(dana)})

    returned = dana.get("/connect/oauth?oauth_state_id=0f0f0f0f-synthetic")

    assert returned.status_code == 200
    page = returned.text
    assert "Finishing with your bank" in page
    assert "data-resume" in page
    assert "data-link-token" not in page
    assert "link-demo-" not in page
    assert 'src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"' in page
    assert 'src="/static/connect.js"' in page
    # Whatever Link finishes is posted to the same steps as a window that never left.
    for action in ("/connect/exchange", "/connect/reconnected", "/connect/exited"):
        assert f'action="{action}"' in page
    # Cancelling here comes after a bank's sign-in, which may have made an Item.
    assert 'href="/connect?outcome=link-left"' in page
    # Nothing Plaid was asked for on the way back.
    assert len(relay.sent) == 1


def test_coming_back_signed_out_signs_in_and_returns_to_the_same_address(world: World) -> None:
    visitor = world.anonymous()

    returned = visitor.get("/connect/oauth?oauth_state_id=0f0f0f0f-synthetic")

    assert returned.status_code == 303
    assert returned.headers["location"] == (
        "/sign-in?next=%2Fconnect%2Foauth%3Foauth_state_id%3D0f0f0f0f-synthetic"
    )


def test_link_opens_only_from_the_address_plaid_brings_the_member_back_to(
    world: World, relay: Relay
) -> None:
    dana = dana_at(world, "https://127.0.0.1:8000")

    refused = dana.post("/connect/link", data={"csrf": world.csrf(dana)})
    page = dana.get(refused.headers["location"]).text

    assert refused.headers["location"] == "/connect?outcome=wrong-address"
    assert f"Connect from {SERVED_AT}." in page
    assert relay.sent == []


def test_link_opens_from_https_on_its_default_port_the_browser_names_without_one(
    world: World,
) -> None:
    relay = as_production(world, redirect_uri=redirect_uri(443))
    dana = dana_at(world, "https://localhost")

    opened = dana.post("/connect/link", data={"csrf": world.csrf(dana)})

    assert opened.status_code == 200
    assert opened_token(opened.text).startswith("link-demo-")
    (sent,) = relay.link_requests()
    assert sent["redirect_uri"] == "https://localhost/connect/oauth"


def test_without_https_link_opens_as_before_with_the_bank_window_kept(world: World) -> None:
    """Served without a certificate, hearth offers Plaid no way back, so Link opens each bank's
    own sign-in in a window of its own, from wherever hearth is reached."""
    relay = as_production(world, redirect_uri=None)
    dana = dana_at(world, "http://127.0.0.1:8000")

    page = dana.get("/connect").text
    opened = dana.post("/connect/link", data={"csrf": world.csrf(dana)})

    assert "allowed redirect URIs" not in page
    assert 'action="/connect/link"' in page
    assert opened.status_code == 200
    assert opened.headers["Cross-Origin-Opener-Policy"] == "same-origin-allow-popups"
    (sent,) = relay.link_requests()
    assert "redirect_uri" not in sent


def test_plaids_production_is_never_sent_a_plain_http_way_back(world: World) -> None:
    relay = as_production(world, redirect_uri="http://localhost:8000/connect/oauth")
    dana = dana_at(world, "http://localhost:8000")

    opened = dana.post("/connect/link", data={"csrf": world.csrf(dana)})

    assert opened.status_code == 200
    (sent,) = relay.link_requests()
    assert "redirect_uri" not in sent


def test_the_page_names_the_redirect_uri_to_allow_in_plaid(world: World, relay: Relay) -> None:
    page = dana_at(world, SERVED_AT).get("/connect").text

    assert f"Plaid brings you back to <code>{RETURN}</code>" in page
    assert "allowed redirect URIs, exactly as written here" in page


def test_signing_in_again_comes_back_where_it_can(world: World) -> None:
    relay = as_production(world, redirect_uri=None)
    hearth = world.app.state.hearth
    # Connected while hearth was served over HTTPS.
    world.app.state.hearth = replace(hearth, plaid=replace(hearth.plaid, redirect_uri=RETURN))
    dana = dana_at(world, SERVED_AT)
    before = set(world.plaid.items())
    token = opened_token(dana.post("/connect/link", data={"csrf": world.csrf(dana)}).text)
    public = world.plaid.public_token(token, "ins_casey")
    dana.post("/connect/exchange", data={"csrf": world.csrf(dana), "public_token": public})
    (item_id,) = set(world.plaid.items()) - before
    world.plaid.require_login(item_id)

    over_https = dana.post("/connect/link", data={"csrf": world.csrf(dana), "item_id": item_id})
    # Then served without a certificate: Link signs in again in the bank's window alone.
    world.app.state.hearth = hearth
    dana = dana_at(world, "http://localhost:8000")
    over_http = dana.post("/connect/link", data={"csrf": world.csrf(dana), "item_id": item_id})

    for opened in (over_https, over_http):
        assert opened.status_code == 200
        assert f'data-item="{item_id}"' in opened.text
    first, again_https, again_http = relay.link_requests()
    assert first["redirect_uri"] == again_https["redirect_uri"] == RETURN
    assert "redirect_uri" not in again_http
    assert "access_token" in again_https
    assert "access_token" in again_http


@pytest.mark.parametrize(
    ("fields", "outcome"),
    [
        ({}, "link-closed"),
        ({"status": "requires_credentials", "last_event": "SELECT_INSTITUTION"}, "link-closed"),
        ({"status": "institution_not_found"}, "link-closed"),
        ({"status": "requires_oauth", "last_event": "OPEN_OAUTH"}, "link-left"),
        # The bank's own sign-in had opened: whatever step Link names, an Item may exist.
        ({"status": "requires_credentials", "last_event": "CLOSE_OAUTH"}, "link-left"),
        ({"status": "requires_account_selection"}, "link-left"),
        ({"status": "requires_credentials", "resumed": "1"}, "link-left"),
        ({"resumed": "1", "lost": "1"}, "link-lost"),
        (
            {"error_type": "INSTITUTION_ERROR", "error_code": "INSTITUTION_DOWN"},
            "link-failed&code=INSTITUTION_DOWN",
        ),
    ],
)
def test_closing_link_tells_the_member_what_it_can_have_cost(
    world: World, fields: dict[str, str], outcome: str
) -> None:
    casey = world.signed_in("casey")

    closed = casey.post("/connect/exited", data={"csrf": world.csrf(casey), **fields})

    assert closed.status_code == 303
    assert closed.headers["location"] == f"/connect?outcome={outcome}"


def test_what_link_says_on_closing_is_kept_and_shown_with_plaids_request_id(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    casey = world.signed_in("casey")
    sent = {
        "error_type": "OAUTH_ERROR",
        "error_code": "OAUTH_STATE_ID_ALREADY_PROCESSED",
        "status": "requires_oauth",
        "last_event": "FAIL_OAUTH",
        "institution_id": "ins_000777",
        "institution_name": "Kestrel Trust\nforged line",
        "request_id": "Zq7RequestSynthetic01",
        "link_session_id": "4b1d-synthetic-session",
    }

    with caplog.at_level("INFO", logger="hearth"):
        closed = casey.post("/connect/exited", data={"csrf": world.csrf(casey), **sent})
    page = casey.get(closed.headers["location"]).text

    assert closed.headers["location"] == (
        "/connect?outcome=link-failed&code=OAUTH_STATE_ID_ALREADY_PROCESSED"
        "&request=Zq7RequestSynthetic01"
    )
    assert "check your Plaid dashboard before you try again" in page
    assert "<code>OAUTH_STATE_ID_ALREADY_PROCESSED</code>" in page
    assert "<code>Zq7RequestSynthetic01</code>" in page
    (record,) = [r for r in caplog.records if r.name == "hearth.web.routes.connect"]
    line = record.getMessage()
    assert "\n" not in line
    for value in [v for k, v in sent.items() if k != "institution_name"]:
        assert value in line
    assert "Kestrel Trustforged line" in line


def test_nothing_link_sends_on_closing_reaches_the_log_unless_plaid_shaped(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    casey = world.signed_in("casey")
    junk = {
        "error_type": "public-production-synthetic-token",
        "error_code": "access-production-synthetic-token",
        "status": "Requires Credentials; DROP",
        "last_event": "open oauth\n",
        "institution_id": "ins 1 <script>",
        "request_id": "req id with spaces",
        "link_session_id": "x" * 65,
    }

    with caplog.at_level("INFO", logger="hearth"):
        closed = casey.post("/connect/exited", data={"csrf": world.csrf(casey), **junk})

    assert closed.headers["location"] == "/connect?outcome=link-closed"
    for value in junk.values():
        assert value.strip() not in caplog.text


def test_closing_link_is_a_form_of_ones_own(world: World) -> None:
    casey = world.signed_in("casey")

    forged = casey.post("/connect/exited", data={"error_code": "INSTITUTION_DOWN"})

    assert forged.status_code == 403


def test_a_refused_call_names_plaids_request_id_for_its_support(world: World) -> None:
    casey = world.signed_in("casey")
    item_id = world.item_ids["casey"]
    world.plaid.fail_next("/item/remove", "INTERNAL_SERVER_ERROR")

    refused = casey.post(f"/connect/items/{item_id}/remove", data={"csrf": world.csrf(casey)})
    page = casey.get(refused.headers["location"]).text

    assert refused.headers["location"] == (
        "/settings/connections?outcome=plaid-failed&code=INTERNAL_SERVER_ERROR&request=demo"
    )
    assert "Plaid did not complete that." in page
    assert "Plaid's request ID, for its support: <code>demo</code>." in page
