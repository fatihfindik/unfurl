"""Security headers and no-store (#10, D-120, SPEC §10.1)."""

import re
from pathlib import Path

import pytest

from app.main import CONTENT_SECURITY_POLICY
from app.services import reminder_email
from tests.helpers import csrf_token

TEMPLATES = Path(__file__).parent.parent / "app" / "templates"


def assert_security_headers(response):
    assert response.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_the_policy_allows_only_our_own_origin():
    assert "script-src 'self'" in CONTENT_SECURITY_POLICY
    assert "frame-ancestors 'none'" in CONTENT_SECURITY_POLICY
    assert "unsafe-inline" not in CONTENT_SECURITY_POLICY
    assert "unsafe-eval" not in CONTENT_SECURITY_POLICY
    assert "http" not in CONTENT_SECURITY_POLICY  # no other sites


@pytest.mark.parametrize("path", ["/", "/learning", "/settings", "/profile", "/documents"])
def test_logged_in_pages_have_the_headers_and_no_store(auth_client, path):
    response = auth_client.get(path)

    assert response.status_code == 200
    assert_security_headers(response)
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert response.headers["Cache-Control"] == "no-store"


def test_htmx_pieces_have_them_too(auth_client):
    response = auth_client.get("/learning", headers={"HX-Request": "true"})

    assert_security_headers(response)
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path", ["/login", "/register", "/privacy", "/does-not-exist"])
def test_public_pages_and_errors_have_them(client, path):
    response = client.get(path)

    assert_security_headers(response)
    assert response.headers["Cache-Control"] == "no-store"


def test_redirects_have_them(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert_security_headers(response)


def test_posts_have_them(auth_client):
    response = auth_client.post(
        "/settings/theme", data={"theme": "dark", "csrf_token": csrf_token(auth_client)}
    )

    assert response.status_code == 204
    assert_security_headers(response)


def test_static_files_have_the_headers_but_may_be_cached(client):
    response = client.get("/static/js/app.js")

    assert response.status_code == 200
    assert_security_headers(response)
    assert "no-store" not in response.headers.get("Cache-Control", "")


def test_a_refused_csrf_post_has_them(auth_client):
    response = auth_client.post("/settings/theme", data={"theme": "dark"})

    assert response.status_code == 403
    assert_security_headers(response)


def test_a_rate_limited_answer_has_them(client):
    for _ in range(11):  # sign in: 10 per minute
        response = client.post(
            "/login",
            data={"email": "x@example.com", "password": "wrong", "csrf_token": csrf_token(client)},
        )

    assert response.status_code == 429
    assert_security_headers(response)


def test_the_photo_keeps_its_own_cache_header(auth_client):
    from tests.test_photo import image_bytes, upload

    upload(auth_client, image_bytes())
    response = auth_client.get("/profile/photo")

    assert response.headers["Cache-Control"] == "private, no-store"
    assert len(response.headers.get_list("X-Content-Type-Options")) == 1
    assert_security_headers(response)


def test_a_routes_own_header_is_kept(client, user):
    token = reminder_email.make_off_token(user)

    response = client.get("/reminders/off", params={"token": token})

    assert response.headers["Referrer-Policy"] == "no-referrer"  # not same-origin
    assert len(response.headers.get_list("Referrer-Policy")) == 1
    assert_security_headers(response)


# --- The templates stay CSP-friendly --------------------------------------------------------


def without_comments(text: str) -> str:
    """Jinja comments ({# ... #}) may mention <script> in their explanation."""
    return re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)


def app_templates():
    # The pdf/ templates too: the CV editor's preview shows them in the browser.
    return list(TEMPLATES.rglob("*.html"))


def test_no_template_has_an_inline_style_attribute():
    offenders = [
        str(p) for p in app_templates() if re.search(r"\sstyle=", without_comments(p.read_text()))
    ]
    assert offenders == []


def test_no_template_has_an_inline_script_or_event_handler():
    offenders = []
    for path in app_templates():
        text = without_comments(path.read_text())
        if re.search(r"<script(?![^>]*\ssrc=)[^>]*>", text) or re.search(r"\son[a-z]+=", text):
            offenders.append(str(path))
        # A <style> block is inline CSS; Alpine's :style with a string writes a style
        # attribute (security review #10, L2); javascript: links run inline code.
        if re.search(r"<style[\s>]", text) or re.search(r"(?:x-bind)?:style=", text):
            offenders.append(str(path))
        if re.search(r"href=\"javascript:", text):
            offenders.append(str(path))
    assert offenders == []
