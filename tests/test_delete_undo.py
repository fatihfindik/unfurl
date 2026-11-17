"""Undo for deletes (SPEC §6.0/2, §12 M4c acceptance; D-086): a deleted profile row and
a deleted CV come back with the same content; once; only for the owner; only briefly."""

import datetime as dt
import json

import pytest

from app.models import Application, CVDocument, DeletedItem, Experience
from app.models.base import utcnow
from app.schemas.cv import CVContent
from app.services import application_service as apps
from app.services import document_service as docs
from app.services import profile_service, trash_service, undo_service
from app.services.auth_service import create_user
from tests.helpers import csrf_token


def headers(client):
    return {"HX-Request": "true", "X-CSRF-Token": csrf_token(client)}


def undo_delete(client, token):
    return client.post("/undo/delete", data={"token": token}, headers=headers(client))


def flash_of(client) -> str:
    """The toast the next full page shows (the session flash)."""
    html = client.get("/documents").text
    marker = 'id="page-flash" hidden data-message="'
    return html.split(marker, 1)[1].split('"', 1)[0] if marker in html else ""


@pytest.fixture
def experience(session, user) -> Experience:
    profile = profile_service.get_or_create_profile(session, user)
    return profile_service.add_item(
        session,
        profile,
        Experience,
        {
            "company": "PlanRadar",
            "title": "Senior PM",
            "location": "Wien",
            "start_date": dt.date(2021, 3, 1),
            "end_date": None,
            "description": "Owned the mobile app.",
            "bullets": ["Shipped 3 releases", "Ran discovery"],
        },
    )


def all_experience(session) -> list[Experience]:
    session.expire_all()
    from sqlmodel import select

    return list(session.exec(select(Experience)))


# --- Profile rows -----------------------------------------------------------------------------


def test_a_deleted_profile_row_comes_back_the_same(auth_client, session, user, experience):
    response = auth_client.delete(
        f"/profile/experience/{experience.id}", headers=headers(auth_client)
    )

    trigger = json.loads(response.headers["HX-Trigger"])
    assert trigger["toast"]["message"] == "Experience deleted."
    assert trigger["toast"]["undo_url"] == "/undo/delete" and trigger["profile-changed"]
    assert all_experience(session) == []

    result = undo_delete(auth_client, trigger["toast"]["undo_token"])

    assert result.headers["HX-Refresh"] == "true"
    (row,) = all_experience(session)
    assert (row.company, row.title, row.location) == ("PlanRadar", "Senior PM", "Wien")
    assert (row.start_date, row.end_date, row.bullets) == (
        dt.date(2021, 3, 1),
        None,
        ["Shipped 3 releases", "Ran discovery"],
    )
    assert row.sort_order == experience.sort_order
    assert flash_of(auth_client) == "Undone."


def test_a_delete_token_works_once(auth_client, session, user, experience):
    response = auth_client.delete(
        f"/profile/experience/{experience.id}", headers=headers(auth_client)
    )
    token = json.loads(response.headers["HX-Trigger"])["toast"]["undo_token"]

    undo_delete(auth_client, token)
    undo_delete(auth_client, token)

    assert len(all_experience(session)) == 1
    assert flash_of(auth_client) == "Nothing to undo."


def test_a_full_section_does_not_take_the_row_back(
    auth_client, session, user, experience, monkeypatch
):
    response = auth_client.delete(
        f"/profile/experience/{experience.id}", headers=headers(auth_client)
    )
    token = json.loads(response.headers["HX-Trigger"])["toast"]["undo_token"]
    monkeypatch.setattr(profile_service, "MAX_ITEMS_PER_SECTION", 0)

    undo_delete(auth_client, token)

    assert all_experience(session) == []
    assert flash_of(auth_client) == "Nothing to undo."


# --- CVs -------------------------------------------------------------------------------------


@pytest.fixture
def master(session, user) -> CVDocument:
    profile_service.get_or_create_profile(session, user)
    return docs.create_cv(session, user, "Master CV", "en", "classic")


def delete_cv(client, cv):
    return client.delete(f"/cvs/{cv.id}", headers=headers(client))


def token_from_flash(client) -> str:
    html = client.get("/documents").text
    return html.split('data-undo-token="', 1)[1].split('"', 1)[0]


def test_a_deleted_cv_comes_back_with_the_same_content(auth_client, session, user, master):
    copy = docs.create_cv(session, user, "For Acme", "en", "modern")
    content = CVContent.model_validate(copy.content)
    content.summary = "My own summary."
    docs.update_content(session, copy, title="For Acme", content=content, user=user)

    response = delete_cv(auth_client, copy)
    assert response.headers["HX-Redirect"] == "/documents"
    page = auth_client.get("/documents").text
    assert 'data-message="&#34;For Acme&#34; deleted."' in page  # escaped quotes
    assert 'data-undo-url="/undo/delete"' in page
    token = page.split('data-undo-token="', 1)[1].split('"', 1)[0]

    undo_delete(auth_client, token)

    session.expire_all()
    (back,) = [cv for cv in docs.list_cvs(session, user) if not cv.is_master]
    assert (back.title, back.template, back.parent_id, back.version) == (
        "For Acme",
        "modern",
        master.id,
        copy.version,
    )
    assert back.content["summary"] == "My own summary."


