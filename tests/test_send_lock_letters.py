"""Sending locks the cover letter with the CV (SPEC §5.4b, §12 M8b; D-159, D-160)."""

import datetime as dt
import json

import pytest
from freezegun import freeze_time
from sqlmodel import select

from app.i18n.document_labels import full_date
from app.models import ApplicationDocument, CoverLetter, CVDocument
from app.services import application_service as apps
from app.services import cover_letter_service as letters
from app.services import document_service as docs
from app.services import undo_service
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile
from tests.test_application_documents import fresh, headers, toast, undo


def soon(days: int, hour: int, minute: int = 0) -> dt.datetime:
    """A moment `days` from now (UTC). Frozen times must come after the login: the
    session cookie of an earlier moment is not valid yet."""
    day = dt.datetime.now(dt.UTC) + dt.timedelta(days=days)
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


FIXED = soon(1, 10)
FIXED_TEXT = full_date(FIXED.date(), "en")


@pytest.fixture
def job(session, user):
    return apps.create(session, user, company="Kranich Health", job_title="Senior PM")


@pytest.fixture
def linked(session, user, job):
    """A job with a draft CV and a draft letter, both made for it."""
    get_or_create_profile(session, user)
    cv = docs.copy_for_application(
        session, user, docs.create_cv(session, user, "Master CV", "en", "classic"), job, "CV"
    )
    apps.link_cv(session, user, job, cv)
    master = letters.create_letter(session, user, "Master letter", "en")
    letter = letters.copy_for_application(session, user, master, job, "Letter")
    apps.link_letter(session, user, job, letter)
    return fresh(session, job), cv, letter


def move(client, job, status):
    return client.patch(
        f"/applications/{job.id}/move", data={"status": status}, headers=headers(client)
    )


def pick(client, job, status):
    return client.post(
        f"/applications/{job.id}/status", data={"status": status}, headers=headers(client)
    )


def save_interview(client, job):
    return client.post(
        f"/applications/{job.id}/interview",
        data={"date": "2026-12-01", "time": "10:00", "interview_type": "video"},
        headers=headers(client),
    )


def sent_records(session, job) -> list[ApplicationDocument]:
    session.expire_all()
    return list(
        session.exec(
            select(ApplicationDocument)
            .where(ApplicationDocument.application_id == job.id)
            .order_by(ApplicationDocument.id)
        )
    )


def token_of(response) -> str:
    return json.loads(response.headers["HX-Trigger"])["toast"]["undo_token"]


# --- Every way of sending a job locks both (plan-reviewer M8b #6) ----------------------------

WAYS = {
    "board": lambda client, job: move(client, job, "applied"),
    "picker": lambda client, job: pick(client, job, "offer"),
    "interview": save_interview,
}


@pytest.mark.parametrize("way", WAYS)
def test_sending_locks_the_cv_and_the_letter(auth_client, session, user, linked, way):
    job, cv, letter = linked

    with freeze_time(FIXED):
        response = WAYS[way](auth_client, job)

    assert fresh(session, cv).status == "locked"
    assert fresh(session, letter).status == "locked"
    records = sent_records(session, job)
    assert [r.doc_type for r in records] == ["cv", "cover_letter"]
    sent_letter = records[1]
    assert (sent_letter.cover_letter_id, sent_letter.document_id) == (letter.id, None)
    assert sent_letter.content_snapshot["subject"] == "Application for Senior PM"
    assert sent_letter.content_snapshot["date"] == FIXED_TEXT
    messages = [e.message for e in apps.events_of(session, fresh(session, job))]
    assert "Letter locked: this is what you sent" in messages
    assert len(token_of(response)) <= undo_service.TOKEN_MAX_LENGTH


@pytest.mark.parametrize("way", WAYS)
def test_undo_unlocks_both_and_forgets_what_was_sent(auth_client, session, user, linked, way):
    job, cv, letter = linked
    response = WAYS[way](auth_client, job)

    assert undo(auth_client, response).status_code in (200, 204)

    assert fresh(session, cv).status == "draft"
    assert fresh(session, letter).status == "draft"
    assert fresh(session, letter).locked_at is None
    assert sent_records(session, job) == []
    assert fresh(session, job).status == "saved"


def test_restoring_an_archived_job_into_a_sent_status_locks(auth_client, session, user, linked):
    job, cv, letter = linked
    apps.archive(session, user, job)

    pick(auth_client, fresh(session, job), "applied")

    assert fresh(session, letter).status == "locked"
    assert fresh(session, cv).status == "locked"


def test_only_a_letter_is_enough(auth_client, session, user, job):
    get_or_create_profile(session, user)
    master = letters.create_letter(session, user, "Master letter", "en")
    letter = letters.copy_for_application(session, user, master, job, "Letter")
    apps.link_letter(session, user, job, letter)

    response = move(auth_client, job, "applied")

    assert fresh(session, letter).status == "locked"
    assert [r.doc_type for r in sent_records(session, job)] == ["cover_letter"]
    undo(auth_client, response)
    assert fresh(session, letter).status == "draft"


def test_rejected_does_not_lock(auth_client, session, user, linked):
    job, cv, letter = linked

    move(auth_client, job, "rejected")

    assert fresh(session, letter).status == "draft"
    assert sent_records(session, job) == []


