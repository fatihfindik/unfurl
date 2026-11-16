"""Undo in the toast (SPEC §6.0/2, D-064): adding a job and changing its status can be
taken back for a short time, once, and only while nothing else changed since."""

import datetime as dt
import json

import pytest

from app.models import Application
from app.models.base import as_utc
from app.services import application_service as apps
from app.services import undo_service
from app.services.auth_service import create_user
from tests.helpers import csrf_token


def send(client, method, url, **data):
    return client.request(
        method,
        url,
        data=data,
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)},
    )


def toast_of(response) -> dict:
    return json.loads(response.headers["HX-Trigger"])["toast"]


def add_job(client, company="Kranich Health", **fields):
    fields = {"company": company, "job_title": "PM", "status": "saved"} | fields
    return send(client, "POST", "/applications", **fields)


def move(client, job_id, status, after_id=""):
    return send(client, "PATCH", f"/applications/{job_id}/move", status=status, after_id=after_id)


def undo(client, response):
    return send(client, "POST", "/applications/undo", token=toast_of(response)["undo_token"])


def column(session, user, status="saved") -> list[str]:
    session.expire_all()
    return [card.company for card in apps.column_of(session, user, status)]


def the_job(session, user, company="Kranich Health") -> Application:
    session.expire_all()
    columns = apps.board(session, user)
    return next(card for cards in columns.values() for card in cards if card.company == company)


# --- The toast offers Undo -----------------------------------------------------------------


def test_adding_and_moving_offer_undo(auth_client, session, user):
    added = add_job(auth_client)
    moved = move(auth_client, the_job(session, user).id, "applied")

    for response in (added, moved):
        toast = toast_of(response)
        assert toast["undo_url"] == "/applications/undo"
        assert toast["undo_token"]


def test_reordering_inside_a_column_has_no_undo(auth_client, session, user):
    add_job(auth_client, "A")
    add_job(auth_client, "B")

    response = move(auth_client, the_job(session, user, "A").id, "saved")

    assert response.headers["HX-Trigger"] == "applications-changed"  # no toast (D-065)


# --- What Undo takes back --------------------------------------------------------------------


def test_undo_of_adding_deletes_the_job_and_its_event(auth_client, session, user):
    add_job(auth_client, "Older")
    response = add_job(auth_client)
    job_id = the_job(session, user).id

    result = undo(auth_client, response)

    assert result.status_code == 204
    assert toast_of(result) == {"message": "Undone.", "undo_url": "", "undo_token": ""}
    assert session.get(Application, job_id) is None
    assert apps.events_of(session, Application(id=job_id)) == []
    assert [c.position for c in apps.column_of(session, user, "saved")] == [0]  # gap closed


def test_undo_of_a_move_puts_everything_back(auth_client, session, user):
    for name in "ABC":
        add_job(auth_client, name)  # Saved reads C, B, A
    job = the_job(session, user, "B")
    # As if B was added a week ago. (The clock is not frozen here: the login
    # session of auth_client would not be valid in the past.)
    week_ago = dt.datetime(2026, 11, 9, 8, 0, tzinfo=dt.UTC)
    job.last_activity_at = week_ago
    session.add(job)
    session.commit()

    response = move(auth_client, job.id, "applied")
    undo(auth_client, response)

    job = the_job(session, user, "B")
    assert (job.status, job.applied_at) == ("saved", None)
    assert as_utc(job.last_activity_at) == week_ago
    assert column(session, user) == ["C", "B", "A"]  # back at its place in the column
    assert column(session, user, "applied") == []
    assert [e.type for e in apps.events_of(session, job)] == ["created"]  # the move is gone


def test_undo_keeps_an_applied_date_that_was_there_before(auth_client, session, user):
    add_job(auth_client, status="applied")
    job = the_job(session, user)
    job.applied_at = dt.date(2026, 11, 2)
    session.add(job)
    session.commit()

    response = move(auth_client, job.id, "interview")
    undo(auth_client, response)

    job = the_job(session, user)
    assert (job.status, job.applied_at) == ("applied", dt.date(2026, 11, 2))


def test_undo_after_the_order_changed_keeps_positions_unique(auth_client, session, user):
    for name in "ABCD":
        add_job(auth_client, name)  # Saved reads D, C, B, A
    response = move(auth_client, the_job(session, user, "D").id, "applied")
    # Reordering writes no event, so the move can still be undone (plan-reviewer #2).
    move(auth_client, the_job(session, user, "A").id, "saved")
    move(
        auth_client,
        the_job(session, user, "C").id,
        "saved",
        after_id=the_job(session, user, "B").id,
    )

    undo(auth_client, response)

    session.expire_all()
    cards = apps.column_of(session, user, "saved")
    assert [c.position for c in cards] == [0, 1, 2, 3]
    assert cards[0].company == "D"  # back at its old place, the top


