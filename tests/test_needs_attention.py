"""Needs attention on the Applications page (SPEC §6.3, D-074, D-077)."""

import datetime as dt
import json
import re

import pytest

from app.models import Application
from app.models.base import utcnow
from app.services import application_service as apps
from app.services import clock
from app.services.auth_service import create_user
from tests.helpers import csrf_token

UTC = dt.UTC


def add(session, user, company, status="saved", days_ago=0, **fields) -> Application:
    job = apps.create(session, user, company=company, job_title="PM")
    if status != "saved":
        apps.move(session, user, job, status)
    job.created_at = utcnow() - dt.timedelta(days=days_ago)
    for name, value in fields.items():
        setattr(job, name, value)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def ago(days) -> dt.date:
    return clock.today() - dt.timedelta(days=days)


def names(session, user, now=None) -> list[tuple[str, str]]:
    session.expire_all()
    return [
        (item.application.company, item.tag)
        for item in apps.needs_attention(session, user, now or utcnow())
    ]


# --- What is in it and in which order (the acceptance criterion) ---------------------------


@pytest.fixture
def jobs(session, user):
    add(session, user, "Waiting Co", days_ago=9)  # apply-by = saved + 7: "Waiting"
    add(session, user, "Fresh Co", days_ago=1)  # not due yet
    add(session, user, "Deadline Co", posting_closes_at=clock.today())  # apply by yesterday
    add(session, user, "Follow Co", status="applied", applied_at=ago(12))
    add(session, user, "Early Follow Co", status="applied", applied_at=ago(3))  # not due
    add(
        session,
        user,
        "Interview Co",
        status="interview",
        interview_at=clock.to_utc(clock.today(), dt.time(23, 59)),
    )
    add(
        session,
        user,
        "Thanks Co",
        status="interview",
        interview_at=clock.to_utc(ago(1), dt.time(10, 0)),
    )
    add(
        session,
        user,
        "Offer Co",
        status="offer",
        next_action_manual=True,
        next_action_type="reply_offer",
        next_action_at=clock.to_utc(clock.today()),
    )
    add(session, user, "Rejected Co", status="rejected")


def test_the_order_is_deadlines_interviews_follow_ups_then_waiting(session, user, jobs):
    assert names(session, user) == [
        ("Deadline Co", "Overdue"),  # yesterday
        ("Offer Co", "Offer"),  # today
        ("Thanks Co", "Interview"),  # thank-you note, yesterday
        ("Interview Co", "Interview"),  # today
        ("Follow Co", "Follow up"),
        ("Waiting Co", "Waiting"),
    ]


def test_a_hand_set_apply_by_is_a_deadline_not_waiting(session, user):
    add(
        session,
        user,
        "Hand Co",
        days_ago=9,
        next_action_manual=True,
        next_action_type="apply_by",
        next_action_at=clock.to_utc(clock.today()),
    )

    assert names(session, user) == [("Hand Co", "Due today")]


def test_archived_and_closed_jobs_are_left_out(session, user, jobs):
    waiting = next(j for j in apps.board(session, user)["saved"] if j.company == "Waiting Co")
    apps.archive(session, user, waiting)

    assert "Waiting Co" not in [name for name, _ in names(session, user)]
    assert "Rejected Co" not in [name for name, _ in names(session, user)]


def test_another_users_jobs_are_never_in_it(session, user):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    add(session, other, "Their Co", days_ago=20)

    assert names(session, user) == []


# --- Snooze ------------------------------------------------------------------------------------


def snooze(client, job):
    return client.post(
        f"/applications/{job.id}/snooze",
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)},
    )


def test_snooze_hides_for_three_days_with_undo(auth_client, session, user):
    job = add(session, user, "Waiting Co", days_ago=9)

    response = snooze(auth_client, job)

    toast = json.loads(response.headers["HX-Trigger"])["toast"]
    assert toast["message"] == "Waiting Co snoozed for 3 days." and toast["undo_token"]
    assert names(session, user) == []
    in_three_days = utcnow() + dt.timedelta(days=3, minutes=1)
    assert names(session, user, now=in_three_days) == [("Waiting Co", "Waiting")]
    assert [e.type for e in apps.events_of(session, job)] == ["created"]  # no event

    auth_client.post(
        "/applications/undo",
        data={"token": toast["undo_token"]},
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(auth_client)},
    )
    assert names(session, user) == [("Waiting Co", "Waiting")]


def test_a_snooze_token_works_only_once(auth_client, session, user):
    job = add(session, user, "Waiting Co", days_ago=9)
    token = json.loads(snooze(auth_client, job).headers["HX-Trigger"])["toast"]["undo_token"]
    headers = {"HX-Request": "true", "X-CSRF-Token": csrf_token(auth_client)}

    auth_client.post("/applications/undo", data={"token": token}, headers=headers)
    snooze(auth_client, job)  # snoozed again
    second = auth_client.post("/applications/undo", data={"token": token}, headers=headers)

    assert json.loads(second.headers["HX-Trigger"])["toast"]["message"] == "Nothing to undo."
    assert names(session, user) == []  # still snoozed: the old token did not unsnooze it


def test_a_new_status_ends_the_snooze(session, user):
    job = add(session, user, "Waiting Co", days_ago=9)
    apps.snooze(session, user, job, utcnow())

    apps.move(session, user, job, "applied")

    session.refresh(job)
    assert job.snoozed_until is None


def test_another_users_job_cannot_be_snoozed(auth_client, session):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = add(session, other, "Their Co", days_ago=9)

    assert snooze(auth_client, theirs).status_code == 404
    session.refresh(theirs)
    assert theirs.snoozed_until is None


def test_snooze_needs_a_login_and_the_csrf_token(client, auth_client, session, user):
    job = add(session, user, "Waiting Co", days_ago=9)
    assert auth_client.post(f"/applications/{job.id}/snooze").status_code == 403
    auth_client.post("/logout", data={"csrf_token": csrf_token(auth_client)})
    assert snooze(client, job).headers["HX-Redirect"].startswith("/login")


# --- On the page --------------------------------------------------------------------------------


def test_the_box_shows_the_first_three_and_show_all(auth_client, session, user, jobs):
    html = auth_client.get("/").text

    box = re.search(r'<section aria-labelledby="attention-title".*?</section>', html, re.S).group(0)
    assert "Needs attention · 6" in box
    assert "Most urgent first. Snooze hides an item for 3 days." in box
    first = box.split("<details")[0]
    assert first.count("<li ") == 3 and "Deadline Co" in first and "Follow Co" not in first
    assert "Show all (6)" in box
    assert "<b>Follow Co</b> · applied 12 days ago, no reply yet" in box
    assert "<b>Waiting Co</b> · saved 9 days ago, apply or archive?" in box
    assert 'hx-post="/applications/' in box and "/snooze" in box


def test_the_box_is_on_the_list_view_too(auth_client, session, user, jobs):
    assert "Needs attention · 6" in auth_client.get("/?view=list").text


def test_no_box_when_nothing_is_due(auth_client, session, user):
    add(session, user, "Fresh Co", days_ago=1)

    assert "Needs attention" not in auth_client.get("/").text


def test_user_text_is_escaped(auth_client, session, user):
    add(session, user, "<script>alert(1)</script>", days_ago=9)

    html = auth_client.get("/").text

    assert "<b><script>" not in html and "&lt;script&gt;alert(1)" in html
