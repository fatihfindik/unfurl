"""Gaps in the M4b tests: logged-out access, odd ids, other users, CSRF, rate limits and
archived jobs on every new route; odd version-2 undo tokens; Needs attention with
snoozes and SQLite's naive datetimes; the detail page in every state (D-071…D-079)."""

import datetime as dt
import json
import secrets

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Application
from app.models.application import BOARD_STATUSES
from app.models.base import utcnow
from app.services import application_service as apps
from app.services import clock, undo_service
from app.services.auth_service import create_user
from tests.helpers import csrf_token, log_in

OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "y" * 12
TOMORROW = (clock.today() + dt.timedelta(days=1)).isoformat()
IN_TWO_DAYS = (clock.today() + dt.timedelta(days=2)).isoformat()


def send(client, method, url, **data):
    return client.request(
        method,
        url,
        data=data,
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)},
    )


def triggers(response) -> dict:
    return json.loads(response.headers["HX-Trigger"])


def toast_of(response) -> dict:
    return triggers(response)["toast"]


def undo(client, token):
    return send(client, "POST", "/applications/undo", token=token)


def fresh(session, job) -> Application:
    session.expire_all()
    return session.get(Application, job.id)


def event_ids(session, job) -> list[int]:
    session.expire_all()
    return [event.id for event in apps.events_of(session, job)]


def snapshot(session, job) -> tuple:
    """Everything the new routes can change, to check that nothing did."""
    job = fresh(session, job)
    return (
        job.status,
        job.archived,
        job.company,
        job.next_action_manual,
        job.next_action_type,
        job.interview_at,
        job.snoozed_until,
        len(apps.events_of(session, job)),
    )


def signed(data) -> str:
    return undo_service._serializer().dumps(data)


@pytest.fixture
def other(session):
    return create_user(session, email=OTHER_EMAIL, password=OTHER_PASSWORD, full_name="Other")


@pytest.fixture
def other_client(client, other):
    """A second browser, logged in as `other` (same test database as `client`)."""
    second = TestClient(app)
    assert log_in(second, OTHER_EMAIL, OTHER_PASSWORD).status_code == 303
    return second


@pytest.fixture
def job(session, user) -> Application:
    return apps.create(session, user, company="Acme", job_title="PM")


@pytest.fixture
def theirs(session, other) -> Application:
    return apps.create(session, other, company="Theirs", job_title="PM")


# Every new write route with data that would change something (a saved job).
WRITES = {
    "status": ("/status", {"status": "applied"}),
    "interview": ("/interview", {"date": IN_TWO_DAYS}),
    "next-step": ("/next-step", {"next_action_type": "custom", "date": TOMORROW, "note": "x"}),
    "done": ("/next-step/done", {}),
    "notes": ("/notes", {"text": "Hello"}),
    "details": ("/details", {"company": "Changed", "job_title": "PM"}),
    "snooze": ("/snooze", {}),
}
READS = ["", "/interview", "/next-step", "/next-step/edit", "/details", "/details/edit"]


