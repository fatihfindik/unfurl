"""Edge cases for the home route `GET /` (applications page, M0: empty page)."""

import re

import pytest


def html_class(html: str) -> str:
    return re.search(r'<html lang="en" class="([^"]*)">', html).group(1)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_home_rejects_write_methods(client, method):
    response = client.request(method, "/")
    assert response.status_code == 405
    # The 405 answer tells the client which method is allowed.
    assert "GET" in response.headers["allow"]


def test_home_is_utf8_html(client):
    content_type = client.get("/").headers["content-type"]
    assert content_type == "text/html; charset=utf-8"


@pytest.mark.parametrize(
    "query",
    [
        "?page=2&sort=desc",
        "?q=%3Cscript%3Ealert(1)%3C/script%3E",
        "?%22%3E%3Cimg%20src=x%20onerror=alert(1)%3E=1",
        "?theme=dark",
    ],
)
def test_query_string_is_ignored_and_not_reflected(client, query):
    response = client.get("/" + query)
    assert response.status_code == 200
    assert "No applications yet." in response.text
    assert "alert(1)" not in response.text
    assert "onerror" not in response.text
    # Only the cookie picks the theme, never a query parameter.
    assert html_class(response.text) == "theme-system"


def test_query_string_keeps_applications_menu_item_current(client):
    # The active menu item is chosen from the path only, so a query string must not break it.
    html = client.get("/?filter=open").text
    assert re.search(r'<a href="/" aria-current="page"', html)


@pytest.mark.parametrize("path", ["/index.html", "/applications", "/%3Cscript%3E"])
def test_home_path_variants_are_not_found(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert "<script>" not in response.text


@pytest.mark.parametrize("value", ["Dark", "DARK", "darkx", "theme-dark"])
def test_theme_cookie_must_match_exactly(client, value):
    # Theme names are case sensitive and must match exactly; anything else is the default.
    client.cookies.set("theme", value)
    assert html_class(client.get("/").text) == "theme-system"


def test_other_cookies_do_not_change_the_theme(client):
    client.cookies.set("Theme", "dark")  # cookie names are case sensitive
    client.cookies.set("session", "abc")
    assert html_class(client.get("/").text) == "theme-system"


def test_very_long_theme_cookie_falls_back_and_is_not_reflected(client):
    value = "x" * 4000
    client.cookies.set("theme", value)
    html = client.get("/").text
    assert html_class(html) == "theme-system"
    assert value not in html
