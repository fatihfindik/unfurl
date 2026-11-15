"""Downloading a CV as PDF (D-057)."""

import logging

import pytest
from markupsafe import escape

from app.schemas.cv import CVContent, CVPersonal
from app.services import document_service as docs
from app.services import pdf_service
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile


@pytest.fixture
def cv(session, user):
    get_or_create_profile(session, user)
    docs.create_cv(session, user, "Master", "en", "classic")  # the copy below needs a master
    return docs.create_cv(session, user, "For Acme", "en", "modern")


def with_name(session, cv, name):
    content = CVContent.model_validate(cv.content)
    content.personal = CVPersonal(full_name=name)
    cv.content = content.to_json()
    session.add(cv)
    session.commit()
    return cv


def test_download_gives_a_pdf_with_the_right_headers(auth_client, session, cv):
    with_name(session, cv, "Fatih Fındık")

    response = auth_client.get(f"/cvs/{cv.id}/pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-disposition"] == (
        'attachment; filename="CV_Fatih_Findik_en.pdf"; '
        "filename*=UTF-8''CV_Fatih_F%C4%B1nd%C4%B1k_en.pdf"
    )


@pytest.mark.parametrize(
    ("name", "ascii_name"),
    [
        ("Jürgen Groß-Çelik", "CV_Jurgen_Gross-Celik_en.pdf"),
        ("İlkay Ğüzel", "CV_Ilkay_Guzel_en.pdf"),
        ('a"b/c;d\r\nX-Evil: 1', "CV_a_b_c_d_X-Evil_1_en.pdf"),
        ("", "CV_en.pdf"),
        ("   ", "CV_en.pdf"),
        ("王小明", "CV_en.pdf"),
        ("x" * 300, f"CV_{'x' * 80}_en.pdf"),
    ],
)
def test_file_names_are_safe(session, cv, name, ascii_name):
    with_name(session, cv, name)

    plain, real = pdf_service.pdf_filename(cv)
    header = pdf_service.content_disposition(cv)

    assert plain == ascii_name
    assert "\r" not in header and "\n" not in header
    assert header.count('"') == 2  # only the two around the plain name
    assert real.endswith("_en.pdf")


def test_a_locked_cv_downloads_with_the_name_it_was_sent_with(auth_client, session, user, cv):
    with_name(session, cv, "Jana Novak")
    docs.lock(session, cv)
    user.full_name = "Jana Novak-Weber"  # the account name changes later
    session.add(user)
    session.commit()

    response = auth_client.get(f"/cvs/{cv.id}/pdf")

    assert response.status_code == 200
    assert 'filename="CV_Jana_Novak_en.pdf"' in response.headers["content-disposition"]


def test_another_users_cv_is_404(auth_client, session):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    get_or_create_profile(session, other)
    theirs = docs.create_cv(session, other, "Theirs", "en", "classic")

    assert auth_client.get(f"/cvs/{theirs.id}/pdf").status_code == 404
    assert auth_client.get("/cvs/abc/pdf").status_code == 404


def test_logged_out_visitors_go_to_login(client):
    response = client.get("/cvs/1/pdf", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_at_most_20_pdfs_a_minute(auth_client, cv, monkeypatch):
    monkeypatch.setattr(pdf_service, "render_cv_pdf", lambda cv: b"%PDF-fake")

    answers = [auth_client.get(f"/cvs/{cv.id}/pdf").status_code for _ in range(21)]

    assert answers[:20] == [200] * 20
    assert answers[20] == 429


def test_a_failed_pdf_shows_a_message_and_logs_no_content(
    auth_client, session, cv, monkeypatch, caplog
):
    with_name(session, cv, "Secret Person")

    def broken(cv):
        raise pdf_service.PDFError("OSError")

    monkeypatch.setattr(pdf_service, "render_cv_pdf", broken)

    with caplog.at_level(logging.WARNING, logger="unfurl.cvs"):
        response = auth_client.get(f"/cvs/{cv.id}/pdf")

    assert response.status_code == 503
    assert str(escape("We couldn't create the PDF")) in response.text  # the page title
    assert f'href="/cvs/{cv.id}"' in response.text
    assert "OSError" in caplog.text
    assert "Secret Person" not in caplog.text


def test_the_editor_has_a_download_button(auth_client, cv):
    page = auth_client.get(f"/cvs/{cv.id}").text

    assert f'href="/cvs/{cv.id}/pdf"' in page
    assert 'data-tooltip="Download PDF"' in page and "data-cv-download" in page