# --- Logged out --------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(WRITES))
def test_logged_out_writes_without_htmx_go_to_login_with_303(client, session, job, name):
    path, data = WRITES[name]
    # A logged-out visitor has a CSRF token too (from /login), so only the login is missing.
    response = client.post(
        f"/applications/{job.id}{path}",
        data=data,
        headers={"X-CSRF-Token": csrf_token(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"  # a POST is never replayed after login
    assert snapshot(session, job) == ("saved", False, "Acme", False, None, None, None, 1)


@pytest.mark.parametrize("name", list(WRITES))
def test_logged_out_htmx_writes_get_hx_redirect(client, session, job, name):
    path, data = WRITES[name]

    response = send(client, "POST", f"/applications/{job.id}{path}", **data)

    assert response.status_code == 200
    assert response.headers["HX-Redirect"].startswith("/login")
    assert "location" not in response.headers
    assert snapshot(session, job) == ("saved", False, "Acme", False, None, None, None, 1)


@pytest.mark.parametrize("path", READS)
def test_logged_out_pages_go_to_login_and_come_back(client, job, path):
    response = client.get(f"/applications/{job.id}{path}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/login?next=/applications/{job.id}{path}"
    assert "Acme" not in response.text


@pytest.mark.parametrize("path", READS)
def test_logged_out_htmx_pieces_return_to_the_whole_page(client, job, path):
    # A piece (e.g. the Next step card) asked by htmx: back to the page it sits on.
    response = client.get(
        f"/applications/{job.id}{path}",
        headers={
            "HX-Request": "true",
            "HX-Current-URL": f"http://testserver/applications/{job.id}",
        },
    )

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == f"/login?next=/applications/{job.id}"
    assert "Acme" not in response.text


def test_an_ended_session_cannot_change_the_status(auth_client, session, user, job):
    user.session_version += 1  # e.g. after a password change
    session.add(user)
    session.commit()

    response = send(auth_client, "POST", f"/applications/{job.id}/status", status="applied")

    assert response.headers["HX-Redirect"].startswith("/login")
    assert fresh(session, job).status == "saved"


# --- Odd ids -----------------------------------------------------------------------------

BAD_IDS = ["abc", "0", "-1", "1.5", str(2**31), str(2**70), "1e3"]


@pytest.mark.parametrize("bad_id", BAD_IDS)
@pytest.mark.parametrize("name", list(WRITES))
def test_bad_ids_are_not_found_on_every_write(auth_client, session, job, name, bad_id):
    path, data = WRITES[name]

    response = send(auth_client, "POST", f"/applications/{bad_id}{path}", **data)

    assert response.status_code == 404
    assert snapshot(session, job) == ("saved", False, "Acme", False, None, None, None, 1)


@pytest.mark.parametrize("bad_id", BAD_IDS)
@pytest.mark.parametrize("path", READS[1:])  # the page itself: test_application_detail.py
def test_bad_ids_are_not_found_on_every_piece(auth_client, path, bad_id):
    assert auth_client.get(f"/applications/{bad_id}{path}").status_code == 404


def test_a_deleted_job_is_not_found(auth_client, session, job):
    job_id = job.id
    session.delete(job)
    session.commit()

    assert auth_client.get(f"/applications/{job_id}").status_code == 404
    response = send(auth_client, "POST", f"/applications/{job_id}/notes", text="x")
    assert response.status_code == 404


# --- Another user's application ----------------------------------------------------------


@pytest.mark.parametrize("name", list(WRITES))
def test_user_b_cannot_write_to_user_as_job(auth_client, session, theirs, name):
    path, data = WRITES[name]

    response = send(auth_client, "POST", f"/applications/{theirs.id}{path}", **data)

    assert response.status_code == 404  # not 403: the job's existence is not leaked
    assert "Theirs" not in response.text
    assert snapshot(session, theirs) == ("saved", False, "Theirs", False, None, None, None, 1)


@pytest.mark.parametrize("path", READS)
def test_user_b_cannot_see_any_piece_of_user_as_job(auth_client, theirs, path):
    response = auth_client.get(f"/applications/{theirs.id}{path}")

    assert response.status_code == 404
    assert "Theirs" not in response.text


@pytest.mark.parametrize("name", ["status", "interview", "notes", "details", "snooze", "done"])
def test_user_b_cannot_write_to_user_as_archived_job(auth_client, session, other, theirs, name):
    apps.archive(session, other, theirs)
    path, data = WRITES[name]

    response = send(auth_client, "POST", f"/applications/{theirs.id}{path}", **data)

    assert response.status_code == 404
    assert snapshot(session, theirs) == ("saved", True, "Theirs", False, None, None, None, 2)


def test_my_detail_page_links_never_lead_to_another_users_jobs(auth_client, session, other, job):
    # "‹ 1 of n ›" only counts my own jobs in the column.
    # Long names: a two-letter one can show up by chance in the random CSRF token.
    for name in ("Other Users First Corp", "Other Users Second Corp"):
        apps.create(session, other, company=name, job_title="PM")

    html = auth_client.get(f"/applications/{job.id}").text

    assert "Other Users" not in html
    for theirs in apps.column_of(session, other, "saved"):
        assert f"/applications/{theirs.id}" not in html


# --- CSRF --------------------------------------------------------------------------------


@pytest.mark.parametrize("token", [None, "", "wrong-token"])
@pytest.mark.parametrize("name", list(WRITES))
def test_every_new_write_needs_the_right_csrf_token(auth_client, session, job, name, token):
    path, data = WRITES[name]
    headers = {"HX-Request": "true"}
    if token is not None:
        headers["X-CSRF-Token"] = token

    response = auth_client.post(f"/applications/{job.id}{path}", data=data, headers=headers)

    assert response.status_code == 403
    assert snapshot(session, job) == ("saved", False, "Acme", False, None, None, None, 1)


@pytest.mark.parametrize("name", list(WRITES))
def test_the_csrf_token_of_another_session_is_refused(
    auth_client, other_client, session, job, name
):
    path, data = WRITES[name]

    response = auth_client.post(
        f"/applications/{job.id}{path}",
        data=data,
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(other_client)},
    )

    assert response.status_code == 403
    assert snapshot(session, job) == ("saved", False, "Acme", False, None, None, None, 1)


def test_the_csrf_token_in_the_form_field_works_too(auth_client, session, job):
    # A normal form (no htmx) sends the token as a field instead of a header.
    response = auth_client.post(
        f"/applications/{job.id}/notes",
        data={"text": "Hello", "csrf_token": csrf_token(auth_client)},
    )

    assert response.status_code == 200
    assert snapshot(session, job)[-1] == 2


# --- Rate limits -------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["status", "interview", "next-step", "done", "details", "snooze"])
def test_writes_are_limited_to_60_a_minute(auth_client, session, job, name):
    path, data = WRITES[name]
    headers = {"HX-Request": "true", "X-CSRF-Token": csrf_token(auth_client)}
    url = f"/applications/{job.id}{path}"

    codes = [auth_client.post(url, data=data, headers=headers).status_code for _ in range(61)]

    assert all(code < 400 for code in codes[:60])
    assert codes[60] == 429


def test_a_limited_status_change_changes_nothing(auth_client, session, job):
    headers = {"HX-Request": "true", "X-CSRF-Token": csrf_token(auth_client)}
    url = f"/applications/{job.id}/status"
    for _ in range(60):
        auth_client.post(url, data={"status": "applied"}, headers=headers)

    response = auth_client.post(url, data={"status": "offer"}, headers=headers)

    assert response.status_code == 429
    assert fresh(session, job).status == "applied"


def test_a_limited_note_is_not_saved(auth_client, session, job):
    headers = {"HX-Request": "true", "X-CSRF-Token": csrf_token(auth_client)}
    url = f"/applications/{job.id}/notes"
    codes = [
        auth_client.post(url, data={"text": "n"}, headers=headers).status_code for _ in range(31)
    ]

    assert codes[30] == 429
    assert len(event_ids(session, job)) == 1 + 30


def test_each_route_has_its_own_limit(auth_client, session, job):
    # Using up the status limit does not block adding a note.
    headers = {"HX-Request": "true", "X-CSRF-Token": csrf_token(auth_client)}
    for _ in range(61):
        auth_client.post(
            f"/applications/{job.id}/status", data={"status": "applied"}, headers=headers
        )

    response = auth_client.post(
        f"/applications/{job.id}/notes", data={"text": "x"}, headers=headers
    )

    assert response.status_code == 200


# --- Archived jobs on every write route ----------------------------------------------------


@pytest.fixture
def archived(session, user, job) -> Application:
    apps.move(session, user, job, "applied")
    apps.archive(session, user, job)
    return fresh(session, job)


def test_picking_archived_for_an_archived_job_changes_nothing(auth_client, session, archived):
    before = event_ids(session, archived)

    response = send(auth_client, "POST", f"/applications/{archived.id}/status", status="archived")

    assert response.status_code == 204
    assert response.headers["HX-Trigger"] == "application-changed"  # no toast, no Undo
    assert event_ids(session, archived) == before
    assert fresh(session, archived).archived is True


def test_saving_an_interview_restores_an_archived_job_and_undo_archives_it_again(
    auth_client, session, user, archived
):
    before = event_ids(session, archived)

    response = send(auth_client, "POST", f"/applications/{archived.id}/interview", date=IN_TWO_DAYS)

    job = fresh(session, archived)
    assert (job.status, job.archived) == ("interview", False)
    assert job.interview_at is not None
    assert [c.id for c in apps.column_of(session, user, "interview")] == [job.id]

    result = undo(auth_client, toast_of(response)["undo_token"])

    assert toast_of(result)["message"] == "Undone."
    job = fresh(session, archived)
    assert (job.status, job.archived, job.interview_at) == ("applied", True, None)
    assert event_ids(session, archived) == before
    assert apps.column_of(session, user, "applied") == []  # off the board again


def test_done_on_an_archived_job_changes_nothing(auth_client, session, archived):
    before = event_ids(session, archived)

    response = send(auth_client, "POST", f"/applications/{archived.id}/next-step/done")

    assert response.status_code == 204
    assert "toast" not in response.headers.get("HX-Trigger", "")
    assert event_ids(session, archived) == before
    assert fresh(session, archived).next_action_manual is False


def test_snoozing_an_archived_job_changes_nothing(auth_client, session, archived):
    response = send(auth_client, "POST", f"/applications/{archived.id}/snooze")

    assert response.status_code == 204
    assert "toast" not in response.headers.get("HX-Trigger", "")
    assert fresh(session, archived).snoozed_until is None


def test_next_step_of_an_archived_job_is_refused_even_with_bad_values(
    auth_client, session, archived
):
    # 409 before the form is read: an archived job has no next step at all.
    response = send(
        auth_client, "POST", f"/applications/{archived.id}/next-step", next_action_type="moon"
    )

    assert response.status_code == 409
    assert fresh(session, archived).next_action_manual is False


def test_details_of_an_archived_job_can_be_edited_and_it_stays_archived(
    auth_client, session, archived
):
    response = send(
        auth_client, "POST", f"/applications/{archived.id}/details", company="New", job_title="PM"
    )

    assert response.status_code == 200
    job = fresh(session, archived)
    assert (job.company, job.archived, job.status) == ("New", True, "applied")


def test_notes_on_an_archived_job_are_kept_and_it_stays_archived(auth_client, session, archived):
    response = send(auth_client, "POST", f"/applications/{archived.id}/notes", text="Too far")

    assert response.status_code == 200
    assert "Too far" in response.text
    assert fresh(session, archived).archived is True


@pytest.mark.parametrize("path", READS)
def test_every_piece_of_an_archived_job_opens(auth_client, archived, path):
    assert auth_client.get(f"/applications/{archived.id}{path}").status_code == 200


# --- Wrong HTTP methods ------------------------------------------------------------------


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/status"),
        ("PUT", "/status"),
        ("DELETE", "/interview"),
        ("PUT", "/interview"),
        ("DELETE", "/next-step"),
        ("PATCH", "/next-step"),
        ("GET", "/next-step/done"),
        ("DELETE", "/next-step/done"),
        ("GET", "/notes"),
        ("DELETE", "/notes"),
        ("PUT", "/details"),
        ("DELETE", "/details"),
        ("POST", "/details/edit"),
        ("POST", "/next-step/edit"),
        ("GET", "/snooze"),
        ("DELETE", "/snooze"),
        ("PUT", ""),
        ("PATCH", ""),
        ("POST", ""),
    ],
)
def test_wrong_methods_are_refused(auth_client, session, job, method, path):
    response = auth_client.request(
        method,
        f"/applications/{job.id}{path}",
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(auth_client)},
    )

    assert response.status_code == 405
    assert snapshot(session, job) == ("saved", False, "Acme", False, None, None, None, 1)


