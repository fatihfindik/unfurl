"""Gaps in the M3b tests: who may use the PDF and photo routes, odd ids, and the
private-detail boxes and template on closed documents.

What test_cv_pdf_route.py, test_cv_photo.py, test_cv_private.py and test_cv_editor.py
already check is not repeated here.
"""

from datetime import date

import pytest

from app.models import CVDocument
from app.services import document_service as docs
from app.services import pdf_service
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile
from tests.helpers import unlock_master
from tests.test_cv_editor import save, stored
from tests.test_cv_photo import give_profile_photo

NEW_ROUTES = ["/pdf", "/photo"]


@pytest.fixture
def profile(session, user):
    profile = get_or_create_profile(session, user)
    profile.date_of_birth = date(1990, 3, 1)
    profile.nationality = "Czech"
    session.add(profile)
    session.commit()
    return profile


@pytest.fixture
def master(session, user, profile):
    return docs.create_cv(session, user, "Master CV", "en", "classic")


@pytest.fixture
def copy(session, user, master):
    return docs.create_cv(session, user, "For Acme", "en", "classic")


@pytest.fixture
def no_render(monkeypatch):
    """Counts PDF renders instead of making real PDFs."""
    calls = []

    def fake(cv):
        calls.append(cv.id)
        return b"%PDF-fake"

    monkeypatch.setattr(pdf_service, "render_cv_pdf", fake)
    return calls


def row(session, cv) -> CVDocument:
    session.expire_all()
    return session.get(CVDocument, cv.id)


def snapshot(session, cv) -> tuple:
    cv = row(session, cv)
    boxes = (cv.show_photo, cv.show_birth_date, cv.show_nationality)
    return (cv.template, boxes, dict(cv.content), cv.updated_at)


# --- Logged out ---------------------------------------------------------------------


@pytest.mark.parametrize("path", NEW_ROUTES)
def test_logged_out_new_routes_go_to_login(client, path):
    response = client.get(f"/cvs/1{path}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.parametrize("path", NEW_ROUTES)
def test_logged_out_htmx_requests_to_new_routes_get_hx_redirect(client, path):
    response = client.get(f"/cvs/1{path}", headers={"HX-Request": "true"}, follow_redirects=False)

    assert response.headers["HX-Redirect"].startswith("/login")


# --- Ids that can't exist and other users' CVs ---------------------------------------


@pytest.mark.parametrize("cv_id", ["abc", "0", "-1", "1.5", str(2**31), str(2**70)])
@pytest.mark.parametrize("path", NEW_ROUTES)
def test_ids_that_cannot_exist_are_404(auth_client, no_render, path, cv_id):
    assert auth_client.get(f"/cvs/{cv_id}{path}").status_code == 404
    assert no_render == []


@pytest.fixture
def their_locked_cv(session):
    # A locked copy with a photo: what another user would most like to see.
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    get_or_create_profile(session, other)
    give_profile_photo(session, other)
    docs.create_cv(session, other, "Master", "en", "classic")
    theirs = docs.create_cv(session, other, "Theirs", "en", "classic")
    docs.update_content(
        session,
        theirs,
        title=theirs.title,
        content=stored(session, theirs),
        shown={"show_photo": True},
        user=other,
    )
    assert stored(session, theirs).personal.photo  # it really has a photo to leak
    return docs.lock(session, row(session, theirs))


@pytest.mark.parametrize("path", NEW_ROUTES)
def test_another_users_locked_cv_with_a_photo_is_404(auth_client, no_render, their_locked_cv, path):
    response = auth_client.get(f"/cvs/{their_locked_cv.id}{path}")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(("text/html", "application/json"))
    assert no_render == []  # no PDF was made of their CV


# --- Private boxes and template on closed documents ---------------------------------


@pytest.mark.parametrize(
    "boxes",
    [
        {"show_photo": "on"},
        {"show_birth_date": "on"},
        {"show_nationality": "on"},
        {"show_photo": "on", "show_birth_date": "on", "show_nationality": "on"},
    ],
)
def test_private_boxes_on_a_locked_copy_are_refused(auth_client, session, user, copy, boxes):
    give_profile_photo(session, user)
    docs.lock(session, copy)
    before = snapshot(session, copy)

    response = save(auth_client, copy, unlock="x", **boxes)

    assert response.status_code == 409
    assert snapshot(session, copy) == before


def test_switching_off_details_on_a_locked_copy_is_refused(auth_client, session, copy):
    # The sent CV must keep the details it was sent with.
    save(auth_client, copy, show_birth_date="on", show_nationality="on")
    docs.lock(session, row(session, copy))
    before = snapshot(session, copy)

    response = save(auth_client, copy)  # boxes not sent = switched off

    assert response.status_code == 409
    assert snapshot(session, copy) == before
    assert stored(session, copy).personal.nationality == "Czech"


@pytest.mark.parametrize(
    "boxes",
    [
        {"show_photo": "on"},
        {"show_birth_date": "on", "show_nationality": "on"},
    ],
)
def test_private_boxes_on_a_closed_master_are_refused(auth_client, session, user, master, boxes):
    give_profile_photo(session, user)
    before = snapshot(session, master)

    response = save(auth_client, master, **boxes)

    assert response.status_code == 409
    assert snapshot(session, master) == before


def test_private_boxes_on_an_unlocked_master_are_saved(auth_client, session, master):
    # The other side of the test above: the unlock really opens the boxes.
    response = save(
        auth_client,
        master,
        show_nationality="on",
        unlock=unlock_master(auth_client, "cv", master.id),
    )

    assert response.status_code == 200
    assert row(session, master).show_nationality
    assert stored(session, master).personal.nationality == "Czech"


@pytest.mark.parametrize("template", ["modern", "compact", "fancy"])
def test_the_template_of_a_closed_master_stays(auth_client, session, master, template):
    before = snapshot(session, master)

    response = save(auth_client, master, template=template)

    assert response.status_code == 409
    assert snapshot(session, master) == before


# --- Odd values for the boxes ---------------------------------------------------------


@pytest.mark.parametrize("value", ["true", "1", "ON", "yes", "on ", "", "off"])
def test_only_on_switches_a_box_on(auth_client, session, copy, value):
    response = save(auth_client, copy, show_birth_date=value, show_nationality=value)

    assert response.status_code == 200
    cv = row(session, copy)
    assert not cv.show_birth_date and not cv.show_nationality
    personal = stored(session, copy).personal
    assert personal.date_of_birth is None and personal.nationality is None


def test_a_box_sent_with_a_huge_value_stays_off(auth_client, session, copy):
    response = save(auth_client, copy, show_nationality="on" * 50_000)

    assert response.status_code == 200
    assert not row(session, copy).show_nationality
    assert stored(session, copy).personal.nationality is None


def test_unknown_box_names_change_nothing(auth_client, session, copy):
    # Only the three boxes are set on the CV; other names must not reach setattr.
    response = save(auth_client, copy, show_email="on", user_id="999", is_locked="on")

    assert response.status_code == 200
    cv = row(session, copy)
    assert cv.status == "draft" and cv.locked_at is None
    assert cv.user_id != 999
