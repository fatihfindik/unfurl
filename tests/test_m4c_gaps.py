"""Gaps in the M4c tests: logged-out access, odd ids, other users, CSRF, rate limits,
archived jobs and locked CVs on every new route; signed but odd version-2 tokens with a
document part ("x"); delete tokens and DeletedItem rows that point elsewhere; the
session flash (D-080…D-086)."""

import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer
from sqlmodel import select

from app.config import get_settings
from app.main import app
from app.models import Application, ApplicationDocument, CVDocument, DeletedItem, Experience
from app.models.base import utcnow
from app.schemas.cv import CVContent
from app.services import application_service as apps
from app.services import document_service as docs
from app.services import pdf_service, profile_service, trash_service, undo_service
from app.services.auth_service import create_user
from tests.helpers import csrf_token, log_in

OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "y" * 12
BAD_IDS = ["abc", "0", "-1", "1.5", str(2**31), str(2**70), "1e3"]


def htmx(client) -> dict:
    return {"HX-Request": "true", "X-CSRF-Token": csrf_token(client)}


def toast_of(response) -> dict:
    return json.loads(response.headers["HX-Trigger"])["toast"]


def fresh(session, row):
    session.expire_all()
    return session.get(type(row), row.id)


def trash(session) -> list[DeletedItem]:
    session.expire_all()
    return list(session.exec(select(DeletedItem)))


def sent_records(session) -> list[ApplicationDocument]:
    session.expire_all()
    return list(session.exec(select(ApplicationDocument)))


def signed(data) -> str:
    return undo_service._serializer().dumps(data)


def signed_delete(data) -> str:
    """A delete token with any content, signed like the app signs them."""
    secret = get_settings().secret_key.get_secret_value()
    return URLSafeTimedSerializer(secret, salt="undo-delete").dumps(data)


def add_experience(session, user) -> Experience:
    profile = profile_service.get_or_create_profile(session, user)
    data = {"company": "PlanRadar", "title": "PM", "start_date": dt.date(2021, 3, 1)}
    return profile_service.add_item(session, profile, Experience, data)


def page_flash(html: str) -> str:
    marker = 'id="page-flash" hidden data-message="'
    return html.split(marker, 1)[1].split('"', 1)[0] if marker in html else ""


@pytest.fixture
def other(session):
    user = create_user(session, email=OTHER_EMAIL, password=OTHER_PASSWORD, full_name="Other")
    profile_service.get_or_create_profile(session, user)
    return user


@pytest.fixture
def other_client(client, other):
    """A second browser, logged in as `other` (same test database as `client`)."""
    second = TestClient(app)
    assert log_in(second, OTHER_EMAIL, OTHER_PASSWORD).status_code == 303
    return second


@pytest.fixture
def master(session, user) -> CVDocument:
    profile_service.get_or_create_profile(session, user)
    return docs.create_cv(session, user, "Master CV", "en", "classic")


@pytest.fixture
def job(session, user) -> Application:
    return apps.create(session, user, company="Acme", job_title="PM")


@pytest.fixture
def copy(session, user, master) -> CVDocument:
    """A loose draft copy of my master, linked to nothing."""
    return docs.create_cv(session, user, "Spare copy", "en", "classic")


@pytest.fixture
def linked(session, user, master, job) -> CVDocument:
    cv = docs.copy_for_application(session, user, master, job, "Acme · CV")
    apps.link_cv(session, user, job, cv)
    return cv


@pytest.fixture
def their_master(session, other) -> CVDocument:
    return docs.create_cv(session, other, "Their master", "en", "classic")


@pytest.fixture
def their_job(session, other) -> Application:
    return apps.create(session, other, company="Their Secret GmbH", job_title="PM")


@pytest.fixture
def their_linked(session, other, their_master, their_job) -> CVDocument:
    cv = docs.copy_for_application(session, other, their_master, their_job, "Theirs · CV")
    apps.link_cv(session, other, their_job, cv)
    return cv


def state(session, job) -> tuple:
    """What the document routes can change on a job, to check that nothing did."""
    job = fresh(session, job)
    cv = session.get(CVDocument, job.cv_id) if job.cv_id else None
    return (
        job.status,
        job.archived,
        job.cv_id,
        cv.status if cv else None,
        len(apps.events_of(session, job)),
        len(sent_records(session)),
    )


# The three new write routes of an application, with data that would change something.
def doc_routes(job, cv) -> dict:
    return {
        "link": ("POST", f"/applications/{job.id}/cv", {"cv": cv.id}),
        "unlink": ("DELETE", f"/applications/{job.id}/cv", {}),
        "send": ("POST", f"/applications/{job.id}/cv/send", {}),
    }


@pytest.fixture
def ready(session, user, master, job, linked, copy):
    """A sent job whose linked CV is still a draft: every route could act on it."""
    job = fresh(session, job)
    job.status = "applied"
    session.add(job)
    session.commit()
    return job


# --- Logged out --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["link", "unlink", "send"])
def test_logged_out_document_writes_go_to_login(client, session, ready, copy, name):
    method, url, data = doc_routes(ready, copy)[name]
    before = state(session, ready)

    plain = client.request(
        method, url, data=data, headers={"X-CSRF-Token": csrf_token(client)}, follow_redirects=False
    )
    with_htmx = client.request(method, url, data=data, headers=htmx(client))

    assert plain.status_code == 303 and plain.headers["location"] == "/login"
    assert with_htmx.headers["HX-Redirect"].startswith("/login")
    assert "location" not in with_htmx.headers
    assert state(session, ready) == before


