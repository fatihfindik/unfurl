"""M11b error and authorization cases not covered by the other M11b test files:
logged-out htmx requests, another user's data, oversized and odd input, rate limits,
and htmx vs plain requests (Settings, the reminder turn-off link, login/register)."""

import datetime as dt
import re

import pytest
from sqlmodel import select

from app.models import LearningGoal, LearningTask, NotificationPreference, User
from app.services import auth_service, reminder_email
from app.services.auth_service import create_user
from tests.helpers import USER_EMAIL, USER_PASSWORD, csrf_token, log_in

HTMX = {"HX-Request": "true"}
HUGE = "x" * 10_000

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

OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "another long password"


def save(client, headers=HTMX, **changes):
    data = GOOD | changes
    data = {key: value for key, value in data.items() if value is not None}
    return client.post(
        "/settings/notifications",
        data=data | {"csrf_token": csrf_token(client)},
        headers=headers,
        follow_redirects=False,
    )


def prefs_of(session, user) -> NotificationPreference | None:
    session.expire_all()
    return session.exec(
        select(NotificationPreference).where(NotificationPreference.user_id == user.id)
    ).first()


def fresh(session, user) -> User:
    session.expire_all()
    return session.get(User, user.id)


@pytest.fixture
def other(session) -> User:
    """A second user, with their own saved settings and time zone."""
    other = create_user(session, email=OTHER_EMAIL, password=OTHER_PASSWORD, full_name="Omar")
    other.timezone = "Asia/Tokyo"
    session.add(other)
    session.add(
        NotificationPreference(
            user_id=other.id, study_reminder_time=dt.time(21, 30), rest_days=["mon"]
        )
    )
    session.commit()
    return other


def off_post(client, token):
    return client.post("/reminders/off", data={"token": token, "csrf_token": csrf_token(client)})


# --- Logged out, htmx ---------------------------------------------------------------------


def test_settings_page_htmx_logged_out_asks_for_a_full_page_login(client):
    response = client.get(
        "/settings",
        headers=HTMX | {"HX-Current-URL": "http://testserver/settings"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/login?next=/settings"


def test_saving_htmx_logged_out_redirects_and_saves_nothing(client, session):
    response = client.post(
        "/settings/notifications",
        data=GOOD,
        headers=HTMX
        | {"X-CSRF-Token": csrf_token(client), "HX-Current-URL": "http://testserver/settings"},
        follow_redirects=False,
    )

    assert response.headers["HX-Redirect"] == "/login?next=/settings"
    assert session.exec(select(NotificationPreference)).first() is None


def test_a_wrong_csrf_token_is_refused(auth_client, session, user):
    response = auth_client.post(
        "/settings/notifications", data=GOOD | {"csrf_token": "not-the-token"}, headers=HTMX
    )

    assert response.status_code == 403
    assert prefs_of(session, user) is None


# --- Another user's data ------------------------------------------------------------------


def test_the_page_shows_only_the_own_settings(auth_client, other):
    html = auth_client.get("/settings").text

    assert re.search(r'<option value="19:00" selected>', html)  # own default, not 21:30
    assert re.search(r'<option value="Europe/Vienna" selected>', html)  # not Tokyo
    assert 'value="mon" checked' not in html


def test_a_sent_user_id_is_ignored(auth_client, session, user, other):
    # The user always comes from the session, never from the form.
    save(auth_client, user_id=str(other.id), study_reminder_time="06:00")

    assert prefs_of(session, user).study_reminder_time == dt.time(6, 0)
    assert prefs_of(session, other).study_reminder_time == dt.time(21, 30)
    assert fresh(session, other).timezone == "Asia/Tokyo"


def test_the_second_user_saves_only_their_own(client, session, user, other):
    log_in(client, OTHER_EMAIL, OTHER_PASSWORD)

    save(client, timezone="Asia/Tokyo", study_reminder_time="22:00", rest_days=None)

    assert prefs_of(session, other).study_reminder_time == dt.time(22, 0)
    assert prefs_of(session, user) is None
    assert fresh(session, user).timezone is None


def test_the_learning_card_ignores_other_users_tasks(auth_client, session, other):
    goal = LearningGoal(user_id=other.id, title="Go")
    session.add(goal)
    session.commit()
    today = dt.date.today()
    session.add(LearningTask(user_id=other.id, goal_id=goal.id, title="Tour", due_date=today))
    session.commit()

    html = auth_client.get("/learning").text

    assert "No tasks planned" in html
    assert ", 21:30" not in html  # not the other user's reminder time


def test_a_logged_in_user_cannot_turn_off_by_session_alone(auth_client, session, user):
    # The token decides whose emails stop; a session without a token changes nothing.
    response = auth_client.post("/reminders/off", data={"csrf_token": csrf_token(auth_client)})

    assert response.status_code == 400
    assert prefs_of(session, user) is None


# --- Invalid and oversized input ----------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"timezone": HUGE}, "timezone"),
        ({"timezone": "Europe/Vienna\x00"}, "timezone"),
        ({"timezone": "../../etc/passwd"}, "timezone"),
        ({"timezone": "europe/vienna"}, "timezone"),  # names are case sensitive
        ({"study_reminder_time": HUGE}, "study_reminder_time"),
        ({"study_reminder_time": "7:30"}, "study_reminder_time"),
        ({"study_reminder_time": "07:30:00"}, "study_reminder_time"),
        ({"rest_days": [HUGE]}, "rest-days"),
        ({"rest_days": ["Mon"]}, "rest-days"),
    ],
)
def test_bad_or_huge_values_are_refused_and_nothing_is_saved(
    auth_client, session, user, changes, field
):
    response = save(auth_client, **changes)

    assert response.status_code == 200
    assert f'id="setting-{field}-error"' in response.text
    assert "HX-Trigger" not in response.headers and "HX-Refresh" not in response.headers
    assert HUGE not in response.text  # a huge value is not echoed back
    assert prefs_of(session, user) is None
    assert fresh(session, user).timezone is None


