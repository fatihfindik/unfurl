import re

import pytest

from tests.helpers import csrf_token, log_in


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
def test_theme_cookie_is_rendered_by_the_server_for_visitors(client, theme):
    # Pages without a login (here /login) use the cookie (D-007).
    client.cookies.set("theme", theme)
    html = client.get("/login").text
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
def test_invalid_theme_cookie_falls_back_to_system(client, bad_value):
    client.cookies.set("theme", bad_value)
    html = client.get("/login").text
    assert html_class(html) == "theme-system"
    assert "alert(1)" not in html
    assert "<script>x" not in html


# --- The theme is saved with the account (M4a, D-068) ---------------------------------------


def save_theme(client, theme):
    return client.post(
        "/settings/theme",
        data={"theme": theme},
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)},
    )


def test_a_new_account_starts_with_system(user):
    assert user.theme == "system"


@pytest.mark.parametrize("theme", ["light", "dark", "system"])
def test_choosing_a_theme_saves_it_with_the_account(auth_client, session, user, theme):
    response = save_theme(auth_client, theme)

    assert response.status_code == 204
    session.refresh(user)
    assert user.theme == theme
    cookie = response.headers["set-cookie"]
    assert f"theme={theme}" in cookie and "Path=/" in cookie and "SameSite=lax" in cookie
    assert "HttpOnly" not in cookie and "Max-Age=31536000" in cookie


def test_logged_in_pages_use_the_account_theme_not_the_cookie(auth_client, session, user):
    user.theme = "dark"
    session.add(user)
    session.commit()
    auth_client.cookies.set("theme", "light")  # an old choice in this browser

    html = auth_client.get("/").text

    assert html_class(html) == "theme-dark"
    assert checked_items(html) == ["dark"]


def test_the_theme_follows_the_user_to_another_browser(client, session, user):
    user.theme = "dark"
    session.add(user)
    session.commit()

    # A new browser: no cookie yet.
    response = log_in(client)

    assert "theme=dark" in response.headers["set-cookie"]  # login writes the cookie
    assert html_class(client.get("/").text) == "theme-dark"
    client.post("/logout", data={"csrf_token": csrf_token(client)})
    assert html_class(client.get("/login").text) == "theme-dark"  # also after logging out


@pytest.mark.parametrize("bad", ["", "purple", "DARK", "dark;x", "<script>"])
def test_unknown_themes_are_refused(auth_client, session, user, bad):
    assert save_theme(auth_client, bad).status_code == 422
    session.refresh(user)
    assert user.theme == "system"


def test_a_broken_value_in_the_database_falls_back_to_system(auth_client, session, user):
    user.theme = "x"
    session.add(user)
    session.commit()

    assert html_class(auth_client.get("/").text) == "theme-system"


def test_saving_needs_a_login_and_the_csrf_token(client, auth_client, session, user):
    assert auth_client.post("/settings/theme", data={"theme": "dark"}).status_code == 403
    auth_client.post("/logout", data={"csrf_token": csrf_token(auth_client)})
    response = save_theme(client, "dark")
    assert response.headers.get("HX-Redirect", "").startswith("/login")
    session.refresh(user)
    assert user.theme == "system"


def test_only_logged_in_pages_save_to_the_account(client, auth_client):
    # On a page without a login the picker only writes the cookie: saving to the
    # account would send the visitor to /login (plan-reviewer #8).
    assert 'data-save-url="/settings/theme"' in auth_client.get("/").text
    assert 'data-save-url="/settings/theme"' in auth_client.get("/privacy").text
    auth_client.post("/logout", data={"csrf_token": csrf_token(auth_client)})
    for path in ("/login", "/privacy"):
        assert "data-save-url" not in client.get(path).text
