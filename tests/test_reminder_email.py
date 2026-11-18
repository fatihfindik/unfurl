"""The study reminder email and its "Turn off these emails" link (D-098, D-112).
M11b acceptance: the turn-off link in the email works."""

import datetime as dt
import logging
import re

from freezegun import freeze_time
from sqlmodel import select

from app.models import (
    LearningGoal,
    LearningTask,
    Notification,
    NotificationPreference,
    StudyLog,
)
from app.services import auth_service, clock, email_service, reminder_email
from app.services import notification_service as notes
from tests.helpers import csrf_token

WED = dt.date(2026, 11, 18)
EVENING = clock.to_utc(WED, dt.time(19, 0))  # 19:00 in Vienna


def plan_a_task(session, user):
    goal = LearningGoal(user_id=user.id, title="Python")
    session.add(goal)
    session.commit()
    session.add(
        LearningTask(
            user_id=user.id,
            goal_id=goal.id,
            title="CS50P Week 3",
            due_date=WED,
            estimated_minutes=25,
        )
    )
    session.commit()


def verify(session, user):
    user.is_verified = True
    session.add(user)
    session.commit()


def prefs(session, user, **values):
    session.add(NotificationPreference(user_id=user.id, **values))
    session.commit()


def link_in(email: email_service.Email) -> str:
    return re.search(r"(/reminders/off\?token=\S+)", email.body).group(1)


def email_pref(session, user) -> bool:
    """The user's study_reminder_email; no row yet means the default (on)."""
    session.expire_all()
    value = session.exec(
        select(NotificationPreference.study_reminder_email).where(
            NotificationPreference.user_id == user.id
        )
    ).first()
    return True if value is None else value


# --- Sending --------------------------------------------------------------------------------


def test_the_reminder_is_also_emailed(session, user, outbox):
    verify(session, user)
    plan_a_task(session, user)

    notes.generate_due(session, EVENING)

    [email] = outbox
    assert email.to == user.email
    assert email.subject == "Time to study"
    assert "Time to study: 25 min" in email.body and "CS50P Week 3" in email.body
    assert "Turn off these emails: http" in email.body
    assert "/learning" in email.body


def test_two_runs_send_one_email(session, user, outbox):
    verify(session, user)
    plan_a_task(session, user)

    notes.generate_due(session, EVENING)
    notes.generate_due(session, EVENING + dt.timedelta(minutes=15))

    assert len(outbox) == 1


def test_no_email_to_an_unverified_address(session, user, outbox):
    plan_a_task(session, user)

    notes.generate_due(session, EVENING)

    assert outbox == []
    assert len(session.exec(select(Notification)).all()) == 1  # in-app still comes


def test_no_email_when_switched_off(session, user, outbox):
    verify(session, user)
    prefs(session, user, study_reminder_email=False)
    plan_a_task(session, user)

    notes.generate_due(session, EVENING)

    assert outbox == []
    assert len(session.exec(select(Notification)).all()) == 1


def test_no_email_for_other_notifications(session, user, outbox):
    verify(session, user)
    prefs(session, user, rest_days=[])
    for back in range(7):
        session.add(StudyLog(user_id=user.id, date=WED - dt.timedelta(days=back), minutes=30))
    session.commit()

    notes.generate_due(session, EVENING)  # a streak milestone, no task: no reminder

    assert outbox == []


def test_a_failing_email_does_not_stop_the_in_app_reminder(session, user, monkeypatch, caplog):
    verify(session, user)
    plan_a_task(session, user)
    monkeypatch.setattr(email_service.get_settings(), "email_backend", "smtp")
    monkeypatch.setattr(email_service.get_settings(), "smtp_host", "127.0.0.1")
    monkeypatch.setattr(email_service.get_settings(), "smtp_port", 1)  # nothing listens

    with caplog.at_level(logging.ERROR):
        assert notes.generate_due(session, EVENING) == 1

    assert len(session.exec(select(Notification)).all()) == 1
    assert "Could not send email: Time to study" in caplog.text
    assert user.email not in caplog.text


# --- The turn-off link (acceptance) ---------------------------------------------------------


def emailed_link(session, user, outbox) -> str:
    verify(session, user)
    plan_a_task(session, user)
    notes.generate_due(session, EVENING)
    return link_in(outbox[0])


def test_opening_the_link_changes_nothing(client, session, user, outbox):
    link = emailed_link(session, user, outbox)

    response = client.get(link)

    assert response.status_code == 200
    assert "Stop study reminder emails?" in response.text
    assert "j***@example.com" in response.text
    assert user.email not in response.text
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert email_pref(session, user) is True


