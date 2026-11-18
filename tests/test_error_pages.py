"""Error pages (D-125, SPEC §10.1): our own pages with the security headers, an empty
body and a toast for htmx, JSON only under /api/."""

import html
import json
import logging
from typing import Annotated

import pytest
from fastapi import Depends, HTTPException, Request
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.security import UserFacingHTTPException
from app.services.auth_service import require_user
from tests.helpers import csrf_token, log_in
from tests.test_security_headers import assert_security_headers

HTMX = {"HX-Request": "true"}


def toast(response) -> str:
    return json.loads(response.headers["HX-Trigger"])["toast"]["message"]


def assert_page(response, status, heading):
    assert response.status_code == status
    assert response.headers["content-type"].startswith("text/html")
    assert heading in html.unescape(response.text)
    assert 'href="/"' in response.text  # "Back to Unfurl"
    assert_security_headers(response)
    assert response.headers["Cache-Control"] == "no-store"


# --- 404 ------------------------------------------------------------------------------------


def test_an_unknown_address_logged_out(client):
    assert_page(client.get("/no-such-page"), 404, "Page not found")


def test_an_unknown_address_logged_in(auth_client):
    assert_page(auth_client.get("/no-such-page"), 404, "Page not found")


def test_a_routes_own_404_gets_the_page(auth_client):
    response = auth_client.get("/applications/999999")

    assert_page(response, 404, "Page not found")
    assert "detail" not in response.text


def test_a_404_for_htmx_is_an_empty_body_with_a_toast(auth_client):
    response = auth_client.get("/applications/999999", headers=HTMX)

    assert response.status_code == 404
    assert response.text == ""
    assert toast(response) == "This item no longer exists. Reload the page to see the latest."
    assert_security_headers(response)


# --- 403, 405, 422 --------------------------------------------------------------------------


def test_a_refused_csrf_token_is_a_page_not_json(auth_client):
    response = auth_client.post("/settings/theme", data={"theme": "dark"})

    assert_page(response, 403, "Your session changed. Please reload the page.")


def test_a_refused_csrf_token_for_htmx_says_to_reload(auth_client):
    response = auth_client.post("/settings/theme", data={"theme": "dark"}, headers=HTMX)

    assert response.status_code == 403
    assert response.text == ""
    assert toast(response) == "Your session changed. Please reload the page."


def test_a_wrong_method_is_a_page(client):
    assert_page(client.put("/login", headers={"X-CSRF-Token": csrf_token(client)}), 405, "Not")


def test_a_form_value_that_cant_be_read_keeps_422(auth_client):
    response = auth_client.post(
        "/settings/theme", data={"theme": "moon", "csrf_token": csrf_token(auth_client)}
    )

    assert_page(response, 422, "This request couldn't be read")


def test_a_422_for_htmx_has_a_toast(auth_client):
    response = auth_client.post(
        "/settings/theme",
        data={"theme": "moon"},
        headers=HTMX | {"X-CSRF-Token": csrf_token(auth_client)},
    )

    assert response.status_code == 422
    assert response.text == ""
    assert "couldn't be read" in toast(response)


def test_a_rate_limit_for_htmx_has_a_toast(auth_client):
    token = csrf_token(auth_client)
    for _ in range(31):
        response = auth_client.post(
            "/settings/theme", data={"theme": "dark"}, headers=HTMX | {"X-CSRF-Token": token}
        )

    assert response.status_code == 429
    assert response.text == ""
    assert toast(response).startswith("Too many requests.")


# --- A test app with routes that fail --------------------------------------------------------


@pytest.fixture
def broken(engine):
    """The real app (debug off) with routes that raise, and the test database."""
    app = create_app(Settings(_env_file=None, app_env="development", debug=False))

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret detail in the error")

    @app.get("/boom-after-commit")
    def boom_after_commit(
        session: Annotated[Session, Depends(get_session)],
        user: Annotated[object, Depends(require_user)],
    ):
        session.commit()  # expires the user: the shared templates would read user.theme
        raise RuntimeError("after commit")

    @app.get("/unverified")
    def unverified():
        raise UserFacingHTTPException(status_code=403, detail="Please verify your email first.")

    @app.get("/internal-detail")
    def internal_detail():
        raise HTTPException(status_code=409, detail="IntegrityError: duplicate key value")

    @app.get("/api/thing")
    def api_thing(request: Request):
        raise HTTPException(status_code=404)

    @app.get("/items/{item_id}")
    def item(item_id: int):
        return {"id": item_id}

    def test_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    return TestClient(app, raise_server_exceptions=False)


def test_a_server_error_shows_our_page_with_the_headers(broken, caplog):
    with caplog.at_level(logging.ERROR, logger="app.errors"):
        response = broken.get("/boom?token=abc123")

    assert_page(response, 500, "Something went wrong")
    assert "secret detail" not in response.text
    assert "Traceback" not in response.text
    assert "Server error on GET /boom" in caplog.text
    assert "abc123" not in caplog.text  # never the query string


def test_a_server_error_after_a_commit_still_shows_the_page(broken, user):
    log_in(broken)

    response = broken.get("/boom-after-commit")

    assert_page(response, 500, "Something went wrong")


def test_a_server_error_for_htmx_has_a_toast(broken):
    response = broken.get("/boom", headers=HTMX)

    assert response.status_code == 500
    assert response.text == ""
    assert toast(response) == "Something went wrong. Please try again."
    assert_security_headers(response)


def test_the_page_falls_back_to_plain_text(broken, monkeypatch):
    from app import errors

    def fail(*args, **kwargs):
        raise RuntimeError("template broken")

    monkeypatch.setattr(errors.error_templates, "TemplateResponse", fail)

    response = broken.get("/boom")

    assert response.status_code == 500
    assert response.text == "Something went wrong."
    assert_security_headers(response)
    assert response.headers["Cache-Control"] == "no-store"


def test_our_own_403_text_is_shown(broken):
    assert_page(broken.get("/unverified"), 403, "Please verify your email first.")
    assert toast(broken.get("/unverified", headers=HTMX)) == "Please verify your email first."


def test_an_internal_detail_is_never_shown(broken):
    # Only UserFacingHTTPException details reach the page (security review L2).
    page = broken.get("/internal-detail")

    assert_page(page, 409, "This changed in the meantime")
    assert "IntegrityError" not in page.text
    assert "IntegrityError" not in toast(broken.get("/internal-detail", headers=HTMX))


def test_a_bad_id_in_the_path_is_a_404(broken):
    assert_page(broken.get("/items/abc"), 404, "Page not found")
    assert broken.get("/items/7").json() == {"id": 7}


def test_api_paths_keep_json(broken):
    response = broken.get("/api/thing")

    assert response.status_code == 404
    # The extension API's error format (SPEC §8, D-137).
    assert response.json() == {"error": {"code": "not_found", "message": "Not found."}}
