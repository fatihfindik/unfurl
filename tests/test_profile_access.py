"""Who may use the profile routes (SPEC §10.1, M2a acceptance criteria).

The general test in test_auth.py only checks GET pages without "{" in the path, so
every profile route is listed here explicitly.
"""

import pytest

from tests.helpers import csrf_token

# (method, url) for every profile route. Section routes are added in step 4.
ROUTES = [
    ("GET", "/profile"),
    ("GET", "/profile/personal"),
    ("GET", "/profile/personal/edit"),
    ("POST", "/profile/personal"),
    ("GET", "/profile/summary"),
    ("GET", "/profile/summary/edit"),
    ("POST", "/profile/summary"),
]


@pytest.mark.parametrize(("method", "url"), ROUTES)
def test_logged_out_visitors_are_sent_to_login(client, method, url):
    # A CSRF token from the anonymous session, so the request gets past the CSRF check
    # and the login check is what answers.
    headers = {"X-CSRF-Token": csrf_token(client)}
    response = client.request(method, url, headers=headers, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.parametrize(("method", "url"), ROUTES)
def test_logged_out_htmx_requests_get_hx_redirect(client, method, url):
    headers = {"X-CSRF-Token": csrf_token(client), "HX-Request": "true"}
    response = client.request(method, url, headers=headers, follow_redirects=False)
    assert response.headers["HX-Redirect"].startswith("/login")
