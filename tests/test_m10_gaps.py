"""Gaps in the M10 tests: logged-out access and rate limits on every Learning route,
odd ids, other users' rows, signed but odd undo tokens, and DeletedItem copies of the
learning kinds that are broken or point at someone else's rows (D-089…D-096, D-086)."""

import datetime as dt
import json

import pytest
from itsdangerous import URLSafeTimedSerializer
from sqlmodel import select

from app.config import get_settings
from app.models import (
    DeletedItem,
    LearningGoal,
    LearningResource,
    LearningTask,
    StudyLog,
)
from app.services import clock, undo_service
from app.services import learning_service as learning
from app.services.auth_service import create_user
from tests.helpers import csrf_token

# Wednesday 18 November 2026, 10:00 in Vienna.
NOW = dt.datetime(2026, 11, 18, 9, 0, tzinfo=dt.UTC)
WED = dt.date(2026, 11, 18)
BAD_IDS = ["abc", "0", "-1", "1.5", "1e3", "99999", str(2**31), str(2**70)]


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(clock, "utcnow", lambda: NOW)


def htmx(client) -> dict:
    return {"HX-Request": "true", "X-CSRF-Token": csrf_token(client)}


def post(client, url, **fields):
    return client.post(url, data=fields, headers=htmx(client))


def toast(response) -> str:
    return json.loads(response.headers.get("HX-Trigger", "{}"))["toast"]["message"]


def signed_delete(data) -> str:
    """A delete token with any content, signed like the app signs them."""
    secret = get_settings().secret_key.get_secret_value()
    return URLSafeTimedSerializer(secret, salt="undo-delete").dumps(data)


def signed_learning(data) -> str:
    return undo_service._learning_serializer().dumps(data)


def all_rows(session, model) -> list:
    session.expire_all()
    return list(session.exec(select(model)))


@pytest.fixture
def goal(session, user) -> LearningGoal:
    """Python with CS50P (3 weeks) and three weekly tasks; Week 1 is done."""
    goal = learning.create_goal(
        session,
        user,
        title="Python",
        target_date=None,
        weekly_hours=2.0,
        resource={"name": "CS50P", "units_total": 3},
        plan_weekly=True,
    )
    learning.set_done(session, user, learning.tasks(session, user)[0], True)
    return goal


@pytest.fixture
def task(session, user, goal) -> LearningTask:
    return learning.tasks(session, user)[1]  # "Week 2", open


@pytest.fixture
def other(session):
    return create_user(session, email="other@example.test", password="x" * 12, full_name="O")


@pytest.fixture
def their_goal(session, other) -> LearningGoal:
    goal = learning.create_goal(
        session,
        other,
        title="Their goal",
        target_date=None,
        weekly_hours=1.0,
        resource={"name": "Their course", "units_total": 2},
        plan_weekly=True,
    )
    learning.set_done(session, other, learning.tasks(session, other)[0], True)
    return goal