def test_notes_cannot_be_deleted(auth_client, session, job):
    # The timeline only grows: no route deletes a note.
    send(auth_client, "POST", f"/applications/{job.id}/notes", text="Keep me")
    note_id = event_ids(session, job)[-1]

    response = auth_client.delete(
        f"/applications/{job.id}/notes/{note_id}",
        headers={"X-CSRF-Token": csrf_token(auth_client)},
    )

    assert response.status_code in (404, 405)
    assert note_id in event_ids(session, job)


# --- Version-2 undo tokens: correctly signed but odd ---------------------------------------


def status_token(client, session, job, status="applied") -> dict:
    """Change the status with the picker; returns the signed data of its undo token."""
    response = send(client, "POST", f"/applications/{job.id}/status", status=status)
    token = toast_of(response)["undo_token"]
    return undo_service._serializer().loads(token)


def test_a_token_pointed_at_another_of_my_jobs_does_nothing(auth_client, session, user, job):
    # Job A's token, re-signed for job B (same change, same "after" values), with B's
    # newest event: the events in it are A's, so nothing may be deleted or written back.
    second = apps.create(session, user, company="Second", job_title="PM")
    apps.move(session, user, second, "applied")
    data = status_token(auth_client, session, job)
    second_events = event_ids(session, second)
    token = signed(data | {"a": second.id, "n": second_events[-1]})

    result = undo(auth_client, token)

    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, second).status == "applied"
    assert event_ids(session, second) == second_events
    assert data["e"][0] in event_ids(session, job)


