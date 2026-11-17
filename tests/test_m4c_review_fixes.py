"""Fixes from the M4c security review (F1, E1, E2, E5)."""

import datetime as dt

import pytest

from app.models import Application, CVDocument, DeletedItem, Skill
from app.models.base import utcnow
from app.services import application_service as apps
from app.services import document_service as docs
from app.services import profile_service, trash_service
from app.services.profile_service import get_or_create_profile
from tests.helpers import csrf_token


def headers(client):
    return {"HX-Request": "true", "X-CSRF-Token": csrf_token(client)}


@pytest.fixture(autouse=True)
def purge_always(monkeypatch):
    # Each test starts as if the last purge was long ago.
    monkeypatch.setattr(trash_service, "_last_purge", None)


@pytest.fixture
def master(session, user) -> CVDocument:
    get_or_create_profile(session, user)
    return docs.create_cv(session, user, "Master CV", "en", "classic")


# F1: deleted personal data goes away even if nobody deletes anything else -------------------


def test_any_logged_in_request_purges_expired_copies(auth_client, session, user):
    profile = get_or_create_profile(session, user)
    item = profile_service.add_item(session, profile, Skill, {"name": "SQL"})
    copy_id = profile_service.delete_item(session, item, user, "skills")
    copy = session.get(DeletedItem, copy_id)
    copy.created_at = utcnow() - trash_service.KEEP - dt.timedelta(seconds=1)
    session.add(copy)
    session.commit()

    auth_client.get("/")  # an unrelated page

    session.expire_all()
    assert session.get(DeletedItem, copy_id) is None


def test_a_failed_undo_still_purges(auth_client, session, user):
    profile = get_or_create_profile(session, user)
    old = profile_service.delete_item(
        session, profile_service.add_item(session, profile, Skill, {"name": "Old"}), user, "skills"
    )
    copy = session.get(DeletedItem, old)
    copy.created_at = utcnow() - trash_service.KEEP - dt.timedelta(seconds=1)
    session.add(copy)
    session.commit()

    auth_client.post("/undo/delete", data={"token": "broken"}, headers=headers(auth_client))

    session.expire_all()
    assert session.get(DeletedItem, old) is None


# E1: a new version of a sent CV doesn't become the job's CV ---------------------------------


def test_a_new_version_of_a_sent_cv_does_not_replace_it(auth_client, session, user, master):
    job = apps.create(session, user, company="Kranich", job_title="PM")
    cv = docs.copy_for_application(session, user, master, job, "Kranich · CV")
    apps.link_cv(session, user, job, cv)
    apps.move(session, user, job, "applied")

    auth_client.post(f"/cvs/{cv.id}/versions", headers=headers(auth_client))

    session.expire_all()
    assert session.get(Application, job.id).cv_id == cv.id  # still the one that was sent


# E2: Undo of "use an existing copy" frees the copy again ------------------------------------


def test_undo_of_linking_frees_the_copy(auth_client, session, user, master):
    import json

    job = apps.create(session, user, company="Kranich", job_title="PM")
    other_job = apps.create(session, user, company="Other", job_title="PM")
    spare = docs.create_cv(session, user, "Spare", "en", "classic")

    response = auth_client.post(
        f"/applications/{job.id}/cv", data={"cv": spare.id}, headers=headers(auth_client)
    )
    token = json.loads(response.headers["HX-Trigger"])["toast"]["undo_token"]
    auth_client.post("/applications/undo", data={"token": token}, headers=headers(auth_client))

    session.expire_all()
    assert session.get(CVDocument, spare.id).application_id is None
    linked = auth_client.post(
        f"/applications/{other_job.id}/cv", data={"cv": spare.id}, headers=headers(auth_client)
    )
    assert linked.status_code == 204  # usable for another job again


# E5: deleting CVs is rate limited --------------------------------------------------------------


def test_cv_delete_is_rate_limited(auth_client, session, user, master):
    codes = [
        auth_client.delete("/cvs/999999", headers=headers(auth_client)).status_code
        for _ in range(61)
    ]

    assert codes[-1] == 429 and codes[0] == 404