def test_logged_out_undo_delete_restores_nothing(auth_client, session, user, master, copy):
    token = undo_service.make_delete_token(user, docs.delete_cv(session, copy, user))
    auth_client.post("/logout", data={"csrf_token": csrf_token(auth_client)})

    plain = auth_client.post(
        "/undo/delete",
        data={"token": token},
        headers={"X-CSRF-Token": csrf_token(auth_client)},
        follow_redirects=False,
    )
    with_htmx = auth_client.post("/undo/delete", data={"token": token}, headers=htmx(auth_client))

    assert plain.status_code == 303 and plain.headers["location"] == "/login"
    assert with_htmx.headers["HX-Redirect"].startswith("/login")
    assert len(trash(session)) == 1
    session.expire_all()
    assert [cv.title for cv in docs.list_cvs(session, user)] == ["Master CV"]


def test_logged_out_deletes_keep_the_cv_and_the_profile_row(client, session, user, copy):
    row = add_experience(session, user)

    for url in (f"/cvs/{copy.id}", f"/profile/experience/{row.id}"):
        plain = client.delete(
            url, headers={"X-CSRF-Token": csrf_token(client)}, follow_redirects=False
        )
        with_htmx = client.delete(url, headers=htmx(client))
        assert plain.status_code == 303 and plain.headers["location"].startswith("/login")
        assert with_htmx.headers["HX-Redirect"].startswith("/login")

    assert fresh(session, copy) is not None and fresh(session, row) is not None
    assert trash(session) == []


