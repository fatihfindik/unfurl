"""HEAD requests are answered like GET, without a body (#5, D-142)."""

import pytest

from app.main import app
from app.services import application_service as apps
from app.services import document_service as docs
from app.services.auth_service import make_verify_token
from app.services.profile_service import get_or_create_profile
from tests.helpers import csrf_token
from tests.test_photo import image_bytes, upload
from tests.test_settings_page import save as save_settings

# Every GET route without a path parameter; new routes are covered automatically.
# Read from the OpenAPI schema: FastAPI keeps included routers out of app.routes.
GET_PATHS = sorted(
    path for path, methods in app.openapi()["paths"].items() if "get" in methods and "{" not in path
)


def same_headers(head, get):
    """Status, content type and length match; the body of HEAD is empty."""
    assert head.status_code == get.status_code
    assert head.content == b""
    assert head.headers.get("content-type") == get.headers.get("content-type")
    assert head.headers.get("content-length") == get.headers.get("content-length")


def test_the_route_list_is_not_empty():
    assert {"/", "/login", "/learning", "/documents", "/settings", "/api/ext/me"} <= set(GET_PATHS)


@pytest.mark.parametrize("path", GET_PATHS)
def test_head_answers_like_get_when_logged_out(client, path):
    head = client.head(path, follow_redirects=False)
    get = client.get(path, follow_redirects=False)

    assert head.status_code != 405
    same_headers(head, get)
    assert head.headers.get("location") == get.headers.get("location")


def test_a_page_that_needs_a_login_redirects(client):
    response = client.head("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.parametrize("path", ["/", "/learning", "/documents", "/settings", "/profile"])
def test_head_on_pages_when_logged_in(auth_client, path):
    response = auth_client.head(path)

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-type"].startswith("text/html")
    assert int(response.headers["content-length"]) > 0


def test_head_on_a_cv_pdf(auth_client, session, user):
    get_or_create_profile(session, user)
    cv = docs.create_cv(session, user, "Master", "en", "classic")

    response = auth_client.head(f"/cvs/{cv.id}/pdf")

    # Not compared with GET: each PDF has its creation time, so the length can differ.
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-type"] == "application/pdf"
    assert int(response.headers["content-length"]) > 0
    assert response.headers["content-disposition"].startswith("attachment;")


def test_head_on_the_profile_photo(auth_client, upload_dir):
    assert upload(auth_client, image_bytes()).status_code == 200

    head = auth_client.head("/profile/photo")
    get = auth_client.get("/profile/photo")

    assert head.status_code == 200
    same_headers(head, get)


def test_head_on_an_application(auth_client, session, user):
    job = apps.create(session, user, company="Kranich Health GmbH", job_title="Product Manager")

    response = auth_client.head(f"/applications/{job.id}")

    assert response.status_code == 200
    assert response.content == b""


def test_head_on_the_badge_keeps_the_session_cookie(auth_client):
    # KeepSessionCookie still sees the request as GET and drops Set-Cookie.
    response = auth_client.head("/notifications/badge")

    assert response.status_code == 200
    assert "set-cookie" not in response.headers


def test_head_gets_the_security_headers(client):
    response = client.head("/login")

    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in response.headers
    assert response.headers["cache-control"] == "no-store"


def test_head_on_a_static_file_is_unchanged(client):
    response = client.head("/static/css/cv_base.css")

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-type"].startswith("text/css")


def test_head_on_a_post_only_route_is_still_not_allowed(client):
    response = client.head("/logout")

    assert response.status_code == 405


def test_head_on_the_verify_link_does_not_verify(client, session, user):
    # Link checkers in email programs may ask with HEAD or GET; only the button verifies
    # (D-142, D-146).
    user.is_verified = False
    session.add(user)
    session.commit()
    link = f"/verify-email?token={make_verify_token(user)}"

    assert client.head(link).status_code == 200
    session.refresh(user)
    assert not user.is_verified

    assert client.get(link).status_code == 200
    session.refresh(user)
    assert not user.is_verified

    token = link.split("token=", 1)[1]
    client.post("/verify-email", data={"token": token, "csrf_token": csrf_token(client)})
    session.refresh(user)
    assert user.is_verified


def test_head_does_not_use_up_the_waiting_toast(auth_client):
    # A new time zone saves a toast for the next full page load (D-114).
    assert save_settings(auth_client, timezone="Asia/Tokyo").headers["HX-Refresh"] == "true"

    auth_client.head("/settings")

    assert 'data-message="Settings saved."' in auth_client.get("/settings").text
