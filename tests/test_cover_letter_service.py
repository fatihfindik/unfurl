"""cover_letter_service: masters, copies, versions, locks, ownership, Undo
(SPEC §5.4, D-152…D-154; shared rules in lineage, D-153)."""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.models import CoverLetter, Profile
from app.schemas.cover_letter import CoverLetterForm, NewCoverLetterForm
from app.services import cover_letter_service as letters
from app.services import trash_service
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile


@pytest.fixture
def profile(session, user) -> Profile:
    profile = get_or_create_profile(session, user)
    profile.email = "jana@example.com"
    profile.phone = "+43 660 1234567"
    profile.location = "Vienna, Austria"
    session.add(profile)
    session.commit()
    return profile


def new_letter(session, user, language="en", title="Letter"):
    return letters.create_letter(session, user, title, language)


def form_of(letter, **changes) -> CoverLetterForm:
    values = {name: getattr(letter, name) for name in CoverLetterForm.model_fields}
    return CoverLetterForm.model_validate(values | changes)


def edit(session, letter, *, master_unlocked=False, **changes):
    return letters.update_letter(
        session, letter, form_of(letter, **changes), master_unlocked=master_unlocked
    )


def reload(session, letter) -> CoverLetter:
    session.expire_all()
    return session.get(CoverLetter, letter.id)


# --- Masters and copies -------------------------------------------------------------


def test_first_letter_in_a_language_is_the_master_with_defaults(session, user, profile):
    letter = new_letter(session, user, language="de")

    assert letter.is_master and letter.version == 0 and letter.parent_id is None
    assert letter.sender_name == user.full_name
    assert letter.sender_email == "jana@example.com"
    assert letter.sender_phone == "+43 660 1234567"
    assert letter.sender_address == "Vienna, Austria"
    assert letter.place == "Vienna, Austria"
    assert letter.salutation == "Sehr geehrte Damen und Herren,"
    assert letter.closing == "Mit freundlichen Grüßen"
    assert letter.subject == "" and letter.body == ""


@pytest.mark.parametrize(
    ("language", "salutation", "closing"),
    [("en", "Dear Hiring Team,", "Kind regards,"), ("tr", "Sayın Yetkili,", "Saygılarımla,")],
)
def test_salutation_and_closing_follow_the_language(
    session, user, profile, language, salutation, closing
):
    letter = new_letter(session, user, language=language)
    assert (letter.salutation, letter.closing) == (salutation, closing)


def test_later_letters_are_numbered_copies_with_the_masters_text(session, user, profile):
    master = new_letter(session, user)
    edit(session, master, master_unlocked=True, body="My master text.", subject="Application")
    first = new_letter(session, user, title="For Kranich")
    second = new_letter(session, user, title="For Dalux")

    for number, copy in [(1, first), (2, second)]:
        assert not copy.is_master
        assert copy.parent_id == master.id
        assert copy.version == number
        assert copy.body == "My master text." and copy.subject == "Application"
    assert first.title == "For Kranich"


def test_another_language_gets_its_own_master(session, user, profile):
    english = new_letter(session, user, language="en")
    german = new_letter(session, user, language="de")
    assert english.is_master and german.is_master


