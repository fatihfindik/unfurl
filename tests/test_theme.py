import re

import pytest


def html_class(html: str) -> str:
    return re.search(r'<html lang="en" class="([^"]*)">', html).group(1)


def checked_items(html: str) -> list[str]:
    """data-theme values of the theme menu rows that are marked with ✓."""
    return re.findall(r'data-theme="(\w+)"[^>]*aria-checked="true"', html)


def button_icon(html: str) -> str:
    """Which theme icon the closed theme button shows (its data-current value)."""
    return re.search(r'data-current="(\w+)"[^>]*aria-label="Color theme"', html).group(1)


def test_no_cookie_uses_system_theme(auth_client):
    html = auth_client.get("/").text
    assert html_class(html) == "theme-system"
    assert checked_items(html) == ["system"]
    assert button_icon(html) == "system"


@pytest.mark.parametrize("theme", ["light", "dark", "system"])
def test_theme_cookie_is_rendered_by_the_server(auth_client, theme):
    auth_client.cookies.set("theme", theme)
    html = auth_client.get("/").text
    assert html_class(html) == f"theme-{theme}"
    assert checked_items(html) == [theme]
    assert button_icon(html) == theme


def test_theme_menu_has_all_three_options(auth_client):
    html = auth_client.get("/").text
    assert 'aria-haspopup="menu"' in html
    assert re.findall(r'role="menuitemradio"[^>]*data-theme="(\w+)"', html) == [
        "light",
        "dark",
        "system",
    ]


@pytest.mark.parametrize(
    "bad_value", ["purple", "", "dark onload=alert(1)", '"><script>x</script>']
)
def test_invalid_theme_cookie_falls_back_to_system(auth_client, bad_value):
    auth_client.cookies.set("theme", bad_value)
    html = auth_client.get("/").text
    assert html_class(html) == "theme-system"
    assert "alert(1)" not in html
    assert "<script>x" not in html
