"""#39 (editing tasks and goals) error and authorization cases not covered by
test_learning_edit.py: logged-out htmx and plain requests, another user's ids on every
route, huge and odd values, a missing or unknown `kind`, smuggled fields, the rate limit
of the task save, and htmx vs plain requests."""

import datetime as dt

import pytest
from freezegun import freeze_time
from sqlmodel import select

from app.models import LearningGoal, LearningTask, StudyLog, User
from app.services import learning_service as learning
from app.services.auth_service import create_user
from tests.helpers import csrf_token, log_in

HTMX = {"HX-Request": "true"}
HUGE = "x" * 10_000
TODAY = dt.date(2026, 11, 18)  # a Wednesday

OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "another long password"

OPEN = {"kind": "open", "title": "Renamed", "due_date": "2026-11-20", "minutes": "30"}
DONE = {"kind": "done", "title": "Renamed", "spent_minutes": "30"}
GOAL = {"title": "Renamed", "target_date": "", "weekly_hours": "", "status": "paused"}

pytestmark = pytest.mark.usefixtures("frozen")


@pytest.fixture
def frozen():
    with freeze_time("2026-11-18 10:00:00"):
        yield


def goal_of(session, user, title="Python", hours=2.0) -> LearningGoal:
    goal = LearningGoal(user_id=user.id, title=title, weekly_hours_target=hours)
    session.add(goal)
    session.commit()
    session.refresh(goal)
    return goal


def task_of(session, user, goal, done=False) -> LearningTask:
    task = learning.add_task(session, user, goal, title="Lecture 4", due_date=TODAY, minutes=45)
    if done:
        task = learning.set_done(session, user, task, True)
    return task


@pytest.fixture
def goal(session, user) -> LearningGoal:
    return goal_of(session, user)


@pytest.fixture
def task(session, user, goal) -> LearningTask:
    return task_of(session, user, goal)


@pytest.fixture
def done(session, user, goal) -> LearningTask:
    return task_of(session, user, goal, done=True)


@pytest.fixture
def other(session) -> User:
    return create_user(session, email=OTHER_EMAIL, password=OTHER_PASSWORD, full_name="Omar")


def post(client, path, headers=HTMX, **data):
    return client.post(
        path,
        data=data | {"csrf_token": csrf_token(client)},
        headers=headers,
        follow_redirects=False,
    )


def fresh(session, model, row_id):
    session.expire_all()
    return session.get(model, row_id)


def spent(session, user, task) -> list[int]:
    session.expire_all()
    return list(
        session.exec(
            select(StudyLog.minutes).where(StudyLog.user_id == user.id, StudyLog.task_id == task.id)
        )
    )


def unchanged_task(session, task_id) -> bool:
    row = fresh(session, LearningTask, task_id)
    return (row.title, row.due_date, row.estimated_minutes) == ("Lecture 4", TODAY, 45)


def unchanged_goal(session, goal_id) -> bool:
    row = fresh(session, LearningGoal, goal_id)
    return (row.title, row.target_date, row.weekly_hours_target, row.status) == (
        "Python",
        None,
        2.0,
        "active",
    )


# --- Logged out ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["/learning/tasks/{task}/edit", "/learning/goals/{goal}/edit"])
def test_a_form_htmx_logged_out_asks_for_a_full_page_login(client, task, goal, url):
    response = client.get(
        url.format(task=task.id, goal=goal.id),
        headers=HTMX | {"HX-Current-URL": "http://testserver/learning"},
        follow_redirects=False,
    )

    assert response.headers["HX-Redirect"] == "/login?next=/learning"
    assert "Lecture 4" not in response.text and "Python" not in response.text


@pytest.mark.parametrize(
    ("url", "data"),
    [("/learning/tasks/{task}", OPEN), ("/learning/goals/{goal}", GOAL)],
)
def test_saving_htmx_logged_out_redirects_and_saves_nothing(client, session, task, goal, url, data):
    response = client.post(
        url.format(task=task.id, goal=goal.id),
        data=data,
        headers=HTMX
        | {"X-CSRF-Token": csrf_token(client), "HX-Current-URL": "http://testserver/learning"},
        follow_redirects=False,
    )

    assert response.headers["HX-Redirect"] == "/login?next=/learning"
    assert unchanged_task(session, task.id) and unchanged_goal(session, goal.id)


@pytest.mark.parametrize(
    ("url", "data"),
    [("/learning/tasks/{task}", OPEN), ("/learning/goals/{goal}", GOAL)],
)
def test_saving_plain_logged_out_goes_to_login(client, session, task, goal, url, data):
    response = post(client, url.format(task=task.id, goal=goal.id), headers={}, **data)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    assert unchanged_task(session, task.id) and unchanged_goal(session, goal.id)


