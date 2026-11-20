"""Writing reminders once, cleaning up and reading them (D-100, D-103, D-104).

M11a acceptance: the same notification is never created twice, also after the purge
and the 200 cap."""

import datetime as dt

import pytest
from sqlmodel import Session, select

from app.models import Notification
from app.services import application_service as apps
from app.services import clock
from app.services import notification_service as notes
from app.services.auth_service import create_user

# Wednesday 18 November 2026, 10:00 in Vienna.
NOW = dt.datetime(2026, 11, 18, 9, 0, tzinfo=dt.UTC)
TODAY = dt.date(2026, 11, 18)


def ago(days: int) -> dt.date:
    return TODAY - dt.timedelta(days=days)


def applied_job(session, user, company="Acme", days=12):
    job = apps.create(session, user, company=company, job_title="PM")
    apps.move(session, user, job, "applied")
    job.applied_at = ago(days)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def rows(session, user=None) -> list[Notification]:
    session.expire_all()
    query = select(Notification)
    if user is not None:
        query = query.where(Notification.user_id == user.id)
    return list(session.exec(query))


@pytest.fixture
def other(session):
    return create_user(session, email="other@example.test", password="x" * 12, full_name="O")


# --- Written once (acceptance) -------------------------------------------------------------


def test_a_reminder_is_written_once(session, user):
    job = applied_job(session, user)

    assert notes.generate_due(session, NOW) == 1
    assert notes.generate_due(session, NOW) == 0
    assert notes.generate_due(session, NOW + dt.timedelta(minutes=15)) == 0

    [note] = rows(session)
    assert (note.type, note.application_id, note.link_url, note.read_at) == (
        "follow_up",
        job.id,
        f"/applications/{job.id}",
        None,
    )
    assert note.dedupe_key == f"follow_up:{job.id}:2026-11-16"


def test_not_written_again_after_the_purge(session, user):
    applied_job(session, user, days=10)
    notes.generate_due(session, NOW)
    notes.mark_all_read(session, user, NOW)
    # The row gets the real time as created_at; the test's clock is NOW.
    for note in rows(session):
        note.created_at = NOW
        session.add(note)
    session.commit()

    later = NOW + dt.timedelta(days=31)
    notes.purge(session, later)
    assert rows(session) == []

    # Every day after the purge: the step's day is out of the window now.
    for day in range(0, 10):
        assert notes.generate_due(session, later + dt.timedelta(days=day)) == 0


def test_a_new_follow_up_step_is_a_new_reminder(session, user):
    job = applied_job(session, user)
    notes.generate_due(session, NOW)

    # The user marked a thank-you note done: a follow-up 5 days later, set by hand.
    job.next_action_manual = True
    job.next_action_type = "follow_up"
    job.next_action_at = clock.to_utc(TODAY + dt.timedelta(days=5))
    session.add(job)
    session.commit()

    assert notes.generate_due(session, NOW + dt.timedelta(days=5)) == 1
    assert len(rows(session)) == 2


def test_two_users_get_their_own(session, user, other):
    applied_job(session, user, "Mine")
    applied_job(session, other, "Theirs")

    assert notes.generate_due(session, NOW) == 2
    assert [n.body.split(" · ")[0] for n in rows(session, user)] == ["Mine"]
    assert [n.body.split(" · ")[0] for n in rows(session, other)] == ["Theirs"]


def test_a_clash_for_one_user_keeps_the_others(engine, session, user, other, monkeypatch):
    mine = applied_job(session, user, "Mine")
    applied_job(session, other, "Theirs")
    key = f"follow_up:{mine.id}:2026-11-16"
    real_short = notes._short
    clashed = []

    def short_after_another_run(text, limit):
        # Another run writes the same key after this run checked for it, from its
        # own session: this run's insert then hits the unique constraint.
        if not clashed:
            clashed.append(True)
            with Session(engine) as other_run:
                other_run.add(
                    Notification(
                        user_id=user.id, type="follow_up", title="x", body="x",
                        link_url="/", dedupe_key=key,
                    )
                )  # fmt: skip
                other_run.commit()
        return real_short(text, limit)

    monkeypatch.setattr(notes, "_short", short_after_another_run)

    assert notes.generate_due(session, NOW) == 1  # only the other user's

    assert [n.title for n in rows(session, user)] == ["x"]  # the other run's, no second
    assert len(rows(session, other)) == 1


