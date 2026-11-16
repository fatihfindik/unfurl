"""The status picker on the detail page (D-076) and Undo token version 2 (D-079)."""

import datetime as dt
import json

import pytest

from app.models import Application
from app.services import application_service as apps
from app.services import clock, undo_service
from app.services.auth_service import create_user
from tests.helpers import csrf_token


def add(session, user, company="Kranich Health", **fields) -> Application:
    fields.setdefault("job_title", "PM")
    return apps.create(session, user, company=company, **fields)


def send(client, url, **data):
    return client.post(
        url, data=data, headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)}
    )


def set_status(client, job, status):
    return send(client, f"/applications/{job.id}/status", status=status)


def undo(client, response):
    token = json.loads(response.headers["HX-Trigger"])["toast"]["undo_token"]
    return send(client, "/applications/undo", token=token)


def toast_of(response) -> dict:
    return json.loads(response.headers["HX-Trigger"])["toast"]


def fresh(session, job) -> Application:
    session.expire_all()
    return session.get(Application, job.id)


# --- The picker on the page ---------------------------------------------------------------


def test_the_detail_page_has_a_keyboard_friendly_status_picker(auth_client, session, user):
    job = add(session, user)

    html = auth_client.get(f"/applications/{job.id}").text

    assert f'<select name="status" hx-post="/applications/{job.id}/status"' in html
    for value, label in [
        ("saved", "Saved"),
        ("applied", "Applied"),
        ("interview", "Interview"),
        ("offer", "Offer"),
        ("rejected", "Rejected"),
        ("archived", "Archived"),
    ]:
        assert f'<option value="{value}"' in html and f">{label}</option>" in html
    assert '<option value="saved" selected>Saved</option>' in html
    # The page reloads its parts after a change (D-076).
    assert 'hx-trigger="application-changed from:body"' in html


# --- Changing the status ------------------------------------------------------------------------


def test_picking_a_status_moves_the_job_with_a_toast_and_undo(auth_client, session, user):
    job = add(session, user)

    response = set_status(auth_client, job, "applied")

    assert response.status_code == 204
    events = json.loads(response.headers["HX-Trigger"])
    assert events["toast"]["message"] == "Kranich Health moved to Applied."
    assert events["toast"]["undo_token"]
    assert events["application-changed"] is True and events["applications-changed"] is True
    job = fresh(session, job)
    assert (job.status, job.applied_at) == ("applied", clock.today())
    assert apps.column_of(session, user, "applied")[0].id == job.id  # top of the column


def test_picking_archived_archives_and_picking_a_status_restores(auth_client, session, user):
    job = add(session, user)

    assert (
        toast_of(set_status(auth_client, job, "archived"))["message"] == "Kranich Health archived."
    )
    assert fresh(session, job).archived

    set_status(auth_client, job, "interview")
    job = fresh(session, job)
    assert (job.archived, job.status) == (False, "interview")
    types = [e.type for e in apps.events_of(session, job)]
    assert types == ["created", "archived", "restored", "status_changed"]


def test_picking_the_same_status_changes_nothing(auth_client, session, user):
    job = add(session, user)

    response = set_status(auth_client, job, "saved")

    assert response.headers["HX-Trigger"] == "application-changed"
    assert [e.type for e in apps.events_of(session, job)] == ["created"]


@pytest.mark.parametrize("status", ["", "withdrawn", "moon", "Saved", "archived; x"])
def test_unknown_statuses_are_refused(auth_client, session, user, status):
    job = add(session, user)

    assert set_status(auth_client, job, status).status_code == 422
    assert fresh(session, job).status == "saved"


# --- A new status clears a hand-set next step and a snooze (D-073) --------------------------------


def test_a_new_status_clears_the_next_step_set_by_hand(auth_client, session, user):
    job = add(session, user)
    job.next_action_manual = True
    job.next_action_type = "custom"
    job.next_action_note = "Call Mira"
    job.next_action_at = clock.to_utc(clock.today())
    job.snoozed_until = clock.now() + dt.timedelta(days=3)
    session.add(job)
    session.commit()

    response = set_status(auth_client, job, "applied")

    job = fresh(session, job)
    assert (job.next_action_manual, job.next_action_type, job.next_action_note) == (
        False,
        None,
        None,
    )
    assert job.snoozed_until is None

    # Undo brings all of it back.
    undo(auth_client, response)
    job = fresh(session, job)
    assert (job.status, job.next_action_type, job.next_action_note) == (
        "saved",
        "custom",
        "Call Mira",
    )
    assert job.next_action_manual and job.snoozed_until is not None


def test_archiving_keeps_a_next_step_set_by_hand(session, user):
    job = add(session, user)
    job.next_action_manual = True
    job.next_action_type = "custom"
    session.add(job)
    session.commit()

    apps.archive(session, user, job)
    apps.restore(session, user, fresh(session, job))

    assert fresh(session, job).next_action_type == "custom"


# --- Undo of the picker (token version 2) ------------------------------------------------------