# --- Another user's ids -------------------------------------------------------------------


@pytest.fixture
def others(session, other):
    """The other user's goal, open task and done task."""
    goal = goal_of(session, other, title="Python")
    return goal, task_of(session, other, goal), task_of(session, other, goal, done=True)


@pytest.mark.parametrize("which", ["open", "done"])
def test_another_users_task_form_is_not_found(auth_client, others, which):
    _, open_task, done_task = others
    task = open_task if which == "open" else done_task

    response = auth_client.get(f"/learning/tasks/{task.id}/edit", headers=HTMX)

    assert response.status_code == 404
    assert "Lecture 4" not in response.text


def test_another_users_done_task_is_not_saved(auth_client, session, other, others):
    _, _, done_task = others

    response = post(auth_client, f"/learning/tasks/{done_task.id}", **DONE)

    assert response.status_code == 404
    assert fresh(session, LearningTask, done_task.id).title == "Lecture 4"
    assert spent(session, other, done_task) == [45]


@pytest.mark.parametrize("data", [OPEN, DONE, {}])
def test_another_users_task_is_not_found_whatever_is_sent(auth_client, session, others, data):
    # The owner check comes before the `kind` check: no "changed in the meantime"
    # message that would tell the task exists.
    _, open_task, _ = others

    response = post(auth_client, f"/learning/tasks/{open_task.id}", **data)

    assert response.status_code == 404
    assert "changed in the meantime" not in response.text
    assert unchanged_task(session, open_task.id)


def test_another_users_goal_is_not_found_with_bad_values(auth_client, session, others):
    # A 404, not the form with errors: errors would tell the goal exists.
    goal, _, _ = others

    response = post(auth_client, f"/learning/goals/{goal.id}", title="", status="nope")

    assert response.status_code == 404
    assert "Choose from the list." not in response.text


def test_the_other_user_cannot_edit_mine(client, session, user, task, goal, other):
    log_in(client, OTHER_EMAIL, OTHER_PASSWORD)

    assert client.get(f"/learning/tasks/{task.id}/edit").status_code == 404
    assert client.get(f"/learning/goals/{goal.id}/edit").status_code == 404
    assert post(client, f"/learning/tasks/{task.id}", **OPEN).status_code == 404
    assert post(client, f"/learning/goals/{goal.id}", **GOAL).status_code == 404
    assert unchanged_task(session, task.id) and unchanged_goal(session, goal.id)


@pytest.mark.parametrize("bad_id", ["abc", "0", "-1", "99999999999", "1.5"])
@pytest.mark.parametrize("url", ["/learning/tasks/{}", "/learning/goals/{}"])
def test_bad_ids_on_save_are_not_found(auth_client, bad_id, url):
    response = post(auth_client, url.format(bad_id), **(OPEN | GOAL))

    assert response.status_code == 404


# --- Huge and odd values -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", HUGE),
        ("title", "   "),
        ("due_date", HUGE),
        ("due_date", "2026-02-30"),
        ("due_date", "99999-01-01"),
        ("minutes", HUGE),
        ("minutes", "9" * 5000),  # more digits than int() reads
        ("minutes", "1e3"),
        ("minutes", "-30"),
        ("minutes", "30.5"),
        ("minutes", "nan"),
        ("minutes", ""),
    ],
)
def test_huge_or_odd_open_task_values_save_nothing(auth_client, session, task, field, value):
    response = post(auth_client, f"/learning/tasks/{task.id}", **(OPEN | {field: value}))

    assert response.status_code == 200
    assert f'id="edit-task-{field}-error"' in response.text
    assert "HX-Trigger" not in response.headers
    assert unchanged_task(session, task.id)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", HUGE),
        ("spent_minutes", HUGE),
        ("spent_minutes", "9" * 5000),
        ("spent_minutes", "100000"),
        ("spent_minutes", "-1"),
        ("spent_minutes", "inf"),
    ],
)
def test_huge_or_odd_done_task_values_save_nothing(auth_client, session, user, done, field, value):
    response = post(auth_client, f"/learning/tasks/{done.id}", **(DONE | {field: value}))

    assert f'id="edit-task-{field}-error"' in response.text
    assert "HX-Trigger" not in response.headers
    assert fresh(session, LearningTask, done.id).title == "Lecture 4"
    assert spent(session, user, done) == [45]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", HUGE),
        ("target_date", HUGE),
        ("target_date", "2031-11-19"),  # just over 5 years
        ("weekly_hours", HUGE),
        ("weekly_hours", "9" * 5000),
        ("weekly_hours", "1e309"),  # float infinity
        ("weekly_hours", "inf"),
        ("weekly_hours", "nan"),
        ("weekly_hours", "-1"),
        ("weekly_hours", "0.4"),
        ("status", HUGE),
        ("status", "active\x00"),
        ("status", "Active"),
    ],
)
def test_huge_or_odd_goal_values_save_nothing(auth_client, session, goal, field, value):
    response = post(auth_client, f"/learning/goals/{goal.id}", **(GOAL | {field: value}))

    assert response.status_code == 200
    assert f'id="edit-goal-{field}-error"' in response.text
    assert "HX-Trigger" not in response.headers
    assert unchanged_goal(session, goal.id)


