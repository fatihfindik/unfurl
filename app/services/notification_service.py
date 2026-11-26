"""Notifications: which reminders are due, writing them once, reading and cleaning up
(SPEC §5.11, §6.11; plan M11a, D-099…D-105).

The scheduler (app/scheduler.py) calls generate_due() and purge() with the current
time. Everything here takes `now` as a parameter, so tests can use any time.
Every query on user data filters by user_id (CLAUDE.md).
"""

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import delete, func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.models import (
    Application,
    LearningGoal,
    LearningTask,
    Notification,
    NotificationPreference,
    User,
)
from app.models.base import as_utc
from app.models.notification import BODY_TEXT, TITLE_TEXT
from app.services import clock, learning_service, next_step_service, reminder_email

logger = logging.getLogger(__name__)

WINDOW_DAYS = 7  # a follow-up or waiting job is reminded if its day was in the last 7 days
KEEP_READ_DAYS = 30  # read notifications older than this are deleted (SPEC §5.11)
KEEP_UNREAD_DAYS = 90  # unread ones too, after 90 days: nothing is kept forever (D-104)
MAX_PER_USER = 200  # above this, the oldest read ones are deleted (D-104)
PANEL_SIZE = 20
STREAK_MILESTONES = (7, 30, 100)  # days in a row that get a "well done" (D-113)
LOOK_AHEAD_DAYS = 7  # the Next reminder card names the weekday within a week (D-115)


@dataclass(frozen=True)
class Due:
    """A reminder that should exist; written once per dedupe key. Job search reminders
    are about an application and link to it; learning ones link to `link`."""

    type: str
    key: str
    title: str
    body: str
    application_id: int | None = None
    link: str | None = None

    @property
    def link_url(self) -> str:
        return self.link or f"/applications/{self.application_id}"


# --- Which reminders are due (D-099, D-100, D-101) -----------------------------------------


def _preferences(session: Session, user: User) -> NotificationPreference:
    """The user's settings, without creating a row (the defaults are all "on")."""
    row = session.exec(
        select(NotificationPreference).where(NotificationPreference.user_id == user.id)
    ).first()
    return row or NotificationPreference(user_id=user.id)


def _in_window(day: dt.date, today: dt.date) -> bool:
    return today - dt.timedelta(days=WINDOW_DAYS - 1) <= day <= today


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def due_for_user(session: Session, user: User, now: dt.datetime) -> list[Due]:
    """Every reminder `user` should have at `now`: job search and learning."""
    prefs = _preferences(session, user)
    return job_search_due(session, user, now, prefs) + learning_due(session, user, now, prefs)


def job_search_due(
    session: Session, user: User, now: dt.datetime, prefs: NotificationPreference
) -> list[Due]:
    """The job search reminders. They come from the same next step as Needs attention
    (D-099), so both always name the same day."""
    today = clock.local(now).date()
    tomorrow = today + dt.timedelta(days=1)
    jobs = session.exec(
        select(Application).where(
            Application.user_id == user.id,
            col(Application.archived).is_(False),
            col(Application.status).in_(("saved", "applied", "interview")),
        )
    )
    due = []
    for job in jobs:
        if job.snoozed_until is not None and as_utc(job.snoozed_until) > as_utc(now):
            continue  # snoozed: comes back when the snooze ends (still in the window)
        # follow_up and stale_saved come from the next step; the interview reminder only
        # from the interview's date, so a "Done" on the step doesn't silence it (D-101).
        step = next_step_service.next_step(job, now)
        step_day = clock.local(step.at).date() if step is not None and step.at else None

        if (
            prefs.follow_up_enabled
            and job.status == "applied"
            and step is not None
            and step.type == "follow_up"
            and step_day is not None
            and _in_window(step_day, today)
        ):
            applied_on = job.applied_at or clock.local(job.created_at).date()
            days = (today - applied_on).days
            due.append(
                Due(
                    "follow_up",
                    f"follow_up:{job.id}:{step_day.isoformat()}",
                    "Time to follow up",
                    f"{job.company} · no reply for {days} days",
                    job.id,
                )
            )
        elif (
            prefs.stale_saved_enabled
            and job.status == "saved"
            and step is not None
            and step.waiting
            and step_day is not None
            and _in_window(step_day, today)
        ):
            days = (today - clock.local(job.created_at).date()).days
            due.append(
                Due(
                    "stale_saved",
                    f"stale_saved:{job.id}:{step_day.isoformat()}",
                    f"Saved {days} days ago",
                    f"{job.company} · apply or archive?",
                    job.id,
                )
            )

        if (
            prefs.interview_enabled
            and job.status == "interview"
            and job.interview_at is not None
            and clock.local(job.interview_at).date() == tomorrow
        ):
            title = "Interview tomorrow"
            if not clock.is_start_of_day(job.interview_at):  # 00:00 means "all day"
                title += f", {clock.local(job.interview_at).strftime('%H:%M')}"
            due.append(
                Due(
                    "interview_tomorrow",
                    f"interview:{job.id}:{tomorrow.isoformat()}",
                    title,
                    f"{job.company} · {job.job_title}",
                    job.id,
                    # Straight to the Interview prep tab (D-180).
                    link=f"/applications/{job.id}?tab=interview",
                )
            )
    return due


