"""M13a gaps: odd requests on the error pages, sign-out with forged or repeated
cookies, and Settings › Documents edge cases (D-125, D-128, D-129)."""

import base64
import html
import json
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlmodel import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.main import SESSION_COOKIE, app, create_app
from app.models import User
from app.security import UserFacingHTTPException
from app.services.auth_service import create_user
from tests.helpers import USER_EMAIL, csrf_token, log_in
from tests.test_security_headers import assert_security_headers

HTMX = {"HX-Request": "true"}


def toast(response) -> str:
    return json.loads(response.headers["HX-Trigger"])["toast"]["message"]


def assert_not_found_page(response):
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "Page not found" in html.unescape(response.text)
    assert_security_headers(response)


# --- Error pages: odd paths ------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/" + "a" * 5000,
        "/ğüşçö/日本語",
        "/a%00b",
        "/%2e%2e/%2e%2e/etc/passwd",
        "/applications/..%2f..%2fsettings",
    ],
)
def test_odd_paths_get_the_404_page(client, path):
    assert_not_found_page(client.get(path))


@pytest.mark.parametrize(
    "path",
    [
        "/<script>alert('xss')</script>",
        "/%3Cscript%3Ealert('xss')%3C/script%3E",
        "/x?q=<script>alert('xss')</script>",
        "/x?q=%22%3E%3Cimg%20src=x%20onerror=alert('xss')%3E",
    ],
)
def test_the_404_page_never_echoes_the_address(client, path):
    response = client.get(path)

    assert_not_found_page(response)
    assert "alert(" not in response.text
    assert "<script>alert" not in response.text


def test_a_404_under_static_is_our_page(client):
    response = client.get("/static/css/no-such-file.css")

    assert_not_found_page(response)


@pytest.mark.parametrize("path", ["/static/..%2f..%2fapp%2fconfig.py", "/static/%2e%2e/main.py"])
def test_static_files_cant_leave_the_static_folder(client, path):
    response = client.get(path)

    assert response.status_code == 404
    assert "secret_key" not in response.text
    assert "create_app" not in response.text


def test_a_head_request_to_an_unknown_address_is_a_404(client):
    response = client.head("/no-such-page")

    assert response.status_code == 404
    assert_security_headers(response)
    assert response.headers["Cache-Control"] == "no-store"


def test_a_logged_in_404_does_not_show_the_users_name(auth_client):
    response = auth_client.get("/no-such-page")

    assert_not_found_page(response)
    assert "Jana" not in response.text
    assert USER_EMAIL not in response.text


def test_a_405_keeps_the_allow_header(client):
    response = client.put("/login", headers={"X-CSRF-Token": csrf_token(client)})

    assert response.status_code == 405
    assert "GET" in response.headers["Allow"]


def test_a_405_for_htmx_is_an_empty_body_with_a_toast(auth_client):
    headers = HTMX | {"X-CSRF-Token": csrf_token(auth_client)}
    response = auth_client.put("/settings", headers=headers)

    assert response.status_code == 405
    assert response.text == ""
    assert toast(response) == "Something went wrong. Please reload the page."


def test_logging_out_with_get_is_not_allowed(auth_client):
    response = auth_client.get("/logout")

    assert response.status_code == 405
    assert auth_client.get("/", follow_redirects=False).status_code == 200


def test_a_json_body_on_a_form_route_is_a_422_page(auth_client):
    response = auth_client.post(
        "/settings/theme",
        json={"theme": "dark"},
        headers={"X-CSRF-Token": csrf_token(auth_client)},
    )

    assert response.status_code == 422
    assert "This request couldn't be read" in html.unescape(response.text)
    assert "loc" not in response.text  # not FastAPI's JSON error list


# --- Error pages: the theme cookie ------------------------------------------------------------


@pytest.mark.parametrize(
    "cookie, theme",
    [
        ("dark", "dark"),
        ("light", "light"),
        ("", "system"),
        ("DARK", "system"),
        ('"><script>alert(1)</script>', "system"),
        ("dark x", "system"),
    ],
)
def test_the_error_page_only_trusts_known_themes(engine, cookie, theme):
    # A fresh client per case: TestClient keeps cookies between requests.
    with TestClient(app, cookies={"theme": cookie}) as visitor:
        response = visitor.get("/no-such-page")

    assert response.status_code == 404
    assert f'class="theme-{theme}"' in response.text
    assert "<script>alert" not in response.text


# --- Error pages: a test app with /api/ routes ------------------------------------------------


