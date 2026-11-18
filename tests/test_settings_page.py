"""Settings › Notifications (SPEC §6.9, D-114) and the panel's settings link (D-115)."""

import datetime as dt
import json
import re

import pytest
from sqlmodel import select

from app.models import NotificationPreference, User
from app.services import application_service as apps
from app.services import clock
from app.services.auth_service import create_user
from tests.helpers import USER_EMAIL, csrf_token, log_in

HTMX = {"HX-Request": "true"}

GOOD = {
    "study_reminder_enabled": "yes",
    "study_reminder_time": "07:30",
    "study_reminder_email": "yes",
    "rest_days": ["sun"],
    "timezone": "Europe/Vienna",
    "streak_enabled": "yes",
    "follow_up_enabled": "yes",
    "interview_enabled": "yes",
    "stale_saved_enabled": "yes",
}


def save(client, **changes):
    data = GOOD | changes
    data = {key: value for key, value in data.items() if value is not None}
    return client.post(
        "/settings/notifications",
        data=data | {"csrf_token": csrf_token(client)},
        headers=HTMX,
    )


def prefs_of(session, user) -> NotificationPreference:
    session.expire_all()
    return session.exec(
        select(NotificationPreference).where(NotificationPreference.user_id == user.id)
    ).one()


def zone_of(session) -> str | None:
    session.expire_all()
    return session.exec(select(User).where(User.email == USER_EMAIL)).one().timezone


# --- The page -------------------------------------------------------------------------------


def test_the_page_shows_the_notification_settings(auth_client):
    html = auth_client.get("/settings").text

    for text in (
        "Notifications",
        "Daily study reminder",
        "Remind me at",
        "Also send by email",
        "Rest days",
        "Time zone",
        "Celebrate 7, 30 and 100-day streaks and mention your streak in reminders.",
        "Follow-up reminders",
        "Interview reminders",
        "Stale saved jobs",
    ):
        assert text in html
    assert re.search(r'<option value="19:00" selected>', html)  # the default
    assert re.search(r'<option value="Europe/Vienna" selected>', html)
    assert 'value="sat" checked' in html and 'value="sun" checked' in html


def test_weekly_summary_is_coming_soon_and_ai_results_are_not_there_yet(auth_client):
    html = auth_client.get("/settings").text

    assert "Weekly email summary" in html and "Coming soon" in html
    assert "AI results" not in html  # comes with M5 (D-060)


def test_the_reminder_times_are_half_hours_from_6_to_23(auth_client):
    html = auth_client.get("/settings").text
    times = re.findall(r'<option value="(\d\d:\d\d)"', html)

    assert times[0] == "06:00" and times[-1] == "23:00" and "06:30" in times
    assert len(times) == 35


def test_the_users_zone_and_common_zones_come_first(auth_client):
    html = auth_client.get("/settings").text
    zones = re.findall(r'<option value="([A-Za-z_/+-]+)"', html)
    zones = [zone for zone in zones if not re.fullmatch(r"\d\d:\d\d", zone)]

    assert zones[:5] == [
        "Europe/Vienna",
        "Europe/Berlin",
        "Europe/Istanbul",
        "Europe/London",
        "UTC",
    ]
    assert "America/New_York" in zones
    assert "America/New York</option>" in html  # shown with a space


