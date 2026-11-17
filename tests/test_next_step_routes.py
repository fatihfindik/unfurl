"""Change and Done on the Next step card (SPEC §6.13, D-073)."""

import datetime as dt
import json

import pytest

from app.models import Application
from app.services import application_service as apps
from app.services import clock
from app.services.auth_service import create_user
from tests.helpers import csrf_token

TOMORROW = (clock.today() + dt.timedelta(days=1)).isoformat()


def add(session, user, company="Kranich Health", **fields) -> Application:
    fields.setdefault("job_title", "PM")
    return apps.create(session, user, company=company, **fields)


def post(client, url, **data):
    return client.post(
        url, data=data, headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)}
    )


def change(client, job, **fields):
    return post(client, f"/applications/{job.id}/next-step", **fields)


def done(client, job):
    return post(client, f"/applications/{job.id}/next-step/done")


def toast_of(response) -> dict:
    return json.loads(response.headers["HX-Trigger"])["toast"]


def undo(client, response):
    return post(client, "/applications/undo", token=toast_of(response)["undo_token"])


def fresh(session, job) -> Application:
    session.expire_all()
    return session.get(Application, job.id)


def card(client, job) -> str:
    return client.get(f"/applications/{job.id}/next-step").text


# --- The card ------------------------------------------------------------------------------


def test_the_card_has_change_and_done(auth_client, session, user):
    job = add(session, user)

    html = card(auth_client, job)

    assert f'hx-get="/applications/{job.id}/next-step/edit"' in html and ">Change<" in html
    assert f'hx-post="/applications/{job.id}/next-step/done"' in html and ">Done<" in html


def test_an_archived_job_has_neither(auth_client, session, user):
    job = add(session, user)
    apps.archive(session, user, job)

    html = card(auth_client, job)

    assert ">Change<" not in html and ">Done<" not in html
    assert (
        change(auth_client, job, next_action_type="custom", date=TOMORROW, note="x").status_code
        == 409
    )


def test_the_form_is_filled_with_the_current_step(auth_client, session, user):
    job = add(session, user, status="applied")

    html = auth_client.get(f"/applications/{job.id}/next-step/edit").text

    assert '<option value="follow_up" selected>Follow up</option>' in html
    follow_up = clock.today() + dt.timedelta(days=10)
    assert f'value="{follow_up.isoformat()}"' in html
    for label in [
        "Apply by",
        "Follow up",
        "Interview",
        "Send thank-you note",
        "Reply to offer",
        "Custom",
    ]:
        assert f">{label}</option>" in html


# --- Change -------------------------------------------------------------------------------


def test_change_sets_the_step_by_hand(auth_client, session, user):
    job = add(session, user)

    response = change(
        auth_client,
        job,
        next_action_type="custom",
        date=TOMORROW,
        time="09:00",
        note="Ask Lukas for a referral",
    )

    assert response.status_code == 200
    assert toast_of(response)["message"] == "Next step saved."
    assert "Ask Lukas for a referral" in response.text  # the card comes back
    job = fresh(session, job)
    assert (job.next_action_manual, job.next_action_type) == (True, "custom")
    assert job.next_action_note == "Ask Lukas for a referral"
    assert clock.local(job.next_action_at).strftime("%H:%M") == "09:00"
    assert apps.events_of(session, job)[-1].type == "next_step_changed"
    assert "Set by you." in card(auth_client, job)


def test_change_on_an_offer_sets_the_deadline(auth_client, session, user):
    job = add(session, user)
    apps.move(session, user, job, "offer")

    change(auth_client, job, next_action_type="reply_offer", date=TOMORROW)

    day = dt.date.fromisoformat(TOMORROW).strftime("%a, %-d %b")
    assert f"Reply to the offer by {day}" in card(auth_client, job)


def test_a_note_is_kept_only_for_custom(auth_client, session, user):
    job = add(session, user)

    change(auth_client, job, next_action_type="follow_up", date=TOMORROW, note="ignored")

    assert fresh(session, job).next_action_note is None


def test_change_ends_a_snooze(auth_client, session, user):
    job = add(session, user)
    job.snoozed_until = clock.now() + dt.timedelta(days=3)
    session.add(job)
    session.commit()

    change(auth_client, job, next_action_type="follow_up", date=TOMORROW)

    assert fresh(session, job).snoozed_until is None