def test_a_missing_goal_status_is_refused(auth_client, session, goal):
    data = {key: value for key, value in GOAL.items() if key != "status"}

    response = post(auth_client, f"/learning/goals/{goal.id}", **data)

    assert 'id="edit-goal-status-error"' in response.text
    assert unchanged_goal(session, goal.id)


def test_the_longest_title_is_saved(auth_client, session, task):
    post(auth_client, f"/learning/tasks/{task.id}", **(OPEN | {"title": "x" * 200}))

    assert fresh(session, LearningTask, task.id).title == "x" * 200


def test_a_title_is_escaped_in_the_form(auth_client, session, task):
    post(auth_client, f"/learning/tasks/{task.id}", **(OPEN | {"title": "<script>x</script>"}))

    html = auth_client.get(f"/learning/tasks/{task.id}/edit").text

    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


# --- The hidden `kind` field --------------------------------------------------------------


@pytest.mark.parametrize("kind", [None, "", "closed", "OPEN", " open ", "done", HUGE])
def test_an_open_task_needs_kind_open(auth_client, session, task, kind):
    data = OPEN | {"kind": kind}
    data = {key: value for key, value in data.items() if value is not None}

    response = post(auth_client, f"/learning/tasks/{task.id}", **data)

    assert response.status_code == 200
    assert "This task changed in the meantime." in response.text
    assert 'name="kind" value="open"' in response.text
    assert HUGE not in response.text
    assert "HX-Trigger" not in response.headers
    assert unchanged_task(session, task.id)


@pytest.mark.parametrize("kind", [None, "", "open", "DONE", "yes"])
def test_a_done_task_needs_kind_done(auth_client, session, user, done, kind):
    data = DONE | {"kind": kind}
    data = {key: value for key, value in data.items() if value is not None}

    response = post(auth_client, f"/learning/tasks/{done.id}", **data)

    assert "This task changed in the meantime." in response.text
    assert 'name="kind" value="done"' in response.text
    assert "HX-Trigger" not in response.headers
    assert fresh(session, LearningTask, done.id).title == "Lecture 4"
    assert spent(session, user, done) == [45]


def test_an_unticked_task_with_a_done_form_saves_nothing(auth_client, session, user, done):
    learning.set_done(session, user, done, False)  # unticked in another tab

    response = post(auth_client, f"/learning/tasks/{done.id}", **DONE)

    assert "This task changed in the meantime." in response.text
    assert 'name="kind" value="open"' in response.text
    assert fresh(session, LearningTask, done.id).title == "Lecture 4"


# --- Smuggled fields ----------------------------------------------------------------------


def test_a_sent_user_id_or_goal_id_does_not_move_a_task(
    auth_client, session, user, task, goal, other
):
    other_goal = goal_of(session, other, title="Theirs")
    own_second = goal_of(session, user, title="Second")

    for goal_id in (other_goal.id, own_second.id):
        post(
            auth_client,
            f"/learning/tasks/{task.id}",
            **(OPEN | {"user_id": str(other.id), "goal_id": str(goal_id)}),
        )

    row = fresh(session, LearningTask, task.id)
    assert (row.user_id, row.goal_id, row.title) == (user.id, goal.id, "Renamed")


def test_sent_done_and_id_fields_are_ignored_on_an_open_task(auth_client, session, task):
    post(
        auth_client,
        f"/learning/tasks/{task.id}",
        **(OPEN | {"done": "yes", "id": "999", "estimated_minutes": "600", "url": "http://x"}),
    )

    row = fresh(session, LearningTask, task.id)
    assert (row.id, row.done, row.estimated_minutes, row.url) == (task.id, False, 30, None)