def test_an_error_for_one_user_keeps_the_others(session, user, other, monkeypatch, caplog):
    applied_job(session, user, "Mine")
    applied_job(session, other, "Theirs")
    real = notes.due_for_user

    def broken_for_user(session_, user_, now):
        if user_.id == user.id:
            raise RuntimeError("broken")
        return real(session_, user_, now)

    monkeypatch.setattr(notes, "due_for_user", broken_for_user)

    assert notes.generate_due(session, NOW) == 1
    assert rows(session, user) == [] and len(rows(session, other)) == 1
    assert f"user {user.id}" in caplog.text
    assert "Mine" not in caplog.text and user.email not in caplog.text


def test_long_company_names_are_cut(session, user):
    applied_job(session, user, "C" * 300)

    notes.generate_due(session, NOW)

    [note] = rows(session)
    assert len(note.body) == 300 and note.body.endswith("…")


def test_deleting_the_application_deletes_its_notifications(session, user):
    job = applied_job(session, user)
    notes.generate_due(session, NOW)

    session.delete(job)
    session.commit()

    assert rows(session) == []


# --- Purge and the 200 cap (D-104) ---------------------------------------------------------


def add_note(session, user, number, read=False, days_ago=0):
    created = NOW - dt.timedelta(days=days_ago, minutes=number)
    session.add(
        Notification(
            user_id=user.id,
            type="follow_up",
            title="T",
            body="B",
            link_url="/",
            dedupe_key=f"k{number}",
            read_at=created if read else None,
            created_at=created,
        )
    )


def test_purge_deletes_only_old_read_ones(session, user):
    add_note(session, user, 1, read=True, days_ago=31)
    add_note(session, user, 2, read=True, days_ago=29)
    add_note(session, user, 3, read=False, days_ago=60)
    session.commit()

    notes.purge(session, NOW)

    assert sorted(n.dedupe_key for n in rows(session)) == ["k2", "k3"]


def test_the_cap_deletes_the_oldest_read_ones_only(session, user, other):
    for number in range(150):
        add_note(session, user, number, read=False)
    for number in range(150, 260):
        add_note(session, user, number, read=True)  # higher number = older
    add_note(session, other, 1, read=True)
    session.commit()

    notes.purge(session, NOW)

    mine = rows(session, user)
    assert len(mine) == 200
    assert sum(n.read_at is None for n in mine) == 150
    kept_read = sorted(int(n.dedupe_key[1:]) for n in mine if n.read_at is not None)
    assert kept_read == list(range(150, 200))  # the 50 newest read ones
    assert len(rows(session, other)) == 1


def test_the_cap_never_deletes_unread_ones(session, user):
    for number in range(210):
        add_note(session, user, number, read=False)
    session.commit()

    notes.purge(session, NOW)

    assert len(rows(session, user)) == 210


def test_not_written_again_after_the_cap(session, user):
    job = applied_job(session, user)
    notes.generate_due(session, NOW)
    notes.mark_all_read(session, user, NOW)
    for number in range(210):
        add_note(session, user, number, read=False)  # newer than the reminder
    session.commit()

    notes.purge(session, NOW)
    assert not [n for n in rows(session, user) if n.application_id == job.id]

    assert notes.generate_due(session, NOW + dt.timedelta(days=8)) == 0


# --- Reading and marking -------------------------------------------------------------------


def test_unread_count_latest_and_mark_read(session, user, other):
    for number in range(25):
        add_note(session, user, number)
    add_note(session, other, 1)
    session.commit()

    assert notes.unread_count(session, user) == 25
    latest = notes.latest(session, user)
    assert len(latest) == 20 and latest[0].dedupe_key == "k0"  # newest first

    notes.mark_read(session, latest[0], NOW)
    assert notes.unread_count(session, user) == 24

    notes.mark_all_read(session, user, NOW)
    assert notes.unread_count(session, user) == 0
    assert notes.unread_count(session, other) == 1


def test_get_owned(session, user, other):
    add_note(session, other, 1)
    session.commit()
    theirs = rows(session, other)[0]

    assert notes.get_owned(session, user, theirs.id) is None
    assert notes.get_owned(session, other, str(theirs.id)).id == theirs.id
    for bad in ("abc", "0", "-1", None, 2**31, "1.5"):
        assert notes.get_owned(session, other, bad) is None


@pytest.mark.parametrize(
    ("minutes_ago", "text"),
    [
        (0, "Just now"),
        (1, "1 minute ago"),
        (45, "45 minutes ago"),
        (60, "1 hour ago"),
        (9 * 60, "9 hours ago"),
        (11 * 60, "Yesterday"),  # 23:00 on Tuesday in Vienna
        (3 * 24 * 60, "Sun, 15 Nov"),
    ],
)
def test_time_text(minutes_ago, text):
    assert notes.time_text(NOW - dt.timedelta(minutes=minutes_ago), NOW) == text
