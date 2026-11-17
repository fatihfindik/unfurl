"""The interview modal (SPEC §6.7, D-076): opened after a move to Interview (board or
status picker), "Skip" keeps the new status, "Save interview" sets the next step."""

import datetime as dt
import json

import pytest

from app.models import Application
from app.services import application_service as apps
from app.services import clock
from app.services.auth_service import create_user
from tests.helpers import csrf_token


def add(session, user, company="Kranich Health", **fields) -> Application:
    fields.setdefault("job_title", "PM")
    return apps.create(session, user, company=company, **fields)


def headers(client):
    return {"HX-Request": "true", "X-CSRF-Token": csrf_token(client)}


def save_interview(client, job, **fields):
    return client.post(f"/applications/{job.id}/interview", data=fields, headers=headers(client))


def triggers(response) -> dict:
    return json.loads(response.headers["HX-Trigger"])


def fresh(session, job) -> Application:
    session.expire_all()
    return session.get(Application, job.id)


IN_TWO_DAYS = (clock.today() + dt.timedelta(days=2)).isoformat()


# --- The modal opens after a move to Interview --------------------------------------------


def test_dragging_to_interview_opens_the_modal(auth_client, session, user):
    job = add(session, user)

    response = auth_client.patch(
        f"/applications/{job.id}/move", data={"status": "interview"}, headers=headers(auth_client)
    )

    assert triggers(response)["open-interview"] == job.id
    assert fresh(session, job).status == "interview"  # moved already: "Skip" keeps it


def test_the_status_picker_opens_the_modal_too(auth_client, session, user):
    job = add(session, user)

    response = auth_client.post(
        f"/applications/{job.id}/status", data={"status": "interview"}, headers=headers(auth_client)
    )

    assert triggers(response)["open-interview"] == job.id


@pytest.mark.parametrize("status", ["applied", "offer", "rejected"])
def test_other_moves_do_not_open_it(auth_client, session, user, status):
    job = add(session, user)

    response = auth_client.patch(
        f"/applications/{job.id}/move", data={"status": status}, headers=headers(auth_client)
    )

    assert "open-interview" not in triggers(response)


def test_both_pages_have_the_modal(auth_client, session, user):
    job = add(session, user)
    apps.move(session, user, job, "interview")

    board = auth_client.get("/").text
    detail = auth_client.get(f"/applications/{job.id}").text

    for html in (board, detail):
        assert '<dialog id="interview"' in html and 'id="interview-body"' in html
    assert f'data-interview-open="{job.id}"' in detail  # "Add interview details"
    assert "Add interview details" in detail


# --- The form ---------------------------------------------------------------------------------


def test_the_form_has_the_fields_and_skip(auth_client, session, user):
    job = add(session, user)

    html = auth_client.get(f"/applications/{job.id}/interview").text

    assert f'hx-post="/applications/{job.id}/interview"' in html
    for label in ["Date", "Time (optional)", "Type", "With (optional)"]:
        assert f">{label}</label>" in html
    assert "Skip, I will add it later" in html and "Save interview" in html


def test_the_form_is_filled_with_the_saved_interview(auth_client, session, user):
    job = add(session, user)
    apps.set_interview(
        session,
        user,
        job,
        at=clock.to_utc(dt.date.fromisoformat(IN_TWO_DAYS), dt.time(14, 30)),
        interview_type="phone",
        interview_with="Mira",
    )

    html = auth_client.get(f"/applications/{job.id}/interview").text

    assert f'value="{IN_TWO_DAYS}"' in html and 'value="14:30"' in html
    assert '<option value="phone" selected>' in html and 'value="Mira"' in html


# --- Saving ----------------------------------------------------------------------------------


