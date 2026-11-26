"""Notes on the timeline (SPEC §6.7, M4b)."""

import json

import pytest

from app.models import Application
from app.services import application_service as apps
from app.services.auth_service import create_user
from tests.helpers import csrf_token


def add(session, user, company="Kranich Health") -> Application:
    return apps.create(session, user, company=company, job_title="PM")


def add_note(client, job, text):
    return client.post(
        f"/applications/{job.id}/notes",
        data={"text": text},
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)},
    )


def notes(session, job) -> list[str]:
    session.expire_all()
    return [e.message for e in apps.events_of(session, job) if e.type == "note_added"]


def test_the_timeline_has_a_note_box(auth_client, session, user):
    job = add(session, user)

    html = auth_client.get(f"/applications/{job.id}").text

    assert f'hx-post="/applications/{job.id}/notes"' in html and ">Add note</button>" in html


def test_a_note_appears_on_top_of_the_timeline(auth_client, session, user):
    job = add(session, user)

    response = add_note(auth_client, job, "  Called Mira, she replies Friday.  ")

    assert response.status_code == 200
    assert response.text.lstrip().startswith('<section id="timeline"')
    first = response.text.index("Note: Called Mira, she replies Friday.")
    assert first < response.text.index("Added by hand")
    assert json.loads(response.headers["HX-Trigger"])["toast"]["message"] == "Note added."
    assert notes(session, job) == ["Called Mira, she replies Friday."]


def test_a_long_note_is_kept_whole(auth_client, session, user):
    job = add(session, user)
    text = "Ö" * 2000

    add_note(auth_client, job, text)

    assert notes(session, job) == [text]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "This field is required."),
        ("   ", "This field is required."),
        ("x" * 2001, "Use at most 2000 characters."),
    ],
)
def test_wrong_notes_are_explained(auth_client, session, user, text, message):
    job = add(session, user)

    response = add_note(auth_client, job, text)

    assert message in response.text and 'aria-invalid="true"' in response.text
    assert "HX-Trigger" not in response.headers
    assert notes(session, job) == []


def test_a_note_is_escaped(auth_client, session, user):
    job = add(session, user)

    response = add_note(auth_client, job, "<script>alert(1)</script>")

    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_a_note_does_not_count_as_a_change_of_the_job(auth_client, session, user):
    job = add(session, user)
    before = job.last_activity_at

    add_note(auth_client, job, "Just a thought.")

    session.refresh(job)
    assert job.last_activity_at == before  # "Updated" on the board stays


def test_another_users_application_is_not_found(auth_client, session):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = add(session, other, "Theirs")

    assert add_note(auth_client, theirs, "hi").status_code == 404
    assert notes(session, theirs) == []


def test_logged_out_and_without_csrf_no_note(client, auth_client, session, user):
    job = add(session, user)
    assert auth_client.post(f"/applications/{job.id}/notes", data={"text": "x"}).status_code == 403
    auth_client.post("/logout", data={"csrf_token": csrf_token(auth_client)})
    assert add_note(client, job, "x").headers["HX-Redirect"].startswith("/login")
    assert notes(session, job) == []


def test_notes_are_rate_limited(auth_client, session, user):
    job = add(session, user)

    codes = [add_note(auth_client, job, f"note {n}").status_code for n in range(31)]

    assert codes[:30] == [200] * 30 and codes[30] == 429