@pytest.mark.parametrize("path", ["/documents", "/cvs/{id}", "/cvs/{id}/pdf"])
def test_logged_out_document_pages_go_to_login(client, linked, path):
    url = path.format(id=linked.id)

    response = client.get(url, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/login?next={url}"
    assert "Acme" not in response.text


def test_an_ended_session_cannot_link(auth_client, session, user, job, copy):
    headers = htmx(auth_client)
    user.session_version += 1  # e.g. after a password change
    session.add(user)
    session.commit()

    response = auth_client.post(f"/applications/{job.id}/cv", data={"cv": copy.id}, headers=headers)

    assert response.headers["HX-Redirect"].startswith("/login")
    assert fresh(session, job).cv_id is None


# --- Odd ids -----------------------------------------------------------------------------


@pytest.mark.parametrize("bad_id", BAD_IDS)
@pytest.mark.parametrize("name", ["link", "unlink", "send"])
def test_bad_application_ids_are_not_found(auth_client, session, ready, copy, name, bad_id):
    method, _, data = doc_routes(ready, copy)[name]
    url = {"link": "/applications/{}/cv", "unlink": "/applications/{}/cv"}.get(
        name, "/applications/{}/cv/send"
    )
    before = state(session, ready)

    response = auth_client.request(method, url.format(bad_id), data=data, headers=htmx(auth_client))

    assert response.status_code == 404
    assert state(session, ready) == before


@pytest.mark.parametrize("bad_id", [*BAD_IDS, ""])
@pytest.mark.parametrize("field", ["master", "cv"])
def test_bad_cv_ids_in_the_form_are_not_found(auth_client, session, job, master, field, bad_id):
    response = auth_client.post(
        f"/applications/{job.id}/cv", data={field: bad_id}, headers=htmx(auth_client)
    )

    # An empty "master" falls through to "cv", which is missing: also not found.
    assert response.status_code == 404
    assert state(session, job) == ("saved", False, None, None, 1, 0)
    session.expire_all()
    assert len(list(session.exec(select(CVDocument)))) == 1  # no copy made


def test_a_form_with_neither_master_nor_cv_is_not_found(auth_client, session, job, master):
    response = auth_client.post(f"/applications/{job.id}/cv", headers=htmx(auth_client))

    assert response.status_code == 404
    assert fresh(session, job).cv_id is None


def test_a_copy_given_as_master_is_refused(auth_client, session, user, job, copy):
    response = auth_client.post(
        f"/applications/{job.id}/cv", data={"master": copy.id}, headers=htmx(auth_client)
    )

    assert response.status_code == 422
    assert fresh(session, job).cv_id is None
    assert len(docs.list_cvs(session, user)) == 2  # no new copy


@pytest.mark.parametrize("bad_id", BAD_IDS)
def test_bad_ids_on_cv_delete_and_pdf_are_not_found(auth_client, session, master, bad_id):
    assert auth_client.delete(f"/cvs/{bad_id}", headers=htmx(auth_client)).status_code == 404
    assert auth_client.get(f"/cvs/{bad_id}/pdf").status_code == 404
    assert trash(session) == []


# --- Another user's application, CV or deleted row -------------------------------------


@pytest.mark.parametrize("name", ["link", "unlink", "send"])
def test_another_users_application_is_not_found(
    auth_client, session, other, their_job, their_linked, copy, name
):
    their_job = fresh(session, their_job)
    their_job.status = "applied"  # so "send" would lock their draft
    session.add(their_job)
    session.commit()
    method, url, data = doc_routes(their_job, copy)[name]
    before = state(session, their_job)

    response = auth_client.request(method, url, data=data, headers=htmx(auth_client))

    assert response.status_code == 404  # not 403: existence is not leaked
    assert "Their" not in response.text
    assert state(session, their_job) == before
    assert fresh(session, copy).application_id is None


def test_their_linked_copy_cannot_be_used_for_my_job(auth_client, session, job, their_linked):
    response = auth_client.post(
        f"/applications/{job.id}/cv", data={"cv": their_linked.id}, headers=htmx(auth_client)
    )

    assert response.status_code == 404
    assert fresh(session, job).cv_id is None
    assert fresh(session, their_linked).application_id is not None


def test_a_master_of_mine_cannot_be_copied_into_their_job(
    auth_client, session, user, master, their_job
):
    response = auth_client.post(
        f"/applications/{their_job.id}/cv", data={"master": master.id}, headers=htmx(auth_client)
    )

    assert response.status_code == 404
    assert fresh(session, their_job).cv_id is None
    assert len(docs.list_cvs(session, user)) == 1  # no copy made before the 404


@pytest.mark.parametrize("method, path", [("GET", ""), ("GET", "/pdf"), ("DELETE", "")])
def test_another_users_cv_is_not_found(
    auth_client, session, their_linked, monkeypatch, method, path
):
    monkeypatch.setattr(pdf_service, "render_cv_pdf", lambda cv: b"%PDF-1.7")

    response = auth_client.request(
        method, f"/cvs/{their_linked.id}{path}", headers=htmx(auth_client)
    )

    assert response.status_code == 404
    assert "Theirs" not in response.text and "Their Secret" not in response.text
    assert fresh(session, their_linked) is not None and trash(session) == []


def test_another_users_profile_row_cannot_be_deleted(auth_client, session, other):
    row = add_experience(session, other)

    response = auth_client.delete(f"/profile/experience/{row.id}", headers=htmx(auth_client))

    assert response.status_code == 404
    assert fresh(session, row) is not None and trash(session) == []


def test_a_delete_token_for_their_deleted_row_does_nothing(
    auth_client, other_client, session, user, other, their_master
):
    copy = docs.create_cv(session, other, "Their copy", "en", "classic")
    item_id = docs.delete_cv(session, copy, other)
    # Signed correctly and for my account, but the row in the trash is theirs.
    forged = undo_service.make_delete_token(user, item_id)

    response = auth_client.post("/undo/delete", data={"token": forged}, headers=htmx(auth_client))

    assert response.status_code == 204
    assert page_flash(auth_client.get("/documents").text) == "Nothing to undo."
    assert [i.id for i in trash(session)] == [item_id]  # still there for them
    session.expire_all()
    assert [cv.title for cv in docs.list_cvs(session, user)] == []
    # They can still take it back themselves.
    their_token = undo_service.make_delete_token(other, item_id)
    other_client.post("/undo/delete", data={"token": their_token}, headers=htmx(other_client))
    session.expire_all()
    assert "Their copy" in [cv.title for cv in docs.list_cvs(session, other)]


def test_a_profile_row_never_comes_back_into_their_profile(auth_client, session, user, other):
    # A DeletedItem of mine whose payload names their profile (never made by the app).
    their_profile = profile_service.get_or_create_profile(session, other)
    item = DeletedItem(
        user_id=user.id,
        kind="experience",
        payload={"profile_id": their_profile.id, "company": "Injected", "title": "PM"},
    )
    session.add(item)
    session.commit()
    token = undo_service.make_delete_token(user, item.id)

    auth_client.post("/undo/delete", data={"token": token}, headers=htmx(auth_client))

    session.expire_all()
    assert list(session.exec(select(Experience))) == []
    assert page_flash(auth_client.get("/documents").text) == "Nothing to undo."


def test_a_cv_payload_of_another_user_never_comes_back(auth_client, session, user, other):
    item = DeletedItem(
        user_id=user.id,
        kind="cv",
        payload={"user_id": other.id, "title": "Injected", "language": "en", "is_master": True},
    )
    session.add(item)
    session.commit()
    token = undo_service.make_delete_token(user, item.id)

    auth_client.post("/undo/delete", data={"token": token}, headers=htmx(auth_client))

    session.expire_all()
    assert list(session.exec(select(CVDocument))) == []


def test_a_restored_cv_is_never_put_into_their_job(
    auth_client, session, user, master, other, their_job
):
    # Payload names their application, as the lineage and as the job that used it.
    payload = trash_service.payload_of(docs.create_cv(session, user, "Mine", "en", "classic"))
    payload |= {"application_id": their_job.id, "_used_by": their_job.id, "version": 9}
    item = DeletedItem(user_id=user.id, kind="cv", payload=payload)
    session.add(item)
    session.commit()
    token = undo_service.make_delete_token(user, item.id)

    auth_client.post("/undo/delete", data={"token": token}, headers=htmx(auth_client))

    session.expire_all()
    assert fresh(session, their_job).cv_id is None
    restored = session.exec(select(CVDocument).where(CVDocument.version == 9)).first()
    assert restored is not None and restored.application_id is None


@pytest.mark.parametrize("kind", ["user", "application", "", "cvs", "Experience"])
def test_an_unknown_kind_is_never_restored(auth_client, session, user, kind):
    item = DeletedItem(user_id=user.id, kind=kind, payload={"user_id": user.id})
    session.add(item)
    session.commit()
    token = undo_service.make_delete_token(user, item.id)

    response = auth_client.post("/undo/delete", data={"token": token}, headers=htmx(auth_client))

    assert response.status_code == 204
    assert page_flash(auth_client.get("/documents").text) == "Nothing to undo."


def test_a_payload_of_the_wrong_section_says_nothing_to_undo(auth_client, session, user):
    row = add_experience(session, user)
    item_id = profile_service.delete_item(session, row, user, "experience")
    item = session.get(DeletedItem, item_id)
    item.kind = "skills"  # an experience payload restored as a skill
    session.add(item)
    session.commit()

    response = auth_client.post(
        "/undo/delete",
        data={"token": undo_service.make_delete_token(user, item_id)},
        headers=htmx(auth_client),
    )

    assert response.status_code == 204
    assert page_flash(auth_client.get("/documents").text) == "Nothing to undo."


# --- Odd delete tokens --------------------------------------------------------------------


def test_a_delete_token_after_its_row_was_purged(auth_client, session, user, master, copy):
    headers = htmx(auth_client)
    first_id = docs.delete_cv(session, copy, user)
    token = undo_service.make_delete_token(user, first_id)
    # The second copy is deleted before the first is purged: SQLite would otherwise give
    # it the freed id. (In real use the token expires before its row is purged.)
    second = docs.create_cv(session, user, "Second", "en", "classic")
    second_id = docs.delete_cv(session, second, user)
    item = session.get(DeletedItem, first_id)
    item.created_at = utcnow() - trash_service.KEEP - dt.timedelta(seconds=1)
    session.add(item)
    session.commit()

    auth_client.post("/undo/delete", data={"token": token}, headers=headers)

    assert [i.id for i in trash(session)] == [second_id]  # purged; the newer one stays
    assert [cv.title for cv in docs.list_cvs(session, user)] == ["Master CV"]
    assert page_flash(auth_client.get("/documents").text) == "Nothing to undo."


@pytest.mark.parametrize("item_id", ["1", 1.0, True, None, [1], {"d": 1}, -1, 0])
def test_a_delete_token_with_an_odd_id_does_nothing(auth_client, session, user, master, item_id):
    docs.delete_cv(session, master, user)
    token = signed_delete({"u": user.id, "d": item_id})

    response = auth_client.post("/undo/delete", data={"token": token}, headers=htmx(auth_client))

    assert response.status_code == 204
    assert docs.list_cvs(session, user) == []
    assert len(trash(session)) == 1


# Found by test-writer (M4c), fixed in the same branch.
def test_a_delete_token_with_a_huge_id_says_nothing_to_undo(auth_client, session, user):
    token = signed_delete({"u": user.id, "d": 2**70})

    response = auth_client.post("/undo/delete", data={"token": token}, headers=htmx(auth_client))

    assert response.status_code == 204


@pytest.mark.parametrize("data", [{"d": 1}, {"u": "1", "d": 1}, [1, 1], "1"])
def test_a_delete_token_without_my_user_does_nothing(auth_client, session, user, master, data):
    item_id = docs.delete_cv(session, master, user)
    data = data | {"d": item_id} if isinstance(data, dict) else data

    auth_client.post("/undo/delete", data={"token": signed_delete(data)}, headers=htmx(auth_client))

    assert docs.list_cvs(session, user) == []


def test_a_change_token_cannot_undo_a_delete_and_back(auth_client, session, user, master, copy):
    item_id = docs.delete_cv(session, copy, user)
    delete_token = undo_service.make_delete_token(user, item_id)
    change_token = signed({"u": user.id, "d": item_id})  # undo salt, delete shape

    auth_client.post("/undo/delete", data={"token": change_token}, headers=htmx(auth_client))
    result = auth_client.post(
        "/applications/undo", data={"token": delete_token}, headers=htmx(auth_client)
    )

    assert toast_of(result)["message"] == "Nothing to undo."
    session.expire_all()
    assert [cv.title for cv in docs.list_cvs(session, user)] == ["Master CV"]


# --- CSRF ---------------------------------------------------------------------------------


@pytest.mark.parametrize("token", [None, "", "wrong-token"])
@pytest.mark.parametrize("name", ["link", "unlink", "send"])
def test_document_writes_need_the_csrf_token(auth_client, session, ready, copy, name, token):
    method, url, data = doc_routes(ready, copy)[name]
    before = state(session, ready)
    headers = {"HX-Request": "true"}
    if token is not None:
        headers["X-CSRF-Token"] = token

    response = auth_client.request(method, url, data=data, headers=headers)

    assert response.status_code == 403
    assert state(session, ready) == before


@pytest.mark.parametrize("token", [None, "", "wrong-token"])
def test_deletes_and_their_undo_need_the_csrf_token(auth_client, session, user, copy, token):
    row = add_experience(session, user)
    headers = {"HX-Request": "true"}
    if token is not None:
        headers["X-CSRF-Token"] = token

    assert auth_client.delete(f"/cvs/{copy.id}", headers=headers).status_code == 403
    assert auth_client.delete(f"/profile/experience/{row.id}", headers=headers).status_code == 403
    assert fresh(session, copy) is not None and fresh(session, row) is not None
    assert trash(session) == []

    item_id = docs.delete_cv(session, copy, user)
    undo_token = undo_service.make_delete_token(user, item_id)
    response = auth_client.post("/undo/delete", data={"token": undo_token}, headers=headers)
    assert response.status_code == 403
    assert [i.id for i in trash(session)] == [item_id]


def test_the_csrf_token_of_another_session_cannot_link(
    auth_client, other_client, session, job, copy
):
    response = auth_client.post(
        f"/applications/{job.id}/cv",
        data={"cv": copy.id},
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(other_client)},
    )

    assert response.status_code == 403
    assert fresh(session, job).cv_id is None


