"""Editor actions: versions, duplicate, refresh, delete (SPEC §6.5, D-045…D-048)."""

import pytest
from sqlmodel import select

from app.models import CVDocument
from app.schemas.cv import CVContent
from app.services import document_service as docs
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile
from tests.helpers import unlock_master
from tests.test_profile_sections import htmx_send


@pytest.fixture
def profile(session, user):
    profile = get_or_create_profile(session, user)
    profile.summary = "From the profile."
    session.add(profile)
    session.commit()
    return profile


@pytest.fixture
def master(session, user, profile):
    return docs.create_cv(session, user, "Master CV", "en", "classic")


@pytest.fixture
def copy(session, user, master):
    return docs.create_cv(session, user, "For Acme", "en", "classic")


def edited(session, cv) -> CVDocument:
    """The CV after one change, so a new version of it isn't refused as identical."""
    content = CVContent.model_validate(cv.content)
    content.summary = "Edited."
    return docs.update_content(session, cv, title=cv.title, content=content)


def all_cvs(session) -> list[CVDocument]:
    session.expire_all()
    return list(session.exec(select(CVDocument).order_by(CVDocument.id)))


# --- Buttons and version picker -----------------------------------------------------


def test_draft_copy_has_its_actions_as_icon_buttons(auth_client, copy):
    page = auth_client.get(f"/cvs/{copy.id}").text

    assert f'hx-post="/cvs/{copy.id}/versions"' in page
    assert 'data-tooltip="Save as new version"' in page
    assert 'data-tooltip="Refresh from profile"' in page and 'data-dialog-open="refresh-cv"' in page
    assert 'data-tooltip="Delete"' in page and f'hx-delete="/cvs/{copy.id}"' in page
    assert "This can't be undone." not in page  # no confirmation since M4c: Undo (D-086)


def test_duplicate_is_back_and_all_documents_needs_an_application(auth_client, copy):
    # M4c (D-084): "Duplicate" makes a copy for another application; "All documents"
    # shows when the back link points to an application instead of Documents.
    page = auth_client.get(f"/cvs/{copy.id}").text

    assert f'hx-post="/cvs/{copy.id}/duplicate"' in page
    assert "All documents" not in page


def test_closed_master_has_no_refresh_until_unlocked(auth_client, master):
    assert 'data-dialog-open="refresh-cv"' not in auth_client.get(f"/cvs/{master.id}").text

    value = unlock_master(auth_client, "cv", master.id)
    unlocked = auth_client.get(f"/cvs/{master.id}?unlock={value}").text
    assert 'data-dialog-open="refresh-cv"' in unlocked
    assert f'hx-vals=\'{{"unlock": "{value}"}}\'' in unlocked


def test_locked_copy_offers_only_duplicate_as_new_version(auth_client, session, copy):
    docs.lock(session, copy)

    page = auth_client.get(f"/cvs/{copy.id}").text

    assert "Duplicate as new version" in page
    assert "Save as new version" not in page
    assert 'data-dialog-open="refresh-cv"' not in page
    assert 'data-dialog-open="delete-cv"' not in page


def test_version_picker_lists_master_and_copies(auth_client, session, user, master, copy):
    v2 = docs.save_as_new_version(session, user, edited(session, copy))

    page = auth_client.get(f"/cvs/{v2.id}").text

    assert "Version: v2" in page
    for cv in (master, copy, v2):
        assert f'href="/cvs/{cv.id}"' in page
    assert f'<a href="/cvs/{v2.id}" aria-current="page"' in page


# --- Save as new version / duplicate ------------------------------------------------


def test_save_as_new_version_opens_the_new_draft(auth_client, session, copy):
    edited(session, copy)

    response = htmx_send(auth_client, "POST", f"/cvs/{copy.id}/versions")

    new = all_cvs(session)[-1]
    assert response.headers["HX-Redirect"] == f"/cvs/{new.id}"
    assert new.version == 2 and new.parent_id == copy.parent_id


def test_nothing_changed_says_so_and_makes_no_version(auth_client, session, copy):
    response = htmx_send(auth_client, "POST", f"/cvs/{copy.id}/versions")

    assert "HX-Redirect" not in response.headers
    assert "Nothing changed since this version." in response.text
    assert len(all_cvs(session)) == 2


