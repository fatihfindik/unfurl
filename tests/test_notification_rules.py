"""Which job search reminders are due (SPEC §6.11, D-099…D-101). Plain calls with a
fixed `now`; no scheduler."""

import datetime as dt

import pytest

from app.models import Application, NotificationPreference
from app.services import application_service as apps
from app.services import clock
from app.services import notification_service as notes
from app.services.auth_service import create_user

# Wednesday 18 November 2026, 10:00 in Vienna.
NOW = dt.datetime(2026, 11, 18, 9, 0, tzinfo=dt.UTC)
TODAY = dt.date(2026, 11, 18)


def ago(days: int) -> dt.date:
    return TODAY - dt.timedelta(days=days)


def add(session, user, company="Acme", status="saved", created_days_ago=0, **fields):
    job = apps.create(session, user, company=company, job_title="Product Manager")
    if status != "saved":
        apps.move(session, user, job, status)
    job.created_at = clock.to_utc(ago(created_days_ago), dt.time(12))
    for name, value in fields.items():
        setattr(job, name, value)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def due(session, user, now=NOW) -> list[tuple[str, str, str]]:
    session.expire_all()
    return [(d.type, d.title, d.body) for d in notes.due_for_user(session, user, now)]


def keys(session, user, now=NOW) -> list[str]:
    session.expire_all()
    return [d.key for d in notes.due_for_user(session, user, now)]


# --- follow_up -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("applied_days_ago", "expected"),
    [(9, False), (10, True), (11, True), (16, True), (17, False)],
)
def test_follow_up_comes_from_day_10_for_seven_days(session, user, applied_days_ago, expected):
    add(session, user, status="applied", applied_at=ago(applied_days_ago))

    found = due(session, user)

    if expected:
        days = applied_days_ago
        assert found == [("follow_up", "Time to follow up", f"Acme · no reply for {days} days")]
    else:
        assert found == []


def test_follow_up_key_names_the_steps_day(session, user):
    job = add(session, user, status="applied", applied_at=ago(12))

    assert keys(session, user) == [f"follow_up:{job.id}:2026-11-16"]


def test_a_hand_set_follow_up_counts(session, user):
    # "Send thank-you note" Done -> a follow-up 5 days later, set by hand (D-073).
    add(
        session,
        user,
        status="applied",
        applied_at=ago(2),
        next_action_manual=True,
        next_action_type="follow_up",
        next_action_at=clock.to_utc(TODAY),
    )

    assert [t for t, *_ in due(session, user)] == ["follow_up"]


def test_done_means_no_follow_up(session, user):
    add(
        session,
        user,
        status="applied",
        applied_at=ago(12),
        next_action_manual=True,
        next_action_type=None,
    )

    assert due(session, user) == []


# --- stale_saved ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("saved_days_ago", "expected"),
    [(6, False), (7, True), (8, True), (13, True), (14, False)],
)
def test_stale_saved_from_day_7_for_seven_days(session, user, saved_days_ago, expected):
    add(session, user, created_days_ago=saved_days_ago)

    found = due(session, user)

    if expected:
        title = f"Saved {saved_days_ago} days ago"
        assert found == [("stale_saved", title, "Acme · apply or archive?")]
    else:
        assert found == []


def test_a_job_with_a_closing_date_is_not_stale(session, user):
    add(session, user, created_days_ago=9, posting_closes_at=TODAY)

    assert due(session, user) == []


# --- interview_tomorrow --------------------------------------------------------------------


@pytest.mark.parametrize(("offset", "expected"), [(0, False), (1, True), (2, False)])
def test_interview_tomorrow(session, user, offset, expected):
    add(
        session,
        user,
        status="interview",
        interview_at=clock.to_utc(TODAY + dt.timedelta(days=offset), dt.time(10, 30)),
    )

    found = due(session, user)

    if expected:
        assert found == [
            ("interview_tomorrow", "Interview tomorrow, 10:30", "Acme · Product Manager")
        ]
    else:
        assert found == []


