"""Small helpers for tests that use the HTTP client (import them in test files)."""

import re

from fastapi.testclient import TestClient

USER_EMAIL = "jana@example.com"
USER_PASSWORD = "correct horse battery"

# The smallest set of settings a production app starts with
# (see Settings.check_production_settings).
PRODUCTION_SETTINGS = {
    "app_env": "production",
    "secret_key": "x" * 32,
    "encryption_key": "k",
    "email_backend": "smtp",
    "smtp_host": "smtp.example.com",
}


def csrf_token(client: TestClient) -> str:
    """The CSRF token of the client's session.

    Read from a page like a browser would: the login form's hidden field, or, for a
    logged-in client (sent on from /login to /), the hx-headers attribute on <body>.
    """
    html = client.get("/login").text
    match = re.search(r'name="csrf_token" value="([^"]+)"', html) or re.search(
        r'"X-CSRF-Token": "([^"]+)"', html
    )
    return match.group(1)


def log_in(client: TestClient, email: str = USER_EMAIL, password: str = USER_PASSWORD):
    """Submit the login form like a browser would. Returns the response (redirect not followed)."""
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": csrf_token(client)},
        follow_redirects=False,
    )
