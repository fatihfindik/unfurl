"""Gaps in the M3a tests: who may use the CV routes, odd ids, and lock/master edge cases.

What test_cv_editor.py, test_cv_actions.py and test_documents_pages.py already check
is not repeated here.
"""

import pytest
from sqlmodel import select

from app.models import CVDocument
from app.services import document_service as docs
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile
from tests.helpers import csrf_token
from tests.test_cv_actions import all_cvs
from tests.test_cv_editor import save, stored
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


# (method, url) for every CV route; "1" stands for any CV id.
ROUTES = [
    ("GET", "/documents"),
    ("GET", "/documents?tab=letters"),
    ("POST", "/cvs"),
    ("GET", "/cvs/1"),
    ("GET", "/cvs/1?edit_master=confirmed"),
    ("POST", "/cvs/1"),
    ("POST", "/cvs/1/versions"),
    ("POST", "/cvs/1/duplicate"),
    ("POST", "/cvs/1/refresh"),
    ("DELETE", "/cvs/1"),
]
# The routes that take a CV id, as (method, path after the id).
CV_ROUTES = [
    ("GET", ""),
    ("POST", ""),
    ("POST", "/versions"),
    ("POST", "/duplicate"),
    ("POST", "/refresh"),
    ("DELETE", ""),
]


# --- Logged out ---------------------------------------------------------------------