def test_settings_need_a_login(client):
    response = client.get("/settings", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/settings"


def test_saving_needs_a_login(client):
    response = client.post(
        "/settings/notifications",
        data=GOOD | {"csrf_token": csrf_token(client)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_saving_needs_the_csrf_token(auth_client, session, user):
    response = auth_client.post("/settings/notifications", data=GOOD, headers=HTMX)

    assert response.status_code == 403
    assert session.exec(select(NotificationPreference)).first() is None


# --- Saving ---------------------------------------------------------------------------------


def test_saving_stores_every_field(auth_client, session, user):
    response = save(
        auth_client,
        study_reminder_enabled=None,  # unticked
        study_reminder_email=None,
        streak_enabled=None,
        follow_up_enabled=None,
        rest_days=["sat", "sun", "mon"],
        study_reminder_time="21:30",
    )

    assert response.status_code == 200
    toast = json.loads(response.headers["HX-Trigger"])["toast"]
    assert toast["message"] == "Settings saved."
    assert 'id="notification-settings"' in response.text  # the form comes back redrawn
    prefs = prefs_of(session, user)
    assert prefs.study_reminder_enabled is False
    assert prefs.study_reminder_email is False
    assert prefs.streak_enabled is False
    assert prefs.follow_up_enabled is False
    assert prefs.interview_enabled is True and prefs.stale_saved_enabled is True
    assert prefs.study_reminder_time == dt.time(21, 30)
    assert prefs.rest_days == ["mon", "sat", "sun"]


def test_no_rest_days_is_allowed(auth_client, session, user):
    save(auth_client, rest_days=None)

    assert prefs_of(session, user).rest_days == []


@pytest.mark.parametrize(
    ("changes", "field", "message"),
    [
        ({"study_reminder_time": "19:15"}, "study_reminder_time", "Choose a time from the list."),
        ({"study_reminder_time": "05:30"}, "study_reminder_time", "Choose a time from the list."),
        ({"study_reminder_time": ""}, "study_reminder_time", "Choose a time from the list."),
        ({"timezone": "Mars/Base"}, "timezone", "Choose a time zone from the list."),
        ({"timezone": ""}, "timezone", "Choose a time zone from the list."),
        (
            {"rest_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]},
            "rest-days",
            "Keep at least one day to study.",
        ),
        ({"rest_days": ["someday"]}, "rest-days", "Choose days from the list."),
    ],
)
def test_bad_values_show_an_error_next_to_the_field(
    auth_client, session, user, changes, field, message
):
    response = save(auth_client, study_reminder_email=None, **changes)

    assert response.status_code == 200
    assert "HX-Trigger" not in response.headers  # no "saved" toast
    assert f'id="setting-{field}-error"' in response.text
    assert message in response.text
    # Nothing is saved, and what the user changed is still in the form.
    assert session.exec(select(NotificationPreference)).first() is None
    assert 'name="study_reminder_email" value="yes" class="size-5 shrink-0 accent-[#4F46E5]">' in (
        response.text
    )


# --- The time zone (D-108, D-109, D-114) ----------------------------------------------------


def test_a_new_zone_is_saved_and_the_page_reloads_with_the_toast(auth_client, session, user):
    response = save(auth_client, timezone="Asia/Tokyo")

    assert response.headers["HX-Refresh"] == "true"
    assert zone_of(session) == "Asia/Tokyo"
    page = auth_client.get("/settings").text  # the reload
    assert 'data-message="Settings saved."' in page
    assert re.search(r'<option value="Asia/Tokyo" selected>', page)


def test_the_new_zone_is_used_from_the_next_request(auth_client, session, user):
    job = apps.create(session, user, company="Acme", job_title="PM")
    job.interview_at = clock.to_utc(dt.date(2026, 11, 20), dt.time(9, 0))  # 09:00 Vienna
    job.status = "interview"
    session.add(job)
    session.commit()

    save(auth_client, timezone="Asia/Tokyo")  # Tokyo is 8 hours ahead in winter

    assert "17:00" in auth_client.get(f"/applications/{job.id}").text


def test_the_same_zone_does_not_reload(auth_client, session, user):
    response = save(auth_client, timezone="Europe/Vienna")

    assert "HX-Refresh" not in response.headers
    assert zone_of(session) == "Europe/Vienna"  # saved, so it stays if the default changes


def test_all_day_dates_move_with_the_zone(auth_client, session, user):
    job = apps.create(session, user, company="Acme", job_title="PM")
    job.interview_at = clock.to_utc(dt.date(2026, 11, 20))  # all day, Vienna
    session.add(job)
    session.commit()

    save(auth_client, timezone="America/New_York")
    session.refresh(job)

    with clock.use_timezone("America/New_York"):
        assert clock.is_start_of_day(job.interview_at)
        assert clock.local(job.interview_at).date() == dt.date(2026, 11, 20)


def test_saving_only_changes_the_users_own_settings(auth_client, session, user):
    other = create_user(session, email="o@example.com", password="x" * 12, full_name="O")

    save(auth_client, study_reminder_enabled=None, timezone="Asia/Tokyo")

    session.expire_all()
    assert session.get(User, other.id).timezone is None
    rows = session.exec(select(NotificationPreference)).all()
    assert [row.user_id for row in rows] == [user.id]


# --- The bell's panel link (D-115) ----------------------------------------------------------


def test_the_panel_links_to_the_settings(auth_client):
    html = auth_client.get("/notifications/panel").text

    assert '<a href="/settings"' in html and "Notification settings" in html


def test_login_after_a_settings_change_keeps_the_zone(client, session, user):
    log_in(client)
    save(client, timezone="Asia/Tokyo")
    client.post("/logout", data={"csrf_token": csrf_token(client)})

    log_in(client)

    assert zone_of(session) == "Asia/Tokyo"