def test_a_cv_used_by_a_saved_job_is_used_again(auth_client, session, user, master):
    job = apps.create(session, user, company="Kranich", job_title="PM")
    cv = docs.copy_for_application(session, user, master, job, "Kranich · CV")
    apps.link_cv(session, user, job, cv)

    delete_cv(auth_client, cv)
    session.expire_all()
    assert session.get(Application, job.id).cv_id is None
    undo_delete(auth_client, token_from_flash(auth_client))

    session.expire_all()
    job = session.get(Application, job.id)
    assert job.cv_id is not None and session.get(CVDocument, job.cv_id).title == "Kranich · CV"


def test_a_taken_version_number_gets_the_next_one(auth_client, session, user, master):
    copy = docs.create_cv(session, user, "v1", "en", "classic")  # version 1
    delete_cv(auth_client, copy)
    token = token_from_flash(auth_client)
    # SQLite reuses the deleted row's id; forget the old object so this session agrees.
    session.expunge(copy)
    docs.create_cv(session, user, "new v1", "en", "classic")  # takes version 1 again

    undo_delete(auth_client, token)

    session.expire_all()
    versions = sorted(cv.version for cv in docs.list_cvs(session, user) if not cv.is_master)
    assert versions == [1, 2]


def test_a_master_does_not_come_back_over_a_new_master(auth_client, session, user, master):
    delete_cv(auth_client, master)
    token = token_from_flash(auth_client)
    # SQLite reuses the deleted row's id; forget the old object so this session agrees.
    session.expunge(master)
    docs.create_cv(session, user, "New master", "en", "classic")

    undo_delete(auth_client, token)

    session.expire_all()
    assert [cv.title for cv in docs.list_cvs(session, user)] == ["New master"]
    assert flash_of(auth_client) == "Nothing to undo."


def test_a_copy_does_not_come_back_without_its_master(auth_client, session, user, master):
    copy = docs.create_cv(session, user, "Copy", "en", "classic")
    delete_cv(auth_client, copy)
    token = token_from_flash(auth_client)
    delete_cv(auth_client, master)

    undo_delete(auth_client, token)

    session.expire_all()
    assert docs.list_cvs(session, user) == []


# --- Only briefly, only the owner -----------------------------------------------------------------


def test_copies_are_purged_after_the_undo_time(session, user, experience):
    copy_id = profile_service.delete_item(session, experience, user, "experience")
    item = session.get(DeletedItem, copy_id)
    item.created_at = utcnow() - trash_service.KEEP - dt.timedelta(seconds=1)
    session.add(item)
    session.commit()

    assert trash_service.take(session, user, copy_id) is None
    session.commit()
    assert session.get(DeletedItem, copy_id) is None  # purged


def test_another_users_token_or_copy_does_nothing(client, auth_client, session, user, experience):
    response = auth_client.delete(
        f"/profile/experience/{experience.id}", headers=headers(auth_client)
    )
    token = json.loads(response.headers["HX-Trigger"])["toast"]["undo_token"]
    copy_id = undo_service.read_delete_token(user, token)
    other = create_user(session, email="other@example.com", password="y" * 12, full_name="O")

    assert undo_service.read_delete_token(other, token) is None
    assert trash_service.take(session, other, copy_id) is None
    forged = undo_service.make_delete_token(other, copy_id)  # signed, but for their account
    auth_client.post("/logout", data={"csrf_token": csrf_token(auth_client)})
    client.post(
        "/login",
        data={"email": other.email, "password": "y" * 12, "csrf_token": csrf_token(client)},
    )
    undo_delete(client, forged)

    assert all_experience(session) == []


def test_a_change_token_is_not_a_delete_token(user):
    token = undo_service.make_change_token(user, 1, [1], 1, {"status": ["saved", "applied"]})

    assert undo_service.read_delete_token(user, token) is None


@pytest.mark.parametrize("token", ["", "x", "a" * 5000])
def test_broken_tokens_change_nothing(auth_client, token):
    response = undo_delete(auth_client, token)

    assert response.status_code == 204
    assert flash_of(auth_client) == "Nothing to undo."


def test_undo_needs_a_login_and_the_csrf_token(client, auth_client):
    assert auth_client.post("/undo/delete", data={"token": "x"}).status_code == 403
    auth_client.post("/logout", data={"csrf_token": csrf_token(auth_client)})
    assert undo_delete(client, "x").headers["HX-Redirect"].startswith("/login")
