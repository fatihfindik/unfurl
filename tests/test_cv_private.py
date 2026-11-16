"""Date of birth and nationality on a CV (D-053): each with its own box, off by default,
copied from the profile only while on; never sent to the AI."""

from datetime import date

import pytest

from app.models import CVDocument
from app.schemas.cv import CVContent, CVPersonal
from app.services import document_service as docs
from app.services import pdf_service
from app.services.profile_service import get_or_create_profile
from tests.test_cv_editor import save, stored


@pytest.fixture
def profile(session, user):
    profile = get_or_create_profile(session, user)
    profile.date_of_birth = date(1990, 3, 1)
    profile.nationality = "Österreich"
    session.add(profile)
    session.commit()
    return profile


def new_copy(session, user, language="en"):
    docs.create_cv(session, user, "Master", language, "classic")
    return docs.create_cv(session, user, "Copy", language, "classic")


def reload(session, cv) -> CVDocument:
    session.expire_all()
    return session.get(CVDocument, cv.id)


def test_off_by_default_and_not_stored(session, user, profile):
    cv = new_copy(session, user)

    personal = stored(session, cv).personal
    assert not cv.show_birth_date and not cv.show_nationality
    assert personal.date_of_birth is None and personal.nationality is None


def test_each_box_brings_its_own_value(auth_client, session, user, profile):
    cv = new_copy(session, user)

    save(auth_client, cv, show_birth_date="on")
    personal = stored(session, cv).personal
    assert personal.date_of_birth == date(1990, 3, 1)
    assert personal.nationality is None  # its own box, still off

    save(auth_client, cv, show_nationality="on")  # birth date box not sent: off again
    personal = stored(session, cv).personal
    assert personal.date_of_birth is None
    assert personal.nationality == "Österreich"
    assert reload(session, cv).show_nationality


@pytest.mark.parametrize(
    ("language", "line"),
    [
        ("en", "Date of birth: 1 March 1990 · Nationality: Österreich"),
        ("de", "Geburtsdatum: 01.03.1990 · Staatsangehörigkeit: Österreich"),
        ("tr", "Doğum tarihi: 01.03.1990 · Uyruk: Österreich"),
    ],
)
def test_shown_in_the_cv_language_in_preview_and_pdf(
    auth_client, session, user, profile, language, line
):
    cv = new_copy(session, user, language)

    response = save(auth_client, cv, show_birth_date="on", show_nationality="on")

    assert line in response.text
    assert line in pdf_service.render_cv_html(reload(session, cv), for_pdf=True)


def test_a_sent_cv_keeps_its_values(auth_client, session, user, profile):
    cv = new_copy(session, user)
    save(auth_client, cv, show_birth_date="on", show_nationality="on", summary="Edited.")
    docs.lock(session, reload(session, cv))

    profile.nationality = "Deutschland"
    session.add(profile)
    session.commit()

    assert stored(session, cv).personal.nationality == "Österreich"


def test_refresh_takes_the_current_values_only_for_boxes_that_are_on(
    auth_client, session, user, profile
):
    cv = new_copy(session, user)
    save(auth_client, cv, show_nationality="on")
    profile.nationality = "Deutschland"
    session.add(profile)
    session.commit()

    docs.refresh_from_profile(session, user, reload(session, cv))

    personal = stored(session, cv).personal
    assert personal.nationality == "Deutschland"
    assert personal.date_of_birth is None  # its box is off


def test_new_versions_keep_both_boxes_and_their_values(auth_client, session, user, profile):
    # Security review M3b (E1): the nationality box was lost on copies while the
    # value stayed in the content, against D-053.
    cv = new_copy(session, user)
    save(auth_client, cv, show_birth_date="on", show_nationality="on", summary="Edited.")

    v2 = docs.save_as_new_version(session, user, reload(session, cv))

    assert v2.show_birth_date and v2.show_nationality
    assert stored(session, v2).personal.nationality == "Österreich"
    assert stored(session, v2).personal.date_of_birth == date(1990, 3, 1)


def test_a_missing_value_is_explained(auth_client, session, user):
    get_or_create_profile(session, user)  # no date of birth, no nationality
    cv = new_copy(session, user)

    response = save(auth_client, cv, show_birth_date="on", show_nationality="on")

    assert "Not in your profile yet: date of birth, nationality." in response.text


def test_without_private_removes_photo_birth_date_and_nationality():
    content = CVContent(
        personal=CVPersonal(
            full_name="Jana",
            photo="QUFB",
            photo_type="image/jpeg",
            date_of_birth=date(1990, 3, 1),
            nationality="Czech",
        ),
        summary="Kept.",
    )

    safe = content.without_private()

    assert safe.personal.photo is None and safe.personal.photo_type is None
    assert safe.personal.date_of_birth is None and safe.personal.nationality is None
    assert safe.personal.full_name == "Jana" and safe.summary == "Kept."
    assert content.personal.nationality == "Czech"  # the original is not changed


def test_old_cvs_without_these_fields_still_load():
    loaded = CVContent.model_validate({"schema_version": 1, "personal": {"full_name": "A"}})

    assert loaded.personal.date_of_birth is None and loaded.personal.nationality is None
