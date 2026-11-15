"""document_service: masters, copies, versions, locks, ownership (SPEC §5.3, D-045…D-048)."""

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app.models import CVDocument, Profile
from app.schemas.cv import CVContent
from app.services import document_service as docs
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile


@pytest.fixture
def profile(session, user) -> Profile:
    profile = get_or_create_profile(session, user)
    profile.headline = "Senior PM"
    profile.summary = "From the profile."
    session.add(profile)
    session.commit()
    return profile


def new_cv(session, user, language="en", title="CV"):
    return docs.create_cv(session, user, title, language, "classic")


def content_of(cv) -> CVContent:
    return CVContent.model_validate(cv.content)


def edit(session, cv, *, summary, master_unlocked=False):
    content = content_of(cv)
    content.summary = summary
    return docs.update_content(
        session, cv, title=cv.title, content=content, master_unlocked=master_unlocked
    )


def reload(session, cv) -> CVDocument:
    session.expire_all()
    return session.get(CVDocument, cv.id)


# --- Masters and copies -------------------------------------------------------------


def test_first_cv_in_a_language_is_the_master(session, user, profile):
    cv = new_cv(session, user)

    assert cv.is_master
    assert cv.version == 0
    assert cv.parent_id is None
    assert content_of(cv).personal.headline == "Senior PM"


def test_later_cvs_are_numbered_copies_under_the_master(session, user, profile):
    master = new_cv(session, user)
    first = new_cv(session, user)
    second = new_cv(session, user)

    assert not first.is_master and first.parent_id == master.id and first.version == 1
    assert second.parent_id == master.id and second.version == 2


def test_each_language_has_its_own_master(session, user, profile):
    english = new_cv(session, user, "en")
    german = new_cv(session, user, "de")

    assert english.is_master and german.is_master


def test_two_new_cvs_at_the_same_moment_give_one_master(session, user, profile, monkeypatch):
    # The second request didn't see the first master yet (it checked before the
    # first one was saved); the unique index turns it into a copy.
    master = new_cv(session, user)
    monkeypatch.setattr(docs, "current_master", _first_none_then_real(docs.current_master))

    cv = new_cv(session, user)

    assert not cv.is_master
    assert cv.parent_id == master.id


def _first_none_then_real(real):
    calls = []

    def fake(session, user, language):
        calls.append(1)
        return None if len(calls) == 1 else real(session, user, language)

    return fake


def test_a_user_has_at_most_100_cvs(session, user, profile, monkeypatch):
    monkeypatch.setattr(docs, "MAX_CVS_PER_USER", 3)
    master = new_cv(session, user)
    new_cv(session, user)
    new_cv(session, user)

    with pytest.raises(docs.TooManyDocuments):
        new_cv(session, user)
    with pytest.raises(docs.TooManyDocuments):
        docs.duplicate(session, user, master)
    with pytest.raises(docs.TooManyDocuments):
        docs.save_as_new_version(session, user, master)


# --- Editing never leaks to other documents or the profile --------------------------


def test_editing_a_copy_changes_neither_the_master_nor_the_profile(session, user, profile):
    master = new_cv(session, user)
    copy = new_cv(session, user)

    edit(session, copy, summary="Only in the copy.")

    assert content_of(reload(session, copy)).summary == "Only in the copy."
    assert content_of(reload(session, master)).summary == "From the profile."
    session.refresh(profile)
    assert profile.summary == "From the profile."


def test_editing_the_master_does_not_change_existing_copies(session, user, profile):
    master = new_cv(session, user)
    copy = new_cv(session, user)

    edit(session, master, summary="New master text.", master_unlocked=True)

    assert content_of(reload(session, master)).summary == "New master text."
    assert content_of(reload(session, copy)).summary == "From the profile."


def test_a_master_is_only_changed_after_it_was_unlocked(session, user, profile):
    master = new_cv(session, user)

    with pytest.raises(docs.MasterNotUnlocked):
        edit(session, master, summary="Sneaky change.")
    with pytest.raises(docs.MasterNotUnlocked):
        docs.refresh_from_profile(session, user, master)

    assert content_of(reload(session, master)).summary == "From the profile."


