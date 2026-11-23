"""Host header check (#85, D-168): the app only answers to its own host names, so a
page using DNS rebinding (its own domain pointing at 127.0.0.1) gets 400."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_config import make_production_settings, make_settings


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8100", "attacker.test", "[::1]"])
def test_an_unknown_host_is_refused(client, host):
    response = client.get("/login", headers={"host": host})

    assert response.status_code == 400
    # Still our security headers (D-120): the middleware runs inside SecurityHeaders.
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in response.headers


@pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1:8100", "localhost", "testserver"])
def test_our_own_hosts_work(client, host):
    assert client.get("/login", headers={"host": host}).status_code == 200


def test_the_extension_api_refuses_an_unknown_host_first(client):
    response = client.post(
        "/api/ext/login",
        headers={"host": "evil.example", "content-type": "application/json"},
        content=b'{"email": "a@b.c", "password": "x"}',
    )

    assert response.status_code == 400


def test_the_base_url_name_is_allowed_in_production():
    settings = make_production_settings(app_base_url="https://unfurl.example.com")
    app = create_app(settings)

    assert TestClient(app, base_url="https://unfurl.example.com").get("/login").status_code == 200
    assert TestClient(app, base_url="http://localhost").get("/login").status_code == 200
    # The test client's own name is only allowed in development.
    assert TestClient(app).get("/login").status_code == 400
    assert TestClient(app, base_url="https://evil.example").get("/login").status_code == 400


def test_allowed_hosts():
    assert make_settings(app_env="development").allowed_hosts == [
        "localhost",
        "127.0.0.1",
        "testserver",
    ]
    personal = make_settings(app_env="development", app_base_url="http://127.0.0.1:8100")
    assert personal.allowed_hosts == ["localhost", "127.0.0.1", "testserver"]
    production = make_production_settings(app_base_url="https://Unfurl.Example.com/")
    assert production.allowed_hosts == ["localhost", "127.0.0.1", "unfurl.example.com"]