@pytest.fixture
def api_app(engine):
    test_app = create_app(Settings(_env_file=None, app_env="development", debug=False))

    @test_app.get("/api/boom")
    def api_boom():
        raise RuntimeError("secret detail")

    @test_app.get("/api/items/{item_id}")
    def api_item(item_id: int):
        return {"id": item_id}

    @test_app.get("/echo")
    def echo(q: str):
        # A plain HTTPException's text is internal: it never reaches the page.
        raise HTTPException(status_code=400, detail=f"Bad value: {q}")

    @test_app.get("/echo-user")
    def echo_user(q: str):
        # Text written for the user, with user input in it: the page must escape it.
        raise UserFacingHTTPException(status_code=400, detail=f"Bad value: {q}")

    def test_session():
        with Session(engine) as session:
            yield session

    test_app.dependency_overrides[get_session] = test_session
    return TestClient(test_app, raise_server_exceptions=False)


def test_a_server_error_under_api_is_json_with_the_headers(api_app):
    response = api_app.get("/api/boom")

    assert response.status_code == 500
    assert response.json() == {"detail": "Server error"}
    assert "secret detail" not in response.text
    assert_security_headers(response)
    assert response.headers["Cache-Control"] == "no-store"


def test_a_bad_id_under_api_keeps_a_json_422(api_app):
    response = api_app.get("/api/items/abc")

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["path", "item_id"]


def test_a_plain_http_errors_text_is_never_shown(api_app):
    page = api_app.get("/echo", params={"q": "<script>alert(1)</script>"})
    htmx = api_app.get("/echo", params={"q": "internal"}, headers=HTMX)

    assert page.status_code == 400
    assert "Bad value" not in page.text
    assert "alert(1)" not in page.text
    assert toast(htmx) == "Something went wrong. Please reload the page."


def test_user_facing_text_is_escaped_on_the_page(api_app):
    response = api_app.get("/echo-user", params={"q": "<script>alert(1)</script>"})

    assert response.status_code == 400
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_user_facing_text_in_a_toast_is_valid_json(api_app):
    response = api_app.get("/echo-user", params={"q": '"}, "x": "ü'}, headers=HTMX)

    assert response.status_code == 400
    assert toast(response) == 'Bad value: "}, "x": "ü'


def test_a_missing_query_value_keeps_422_not_404(api_app):
    # Only a bad value in the *path* becomes a 404 (D-125).
    response = api_app.get("/echo")

    assert response.status_code == 422
    assert "This request couldn't be read" in html.unescape(response.text)


# --- Sign out: repeated, forged and odd cookies ----------------------------------------------


def sign_out(client, token=None):
    return client.post(
        "/logout",
        data={"csrf_token": token or csrf_token(client)},
        follow_redirects=False,
    )


def version(engine, user) -> int:
    with Session(engine) as check:
        return check.get(User, user.id).session_version


def session_cookie(data: dict, secret: str | None = None) -> str:
    """A session cookie like Starlette's SessionMiddleware makes it, signed with `secret`
    (the app's own key by default)."""
    secret = secret or get_settings().secret_key.get_secret_value()
    payload = base64.b64encode(json.dumps(data).encode())
    return TimestampSigner(secret).sign(payload).decode()


def test_two_sign_outs_with_the_same_cookie_count_once(auth_client, engine, user):
    # Like two tabs signing out at the same time with the same cookie.
    copy = TestClient(app, cookies=dict(auth_client.cookies))
    token = csrf_token(auth_client)

    sign_out(auth_client, token)
    response = sign_out(copy, token)

    assert response.status_code == 303
    assert version(engine, user) == 1


def test_signing_out_does_not_touch_another_user(client, session, engine, user):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    other_client = TestClient(app)
    log_in(other_client, email="other@example.com", password="x" * 12)
    log_in(client)

    sign_out(client)

    assert version(engine, other) == 0
    assert other_client.get("/", follow_redirects=False).status_code == 200


def test_a_cookie_signed_with_another_key_cant_sign_anyone_out(auth_client, engine, user):
    forged = session_cookie(
        {"user_id": user.id, "session_version": 0, "csrf": "t"}, secret="not-the-key" * 4
    )
    attacker = TestClient(app, cookies={SESSION_COOKIE: forged})

    response = attacker.post("/logout", data={"csrf_token": "t"}, follow_redirects=False)

    assert response.status_code == 403  # the forged session is ignored, so no CSRF token
    assert version(engine, user) == 0
    assert auth_client.get("/", follow_redirects=False).status_code == 200


