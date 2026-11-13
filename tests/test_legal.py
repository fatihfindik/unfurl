"""Legal pages (SPEC §6.12): open to everyone, placeholder text, linked from every layout."""

import re

import pytest

LEGAL_PAGES = [
    ("/privacy", "Privacy policy"),
    ("/terms", "Terms of use"),
    ("/imprint", "Imprint"),
]


@pytest.mark.parametrize(("url", "title"), LEGAL_PAGES)
def test_legal_pages_are_open_without_login(client, url, title):
    response = client.get(url, follow_redirects=False)
    assert response.status_code == 200
    assert re.search(rf"<h1[^>]*>{title}</h1>", response.text)
    assert f"<title>{title} · Unfurl</title>" in response.text
    assert "TODO: legal review" in response.text


@pytest.mark.parametrize(("url", "title"), LEGAL_PAGES)
def test_legal_pages_work_when_logged_in_too(auth_client, url, title):
    assert auth_client.get(url).status_code == 200


def legal_links_in(html: str) -> list[str]:
    footer = html.split('aria-label="Legal"', 1)[1].split("</nav>", 1)[0]
    return re.findall(r'<a href="([^"]+)"', footer)


@pytest.mark.parametrize("url", ["/login", "/register", "/privacy"])
def test_public_pages_link_to_all_legal_pages(client, url):
    assert legal_links_in(client.get(url).text) == ["/privacy", "/terms", "/imprint"]


def test_app_pages_link_to_all_legal_pages(auth_client):
    assert legal_links_in(auth_client.get("/").text) == ["/privacy", "/terms", "/imprint"]


def test_register_consent_links_point_to_real_pages(client):
    html = client.get("/register").text
    for url in ("/privacy", "/terms"):
        assert f'<a href="{url}">' in html
        assert client.get(url).status_code == 200