# --- Archived jobs, locked CVs, CVs of other jobs --------------------------------------------


@pytest.fixture
def archived(session, user, ready) -> Application:
    apps.archive(session, user, ready)
    return fresh(session, ready)


@pytest.mark.parametrize("field", ["master", "cv"])
def test_nothing_is_linked_to_an_archived_job(auth_client, session, user, job, master, copy, field):
    apps.archive(session, user, job)
    value = master.id if field == "master" else copy.id

    response = auth_client.post(
        f"/applications/{job.id}/cv", data={field: value}, headers=htmx(auth_client)
    )

    assert response.status_code == 409
    assert fresh(session, job).cv_id is None
    assert len(docs.list_cvs(session, user)) == 2  # no copy made
    assert fresh(session, copy).application_id is None


def test_an_archived_job_cannot_lock_as_sent(auth_client, session, archived, linked):
    before = state(session, archived)

    response = auth_client.post(f"/applications/{archived.id}/cv/send", headers=htmx(auth_client))

    assert response.status_code == 409
    assert state(session, archived) == before
    assert fresh(session, linked).status == "draft"


# Found by test-writer (M4c), fixed in the same branch.
def test_an_archived_jobs_cv_cannot_be_removed(auth_client, session, archived, linked):
    before = state(session, archived)

    response = auth_client.delete(f"/applications/{archived.id}/cv", headers=htmx(auth_client))

    assert response.status_code == 409
    assert state(session, archived) == before