@pytest.mark.parametrize(
    "data",
    [
        {"user_id": "1", "session_version": 0},  # id as text
        {"user_id": 1, "session_version": "0"},  # version as text
        {"user_id": 1, "session_version": None},
        {"user_id": 1},  # no version at all
        {"user_id": 999999, "session_version": 0},  # a user that doesn't exist
    ],
)
def test_odd_session_values_sign_out_harmlessly(auth_client, engine, user, data):
    cookie = session_cookie(data | {"csrf": "t"})
    odd = TestClient(app, cookies={SESSION_COOKIE: cookie})

    response = odd.post("/logout", data={"csrf_token": "t"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert version(engine, user) == 0
    assert auth_client.get("/", follow_redirects=False).status_code == 200


def test_signing_out_clears_this_browsers_session(auth_client):
    sign_out(auth_client)

    response = auth_client.get("/settings", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_signing_out_twice_in_one_browser_is_harmless(auth_client, engine, user):
    sign_out(auth_client)
    response = sign_out(auth_client)

    assert response.status_code == 303
    assert version(engine, user) == 1


# --- Settings › Documents ---------------------------------------------------------------------


def save(client, data, headers=None):
    return client.post(
        "/settings/documents",
        data=data,
        headers=(headers or HTMX) | {"X-CSRF-Token": csrf_token(client)},
    )


def saved_language(engine, user) -> str:
    with Session(engine) as check:
        return check.get(User, user.id).default_doc_language


def new_cv_language(client) -> str:
    page = client.get("/documents").text
    form = page[page.index('id="new-cv-language"') :].split("</select>")[0]
    for code in ("en", "de", "tr"):
        if f'value="{code}" selected' in form:
            return code
    raise AssertionError("no language selected")


@pytest.mark.parametrize(
    "values",
    [
        [("default_doc_language", "de"), ("default_doc_language", "fr")],
        [("default_doc_language", "fr"), ("default_doc_language", "de")],
        [("default_doc_language", "de"), ("default_doc_language", "tr")],
    ],
)
def test_several_values_never_save_an_unknown_language(auth_client, engine, user, values):
    response = auth_client.post(
        "/settings/documents",
        content=urlencode(values),
        headers=HTMX
        | {
            "X-CSRF-Token": csrf_token(auth_client),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    assert response.status_code == 200
    saved = saved_language(engine, user)
    assert saved in ("en", "de", "tr")
    # The value that was saved is the one the redrawn form shows.
    assert f'value="{saved}" selected' in response.text


def test_a_normal_form_post_saves_too(auth_client, engine, user):
    response = auth_client.post(
        "/settings/documents",
        data={"default_doc_language": "tr", "csrf_token": csrf_token(auth_client)},
    )

    assert response.status_code == 200
    assert saved_language(engine, user) == "tr"


def test_a_json_body_saves_nothing(auth_client, engine, user):
    response = auth_client.post(
        "/settings/documents",
        json={"default_doc_language": "de"},
        headers=HTMX | {"X-CSRF-Token": csrf_token(auth_client)},
    )

    assert response.status_code in (200, 422)
    assert saved_language(engine, user) == "en"


def test_a_missing_field_saves_nothing(auth_client, engine, user):
    response = save(auth_client, {})

    assert response.status_code == 200
    assert "Choose one of the languages." in response.text
    assert saved_language(engine, user) == "en"


def test_a_refused_value_is_not_echoed_unescaped(auth_client):
    response = save(auth_client, {"default_doc_language": "<script>alert(1)</script>"})

    assert "<script>alert(1)</script>" not in response.text


def test_the_settings_page_shows_the_saved_language(auth_client):
    save(auth_client, {"default_doc_language": "tr"})

    page = auth_client.get("/settings").text
    form = page[page.index('id="document-settings"') :].split("</select>")[0]

    assert 'value="tr" selected' in form


def test_htmx_without_a_login_is_sent_to_the_login_page(client, engine, user):
    response = save(client, {"default_doc_language": "de"})

    assert response.headers["HX-Redirect"].startswith("/login")
    assert saved_language(engine, user) == "en"


def test_each_user_gets_their_own_new_cv_language(client, session, user):
    create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    other_client = TestClient(app)
    log_in(other_client, email="other@example.com", password="x" * 12)
    log_in(client)

    save(other_client, {"default_doc_language": "tr"})

    assert new_cv_language(other_client) == "tr"
    assert new_cv_language(client) == "en"


def test_refused_saves_count_towards_the_rate_limit(auth_client, engine, user):
    # The limit is per request, so 30 refused values use it up too.
    for _ in range(30):
        save(auth_client, {"default_doc_language": "fr"})

    response = save(auth_client, {"default_doc_language": "de"})

    assert response.status_code == 429
    assert toast(response).startswith("Too many requests.")
    assert saved_language(engine, user) == "en"


def test_the_rate_limit_without_htmx_is_a_page(auth_client):
    token = csrf_token(auth_client)
    for _ in range(31):
        response = auth_client.post(
            "/settings/documents", data={"default_doc_language": "de", "csrf_token": token}
        )

    assert response.status_code == 429
    assert "Too many requests" in response.text
    assert_security_headers(response)