def test_a_letter_is_locked_and_recorded_once(auth_client, session, user, linked):
    job, cv, letter = linked
    move(auth_client, job, "applied")

    move(auth_client, fresh(session, job), "interview")

    assert [r.doc_type for r in sent_records(session, job)] == ["cv", "cover_letter"]


def test_undo_never_unlocks_a_letter_locked_by_something_else(auth_client, session, user, linked):
    job, cv, letter = linked
    response = move(auth_client, job, "applied")
    # Something else locked it again later (another lock, another time).
    current = fresh(session, letter)
    current.locked_at = dt.datetime(2030, 1, 1, tzinfo=dt.UTC)
    session.add(current)
    session.commit()

    undo(auth_client, response)

    assert fresh(session, letter).status == "locked"


def test_a_broken_token_is_nothing_to_undo(session, user, linked):
    job, cv, letter = linked
    for bad in ("x", [["x", "y"]], [[0, "t"]], [[1]]):
        with pytest.raises(apps.NothingToUndo):
            apps._undo_documents(session, user, job, {"locked_letters": bad})


# --- "Lock as sent" (D-159) -------------------------------------------------------------------


def send(client, job):
    return client.post(f"/applications/{job.id}/cover-letter/send", headers=headers(client))


def test_a_letter_linked_after_sending_waits_for_lock_as_sent(
    auth_client,
    session,
    user,
    job,
):
    get_or_create_profile(session, user)
    move(auth_client, job, "applied")
    master = letters.create_letter(session, user, "Master letter", "en")
    letter = letters.copy_for_application(session, user, master, job, "Letter")
    apps.link_letter(session, user, fresh(session, job), letter)

    html = auth_client.get(f"/applications/{job.id}").text
    assert fresh(session, letter).status == "draft"
    assert f'hx-post="/applications/{job.id}/cover-letter/send"' in html

    response = send(auth_client, job)
    assert toast(response) == "Cover letter locked: this is what you sent."
    assert fresh(session, letter).status == "locked"
    assert [r.doc_type for r in sent_records(session, job)] == ["cover_letter"]

    undo(auth_client, response)
    assert fresh(session, letter).status == "draft"
    assert sent_records(session, job) == []


def test_lock_as_sent_needs_a_sent_job_and_a_draft_letter(auth_client, session, user, linked):
    job, cv, letter = linked

    assert send(auth_client, job).status_code == 409  # still saved
    move(auth_client, job, "applied")
    assert send(auth_client, job).status_code == 409  # locked already


def test_lock_as_sent_without_csrf_or_for_another_user(auth_client, session, user, linked):
    job, cv, letter = linked
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    their_job = apps.create(session, other, company="Theirs", job_title="PM", status="applied")

    assert auth_client.post(f"/applications/{job.id}/cover-letter/send").status_code == 403
    assert send(auth_client, their_job).status_code == 404
    assert fresh(session, letter).status == "draft"


# --- The date line stays the day it was sent (D-160) ------------------------------------------


def test_the_preview_keeps_the_day_it_was_sent(auth_client, session, user, linked):
    job, cv, letter = linked
    with freeze_time(FIXED):
        move(auth_client, job, "applied")

    later = soon(9, 12)
    with freeze_time(later):
        page = auth_client.get(f"/cover-letters/{letter.id}").text

    assert FIXED_TEXT in page and full_date(later.date(), "en") not in page


def test_the_day_it_was_sent_is_the_users_day(auth_client, session, user, linked):
    # 23:30 UTC is already the next day in Vienna.
    job, cv, letter = linked
    user.timezone = "Europe/Vienna"
    session.add(user)
    session.commit()
    late = soon(1, 23, 30)

    with freeze_time(late):
        move(auth_client, job, "applied")

    next_day = full_date(late.date() + dt.timedelta(days=1), "en")
    assert sent_records(session, job)[1].content_snapshot["date"] == next_day


def test_without_a_sent_record_the_lock_time_is_used(session, user, linked):
    job, cv, letter = linked
    letter.status = "locked"
    letter.locked_at = dt.datetime(2026, 2, 3, 9, tzinfo=dt.UTC)
    session.add(letter)
    session.commit()

    assert letters.letter_date(session, letter) == "3 February 2026"


def test_a_draft_shows_today(session, user, linked):
    job, cv, letter = linked

    with freeze_time(FIXED):
        assert letters.letter_date(session, letter) == FIXED_TEXT


# --- A locked letter never changes (acceptance) -----------------------------------------------


def test_a_sent_letter_cannot_be_saved_or_deleted(auth_client, session, user, linked):
    job, cv, letter = linked
    move(auth_client, job, "applied")
    before = fresh(session, letter).body

    saved = auth_client.post(
        f"/cover-letters/{letter.id}", data={"body": "Changed"}, headers=headers(auth_client)
    )
    deleted = auth_client.delete(f"/cover-letters/{letter.id}", headers=headers(auth_client))
    refreshed = auth_client.post(
        f"/cover-letters/{letter.id}/refresh", headers=headers(auth_client)
    )

    assert (saved.status_code, deleted.status_code, refreshed.status_code) == (409, 409, 409)
    assert fresh(session, letter).body == before
    assert session.get(CoverLetter, letter.id) is not None
    assert session.get(CVDocument, cv.id).status == "locked"