def test_the_button_turns_the_emails_off(client, session, user, outbox):
    link = emailed_link(session, user, outbox)
    token = link.split("token=")[1]
    page = client.get(link)

    response = client.post(
        "/reminders/off", data={"token": token, "csrf_token": csrf_token(client)}
    )

    assert response.status_code == 200
    assert "You won't get study reminder emails any more." in response.text
    assert "In-app reminders stay" in response.text
    assert email_pref(session, user) is False
    assert page.status_code == 200
    # Only the emails: the reminder itself stays on.
    session.expire_all()
    row = session.exec(select(NotificationPreference)).one()
    assert row.study_reminder_enabled is True


def test_after_turning_off_no_more_emails(client, session, user, outbox):
    link = emailed_link(session, user, outbox)
    client.get(link)
    client.post(
        "/reminders/off",
        data={"token": link.split("token=")[1], "csrf_token": csrf_token(client)},
    )

    notes.generate_due(session, clock.to_utc(WED + dt.timedelta(days=1), dt.time(19, 0)))

    assert len(outbox) == 1  # only the first one
    assert len(session.exec(select(Notification)).all()) == 2  # in-app goes on


def test_the_button_needs_the_csrf_token(client, session, user, outbox):
    link = emailed_link(session, user, outbox)
    client.get(link)

    response = client.post("/reminders/off", data={"token": link.split("token=")[1]})

    assert response.status_code == 403
    assert email_pref(session, user) is True


def test_a_broken_token_is_not_valid(client, session, user):
    for token in ("", "abc", "x" * 600):
        response = client.get("/reminders/off", params={"token": token})
        assert response.status_code == 400
        assert "This link is no longer valid" in response.text
        assert "You can turn reminders off in Settings." in response.text

    response = client.post(
        "/reminders/off", data={"token": "abc", "csrf_token": csrf_token(client)}
    )
    assert response.status_code == 400


def test_a_token_made_for_something_else_is_not_valid(client, session, user):
    reset = auth_service.make_reset_token(user)
    verify_token = auth_service.make_verify_token(user)

    for token in (reset, verify_token):
        assert client.get("/reminders/off", params={"token": token}).status_code == 400
    assert reminder_email.user_from_off_token(session, auth_service.make_reset_token(user)) is None


def test_an_old_token_is_not_valid(client, session, user):
    with freeze_time("2026-08-01"):
        token = reminder_email.make_off_token(user)
    with freeze_time("2026-11-18"):  # more than 90 days later
        assert client.get("/reminders/off", params={"token": token}).status_code == 400


def test_a_token_is_valid_for_90_days(client, session, user):
    with freeze_time("2026-08-21"):
        token = reminder_email.make_off_token(user)
    with freeze_time("2026-11-18"):  # 89 days later
        assert client.get("/reminders/off", params={"token": token}).status_code == 200


def test_a_token_for_an_old_address_is_not_valid(client, session, user):
    token = reminder_email.make_off_token(user)
    user.email = "new@example.com"
    session.add(user)
    session.commit()

    assert client.get("/reminders/off", params={"token": token}).status_code == 400
    response = client.post(
        "/reminders/off", data={"token": token, "csrf_token": csrf_token(client)}
    )
    assert response.status_code == 400


def test_the_post_only_changes_the_tokens_user(client, session, user):
    other = auth_service.create_user(
        session, email="other@example.com", password="x" * 12, full_name="O"
    )
    token = reminder_email.make_off_token(user)

    client.post("/reminders/off", data={"token": token, "csrf_token": csrf_token(client)})

    session.expire_all()
    rows = {row.user_id: row for row in session.exec(select(NotificationPreference))}
    assert rows[user.id].study_reminder_email is False
    assert other.id not in rows  # untouched (no row: the defaults, emails on)


def test_the_address_is_masked():
    assert reminder_email.masked("jana@example.com") == "j***@example.com"
    assert reminder_email.masked("x@y.org") == "x***@y.org"


def test_the_turn_off_pages_are_not_cached(client, session, user):
    token = reminder_email.make_off_token(user)

    page = client.get("/reminders/off", params={"token": token})
    done = client.post("/reminders/off", data={"token": token, "csrf_token": csrf_token(client)})

    assert page.headers["Cache-Control"] == "no-store"
    assert done.headers["Cache-Control"] == "no-store"


def test_emails_are_sent_after_every_users_reminders_are_written(session, user, outbox):
    verify(session, user)
    plan_a_task(session, user)
    other = auth_service.create_user(
        session, email="other@example.com", password="x" * 12, full_name="O"
    )
    verify(session, other)
    plan_a_task(session, other)

    assert notes.generate_due(session, EVENING) == 2

    assert sorted(email.to for email in outbox) == ["jana@example.com", "other@example.com"]