# --- Learning: the daily study reminder and the streak (D-111, D-113) ------------------------


def _done_today(session: Session, user: User, today: dt.date) -> bool:
    """True if a task was completed today (the user's day)."""
    return (
        session.exec(
            select(LearningTask.id).where(
                LearningTask.user_id == user.id,
                col(LearningTask.done).is_(True),
                col(LearningTask.done_at) >= clock.to_utc(today),
                col(LearningTask.done_at) < clock.to_utc(today + dt.timedelta(days=1)),
            )
        ).first()
        is not None
    )


def _open_tasks(user: User):
    """Open tasks of active goals: a paused or done goal's tasks stay in the plan but
    don't trigger the study reminder (D-122, #46)."""
    return (
        select(LearningTask)
        .join(LearningGoal, col(LearningGoal.id) == LearningTask.goal_id)
        .where(
            LearningTask.user_id == user.id,
            LearningGoal.user_id == user.id,
            LearningGoal.status == "active",
            col(LearningTask.done).is_(False),
        )
    )


def _open_tasks_until(session: Session, user: User, day: dt.date) -> list[LearningTask]:
    """Open tasks planned for `day` or earlier (overdue ones stay open), oldest day first."""
    return list(
        session.exec(
            _open_tasks(user)
            .where(LearningTask.due_date <= day)
            .order_by(
                col(LearningTask.due_date), col(LearningTask.sort_order), col(LearningTask.id)
            )
        )
    )


def study_task(
    session: Session, user: User, prefs: NotificationPreference, today: dt.date
) -> LearningTask | None:
    """The task today's study reminder names, or None if today gets no reminder (D-111):
    a rest day, a task already done today, or nothing planned for today or before.
    Today's first task comes before the oldest overdue one."""
    if today.weekday() in learning_service.rest_weekdays(prefs):
        return None
    if _done_today(session, user, today):
        return None
    tasks = _open_tasks_until(session, user, today)
    todays = [task for task in tasks if task.due_date == today]
    return (todays or tasks or [None])[0]


def _streak(session: Session, user: User, prefs: NotificationPreference, today: dt.date):
    """(days in a row, the last active day or None). From `today`, not clock.today():
    the scheduler passes its own time (plan review B3)."""
    active = learning_service.active_days(session, user, today)
    rest = learning_service.rest_weekdays(prefs)
    last = max((day for day in active if day <= today), default=None)
    return learning_service.count_streak(active, rest, today), last


def learning_due(
    session: Session, user: User, now: dt.datetime, prefs: NotificationPreference
) -> list[Due]:
    if not (prefs.study_reminder_enabled or prefs.streak_enabled):
        return []
    local_now = clock.local(now)
    today = local_now.date()
    streak, last_active = _streak(session, user, prefs, today)
    due = []

    if prefs.study_reminder_enabled and local_now.time() >= prefs.study_reminder_time:
        task = study_task(session, user, prefs, today)
        if task is not None:
            body = task.title
            if prefs.streak_enabled and streak >= 2:
                body += f" · keep your {streak}-day streak going"  # tone rules, SPEC §6.11
            due.append(
                Due(
                    "study_reminder",
                    f"study:{today.isoformat()}",
                    f"Time to study: {task.estimated_minutes} min",
                    body,
                    link="/learning",
                )
            )

    # A task done at 23:58 still gets its "well done" after midnight: the key is the
    # last active day, so it is written once (D-113).
    if (
        prefs.streak_enabled
        and streak in STREAK_MILESTONES
        and last_active is not None
        and last_active >= today - dt.timedelta(days=1)
    ):
        due.append(
            Due(
                "streak_milestone",
                f"streak:{streak}:{last_active.isoformat()}",
                f"{streak}-day streak",
                f"You studied {streak} days in a row. Nice work.",
                link="/learning",
            )
        )
    return due