def test_undo_of_change_brings_the_automatic_step_back(auth_client, session, user):
    job = add(session, user)

    response = change(auth_client, job, next_action_type="custom", date=TOMORROW, note="x")
    undo(auth_client, response)

    job = fresh(session, job)
    assert (job.next_action_manual, job.next_action_type) == (False, None)
    assert "Set automatically from the status." in card(auth_client, job)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"next_action_type": "moon", "date": TOMORROW}, "Choose a next step from the list."),
        ({"next_action_type": "follow_up", "date": ""}, "This field is required."),
        ({"next_action_type": "follow_up", "date": "9999-12-31"}, "Pick a date between"),
        (
            {"next_action_type": "custom", "date": TOMORROW, "note": "  "},
            "Write what you want to do.",
        ),
        (
            {"next_action_type": "custom", "date": TOMORROW, "note": "x" * 201},
            "Use at most 200 characters.",
        ),
        (
            {"next_action_type": "follow_up", "date": TOMORROW, "time": "noon"},
            "Use the format HH:MM.",
        ),
    ],
)
def test_wrong_values_are_explained(auth_client, session, user, fields, message):
    job = add(session, user)

    response = change(auth_client, job, **fields)

    assert response.status_code == 200 and message in response.text
    assert "HX-Trigger" not in response.headers
    assert fresh(session, job).next_action_manual is False


# --- Done ---------------------------------------------------------------------------------


def test_done_leaves_no_next_step_until_the_status_changes(auth_client, session, user):
    job = add(session, user, status="applied")

    response = done(auth_client, job)

    assert toast_of(response)["message"] == "Done: Follow up."
    assert "Done. Change the status or set a new next step." in card(auth_client, job)
    assert apps.events_of(session, job)[-1].message == "Done: Follow up"

    apps.move(session, user, fresh(session, job), "interview")  # a new status: rules again
    assert fresh(session, job).next_action_manual is False


def test_done_on_the_thank_you_note_brings_a_follow_up(auth_client, session, user):
    job = add(session, user)
    apps.set_interview(
        session,
        user,
        job,
        at=clock.now() - dt.timedelta(hours=2),
        interview_type=None,
        interview_with=None,
    )

    done(auth_client, job)

    in_five_days = (clock.today() + dt.timedelta(days=5)).strftime("%a, %-d %b")
    assert f"Follow up on {in_five_days}" in card(auth_client, job)

    done(auth_client, job)  # and that follow-up, once done, ends it (no loop)
    assert "No next step" in card(auth_client, job)


def test_undo_of_done(auth_client, session, user):
    job = add(session, user, status="applied")

    undo(auth_client, done(auth_client, job))

    assert "Follow up on" in card(auth_client, job)


def test_done_without_a_next_step_changes_nothing(auth_client, session, user):
    job = add(session, user)
    apps.move(session, user, job, "rejected")

    response = done(auth_client, job)

    assert response.headers["HX-Trigger"] == "application-changed"
    assert fresh(session, job).next_action_manual is False


def test_the_timeline_shows_status_note_interview_and_next_step(auth_client, session, user):
    # SPEC M4b acceptance: all four kinds of change appear on the timeline.
    job = add(session, user)
    apps.move(session, user, job, "interview")
    apps.set_interview(
        session,
        user,
        job,
        at=clock.to_utc(clock.today() + dt.timedelta(days=2)),
        interview_type=None,
        interview_with=None,
    )
    change(auth_client, job, next_action_type="custom", date=TOMORROW, note="Prepare questions")
    apps.add_note(session, user, fresh(session, job), "Mira prefers mornings.")

    html = auth_client.get(f"/applications/{job.id}").text

    for text in [
        "Status: Saved → Interview",
        "Interview set for",
        "Next step: Prepare questions",
        "Note: Mira prefers mornings.",
    ]:
        assert text in html


# --- Who may --------------------------------------------------------------------------------


def test_another_users_application_is_not_found(auth_client, session):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = add(session, other, "Theirs")

    assert auth_client.get(f"/applications/{theirs.id}/next-step").status_code == 404
    assert auth_client.get(f"/applications/{theirs.id}/next-step/edit").status_code == 404
    assert (
        change(auth_client, theirs, next_action_type="custom", date=TOMORROW, note="x").status_code
        == 404
    )
    assert done(auth_client, theirs).status_code == 404
    assert fresh(session, theirs).next_action_manual is False


def test_logged_out_and_without_csrf_nothing_changes(client, auth_client, session, user):
    job = add(session, user)
    assert auth_client.post(f"/applications/{job.id}/next-step/done").status_code == 403
    auth_client.post("/logout", data={"csrf_token": csrf_token(auth_client)})
    assert done(client, job).headers["HX-Redirect"].startswith("/login")
    assert fresh(session, job).next_action_manual is False