def test_a_sent_cv_cannot_be_removed_or_deleted(auth_client, session, user, job, linked):
    apps.move(session, user, fresh(session, job), "applied")  # locks it
    before = state(session, job)

    assert (
        auth_client.delete(f"/applications/{job.id}/cv", headers=htmx(auth_client)).status_code
        == 409
    )
    assert auth_client.delete(f"/cvs/{linked.id}", headers=htmx(auth_client)).status_code == 409

    assert state(session, job) == before
    assert fresh(session, linked).status == "locked" and trash(session) == []


def test_a_sent_cv_cannot_be_used_for_another_job(auth_client, session, user, job, linked):
    apps.move(session, user, fresh(session, job), "applied")
    second = apps.create(session, user, company="Second", job_title="PM")

    response = auth_client.post(
        f"/applications/{second.id}/cv", data={"cv": linked.id}, headers=htmx(auth_client)
    )

    assert response.status_code == 409
    assert fresh(session, second).cv_id is None
    assert fresh(session, linked).application_id == job.id


def test_a_cv_in_use_elsewhere_is_refused_by_the_unique_index(
    auth_client, session, user, job, copy
):
    # A loose copy that is somehow another job's current CV (no lineage): the unique
    # cv_id index must still keep it on one job only.
    second = apps.create(session, user, company="Second", job_title="PM")
    second.cv_id = copy.id
    session.add(second)
    session.commit()

    response = auth_client.post(
        f"/applications/{job.id}/cv", data={"cv": copy.id}, headers=htmx(auth_client)
    )

    assert response.status_code == 409
    assert fresh(session, job).cv_id is None
    assert fresh(session, second).cv_id == copy.id
    assert fresh(session, copy).application_id is None  # rolled back
    assert len(apps.events_of(session, fresh(session, job))) == 1


@pytest.mark.parametrize("status", ["saved", "rejected"])
def test_lock_as_sent_needs_a_sent_job(auth_client, session, user, job, linked, status):
    if status != "saved":
        apps.move(session, user, fresh(session, job), status)

    response = auth_client.post(f"/applications/{job.id}/cv/send", headers=htmx(auth_client))

    assert response.status_code == 409
    assert fresh(session, linked).status == "draft" and sent_records(session) == []


def test_lock_as_sent_needs_a_draft_cv(auth_client, session, user, job, linked):
    apps.move(session, user, fresh(session, job), "applied")  # locks it already
    before = state(session, job)

    response = auth_client.post(f"/applications/{job.id}/cv/send", headers=htmx(auth_client))

    assert response.status_code == 409
    assert state(session, job) == before


def test_lock_as_sent_without_a_cv(auth_client, session, user, job):
    apps.move(session, user, job, "applied")

    response = auth_client.post(f"/applications/{job.id}/cv/send", headers=htmx(auth_client))

    assert response.status_code == 409


def test_removing_without_a_cv_is_refused(auth_client, session, job):
    response = auth_client.delete(f"/applications/{job.id}/cv", headers=htmx(auth_client))

    assert response.status_code == 409
    assert state(session, job) == ("saved", False, None, None, 1, 0)


# --- A cv_id or application_id that points at another user (bad data) ---------------------


def test_their_cv_as_my_cv_id_is_never_shown_locked_or_unlinked(
    auth_client, session, user, job, their_linked
):
    their_cv_id = their_linked.id
    their_job_id = their_linked.application_id
    mine = fresh(session, job)
    # Free their cv_id first: the column is unique.
    theirs = session.get(Application, their_job_id)
    theirs.cv_id = None
    session.add(theirs)
    session.commit()
    mine.cv_id = their_cv_id
    session.add(mine)
    session.commit()

    html = auth_client.get(f"/applications/{job.id}").text
    unlink = auth_client.delete(f"/applications/{job.id}/cv", headers=htmx(auth_client))
    auth_client.patch(
        f"/applications/{job.id}/move", data={"status": "applied"}, headers=htmx(auth_client)
    )

    assert "Theirs · CV" not in html
    assert unlink.status_code == 409
    session.expire_all()
    assert session.get(CVDocument, their_cv_id).status == "draft"  # never locked by me
    assert sent_records(session) == []