def test_a_done_task_ignores_a_sent_date_and_duration(auth_client, session, user, done):
    # The done form has no date or duration; sending them changes neither.
    post(
        auth_client,
        f"/learning/tasks/{done.id}",
        **(DONE | {"due_date": "2027-01-01", "minutes": "600", "done": "no"}),
    )

    row = fresh(session, LearningTask, done.id)
    assert (row.done, row.due_date, row.estimated_minutes) == (True, TODAY, 45)
    assert spent(session, user, done) == [30]


def test_a_sent_user_id_does_not_move_a_goal(auth_client, session, user, goal, other):
    post(
        auth_client,
        f"/learning/goals/{goal.id}",
        **(GOAL | {"user_id": str(other.id), "id": "999", "color": "5"}),
    )

    row = fresh(session, LearningGoal, goal.id)
    assert (row.id, row.user_id, row.title) == (goal.id, user.id, "Renamed")
    assert learning.goals(session, other) == []


def test_editing_a_done_task_writes_no_log_for_another_user(
    auth_client, session, user, done, other
):
    post(auth_client, f"/learning/tasks/{done.id}", **(DONE | {"user_id": str(other.id)}))

    session.expire_all()
    assert session.exec(select(StudyLog).where(StudyLog.user_id == other.id)).all() == []
    assert spent(session, user, done) == [30]


# --- Rate limit ---------------------------------------------------------------------------


def test_saving_a_task_is_rate_limited(auth_client, session, task):
    token = csrf_token(auth_client)

    codes = [
        auth_client.post(
            f"/learning/tasks/{task.id}",
            data=OPEN | {"title": f"T{i}", "csrf_token": token},
            headers=HTMX,
        ).status_code
        for i in range(61)
    ]

    assert codes[:60] == [200] * 60 and codes[60] == 429
    assert fresh(session, LearningTask, task.id).title == "T59"  # the 61st saved nothing


def test_refused_saves_count_for_the_limit(auth_client, task):
    # Bad values cost the same as good ones, so guessing can't go on forever.
    token = csrf_token(auth_client)
    for _ in range(60):
        auth_client.post(
            f"/learning/tasks/{task.id}",
            data={"kind": "nope", "csrf_token": token},
            headers=HTMX,
        )

    response = post(auth_client, f"/learning/tasks/{task.id}", **OPEN)

    assert response.status_code == 429


def test_opening_the_forms_is_not_limited_by_saves(auth_client, task, goal):
    token = csrf_token(auth_client)
    for _ in range(60):
        auth_client.post(
            f"/learning/tasks/{task.id}", data=OPEN | {"csrf_token": token}, headers=HTMX
        )

    assert auth_client.get(f"/learning/tasks/{task.id}/edit").status_code == 200
    assert post(auth_client, f"/learning/goals/{goal.id}", **GOAL).status_code == 200


# --- htmx vs plain requests ---------------------------------------------------------------


@pytest.mark.parametrize("headers", [HTMX, {}])
def test_the_forms_are_fragments(auth_client, task, goal, headers):
    for url in (f"/learning/tasks/{task.id}/edit", f"/learning/goals/{goal.id}/edit"):
        response = auth_client.get(url, headers=headers)

        assert response.status_code == 200
        assert "<html" not in response.text and "<form" in response.text


@pytest.mark.parametrize(
    ("url", "data"),
    [("/learning/tasks/{task}", OPEN), ("/learning/goals/{goal}", GOAL)],
)
def test_a_plain_save_saves_like_an_htmx_one(auth_client, session, task, goal, url, data):
    response = post(auth_client, url.format(task=task.id, goal=goal.id), headers={}, **data)

    assert response.status_code == 200
    assert fresh(session, LearningTask, task.id).title == (
        "Renamed" if "tasks" in url else "Lecture 4"
    )
    assert fresh(session, LearningGoal, goal.id).title == (
        "Renamed" if "goals" in url else "Python"
    )


def test_a_plain_save_with_errors_saves_nothing(auth_client, session, task):
    response = post(
        auth_client, f"/learning/tasks/{task.id}", headers={}, **(OPEN | {"minutes": "0"})
    )

    assert response.status_code == 200
    assert 'id="edit-task-minutes-error"' in response.text
    assert unchanged_task(session, task.id)


def test_a_wrong_csrf_token_is_refused_on_both_saves(auth_client, session, task, goal):
    for url, data in ((f"/learning/tasks/{task.id}", OPEN), (f"/learning/goals/{goal.id}", GOAL)):
        response = auth_client.post(url, data=data | {"csrf_token": "nope"}, headers=HTMX)

        assert response.status_code == 403
    assert unchanged_task(session, task.id) and unchanged_goal(session, goal.id)