def test_a_token_pointed_at_another_users_job_does_nothing(
    auth_client, session, user, other, theirs, job
):
    apps.move(session, other, theirs, "applied")
    data = status_token(auth_client, session, job)
    theirs_events = event_ids(session, theirs)
    token = signed(data | {"a": theirs.id, "n": theirs_events[-1], "e": [theirs_events[-1]]})

    result = undo(auth_client, token)

    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, theirs).status == "applied"
    assert event_ids(session, theirs) == theirs_events


@pytest.mark.parametrize("shift", ["minus one", "plus one", "none", "true", "text", "float"])
def test_a_newest_event_that_does_not_match_does_nothing(auth_client, session, job, shift):
    data = status_token(auth_client, session, job)
    n = data["n"]
    newest = {
        "minus one": n - 1,
        "plus one": n + 1,
        "none": None,
        "true": True,
        "text": str(n),
        "float": float(n),
    }[shift]

    result = undo(auth_client, signed(data | {"n": newest}))

    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, job).status == "applied"
    assert data["e"][0] in event_ids(session, job)


def test_a_missing_newest_event_does_nothing(auth_client, session, job):
    data = status_token(auth_client, session, job)
    del data["n"]

    result = undo(auth_client, signed(data))

    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, job).status == "applied"


@pytest.mark.parametrize("which", ["only the other job's", "both"])
def test_events_of_another_job_are_never_deleted(auth_client, session, user, job, which):
    second = apps.create(session, user, company="Second", job_title="PM")
    second_event = event_ids(session, second)[0]
    data = status_token(auth_client, session, job)
    events = [second_event] if which == "only the other job's" else [second_event, *data["e"]]

    result = undo(auth_client, signed(data | {"e": events}))

    assert toast_of(result)["message"] == "Nothing to undo."
    assert event_ids(session, second) == [second_event]
    assert data["e"][0] in event_ids(session, job)  # rolled back, not half deleted
    assert fresh(session, job).status == "applied"