# --- When Undo is no longer possible ---------------------------------------------------------


def test_a_token_works_only_once(auth_client, session, user):
    add_job(auth_client)
    response = move(auth_client, the_job(session, user).id, "applied")

    undo(auth_client, response)
    second = undo(auth_client, response)

    assert toast_of(second)["message"] == "Nothing to undo."
    assert the_job(session, user).status == "saved"


def test_undo_of_adding_twice_is_not_an_error(auth_client, session, user):
    response = add_job(auth_client)

    undo(auth_client, response)
    second = undo(auth_client, response)

    assert second.status_code == 204
    assert toast_of(second)["message"] == "Nothing to undo."


def test_an_older_action_cannot_overwrite_a_newer_one(auth_client, session, user):
    add_job(auth_client)
    job_id = the_job(session, user).id
    first = move(auth_client, job_id, "applied")
    move(auth_client, job_id, "interview")

    result = undo(auth_client, first)

    assert toast_of(result)["message"] == "Nothing to undo."
    assert the_job(session, user).status == "interview"


def test_undo_of_adding_is_not_possible_after_a_move(auth_client, session, user):
    added = add_job(auth_client)
    move(auth_client, the_job(session, user).id, "applied")

    assert toast_of(undo(auth_client, added))["message"] == "Nothing to undo."
    assert the_job(session, user).status == "applied"


def test_a_token_is_good_for_one_minute(auth_client, session, user, monkeypatch):
    assert undo_service.UNDO_SECONDS == 60
    add_job(auth_client)
    response = move(auth_client, the_job(session, user).id, "applied")
    # Instead of waiting a minute: a token older than -1 seconds is too old.
    monkeypatch.setattr(undo_service, "UNDO_SECONDS", -1)

    result = undo(auth_client, response)

    assert toast_of(result)["message"] == "Nothing to undo."
    assert the_job(session, user).status == "applied"


@pytest.mark.parametrize("token", ["", "abc", "x" * 5000, "eyJ1IjoxfQ.forged.signature"])
def test_a_broken_token_changes_nothing(auth_client, session, user, token):
    add_job(auth_client)
    move(auth_client, the_job(session, user).id, "applied")

    result = send(auth_client, "POST", "/applications/undo", token=token)

    assert result.status_code == 204
    assert toast_of(result)["message"] == "Nothing to undo."
    assert the_job(session, user).status == "applied"


def test_a_changed_token_is_refused(auth_client, session, user):
    add_job(auth_client)
    response = move(auth_client, the_job(session, user).id, "applied")
    token = toast_of(response)["undo_token"]
    tampered = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")

    result = send(auth_client, "POST", "/applications/undo", token=tampered)

    assert toast_of(result)["message"] == "Nothing to undo."


# --- Only the owner ------------------------------------------------------------------------


def test_another_users_token_does_nothing(client, auth_client, session, user):
    add_job(auth_client)
    response = move(auth_client, the_job(session, user).id, "applied")
    token = toast_of(response)["undo_token"]

    other = create_user(session, email="other@example.com", password="y" * 12, full_name="O")
    auth_client.post("/logout", data={"csrf_token": csrf_token(auth_client)})
    client.post(
        "/login",
        data={"email": other.email, "password": "y" * 12, "csrf_token": csrf_token(client)},
    )

    result = send(client, "POST", "/applications/undo", token=token)

    assert toast_of(result)["message"] == "Nothing to undo."
    assert the_job(session, user).status == "applied"


def test_a_token_for_another_users_application_does_nothing(auth_client, session, user):
    # Even a correctly signed token can't reach someone else's application.
    other = create_user(session, email="other@example.com", password="y" * 12, full_name="O")
    theirs = apps.create(session, other, company="Theirs", job_title="PM")
    event = apps.events_of(session, theirs)[-1]
    token = undo_service.make_token(user, theirs.id, [event.id], before=None)

    result = send(auth_client, "POST", "/applications/undo", token=token)

    assert toast_of(result)["message"] == "Nothing to undo."
    session.expire_all()
    assert session.get(Application, theirs.id) is not None


def test_undo_service_twice_in_a_row_is_safe(session, user):
    # Two clicks at the same moment: the second finds the events gone (D-064).
    job = apps.create(session, user, company="A", job_title="PM")
    before = apps.before_note(session, user, job)
    event = apps.move(session, user, job, "applied")

    apps.undo(session, user, job.id, [event.id], before)
    with pytest.raises(apps.NothingToUndo):
        apps.undo(session, user, job.id, [event.id], before)


def test_logged_out_visitors_cannot_undo(client, session, user):
    response = send(client, "POST", "/applications/undo", token="x")

    assert response.headers.get("HX-Redirect", "").startswith("/login")


def test_undo_needs_the_csrf_token(auth_client):
    response = auth_client.post("/applications/undo", data={"token": "x"})

    assert response.status_code == 403
