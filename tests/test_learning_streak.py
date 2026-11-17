"""The streak rule (SPEC §5.10, D-091): unit tests on count_streak with fixed dates, then
the active-day rule against the database. M10 acceptance: rest days included."""

import datetime as dt

import pytest

from app.models import LearningGoal, LearningTask, StudyLog
from app.services import clock
from app.services import learning_service as learning

# Wednesday 18 November 2026. Weekends (sat=5, sun=6) are the default rest days.
WED = dt.date(2026, 11, 18)
WEEKEND = {5, 6}


def days_before(day: dt.date, *backs: int) -> set[dt.date]:
    return {day - dt.timedelta(days=back) for back in backs}


# --- count_streak: plain dates ------------------------------------------------------------


def test_no_activity_is_no_streak():
    assert learning.count_streak(set(), WEEKEND, WED) == 0


def test_active_days_in_a_row_count():
    assert learning.count_streak(days_before(WED, 0, 1, 2), WEEKEND, WED) == 3


def test_today_not_active_yet_starts_from_yesterday():
    # Mon and Tue active, nothing yet on Wednesday: the day isn't over.
    assert learning.count_streak(days_before(WED, 1, 2), WEEKEND, WED) == 2


def test_a_missed_weekday_ends_the_streak():
    # Wed active, Tue missed, Mon active: only today counts.
    assert learning.count_streak(days_before(WED, 0, 2), WEEKEND, WED) == 1


def test_a_rest_day_without_activity_is_skipped():
    # Mon 16 and Fri 13 active, the weekend in between empty: still a streak of 2.
    monday = dt.date(2026, 11, 16)
    active = {monday, dt.date(2026, 11, 13)}
    assert learning.count_streak(active, WEEKEND, monday) == 2


def test_studying_on_a_rest_day_counts():
    monday = dt.date(2026, 11, 16)
    active = {monday, dt.date(2026, 11, 15), dt.date(2026, 11, 14), dt.date(2026, 11, 13)}
    assert learning.count_streak(active, WEEKEND, monday) == 4


def test_today_a_rest_day_without_activity():
    # Sunday, nothing today or Saturday; Friday and Thursday active.
    sunday = dt.date(2026, 11, 22)
    active = {dt.date(2026, 11, 20), dt.date(2026, 11, 19)}
    assert learning.count_streak(active, WEEKEND, sunday) == 2


def test_without_rest_days_the_weekend_breaks_it():
    monday = dt.date(2026, 11, 16)
    active = {monday, dt.date(2026, 11, 13)}
    assert learning.count_streak(active, set(), monday) == 1


def test_other_rest_days():
    # Rest on Tuesday only: Wed and Mon active make a streak of 2.
    assert learning.count_streak(days_before(WED, 0, 2), {1}, WED) == 2


def test_only_rest_days_never_loop_forever():
    # Six rest days and no activity: the window ends the search.
    assert learning.count_streak(set(), {0, 1, 2, 3, 4, 5}, WED) == 0


def test_the_streak_is_at_most_the_window():
    active = days_before(WED, *range(learning.STREAK_WINDOW + 50))
    assert learning.count_streak(active, WEEKEND, WED) == learning.STREAK_WINDOW


def test_rest_weekdays_ignores_unknown_values(session, user):
    prefs = learning.preferences(session, user)
    prefs.rest_days = ["sat", "sun", "xyz"]
    assert learning.rest_weekdays(prefs) == {5, 6}


# --- Active days from the database --------------------------------------------------------

# Wednesday 18 November 2026, 10:00 in Vienna (09:00 UTC).
NOW = dt.datetime(2026, 11, 18, 9, 0, tzinfo=dt.UTC)


@pytest.fixture
def frozen(monkeypatch):
    """clock.now() and clock.today() return NOW."""
    monkeypatch.setattr(clock, "utcnow", lambda: NOW)


@pytest.fixture
def goal(session, user) -> LearningGoal:
    row = LearningGoal(user_id=user.id, title="Python")
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def log(session, user, day: dt.date, minutes: int, goal=None) -> None:
    session.add(StudyLog(user_id=user.id, goal_id=goal and goal.id, date=day, minutes=minutes))
    session.commit()


def done_task(session, user, goal, done_at: dt.datetime) -> LearningTask:
    task = LearningTask(
        user_id=user.id,
        goal_id=goal.id,
        title="Week 1",
        due_date=done_at.date(),
        done=True,
        done_at=done_at,
    )
    session.add(task)
    session.commit()
    return task


def test_ten_minutes_of_logged_time_makes_a_day_active(session, user, frozen):
    log(session, user, WED, 10)
    log(session, user, WED - dt.timedelta(days=1), 9)

    assert learning.active_days(session, user, WED) == {WED}


def test_logged_time_of_one_day_adds_up(session, user, frozen):
    log(session, user, WED, 5)
    log(session, user, WED, 5)

    assert learning.active_days(session, user, WED) == {WED}


def test_a_completed_task_makes_its_day_active(session, user, goal, frozen):
    done_task(session, user, goal, NOW)

    assert learning.active_days(session, user, WED) == {WED}


def test_the_day_of_a_task_is_the_day_in_vienna(session, user, goal, frozen):
    # 23:30 UTC on Monday is 00:30 on Tuesday in Vienna.
    done_task(session, user, goal, dt.datetime(2026, 11, 16, 23, 30, tzinfo=dt.UTC))

    assert learning.active_days(session, user, WED) == {dt.date(2026, 11, 17)}


def test_old_days_are_not_read(session, user, goal, frozen):
    old = WED - dt.timedelta(days=learning.STREAK_WINDOW + 1)
    log(session, user, old, 60)
    done_task(session, user, goal, clock.to_utc(old, dt.time(12)))

    assert learning.active_days(session, user, WED) == set()


def test_another_users_activity_does_not_count(session, user, goal, frozen):
    from app.services.auth_service import create_user

    other = create_user(session, email="other@example.test", password="x" * 12, full_name="O")
    log(session, other, WED, 60)

    assert learning.active_days(session, user, WED) == set()


def test_streak_uses_the_users_rest_days(session, user, frozen):
    # Fri 13 and Mon 16 active, nothing on the weekend, Tue 17 active, Wed not yet.
    for day in (13, 16, 17):
        log(session, user, dt.date(2026, 11, day), 30)

    assert learning.streak(session, user) == 3

    learning.set_rest_days(session, user, [])
    assert learning.streak(session, user) == 2


def test_a_deleted_goal_keeps_its_logs(session, user, goal, frozen):
    log(session, user, WED, 30, goal=goal)
    session.delete(goal)
    session.commit()

    assert learning.active_days(session, user, WED) == {WED}
    assert session.get(StudyLog, 1).goal_id is None