# --- Versions -----------------------------------------------------------------------


def test_save_as_new_version_takes_the_next_free_number(session, user, profile):
    new_cv(session, user)
    v1 = edit(session, new_cv(session, user), summary="Edited v1.")

    first = docs.save_as_new_version(session, user, v1)
    second = docs.save_as_new_version(session, user, v1)  # again from v1: no second v2

    assert (first.version, second.version) == (2, 3)
    assert first.parent_id == second.parent_id == v1.parent_id


def test_a_new_version_copies_the_content_and_settings(session, user, profile):
    new_cv(session, user)
    v1 = new_cv(session, user)
    edit(session, v1, summary="Tailored.")

    v2 = docs.save_as_new_version(session, user, reload(session, v1))

    assert content_of(v2).summary == "Tailored."
    assert v2.title == v1.title
    assert v2.status == "draft"


def test_a_never_edited_draft_gives_no_identical_new_version(session, user, profile):
    new_cv(session, user)
    v1 = new_cv(session, user)

    with pytest.raises(docs.NothingChanged):
        docs.save_as_new_version(session, user, v1)

    edit(session, v1, summary="Now it is different.")
    assert docs.save_as_new_version(session, user, reload(session, v1)).version == 2


def test_a_new_draft_is_never_edited_until_saved(session, user, profile):
    new_cv(session, user)
    copy = new_cv(session, user)
    assert docs.never_edited(reload(session, copy))

    edit(session, copy, summary="Changed.")
    assert not docs.never_edited(reload(session, copy))


def test_a_new_version_of_the_master_is_a_copy_under_it(session, user, profile):
    master = new_cv(session, user)

    copy = docs.save_as_new_version(session, user, master)

    assert not copy.is_master
    assert copy.parent_id == master.id
    assert copy.version == 1


def test_duplicate_makes_a_separate_copy(session, user, profile):
    master = new_cv(session, user, title="Main CV")

    copy = docs.duplicate(session, user, master)

    assert copy.title == "Main CV (copy)"
    assert copy.parent_id == master.id
    assert not copy.is_master


def test_duplicate_does_not_repeat_copy_in_the_title(session, user, profile):
    master = new_cv(session, user, title="Main CV")

    again = docs.duplicate(session, user, docs.duplicate(session, user, master))

    assert again.title == "Main CV (copy)"


def test_duplicate_keeps_long_titles_within_the_limit(session, user, profile):
    master = new_cv(session, user, title="x" * 200)

    assert len(docs.duplicate(session, user, master).title) == 200


def test_versions_in_lineage_lists_master_and_copies(session, user, profile):
    master = new_cv(session, user)
    v1 = edit(session, new_cv(session, user), summary="Edited v1.")
    v2 = docs.save_as_new_version(session, user, v1)
    new_cv(session, user, "de")  # another master, not in this lineage

    ids = [cv.id for cv in docs.versions_in_lineage(session, user, v2)]

    assert ids == [master.id, v1.id, v2.id]


# --- Refresh from profile -----------------------------------------------------------


def test_refresh_from_profile_replaces_the_content(session, user, profile):
    new_cv(session, user)
    copy = new_cv(session, user)
    edit(session, copy, summary="Edited.")
    profile.summary = "Newer profile text."
    session.add(profile)
    session.commit()

    refreshed = docs.refresh_from_profile(session, user, reload(session, copy))

    assert content_of(refreshed).summary == "Newer profile text."
    session.refresh(profile)
    assert refreshed.source_profile_version == profile.version


# --- Locks --------------------------------------------------------------------------


