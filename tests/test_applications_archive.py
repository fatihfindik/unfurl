"""Archive and restore (SPEC §5.5, §6.7, D-063, D-066): archived jobs leave the board and
the counts, "Show archived" shows them faded, Restore brings them back; all with Undo."""

import json
import re

import pytest

from app.models import Application
from app.services import application_service as apps
from app.services.auth_service import create_user
from tests.helpers import csrf_token


def add(session, user, company, **fields):
    fields.setdefault("job_title", "Product Manager")
    return apps.create(session, user, company=company, **fields)


def post(client, url, **data):
    return client.post(
        url, data=data, headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)}
    )


def toast_of(response) -> dict:
    return json.loads(response.headers["HX-Trigger"])["toast"]


def column(session, user, status="saved") -> list[str]:
    session.expire_all()
    return [card.company for card in apps.column_of(session, user, status)]


def fresh(session, job) -> Application:
    session.expire_all()
    return session.get(Application, job.id)


@pytest.fixture
def jobs(session, user) -> dict[str, Application]:
    """Saved reads C, B, A from the top."""
    return {name: add(session, user, name) for name in "ABC"}


# --- The service -------------------------------------------------------------------------


def test_archiving_takes_a_job_off_the_board(session, user, jobs):
    event = apps.archive(session, user, jobs["B"])

    assert event.type == "archived"
    job = fresh(session, jobs["B"])
    assert (job.archived, job.status) == (True, "saved")  # it keeps its status
    assert column(session, user) == ["C", "A"]
    assert [c.position for c in apps.column_of(session, user, "saved")] == [0, 1]
    assert apps.counts(session, user)["saved"] == 2
    assert apps.archived_count(session, user) == 1


def test_restoring_puts_it_at_the_top_of_its_column(session, user, jobs):
    apps.archive(session, user, jobs["A"])

    event = apps.restore(session, user, fresh(session, jobs["A"]))

    assert event.type == "restored"
    assert column(session, user) == ["A", "C", "B"]
    assert fresh(session, jobs["A"]).archived is False


def test_archiving_twice_or_restoring_a_job_on_the_board_is_refused(session, user, jobs):
    with pytest.raises(apps.NotOnBoard):
        apps.restore(session, user, jobs["A"])
    apps.archive(session, user, jobs["A"])
    with pytest.raises(apps.NotOnBoard):
        apps.archive(session, user, fresh(session, jobs["A"]))


def test_the_events_are_kept(session, user, jobs):
    apps.archive(session, user, jobs["A"])
    apps.restore(session, user, fresh(session, jobs["A"]))

    types = [e.type for e in apps.events_of(session, jobs["A"])]
    assert types == ["created", "archived", "restored"]


def test_an_archived_job_is_not_deleted(session, user, jobs):
    apps.archive(session, user, jobs["A"])

    assert session.get(Application, jobs["A"].id) is not None


# --- The routes -----------------------------------------------------------------------------


def test_archive_and_restore_show_a_toast_with_undo(auth_client, session, user, jobs):
    archived = post(auth_client, f"/applications/{jobs['B'].id}/archive")
    restored = post(auth_client, f"/applications/{jobs['B'].id}/restore")

    assert archived.status_code == restored.status_code == 204
    assert toast_of(archived)["message"] == "B archived."
    assert toast_of(restored)["message"] == "B restored."
    assert toast_of(archived)["undo_token"] and toast_of(restored)["undo_token"]


def test_undo_of_archive_puts_the_job_back_where_it_was(auth_client, session, user, jobs):
    response = post(auth_client, f"/applications/{jobs['B'].id}/archive")

    undone = post(auth_client, "/applications/undo", token=toast_of(response)["undo_token"])

    assert toast_of(undone)["message"] == "Undone."
    assert column(session, user) == ["C", "B", "A"]  # back in the middle
    assert fresh(session, jobs["B"]).archived is False
    assert [e.type for e in apps.events_of(session, jobs["B"])] == ["created"]


def test_undo_of_restore_archives_it_again(auth_client, session, user, jobs):
    post(auth_client, f"/applications/{jobs['B'].id}/archive")
    response = post(auth_client, f"/applications/{jobs['B'].id}/restore")

    post(auth_client, "/applications/undo", token=toast_of(response)["undo_token"])

    assert fresh(session, jobs["B"]).archived is True
    assert column(session, user) == ["C", "A"]
    assert [c.position for c in apps.column_of(session, user, "saved")] == [0, 1]


def test_archiving_an_archived_job_changes_nothing(auth_client, session, user, jobs):
    post(auth_client, f"/applications/{jobs['A'].id}/archive")

    again = post(auth_client, f"/applications/{jobs['A'].id}/archive")

    assert again.status_code == 204
    assert again.headers["HX-Trigger"] == "applications-changed"  # no toast, no Undo
    assert [e.type for e in apps.events_of(session, jobs["A"])] == ["created", "archived"]


def test_an_archived_card_cannot_be_dragged(auth_client, session, user, jobs):
    post(auth_client, f"/applications/{jobs['A'].id}/archive")

    response = auth_client.patch(
        f"/applications/{jobs['A'].id}/move",
        data={"status": "applied"},
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(auth_client)},
    )

    assert response.status_code == 422
    assert fresh(session, jobs["A"]).status == "saved"


