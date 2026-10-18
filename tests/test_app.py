from tests.conftest import World


def test_health_check_answers(world: World) -> None:
    response = world.anonymous().get("/healthz")

    assert response.status_code == 200
    assert response.text == "ok"


def test_no_machine_readable_api_is_published(world: World) -> None:
    client = world.signed_in("avery")

    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404