def test_a_locked_copy_can_never_change_or_be_deleted(session, user, profile):
    new_cv(session, user)
    copy = docs.lock(session, new_cv(session, user))
    assert copy.is_locked and copy.locked_at is not None

    with pytest.raises(docs.DocumentLocked):
        edit(session, copy, summary="Changed after sending.")
    with pytest.raises(docs.DocumentLocked):
        docs.refresh_from_profile(session, user, copy)
    with pytest.raises(docs.DocumentLocked):
        docs.delete_cv(session, copy)

    stored = reload(session, copy)
    assert stored is not None
    assert content_of(stored).summary == "From the profile."


def test_a_locked_copy_can_be_duplicated_as_a_new_draft(session, user, profile):
    new_cv(session, user)
    sent = docs.lock(session, new_cv(session, user))

    draft = docs.save_as_new_version(session, user, sent)
    edit(session, draft, summary="Changes go into the new draft.")

    assert draft.status == "draft"
    assert content_of(reload(session, sent)).summary == "From the profile."


def test_masters_are_never_locked(session, user, profile):
    master = new_cv(session, user)

    with pytest.raises(ValueError):
        docs.lock(session, master)
    assert not reload(session, master).is_locked


def test_locking_twice_keeps_the_first_time(session, user, profile):
    new_cv(session, user)
    copy = docs.lock(session, new_cv(session, user))
    first = copy.locked_at

    assert docs.lock(session, copy).locked_at == first


# --- Delete -------------------------------------------------------------------------


def test_a_draft_copy_can_be_deleted(session, user, profile):
    new_cv(session, user)
    copy = new_cv(session, user)

    docs.delete_cv(session, copy)

    assert reload(session, copy) is None


def test_a_master_with_copies_cannot_be_deleted(session, user, profile):
    master = new_cv(session, user)
    new_cv(session, user)
    new_cv(session, user)

    with pytest.raises(docs.MasterHasCopies) as error:
        docs.delete_cv(session, master)

    assert error.value.copies == 2
    assert reload(session, master) is not None


def test_after_its_copies_are_gone_a_master_can_be_deleted(session, user, profile):
    master = new_cv(session, user)
    copy = new_cv(session, user)
    docs.delete_cv(session, copy)

    docs.delete_cv(session, reload(session, master))

    assert reload(session, master) is None
    assert new_cv(session, user).is_master  # the next CV in English is the master again


# --- Ownership ----------------------------------------------------------------------


def test_another_users_cv_is_404(session, user, profile):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = new_cv(session, other)

    with pytest.raises(HTTPException) as error:
        docs.get_owned_cv(session, user, theirs.id)
    assert error.value.status_code == 404


@pytest.mark.parametrize("cv_id", ["abc", "0", "-1", str(2**40), None])
def test_ids_that_cannot_exist_are_404(session, user, cv_id):
    with pytest.raises(HTTPException) as error:
        docs.get_owned_cv(session, user, cv_id)
    assert error.value.status_code == 404


def test_list_shows_only_the_users_own_cvs_masters_first(session, user, profile):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    new_cv(session, other)
    master = new_cv(session, user)
    copy = new_cv(session, user)
    german = new_cv(session, user, "de")

    listed = docs.list_cvs(session, user)

    assert [cv.id for cv in listed] == [german.id, master.id, copy.id]
    assert len(session.exec(select(CVDocument)).all()) == 4


def test_version_numbers_and_copy_counts_only_look_at_the_owner(session, user, profile):
    # Rows of another user under the same parent id can't shift the numbers
    # (CLAUDE.md: every query on user data filters by user_id).
    master = new_cv(session, user)
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    session.add(
        CVDocument(
            user_id=other.id, title="Odd", language="en", content={}, parent_id=master.id, version=7
        )
    )
    session.commit()

    copy = new_cv(session, user)

    assert copy.version == 1
    assert docs.copy_count(session, master) == 1


def test_a_failed_master_insert_that_is_not_the_race_is_not_hidden(
    session, user, profile, monkeypatch
):
    from sqlalchemy.exc import IntegrityError

    def failing_commit():
        raise IntegrityError("INSERT", {}, Exception("disk full"))

    monkeypatch.setattr(session, "commit", failing_commit)

    with pytest.raises(IntegrityError):
        new_cv(session, user)
