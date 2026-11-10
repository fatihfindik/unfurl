import re

from sqlmodel import text

from app.db import get_session


def main_nav_links(html: str) -> list[tuple[str, str, bool]]:
    """Return the sidebar menu links as (url, label, is_current) in page order."""
    section = html.split('id="main-nav-links"', 1)[1].split("</nav>", 1)[0]
    links = re.findall(
        r'<a href="([^"]+)"( aria-current="page")?.*?</svg>\s*([^<]+?)\s*</a>', section, re.S
    )
    return [(url, label, bool(current)) for url, current, label in links]


def test_home_shows_empty_applications_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert re.search(r"<h1[^>]*>Applications</h1>", response.text)
    assert "No applications yet." in response.text


def test_sidebar_has_four_items_in_order_with_applications_current(client):
    links = main_nav_links(client.get("/").text)
    assert links == [
        ("/", "Applications", True),
        ("/learning", "Learning", False),
        ("/documents", "Documents", False),
        ("/settings", "Settings", False),
    ]


def test_top_bar_has_theme_picker_bell_and_account_menu(client):
    html = client.get("/").text
    assert 'aria-label="Color theme"' in html
    for label in ("Light mode", "Dark mode", "Use system setting"):
        assert f'aria-label="{label}"' in html
    assert 'aria-label="Notifications"' in html
    assert 'aria-label="Account menu"' in html
    menu = html.split('id="account-menu"', 1)[1]
    for item in ("My profile", "Settings", "Sign out"):
        assert item in menu


def test_assets_come_from_our_own_origin(client):
    html = client.get("/").text
    scripts = re.findall(r"<script\b[^>]*>", html)
    assert scripts, "expected script tags"
    # Every script has a /static src: no CDNs and no inline scripts (SPEC §10.1).
    for tag in scripts:
        assert 'src="/static/' in tag
    assert '<link rel="stylesheet" href="/static/css/app.css">' in html
    assert not re.search(r'(src|href)="(https?:)?//', html)


def test_unknown_page_returns_404(client):
    assert client.get("/does-not-exist").status_code == 404


def test_get_session_can_query_the_database():
    sessions = get_session()
    session = next(sessions)
    assert session.exec(text("SELECT 1")).one() == (1,)
    sessions.close()  # runs the end of the `with` block in get_session -> session closed
