"""Each user's own time zone (D-108, D-109): the clock, the middleware, login and
register, and moving "all day" dates when the zone changes."""

import datetime as dt

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlmodel import select
from starlette.middleware.sessions import SessionMiddleware

from app.main import UserTimezone
from app.models import User
from app.models.base import as_utc
from app.services import application_service as apps
from app.services import clock
from app.services.auth_service import SESSION_TIMEZONE, create_user, current_user
from tests.helpers import USER_EMAIL, csrf_token

UTC = dt.UTC


def fresh_user(session) -> User:
    session.expire_all()
    return session.exec(select(User).where(User.email == USER_EMAIL)).one()


# --- The clock ------------------------------------------------------------------------------


def test_without_a_zone_the_default_is_used():
    assert clock.tz().key == "Europe/Vienna"


def test_use_timezone_changes_the_zone_and_restores_it():
    stored = dt.datetime(2026, 11, 18, 22, 30, tzinfo=UTC)

    with clock.use_timezone("Europe/Istanbul"):  # UTC+3
        assert clock.local(stored).date() == dt.date(2026, 11, 19)
        assert clock.to_utc(dt.date(2026, 11, 19)) == dt.datetime(2026, 11, 18, 21, 0, tzinfo=UTC)
        with clock.use_timezone("America/New_York"):  # UTC-5
            assert clock.local(stored).time() == dt.time(17, 30)
        assert clock.tz().key == "Europe/Istanbul"
    assert clock.tz().key == "Europe/Vienna"


def test_an_unknown_zone_means_the_default():
    for name in ("Mars/Olympus", "", None, "../../etc/passwd", "europe/vienna"):
        with clock.use_timezone(name):
            assert clock.tz().key == "Europe/Vienna"
    assert clock.valid_zone("Europe/Istanbul") == "Europe/Istanbul"
    assert clock.valid_zone(123) is None


# --- The middleware -------------------------------------------------------------------------


def zone_app() -> TestClient:
    """A tiny app with the same two middlewares as app/main.py, in the same order."""
    app = FastAPI()

    @app.get("/set")
    def set_zone(request: Request, name: str):
        request.session[SESSION_TIMEZONE] = name
        return {}

    @app.get("/zone")
    def zone():  # a sync route: runs on the thread pool
        return {"zone": clock.tz().key}

    @app.get("/zone-async")
    async def zone_async():
        return {"zone": clock.tz().key}

    app.add_middleware(UserTimezone)
    app.add_middleware(SessionMiddleware, secret_key="test")
    return TestClient(app)


def test_the_middleware_uses_the_zone_from_the_session():
    client = zone_app()
    assert client.get("/zone").json() == {"zone": "Europe/Vienna"}

    client.get("/set", params={"name": "Europe/Istanbul"})

    assert client.get("/zone").json() == {"zone": "Europe/Istanbul"}
    assert client.get("/zone-async").json() == {"zone": "Europe/Istanbul"}
    assert clock.tz().key == "Europe/Vienna"  # nothing leaks out of the request


def test_the_middleware_ignores_a_bad_zone_in_the_session():
    client = zone_app()
    client.get("/set", params={"name": "Not/AZone"})

    assert client.get("/zone").json() == {"zone": "Europe/Vienna"}


# --- Register and login ---------------------------------------------------------------------


def register(client, timezone):
    return client.post(
        "/register",
        data={
            "full_name": "Ada Lovelace",
            "email": "ada@example.com",
            "password": "x" * 12,
            "privacy": "yes",
            "timezone": timezone,
            "csrf_token": csrf_token(client),
        },
        follow_redirects=False,
    )


def test_register_saves_the_browsers_zone(client, session, outbox):
    assert register(client, "Europe/Istanbul").status_code == 303

    user = session.exec(select(User).where(User.email == "ada@example.com")).one()
    assert user.timezone == "Europe/Istanbul"


def test_register_ignores_an_unknown_zone(client, session, outbox):
    assert register(client, "Nowhere/Town").status_code == 303

    user = session.exec(select(User).where(User.email == "ada@example.com")).one()
    assert user.timezone is None


def login_with_zone(client, timezone):
    return client.post(
        "/login",
        data={
            "email": USER_EMAIL,
            "password": "correct horse battery",
            "timezone": timezone,
            "csrf_token": csrf_token(client),
        },
        follow_redirects=False,
    )


def test_login_saves_the_zone_only_if_none_is_saved(client, session, user):
    assert login_with_zone(client, "Europe/Istanbul").status_code == 303
    assert fresh_user(session).timezone == "Europe/Istanbul"

    client.post("/logout", data={"csrf_token": csrf_token(client)})
    login_with_zone(client, "America/New_York")  # another browser, another zone

    assert fresh_user(session).timezone == "Europe/Istanbul"  # Settings' choice stays


def test_login_ignores_an_unknown_zone(client, session, user):
    assert login_with_zone(client, "Bad/Zone").status_code == 303
    assert fresh_user(session).timezone is None


def test_the_login_and_register_forms_have_the_hidden_field(client):
    for page in ("/login", "/register"):
        assert 'name="timezone"' in client.get(page).text


# --- A change on another device -------------------------------------------------------------


def test_current_user_puts_a_changed_zone_into_the_session(session, user):
    user.timezone = "Asia/Tokyo"
    session.add(user)
    session.commit()
    scope = {
        "type": "http",
        "session": {"user_id": user.id, "session_version": 0, SESSION_TIMEZONE: None},
        "headers": [],
    }
    request = Request(scope)

    assert current_user(request, session) is not None
    assert request.session[SESSION_TIMEZONE] == "Asia/Tokyo"


# --- All-day dates move with the zone (D-108, B1) ------------------------------------------


def test_all_day_dates_move_to_the_same_day_in_the_new_zone(session, user):
    job = apps.create(session, user, company="Acme", job_title="PM")
    all_day = clock.to_utc(dt.date(2026, 11, 20))  # 00:00 in Vienna
    timed = clock.to_utc(dt.date(2026, 11, 21), dt.time(10, 0))
    job.interview_at, job.next_action_at = all_day, timed
    session.add(job)
    session.commit()

    apps.move_all_day_times(session, user, None, "Europe/Istanbul")
    session.commit()
    session.refresh(job)

    with clock.use_timezone("Europe/Istanbul"):
        assert clock.is_start_of_day(job.interview_at)
        assert clock.local(job.interview_at).date() == dt.date(2026, 11, 20)
    assert as_utc(job.next_action_at) == timed  # a real time is a moment: unchanged


def test_moving_only_touches_the_users_own_jobs(session, user):
    other = create_user(session, email="o@example.com", password="x" * 12, full_name="O")
    theirs = apps.create(session, other, company="Other", job_title="PM")
    theirs.interview_at = clock.to_utc(dt.date(2026, 11, 20))
    session.add(theirs)
    session.commit()
    before = theirs.interview_at

    apps.move_all_day_times(session, user, None, "Asia/Tokyo")
    session.commit()
    session.refresh(theirs)

    assert as_utc(theirs.interview_at) == as_utc(before)


def test_the_first_login_zone_moves_existing_all_day_dates(client, session, user):
    job = apps.create(session, user, company="Acme", job_title="PM")
    job.interview_at = clock.to_utc(dt.date(2026, 11, 20))
    session.add(job)
    session.commit()

    login_with_zone(client, "Asia/Tokyo")
    session.refresh(job)

    with clock.use_timezone("Asia/Tokyo"):
        assert clock.local(job.interview_at) == dt.datetime(
            2026, 11, 20, tzinfo=clock.tz()
        ).astimezone(clock.tz())