def test_a_locked_copy_becomes_a_new_draft(auth_client, session, copy):
    docs.lock(session, copy)

    htmx_send(auth_client, "POST", f"/cvs/{copy.id}/versions")

    new = all_cvs(session)[-1]
    assert new.status == "draft" and new.version == 2


def test_duplicate_opens_the_copy(auth_client, session, master):
    response = htmx_send(auth_client, "POST", f"/cvs/{master.id}/duplicate")

    new = all_cvs(session)[-1]
    assert response.headers["HX-Redirect"] == f"/cvs/{new.id}"
    assert new.title == "Master CV (copy)"


def test_the_limit_shows_a_message(auth_client, session, copy, monkeypatch):
    monkeypatch.setattr(docs, "MAX_CVS_PER_USER", 2)

    response = htmx_send(auth_client, "POST", f"/cvs/{copy.id}/duplicate")

    assert "HX-Redirect" not in response.headers
    assert "You can have up to" in response.text
    assert len(all_cvs(session)) == 2


# --- Refresh from profile -----------------------------------------------------------


def test_refresh_gets_the_new_profile_text(auth_client, session, profile, copy):
    profile.summary = "Newer profile text."
    session.add(profile)
    session.commit()

    response = htmx_send(auth_client, "POST", f"/cvs/{copy.id}/refresh")

    assert response.headers["HX-Redirect"] == f"/cvs/{copy.id}"
    assert CVContent.model_validate(all_cvs(session)[1].content).summary == "Newer profile text."


def test_refresh_of_a_master_needs_the_unlock(auth_client, session, profile, master):
    profile.summary = "Newer profile text."
    session.add(profile)
    session.commit()

    assert htmx_send(auth_client, "POST", f"/cvs/{master.id}/refresh").status_code == 409
    assert CVContent.model_validate(all_cvs(session)[0].content).summary == "From the profile."

    value = unlock_master(auth_client, "cv", master.id)
    response = htmx_send(auth_client, "POST", f"/cvs/{master.id}/refresh", {"unlock": value})
    assert response.headers["HX-Redirect"] == f"/cvs/{master.id}?unlock={value}"
    assert CVContent.model_validate(all_cvs(session)[0].content).summary == "Newer profile text."


def test_refresh_of_a_locked_copy_is_refused(auth_client, session, copy):
    docs.lock(session, copy)

    assert htmx_send(auth_client, "POST", f"/cvs/{copy.id}/refresh").status_code == 409


# --- Delete -------------------------------------------------------------------------


def test_delete_a_copy_goes_back_to_documents(auth_client, session, copy):
    response = htmx_send(auth_client, "DELETE", f"/cvs/{copy.id}")

    assert response.headers["HX-Redirect"] == "/documents"
    assert copy.id not in [cv.id for cv in all_cvs(session)]


def test_a_master_with_copies_says_why_it_stays(auth_client, session, master, copy):
    response = htmx_send(auth_client, "DELETE", f"/cvs/{master.id}")

    assert "HX-Redirect" not in response.headers
    assert "This master has 1 copy. Delete the copy first." in response.headers["HX-Trigger"]
    assert len(all_cvs(session)) == 2


def test_a_locked_copy_cannot_be_deleted(auth_client, session, copy):
    docs.lock(session, copy)

    assert htmx_send(auth_client, "DELETE", f"/cvs/{copy.id}").status_code == 409
    assert len(all_cvs(session)) == 2


# --- Another user's CV, CSRF ----------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/versions"), ("POST", "/duplicate"), ("POST", "/refresh"), ("DELETE", "")],
)
def test_another_users_cv_is_404_and_unchanged(auth_client, session, method, path):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    get_or_create_profile(session, other)
    theirs = docs.create_cv(session, other, "Theirs", "en", "classic")
    copy = docs.create_cv(session, other, "Their copy", "en", "classic")

    response = htmx_send(auth_client, method, f"/cvs/{copy.id}{path}")

    assert response.status_code == 404
    assert [cv.id for cv in all_cvs(session)] == [theirs.id, copy.id]


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/versions"), ("POST", "/duplicate"), ("POST", "/refresh"), ("DELETE", "")],
)
def test_actions_need_a_csrf_token(auth_client, session, copy, method, path):
    response = auth_client.request(method, f"/cvs/{copy.id}{path}", headers={"HX-Request": "true"})

    assert response.status_code == 403
    assert len(all_cvs(session)) == 2
