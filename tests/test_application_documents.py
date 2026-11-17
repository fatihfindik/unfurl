"""CVs in applications (SPEC §5.3, §5.4b, §12 M4c; D-080, D-082, D-083)."""

import json

import pytest

from app.models import Application, ApplicationDocument, CVDocument
from app.schemas.cv import CVContent
from app.services import application_service as apps
from app.services import document_service as docs
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile
from tests.helpers import csrf_token


def headers(client):
    return {"HX-Request": "true", "X-CSRF-Token": csrf_token(client)}


@pytest.fixture
def master(session, user) -> CVDocument:
    get_or_create_profile(session, user)
    return docs.create_cv(session, user, "Master CV", "en", "classic")


@pytest.fixture
def job(session, user) -> Application:
    return apps.create(session, user, company="Kranich Health", job_title="Senior PM")


def use_master(client, job, master):
    return client.post(
        f"/applications/{job.id}/cv", data={"master": master.id}, headers=headers(client)
    )


def use_cv(client, job, cv):
    return client.post(f"/applications/{job.id}/cv", data={"cv": cv.id}, headers=headers(client))


def undo(client, response):
    token = json.loads(response.headers["HX-Trigger"])["toast"]["undo_token"]
    return client.post("/applications/undo", data={"token": token}, headers=headers(client))


def toast(response) -> str:
    return json.loads(response.headers["HX-Trigger"])["toast"]["message"]


def fresh(session, row):
    session.expire_all()
    return session.get(type(row), row.id)


def move(client, job, status):
    return client.patch(
        f"/applications/{job.id}/move", data={"status": status}, headers=headers(client)
    )


# --- Linking (D-080) -----------------------------------------------------------------------


def test_copying_the_master_makes_a_linked_draft(auth_client, session, user, master, job):
    response = use_master(auth_client, job, master)

    assert toast(response) == "Kranich Health · CV is used for Kranich Health."
    job = fresh(session, job)
    cv = session.get(CVDocument, job.cv_id)
    assert (cv.title, cv.application_id, cv.parent_id, cv.status) == (
        "Kranich Health · CV",
        job.id,
        master.id,
        "draft",
    )
    assert fresh(session, master).application_id is None  # the master stays untouched
    assert apps.events_of(session, job)[-1].type == "document_linked"


def test_undo_of_copying_deletes_the_untouched_copy(auth_client, session, user, master, job):
    response = use_master(auth_client, job, master)
    cv_id = fresh(session, job).cv_id

    undo(auth_client, response)

    assert fresh(session, job).cv_id is None
    assert session.get(CVDocument, cv_id) is None
    assert apps.events_of(session, job)[-1].type == "created"


def test_undo_of_copying_keeps_an_edited_copy(auth_client, session, user, master, job):
    response = use_master(auth_client, job, master)
    cv = session.get(CVDocument, fresh(session, job).cv_id)
    docs.update_content(
        session, cv, title="Edited", content=CVContent.model_validate(cv.content), user=user
    )

    undo(auth_client, response)

    # The link is taken back, but the edited copy stays (D-080).
    assert session.get(CVDocument, cv.id) is not None
    assert fresh(session, job).cv_id is None


def test_an_existing_copy_can_be_used(auth_client, session, user, master, job):
    copy = docs.create_cv(session, user, "Spare copy", "en", "classic")

    use_cv(auth_client, job, copy)

    assert fresh(session, job).cv_id == copy.id
    assert fresh(session, copy).application_id == job.id


def test_a_master_or_a_locked_copy_cannot_be_used(auth_client, session, user, master, job):
    copy = docs.create_cv(session, user, "Sent elsewhere", "en", "classic")
    docs.lock(session, copy)

    assert use_cv(auth_client, job, master).status_code == 409
    assert use_cv(auth_client, job, copy).status_code == 409
    assert fresh(session, job).cv_id is None


def test_a_copy_of_another_application_cannot_be_used(auth_client, session, user, master, job):
    other_job = apps.create(session, user, company="Other", job_title="PM")
    use_master(auth_client, other_job, master)
    theirs = fresh(session, other_job).cv_id

    response = auth_client.post(
        f"/applications/{job.id}/cv", data={"cv": theirs}, headers=headers(auth_client)
    )

    assert response.status_code == 409
    assert fresh(session, job).cv_id is None


def test_another_users_cv_or_application_is_not_found(auth_client, session, user, master, job):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    get_or_create_profile(session, other)
    their_master = docs.create_cv(session, other, "Theirs", "en", "classic")
    their_copy = docs.create_cv(session, other, "Their copy", "en", "classic")
    their_job = apps.create(session, other, company="Theirs", job_title="PM")

    assert use_master(auth_client, job, their_master).status_code == 404
    assert use_cv(auth_client, job, their_copy).status_code == 404
    assert use_master(auth_client, their_job, master).status_code == 404
    assert fresh(session, job).cv_id is None and fresh(session, their_job).cv_id is None


def test_removing_and_its_undo(auth_client, session, user, master, job):
    use_master(auth_client, job, master)
    cv_id = fresh(session, job).cv_id

    response = auth_client.delete(f"/applications/{job.id}/cv", headers=headers(auth_client))
    assert toast(response) == "CV removed." and fresh(session, job).cv_id is None
    undo(auth_client, response)

    assert fresh(session, job).cv_id == cv_id