def test_the_database_allows_one_master_per_language(session, user, profile):
    new_letter(session, user)
    session.add(CoverLetter(user_id=user.id, title="Second", language="en", is_master=True))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_a_version_number_is_used_once_per_master(session, user, profile):
    master = new_letter(session, user)
    copy = new_letter(session, user)
    session.add(
        CoverLetter(
            user_id=user.id, title="Twin", language="en", parent_id=master.id, version=copy.version
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_editing_the_master_does_not_change_its_copies(session, user, profile):
    master = new_letter(session, user)
    copy = new_letter(session, user)

    edit(session, master, master_unlocked=True, body="New master text.")

    assert reload(session, copy).body == ""


def test_the_limit_counts_letters(session, user, profile, monkeypatch):
    monkeypatch.setattr(letters, "MAX_LETTERS_PER_USER", 2)
    new_letter(session, user)
    new_letter(session, user)
    with pytest.raises(letters.TooManyDocuments):
        new_letter(session, user)


# --- Versions -----------------------------------------------------------------------


def test_a_new_version_of_an_unedited_copy_is_refused(session, user, profile):
    new_letter(session, user)
    copy = new_letter(session, user)
    with pytest.raises(letters.NothingChanged):
        letters.save_as_new_version(session, user, copy)


def test_a_new_version_of_an_edited_copy_is_the_next_number(session, user, profile):
    master = new_letter(session, user)
    copy = new_letter(session, user)
    edit(session, copy, body="Changed.")

    version = letters.save_as_new_version(session, user, copy)

    assert version.parent_id == master.id
    assert version.version == copy.version + 1
    assert version.body == "Changed."


def test_versions_in_lineage_lists_the_master_first(session, user, profile):
    master = new_letter(session, user)
    copy = new_letter(session, user)
    new_letter(session, user, language="de")  # another lineage

    assert [v.id for v in letters.versions_in_lineage(session, user, copy)] == [master.id, copy.id]


# --- Masters and locks ----------------------------------------------------------------


def test_a_master_changes_only_after_it_was_unlocked(session, user, profile):
    master = new_letter(session, user)
    with pytest.raises(letters.MasterNotUnlocked):
        edit(session, master, body="Changed.")
    with pytest.raises(letters.MasterNotUnlocked):
        letters.refresh_sender(session, user, master)
    assert reload(session, master).body == ""


def test_a_locked_letter_never_changes(session, user, profile):
    new_letter(session, user)
    copy = new_letter(session, user)
    edit(session, copy, body="Sent text.")
    letters.lock(session, copy)

    with pytest.raises(letters.DocumentLocked):
        edit(session, copy, body="Changed.")
    with pytest.raises(letters.DocumentLocked):
        letters.refresh_sender(session, user, copy)
    with pytest.raises(letters.DocumentLocked):
        letters.delete_letter(session, copy, user)
    assert reload(session, copy).body == "Sent text."


def test_duplicate_as_new_version_of_a_locked_letter_is_a_draft(session, user, profile):
    new_letter(session, user)
    copy = new_letter(session, user)
    letters.lock(session, copy)  # never edited, but locked: allowed

    draft = letters.save_as_new_version(session, user, copy)

    assert draft.status == "draft" and draft.version == copy.version + 1
    assert reload(session, copy).is_locked


def test_a_master_is_never_locked(session, user, profile):
    master = new_letter(session, user)
    with pytest.raises(ValueError):
        letters.lock(session, master)


def test_refresh_from_profile_changes_only_the_sender(session, user, profile):
    new_letter(session, user)
    copy = new_letter(session, user)
    edit(session, copy, sender_phone="old", body="Kept.", place="Graz")
    profile.phone = "+43 1 999"
    profile.version += 1
    session.add(profile)
    session.commit()

    letters.refresh_sender(session, user, copy)

    copy = reload(session, copy)
    assert copy.sender_phone == "+43 1 999"
    assert copy.source_profile_version == profile.version
    assert (copy.body, copy.place) == ("Kept.", "Graz")


# --- Delete and Undo ----------------------------------------------------------------


def test_a_master_with_copies_cant_be_deleted(session, user, profile):
    master = new_letter(session, user)
    new_letter(session, user)
    with pytest.raises(letters.MasterHasCopies) as info:
        letters.delete_letter(session, master, user)
    assert info.value.copies == 1


def test_a_deleted_letter_comes_back_with_its_text(session, user, profile):
    new_letter(session, user)
    copy = new_letter(session, user)
    edit(session, copy, body="Dear Kranich.", recipient_company="Kranich Health")
    item_id = letters.delete_letter(session, copy, user)

    item = trash_service.take(session, user, item_id)
    restored = letters.restore_letter(session, user, item.payload)
    session.commit()

    assert restored.body == "Dear Kranich."
    assert restored.recipient_company == "Kranich Health"
    assert restored.version == copy.version


def test_a_copy_doesnt_come_back_without_its_master(session, user, profile):
    master = new_letter(session, user)
    copy = new_letter(session, user)
    copy_item = letters.delete_letter(session, copy, user)
    letters.delete_letter(session, master, user)

    item = trash_service.take(session, user, copy_item)
    with pytest.raises(trash_service.CannotRestore):
        letters.restore_letter(session, user, item.payload)


def test_an_old_master_doesnt_come_back_over_a_new_one(session, user, profile):
    master = new_letter(session, user)
    item_id = letters.delete_letter(session, master, user)
    new_letter(session, user)  # the new master of the language

    item = trash_service.take(session, user, item_id)
    with pytest.raises(trash_service.CannotRestore):
        letters.restore_letter(session, user, item.payload)


def test_another_users_payload_is_refused(session, user, profile):
    new_letter(session, user)
    copy = new_letter(session, user)
    item_id = letters.delete_letter(session, copy, user)
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")

    payload = trash_service.take(session, user, item_id).payload
    with pytest.raises(trash_service.CannotRestore):
        letters.restore_letter(session, other, payload)


# --- Ownership ----------------------------------------------------------------------


def test_another_users_letter_is_not_found(session, user, profile):
    letter = new_letter(session, user)
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")

    with pytest.raises(HTTPException) as info:
        letters.get_owned_letter(session, other, letter.id)
    assert info.value.status_code == 404
    assert letters.list_letters(session, other) == []


@pytest.mark.parametrize("letter_id", ["abc", "0", "-1", "99999999999999999999", None])
def test_odd_ids_are_not_found(session, user, letter_id):
    with pytest.raises(HTTPException) as info:
        letters.get_owned_letter(session, user, letter_id)
    assert info.value.status_code == 404


def test_list_shows_masters_first(session, user, profile):
    en_master = new_letter(session, user, language="en")
    en_copy = new_letter(session, user, language="en")
    de_master = new_letter(session, user, language="de")

    ids = [letter.id for letter in letters.list_letters(session, user)]
    assert ids == [de_master.id, en_master.id, en_copy.id]


# --- Forms --------------------------------------------------------------------------


def test_the_form_keeps_one_kind_of_line_break_and_empty_text_as_blank():
    form = CoverLetterForm.model_validate(
        {"title": " Letter ", "body": "First.\r\n\r\nSecond.\r\n", "subject": "   "}
    )
    columns = form.columns()
    assert columns["title"] == "Letter"
    assert columns["body"] == "First.\n\nSecond."
    assert columns["subject"] == "" and columns["salutation"] == ""
    assert columns["recipient_name"] is None


def test_the_body_has_a_limit():
    CoverLetterForm.model_validate({"title": "T", "body": "x" * 10_000})
    with pytest.raises(ValidationError):
        CoverLetterForm.model_validate({"title": "T", "body": "x" * 10_001})


def test_the_title_is_required_and_the_sender_email_must_be_an_address():
    with pytest.raises(ValidationError):
        CoverLetterForm.model_validate({"title": "  "})
    with pytest.raises(ValidationError):
        CoverLetterForm.model_validate({"title": "T", "sender_email": "not an email"})


def test_a_new_letter_needs_a_known_language():
    NewCoverLetterForm.model_validate({"title": "T", "language": "tr"})
    with pytest.raises(ValidationError):
        NewCoverLetterForm.model_validate({"title": "T", "language": "fr"})
