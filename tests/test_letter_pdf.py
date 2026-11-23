"""Cover letters as PDF (SPEC §6.6, §12 M8b; D-160, D-161).

These tests make real PDFs, so they need Pango, like tests/test_cv_pdf.py.
"""

import datetime as dt
import logging
from urllib.parse import unquote

import pytest
from freezegun import freeze_time
from markupsafe import escape

from app.i18n.document_labels import full_date
from app.models import CoverLetter
from app.services import application_service as apps
from app.services import cover_letter_service as letters
from app.services import pdf_service
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile
from tests.helpers import csrf_token
from tests.test_application_documents import fresh, headers
from tests.test_cv_pdf import A4, read, text_of


@pytest.fixture
def letter(session, user) -> CoverLetter:
    get_or_create_profile(session, user)
    letters.create_letter(session, user, "Master", "de")  # the copy below needs a master
    return letters.create_letter(session, user, "For Acme", "de")


def fill(session, letter, **values) -> CoverLetter:
    for name, value in values.items():
        setattr(letter, name, value)
    session.add(letter)
    session.commit()
    session.refresh(letter)
    return letter


def pdf_of(client, letter):
    return client.get(f"/cover-letters/{letter.id}/pdf")


def file_name(response) -> str:
    """The real file name from filename*=UTF-8''…"""
    header = response.headers["content-disposition"]
    return unquote(header.split("filename*=UTF-8''")[1])


# --- The PDF itself (acceptance: name and date in three languages) ---------------------------

LANGUAGES = {
    "de": ("Sehr geehrte Damen und Herren,", "Mit freundlichen Grüßen"),
    "en": ("Dear Hiring Team,", "Kind regards,"),
    "tr": ("Sayın Yetkili,", "Saygılarımla,"),
}


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_pdf_has_the_name_and_the_date(auth_client, session, user, language):
    get_or_create_profile(session, user)
    letter = letters.create_letter(session, user, "Letter", language)
    fill(
        session,
        letter,
        sender_name="Fatih Fındık",
        place="Wien",
        recipient_company="Kranich Health GmbH",
        subject="Bewerbung als Product Manager",
        body="Ich freue mich über Ihre Stelle.\n\nZweiter Absatz: Größe, Ölçü, İstanbul.",
    )
    today = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)

    with freeze_time(today.replace(hour=10)):
        response = pdf_of(auth_client, letter)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"
    text = text_of(response.content)
    salutation, closing = LANGUAGES[language]
    assert "Fatih Fındık" in text
    assert f"Wien, {full_date(today.date(), language)}" in text
    assert salutation in text and closing in text
    assert "Größe, Ölçü, İstanbul" in text
    assert "Kranich Health GmbH" in text


def test_the_pdf_is_one_a4_page_with_the_apps_font(auth_client, session, letter):
    fill(session, letter, sender_name="Fatih Findik", body="A short letter.")

    pdf = read(pdf_of(auth_client, letter).content)

    assert len(pdf.pages) == 1
    box = pdf.pages[0].mediabox
    assert (round(float(box.width)), round(float(box.height))) == A4
    fonts = {str(f.get_object()["/BaseFont"]) for f in pdf.pages[0]["/Resources"]["/Font"].values()}
    assert any("Inter" in name for name in fonts)


def test_a_long_letter_goes_on_to_a_second_page(auth_client, session, letter):
    fill(session, letter, body="\n\n".join(["A paragraph that says something. " * 12] * 14))

    assert len(read(pdf_of(auth_client, letter).content).pages) >= 2


def test_html_in_the_text_stays_text(auth_client, session, letter):
    fill(session, letter, body="<b>bold</b> <script>alert(1)</script>")

    assert "<b>bold</b>" in text_of(pdf_of(auth_client, letter).content)


def test_a_sent_letter_keeps_the_day_it_was_sent(auth_client, session, user, letter):
    job = apps.create(session, user, company="Acme", job_title="PM")
    apps.link_letter(session, user, job, letter)
    sent_day = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)
    with freeze_time(sent_day.replace(hour=10)):
        auth_client.patch(
            f"/applications/{job.id}/move", data={"status": "applied"}, headers=headers(auth_client)
        )
    later = sent_day + dt.timedelta(days=8)

    with freeze_time(later.replace(hour=10)):
        text = text_of(pdf_of(auth_client, letter).content)

    assert fresh(session, letter).status == "locked"
    assert full_date(sent_day.date(), "de") in text
    assert full_date(later.date(), "de") not in text


