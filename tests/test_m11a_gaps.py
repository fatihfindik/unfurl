"""M11a gaps: edge cases around the bell routes and the reminder rules that the main
notification test files leave out (D-099…D-105)."""

import datetime as dt
import time

import pytest
from sqlmodel import select

from app.models import Application, Notification, NotificationPreference
from app.services import application_service as apps
from app.services import clock
from app.services import notification_service as notes
from app.services.auth_service import create_user
from tests.helpers import csrf_token

# Wednesday 18 November 2026, 10:00 in Vienna (like the other notification tests).
NOW = dt.datetime(2026, 11, 18, 9, 0, tzinfo=dt.UTC)
TODAY = dt.date(2026, 11, 18)


def ago(days: int) -> dt.date:
    return TODAY - dt.timedelta(days=days)


def add_note(session, user, number=1, read=False, link="/applications/7", **fields):
    note = Notification(
        user_id=user.id,
        type="follow_up",
        title=fields.pop("title", f"Time to follow up {number}"),
        body=fields.pop("body", f"Acme {number} · no reply"),
        link_url=link,
        dedupe_key=f"key{number}",
        read_at=dt.datetime(2026, 11, 1, tzinfo=dt.UTC) if read else None,
        **fields,
    )
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


def applied_job(session, user, company="Acme", days=12, job_title="PM"):
    job = apps.create(session, user, company=company, job_title=job_title)
    apps.move(session, user, job, "applied")
    job.applied_at = ago(days)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def post(client, url, htmx=True):
    headers = {"X-CSRF-Token": csrf_token(client)}
    if htmx:
        headers["HX-Request"] = "true"
    return client.post(url, headers=headers, follow_redirects=False)


def all_notes(session) -> list[Notification]:
    session.expire_all()
    return list(session.exec(select(Notification)))


@pytest.fixture
def other(session):
    return create_user(session, email="other@example.test", password="x" * 12, full_name="O")


# --- Routes: limits and methods ------------------------------------------------------------


def test_open_is_rate_limited(auth_client, session, user):
    note = add_note(session, user)

    codes = [
        post(auth_client, f"/notifications/{note.id}/open", htmx=False).status_code
        for _ in range(61)
    ]

    assert codes[:60] == [303] * 60 and codes[60] == 429


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("GET", "/notifications/{id}/open"),
        ("GET", "/notifications/read-all"),
        ("DELETE", "/notifications/{id}/open"),
        ("PUT", "/notifications/read-all"),
        ("POST", "/notifications/badge"),
        ("POST", "/notifications/panel"),
    ],
)
def test_wrong_methods_are_405_and_change_nothing(auth_client, session, user, method, url):
    note = add_note(session, user)

    response = auth_client.request(
        method,
        url.format(id=note.id),
        headers={"X-CSRF-Token": csrf_token(auth_client)},
        follow_redirects=False,
    )

    assert response.status_code == 405
    [kept] = all_notes(session)
    assert kept.read_at is None


# --- Routes: other users' data ---------------------------------------------------------------


def test_badge_and_panel_ignore_guessed_ids(auth_client, session, user, other):
    # The routes take no ids; extra query values must not switch to another user.
    theirs = add_note(session, other, title="Their reminder")

    badge = auth_client.get(f"/notifications/badge?user_id={other.id}&id={theirs.id}").text
    panel = auth_client.get(f"/notifications/panel?user_id={other.id}&id={theirs.id}").text

    assert "unread" not in badge
    assert "Their reminder" not in panel
    assert f'data-notification="{theirs.id}"' not in panel
    assert "No notifications yet." in panel


def test_read_all_never_touches_another_users_notifications(auth_client, session, other):
    theirs = add_note(session, other)

    response = post(auth_client, f"/notifications/read-all?user_id={other.id}")

    assert response.status_code == 200
    session.refresh(theirs)
    assert theirs.read_at is None


def test_open_to_another_users_application_still_hides_it(auth_client, session, user, other):
    # A stored link that names someone else's job (changed by hand) only redirects;
    # the application page itself stays 404 for this user.
    theirs = apps.create(session, other, company="Secret Co", job_title="PM")
    note = add_note(session, user, link=f"/applications/{theirs.id}")

    response = post(auth_client, f"/notifications/{note.id}/open", htmx=False)
    page = auth_client.get(response.headers["location"], follow_redirects=False)

    assert page.status_code == 404
    assert "Secret Co" not in page.text


# --- Routes: states of the bell --------------------------------------------------------------


def test_an_unverified_user_still_sees_the_bell(auth_client, user):
    # The `user` fixture has no verified email: the verify banner is shown too.
    assert user.is_verified is False
    html = auth_client.get("/").text

    assert "Verify your email" in html
    assert 'hx-get="/notifications/badge"' in html
    assert auth_client.get("/notifications/badge").status_code == 200
    assert auth_client.get("/notifications/panel").status_code == 200


def test_panel_with_only_read_notifications(auth_client, session, user):
    add_note(session, user, 1, read=True)
    add_note(session, user, 2, read=True)

    html = auth_client.get("/notifications/panel").text

    assert html.count("data-notification=") == 2
    assert "Mark all as read" not in html
    assert "Unread</span>" not in html
    assert "No notifications yet." not in html


