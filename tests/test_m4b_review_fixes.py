"""Fixes from the M4b security review (low findings 1–5)."""

import datetime as dt
import json

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.models import Application
from app.services import application_service as apps
from app.services import clock, undo_service
from tests.helpers import csrf_token


def headers(client):
    return {"HX-Request": "true", "X-CSRF-Token": csrf_token(client)}


def add(session, user, company="Kranich Health") -> Application:
    return apps.create(session, user, company=company, job_title="PM")


def toast_of(response) -> dict:
    return json.loads(response.headers["HX-Trigger"])["toast"]


def fresh(session, job) -> Application:
    session.expire_all()
    return session.get(Application, job.id)


# 1. Long texts don't make an oversized token -------------------------------------------------


def test_texts_after_the_change_are_only_fingerprints(auth_client, session, user):
    job = add(session, user)
    apps.move(session, user, job, "interview")
    emoji = "😀" * 200

    response = auth_client.post(
        f"/applications/{job.id}/interview",
        data={"date": (clock.today() + dt.timedelta(days=2)).isoformat(), "interview_with": emoji},
        headers=headers(auth_client),
    )

    note = undo_service.read_token(user, toast_of(response)["undo_token"])
    assert note["fields"]["interview_with"] == [None, apps.fingerprint(emoji)]
    # And Undo still works with the fingerprint.
    auth_client.post(
        "/applications/undo",
        data={"token": toast_of(response)["undo_token"]},
        headers=headers(auth_client),
    )
    assert fresh(session, job).interview_with is None


def test_a_token_that_would_be_too_long_means_no_undo(auth_client, session, user, monkeypatch):
    monkeypatch.setattr(undo_service, "TOKEN_MAX_LENGTH", 50)
    job = add(session, user)

    response = auth_client.post(
        f"/applications/{job.id}/status", data={"status": "applied"}, headers=headers(auth_client)
    )

    toast = toast_of(response)
    assert toast["message"] == "Kranich Health moved to Applied."
    assert (toast["undo_url"], toast["undo_token"]) == ("", "")


# 3. Undo of "add" keeps edits made since -------------------------------------------------------


def test_undo_of_add_after_editing_the_details_keeps_the_job(auth_client, session, user):
    response = auth_client.post(
        "/applications",
        data={"company": "Kranich Health", "job_title": "PM", "status": "saved"},
        headers=headers(auth_client),
    )
    job = apps.column_of(session, user, "saved")[0]
    auth_client.post(
        f"/applications/{job.id}/details",
        data={"company": "Kranich Health", "job_title": "PM", "notes": "Important!"},
        headers=headers(auth_client),
    )

    result = auth_client.post(
        "/applications/undo",
        data={"token": toast_of(response)["undo_token"]},
        headers=headers(auth_client),
    )

    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, job).notes == "Important!"


def test_undo_of_add_right_away_still_deletes(auth_client, session, user):
    response = auth_client.post(
        "/applications",
        data={"company": "Kranich Health", "job_title": "PM", "status": "saved"},
        headers=headers(auth_client),
    )

    auth_client.post(
        "/applications/undo",
        data={"token": toast_of(response)["undo_token"]},
        headers=headers(auth_client),
    )

    assert apps.column_of(session, user, "saved") == []


# 4. A wrong time zone stops the app at start ----------------------------------------------------


@pytest.mark.parametrize("value", ["Europe/Viena", "Mars/Olympus", "", "../../etc/passwd"])
def test_an_unknown_time_zone_is_refused(value):
    with pytest.raises(ValidationError, match="DEFAULT_TIMEZONE"):
        Settings(app_env="development", default_timezone=value)


def test_a_known_time_zone_is_accepted():
    assert Settings(app_env="development", default_timezone="Europe/Berlin").default_timezone == (
        "Europe/Berlin"
    )


# 5. At most 500 notes per job ---------------------------------------------------------------------


def test_notes_per_job_are_limited(auth_client, session, user, monkeypatch):
    monkeypatch.setattr(apps, "MAX_NOTES_PER_APPLICATION", 2)
    job = add(session, user)
    for text in ("one", "two"):
        apps.add_note(session, user, job, text)

    response = auth_client.post(
        f"/applications/{job.id}/notes", data={"text": "three"}, headers=headers(auth_client)
    )

    assert "This job has 2 notes already." in response.text
    assert "HX-Trigger" not in response.headers
    assert [e.message for e in apps.events_of(session, job) if e.type == "note_added"] == [
        "one",
        "two",
    ]