def test_events_of_another_users_job_are_never_deleted(
    auth_client, session, user, other, theirs, job
):
    their_event = event_ids(session, theirs)[0]
    data = status_token(auth_client, session, job)

    result = undo(auth_client, signed(data | {"e": [their_event, *data["e"]]}))

    assert toast_of(result)["message"] == "Nothing to undo."
    assert event_ids(session, theirs) == [their_event]


@pytest.mark.parametrize("index", [True, False, 1.5, "0", [0], {"i": 0}])
def test_an_index_of_the_wrong_type_does_nothing(auth_client, session, job, index):
    data = status_token(auth_client, session, job)
    fields = data["f"] | {"index": [index, None]}

    result = undo(auth_client, signed(data | {"f": fields}))

    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, job).status == "applied"
    assert data["e"][0] in event_ids(session, job)


@pytest.mark.parametrize("index", [2**40, 10_000])
def test_a_large_index_puts_the_card_at_the_bottom(auth_client, session, user, job, index):
    apps.create(session, user, company="Top", job_title="PM")
    data = status_token(auth_client, session, job)
    fields = data["f"] | {"index": [index, None]}

    result = undo(auth_client, signed(data | {"f": fields}))

    assert toast_of(result)["message"] == "Undone."
    assert [c.company for c in apps.column_of(session, user, "saved")] == ["Top", "Acme"]


@pytest.mark.parametrize(
    "field, value",
    [
        ("applied_at", 5),
        ("applied_at", "soon"),
        ("last_activity_at", None),
        ("last_activity_at", "yesterday"),
        ("status", None),
        ("status", 3),
    ],
)
def test_broken_before_values_with_correct_after_values_do_nothing(
    auth_client, session, job, field, value
):
    # The "after" values match the job, so the compare passes; the "before" value is
    # what must be refused.
    data = status_token(auth_client, session, job)
    fields = data["f"] | {field: [value, data["f"][field][1]]}

    result = undo(auth_client, signed(data | {"f": fields}))

    assert result.status_code == 204
    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, job).status == "applied"
    assert data["e"][0] in event_ids(session, job)