def test_read_all_when_nothing_is_unread(auth_client, session, user):
    note = add_note(session, user, read=True)
    first_read = note.read_at

    response = post(auth_client, "/notifications/read-all")

    assert response.status_code == 200
    session.refresh(note)
    assert note.read_at.replace(tzinfo=None) == first_read.replace(tzinfo=None)  # not moved


def test_opening_a_read_notification_keeps_its_read_time(auth_client, session, user):
    note = add_note(session, user, read=True)
    first_read = note.read_at

    response = post(auth_client, f"/notifications/{note.id}/open", htmx=False)

    assert response.status_code == 303
    session.refresh(note)
    assert note.read_at.replace(tzinfo=None) == first_read.replace(tzinfo=None)


def test_a_notification_of_an_archived_job_still_opens(auth_client, session, user):
    job = applied_job(session, user)
    notes.generate_due(session, NOW)
    apps.archive(session, user, job)
    [note] = all_notes(session)

    response = post(auth_client, f"/notifications/{note.id}/open", htmx=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/applications/{job.id}"
    assert auth_client.get(response.headers["location"]).status_code == 200
    session.refresh(note)
    assert note.read_at is not None


def test_a_notification_of_a_deleted_job_is_gone(auth_client, session, user):
    job = applied_job(session, user)
    notes.generate_due(session, NOW)
    [note] = all_notes(session)
    note_id = note.id
    session.delete(session.get(Application, job.id))
    session.commit()

    assert post(auth_client, f"/notifications/{note_id}/open", htmx=False).status_code == 404


def test_panel_escapes_html_and_keeps_newlines_harmless(auth_client, session, user):
    applied_job(session, user, company='<script>alert("x")</script>\nEvil Co')
    notes.generate_due(session, NOW)

    html = auth_client.get("/notifications/panel").text

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


# --- Generating and purging ------------------------------------------------------------------


def test_generate_with_users_without_applications(session, user, other):
    session.add(NotificationPreference(user_id=other.id))  # a row with the defaults
    session.commit()

    assert notes.generate_due(session, NOW) == 0
    assert all_notes(session) == []


def test_generate_with_no_users_at_all(session):
    assert notes.generate_due(session, NOW) == 0


def test_a_preference_row_with_the_defaults_keeps_all_types_on(session, user):
    applied_job(session, user)
    session.add(NotificationPreference(user_id=user.id))
    session.commit()

    assert notes.generate_due(session, NOW) == 1


def test_purge_on_an_empty_table(session):
    notes.purge(session, NOW)

    assert all_notes(session) == []


@pytest.mark.parametrize(
    "company",
    [
        "C" * 300,
        "Line one\nLine two\r\n\tTabbed",
        "<img src=x onerror=alert(1)>",
        "Ünïcödé · Gesellschaft m.b.H. 🚀",
    ],
)
def test_odd_company_names_still_make_a_reminder(session, user, company):
    applied_job(session, user, company=company)

    assert notes.generate_due(session, NOW) == 1

    [note] = all_notes(session)
    assert len(note.title) <= 200 and len(note.body) <= 300
    assert note.body.startswith(company[:50])  # stored as given, escaped only when shown


def test_long_interview_body_is_cut(session, user):
    job = apps.create(session, user, company="C" * 200, job_title="T" * 200)
    apps.move(session, user, job, "interview")
    job.interview_at = clock.to_utc(TODAY + dt.timedelta(days=1), dt.time(9))
    session.add(job)
    session.commit()

    assert notes.generate_due(session, NOW) == 1

    [note] = all_notes(session)
    assert note.type == "interview_tomorrow"
    assert len(note.body) == 300 and note.body.endswith("…")


def test_interview_reminder_also_after_done(session, user):
    # "Done" on the interview step leaves no next step, but the interview is still in
    # the calendar: D-101 reminds from the interview's date alone.
    job = apps.create(session, user, company="Acme", job_title="PM")
    apps.move(session, user, job, "interview")
    job.interview_at = clock.to_utc(TODAY + dt.timedelta(days=1), dt.time(10))
    job.next_action_manual = True
    job.next_action_type = None
    session.add(job)
    session.commit()
    session.expire_all()

    assert [d.type for d in notes.due_for_user(session, user, NOW)] == ["interview_tomorrow"]


def test_two_thousand_applications_stay_fast(session, user):
    # SPEC limit: 2,000 applications per user. Inserted directly to keep the test fast.
    created = clock.to_utc(ago(30), dt.time(12))
    for number in range(2000):
        session.add(
            Application(
                user_id=user.id,
                company=f"Co {number}",
                job_title="PM",
                status="applied",
                applied_at=ago(10 + number % 20),  # some in the window, some not
                created_at=created,
                last_activity_at=created,
            )
        )
    session.commit()

    started = time.perf_counter()
    written = notes.generate_due(session, NOW)
    again = notes.generate_due(session, NOW)
    elapsed = time.perf_counter() - started

    # applied 10..16 days ago are in the window: 7 of every 20.
    assert written == 700 and again == 0
    assert elapsed < 10  # generous; a sanity check, not a benchmark