@pytest.mark.parametrize(("method", "url"), ROUTES)
def test_logged_out_visitors_are_sent_to_login(client, method, url):
    # A token from the anonymous session, so the login check is what answers.
    headers = {"X-CSRF-Token": csrf_token(client)}
    response = client.request(method, url, headers=headers, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.parametrize(("method", "url"), ROUTES)
def test_logged_out_htmx_requests_get_hx_redirect(client, method, url):
    headers = {"X-CSRF-Token": csrf_token(client), "HX-Request": "true"}
    response = client.request(method, url, headers=headers, follow_redirects=False)

    assert response.headers["HX-Redirect"].startswith("/login")


# --- Ids that can't exist -----------------------------------------------------------


@pytest.mark.parametrize("cv_id", ["abc", "0", "-1", str(2**31), str(2**70)])
@pytest.mark.parametrize(("method", "path"), CV_ROUTES)
def test_ids_that_cannot_exist_are_404_on_every_route(auth_client, session, method, path, cv_id):
    response = htmx_send(auth_client, method, f"/cvs/{cv_id}{path}", {"title": "X"})

    assert response.status_code == 404
    assert session.exec(select(CVDocument)).all() == []


# --- Another user's master, even with the unlock -------------------------------------


@pytest.fixture
def other(session):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    get_or_create_profile(session, other)
    return other


@pytest.fixture
def their_master(session, other) -> CVDocument:
    return docs.create_cv(session, other, "Theirs", "en", "classic")


@pytest.mark.parametrize(("method", "path"), CV_ROUTES)
def test_another_users_master_is_404_even_when_unlocked(
    auth_client, session, their_master, method, path
):
    before = (their_master.title, dict(their_master.content), their_master.updated_at)
    url = f"/cvs/{their_master.id}{path}"
    if method == "GET":
        url += "?edit_master=confirmed"
    form = {"title": "Taken", "summary": "Taken", "edit_master": "confirmed"}

    response = htmx_send(auth_client, method, url, form)

    assert response.status_code == 404
    assert "Theirs" not in response.text
    cvs = all_cvs(session)
    assert [cv.id for cv in cvs] == [their_master.id]  # nothing added or deleted
    assert (cvs[0].title, cvs[0].content, cvs[0].updated_at) == before


@pytest.mark.parametrize("path", ["/versions", "/duplicate"])
def test_another_users_locked_copy_gives_no_new_draft(
    auth_client, session, other, their_master, path
):
    # A locked copy normally always gets a new draft (no "Nothing changed"),
    # so this is the request most likely to slip through.
    sent = docs.lock(session, docs.create_cv(session, other, "Sent", "en", "classic"))

    response = htmx_send(auth_client, "POST", f"/cvs/{sent.id}{path}")

    assert response.status_code == 404
    assert [cv.id for cv in all_cvs(session)] == [their_master.id, sent.id]


# --- Locked copies and masters ------------------------------------------------------


def test_the_unlock_does_not_open_a_locked_copy(auth_client, session, copy):
    docs.lock(session, copy)

    page = auth_client.get(f"/cvs/{copy.id}?edit_master=confirmed").text
    assert '<fieldset class="m-0 flex flex-col gap-4 border-0 p-0" disabled>' in page

    response = save(auth_client, copy, summary="Changed after sending.", edit_master="confirmed")
    assert response.status_code == 409
    assert stored(session, copy).summary == "From the profile."


def test_refresh_of_a_locked_copy_is_refused_even_with_the_unlock(
    auth_client, session, profile, copy
):
    docs.lock(session, copy)
    profile.summary = "Newer profile text."
    session.add(profile)
    session.commit()

    response = htmx_send(
        auth_client, "POST", f"/cvs/{copy.id}/refresh", {"edit_master": "confirmed"}
    )

    assert response.status_code == 409
    assert stored(session, copy).summary == "From the profile."


@pytest.mark.parametrize("value", ["yes", "Confirmed", "confirmed "])
def test_only_the_exact_unlock_value_opens_a_master(auth_client, session, master, value):
    response = save(auth_client, master, summary="Sneaky change.", edit_master=value)

    assert response.status_code == 409
    assert stored(session, master).summary == "From the profile."


@pytest.mark.parametrize("value", ["yes", "Confirmed"])
def test_the_editor_stays_read_only_for_other_unlock_values(auth_client, master, value):
    page = auth_client.get(f"/cvs/{master.id}?edit_master={value}").text

    assert '<fieldset class="m-0 flex flex-col gap-4 border-0 p-0" disabled>' in page
    assert 'name="edit_master"' not in page


@pytest.mark.parametrize("locked", [False, True])
def test_a_bad_title_does_not_open_a_partial_save_on_a_closed_document(
    auth_client, session, master, copy, locked
):
    # The partial save (one bad text field, the rest saved) must still go through the
    # master and lock rules, not around them.
    cv = copy if locked else master
    if locked:
        docs.lock(session, copy)
    before = stored(session, cv)

    response = save(auth_client, cv, title="", headline="Changed", visible_sections=["skills"])

    assert response.status_code == 409
    assert stored(session, cv) == before


def test_sections_not_on_the_page_change_nothing_on_a_locked_copy(auth_client, session, copy):
    docs.lock(session, copy)
    before = stored(session, copy)

    save(auth_client, copy, summary="Changed", visible_sections=["hobbies"])

    assert stored(session, copy) == before


def test_duplicate_of_a_locked_copy_is_a_new_draft_and_the_sent_one_stays(
    auth_client, session, copy
):
    docs.lock(session, copy)

    response = htmx_send(auth_client, "POST", f"/cvs/{copy.id}/duplicate")

    old, new = all_cvs(session)[1:]
    assert response.headers["HX-Redirect"] == f"/cvs/{new.id}"
    assert new.status == "draft" and new.locked_at is None
    assert old.id == copy.id and old.status == "locked"


def test_new_version_of_a_closed_master_leaves_the_master_alone(auth_client, session, master):
    # "Save as new version" is how a master's content is used without the unlock.
    before = (master.title, dict(master.content), master.updated_at)

    response = htmx_send(auth_client, "POST", f"/cvs/{master.id}/versions")

    old, new = all_cvs(session)
    assert response.headers["HX-Redirect"] == f"/cvs/{new.id}"
    assert not new.is_master and new.parent_id == master.id
    assert (old.title, old.content, old.updated_at) == before


def test_a_master_without_copies_can_be_deleted(auth_client, session, master):
    response = htmx_send(auth_client, "DELETE", f"/cvs/{master.id}")

    assert response.headers["HX-Redirect"] == "/documents"
    assert all_cvs(session) == []


def test_a_master_with_only_a_locked_copy_stays(auth_client, session, master, copy):
    docs.lock(session, copy)

    response = htmx_send(auth_client, "DELETE", f"/cvs/{master.id}")

    assert "This master has 1 copy." in response.text
    assert len(all_cvs(session)) == 2
