"""Fixes from the M11a security review: no personal data in logs (M1), the bell's
background requests don't touch the session cookie (L1, L2), unread notifications
don't stay for ever (L3)."""

import datetime as dt

from sqlalchemy.exc import OperationalError
from sqlmodel import select

from app import db
from app.models import Notification
from app.services import application_service as apps
from app.services import notification_service as notes

NOW = dt.datetime(2026, 11, 18, 9, 0, tzinfo=dt.UTC)


def test_the_app_engine_hides_query_values_in_errors():
    assert db.engine.hide_parameters is True


def test_a_database_error_logs_no_company(session, user, monkeypatch, caplog):
    job = apps.create(session, user, company="Secret Employer GmbH", job_title="PM")
    apps.move(session, user, job, "applied")
    job.applied_at = dt.date(2026, 11, 6)
    session.add(job)
    session.commit()

    def locked(*args, **kwargs):
        raise OperationalError(
            "INSERT INTO notification", {"body": "Secret Employer GmbH"}, Exception("locked")
        )

    monkeypatch.setattr(session, "commit", locked)

    notes.generate_due(session, NOW)

    assert f"user {user.id} failed: OperationalError" in caplog.text
    assert "Secret Employer" not in caplog.text


def test_background_gets_send_no_session_cookie(auth_client):
    for url in ("/notifications/badge", "/notifications/panel"):
        response = auth_client.get(url, headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "set-cookie" not in response.headers, url


def test_the_cookie_is_dropped_even_when_the_session_changes(auth_client, monkeypatch):
    # Rendering the panel could store a new CSRF token in the session; its answer
    # still sends no cookie, so a sign-out in another tab can't be undone by it.
    from app import security

    real = security.get_csrf_token

    def new_token(request):
        request.session["touched"] = True  # the session changes during the request
        return real(request)

    monkeypatch.setattr("app.templating.get_csrf_token", new_token)

    response = auth_client.get("/notifications/panel")

    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    # The same change on a normal page does send the cookie.
    assert "set-cookie" in auth_client.get("/").headers


def test_old_unread_notifications_are_purged_after_90_days(session, user):
    for key, days in (("fresh", 89), ("old", 91)):
        created = NOW - dt.timedelta(days=days)
        session.add(
            Notification(
                user_id=user.id, type="follow_up", title="T", body="B", link_url="/",
                dedupe_key=key, created_at=created,
            )
        )  # fmt: skip
    session.commit()

    notes.purge(session, NOW)

    session.expire_all()
    assert [n.dedupe_key for n in session.exec(select(Notification))] == ["fresh"]