def test_my_copy_naming_their_job_shows_no_company(
    auth_client, session, user, copy, their_job, their_linked, monkeypatch
):
    copy = fresh(session, copy)
    copy.application_id = their_job.id
    session.add(copy)
    session.commit()
    # Edited, so "Save as new version" really makes one.
    docs.update_content(
        session, copy, title="Edited", content=CVContent.model_validate(copy.content), user=user
    )
    monkeypatch.setattr(pdf_service, "render_cv_pdf", lambda cv: b"%PDF-1.7")

    editor = auth_client.get(f"/cvs/{copy.id}").text
    documents = auth_client.get("/documents").text
    pdf = auth_client.get(f"/cvs/{copy.id}/pdf")
    version = auth_client.post(f"/cvs/{copy.id}/versions", headers=htmx(auth_client))

    assert "Their Secret" not in editor and "Their Secret" not in documents
    assert "Their_Secret" not in pdf.headers["content-disposition"]
    assert "Not linked to an application" in documents
    assert version.headers["HX-Redirect"] != f"/cvs/{copy.id}"  # a new version was made
    assert fresh(session, their_job).cv_id == their_linked.id  # never my new version


# --- Version-2 tokens with a document part ("x"): signed but odd ----------------------------


@pytest.fixture
def sent_token(auth_client, session, user, job, linked) -> dict:
    """Move to Applied (locks the linked CV); the signed data of its undo token."""
    response = auth_client.patch(
        f"/applications/{job.id}/move", data={"status": "applied"}, headers=htmx(auth_client)
    )
    data = undo_service._serializer().loads(toast_of(response)["undo_token"])
    assert set(data["x"]) == {"sent", "locked"}
    return data


def undo(client, data) -> str:
    response = client.post("/applications/undo", data={"token": signed(data)}, headers=htmx(client))
    assert response.status_code == 204
    return toast_of(response)["message"]


def still_sent(session, job, linked) -> bool:
    return (
        fresh(session, job).status == "applied"
        and fresh(session, linked).status == "locked"
        and len([r for r in sent_records(session) if r.application_id == job.id]) == 1
    )


def their_sent_record(session, other, their_job, their_linked) -> ApplicationDocument:
    apps.move(session, other, fresh(session, their_job), "applied")
    return apps.sent_documents(session, other, fresh(session, their_job))[0]


def test_x_sent_naming_their_record_does_nothing(
    auth_client, session, other, job, linked, sent_token, their_job, their_linked
):
    theirs = their_sent_record(session, other, their_job, their_linked)
    x = sent_token["x"] | {"sent": [*sent_token["x"]["sent"], theirs.id]}

    assert undo(auth_client, sent_token | {"x": x}) == "Nothing to undo."
    assert session.get(ApplicationDocument, theirs.id) is not None
    assert still_sent(session, job, linked)  # rolled back, not half undone
    assert fresh(session, their_linked).status == "locked"


def test_x_sent_naming_my_other_jobs_record_does_nothing(
    auth_client, session, user, master, job, linked, sent_token
):
    second = apps.create(session, user, company="Second", job_title="PM")
    cv = docs.copy_for_application(session, user, master, second, "Second · CV")
    apps.link_cv(session, user, second, cv)
    apps.move(session, user, fresh(session, second), "applied")
    record = apps.sent_documents(session, user, fresh(session, second))[0]

    assert undo(auth_client, sent_token | {"x": {"sent": [record.id]}}) == "Nothing to undo."
    assert session.get(ApplicationDocument, record.id) is not None
    assert still_sent(session, job, linked)


def test_x_locked_never_unlocks_their_cv(
    auth_client, session, other, job, linked, sent_token, their_job, their_linked
):
    their_sent_record(session, other, their_job, their_linked)
    theirs = fresh(session, their_linked)
    pair = [theirs.id, theirs.locked_at.replace(tzinfo=dt.UTC).isoformat()]
    x = sent_token["x"] | {"locked": [*sent_token["x"]["locked"], pair]}

    undo(auth_client, sent_token | {"x": x})

    assert fresh(session, their_linked).status == "locked"


def test_x_locked_never_unlocks_my_other_jobs_cv(
    auth_client, session, user, master, job, linked, sent_token
):
    second = apps.create(session, user, company="Second", job_title="PM")
    cv = docs.copy_for_application(session, user, master, second, "Second · CV")
    apps.link_cv(session, user, second, cv)
    apps.move(session, user, fresh(session, second), "applied")
    cv = fresh(session, cv)
    pair = [cv.id, cv.locked_at.replace(tzinfo=dt.UTC).isoformat()]
    x = sent_token["x"] | {"locked": [*sent_token["x"]["locked"], pair]}

    undo(auth_client, sent_token | {"x": x})

    assert fresh(session, cv).status == "locked"
    assert fresh(session, linked).status == "draft"  # the real part was undone


@pytest.mark.parametrize("target", ["their draft", "my other job's draft", "my master"])
def test_x_created_cv_never_deletes_another_cv(
    auth_client, session, user, other, master, job, linked, sent_token, their_master, target
):
    if target == "their draft":
        cv = docs.create_cv(session, other, "Their draft", "en", "classic")
    elif target == "my other job's draft":
        second = apps.create(session, user, company="Second", job_title="PM")
        cv = docs.copy_for_application(session, user, master, second, "Second · CV")
    else:
        cv = master
    x = sent_token["x"] | {"created_cv": cv.id}

    undo(auth_client, sent_token | {"x": x})

    assert fresh(session, cv) is not None