@pytest.mark.parametrize("value", ["yesterday", 5, True])
def test_a_broken_snooze_before_value_does_nothing(auth_client, session, job, value):
    response = send(auth_client, "POST", f"/applications/{job.id}/snooze")
    data = undo_service._serializer().loads(toast_of(response)["undo_token"])
    after = data["f"]["snoozed_until"][1]

    result = undo(auth_client, signed(data | {"f": {"snoozed_until": [value, after]}}))

    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, job).snoozed_until is not None


# Found by test-writer (M4b): undo_change() checks that a status is a board status,
# but writes back any text for next_action_type and interview_type. A correctly signed
# token can then leave a value the app does not know, and the detail page fails with
# KeyError in next_step_service.LABELS (only possible with the secret key; defence in
# depth like the status check in application_service.undo_change).
# Found by test-writer (M4b): _from_text accepted any text for these kinds.
def test_an_unknown_next_action_type_in_a_signed_token_does_nothing(
    auth_client, session, user, job
):
    response = send(
        auth_client,
        "POST",
        f"/applications/{job.id}/next-step",
        next_action_type="custom",
        date=TOMORROW,
        note="Call",
    )
    data = undo_service._serializer().loads(toast_of(response)["undo_token"])
    # The "after" values stay as the token has them, so the compare passes.
    fields = data["f"] | {
        "next_action_type": ["moon", data["f"]["next_action_type"][1]],
        "next_action_manual": [True, True],
    }

    result = undo(auth_client, signed(data | {"f": fields}))

    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, job).next_action_type == "custom"
    assert auth_client.get(f"/applications/{job.id}").status_code == 200


# Found by test-writer (M4b): _from_text accepted any text for these kinds.
def test_an_unknown_interview_type_in_a_signed_token_does_nothing(auth_client, session, job):
    response = send(
        auth_client,
        "POST",
        f"/applications/{job.id}/interview",
        date=IN_TWO_DAYS,
        interview_type="video",
    )
    data = undo_service._serializer().loads(toast_of(response)["undo_token"])
    fields = data["f"] | {"interview_type": ["moon", data["f"]["interview_type"][1]]}

    result = undo(auth_client, signed(data | {"f": fields}))

    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, job).interview_type == "video"


def test_a_v2_token_for_another_user_does_nothing(auth_client, other_client, session, job):
    response = send(auth_client, "POST", f"/applications/{job.id}/status", status="applied")

    result = undo(other_client, toast_of(response)["undo_token"])

    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, job).status == "applied"


def test_a_too_long_token_is_refused(auth_client, session, job):
    data = status_token(auth_client, session, job)
    # Random text: a repeated letter would be compressed below the limit.
    token = signed(data | {"x": secrets.token_hex(undo_service.TOKEN_MAX_LENGTH)})
    assert len(token) > undo_service.TOKEN_MAX_LENGTH

    result = undo(auth_client, token)

    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, job).status == "applied"


# --- Undo of snooze when something happened in between -----------------------------------


def snooze(client, job):
    return send(client, "POST", f"/applications/{job.id}/snooze")


def test_undo_of_snooze_after_the_job_was_archived_does_nothing(auth_client, session, job):
    snoozed = snooze(auth_client, job)
    send(auth_client, "POST", f"/applications/{job.id}/archive")
    snoozed_until = fresh(session, job).snoozed_until

    result = undo(auth_client, toast_of(snoozed)["undo_token"])

    assert toast_of(result)["message"] == "Nothing to undo."
    job = fresh(session, job)
    assert job.archived is True
    assert job.snoozed_until == snoozed_until


def test_undo_of_snooze_after_archive_and_its_undo_works_again(auth_client, session, job):
    # The archive was taken back, so the snooze is the newest change again.
    snoozed = snooze(auth_client, job)
    archived = send(auth_client, "POST", f"/applications/{job.id}/archive")
    undo(auth_client, toast_of(archived)["undo_token"])

    result = undo(auth_client, toast_of(snoozed)["undo_token"])

    assert toast_of(result)["message"] == "Undone."
    job = fresh(session, job)
    assert (job.archived, job.snoozed_until) == (False, None)


