"""CV PDFs with WeasyPrint (SPEC §6.5, plan M3b, D-051, D-054, D-059).

These tests make real PDFs, so they need Pango (`brew install pango` on a Mac; CI and
Docker install it). Without Pango they fail on purpose: a skipped test proves nothing.
"""

import io
from datetime import date

import pytest
from pypdf import PdfReader

from app.models import CVDocument
from app.schemas.cv import (
    CVBullet,
    CVContent,
    CVEducation,
    CVExperience,
    CVLanguage,
    CVPersonal,
    CVProject,
    CVSkill,
)
from app.services import pdf_service

A4 = (595, 842)  # points, rounded


def sample_content(**changes) -> CVContent:
    """A realistic profile: enough for a full page, with Turkish and German letters."""
    content = CVContent(
        personal=CVPersonal(
            full_name="Fatih Fındık",
            headline="Senior Ürün Yöneticisi · Größe",
            email="fatih@example.com",
            phone="+43 660 1234567",
            location="İstanbul / Wien",
        ),
        summary="Product manager with eight years in B2B SaaS. Türkçe ğşıİöüç, Deutsch äöüß.",
        experience=[
            CVExperience(
                title=f"Product Manager {n}",
                company=f"Şirket {n} GmbH",
                start_date=date(2016 + n, 1, 1),
                end_date=None if n == 0 else date(2017 + n, 6, 1),
                bullets=[
                    CVBullet(text=f"Shipped feature {n}.{b} for 10,000 users") for b in range(4)
                ],
            )
            for n in range(4)
        ],
        education=[CVEducation(institution="TU Wien", degree="MSc", start_date=date(2012, 1, 1))],
        skills=[CVSkill(name=name) for name in ["SQL", "Discovery", "Roadmaps", "Figma", "Jira"]],
        languages=[
            CVLanguage(language="German", level="C1"),
            CVLanguage(language="Turkish", level="native"),
        ],
        projects=[CVProject(name="Unfurl", description="Job search with a learning plan.")],
    )
    return content.model_copy(update=changes)


def make_cv(language="en", content=None) -> CVDocument:
    content = content or sample_content()
    return CVDocument(user_id=1, title="CV", language=language, content=content.to_json())


def read(pdf: bytes) -> PdfReader:
    return PdfReader(io.BytesIO(pdf))


def text_of(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in read(pdf).pages)


def font_names(pdf: bytes) -> set[str]:
    names = set()
    for page in read(pdf).pages:
        for font in page["/Resources"]["/Font"].values():
            names.add(str(font.get_object()["/BaseFont"]))
    return names


@pytest.fixture(scope="module")
def pdf() -> bytes:
    return pdf_service.render_cv_pdf(make_cv())


def test_it_is_a_pdf_on_a4(pdf):
    reader = read(pdf)
    assert pdf.startswith(b"%PDF")
    for page in reader.pages:
        size = (round(float(page.mediabox.width)), round(float(page.mediabox.height)))
        assert size == A4


def test_the_text_can_be_selected(pdf):
    text = text_of(pdf)
    for expected in [
        "Fatih Fındık",
        "Product Manager 2",
        "Shipped feature 1.3",
        "TU Wien",
        "Roadmaps",
    ]:
        assert expected in text


def test_a_sample_profile_fits_on_one_or_two_pages(pdf):
    assert 1 <= len(read(pdf).pages) <= 2


def test_our_fonts_are_embedded_including_the_extended_letters(pdf):
    # Not just "some font": Inter and Source Serif, and the "Ext" half for ğ ş İ.
    # (With one family + unicode-range, WeasyPrint drew wrong shapes for these
    # letters while the text layer looked fine; this checks the fix, D-054.)
    names = " ".join(font_names(pdf))
    assert "Inter" in names
    assert "Source-Serif-4" in names or "SourceSerif4" in names
    assert "Ext" in names


def test_turkish_and_german_letters_are_in_the_text(pdf):
    text = text_of(pdf)
    for letters in ["ğşıİöüç", "äöüß", "İstanbul", "Şirket"]:
        assert letters in text


def test_hidden_sections_and_unticked_bullets_are_left_out():
    content = sample_content(hidden_sections=["summary", "projects"])
    content.experience[0].bullets[0].selected = False

    text = text_of(pdf_service.render_cv_pdf(make_cv(content=content)))

    assert "Product manager with eight years" not in text
    assert "Job search with a learning plan" not in text
    assert "Shipped feature 0.0" not in text
    assert "Shipped feature 0.1" in text


def test_the_pdf_html_follows_the_ats_rules():
    html = pdf_service.render_cv_html(make_cv(), for_pdf=True)

    assert "<table" not in html  # no tables for layout
    assert "<h2>" in html  # standard section headings
    assert "<img" not in html  # no pictures of text (the photo comes in step 5a)
    assert "<script" not in html


def test_a_weasyprint_failure_is_a_pdf_error(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("Pango is missing")

    monkeypatch.setattr(pdf_service, "render_cv_html", broken)

    with pytest.raises(pdf_service.PDFError) as error:
        pdf_service.render_cv_pdf(make_cv())
    assert str(error.value) == "RuntimeError"  # only the type, never CV content


# --- What WeasyPrint may load (D-059) ---------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/x.png",
        "https://example.com/x.css",
        "file:///etc/passwd",
        (pdf_service.STATIC_DIR / "img" / "logo.svg").as_uri(),  # static, but not css/fonts
        (pdf_service.STATIC_DIR / "css" / ".." / ".." / "config.py").resolve().as_uri(),
        (pdf_service.STATIC_DIR / "css").as_uri() + "/../../config.py",
        "file://evil.example.com/static/css/cv_classic.css",
        "data:text/html;base64,PHNjcmlwdD4=",
        "data:image/png;base64,iVBORw0KGgo=",
        "ftp://example.com/x",
        "javascript:alert(1)",
    ],
)
def test_other_resources_are_blocked(url):
    assert not pdf_service.is_allowed_url(url)


@pytest.mark.parametrize(
    "url",
    [
        (pdf_service.STATIC_DIR / "css" / "cv_classic.css").as_uri(),
        (pdf_service.STATIC_DIR / "fonts" / "inter-latin-wght-normal.woff2").as_uri(),
        "data:image/jpeg;base64,/9j/4AAQSkZJRg==",
    ],
)
def test_our_stylesheets_fonts_and_the_photo_are_allowed(url):
    assert pdf_service.is_allowed_url(url)


def test_a_blocked_resource_does_not_stop_the_pdf(monkeypatch):
    # Even if a template ever pointed at a remote file, the fetcher refuses it and
    # the CV is still made (without that resource).
    real = pdf_service.render_cv_html

    def with_remote_image(cv, *, for_pdf):
        return real(cv, for_pdf=for_pdf).replace(
            "</body>", '<img src="http://169.254.169.254/latest/meta-data"></body>'
        )

    monkeypatch.setattr(pdf_service, "render_cv_html", with_remote_image)

    assert pdf_service.render_cv_pdf(make_cv()).startswith(b"%PDF")
