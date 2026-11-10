import re

import pytest


def html_class(html: str) -> str:
    return re.search(r'<html lang="en" class="([^"]*)">', html).group(1)


def pressed_buttons(html: str) -> list[str]:
    """data-theme values of the theme buttons that are marked as active."""
    return re.findall(r'data-theme="(\w+)"[^>]*aria-pressed="true"', html)


def test_no_cookie_uses_system_theme(client):
    html = client.get("/").text
    assert html_class(html) == "theme-system"
    assert pressed_buttons(html) == ["system"]


@pytest.mark.parametrize("theme", ["light", "dark", "system"])
def test_theme_cookie_is_rendered_by_the_server(client, theme):
    client.cookies.set("theme", theme)
    html = client.get("/").text
    assert html_class(html) == f"theme-{theme}"
    assert pressed_buttons(html) == [theme]


@pytest.mark.parametrize(
    "bad_value", ["purple", "", "dark onload=alert(1)", '"><script>x</script>']
)
def test_invalid_theme_cookie_falls_back_to_system(client, bad_value):
    client.cookies.set("theme", bad_value)
    html = client.get("/").text
    assert html_class(html) == "theme-system"
    assert "alert(1)" not in html
    assert "<script>x" not in html