@pytest.mark.parametrize(
    "x",
    [
        {"sent": ["1"]},
        {"sent": [True]},
        {"sent": "abc"},
        {"locked": [[1]]},
        {"locked": [5]},
        {"locked": [["1", "x"]]},
        {"locked": "abc"},
        {"created_cv": "1"},
        {"created_cv": True},
        {"created_cv": None},
        {"unknown": 1},
    ],
)
def test_odd_x_values_never_break_the_undo(auth_client, session, job, linked, sent_token, x):
    message = undo(auth_client, sent_token | {"x": x})

    assert message in ("Undone.", "Nothing to undo.")


@pytest.mark.parametrize("x", ["abc", 5, [1], True])
def test_an_x_that_is_not_an_object_does_nothing(auth_client, session, job, linked, sent_token, x):
    assert undo(auth_client, sent_token | {"x": x}) == "Nothing to undo."
    assert still_sent(session, job, linked)


# Found by test-writer (M4c), fixed in the same branch.
@pytest.mark.parametrize("x", [{"sent": 5}, {"sent": None}, {"locked": 5}, {"locked": None}])
def test_x_lists_of_the_wrong_type_say_nothing_to_undo(
    auth_client, session, job, linked, sent_token, x
):
    assert undo(auth_client, sent_token | {"x": sent_token["x"] | x}) == "Nothing to undo."


# Found by test-writer (M4c), fixed in the same branch.
@pytest.mark.parametrize("key", ["sent", "locked", "created_cv"])
def test_x_ids_that_are_too_big_say_nothing_to_undo(
    auth_client, session, job, linked, sent_token, key
):
    value = {"sent": [2**70], "locked": [[2**70, "x"]], "created_cv": 2**70}[key]

    assert undo(auth_client, sent_token | {"x": sent_token["x"] | {key: value}}) in (
        "Undone.",
        "Nothing to undo.",
    )


def test_a_cv_id_before_value_of_their_cv_does_nothing(
    auth_client, session, user, job, copy, their_linked
):
    # Unlink's token says cv_id went [X, None]; re-signed with their CV as X.
    auth_client.post(f"/applications/{job.id}/cv", data={"cv": copy.id}, headers=htmx(auth_client))
    response = auth_client.delete(f"/applications/{job.id}/cv", headers=htmx(auth_client))
    data = undo_service._serializer().loads(toast_of(response)["undo_token"])
    data["f"]["cv_id"] = [their_linked.id, None]

    assert undo(auth_client, data) == "Nothing to undo."
    assert fresh(session, job).cv_id is None


def test_a_cv_id_before_value_of_my_other_jobs_cv_does_nothing(
    auth_client, session, user, master, job, copy
):
    second = apps.create(session, user, company="Second", job_title="PM")
    cv = docs.copy_for_application(session, user, master, second, "Second · CV")
    apps.link_cv(session, user, second, cv)
    auth_client.post(f"/applications/{job.id}/cv", data={"cv": copy.id}, headers=htmx(auth_client))
    response = auth_client.delete(f"/applications/{job.id}/cv", headers=htmx(auth_client))
    data = undo_service._serializer().loads(toast_of(response)["undo_token"])
    data["f"]["cv_id"] = [cv.id, None]

    assert undo(auth_client, data) == "Nothing to undo."  # the unique index refuses it
    assert fresh(session, job).cv_id is None
    assert fresh(session, second).cv_id == cv.id


def test_undo_of_unlink_after_the_cv_was_deleted(auth_client, session, user, job, copy):
    auth_client.post(f"/applications/{job.id}/cv", data={"cv": copy.id}, headers=htmx(auth_client))
    response = auth_client.delete(f"/applications/{job.id}/cv", headers=htmx(auth_client))
    token = toast_of(response)["undo_token"]
    docs.delete_cv(session, fresh(session, copy), user)

    result = auth_client.post(
        "/applications/undo", data={"token": token}, headers=htmx(auth_client)
    )

    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, job).cv_id is None


def test_lock_as_sent_undo_works_once(auth_client, session, user, job, linked):
    apps.move(session, user, fresh(session, job), "applied")
    # move() locked it; take the lock back, as if the CV was linked after sending.
    for record in sent_records(session):
        session.delete(record)
    cv = fresh(session, linked)
    cv.status, cv.locked_at = "draft", None
    session.add(cv)
    session.commit()
    response = auth_client.post(f"/applications/{job.id}/cv/send", headers=htmx(auth_client))
    token = toast_of(response)["undo_token"]

    first = auth_client.post("/applications/undo", data={"token": token}, headers=htmx(auth_client))
    second = auth_client.post(
        "/applications/undo", data={"token": token}, headers=htmx(auth_client)
    )

    assert toast_of(first)["message"] == "Undone."
    assert toast_of(second)["message"] == "Nothing to undo."
    assert fresh(session, linked).status == "draft" and sent_records(session) == []


# --- The status picker locks too (D-082) ----------------------------------------------------


@pytest.mark.parametrize("status", ["applied", "interview", "offer"])
def test_the_status_picker_locks_and_its_undo_unlocks(auth_client, session, job, linked, status):
    response = auth_client.post(
        f"/applications/{job.id}/status", data={"status": status}, headers=htmx(auth_client)
    )
    assert fresh(session, linked).status == "locked" and len(sent_records(session)) == 1

    result = auth_client.post(
        "/applications/undo",
        data={"token": toast_of(response)["undo_token"]},
        headers=htmx(auth_client),
    )

    assert toast_of(result)["message"] == "Undone."
    assert fresh(session, linked).status == "draft" and sent_records(session) == []
    assert fresh(session, job).status == "saved"


