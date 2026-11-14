import pytest
from fastapi import Depends, FastAPI, Form, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app.security import CSRF_HEADER, get_csrf_token, safe_redirect_path, verify_csrf
from app.templating import templates

# --- safe_redirect_path ---------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "//evil.com",
        "https://evil.com",
        "http://evil.com/login",
        "/\\evil.com",
        "\\\\evil.com",
        "javascript:alert(1)",
        "JAVASCRIPT:alert(1)",
        "evil.com",
        "settings",
        "/%2F%2Fevil.com",
        "/%5Cevil.com",
        "/settings\nLocation: https://evil.com",
        "/settings\x00",
        "",
        None,
    ],
)
def test_unsafe_redirects_fall_back_to_home(value):
    assert safe_redirect_path(value) == "/"


@pytest.mark.parametrize(
    "value",
    ["/", "/settings", "/applications?page=2&sort=desc", "/documents#cv", "/learning/goals/3"],
)
def test_paths_on_this_site_are_kept(value):
    assert safe_redirect_path(value) == value


def test_custom_default():
    assert safe_redirect_path("https://evil.com", default="/settings") == "/settings"


@pytest.mark.parametrize("back_url", ["javascript:alert(1)", "https://evil.com", "//evil.com"])
def test_page_header_never_links_off_site(back_url):
    html = templates.env.from_string(
        '{% from "components/page_header.html" import page_header %}'
        "{{ page_header('Profile', back_url=back_url) }}"
    ).render(back_url=back_url)
    assert '<a href="/"' in html
    assert "evil" not in html and "javascript" not in html


# --- CSRF -------------------------------------------------------------------------
# A small app with the same session middleware and CSRF dependency as Unfurl,
# so the check is tested on its own, before real forms exist.


def make_csrf_app() -> FastAPI:
    app = FastAPI(dependencies=[Depends(verify_csrf)])
    app.add_middleware(SessionMiddleware, secret_key="test-secret")

    @app.get("/token")
    def token(request: Request):
        return {"token": get_csrf_token(request)}

    @app.post("/form")
    def form(name: str = Form()):
        # The route still gets the form field after verify_csrf has read the form.
        return {"name": name}

    @app.post("/htmx")
    def htmx():
        return {"ok": True}

    @app.post("/api/ext/applications")
    def extension_api():
        return {"ok": True}

    return app


@pytest.fixture
def csrf_client() -> TestClient:
    return TestClient(make_csrf_app())


def fetch_token(client: TestClient) -> str:
    return client.get("/token").json()["token"]


def test_post_without_token_is_forbidden(csrf_client):
    fetch_token(csrf_client)  # the session has a token, but the form doesn't send it
    response = csrf_client.post("/form", data={"name": "Jana"})
    assert response.status_code == 403


def test_post_without_any_session_is_forbidden(csrf_client):
    response = csrf_client.post("/form", data={"name": "Jana", "csrf_token": "guess"})
    assert response.status_code == 403


def test_post_with_wrong_token_is_forbidden(csrf_client):
    fetch_token(csrf_client)
    response = csrf_client.post("/form", data={"name": "Jana", "csrf_token": "wrong"})
    assert response.status_code == 403


def test_post_with_non_ascii_token_is_forbidden_not_an_error(csrf_client):
    # compare_digest can't compare non-ASCII str directly; the check must say 403, not crash.
    fetch_token(csrf_client)
    response = csrf_client.post("/form", data={"name": "Jana", "csrf_token": "ğüş"})
    assert response.status_code == 403


def test_form_field_token_passes_and_form_data_still_reaches_the_route(csrf_client):
    token = fetch_token(csrf_client)
    response = csrf_client.post("/form", data={"name": "Jana", "csrf_token": token})
    assert response.status_code == 200
    assert response.json() == {"name": "Jana"}


def test_htmx_header_token_passes(csrf_client):
    token = fetch_token(csrf_client)
    response = csrf_client.post("/htmx", headers={CSRF_HEADER: token, "HX-Request": "true"})
    assert response.status_code == 200


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_other_changing_methods_are_checked_too(csrf_client, method):
    fetch_token(csrf_client)
    assert csrf_client.request(method, "/htmx").status_code in (403, 405)


def test_get_is_not_checked(csrf_client):
    assert csrf_client.get("/token").status_code == 200


def test_token_stays_the_same_within_a_session(csrf_client):
    assert fetch_token(csrf_client) == fetch_token(csrf_client)


def test_extension_api_is_exempt(csrf_client):
    assert csrf_client.post("/api/ext/applications").status_code == 200


# --- Session cookie flags (SPEC §6.1, §10.1) ---------------------------------------


def session_cookie_header(app: FastAPI) -> str:
    """Set-Cookie header of the session cookie after a request that writes the session."""

    @app.get("/test-session-cookie")
    def write_session(request: Request):
        get_csrf_token(request)
        return {}

    response = TestClient(app, base_url="https://testserver").get("/test-session-cookie")
    return response.headers["set-cookie"]


def test_session_cookie_flags_in_development():
    from app.config import Settings
    from app.main import create_app

    header = session_cookie_header(create_app(Settings(_env_file=None, app_env="development")))
    assert header.startswith("unfurl_session=")
    assert "httponly" in header.lower()
    assert "samesite=lax" in header.lower()
    assert "max-age=1209600" in header.lower()  # 14 days
    assert "secure" not in header.lower()


def test_session_cookie_is_secure_in_production():
    from app.config import Settings
    from app.main import create_app
    from tests.helpers import PRODUCTION_SETTINGS

    settings = Settings(_env_file=None, **PRODUCTION_SETTINGS)
    header = session_cookie_header(create_app(settings))
    # __Host- prefix: only accepted with Secure, path=/ and no Domain.
    assert header.startswith("__Host-unfurl_session=")
    assert "secure" in header.lower()
    assert "httponly" in header.lower()
    assert "path=/" in header.lower()
    assert "domain=" not in header.lower()


def test_csrf_fails_closed_for_json_bodies_on_the_real_app(auth_client):
    # The token is only read from the header or a form field; a JSON body is rejected.
    response = auth_client.post("/logout", json={"csrf_token": "anything"})
    assert response.status_code == 403