@pytest.mark.parametrize("action", ["archive", "restore"])
def test_another_users_job_is_not_found(auth_client, session, action):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = add(session, other, "Theirs")
    if action == "restore":
        apps.archive(session, other, theirs)

    response = post(auth_client, f"/applications/{theirs.id}/{action}")

    assert response.status_code == 404
    assert fresh(session, theirs).archived is (action == "restore")


@pytest.mark.parametrize("action", ["archive", "restore"])
@pytest.mark.parametrize("bad_id", ["abc", "0", "-1", str(2**70)])
def test_bad_ids_are_not_found(auth_client, action, bad_id):
    assert post(auth_client, f"/applications/{bad_id}/{action}").status_code == 404


@pytest.mark.parametrize("action", ["archive", "restore"])
def test_logged_out_and_without_csrf_nothing_happens(
    client, auth_client, session, user, jobs, action
):
    job = jobs["A"]
    assert auth_client.post(f"/applications/{job.id}/{action}").status_code == 403
    auth_client.post("/logout", data={"csrf_token": csrf_token(auth_client)})
    response = post(client, f"/applications/{job.id}/{action}")
    assert response.headers.get("HX-Redirect", "").startswith("/login")
    assert fresh(session, job).archived is False


# --- On the page -----------------------------------------------------------------------------


def test_archived_jobs_are_hidden_and_counted_separately(auth_client, session, user, jobs):
    apps.archive(session, user, jobs["A"])

    board = auth_client.get("/").text
    listed = auth_client.get("/?view=list").text

    assert "Show archived (1)" in board
    for html in (board, listed):
        assert "data-archived" not in html
    assert '<tr data-row="' + str(jobs["A"].id) not in listed
    assert re.search(r'data-summary="saved".*?<strong[^>]*>2</strong>', listed, re.S)


def test_show_archived_shows_them_faded_with_restore(auth_client, session, user, jobs):
    apps.archive(session, user, jobs["A"])

    board = auth_client.get("/?archived=1").text
    listed = auth_client.get("/?view=list&archived=1").text

    card = re.search(
        rf'<article data-card="{jobs["A"].id}" data-archived.*?</article>', board, re.S
    )
    assert card and "opacity-60" in card.group(0) and "ARCHIVED" in card.group(0)
    assert f'hx-post="/applications/{jobs["A"].id}/restore"' in card.group(0)
    saved = re.search(r'data-column="saved".*?</section>', board, re.S).group(0)
    assert saved.rindex(f'data-card="{jobs["A"].id}"') > saved.index(f'data-card="{jobs["B"].id}"')
    assert f'<tr data-row="{jobs["A"].id}" data-archived' in listed
    assert 'name="archived" value="1" checked' in board
    assert ">Clear filters</a>" in board  # "Show archived" counts as a filter


def test_the_list_has_an_archive_button_for_jobs_on_the_board(auth_client, session, user, jobs):
    listed = auth_client.get("/?view=list").text

    for job in jobs.values():
        assert f'hx-post="/applications/{job.id}/archive"' in listed
    assert "/archive" not in auth_client.get("/").text  # not on the cards (SPEC §6.7)


def test_without_archived_jobs_there_is_no_checkbox(auth_client, jobs):
    assert "Show archived" not in auth_client.get("/").text


def test_the_checkbox_count_is_refreshed_with_the_region(auth_client, session, user, jobs):
    apps.archive(session, user, jobs["A"])
    apps.archive(session, user, jobs["B"])

    html = auth_client.get("/", headers={"HX-Request": "true"}).text

    assert 'id="filter-extras"' in html and 'hx-swap-oob="true"' in html
    assert "Show archived (2)" in html


def test_another_users_archived_jobs_are_not_counted_or_shown(auth_client, session, user, jobs):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    apps.archive(session, other, add(session, other, "Their Archive"))
    apps.archive(session, user, jobs["A"])

    html = auth_client.get("/?archived=1&view=list").text

    assert "Show archived (1)" in html
    assert "Their Archive" not in html


# --- Rate limits on the other write routes (security review M4a, A) -------------------------


def test_moving_archiving_and_the_theme_are_rate_limited(auth_client, session, user, jobs):
    job = jobs["A"]
    headers = {"HX-Request": "true", "X-CSRF-Token": csrf_token(auth_client)}

    moves = [
        auth_client.patch(f"/applications/{job.id}/move", data={"status": "saved"}, headers=headers)
        for _ in range(121)
    ]
    themes = [
        auth_client.post("/settings/theme", data={"theme": "dark"}, headers=headers)
        for _ in range(31)
    ]
    archives = [
        auth_client.post(f"/applications/{job.id}/archive", headers=headers) for _ in range(61)
    ]

    assert moves[-1].status_code == 429 and moves[-2].status_code != 429
    assert themes[-1].status_code == 429 and themes[-2].status_code == 204
    assert archives[-1].status_code == 429 and archives[-2].status_code == 204