def test_a_long_company_name_still_fits_the_title(auth_client, session, user, master):
    job = apps.create(session, user, company="K" * 200, job_title="PM")

    use_master(auth_client, job, master)

    cv = session.get(CVDocument, fresh(session, job).cv_id)
    assert len(cv.title) == 200 and cv.title.endswith(" · CV")


def test_new_versions_stay_with_the_application_but_duplicate_does_not(session, user, master, job):
    cv = docs.copy_for_application(session, user, master, job, "Kranich Health · CV")
    docs.update_content(
        session, cv, title="Edited", content=CVContent.model_validate(cv.content), user=user
    )

    version = docs.save_as_new_version(session, user, cv)
    duplicate = docs.duplicate(session, user, cv)

    assert version.application_id == job.id
    assert duplicate.application_id is None


# --- Locking on send (D-082, the acceptance criterion) -------------------------------------


@pytest.mark.parametrize("status", ["applied", "interview", "offer"])
def test_sending_locks_the_cv_and_keeps_a_copy(auth_client, session, user, master, job, status):
    use_master(auth_client, job, master)
    cv = session.get(CVDocument, fresh(session, job).cv_id)

    move(auth_client, job, status)

    cv = fresh(session, cv)
    assert cv.status == "locked" and cv.locked_at is not None
    (sent,) = apps.sent_documents(session, user, fresh(session, job))
    assert (sent.doc_type, sent.document_id, sent.content_snapshot) == ("cv", cv.id, cv.content)
    assert "locked: this is what you sent" in apps.events_of(session, job)[-1].message


def test_undo_unlocks_and_removes_the_sent_copy(auth_client, session, user, master, job):
    use_master(auth_client, job, master)
    cv_id = fresh(session, job).cv_id

    response = move(auth_client, job, "applied")
    undo(auth_client, response)

    assert session.get(CVDocument, cv_id).status == "draft"
    assert apps.sent_documents(session, user, fresh(session, job)) == []
    assert fresh(session, job).status == "saved"
    assert [e.type for e in apps.events_of(session, job)] == ["created", "document_linked"]


def test_rejected_does_not_lock(auth_client, session, user, master, job):
    use_master(auth_client, job, master)

    move(auth_client, job, "rejected")

    assert session.get(CVDocument, fresh(session, job).cv_id).status == "draft"


def test_a_cv_is_locked_and_recorded_once(auth_client, session, user, master, job):
    use_master(auth_client, job, master)
    move(auth_client, job, "applied")

    move(auth_client, job, "interview")

    assert len(apps.sent_documents(session, user, fresh(session, job))) == 1


def test_linking_to_a_sent_job_waits_for_lock_as_sent(auth_client, session, user, master, job):
    move(auth_client, job, "applied")
    use_master(auth_client, job, master)
    cv_id = fresh(session, job).cv_id
    assert session.get(CVDocument, cv_id).status == "draft"
    assert "Lock as sent" in auth_client.get(f"/applications/{job.id}").text

    response = auth_client.post(f"/applications/{job.id}/cv/send", headers=headers(auth_client))

    assert toast(response) == "CV locked: this is what you sent."
    assert session.get(CVDocument, cv_id).status == "locked"
    undo(auth_client, response)
    assert session.get(CVDocument, cv_id).status == "draft"


def test_undo_never_unlocks_a_cv_locked_by_something_else(auth_client, session, user, master, job):
    use_master(auth_client, job, master)
    cv = session.get(CVDocument, fresh(session, job).cv_id)
    response = move(auth_client, job, "applied")
    # The lock changed (as if locked again later): Undo must leave it locked.
    cv = fresh(session, cv)
    cv.locked_at = cv.locked_at.replace(year=2025)
    session.add(cv)
    session.commit()

    undo(auth_client, response)

    assert fresh(session, cv).status == "locked"


def test_a_sent_copy_stays_when_the_cv_is_deleted_later(session, user, master, job):
    cv = docs.copy_for_application(session, user, master, job, "Kranich Health · CV")
    apps.link_cv(session, user, job, cv)
    apps.move(session, user, job, "applied")
    record = apps.sent_documents(session, user, job)[0]

    session.delete(fresh(session, cv))  # (the app refuses this for a locked CV; the FK must cope)
    session.commit()

    record = session.get(ApplicationDocument, record.id)
    assert record.document_id is None and record.content_snapshot


# --- On the page -----------------------------------------------------------------------------


def test_the_documents_card(auth_client, session, user, master, job):
    html = auth_client.get(f"/applications/{job.id}").text
    assert "Copy my master CV (EN)" in html

    use_master(auth_client, job, master)
    html = auth_client.get(f"/applications/{job.id}").text
    assert "Kranich Health · CV · v1" in html and ">Draft<" in html
    assert f'href="/cvs/{fresh(session, job).cv_id}?application={job.id}"' in html

    move(auth_client, job, "applied")
    html = auth_client.get(f"/applications/{job.id}").text
    assert "Sent · locked" in html and "Sent on " in html and ">Remove<" not in html


def test_logged_out_and_without_csrf_nothing_is_linked(
    client, auth_client, session, user, master, job
):
    assert (
        auth_client.post(f"/applications/{job.id}/cv", data={"master": master.id}).status_code
        == 403
    )
    auth_client.post("/logout", data={"csrf_token": csrf_token(auth_client)})
    assert use_master(client, job, master).headers["HX-Redirect"].startswith("/login")
    assert fresh(session, job).cv_id is None