# --- Logged out ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "/learning",
        "/learning?view=all",
        "/learning?view=done&goal=1",
        "/learning/tasks/new",
        "/learning/goals/1/delete",
    ],
)
def test_logged_out_pages_go_to_login(client, url):
    response = client.get(url, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.parametrize(
    "url",
    [
        "/learning/goals",
        "/learning/tasks",
        "/learning/tasks/{task}/done",
        "/learning/undo",
        "/learning/rest-days",
        "/learning/tasks/{task}/reschedule",
        "/learning/tasks/{task}/delete",
        "/learning/goals/{goal}/delete",
        "/undo/delete",
    ],
)
def test_logged_out_changes_go_to_login(client, session, user, goal, task, url):
    response = client.post(
        url.format(task=task.id, goal=goal.id),
        data={"title": "G", "done": "yes", "due_date": "2026-11-20", "rest_days": ["mon"]},
        headers={"X-CSRF-Token": csrf_token(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    # Nothing changed.
    assert [g.title for g in learning.goals(session, user)] == ["Python"]
    session.expire_all()
    second = learning.tasks(session, user)[1]
    assert (second.done, second.due_date) == (False, task.due_date)
    assert len(learning.tasks(session, user)) == 3
    assert learning.preferences(session, user).rest_days == ["sat", "sun"]


# --- Rate limits --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "/learning/tasks",
        "/learning/tasks/{task}/done",
        "/learning/undo",
        "/learning/rest-days",
        "/learning/tasks/{task}/reschedule",
        "/learning/tasks/{task}/delete",
        "/learning/goals/{goal}/delete",
        "/undo/delete",
    ],
)
def test_learning_posts_are_rate_limited(auth_client, goal, task, url):
    headers = htmx(auth_client)
    target = url.format(task=task.id, goal=goal.id)
    # The deletes answer 404 after the first one; those still count.
    codes = [
        auth_client.post(target, data={"due_date": "2026-11-20"}, headers=headers).status_code
        for _ in range(61)
    ]

    assert 429 not in codes[:60]
    assert codes[60] == 429


# --- Odd ids and other users' ids ---------------------------------------------------------

ID_ROUTES = [
    ("post", "/learning/tasks/{id}/done"),
    ("post", "/learning/tasks/{id}/reschedule"),
    ("post", "/learning/tasks/{id}/delete"),
    ("get", "/learning/goals/{id}/delete"),
    ("post", "/learning/goals/{id}/delete"),
]


@pytest.mark.parametrize(("method", "url"), ID_ROUTES)
@pytest.mark.parametrize("bad_id", BAD_IDS)
def test_odd_ids_are_404(auth_client, session, user, goal, method, url, bad_id):
    target = url.format(id=bad_id)
    if method == "get":
        response = auth_client.get(target)
    else:
        response = post(auth_client, target, done="yes", due_date="2026-11-20")

    assert response.status_code == 404
    assert len(learning.tasks(session, user)) == 3
    assert len(learning.goals(session, user)) == 1


def test_other_users_rows_are_untouched_by_all_id_routes(auth_client, session, their_goal, other):
    their_tasks = learning.tasks(session, other)
    before = [(t.id, t.done, t.due_date) for t in their_tasks]

    for method, url in ID_ROUTES:
        row_id = their_goal.id if "goals" in url else their_tasks[1].id
        target = url.format(id=row_id)
        if method == "get":
            response = auth_client.get(target)
        else:
            response = post(auth_client, target, done="yes", due_date="2026-11-20")
        assert response.status_code == 404, url
        # Nothing about their goal leaks into the 404 page.
        assert "Their goal" not in response.text

    session.expire_all()
    assert [(t.id, t.done, t.due_date) for t in learning.tasks(session, other)] == before
    assert [g.id for g in learning.goals(session, other)] == [their_goal.id]
    assert all_rows(session, DeletedItem) == []


@pytest.mark.parametrize("goal_id", ["abc", "0", "-1", "1.5", str(2**31), str(2**70)])
def test_add_task_with_an_odd_goal_id_asks_for_a_goal(auth_client, session, user, goal, goal_id):
    response = post(
        auth_client, "/learning/tasks",
        goal_id=goal_id, title="T", due_date="2026-11-18", minutes="45",
    )  # fmt: skip

    assert response.status_code == 200
    assert "Choose a goal." in response.text
    assert len(learning.tasks(session, user)) == 3


@pytest.mark.parametrize("goal_id", ["abc", "-1", str(2**70)])
def test_task_form_with_an_odd_goal_preselects_nothing(auth_client, goal, goal_id):
    response = auth_client.get(f"/learning/tasks/new?goal={goal_id}")

    assert response.status_code == 200
    select_html = response.text.split('name="goal_id"', 1)[1].split("</select>", 1)[0]
    assert "selected" not in select_html


# --- Undo of a tick: signed but odd tokens ------------------------------------------------


def test_learning_undo_with_my_signature_on_their_task_does_nothing(
    auth_client, session, user, their_goal, other
):
    theirs = learning.tasks(session, other)[0]  # done
    token = signed_learning({"u": user.id, "t": theirs.id, "d": True})

    response = post(auth_client, "/learning/undo", token=token)

    assert toast(response) == "Nothing to undo."
    session.refresh(theirs)
    assert theirs.done


@pytest.mark.parametrize(
    "data",
    [
        {"t": 1, "d": True},  # no user
        {"u": "USER", "t": "1", "d": True},
        {"u": "USER", "t": 2**70, "d": True},
        {"u": "USER", "t": 0, "d": True},
        {"u": "USER", "t": 1, "d": "yes"},
        {"u": "USER", "t": True, "d": True},
        [1, True],
        "text",
    ],
)
def test_learning_undo_with_odd_signed_tokens_does_nothing(auth_client, session, user, goal, data):
    if isinstance(data, dict) and data.get("u") == "USER":
        data = data | {"u": user.id}
    first = learning.tasks(session, user)[0]

    response = post(auth_client, "/learning/undo", token=signed_learning(data))

    assert response.status_code == 204
    assert toast(response) == "Nothing to undo."
    session.refresh(first)
    assert first.done


def test_learning_undo_is_rate_limited_per_route_not_shared_with_ticks(auth_client, task):
    # 60 ticks don't use up the Undo limit.
    headers = htmx(auth_client)
    for _ in range(60):
        auth_client.post(f"/learning/tasks/{task.id}/done", data={"done": "yes"}, headers=headers)

    response = auth_client.post("/learning/undo", data={"token": ""}, headers=headers)

    assert response.status_code == 204


# --- Undo of a delete: tokens -------------------------------------------------------------


def test_my_token_for_their_learning_copy_does_nothing(
    auth_client, session, user, other, their_goal
):
    copy_id = learning.delete_goal(session, other, their_goal)

    response = undo_copy(auth_client, user, copy_id)

    assert response.status_code == 204
    assert learning.goals(session, user) == []
    assert learning.goals(session, other) == []
    assert [item.id for item in all_rows(session, DeletedItem)] == [copy_id]  # still theirs


@pytest.mark.parametrize(
    "data",
    [{"d": "ID"}, {"u": "USER", "d": "1"}, {"u": "USER", "d": 2**70}, {"u": "USER"}, [1, 2]],
)
def test_odd_signed_delete_tokens_do_nothing(auth_client, session, user, task, data):
    copy_id = learning.delete_task(session, user, task)
    if isinstance(data, dict):
        names = {"USER": user.id, "ID": copy_id}
        data = {key: names.get(value, value) for key, value in data.items()}

    response = post(auth_client, "/undo/delete", token=signed_delete(data))

    assert response.status_code == 204
    assert len(learning.tasks(session, user)) == 2
    assert len(all_rows(session, DeletedItem)) == 1


def test_a_task_delete_undo_works_once(auth_client, session, user, task):
    deleted = post(auth_client, f"/learning/tasks/{task.id}/delete")
    token = json.loads(deleted.headers["HX-Trigger"])["toast"]["undo_token"]
    post(auth_client, "/undo/delete", token=token)

    post(auth_client, "/undo/delete", token=token)

    assert len(learning.tasks(session, user)) == 3
    assert "Nothing to undo." in auth_client.get("/learning").text


def test_learning_tick_token_is_no_delete_token(auth_client, session, user, task):
    copy_id = learning.delete_task(session, user, task)
    tick_token = signed_learning({"u": user.id, "d": copy_id, "t": copy_id})

    post(auth_client, "/undo/delete", token=tick_token)

    assert len(learning.tasks(session, user)) == 2


# --- Undo of a delete: limits -------------------------------------------------------------


def test_restoring_a_task_respects_the_task_limit(auth_client, session, user, task, monkeypatch):
    deleted = post(auth_client, f"/learning/tasks/{task.id}/delete")
    monkeypatch.setattr(learning, "MAX_TASKS", 2)  # the two tasks left fill it

    response = post(
        auth_client,
        "/undo/delete",
        token=json.loads(deleted.headers["HX-Trigger"])["toast"]["undo_token"],
    )

    assert response.status_code == 204
    assert [t.title for t in learning.tasks(session, user)] == ["Week 1", "Week 3"]
    assert "Nothing to undo." in auth_client.get("/learning").text
    assert len(all_rows(session, DeletedItem)) == 1  # rolled back, the copy stays


def test_restoring_a_goal_respects_the_task_limit(auth_client, session, user, goal, monkeypatch):
    deleted = post(auth_client, f"/learning/goals/{goal.id}/delete")
    session.expunge(goal)  # the deleted goal's object; the new goal may get its id
    learning.add_task(
        session,
        user,
        learning.create_goal(
            session, user, title="Other", target_date=None, weekly_hours=None,
            resource=None, plan_weekly=False,
        ),
        title="Only task", due_date=WED, minutes=25,
    )  # fmt: skip
    monkeypatch.setattr(learning, "MAX_TASKS", 3)  # 1 + the goal's 3 tasks is one too many

    post(
        auth_client,
        "/undo/delete",
        token=json.loads(deleted.headers["HX-Trigger"])["toast"]["undo_token"],
    )

    assert [g.title for g in learning.goals(session, user)] == ["Other"]
    assert [t.title for t in learning.tasks(session, user)] == ["Only task"]
    assert all_rows(session, LearningResource) == []


# --- Undo of a delete: copies that point at someone else's rows ---------------------------


def add_copy(session, user, kind, payload) -> int:
    item = DeletedItem(user_id=user.id, kind=kind, payload=payload)
    session.add(item)
    session.commit()
    return item.id


def undo_copy(client, user, copy_id):
    return post(client, "/undo/delete", token=undo_service.make_delete_token(user, copy_id))


def task_payload(goal_id, **more) -> dict:
    task = {
        "old_id": 999,
        "goal_id": goal_id,
        "title": "Restored",
        "due_date": "2026-11-20",
        "estimated_minutes": 30,
        "done": False,
    }
    return {"task": task | more, "logs": []}


def test_a_task_copy_cannot_go_to_their_goal(auth_client, session, user, other, their_goal):
    copy_id = add_copy(session, user, "learning_task", task_payload(their_goal.id))

    undo_copy(auth_client, user, copy_id)

    assert [t.title for t in learning.tasks(session, other)] == ["Week 1", "Week 2"]
    assert learning.tasks(session, user) == []
    assert "Nothing to undo." in auth_client.get("/learning").text


def test_a_task_copy_drops_their_resource_and_their_user_id(
    auth_client, session, user, other, goal, their_goal
):
    their_resource = session.exec(
        select(LearningResource).where(LearningResource.user_id == other.id)
    ).one()
    payload = task_payload(goal.id, resource_id=their_resource.id, user_id=other.id)
    copy_id = add_copy(session, user, "learning_task", payload)

    undo_copy(auth_client, user, copy_id)

    [restored] = [t for t in learning.tasks(session, user) if t.title == "Restored"]
    assert (restored.user_id, restored.resource_id) == (user.id, None)
    assert [t.title for t in learning.tasks(session, other)] == ["Week 1", "Week 2"]


def test_a_copy_cannot_take_their_study_logs(auth_client, session, user, other, goal, their_goal):
    [their_log] = session.exec(select(StudyLog).where(StudyLog.user_id == other.id)).all()
    before = (their_log.goal_id, their_log.task_id)
    payload = task_payload(goal.id)
    payload["logs"] = [[their_log.id, 999]]
    copy_id = add_copy(session, user, "learning_task", payload)

    undo_copy(auth_client, user, copy_id)

    session.refresh(their_log)
    assert (their_log.user_id, their_log.goal_id, their_log.task_id) == (other.id, *before)


# --- Undo of a delete: broken copies ------------------------------------------------------
# Copies are written by the app, but a broken one must answer "Nothing to undo."
# and leave the data as it was, never a 500.

BROKEN_TASK_COPIES = [
    {},
    [],
    "text",
    {"task": None},
    {"task": {"goal_id": "abc"}},
    {"task": {"goal_id": None}},
    {"task": {"goal_id": "GOAL"}},  # no title, no date
    task_payload("GOAL", due_date="not a date"),
    task_payload("GOAL", done="yes"),  # was a 500 before the M10 review fixes
    task_payload("GOAL", resource_id="abc"),
    task_payload("GOAL") | {"logs": "x"},
    task_payload("GOAL") | {"logs": [["abc", 1]]},
    {"task": {k: v for k, v in task_payload("GOAL")["task"].items() if k != "old_id"}},
]

BROKEN_GOAL_COPIES = [
    {},
    [],
    {"goal": {"title": "G"}, "resources": [], "tasks": None},
    {"goal": None, "resources": [], "tasks": []},  # was a 500 before the M10 review fixes
    {"goal": {}, "resources": [], "tasks": []},  # no title
    {"goal": {"title": "G", "target_date": "soon"}, "resources": [], "tasks": []},
    {"goal": {"title": "G", "source_application_id": "abc"}, "resources": [], "tasks": []},
    {"goal": {"title": "G"}, "resources": [{"name": "R"}], "tasks": []},  # no old_id
    {"goal": {"title": "G"}, "resources": [{"old_id": 1}], "tasks": []},  # no name
    {"goal": {"title": "G"}, "resources": [], "tasks": [{"old_id": 1, "title": "T"}]},
    {
        "goal": {"title": "G"},
        "resources": [],
        "tasks": [{"old_id": 1, "title": "T", "due_date": "2026-11-20", "resource_id": [1]}],
    },
    {"goal": {"title": "G"}, "resources": [], "tasks": [], "logs": [[1]]},
]


def with_goal(payload, goal_id):
    """Put the real goal id where a copy says "GOAL"."""
    text = json.dumps(payload).replace('"GOAL"', str(goal_id))
    return json.loads(text)


@pytest.mark.parametrize("payload", BROKEN_TASK_COPIES)
def test_broken_task_copies_are_nothing_to_undo(auth_client, session, user, goal, payload):
    copy_id = add_copy(session, user, "learning_task", with_goal(payload, goal.id))

    response = undo_copy(auth_client, user, copy_id)

    assert response.status_code == 204
    assert len(learning.tasks(session, user)) == 3
    assert "Nothing to undo." in auth_client.get("/learning").text


@pytest.mark.parametrize("payload", BROKEN_GOAL_COPIES)
def test_broken_goal_copies_are_nothing_to_undo(auth_client, session, user, payload):
    copy_id = add_copy(session, user, "learning_goal", payload)

    response = undo_copy(auth_client, user, copy_id)

    assert response.status_code == 204
    assert learning.goals(session, user) == []
    assert learning.tasks(session, user) == []
    assert "Nothing to undo." in auth_client.get("/learning").text


# Ids in a copy are checked like database ids (was a 500 before the M10 review fixes).
@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("learning_task", {"task": {"goal_id": 2**70}}),
        ("learning_task", task_payload("GOAL", resource_id=2**70)),
        ("learning_task", task_payload("GOAL") | {"logs": [[2**70, 999]]}),
        ("learning_goal", {"goal": {"title": "G", "source_application_id": 2**70},
                           "resources": [], "tasks": []}),
    ],
)  # fmt: skip
def test_copies_with_huge_ids_are_nothing_to_undo(auth_client, session, user, goal, kind, payload):
    copy_id = add_copy(session, user, kind, with_goal(payload, goal.id))

    response = undo_copy(auth_client, user, copy_id)

    assert response.status_code == 204
    assert len(learning.goals(session, user)) == 1
    assert len(learning.tasks(session, user)) == 3