@dataclass(frozen=True)
class NextReminder:
    """The Learning page's Next reminder card (D-115). state: "off", "no_tasks" or "on"."""

    state: str
    text: str = ""


def next_reminder(session: Session, user: User, now: dt.datetime) -> NextReminder:
    """When the next study reminder comes: "Today, 19:00", "Tomorrow, 19:00",
    "Mon, 19:00" or "Mon, 30 Nov, 19:00". Doesn't create the preferences row."""
    prefs = _preferences(session, user)
    if not prefs.study_reminder_enabled:
        return NextReminder("off")
    first_open = session.exec(
        _open_tasks(user).with_only_columns(func.min(LearningTask.due_date))
    ).one()
    if first_open is None:
        return NextReminder("no_tasks")

    today = clock.local(now).date()
    at = prefs.study_reminder_time.strftime("%H:%M")
    sent_today = session.exec(
        select(Notification.id).where(
            Notification.user_id == user.id, Notification.dedupe_key == f"study:{today}"
        )
    ).first()
    if sent_today is None and study_task(session, user, prefs, today) is not None:
        return NextReminder("on", f"Today, {at}")

    # Later days: the first day from tomorrow (or the first task's day) that isn't a
    # rest day. Open tasks stay open, so a task planned for that day or before is there.
    rest = learning_service.rest_weekdays(prefs)
    day = max(today + dt.timedelta(days=1), first_open)
    for _ in range(6):  # at most six rest days in a row (D-090)
        if day.weekday() not in rest:
            break
        day += dt.timedelta(days=1)
    if day == today + dt.timedelta(days=1):
        name = "Tomorrow"
    elif (day - today).days < LOOK_AHEAD_DAYS:
        name = day.strftime("%a")
    else:
        name = day.strftime("%a, %-d %b")
    return NextReminder("on", f"{name}, {at}")


# --- Writing them once ----------------------------------------------------------------------


def generate_for_user(
    session: Session, user: User, now: dt.datetime, emails: list | None = None
) -> int:
    """Write the user's due reminders that don't exist yet; commits. Returns how many
    were written. If another run wrote the same key a moment earlier, the unique
    constraint stops it: everything of this user is rolled back and counted as 0
    (the next run, 15 minutes later, finds the rest).

    A study reminder email is added to `emails` as (user id, email) (generate_due
    sends them all after the loop); without a list it is sent right away."""
    due = due_for_user(session, user, now)
    if not due:
        return 0
    keys = {item.key for item in due}
    existing = set(
        session.exec(
            select(Notification.dedupe_key).where(
                Notification.user_id == user.id, col(Notification.dedupe_key).in_(keys)
            )
        )
    )
    new = [item for item in due if item.key not in existing]
    for item in new:
        session.add(
            Notification(
                user_id=user.id,
                application_id=item.application_id,
                type=item.type,
                title=_short(item.title, TITLE_TEXT),
                body=_short(item.body, BODY_TEXT),
                link_url=item.link_url,
                dedupe_key=item.key,
            )
        )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return 0
    # The study reminder also goes by email, once: only when this run wrote it and the
    # commit worked (plan review B2), and never to an unverified address (D-112).
    for item in new:
        if item.type == "study_reminder" and user.is_verified:
            if _preferences(session, user).study_reminder_email:
                email = reminder_email.build(
                    user, _short(item.title, TITLE_TEXT), _short(item.body, BODY_TEXT)
                )
                if emails is None:
                    reminder_email.send_all([email])
                else:
                    emails.append((user.id, email))
    return len(new)


def generate_due(session: Session, now: dt.datetime) -> int:
    """One run of the scheduler: every user, one commit per user (D-103). A failure
    for one user is logged (only the user id) and the others still get theirs."""
    written = 0
    emails = []
    user_ids = list(session.exec(select(User.id)))
    for user_id in user_ids:
        try:
            user = session.get(User, user_id)
            if user is not None:
                # "Today" and "tomorrow" are the user's own days (D-109).
                with clock.use_timezone(user.timezone):
                    written += generate_for_user(session, user, now, emails)
        except Exception as exc:
            session.rollback()
            # Only the id and the kind of error: an error's text can quote data.
            logger.error("Notifications for user %s failed: %s", user_id, type(exc).__name__)
    # After every user's reminders are written: a slow mail server can't hold them up
    # (security review M11b, L1). An account deleted (or unverified) during the run
    # gets nothing: one query just before sending (D-131).
    reminder_email.send_all(still_wanted(session, emails))
    return written


