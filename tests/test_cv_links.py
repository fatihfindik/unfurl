"""Links on CVs (#23, D-056): clickable only in the PDF, only http(s), checked again."""

import io
from datetime import date

import pytest
from pypdf import PdfReader

from app.models import CVDocument
from app.schemas.cv import CVCertification, CVContent, CVPersonal, CVProject
from app.services import pdf_service


def cv_with(**personal) -> CVDocument:
    content = CVContent(
        personal=CVPersonal(full_name="Jana", email="jana@example.com", **personal),
        projects=[CVProject(name="Unfurl", url="https://unfurl.example")],
        certifications=[
            CVCertification(name="AWS", date=date(2022, 1, 1), url="https://aws.example/cert")
        ],
    )
    return CVDocument(user_id=1, title="CV", language="en", content=content.to_json())


LINKS = {
    "linkedin_url": "https://linkedin.com/in/jana",
    "github_url": "https://github.com/jana",
    "website": "http://jana.example",
}


def test_pdf_links_are_clickable_and_visible_as_text():
    html = pdf_service.render_cv_html(cv_with(**LINKS), for_pdf=True)

    for url in [*LINKS.values(), "https://unfurl.example", "https://aws.example/cert"]:
        assert f'<a href="{url}">{url}</a>' in html


def test_the_pdf_really_has_link_annotations():
    pdf = PdfReader(io.BytesIO(pdf_service.render_cv_pdf(cv_with(**LINKS))))
    uris = {
        annotation.get_object()["/A"]["/URI"]
        for page in pdf.pages
        for annotation in page.get("/Annots", [])
        if "/A" in annotation.get_object()
    }

    assert "https://github.com/jana" in uris
    assert "https://unfurl.example" in uris


def test_the_preview_shows_links_as_text_only():
    html = pdf_service.render_cv_html(cv_with(**LINKS), for_pdf=False)

    assert "<a href" not in html
    assert "https://github.com/jana" in html


@pytest.mark.parametrize(
    "value",
    ["javascript:alert(1)", "data:text/html,<script>1</script>", "ftp://x.example", "jana.example"],
)
def test_other_schemes_stay_plain_text(value):
    # The profile form refuses these, but the CV's JSON is checked again (#23).
    html = pdf_service.render_cv_html(cv_with(website=value), for_pdf=True)

    assert "<a href" not in html.split("cv-contacts", 1)[1].split("</p>", 1)[0]
    assert "<script>" not in html


def test_odd_but_valid_links_are_escaped():
    html = pdf_service.render_cv_html(cv_with(website='https://"><x'), for_pdf=True)

    assert '"><x' not in html
    assert "https://&#34;&gt;&lt;x" in html


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://a.example", True),
        ("http://a.example/x?y=1", True),
        ("javascript:alert(1)", False),
        ("https:// a.example", False),
        ("", False),
        (None, False),
    ],
)
def test_is_link(value, expected):
    assert pdf_service.is_link(value) is expected