def test_an_error_keeps_the_saved_zone_and_settings(auth_client, session, user):
    save(auth_client, timezone="Asia/Tokyo", study_reminder_time="21:00")

    save(auth_client, timezone=HUGE, study_reminder_time="06:00")

    assert fresh(session, user).timezone == "Asia/Tokyo"
    assert prefs_of(session, user).study_reminder_time == dt.time(21, 0)


def test_an_empty_form_is_refused(auth_client, session, user):
    response = auth_client.post(
        "/settings/notifications", data={"csrf_token": csrf_token(auth_client)}, headers=HTMX
    )

    assert response.status_code == 200
    assert 'id="setting-timezone-error"' in response.text
    assert 'id="setting-study_reminder_time-error"' in response.text
    assert prefs_of(session, user) is None


def test_a_repeated_rest_day_is_saved_once(auth_client, session, user):
    save(auth_client, rest_days=["mon", "mon", "sun", "mon"])

    assert prefs_of(session, user).rest_days == ["mon", "sun"]


@pytest.mark.parametrize("value", ["on", "true", "1", "YES", HUGE])
def test_a_switch_is_on_only_with_yes(auth_client, session, user, value):
    save(auth_client, study_reminder_email=value)

    assert prefs_of(session, user).study_reminder_email is False


@pytest.mark.parametrize("token", [HUGE, "a.b.c", "%00", " "])
def test_a_huge_or_odd_token_is_not_valid(client, token):
    page = client.get("/reminders/off", params={"token": token})
    post = off_post(client, token)

    assert page.status_code == 400 and post.status_code == 400
    assert HUGE not in page.text
    assert page.headers["Referrer-Policy"] == "no-referrer"


def test_no_token_at_all_is_not_valid(client):
    response = client.get("/reminders/off")

    assert response.status_code == 400
    assert "This link is no longer valid" in response.text


def test_a_token_in_the_query_of_the_post_is_not_used(client, session, user):
    # The POST reads the token from the form only.
    token = reminder_email.make_off_token(user)

    response = client.post(
        "/reminders/off", params={"token": token}, data={"csrf_token": csrf_token(client)}
    )

    assert response.status_code == 400
    assert prefs_of(session, user) is None


def test_a_token_for_a_deleted_user_is_not_valid(client, session, user):
    token = reminder_email.make_off_token(user)
    session.delete(user)
    session.commit()

    assert client.get("/reminders/off", params={"token": token}).status_code == 400
    assert off_post(client, token).status_code == 400