@pytest.mark.parametrize(
    "path, data",
    [
        ("/status", {"status": "applied"}),
        ("/next-step", {"next_action_type": "follow_up", "date": TOMORROW}),
        ("/interview", {"date": IN_TWO_DAYS}),
    ],
)
def test_undo_of_snooze_after_a_newer_change_does_nothing(auth_client, session, job, path, data):
    snoozed = snooze(auth_client, job)
    send(auth_client, "POST", f"/applications/{job.id}{path}", **data)
    after = snapshot(session, job)

    result = undo(auth_client, toast_of(snoozed)["undo_token"])

    assert toast_of(result)["message"] == "Nothing to undo."
    assert snapshot(session, job) == after


def test_undo_of_snooze_after_a_details_change_still_works(auth_client, session, job):
    # Details write no event and don't touch the snooze: the snooze can still be undone.
    snoozed = snooze(auth_client, job)
    send(auth_client, "POST", f"/applications/{job.id}/details", company="New", job_title="PM")

    result = undo(auth_client, toast_of(snoozed)["undo_token"])

    assert toast_of(result)["message"] == "Undone."
    job = fresh(session, job)
    assert (job.company, job.snoozed_until) == ("New", None)


def test_undo_of_a_status_change_after_a_note_does_nothing(auth_client, session, job):
    # A note is an event too: it is newer than the status change.
    changed = send(auth_client, "POST", f"/applications/{job.id}/status", status="applied")
    send(auth_client, "POST", f"/applications/{job.id}/notes", text="Sent it")

    result = undo(auth_client, toast_of(changed)["undo_token"])

    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, job).status == "applied"


# --- Needs attention: snoozes and naive datetimes from SQLite -------------------------------


def waiting_job(session, user, **fields) -> Application:
    """A job saved 9 days ago: "Waiting" in Needs attention."""
    job = apps.create(session, user, company="Waiting Co", job_title="PM")
    job.created_at = utcnow() - dt.timedelta(days=9)
    for name, value in fields.items():
        setattr(job, name, value)
    session.add(job)
    session.commit()
    return job


def attention(session, user, now=None) -> list[str]:
    session.expire_all()
    return [
        item.application.company for item in apps.needs_attention(session, user, now or utcnow())
    ]


def naive(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=None)


@pytest.mark.parametrize(
    "offset, shown",
    [
        (dt.timedelta(hours=-1), True),
        (dt.timedelta(days=-10), True),
        (dt.timedelta(hours=1), False),
        (dt.timedelta(days=2), False),
    ],
)
@pytest.mark.parametrize("stored_naive", [False, True])
def test_a_snooze_hides_until_its_time(session, user, offset, shown, stored_naive):
    until = utcnow() + offset
    waiting_job(session, user, snoozed_until=naive(until) if stored_naive else until)

    assert attention(session, user) == (["Waiting Co"] if shown else [])


def test_the_database_gives_back_naive_snooze_times(session, user):
    # The reason for the tests above: SQLite drops the time zone.
    job = waiting_job(session, user, snoozed_until=utcnow() + dt.timedelta(hours=1))

    assert fresh(session, job).snoozed_until.tzinfo is None
    assert attention(session, user) == []


@pytest.mark.parametrize("now_naive", [False, True])
def test_a_naive_now_works_too(session, user, now_naive):
    now = utcnow()
    waiting_job(session, user, snoozed_until=now + dt.timedelta(hours=1))

    assert attention(session, user, naive(now) if now_naive else now) == []
    later = now + dt.timedelta(hours=2)
    assert attention(session, user, naive(later) if now_naive else later) == ["Waiting Co"]


def test_a_snooze_ending_right_now_shows_the_job_again(session, user):
    now = utcnow()
    waiting_job(session, user, snoozed_until=now)

    assert attention(session, user, now) == ["Waiting Co"]


def test_a_snoozed_job_is_hidden_on_the_page_and_back_when_the_snooze_is_over(
    auth_client, session, user
):
    job = waiting_job(session, user, snoozed_until=utcnow() + dt.timedelta(days=1))
    assert f"/applications/{job.id}/snooze" not in auth_client.get("/").text

    job = fresh(session, job)
    job.snoozed_until = naive(utcnow() - dt.timedelta(minutes=1))
    session.add(job)
    session.commit()

    assert f"/applications/{job.id}/snooze" in auth_client.get("/").text