def test_a_status_change_on_another_users_job_locks_nothing(
    auth_client, session, their_job, their_linked
):
    response = auth_client.post(
        f"/applications/{their_job.id}/status",
        data={"status": "applied"},
        headers=htmx(auth_client),
    )

    assert response.status_code == 404
    assert fresh(session, their_linked).status == "draft" and sent_records(session) == []


# --- The flash -----------------------------------------------------------------------------


def test_the_flash_is_shown_once_and_never_to_htmx(auth_client, session, master, copy):
    headers = htmx(auth_client)  # (reading the CSRF token loads a page: do it first)
    auth_client.delete(f"/cvs/{copy.id}", headers=headers)

    piece = auth_client.get("/documents", headers={"HX-Request": "true"})
    first = auth_client.get("/documents").text
    second = auth_client.get("/documents").text

    assert 'id="page-flash"' not in piece.text
    assert page_flash(first) == "&#34;Spare copy&#34; deleted."
    assert 'id="page-flash"' not in second


def test_the_flash_is_not_used_up_by_a_write(auth_client, session, master, copy):
    headers = htmx(auth_client)
    auth_client.delete(f"/cvs/{copy.id}", headers=headers)

    auth_client.post("/cvs", data={"language": "de"}, headers=headers)  # any POST

    assert page_flash(auth_client.get("/documents").text) == "&#34;Spare copy&#34; deleted."


def test_the_flash_escapes_the_title_and_cuts_it(auth_client, session, user, master):
    evil = docs.create_cv(session, user, '"><script>alert(1)</script>' + "x" * 80, "en", "classic")
    headers = htmx(auth_client)
    auth_client.delete(f"/cvs/{evil.id}", headers=headers)

    html = auth_client.get("/documents").text

    assert "<script>alert(1)" not in html
    message = page_flash(html)
    assert message.startswith("&#34;&#34;&gt;&lt;script&gt;") and "…" in message


def test_the_flash_belongs_to_one_browser(auth_client, other_client, session, master, copy):
    headers = htmx(auth_client)
    auth_client.delete(f"/cvs/{copy.id}", headers=headers)

    assert 'id="page-flash"' not in other_client.get("/documents").text
    assert page_flash(auth_client.get("/documents").text) != ""


def test_a_master_with_copies_answers_with_a_toast_not_a_flash(auth_client, session, master, copy):
    headers = htmx(auth_client)

    response = auth_client.delete(f"/cvs/{master.id}", headers=headers)

    assert response.status_code == 204
    assert toast_of(response) == {
        "message": "This master has 1 copy. Delete the copy first.",
        "undo_url": "",
        "undo_token": "",
    }
    assert 'id="page-flash"' not in auth_client.get("/documents").text
    assert fresh(session, master) is not None and trash(session) == []


# --- Rate limits ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["link", "unlink", "send"])
def test_document_writes_are_limited_to_60_a_minute(auth_client, session, ready, copy, name):
    method, url, data = doc_routes(ready, copy)[name]
    headers = htmx(auth_client)

    codes = [
        auth_client.request(method, url, data=data, headers=headers).status_code for _ in range(61)
    ]

    assert 429 not in codes[:60]  # (409 after the first unlink or send is fine)
    assert codes[60] == 429


def test_a_limited_link_changes_nothing(auth_client, session, user, job, copy):
    headers = htmx(auth_client)
    second = docs.create_cv(session, user, "Second copy", "en", "classic")
    for _ in range(60):
        auth_client.post(f"/applications/{job.id}/cv", data={"cv": copy.id}, headers=headers)

    response = auth_client.post(
        f"/applications/{job.id}/cv", data={"cv": second.id}, headers=headers
    )

    assert response.status_code == 429
    assert fresh(session, job).cv_id == copy.id
    assert fresh(session, second).application_id is None


def test_undo_delete_is_limited_to_60_a_minute(auth_client, session, user, master, copy):
    headers = htmx(auth_client)
    for _ in range(60):
        auth_client.post("/undo/delete", data={"token": "x"}, headers=headers)
    token = undo_service.make_delete_token(user, docs.delete_cv(session, copy, user))

    response = auth_client.post("/undo/delete", data={"token": token}, headers=headers)

    assert response.status_code == 429
    assert len(trash(session)) == 1  # still there, nothing restored


# --- Wrong methods -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/applications/{job}/cv"),
        ("PUT", "/applications/{job}/cv"),
        ("PATCH", "/applications/{job}/cv"),
        ("GET", "/applications/{job}/cv/send"),
        ("PUT", "/applications/{job}/cv/send"),
        ("DELETE", "/applications/{job}/cv/send"),
        ("GET", "/undo/delete"),
        ("PUT", "/undo/delete"),
        ("DELETE", "/undo/delete"),
        ("PUT", "/cvs/{cv}"),
        ("PATCH", "/cvs/{cv}"),
        ("POST", "/cvs/{cv}/pdf"),
        ("DELETE", "/documents"),
    ],
)
def test_wrong_methods_are_refused(auth_client, session, ready, linked, method, path):
    before = state(session, ready)

    response = auth_client.request(
        method,
        path.format(job=ready.id, cv=linked.id),
        data={"cv": linked.id, "token": "x"},
        headers=htmx(auth_client),
    )

    assert response.status_code == 405
    assert state(session, ready) == before
    assert trash(session) == []
