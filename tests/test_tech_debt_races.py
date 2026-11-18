"""Small fixes from earlier security reviews: #15 (forgot password does the same work
with and without an account), #16 (a reset link can't be used twice at the same
moment), #22 (the 50-row profile limit locks the profile row)."""

import datetime as dt

from sqlmodel import Session, select

from app.models import Experience, User
from app.routers import auth as auth_routes
from app.services import auth_service, email_service, profile_service
from tests.helpers import USER_EMAIL
from tests.test_password_reset import forgot, reset

START = dt.date(2020, 1, 1)

# --- #15 ------------------------------------------------------------------------------------


def count_work(monkeypatch) -> dict:
    """Count the signing and rendering the forgot-password route does."""
    calls = {"token": 0, "render": 0}
    make_token, render = auth_routes.make_reset_token, email_service.render_email

    def counted_token(user):
        calls["token"] += 1
        return make_token(user)

    def counted_render(*args, **kwargs):
        calls["render"] += 1
        return render(*args, **kwargs)

    monkeypatch.setattr(auth_routes, "make_reset_token", counted_token)
    monkeypatch.setattr(email_service, "render_email", counted_render)
    return calls


def test_forgot_password_does_the_same_work_without_an_account(client, user, outbox, monkeypatch):
    calls = count_work(monkeypatch)

    known = forgot(client, USER_EMAIL)
    with_account = dict(calls)
    unknown = forgot(client, "nobody@example.com")

    assert with_account == {"token": 1, "render": 1}
    assert calls == {"token": 2, "render": 2}  # the same again, for the stand-in
    assert known.text.count("If an account exists") == unknown.text.count("If an account exists")
    assert [email.to for email in outbox] == [USER_EMAIL]  # only the real one is sent


def test_the_stand_in_is_never_saved(client, session, outbox):
    forgot(client, "nobody@example.com")

    assert session.exec(select(User)).all() == []


def test_forgot_password_still_respects_the_email_limit(client, user, outbox):
    for _ in range(5):
        forgot(client, USER_EMAIL)

    assert len(outbox) == 3  # MAX_EMAILS per address per hour


# --- #16 ------------------------------------------------------------------------------------


def test_a_reset_link_works_once_even_at_the_same_moment(engine, user):
    # Two requests read the user (same password hash) before either one saves.
    with Session(engine) as first, Session(engine) as second:
        one = first.get(User, user.id)
        two = second.get(User, user.id)

        assert auth_service.reset_password(first, one, "first new password") is True
        assert auth_service.reset_password(second, two, "second new password") is False

    with Session(engine) as check:
        saved = check.get(User, user.id)
        assert auth_service.verify_password("first new password", saved.password_hash)
        assert saved.session_version == 1 and saved.is_verified


def test_the_route_says_the_link_is_invalid_when_it_lost_the_race(client, user, monkeypatch):
    token = auth_service.make_reset_token(user)
    monkeypatch.setattr(auth_routes, "reset_password", lambda *args: False)

    response = reset(client, token)

    assert response.status_code == 400
    assert "invalid" in response.text.lower()


def test_a_normal_reset_still_works(client, user):
    token = auth_service.make_reset_token(user)

    response = reset(client, token)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?reset=1"
    assert reset(client, token).status_code == 400  # used: the fingerprint changed


# --- #22 ------------------------------------------------------------------------------------


def test_adding_a_row_locks_the_profile_first(session, user, monkeypatch):
    profile = profile_service.get_or_create_profile(session, user)
    statements = []
    exec_ = session.exec

    def recording_exec(statement, *args, **kwargs):
        statements.append(statement)
        return exec_(statement, *args, **kwargs)

    monkeypatch.setattr(session, "exec", recording_exec)
    profile_service.add_item(
        session, profile, Experience, {"title": "PM", "company": "Acme", "start_date": START}
    )

    first = statements[0]
    assert first._for_update_arg is not None  # SELECT ... FOR UPDATE on Postgres
    assert "profile" in str(first).lower()


def test_the_limit_still_holds(session, user):
    profile = profile_service.get_or_create_profile(session, user)
    for number in range(profile_service.MAX_ITEMS_PER_SECTION):
        profile_service.add_item(
            session,
            profile,
            Experience,
            {"title": f"Job {number}", "company": "A", "start_date": START},
        )

    try:
        profile_service.add_item(
            session, profile, Experience, {"title": "One more", "company": "A", "start_date": START}
        )
    except profile_service.TooManyItems:
        pass
    else:
        raise AssertionError("the 51st row was added")
