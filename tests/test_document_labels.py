"""CV words in German, English and Turkish (D-055)."""

from datetime import date

import pytest

from app.i18n.document_labels import LABELS, full_date, labels
from app.models import Experience
from app.services import document_service as docs
from app.services.profile_service import add_item, get_or_create_profile


def test_every_language_has_the_same_words():
    keys = set(LABELS["en"])
    for language, words in LABELS.items():
        assert set(words) == keys, language
        assert all(words.values()), language


def test_unknown_language_falls_back_to_english():
    assert labels("fr") == LABELS["en"]


@pytest.mark.parametrize(
    ("language", "text"),
    [("en", "1 March 1990"), ("de", "01.03.1990"), ("tr", "01.03.1990")],
)
def test_full_dates_are_written_the_local_way(language, text):
    assert full_date(date(1990, 3, 1), language) == text


@pytest.mark.parametrize(
    ("language", "heading"),
    [("en", "Experience"), ("de", "Berufserfahrung"), ("tr", "İş Deneyimi")],
)
def test_the_editor_preview_uses_the_cv_language(auth_client, session, user, language, heading):
    profile = get_or_create_profile(session, user)
    add_item(
        session,
        profile,
        Experience,
        {"company": "Acme", "title": "PM", "start_date": date(2020, 1, 1)},
    )
    cv = docs.create_cv(session, user, "CV", language, "classic")

    page = auth_client.get(f"/cvs/{cv.id}").text

    assert f"<h2>{heading}</h2>" in page
