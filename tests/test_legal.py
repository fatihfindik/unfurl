"""Legal pages (SPEC §6.12, D-133): open to everyone, drafted texts with a Draft band
until reviewed, the Impressum from LEGAL_* settings, linked from every layout."""

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
    assert "Draft · not yet reviewed by a lawyer." in response.text


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


def test_app_pages_put_the_legal_links_bottom_right(auth_client):
    html = auth_client.get("/").text
    footer = html[html.index("<footer") :]
    assert "justify-end" in footer[: footer.index(">")]


# --- Drafted texts (D-133) ------------------------------------------------------------------

from app.config import get_settings  # noqa: E402


@pytest.fixture
def operator(monkeypatch):
    """Made-up Impressum details (the real ones are only in .env)."""
    settings = get_settings()
    monkeypatch.setattr(settings, "legal_operator_name", "Max Muster")
    monkeypatch.setattr(settings, "legal_address", "Teststraße 1, 1010 Wien, Austria")
    monkeypatch.setattr(settings, "legal_email", "hello@example.com")
    return settings


def test_the_imprint_shows_the_operator(client, operator):
    page = client.get("/imprint").text

    assert "Max Muster" in page
    assert "Teststraße 1, 1010 Wien, Austria" in page
    assert 'href="mailto:hello@example.com"' in page
    assert "§ 5 E-Commerce-Gesetz" in page


def test_without_details_the_imprint_says_so(client):
    page = client.get("/imprint").text

    assert page.count("[to be added]") == 3
    assert "mailto:" not in page


def test_the_details_are_escaped(client, operator, monkeypatch):
    monkeypatch.setattr(operator, "legal_operator_name", "<script>x</script>")

    page = client.get("/imprint").text

    assert "<script>x</script>" not in page
    assert "&lt;script&gt;" in page


@pytest.mark.parametrize(("url", "_"), LEGAL_PAGES)
def test_the_draft_band_goes_after_review(client, monkeypatch, url, _):
    monkeypatch.setattr(get_settings(), "legal_reviewed", True)

    assert "Draft · not yet reviewed" not in client.get(url).text


def test_the_privacy_policy_covers_the_gdpr_points(client, operator):
    page = client.get("/privacy").text

    for heading in (
        "Who is responsible",
        "What we store and why",
        "Data about other people",
        "Security and abuse protection",
        "Who processes data for us",
        "No automated decisions",
        "How long we keep it",
        "Your rights",
        "Cookies",
        "Minimum age",
    ):
        assert heading in page, heading
    assert "Max Muster" in page
    assert "Datenschutzbehörde" in page
    assert "Export my data" in page and "Delete my account" in page
    assert "aged 16 or older" in page
    assert "your CVs and cover letters and their versions" in page


def test_the_terms_keep_consumer_protections(client):
    page = client.get("/terms").text

    assert "gross negligence" in page
    assert "Austrian law applies" in page
    assert "the country you live in" in page
    assert "16 or older" in page
    assert "CVs and cover letters" in page