@pytest.mark.parametrize(
    "data",
    [
        {"user_id": "1", "email": USER_EMAIL},  # a string id
        {"email": USER_EMAIL},
        {"user_id": None, "email": USER_EMAIL},
    ],
)
def test_a_signed_token_with_odd_data_is_not_valid(client, session, user, data):
    # Signed with the right key and salt, but not what make_off_token writes.
    token = auth_service.token_serializer(reminder_email.OFF_TOKEN_SALT).dumps(data)

    assert client.get("/reminders/off", params={"token": token}).status_code == 400
    assert off_post(client, token).status_code == 400
    assert prefs_of(session, user) is None


def test_a_signed_list_token_is_not_valid(client, user):
    token = auth_service.token_serializer(reminder_email.OFF_TOKEN_SALT).dumps([user.id])

    assert client.get("/reminders/off", params={"token": token}).status_code == 400


def test_another_sessions_csrf_token_is_refused(client, session, user):
    token = reminder_email.make_off_token(user)
    stolen = csrf_token(client)
    client.cookies.clear()  # a new browser session with its own CSRF token

    response = client.post("/reminders/off", data={"token": token, "csrf_token": stolen})

    assert response.status_code == 403
    assert prefs_of(session, user) is None


def login_with_zone(client, timezone):
    return client.post(
        "/login",
        data={
            "email": USER_EMAIL,
            "password": USER_PASSWORD,
            "timezone": timezone,
            "csrf_token": csrf_token(client),
        },
        follow_redirects=False,
    )


@pytest.mark.parametrize("zone", [HUGE, "Europe/Vienna\x00", "<script>"])
def test_login_with_a_huge_or_odd_zone_still_logs_in(client, session, user, zone):
    response = login_with_zone(client, zone)

    assert response.status_code == 303
    assert fresh(session, user).timezone is None
    assert client.get("/settings").status_code == 200


@pytest.mark.parametrize("zone", [HUGE, "Europe/Vienna\x00"])
def test_register_with_a_huge_or_odd_zone_still_registers(client, session, outbox, zone):
    response = client.post(
        "/register",
        data={
            "full_name": "Ada Lovelace",
            "email": "ada@example.com",
            "password": "x" * 12,
            "privacy": "yes",
            "timezone": zone,
            "csrf_token": csrf_token(client),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    user = session.exec(select(User).where(User.email == "ada@example.com")).one()
    assert user.timezone is None


def test_a_failed_login_does_not_save_the_zone(client, session, user):
    response = client.post(
        "/login",
        data={
            "email": USER_EMAIL,
            "password": "wrong password",
            "timezone": "Asia/Tokyo",
            "csrf_token": csrf_token(client),
        },
        follow_redirects=False,
    )

    assert response.status_code != 303
    assert fresh(session, user).timezone is None


# --- Rate limits --------------------------------------------------------------------------


def test_saving_settings_is_rate_limited(auth_client, session, user):
    codes = [save(auth_client).status_code for _ in range(30)]
    last = save(auth_client, study_reminder_time="22:00")

    assert codes == [200] * 30
    assert last.status_code == 429
    assert prefs_of(session, user).study_reminder_time == dt.time(7, 30)  # not saved


def test_opening_the_turn_off_link_is_rate_limited(client, user):
    token = reminder_email.make_off_token(user)
    codes = [client.get("/reminders/off", params={"token": token}).status_code for _ in range(31)]

    assert codes[:30] == [200] * 30
    assert codes[30] == 429


def test_the_turn_off_button_is_rate_limited(client, session, user):
    # Ten guesses with bad tokens use up the limit; a good token is refused after that.
    codes = [off_post(client, f"guess-{n}").status_code for n in range(10)]
    last = off_post(client, reminder_email.make_off_token(user))

    assert codes == [400] * 10
    assert last.status_code == 429
    assert prefs_of(session, user) is None


# --- htmx vs plain requests ---------------------------------------------------------------


def test_a_plain_save_saves_like_an_htmx_one(auth_client, session, user):
    response = save(auth_client, headers={}, study_reminder_time="20:00")

    assert response.status_code == 200
    assert prefs_of(session, user).study_reminder_time == dt.time(20, 0)


def test_a_plain_save_with_errors_saves_nothing(auth_client, session, user):
    response = save(auth_client, headers={}, timezone="Mars/Base")

    assert response.status_code == 200
    assert 'id="setting-timezone-error"' in response.text
    assert prefs_of(session, user) is None


def test_the_learning_region_has_the_reminder_card(auth_client):
    # A tab switch (htmx) redraws only the region: the card must still be there.
    html = auth_client.get("/learning", headers=HTMX).text

    assert 'data-card="reminder"' in html
    assert "<html" not in html
