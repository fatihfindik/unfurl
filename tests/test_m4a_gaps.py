"""Gaps in the M4a tests: logged-out access, other users, odd undo tokens, wrong methods,
odd addresses, rate limits and the per-user job limit (SPEC §6.7, D-061…D-070)."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.main import app
from app.models import Application
from app.services import application_service as apps
from app.services import undo_service
from app.services.auth_service import create_user
from tests.helpers import csrf_token, log_in

OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "y" * 12


def send(client, method, url, **data):
    return client.request(
        method,
        url,
        data=data,
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)},
    )


def toast_of(response) -> dict:
    return json.loads(response.headers["HX-Trigger"])["toast"]


def fresh(session, job) -> Application:
    session.expire_all()
    return session.get(Application, job.id)


def all_jobs(session) -> list[Application]:
    session.expire_all()
    return list(session.exec(select(Application)))


def event_ids(session, job) -> list[int]:
    session.expire_all()
    return [event.id for event in apps.events_of(session, job)]


@pytest.fixture
def other(session):
    return create_user(session, email=OTHER_EMAIL, password=OTHER_PASSWORD, full_name="Other")


@pytest.fixture
def other_client(client, other):
    """A second browser, logged in as `other`. The database override of `client` is
    set on the app, so this client uses the test database too."""
    second = TestClient(app)
    assert log_in(second, OTHER_EMAIL, OTHER_PASSWORD).status_code == 303
    return second


@pytest.fixture
def job(session, user) -> Application:
    return apps.create(session, user, company="Acme", job_title="PM")


@pytest.fixture
def theirs(session, other) -> Application:
    return apps.create(session, other, company="Theirs", job_title="PM")


# --- Logged out: every new route ---------------------------------------------------------


def write_routes(job_id) -> list[tuple[str, str, dict]]:
    return [
        ("POST", "/applications", {"company": "A", "job_title": "B", "status": "saved"}),
        ("PATCH", f"/applications/{job_id}/move", {"status": "applied"}),
        ("POST", "/applications/undo", {"token": "x"}),
        ("POST", f"/applications/{job_id}/archive", {}),
        ("POST", f"/applications/{job_id}/restore", {}),
        ("POST", "/settings/theme", {"theme": "dark"}),
    ]


@pytest.mark.parametrize("index", range(6))
def test_logged_out_writes_without_htmx_go_to_login_with_303(client, session, job, index):
    method, url, data = write_routes(job.id)[index]
    # A logged-out visitor has a CSRF token too (from /login), so only the login is missing.
    response = client.request(
        method,
        url,
        data=data,
        headers={"X-CSRF-Token": csrf_token(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    # Not a GET: a form sent without a session is never replayed after login.
    assert response.headers["location"] == "/login"
    job = fresh(session, job)
    assert (job.status, job.archived) == ("saved", False)
    assert len(all_jobs(session)) == 1


@pytest.mark.parametrize("index", range(6))
def test_logged_out_htmx_writes_get_hx_redirect_not_303(client, session, job, index):
    method, url, data = write_routes(job.id)[index]

    response = send(client, method, url, **data)

    assert response.status_code == 200
    assert response.headers["HX-Redirect"].startswith("/login")
    assert "location" not in response.headers
    assert fresh(session, job).status == "saved"


def test_logged_out_page_keeps_the_address_in_next(client):
    response = client.get("/?view=list&q=acme", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/%3Fview%3Dlist%26q%3Dacme"


def test_logged_out_htmx_page_request_returns_to_the_whole_page(client):
    response = client.get(
        "/?view=list",
        headers={"HX-Request": "true", "HX-Current-URL": "http://testserver/?view=list&q=acme"},
    )

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/login?next=/%3Fview%3Dlist%26q%3Dacme"
    assert "applications-region" not in response.text


@pytest.mark.parametrize(
    "current", ["https://evil.example//evil.example", "http://testserver/\\evil.example", ""]
)
def test_logged_out_htmx_page_request_never_redirects_off_site(client, current):
    response = client.get("/", headers={"HX-Request": "true", "HX-Current-URL": current})

    assert response.headers["HX-Redirect"] == "/login"


def test_an_ended_session_cannot_write(auth_client, session, user, job):
    # After a password change the session_version goes up and old sessions stop working.
    user.session_version += 1
    session.add(user)
    session.commit()

    response = send(auth_client, "PATCH", f"/applications/{job.id}/move", status="applied")

    assert response.headers["HX-Redirect"].startswith("/login")
    assert fresh(session, job).status == "saved"


# --- CSRF on every write route -----------------------------------------------------------


@pytest.mark.parametrize("index", range(6))
@pytest.mark.parametrize("token", [None, "", "wrong-token"])
def test_every_write_needs_the_right_csrf_token(auth_client, session, user, job, index, token):
    method, url, data = write_routes(job.id)[index]
    headers = {"HX-Request": "true"}
    if token is not None:
        headers["X-CSRF-Token"] = token

    response = auth_client.request(method, url, data=data, headers=headers)

    assert response.status_code == 403
    job = fresh(session, job)
    assert (job.status, job.archived) == ("saved", False)
    assert len(all_jobs(session)) == 1
    session.refresh(user)
    assert user.theme == "system"


def test_the_csrf_token_of_another_session_is_refused(auth_client, other_client, session, job):
    response = auth_client.post(
        f"/applications/{job.id}/archive",
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(other_client)},
    )

    assert response.status_code == 403
    assert fresh(session, job).archived is False


# --- Another user's application ----------------------------------------------------------


def test_another_users_archived_card_cannot_be_moved(auth_client, session, other, theirs):
    apps.archive(session, other, theirs)

    response = send(auth_client, "PATCH", f"/applications/{theirs.id}/move", status="saved")

    assert response.status_code == 404
    assert fresh(session, theirs).archived is True


@pytest.mark.parametrize("status", ["applied", "saved"])
def test_another_users_card_as_after_id_is_not_found_and_changes_nothing(
    auth_client, session, user, job, theirs, status
):
    response = send(
        auth_client,
        "PATCH",
        f"/applications/{job.id}/move",
        status=status,
        after_id=str(theirs.id),
    )

    assert response.status_code == 404
    assert fresh(session, job).status == "saved"
    assert event_ids(session, job) == [apps.events_of(session, job)[0].id]  # no new event


@pytest.mark.parametrize("after_id", ["abc", "0", "-1", str(2**70), " ", "1.5"])
def test_an_after_id_that_is_not_an_id_is_not_found(auth_client, session, job, after_id):
    response = send(
        auth_client, "PATCH", f"/applications/{job.id}/move", status="applied", after_id=after_id
    )

    assert response.status_code == 404
    assert fresh(session, job).status == "saved"


def test_an_own_archived_card_as_after_id_puts_the_card_at_the_top(auth_client, session, user, job):
    archived = apps.create(session, user, company="Archived", job_title="PM", status="applied")
    apps.archive(session, user, archived)
    apps.create(session, user, company="On board", job_title="PM", status="applied")

    response = send(
        auth_client,
        "PATCH",
        f"/applications/{job.id}/move",
        status="applied",
        after_id=str(archived.id),
    )

    assert response.status_code == 204
    session.expire_all()
    assert [c.company for c in apps.column_of(session, user, "applied")] == ["Acme", "On board"]


def test_another_user_sees_none_of_my_jobs_on_any_view(other_client, session, user, job):
    apps.archive(session, user, apps.create(session, user, company="Hidden", job_title="PM"))
    for url in ("/", "/?view=list", "/?view=list&archived=1", "/?archived=1&q=acme"):
        html = other_client.get(url).text
        assert "Acme" not in html and "Hidden" not in html


@pytest.mark.parametrize("action", ["archive", "restore"])
def test_user_b_cannot_archive_or_restore_user_as_job(other_client, session, user, job, action):
    if action == "restore":
        apps.archive(session, user, job)

    response = send(other_client, "POST", f"/applications/{job.id}/{action}")

    assert response.status_code == 404
    assert fresh(session, job).archived is (action == "restore")


# --- Undo tokens between users -----------------------------------------------------------


def test_user_as_undo_token_sent_by_user_b_does_nothing(
    auth_client, other_client, session, user, job
):
    response = send(auth_client, "PATCH", f"/applications/{job.id}/move", status="applied")
    token = toast_of(response)["undo_token"]

    result = send(other_client, "POST", "/applications/undo", token=token)

    assert result.status_code == 204
    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, job).status == "applied"


def test_a_token_for_user_a_naming_user_bs_job_does_nothing_for_user_b(
    other_client, session, user, other, theirs
):
    # Signed for A but pointing at B's job, sent by B: the user in the token decides.
    event = apps.events_of(session, theirs)[-1]
    token = undo_service.make_token(user, theirs.id, [event.id], before=None)

    result = send(other_client, "POST", "/applications/undo", token=token)

    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, theirs) is not None


def test_user_bs_own_token_cannot_reach_user_as_job(other_client, session, other, user, job):
    # B makes a real move, then the token is re-signed with A's job id (as if B knew
    # the secret key): the job must still be out of B's reach.
    before = apps.before_note(session, user, job)
    event = apps.move(session, user, job, "applied")
    token = undo_service.make_token(other, job.id, [event.id], before)

    result = send(other_client, "POST", "/applications/undo", token=token)

    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, job).status == "applied"


# --- Undo tokens with odd shapes (correctly signed) ----------------------------------------


@pytest.fixture
def moved(session, user, job):
    """`job` moved Saved -> Applied; returns (job, the move event id, the before note)."""
    before = apps.before_note(session, user, job)
    event = apps.move(session, user, job, "applied")
    return job, event.id, before


def signed(data) -> str:
    return undo_service._serializer().dumps(data)


def odd_tokens(user_id, job_id, event_id, before) -> dict[str, object]:
    good = {"u": user_id, "a": job_id, "e": [event_id], "b": before}
    return {
        "not a dict": [user_id, job_id, [event_id], before],
        "user as text": good | {"u": str(user_id)},
        "user missing": {k: v for k, v in good.items() if k != "u"},
        "events not a list": good | {"e": event_id},
        "events as text": good | {"e": [str(event_id)]},
        "events empty": good | {"e": []},
        "events None": good | {"e": None},
        "events a dict": good | {"e": {"0": event_id}},
        "application missing": {k: v for k, v in good.items() if k != "a"},
        "application text": good | {"a": "abc"},
        "application None": good | {"a": None},
        "application a list": good | {"a": [job_id]},
        "application too big": good | {"a": 2**70},
        "before a string": good | {"b": "saved"},
        "before a list": good | {"b": ["saved"]},
        "before unknown status": good | {"b": before | {"status": "withdrawn"}},
        "before status moon": good | {"b": before | {"status": "moon"}},
        "before status missing": good | {"b": {"index": 0}},
    }


ODD_SHAPES = list(odd_tokens(1, 1, 1, {}).keys())


@pytest.mark.parametrize("shape", ODD_SHAPES)
def test_an_odd_but_signed_token_changes_nothing(auth_client, session, user, moved, shape):
    job, event_id, before = moved
    token = signed(odd_tokens(user.id, job.id, event_id, before)[shape])

    result = send(auth_client, "POST", "/applications/undo", token=token)

    assert result.status_code == 204
    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, job).status == "applied"
    assert event_id in event_ids(session, job)


def test_an_application_id_as_text_still_only_finds_own_jobs(
    auth_client, session, user, other, theirs
):
    before = apps.before_note(session, other, theirs)
    event = apps.move(session, other, theirs, "applied")
    token = signed({"u": user.id, "a": str(theirs.id), "e": [event.id], "b": before})

    result = send(auth_client, "POST", "/applications/undo", token=token)

    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, theirs).status == "applied"


def test_events_of_another_job_cannot_be_deleted_with_a_token(auth_client, session, user, moved):
    # The token names this job's newest event plus an event of another job of the same
    # user: nothing may be deleted.
    job, event_id, before = moved
    second = apps.create(session, user, company="Second", job_title="PM")
    second_event = apps.events_of(session, second)[-1].id
    token = signed({"u": user.id, "a": job.id, "e": [second_event, event_id], "b": before})

    result = send(auth_client, "POST", "/applications/undo", token=token)

    assert toast_of(result)["message"] == "Nothing to undo."
    assert event_ids(session, second) == [second_event]
    assert event_id in event_ids(session, job)


def test_a_foreign_older_event_with_the_newest_event_is_refused(auth_client, session, user, job):
    # Here the other job's event is older, so the "newest event" check passes and the
    # delete must notice that one event is not this job's.
    older = apps.create(session, user, company="Older", job_title="PM")
    older_event = apps.events_of(session, older)[-1].id
    before = apps.before_note(session, user, job)
    event = apps.move(session, user, job, "applied")
    token = signed({"u": user.id, "a": job.id, "e": [older_event, event.id], "b": before})

    result = send(auth_client, "POST", "/applications/undo", token=token)

    assert toast_of(result)["message"] == "Nothing to undo."
    assert event_ids(session, older) == [older_event]
    assert fresh(session, job).status == "applied"
    assert event.id in event_ids(session, job)


BROKEN_BEFORE = {
    "index as text": {"index": "abc"},
    "last_activity_at missing": {"last_activity_at": None},
    "last_activity_at not a date": {"last_activity_at": "yesterday"},
    "applied_at not a date": {"applied_at": "soon"},
}


# Found by test-writer (M4a): these gave a 500 before undo() checked the values.
@pytest.mark.parametrize("shape", list(BROKEN_BEFORE))
def test_a_before_note_with_broken_values_says_nothing_to_undo(
    auth_client, session, user, moved, shape
):
    job, event_id, before = moved
    token = signed({"u": user.id, "a": job.id, "e": [event_id], "b": before | BROKEN_BEFORE[shape]})

    result = send(auth_client, "POST", "/applications/undo", token=token)

    assert result.status_code == 204
    assert toast_of(result)["message"] == "Nothing to undo."


@pytest.mark.parametrize("shape", list(BROKEN_BEFORE))
def test_a_before_note_with_broken_values_changes_nothing(client, session, user, moved, shape):
    # Whatever the answer is, the events and the job must stay as they are (rollback).
    job, event_id, before = moved
    browser = TestClient(app, raise_server_exceptions=False)
    log_in(browser)
    token = signed({"u": user.id, "a": job.id, "e": [event_id], "b": before | BROKEN_BEFORE[shape]})

    send(browser, "POST", "/applications/undo", token=token)

    assert fresh(session, job).status == "applied"
    assert event_id in event_ids(session, job)


# --- Undo when something happened in between ---------------------------------------------


def test_undo_of_a_move_after_the_job_was_archived_does_nothing(auth_client, session, user, job):
    moved = send(auth_client, "PATCH", f"/applications/{job.id}/move", status="applied")
    send(auth_client, "POST", f"/applications/{job.id}/archive")

    result = send(auth_client, "POST", "/applications/undo", token=toast_of(moved)["undo_token"])

    assert toast_of(result)["message"] == "Nothing to undo."
    job = fresh(session, job)
    assert (job.status, job.archived) == ("applied", True)


def test_undo_of_adding_after_the_job_was_archived_keeps_the_job(auth_client, session, user):
    added = send(auth_client, "POST", "/applications", company="Acme", job_title="PM")
    job = all_jobs(session)[0]
    send(auth_client, "POST", f"/applications/{job.id}/archive")

    result = send(auth_client, "POST", "/applications/undo", token=toast_of(added)["undo_token"])

    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, job).archived is True


def test_undo_of_archive_after_a_restore_does_nothing(auth_client, session, user, job):
    archived = send(auth_client, "POST", f"/applications/{job.id}/archive")
    send(auth_client, "POST", f"/applications/{job.id}/restore")

    result = send(auth_client, "POST", "/applications/undo", token=toast_of(archived)["undo_token"])

    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, job).archived is False


def test_an_archive_token_works_only_once(auth_client, session, user, job):
    archived = send(auth_client, "POST", f"/applications/{job.id}/archive")
    token = toast_of(archived)["undo_token"]

    first = send(auth_client, "POST", "/applications/undo", token=token)
    second = send(auth_client, "POST", "/applications/undo", token=token)

    assert toast_of(first)["message"] == "Undone."
    assert toast_of(second)["message"] == "Nothing to undo."
    assert fresh(session, job).archived is False


def test_undo_is_rate_limited(auth_client):
    codes = [
        send(auth_client, "POST", "/applications/undo", token="x").status_code for _ in range(61)
    ]

    assert codes[:60] == [204] * 60
    assert codes[60] == 429


# --- Wrong HTTP methods ------------------------------------------------------------------


@pytest.mark.parametrize(
    "method, path",
    [
        ("PUT", "/applications"),
        ("PATCH", "/applications"),
        ("DELETE", "/applications"),
        ("GET", "/applications/undo"),
        ("PUT", "/applications/undo"),
        ("DELETE", "/applications/undo"),
        ("GET", "/applications/{id}/archive"),
        ("DELETE", "/applications/{id}/archive"),
        ("PATCH", "/applications/{id}/archive"),
        ("GET", "/applications/{id}/restore"),
        ("DELETE", "/applications/{id}/restore"),
        ("PUT", "/applications/{id}/move"),
        ("GET", "/settings/theme"),
        ("PUT", "/settings/theme"),
        ("DELETE", "/settings/theme"),
    ],
)
def test_wrong_methods_are_refused(auth_client, session, job, method, path):
    response = auth_client.request(
        method,
        path.format(id=job.id),
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(auth_client)},
    )

    assert response.status_code == 405
    job = fresh(session, job)
    assert (job.status, job.archived) == ("saved", False)


def test_there_is_no_delete_route_for_a_job(auth_client, session, job):
    # Jobs are archived, never deleted from the page (D-066).
    response = auth_client.delete(
        f"/applications/{job.id}", headers={"X-CSRF-Token": csrf_token(auth_client)}
    )

    assert response.status_code in (404, 405)
    assert fresh(session, job) is not None


# --- Odd addresses on GET / --------------------------------------------------------------


ODD_QUERIES = [
    "more=" + ",".join(["saved"] * 2000),
    "more=" + "x" * 10_000,
    "page=" + "9" * 5000,  # more digits than int() accepts by default
    "page=%00",
    "view=list&page=" + "9" * 5000,
    "view=list&view=board&view=" + "x" * 5000,
    "sort=" + "x" * 10_000,
    "view=list&sort=company&sort=-company",
    "q=%00%01%02%1b",
    "q=%F0%9F%98%80%E2%80%AE",  # emoji and right-to-left override
    "q=%ff%fe",  # not valid UTF-8
    "source=" + "linkedin" * 1000,
    "mode=remote%00",
    "status=saved&view=board",
    "archived=11&archived=1",
    "&&&=&==",
    "q=" + "%25" * 3000,
]


@pytest.mark.parametrize("query", ODD_QUERIES)
@pytest.mark.parametrize("htmx", [False, True])
def test_odd_addresses_still_show_the_page(auth_client, session, user, job, query, htmx):
    headers = {"HX-Request": "true"} if htmx else {}

    response = auth_client.get("/?" + query, headers=headers)

    assert response.status_code == 200
    assert 'id="applications-region"' in response.text
    assert "x" * 1000 not in response.text and "9" * 1000 not in response.text


def test_an_htmx_page_request_never_sends_the_whole_page(auth_client, job):
    html = auth_client.get("/?view=list&q=acme", headers={"HX-Request": "true"}).text

    assert "<html" not in html
    assert 'id="applications-region"' in html
    assert "hx-swap-oob" in html


def test_a_history_restore_without_hx_request_is_a_normal_page(auth_client, job):
    html = auth_client.get("/", headers={"HX-History-Restore-Request": "true"}).text

    assert "<html" in html


# --- The job limit per user (D-061) --------------------------------------------------------


@pytest.fixture
def small_limit(monkeypatch):
    monkeypatch.setattr(apps, "MAX_APPLICATIONS_PER_USER", 2)


def add_job(client, company="New"):
    return send(client, "POST", "/applications", company=company, job_title="PM", status="saved")


def test_archived_jobs_count_toward_the_limit(auth_client, session, user, small_limit):
    for name in ("A", "B"):
        apps.archive(session, user, apps.create(session, user, company=name, job_title="PM"))

    response = add_job(auth_client)

    assert response.status_code == 200
    assert "You have reached the limit" in response.text
    assert "HX-Trigger" not in response.headers  # no toast, no undo token
    assert len(all_jobs(session)) == 2


def test_at_the_limit_existing_jobs_can_still_change(auth_client, session, user, small_limit):
    first = apps.create(session, user, company="A", job_title="PM")
    apps.create(session, user, company="B", job_title="PM")

    moved = send(auth_client, "PATCH", f"/applications/{first.id}/move", status="applied")
    archived = send(auth_client, "POST", f"/applications/{first.id}/archive")
    restored = send(auth_client, "POST", f"/applications/{first.id}/restore")
    undone = send(auth_client, "POST", "/applications/undo", token=toast_of(restored)["undo_token"])

    assert [r.status_code for r in (moved, archived, restored, undone)] == [204] * 4
    assert toast_of(undone)["message"] == "Undone."
    assert fresh(session, first).archived is True


def test_undoing_an_add_makes_room_again(auth_client, session, user, small_limit):
    apps.create(session, user, company="A", job_title="PM")
    added = add_job(auth_client, "B")
    send(auth_client, "POST", "/applications/undo", token=toast_of(added)["undo_token"])

    response = add_job(auth_client, "C")

    assert "You have reached the limit" not in response.text
    session.expire_all()
    assert sorted(a.company for a in all_jobs(session)) == ["A", "C"]


def test_another_users_jobs_do_not_count_toward_my_limit(
    auth_client, session, user, other, small_limit
):
    for name in ("X", "Y"):
        apps.create(session, other, company=name, job_title="PM")

    response = add_job(auth_client, "Mine")

    assert "You have reached the limit" not in response.text
    assert [a.company for a in apps.column_of(session, user, "saved")] == ["Mine"]


# --- Theme route and theme cookie --------------------------------------------------------


@pytest.mark.parametrize("theme", ["x" * 10_000, "dark\r\nSet-Cookie: a=b", "light dark"])
def test_odd_theme_values_are_refused(auth_client, session, user, theme):
    response = send(auth_client, "POST", "/settings/theme", theme=theme)

    assert response.status_code == 422
    assert "set-cookie" not in response.headers
    session.refresh(user)
    assert user.theme == "system"


def test_a_theme_sent_as_json_is_refused(auth_client, session, user):
    response = auth_client.post(
        "/settings/theme",
        json={"theme": "dark"},
        headers={"X-CSRF-Token": csrf_token(auth_client)},
    )

    assert response.status_code == 422
    session.refresh(user)
    assert user.theme == "system"


def test_a_failed_login_does_not_set_the_theme_cookie(client, session, user):
    user.theme = "dark"
    session.add(user)
    session.commit()

    response = log_in(client, password="wrong password")

    assert "theme=" not in response.headers.get("set-cookie", "")


def test_saving_a_theme_changes_only_my_account(auth_client, session, user, other):
    send(auth_client, "POST", "/settings/theme", theme="dark")

    session.refresh(user)
    session.refresh(other)
    assert (user.theme, other.theme) == ("dark", "system")


# --- Legal pages with the optional current_user ------------------------------------------


@pytest.mark.parametrize("path", ["/privacy", "/terms", "/imprint"])
def test_legal_pages_use_the_account_theme(auth_client, session, user, path):
    user.theme = "dark"
    session.add(user)
    session.commit()
    auth_client.cookies.set("theme", "light")

    html = auth_client.get(path).text

    assert '<html lang="en" class="theme-dark">' in html


@pytest.mark.parametrize("path", ["/privacy", "/terms", "/imprint"])
def test_legal_pages_still_open_with_an_ended_session(auth_client, session, user, path):
    user.session_version += 1
    user.theme = "dark"
    session.add(user)
    session.commit()

    response = auth_client.get(path, follow_redirects=False)

    assert response.status_code == 200
    assert "data-save-url" not in response.text  # treated as a visitor now


@pytest.mark.parametrize("path", ["/privacy", "/terms", "/imprint"])
def test_legal_pages_open_after_the_account_was_deleted(auth_client, session, user, path):
    session.delete(user)
    session.commit()

    response = auth_client.get(path, follow_redirects=False)

    assert response.status_code == 200
    assert "data-save-url" not in response.text


def test_a_deleted_account_cannot_reach_the_board(auth_client, session, user):
    session.delete(user)
    session.commit()

    response = auth_client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