# --- The file name (D-161) --------------------------------------------------------------------


def test_the_name_has_the_company_of_the_application(auth_client, session, user, letter):
    fill(session, letter, sender_name="Fatih Fındık", recipient_company="Someone else")
    job = apps.create(session, user, company="Kranich Health", job_title="PM")
    apps.link_letter(session, user, job, letter)

    response = pdf_of(auth_client, letter)

    assert response.headers["content-disposition"] == (
        'attachment; filename="CoverLetter_Fatih_Findik_Kranich_Health_de.pdf"; '
        "filename*=UTF-8''CoverLetter_Fatih_F%C4%B1nd%C4%B1k_Kranich_Health_de.pdf"
    )


def test_without_an_application_the_recipient_company_is_used(auth_client, session, letter):
    fill(session, letter, sender_name="Jürgen Groß", recipient_company="Şişecam A.Ş.")

    response = pdf_of(auth_client, letter)

    header = response.headers["content-disposition"]
    assert 'filename="CoverLetter_Jurgen_Gross_Sisecam_A_S_de.pdf"' in header
    assert file_name(response) == "CoverLetter_Jürgen_Groß_Şişecam_A_Ş_de.pdf"


@pytest.mark.parametrize(
    ("name", "company", "expected"),
    [
        ("", "Acme", "CoverLetter_Acme_de.pdf"),
        ("", None, "CoverLetter_de.pdf"),
        ('Evil"; name\r\nX', 'Co"; x', "CoverLetter_Evil_name_X_Co_x_de.pdf"),
    ],
)
def test_odd_names_make_a_safe_file_name(name, company, expected):
    letter = CoverLetter(
        user_id=1, title="L", language="de", sender_name=name, recipient_company=company
    )

    ascii_name, real_name = pdf_service.letter_filename(letter)

    assert ascii_name == expected
    header = pdf_service.attachment(ascii_name, real_name)
    assert "\r" not in header and "\n" not in header and header.count('"') == 2


# --- Access, limits, failures -----------------------------------------------------------------


def test_another_users_letter_is_not_found(auth_client, session, letter):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    get_or_create_profile(session, other)
    theirs = letters.create_letter(session, other, "Theirs", "en")

    assert pdf_of(auth_client, theirs).status_code == 404


@pytest.mark.parametrize("bad_id", ["abc", "0", "-1", "99999999999999999999"])
def test_odd_ids_are_not_found(auth_client, bad_id):
    assert auth_client.get(f"/cover-letters/{bad_id}/pdf").status_code == 404


def test_logged_out_goes_to_login(client, auth_client, letter):
    auth_client.post("/logout", data={"csrf_token": csrf_token(auth_client)})

    response = client.get(f"/cover-letters/{letter.id}/pdf", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_at_most_20_pdfs_a_minute(auth_client, letter, monkeypatch):
    monkeypatch.setattr(pdf_service, "render_letter_pdf", lambda context: b"%PDF-fake")

    answers = [pdf_of(auth_client, letter).status_code for _ in range(21)]

    assert answers[:20] == [200] * 20
    assert answers[20] == 429


def test_a_failed_pdf_shows_a_message_and_logs_no_text(
    auth_client, session, letter, monkeypatch, caplog
):
    fill(session, letter, body="Secret words")

    def broken(context):
        raise pdf_service.PDFError("OSError")

    monkeypatch.setattr(pdf_service, "render_letter_pdf", broken)

    with caplog.at_level(logging.WARNING, logger="unfurl.cover_letters"):
        response = pdf_of(auth_client, letter)

    assert response.status_code == 503
    assert str(escape("We couldn't create the PDF")) in response.text
    assert f'href="/cover-letters/{letter.id}"' in response.text
    assert "Your cover letter is saved." in response.text
    assert "OSError" in caplog.text and "Secret words" not in caplog.text


# --- The links --------------------------------------------------------------------------------


def test_the_editor_has_a_download_button(auth_client, letter):
    page = auth_client.get(f"/cover-letters/{letter.id}").text

    assert f'href="/cover-letters/{letter.id}/pdf"' in page
    assert 'data-tooltip="Download PDF"' in page and "data-letter-download" in page


def test_the_documents_card_links_the_pdf(auth_client, session, user, letter):
    job = apps.create(session, user, company="Acme", job_title="PM")
    apps.link_letter(session, user, job, letter)

    page = auth_client.get(f"/applications/{job.id}").text

    assert f'href="/cover-letters/{letter.id}/pdf"' in page