def still_wanted(session: Session, emails: list) -> list:
    """The emails of `emails` ((user id, email) pairs) whose user still exists and
    is verified."""
    if not emails:
        return []
    ids = {user_id for user_id, _ in emails}
    keep = set(
        session.exec(select(User.id).where(col(User.id).in_(ids), col(User.is_verified).is_(True)))
    )
    return [email for user_id, email in emails if user_id in keep]


# --- Cleaning up (D-104) --------------------------------------------------------------------


def purge(session: Session, now: dt.datetime) -> None:
    """Delete read notifications older than KEEP_READ_DAYS and any older than
    KEEP_UNREAD_DAYS; then, for users above MAX_PER_USER, their oldest read ones.
    The cap never deletes unread ones (D-104)."""
    session.exec(
        delete(Notification).where(
            col(Notification.read_at).is_not(None),
            Notification.created_at < now - dt.timedelta(days=KEEP_READ_DAYS),
        )
    )
    session.exec(
        delete(Notification).where(
            Notification.created_at < now - dt.timedelta(days=KEEP_UNREAD_DAYS)
        )
    )
    too_many = session.exec(
        select(Notification.user_id)
        .group_by(Notification.user_id)
        .having(func.count() > MAX_PER_USER)
    ).all()
    for user_id in too_many:
        total = session.exec(
            select(func.count()).select_from(Notification).where(Notification.user_id == user_id)
        ).one()
        oldest_read = session.exec(
            select(Notification.id)
            .where(Notification.user_id == user_id, col(Notification.read_at).is_not(None))
            .order_by(col(Notification.created_at), col(Notification.id))
            .limit(total - MAX_PER_USER)
        ).all()
        if oldest_read:
            session.exec(delete(Notification).where(col(Notification.id).in_(oldest_read)))
    session.commit()


# --- Reading and marking (the bell) ---------------------------------------------------------


def unread_count(session: Session, user: User) -> int:
    return session.exec(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, col(Notification.read_at).is_(None))
    ).one()


def latest(session: Session, user: User, limit: int = PANEL_SIZE) -> list[Notification]:
    return list(
        session.exec(
            select(Notification)
            .where(Notification.user_id == user.id)
            .order_by(col(Notification.created_at).desc(), col(Notification.id).desc())
            .limit(limit)
        )
    )


def get_owned(session: Session, user: User, notification_id) -> Notification | None:
    """The user's notification `notification_id`, or None (also for bad ids)."""
    try:
        number = int(notification_id)
    except (TypeError, ValueError):
        return None
    if not 0 < number <= 2**31 - 1:
        return None
    return session.exec(
        select(Notification).where(Notification.id == number, Notification.user_id == user.id)
    ).first()


def mark_read(session: Session, notification: Notification, now: dt.datetime) -> None:
    """`notification` comes from get_owned, so it is the user's."""
    if notification.read_at is None:
        notification.read_at = now
        session.add(notification)
        session.commit()


def mark_all_read(session: Session, user: User, now: dt.datetime) -> None:
    session.exec(
        update(Notification)
        .where(Notification.user_id == user.id, col(Notification.read_at).is_(None))
        .values(read_at=now)
    )
    session.commit()


def time_text(value: dt.datetime, now: dt.datetime) -> str:
    """When a notification came, as the panel shows it (prototype): "Just now",
    "5 minutes ago", "2 hours ago", "Yesterday", "Mon, 16 Nov"."""
    seconds = (as_utc(now) - as_utc(value)).total_seconds()
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        minutes = int(seconds // 60)
        return "1 minute ago" if minutes == 1 else f"{minutes} minutes ago"
    day, today = clock.local(value).date(), clock.local(now).date()
    if day == today:
        hours = int(seconds // 3600)
        return "1 hour ago" if hours == 1 else f"{hours} hours ago"
    if day == today - dt.timedelta(days=1):
        return "Yesterday"
    return clock.local(value).strftime("%a, %-d %b")