def test_a_snooze_on_an_interview_and_naive_interview_times(session, user):
    job = apps.create(session, user, company="Interview Co", job_title="PM")
    apps.move(session, user, job, "interview")
    job.interview_at = naive(utcnow() - dt.timedelta(hours=1))  # passed: thank-you due
    session.add(job)
    session.commit()

    assert attention(session, user) == ["Interview Co"]

    apps.snooze(session, user, fresh(session, job), utcnow())
    assert attention(session, user) == []


def test_an_archived_job_with_an_old_snooze_is_still_left_out(session, user):
    job = waiting_job(session, user, snoozed_until=utcnow() - dt.timedelta(days=1))
    apps.archive(session, user, job)

    assert attention(session, user) == []


# --- The detail page in every state ------------------------------------------------------


@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("status", BOARD_STATUSES)
def test_the_detail_page_opens_for_every_status(auth_client, session, user, job, status, archived):
    if status != "saved":
        apps.move(session, user, job, status)
    if archived:
        apps.archive(session, user, job)

    response = auth_client.get(f"/applications/{job.id}")

    assert response.status_code == 200
    assert "Acme" in response.text
    for path in READS[1:]:
        assert auth_client.get(f"/applications/{job.id}{path}").status_code == 200


def set_fields(session, job, **fields) -> None:
    for name, value in fields.items():
        setattr(job, name, value)
    session.add(job)
    session.commit()


NOW = utcnow()
STATES = {
    "interview without a date": ("interview", {}),
    "interview today all day": ("interview", {"interview_at": clock.to_utc(clock.today())}),
    "interview passed": ("interview", {"interview_at": naive(NOW - dt.timedelta(days=2))}),
    "interview long ago all day": (
        "interview",
        {"interview_at": clock.to_utc(clock.today() - dt.timedelta(days=300))},
    ),
    "offer with a deadline": (
        "offer",
        {
            "next_action_manual": True,
            "next_action_type": "reply_offer",
            "next_action_at": naive(NOW - dt.timedelta(days=1)),
        },
    ),
    "done (nothing until the status changes)": (
        "applied",
        {"next_action_manual": True, "next_action_type": None},
    ),
    "custom step without a date": (
        "saved",
        {"next_action_manual": True, "next_action_type": "custom", "next_action_note": "Ask"},
    ),
    "hand-set follow-up": (
        "applied",
        {
            "next_action_manual": True,
            "next_action_type": "follow_up",
            "next_action_at": NOW + dt.timedelta(days=3),
        },
    ),
    "closing date today": ("saved", {"posting_closes_at": clock.today()}),
    "closing date passed": ("saved", {"posting_closes_at": clock.today() - dt.timedelta(days=30)}),
    "applied without applied_at": ("applied", {"applied_at": None}),
    "snoozed": ("saved", {"snoozed_until": NOW + dt.timedelta(days=3)}),
}


@pytest.mark.parametrize("state", list(STATES))
def test_the_detail_page_and_board_open_in_every_state(auth_client, session, user, job, state):
    status, fields = STATES[state]
    if status != "saved":
        apps.move(session, user, job, status)
    set_fields(session, fresh(session, job), **fields)

    for path in READS:
        response = auth_client.get(f"/applications/{job.id}{path}")
        assert response.status_code == 200, path
    assert auth_client.get("/").status_code == 200  # Needs attention reads it too
    assert auth_client.get("/?view=list").status_code == 200


def test_done_twice_changes_nothing_the_second_time(auth_client, session, job):
    first = send(auth_client, "POST", f"/applications/{job.id}/next-step/done")
    events = event_ids(session, job)

    second = send(auth_client, "POST", f"/applications/{job.id}/next-step/done")

    assert "toast" in triggers(first)
    assert second.status_code == 204
    assert "toast" not in second.headers.get("HX-Trigger", "")
    assert event_ids(session, job) == events


def test_status_sent_as_json_is_refused(auth_client, session, job):
    response = auth_client.post(
        f"/applications/{job.id}/status",
        json={"status": "applied"},
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(auth_client)},
    )

    assert response.status_code == 422
    assert fresh(session, job).status == "saved"


@pytest.mark.parametrize("status", ["archived ", " applied", "APPLIED", "applied\x00", "index"])
def test_near_miss_statuses_are_refused(auth_client, session, job, status):
    response = send(auth_client, "POST", f"/applications/{job.id}/status", status=status)

    assert response.status_code == 422
    assert snapshot(session, job) == ("saved", False, "Acme", False, None, None, None, 1)