def test_saving_sets_the_interview_and_the_next_step(auth_client, session, user):
    job = add(session, user)
    apps.move(session, user, job, "interview")

    response = save_interview(
        auth_client,
        job,
        date=IN_TWO_DAYS,
        time="10:00",
        interview_type="video",
        interview_with="Mira Hofer",
    )

    events = triggers(response)
    day = dt.date.fromisoformat(IN_TWO_DAYS)
    assert events["toast"]["message"] == f"Interview saved for {day.strftime('%a, %-d %b')}, 10:00."
    assert events["toast"]["undo_token"] and events["close-dialog"] == "interview"
    assert events["application-changed"] is True
    job = fresh(session, job)
    assert clock.local(job.interview_at) == clock.local(clock.to_utc(day, dt.time(10, 0)))
    assert (job.interview_type, job.interview_with) == ("video", "Mira Hofer")
    assert apps.events_of(session, job)[-1].type == "interview_scheduled"
    detail = auth_client.get(f"/applications/{job.id}").text
    assert "Video call with Mira Hofer." in detail and "10:00" in detail


def test_without_a_time_the_interview_is_all_day(auth_client, session, user):
    job = add(session, user)

    save_interview(auth_client, job, date=IN_TWO_DAYS)

    job = fresh(session, job)
    assert clock.is_start_of_day(job.interview_at)
    assert job.status == "interview"  # saved from elsewhere: moved to Interview too


def test_a_new_date_clears_a_next_step_set_by_hand(auth_client, session, user):
    job = add(session, user)
    apps.move(session, user, job, "interview")
    job = fresh(session, job)
    job.next_action_manual = True
    job.next_action_type = None  # "Done"
    session.add(job)
    session.commit()

    save_interview(auth_client, job, date=IN_TWO_DAYS)

    assert fresh(session, job).next_action_manual is False


def test_undo_takes_the_interview_back(auth_client, session, user):
    job = add(session, user)
    apps.move(session, user, job, "interview")

    response = save_interview(auth_client, job, date=IN_TWO_DAYS, time="09:00")
    auth_client.post(
        "/applications/undo",
        data={"token": triggers(response)["toast"]["undo_token"]},
        headers=headers(auth_client),
    )

    job = fresh(session, job)
    assert (job.status, job.interview_at) == ("interview", None)
    assert apps.events_of(session, job)[-1].type == "status_changed"


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"date": ""}, "This field is required."),
        ({"date": "tomorrow"}, "Use the format YYYY-MM-DD."),
        ({"date": "9999-12-31"}, "Pick a date between last year and two years from now."),
        ({"date": "2001-01-01"}, "Pick a date between last year and two years from now."),
        ({"date": IN_TWO_DAYS, "time": "25:99"}, "Use the format HH:MM."),
        ({"date": IN_TWO_DAYS, "interview_type": "carrier pigeon"}, "Choose a type from the list."),
        ({"date": IN_TWO_DAYS, "interview_with": "x" * 201}, "Use at most 200 characters."),
    ],
)
def test_wrong_values_are_explained_and_not_saved(auth_client, session, user, fields, message):
    job = add(session, user)

    response = save_interview(auth_client, job, **fields)

    assert response.status_code == 200
    assert message in response.text
    assert "HX-Trigger" not in response.headers
    assert fresh(session, job).interview_at is None


def test_user_text_is_escaped(auth_client, session, user):
    job = add(session, user, "<b>Co</b>")

    html = auth_client.get(f"/applications/{job.id}/interview").text

    assert "<b>Co</b>" not in html and "&lt;b&gt;Co&lt;/b&gt;" in html


# --- Who may --------------------------------------------------------------------------------


def test_another_users_application_is_not_found(auth_client, session):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = add(session, other, "Theirs")

    assert auth_client.get(f"/applications/{theirs.id}/interview").status_code == 404
    assert save_interview(auth_client, theirs, date=IN_TWO_DAYS).status_code == 404
    assert fresh(session, theirs).interview_at is None


def test_logged_out_and_without_csrf_nothing_is_saved(client, auth_client, session, user):
    job = add(session, user)
    response = auth_client.post(f"/applications/{job.id}/interview", data={"date": IN_TWO_DAYS})
    assert response.status_code == 403
    auth_client.post("/logout", data={"csrf_token": csrf_token(auth_client)})
    assert save_interview(client, job, date=IN_TWO_DAYS).headers["HX-Redirect"].startswith("/login")
    assert fresh(session, job).interview_at is None