def test_interview_tomorrow_links_to_the_prep_tab(session, user):
    job = add(
        session,
        user,
        status="interview",
        interview_at=clock.to_utc(TODAY + dt.timedelta(days=1), dt.time(10, 30)),
    )

    [item] = notes.due_for_user(session, user, NOW)

    assert item.link_url == f"/applications/{job.id}?tab=interview"  # D-180


def test_an_all_day_interview_has_no_time(session, user):
    add(session, user, status="interview", interview_at=clock.to_utc(TODAY + dt.timedelta(days=1)))

    assert due(session, user)[0][1] == "Interview tomorrow"


def test_no_interview_reminder_after_leaving_interview(session, user):
    add(
        session,
        user,
        status="rejected",
        interview_at=clock.to_utc(TODAY + dt.timedelta(days=1), dt.time(10)),
    )

    assert due(session, user) == []


def test_interview_tomorrow_near_midnight_in_vienna(session, user):
    # 23:30 on Thursday in Vienna is still "tomorrow" on Wednesday.
    add(
        session,
        user,
        status="interview",
        interview_at=clock.to_utc(TODAY + dt.timedelta(days=1), dt.time(23, 30)),
    )
    late_wednesday = clock.to_utc(TODAY, dt.time(23, 59))

    assert due(session, user, late_wednesday)[0][1] == "Interview tomorrow, 23:30"


def test_interview_across_the_clock_change(session, user):
    # Sunday 29 March 2026 the clocks go forward; Saturday evening it is "tomorrow".
    add(
        session,
        user,
        status="interview",
        interview_at=clock.to_utc(dt.date(2026, 3, 29), dt.time(9)),
    )
    saturday = clock.to_utc(dt.date(2026, 3, 28), dt.time(20))

    assert due(session, user, saturday)[0][1] == "Interview tomorrow, 09:00"


# --- Left out ------------------------------------------------------------------------------


def test_archived_jobs_are_left_out(session, user):
    add(session, user, status="applied", applied_at=ago(12), archived=True)

    assert due(session, user) == []


def test_snoozed_jobs_come_back_after_the_snooze(session, user):
    add(
        session,
        user,
        status="applied",
        applied_at=ago(12),
        snoozed_until=NOW + dt.timedelta(days=3),
    )

    assert due(session, user) == []
    assert [t for t, *_ in due(session, user, NOW + dt.timedelta(days=3, minutes=1))] == [
        "follow_up"
    ]


@pytest.mark.parametrize(
    ("setting", "status", "fields"),
    [
        ("follow_up_enabled", "applied", {"applied_at": ago(12)}),
        ("stale_saved_enabled", "saved", {}),
        (
            "interview_enabled",
            "interview",
            {"interview_at": clock.to_utc(TODAY + dt.timedelta(days=1), dt.time(9))},
        ),
    ],
)
def test_a_switched_off_type_is_left_out(session, user, setting, status, fields):
    add(session, user, status=status, created_days_ago=9, **fields)
    session.add(NotificationPreference(user_id=user.id, **{setting: False}))
    session.commit()

    assert due(session, user) == []


def test_other_users_jobs_are_not_mine(session, user):
    other = create_user(session, email="other@example.test", password="x" * 12, full_name="O")
    add(session, other, status="applied", applied_at=ago(12))

    assert due(session, user) == []


# --- The same day as Needs attention (acceptance, D-099) -----------------------------------


def test_reminders_and_needs_attention_name_the_same_jobs(session, user):
    add(session, user, "Follow Co", status="applied", applied_at=ago(10))
    add(session, user, "Early Co", status="applied", applied_at=ago(9))
    add(session, user, "Waiting Co", created_days_ago=7)
    add(session, user, "Fresh Co", created_days_ago=6)

    session.expire_all()
    attention = {
        item.application.company
        for item in apps.needs_attention(session, user, NOW)
        if item.tag in ("Follow up", "Waiting")
    }
    reminded = {d.body.split(" · ")[0] for d in notes.due_for_user(session, user, NOW)}

    assert attention == reminded == {"Follow Co", "Waiting Co"}


def test_due_reads_only_open_statuses(session, user):
    add(session, user, status="offer")
    job = session.get(Application, 1)

    assert job.status == "offer"
    assert due(session, user) == []
