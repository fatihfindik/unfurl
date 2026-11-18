"""The scheduler (app/scheduler.py, D-103): when it starts, which jobs it has, and what
the jobs do. The jobs are called directly; the timer itself is never waited for."""

import datetime as dt

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlmodel import Session, select

from app import scheduler
from app.config import Settings, get_settings
from app.main import create_app
from app.models import Notification
from app.services import application_service as apps
from app.services import notification_service


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, app_env="development", **overrides)


# --- Starting and stopping -----------------------------------------------------------------


def test_switched_off_it_does_not_start():
    assert scheduler.start(settings(scheduler_enabled=False)) is None


def test_the_test_settings_switch_it_off():
    # tests/conftest.py sets SCHEDULER_ENABLED=false for every test.
    assert get_settings().scheduler_enabled is False


def test_switched_on_it_starts_and_stops():
    running = scheduler.start(settings(scheduler_enabled=True))
    try:
        assert running is not None and running.running
    finally:
        scheduler.shutdown(running)
    assert not running.running


def test_shutdown_without_a_scheduler_does_nothing():
    scheduler.shutdown(None)


def test_the_jobs_and_their_times():
    built = scheduler.build(settings(default_timezone="Europe/Vienna"))
    built.start(paused=True)  # the job defaults are applied on start; paused: nothing runs
    try:
        jobs = {job.id: job for job in built.get_jobs()}
    finally:
        built.shutdown(wait=False)

    assert set(jobs) == {"generate_notifications", "purge_notifications"}
    generate = {field.name: str(field) for field in jobs["generate_notifications"].trigger.fields}
    assert generate["minute"] == "*/15"  # :00, :15, :30, :45 (D-109)
    purge = jobs["purge_notifications"].trigger
    fields = {field.name: str(field) for field in purge.fields}
    assert (fields["hour"], fields["minute"]) == ("3", "0")
    assert str(purge.timezone) == "Europe/Vienna"
    assert str(built.timezone) == "Europe/Vienna"
    for job in jobs.values():
        assert job.max_instances == 1 and job.coalesce is True


def test_the_app_starts_and_stops_it_with_its_lifespan(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "start", lambda s: calls.append("start") or "running")
    monkeypatch.setattr(scheduler, "shutdown", lambda running: calls.append(("stop", running)))

    with TestClient(create_app(settings(scheduler_enabled=True))) as client:
        assert client.get("/login").status_code == 200
        assert calls == ["start"]

    assert calls == ["start", ("stop", "running")]


def test_an_app_built_in_tests_never_starts_jobs(monkeypatch):
    def fail(self, *args, **kwargs):
        raise AssertionError("the scheduler must not start in tests")

    monkeypatch.setattr(BackgroundScheduler, "start", fail)

    with TestClient(create_app(get_settings())) as client:
        assert client.get("/login").status_code == 200


# --- The jobs ------------------------------------------------------------------------------


def test_the_generate_job_writes_due_reminders(engine, session, user):
    with freeze_time("2026-11-06 09:00:00"):
        job = apps.create(session, user, company="Acme", job_title="PM")
        apps.move(session, user, job, "applied")  # applied on 6 November

    with freeze_time("2026-11-16 09:00:00"):  # 10 days later
        scheduler.generate_notifications(engine)
        scheduler.generate_notifications(engine)  # the next run writes nothing new

    notes = session.exec(select(Notification)).all()
    assert [(n.type, n.body) for n in notes] == [("follow_up", "Acme · no reply for 10 days")]


def test_the_purge_job_deletes_old_read_ones(engine, session, user):
    with freeze_time("2026-10-01 09:00:00"):
        session.add(
            Notification(
                user_id=user.id,
                type="follow_up",
                title="T",
                body="B",
                link_url="/",
                dedupe_key="old",
                read_at=dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC),
            )
        )
        session.commit()

    with freeze_time("2026-11-16 02:00:00"):  # 03:00 in Vienna
        scheduler.purge_notifications(engine)

    with Session(engine) as fresh:
        assert fresh.exec(select(Notification)).all() == []


def test_a_failing_purge_is_logged(engine, monkeypatch, caplog):
    def broken(session, now):
        raise RuntimeError("database gone")

    monkeypatch.setattr(notification_service, "purge", broken)

    scheduler.purge_notifications(engine)  # does not raise

    assert "Purging notifications failed" in caplog.text


@pytest.mark.parametrize("job", ["generate_notifications", "purge_notifications"])
def test_jobs_use_the_app_database_by_default(monkeypatch, job):
    used = []

    class FakeSession:
        def __init__(self, engine):
            used.append(engine)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(scheduler, "Session", FakeSession)
    monkeypatch.setattr(notification_service, "generate_due", lambda s, now: 0)
    monkeypatch.setattr(notification_service, "purge", lambda s, now: None)

    getattr(scheduler, job)()

    assert used == [scheduler.db.engine]