def test_undo_puts_the_status_and_the_place_back(auth_client, session, user):
    for name in "ABC":
        add(session, user, name)  # Saved reads C, B, A
    middle = apps.column_of(session, user, "saved")[1]

    response = set_status(auth_client, middle, "offer")
    result = undo(auth_client, response)

    assert toast_of(result)["message"] == "Undone."
    assert [c.company for c in apps.column_of(session, user, "saved")] == ["C", "B", "A"]
    assert [e.type for e in apps.events_of(session, middle)] == ["created"]


def test_undo_of_archived_then_restored_via_the_picker(auth_client, session, user):
    job = add(session, user)
    set_status(auth_client, job, "archived")

    response = set_status(auth_client, job, "applied")  # restore + move: two events
    undo(auth_client, response)

    job = fresh(session, job)
    assert (job.archived, job.status) == (True, "saved")
    assert [e.type for e in apps.events_of(session, job)] == ["created", "archived"]


def test_a_token_works_once(auth_client, session, user):
    job = add(session, user)
    response = set_status(auth_client, job, "applied")

    undo(auth_client, response)
    second = undo(auth_client, response)

    assert toast_of(second)["message"] == "Nothing to undo."


def test_a_newer_change_is_not_overwritten(auth_client, session, user):
    job = add(session, user)
    first = set_status(auth_client, job, "applied")
    set_status(auth_client, job, "interview")

    assert toast_of(undo(auth_client, first))["message"] == "Nothing to undo."
    assert fresh(session, job).status == "interview"


def test_a_field_changed_without_an_event_blocks_the_undo(auth_client, session, user):
    # Something changed a field of this action later without writing an event (as a
    # snooze or the Details form do): Undo must not overwrite it.
    job = add(session, user)
    response = set_status(auth_client, job, "applied")  # applied_at: None -> today
    job = fresh(session, job)
    job.applied_at = dt.date(2026, 1, 2)
    session.add(job)
    session.commit()

    assert toast_of(undo(auth_client, response))["message"] == "Nothing to undo."
    job = fresh(session, job)
    assert (job.status, job.applied_at) == ("applied", dt.date(2026, 1, 2))


def test_the_token_carries_only_what_changed(auth_client, session, user):
    job = add(session, user)

    response = set_status(auth_client, job, "applied")

    note = undo_service.read_token(user, toast_of(response)["undo_token"])
    assert note["version"] == 2
    assert set(note["fields"]) == {"status", "applied_at", "last_activity_at", "index"}


def test_long_turkish_text_still_fits_in_the_token(session, user):
    text = "ğüşıöçĞÜŞİÖÇ" * 16  # 192 characters, many bytes each
    fields = {
        "next_action_note": [text, None],
        "interview_with": [text, None],
        "next_action_type": ["custom", None],
        "next_action_at": [clock.now().isoformat(), None],
        "interview_at": [clock.now().isoformat(), None],
        "last_activity_at": [clock.now().isoformat(), clock.now().isoformat()],
        "snoozed_until": [clock.now().isoformat(), None],
    }

    token = undo_service.make_change_token(user, 123456, [987654, 987655], 987655, fields)

    assert len(token) <= undo_service.TOKEN_MAX_LENGTH
    assert undo_service.read_token(user, token)["fields"] == fields


@pytest.mark.parametrize(
    "fields",
    [
        {"moon": ["a", "b"]},
        {"status": "applied"},
        {"status": ["saved"]},
        {"status": [None, "applied"]},
        {"status": ["withdrawn", "applied"]},
        {"archived": ["no", True]},
        {"index": ["x", None], "status": ["saved", "applied"]},
        {"interview_with": ["x" * 500, None]},
        {},
    ],
)
def test_odd_but_signed_v2_tokens_change_nothing(auth_client, session, user, fields):
    job = add(session, user)
    set_status(auth_client, job, "applied")
    newest = apps.newest_event_id(session, fresh(session, job))
    token = undo_service.make_change_token(user, job.id, [], newest, fields)

    result = send(auth_client, "/applications/undo", token=token)

    assert result.status_code == 204
    assert toast_of(result)["message"] == "Nothing to undo."
    assert fresh(session, job).status == "applied"


# --- Who may change it ---------------------------------------------------------------------------


def test_another_users_application_is_not_found(auth_client, session):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = add(session, other, "Theirs")

    assert set_status(auth_client, theirs, "applied").status_code == 404
    assert fresh(session, theirs).status == "saved"


@pytest.mark.parametrize("bad_id", ["abc", "0", "-1", str(2**70)])
def test_bad_ids_are_not_found(auth_client, bad_id):
    response = send(auth_client, f"/applications/{bad_id}/status", status="applied")

    assert response.status_code == 404


def test_logged_out_and_without_csrf_nothing_changes(client, auth_client, session, user):
    job = add(session, user)
    assert (
        auth_client.post(f"/applications/{job.id}/status", data={"status": "applied"}).status_code
        == 403
    )
    auth_client.post("/logout", data={"csrf_token": csrf_token(auth_client)})
    response = set_status(client, job, "applied")
    assert response.headers.get("HX-Redirect", "").startswith("/login")
    assert fresh(session, job).status == "saved"
