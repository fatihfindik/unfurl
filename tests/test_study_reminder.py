"""The daily study reminder (D-111), the streak milestone (D-113) and the Next reminder
card (D-115). M11b acceptance: the reminder comes at the chosen time in the user's
time zone, not on rest days, not after a task was done that day, never twice; the
scheduler logic is tested with a fixed clock (every function takes `now`)."""

import datetime as dt

import pytest
from sqlmodel import select

from app.models import LearningGoal, LearningTask, Notification, NotificationPreference, StudyLog
from app.services import clock
from app.services import notification_service as notes
from app.services.auth_service import create_user

UTC = dt.UTC
WED = dt.date(2026, 11, 18)  # a Wednesday; Vienna is UTC+1 in November


def at(day: dt.date, hour: int, minute: int = 0, zone: str | None = None) -> dt.datetime:
    """A wall-clock time in `zone` (default Vienna) as UTC, like the scheduler's `now`."""
    with clock.use_timezone(zone):
        return clock.to_utc(day, dt.time(hour, minute))


def goal_for(session, user) -> LearningGoal:
    goal = LearningGoal(user_id=user.id, title="Python")
    session.add(goal)
    session.commit()
    session.refresh(goal)
    return goal


def task(session, user, due: dt.date, title="CS50P Week 3", minutes=25, done_at=None):
    goal = session.exec(select(LearningGoal).where(LearningGoal.user_id == user.id)).first()
    goal = goal or goal_for(session, user)
    row = LearningTask(
        user_id=user.id,
        goal_id=goal.id,
        title=title,
        due_date=due,
        estimated_minutes=minutes,
        done=done_at is not None,
        done_at=done_at,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def studied(session, user, *days: dt.date):
    """Days with 30 logged minutes: active days for the streak."""
    for day in days:
        session.add(StudyLog(user_id=user.id, date=day, minutes=30))
    session.commit()


def prefs(session, user, **values) -> NotificationPreference:
    row = NotificationPreference(user_id=user.id, **values)
    session.add(row)
    session.commit()
    return row


def rows(session, user, kind=None) -> list[Notification]:
    session.expire_all()
    query = select(Notification).where(Notification.user_id == user.id)
    if kind:
        query = query.where(Notification.type == kind)
    return list(session.exec(query))


def run(session, now) -> int:
    return notes.generate_due(session, now)


def set_zone(session, user, zone):
    user.timezone = zone
    session.add(user)
    session.commit()


# --- When (acceptance) ----------------------------------------------------------------------


def test_the_reminder_comes_at_the_chosen_time_not_before(session, user):
    task(session, user, WED)

    run(session, at(WED, 18, 59))
    assert rows(session, user) == []

    run(session, at(WED, 19, 0))
    [note] = rows(session, user)
    assert (note.type, note.title, note.body, note.link_url) == (
        "study_reminder",
        "Time to study: 25 min",
        "CS50P Week 3",
        "/learning",
    )
    assert note.dedupe_key == "study:2026-11-18" and note.application_id is None


def test_a_reminder_missed_at_its_time_comes_later_that_day(session, user):
    task(session, user, WED)

    run(session, at(WED, 23, 59))  # the app was asleep at 19:00

    assert len(rows(session, user, "study_reminder")) == 1


def test_it_does_not_move_to_the_next_day(session, user):
    task(session, user, WED)
    task(session, user, WED + dt.timedelta(days=1))

    run(session, at(WED + dt.timedelta(days=1), 0, 15))  # just after midnight, Thursday

    assert rows(session, user) == []  # Thursday's comes at 19:00 on Thursday


def test_the_chosen_time_is_used(session, user):
    prefs(session, user, study_reminder_time=dt.time(7, 30))
    task(session, user, WED)

    run(session, at(WED, 7, 15))
    assert rows(session, user) == []
    run(session, at(WED, 7, 30))
    assert len(rows(session, user)) == 1


def test_each_user_gets_it_in_their_own_time_zone(session, user):
    istanbul = create_user(session, email="i@example.com", password="x" * 12, full_name="I")
    set_zone(session, istanbul, "Europe/Istanbul")  # UTC+3, two hours ahead of Vienna
    task(session, user, WED)
    task(session, istanbul, WED)

    run(session, at(WED, 17, 0))  # 17:00 Vienna = 19:00 Istanbul
    assert rows(session, user) == []
    assert len(rows(session, istanbul)) == 1

    run(session, at(WED, 19, 0))  # 19:00 Vienna
    assert len(rows(session, user)) == 1
    assert len(rows(session, istanbul)) == 1


def test_the_day_is_the_users_day(session, user):
    set_zone(session, user, "Pacific/Auckland")  # UTC+13: already Thursday
    prefs(session, user, study_reminder_time=dt.time(8, 0))
    thursday = WED + dt.timedelta(days=1)
    task(session, user, thursday)

    now = at(thursday, 8, 0, zone="Pacific/Auckland")  # Wednesday 19:00 UTC
    assert now.date() == WED
    run(session, now)

    [note] = rows(session, user)
    assert note.dedupe_key == "study:2026-11-19"


def test_summer_time_is_followed(session, user):
    # 1 July: Vienna is UTC+2, so 19:00 there is 17:00 UTC.
    july = dt.date(2026, 7, 1)
    task(session, user, july)

    run(session, dt.datetime(2026, 7, 1, 16, 59, tzinfo=UTC))
    assert rows(session, user) == []
    run(session, dt.datetime(2026, 7, 1, 17, 0, tzinfo=UTC))
    assert len(rows(session, user)) == 1


# --- When not (acceptance) ------------------------------------------------------------------


def test_not_on_a_rest_day(session, user):
    saturday = dt.date(2026, 11, 21)  # sat and sun are the default rest days
    task(session, user, saturday)

    run(session, at(saturday, 20, 0))

    assert rows(session, user) == []


def test_chosen_rest_days_are_used(session, user):
    prefs(session, user, rest_days=["wed"])
    task(session, user, WED)

    run(session, at(WED, 20, 0))

    assert rows(session, user) == []


def test_not_after_a_task_was_done_that_day(session, user):
    task(session, user, WED, title="Open one")
    task(session, user, WED, title="Done one", done_at=at(WED, 8, 0))

    run(session, at(WED, 20, 0))

    assert rows(session, user, "study_reminder") == []


def test_a_task_done_yesterday_does_not_count_for_today(session, user):
    task(session, user, WED)
    task(session, user, WED, title="Done", done_at=at(WED - dt.timedelta(days=1), 23, 30))

    run(session, at(WED, 20, 0))

    assert len(rows(session, user, "study_reminder")) == 1


def test_not_without_a_task(session, user):
    task(session, user, WED + dt.timedelta(days=2))  # only later

    run(session, at(WED, 20, 0))

    assert rows(session, user) == []


def test_an_overdue_task_is_enough(session, user):
    task(session, user, WED - dt.timedelta(days=3), title="Old one", minutes=40)

    run(session, at(WED, 20, 0))

    [note] = rows(session, user)
    assert (note.title, note.body) == ("Time to study: 40 min", "Old one")


def test_todays_first_task_comes_before_an_overdue_one(session, user):
    task(session, user, WED - dt.timedelta(days=3), title="Old one")
    task(session, user, WED, title="Today one")

    run(session, at(WED, 20, 0))

    assert rows(session, user)[0].body == "Today one"


def test_not_when_switched_off(session, user):
    prefs(session, user, study_reminder_enabled=False)
    task(session, user, WED)

    run(session, at(WED, 20, 0))

    assert rows(session, user, "study_reminder") == []


def test_never_twice_a_day(session, user):
    task(session, user, WED)

    assert run(session, at(WED, 19, 0)) == 1
    assert run(session, at(WED, 19, 15)) == 0
    assert run(session, at(WED, 23, 45)) == 0
    assert len(rows(session, user)) == 1


def test_one_per_day_on_following_days(session, user):
    task(session, user, WED)
    thursday = WED + dt.timedelta(days=1)

    run(session, at(WED, 19, 0))
    run(session, at(thursday, 19, 0))  # Wednesday's task is overdue now

    assert sorted(note.dedupe_key for note in rows(session, user)) == [
        "study:2026-11-18",
        "study:2026-11-19",
    ]


def test_other_users_tasks_are_not_used(session, user):
    other = create_user(session, email="o@example.com", password="x" * 12, full_name="O")
    task(session, other, WED, title="Someone else's")

    run(session, at(WED, 20, 0))

    assert rows(session, user) == []
    assert rows(session, other)[0].body == "Someone else's"


# --- The streak in the text -----------------------------------------------------------------


def test_the_streak_is_mentioned_from_two_days(session, user):
    studied(session, user, WED - dt.timedelta(days=2), WED - dt.timedelta(days=1))
    task(session, user, WED)

    run(session, at(WED, 20, 0))

    assert rows(session, user)[0].body == "CS50P Week 3 · keep your 2-day streak going"


def test_a_one_day_streak_is_not_mentioned(session, user):
    studied(session, user, WED - dt.timedelta(days=1))
    task(session, user, WED)

    run(session, at(WED, 20, 0))

    assert rows(session, user)[0].body == "CS50P Week 3"


def test_the_streak_is_not_mentioned_when_switched_off(session, user):
    prefs(session, user, streak_enabled=False)
    studied(session, user, WED - dt.timedelta(days=2), WED - dt.timedelta(days=1))
    task(session, user, WED)

    run(session, at(WED, 20, 0))

    assert rows(session, user)[0].body == "CS50P Week 3"


# --- Streak milestones (D-113) --------------------------------------------------------------


def days_up_to(last: dt.date, count: int) -> list[dt.date]:
    return [last - dt.timedelta(days=back) for back in range(count)]


@pytest.mark.parametrize(("count", "written"), [(6, False), (7, True), (8, False)])
def test_a_seven_day_streak_is_celebrated(session, user, count, written):
    prefs(session, user, rest_days=[])
    studied(session, user, *days_up_to(WED, count))

    run(session, at(WED, 12, 0))

    milestones = rows(session, user, "streak_milestone")
    if written:
        [note] = milestones
        assert (note.title, note.body, note.link_url, note.dedupe_key) == (
            "7-day streak",
            "You studied 7 days in a row. Nice work.",
            "/learning",
            "streak:7:2026-11-18",
        )
    else:
        assert milestones == []


@pytest.mark.parametrize("count", [30, 100])
def test_30_and_100_days_are_celebrated(session, user, count):
    prefs(session, user, rest_days=[])
    studied(session, user, *days_up_to(WED, count))

    run(session, at(WED, 12, 0))

    [note] = rows(session, user, "streak_milestone")
    assert note.title == f"{count}-day streak"


def test_a_milestone_is_written_once(session, user):
    prefs(session, user, rest_days=[])
    studied(session, user, *days_up_to(WED, 7))

    run(session, at(WED, 12, 0))
    run(session, at(WED, 22, 0))
    run(session, at(WED + dt.timedelta(days=1), 0, 15))  # still 7 until Thursday counts

    assert len(rows(session, user, "streak_milestone")) == 1


def test_a_streak_reached_just_before_midnight_is_celebrated_after_it(session, user):
    prefs(session, user, rest_days=[])
    studied(session, user, *days_up_to(WED, 7))

    run(session, at(WED + dt.timedelta(days=1), 0, 15))  # the 23:45 run was missed

    [note] = rows(session, user, "streak_milestone")
    assert note.dedupe_key == "streak:7:2026-11-18"


def test_an_old_streak_is_not_celebrated_later(session, user):
    prefs(session, user, rest_days=[])
    studied(session, user, *days_up_to(WED - dt.timedelta(days=3), 7))

    run(session, at(WED, 12, 0))

    assert rows(session, user, "streak_milestone") == []


def test_no_milestones_when_switched_off(session, user):
    prefs(session, user, rest_days=[], streak_enabled=False)
    studied(session, user, *days_up_to(WED, 7))

    run(session, at(WED, 12, 0))

    assert rows(session, user, "streak_milestone") == []


# --- The Next reminder card (D-115) ---------------------------------------------------------


def card(session, user, now):
    return notes.next_reminder(session, user, now)


def test_card_today(session, user):
    task(session, user, WED)

    assert card(session, user, at(WED, 10, 0)) == notes.NextReminder("on", "Today, 19:00")


def test_card_tomorrow_after_todays_reminder_came(session, user):
    task(session, user, WED)
    run(session, at(WED, 19, 0))

    assert card(session, user, at(WED, 19, 5)).text == "Tomorrow, 19:00"


def test_card_tomorrow_after_a_task_was_done_today(session, user):
    task(session, user, WED)
    task(session, user, WED, title="Done", done_at=at(WED, 9, 0))

    assert card(session, user, at(WED, 10, 0)).text == "Tomorrow, 19:00"


def test_card_skips_rest_days(session, user):
    friday = dt.date(2026, 11, 20)
    task(session, user, friday)
    run(session, at(friday, 19, 0))

    assert card(session, user, at(friday, 20, 0)).text == "Mon, 19:00"  # weekend skipped


def test_card_waits_for_the_first_planned_task(session, user):
    task(session, user, dt.date(2026, 11, 20))  # Friday

    assert card(session, user, at(WED, 10, 0)).text == "Fri, 19:00"


def test_card_names_the_date_for_a_task_more_than_a_week_away(session, user):
    task(session, user, dt.date(2026, 11, 30))

    assert card(session, user, at(WED, 10, 0)).text == "Mon, 30 Nov, 19:00"


def test_card_off(session, user):
    prefs(session, user, study_reminder_enabled=False)
    task(session, user, WED)

    assert card(session, user, at(WED, 10, 0)).state == "off"


def test_card_without_tasks(session, user):
    task(session, user, WED, done_at=at(WED, 9, 0))  # only done ones

    assert card(session, user, at(WED, 10, 0)).state == "no_tasks"


def test_the_card_on_the_learning_page(auth_client, session, user):
    prefs(session, user, study_reminder_time=dt.time(21, 30))
    task(session, user, WED + dt.timedelta(days=1))

    html = auth_client.get("/learning").text

    assert 'data-card="reminder"' in html
    assert "Change reminder" in html and 'href="/settings"' in html
    assert ", 21:30" in html


def test_the_card_says_off_with_a_link_to_turn_it_on(auth_client, session, user):
    prefs(session, user, study_reminder_enabled=False)

    html = auth_client.get("/learning").text

    assert ">Off</span>" in html and "Turn on" in html


def test_the_card_offers_a_new_goal_first(auth_client):
    html = auth_client.get("/learning").text

    assert "No tasks planned" in html
    assert 'data-dialog-load="add-task"' not in html  # a task needs a goal


def test_the_card_offers_to_add_a_task_once_there_is_a_goal(auth_client, session, user):
    goal_for(session, user)

    html = auth_client.get("/learning").text

    card = html.split('data-card="reminder"')[1].split("</div>")[0]
    assert "No tasks planned" in card
    assert 'data-dialog-url="/learning/tasks/new"' in card
