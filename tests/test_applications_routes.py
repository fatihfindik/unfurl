"""Adding a job by hand (SPEC §6.7 "Add job", D-062, D-065)."""

import json

import pytest

from app.models import Application
from app.routers import applications as routes
from app.services import application_service as apps
from app.services.auth_service import create_user
from tests.helpers import csrf_token


def post_job(client, **fields):
    data = {"company": "Kranich Health", "job_title": "Product Manager", "status": "saved"}
    return client.post(
        "/applications",
        data=data | fields,
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)},
    )


def jobs(session, user) -> list[Application]:
    session.expire_all()
    return [
        card for status in ("saved", "applied") for card in apps.column_of(session, user, status)
    ]


def events_of(response) -> dict:
    return json.loads(response.headers["HX-Trigger"])


# --- The page ---------------------------------------------------------------------------


def test_the_page_has_the_add_job_button_and_form(auth_client):
    html = auth_client.get("/").text

    assert 'data-dialog-open="add-job"' in html
    assert '<dialog id="add-job"' in html
    assert 'hx-post="/applications"' in html
    for label in ["Job link or posting text", "Company", "Role", "Location", "Work mode", "Status"]:
        assert f">{label}</label>" in html
    assert "Auto-fill" not in html and "credit" not in html  # AI comes with M5


def test_an_htmx_request_for_the_page_gets_only_the_jobs_part(auth_client, session, user):
    apps.create(session, user, company="Kranich Health", job_title="PM")

    html = auth_client.get("/", headers={"HX-Request": "true"}).text

    assert html.lstrip().startswith('<div id="applications-region"')
    assert "Kranich Health" in html
    assert "<html" not in html and "<dialog" not in html


# --- Saving -----------------------------------------------------------------------------


def test_a_job_is_saved_with_all_its_fields(auth_client, session, user):
    response = post_job(
        auth_client,
        location=" Vienna ",
        remote_type="hybrid",
        status="applied",
        posting="https://www.stepstone.at/job/123",
    )

    assert response.status_code == 200
    (job,) = jobs(session, user)
    assert (job.company, job.job_title) == ("Kranich Health", "Product Manager")
    assert (job.location, job.remote_type, job.status) == ("Vienna", "hybrid", "applied")
    assert (job.job_url, job.source) == ("https://www.stepstone.at/job/123", "stepstone")
    assert job.description_raw == "" and job.applied_at is not None


def test_text_that_is_not_one_link_is_the_posting_text(auth_client, session, user):
    text = "We are hiring!\nSee https://example.com/jobs for more."

    post_job(auth_client, posting=text)

    (job,) = jobs(session, user)
    assert (job.job_url, job.source) == (None, "manual")
    assert job.description_raw == text


def test_only_company_and_role_are_needed(auth_client, session, user):
    post_job(auth_client)

    (job,) = jobs(session, user)
    assert (job.location, job.remote_type, job.job_url, job.source) == (
        None,
        "unknown",
        None,
        "manual",
    )


def test_after_saving_the_page_gets_a_toast_a_reload_and_a_closed_modal(auth_client):
    response = post_job(auth_client, status="applied")

    events = events_of(response)
    assert events["toast"]["message"] == "Kranich Health saved to Applied."
    assert events["applications-changed"] is True
    assert events["close-dialog"] == "add-job"
    # The form comes back empty, ready for the next job.
    assert 'id="add-job-form"' in response.text
    assert "Kranich Health" not in response.text


def test_a_company_name_with_turkish_letters_is_safe_in_the_header(auth_client):
    response = post_job(auth_client, company="Şişecam Çevre ğüı İ")

    assert response.headers["HX-Trigger"].isascii()  # HTTP headers can't carry "Ş"
    assert events_of(response)["toast"]["message"] == "Şişecam Çevre ğüı İ saved to Saved."


def test_values_the_form_does_not_have_are_ignored(auth_client, session, user):
    post_job(
        auth_client,
        user_id="999",
        source="linkedin",
        job_url="https://evil.example/",
        description_raw="injected",
        archived="true",
        position="7",
        applied_at="2001-01-01",
    )

    (job,) = jobs(session, user)
    assert job.user_id == user.id
    assert (job.source, job.job_url, job.description_raw) == ("manual", None, "")
    assert (job.archived, job.position, job.applied_at) == (False, 0, None)


# --- Errors come back in the form, nothing is saved ---------------------------------------


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"company": ""}, "This field is required."),
        ({"company": "   "}, "This field is required."),
        ({"job_title": ""}, "This field is required."),
        ({"company": "x" * 201}, "Use at most 200 characters."),
        ({"job_title": "x" * 201}, "Use at most 200 characters."),
        ({"location": "x" * 201}, "Use at most 200 characters."),
        ({"remote_type": "moon"}, "Choose a work mode from the list."),
        ({"status": "offer"}, "Choose Saved or Applied."),
        ({"status": "archived"}, "Choose Saved or Applied."),
        ({"posting": "x" * 50_001}, "Use at most 50000 characters."),
        (
            {"posting": "https://example.com/" + "x" * 2000},
            "Use a link of at most 2000 characters.",
        ),
    ],
)
def test_a_wrong_value_is_explained_and_not_saved(auth_client, session, user, fields, message):
    response = post_job(auth_client, **fields)

    assert response.status_code == 200
    assert message in response.text
    assert "HX-Trigger" not in response.headers
    assert jobs(session, user) == []


def test_the_form_keeps_what_was_typed(auth_client):
    response = post_job(auth_client, company="", job_title="Senior PM", location="Graz")

    assert 'value="Senior PM"' in response.text and 'value="Graz"' in response.text


@pytest.mark.parametrize("link", ["javascript:alert(1)", "data:text/html,x", "file:///etc/passwd"])
def test_only_http_links_become_the_job_link(auth_client, session, user, link):
    post_job(auth_client, posting=link)

    (job,) = jobs(session, user)
    assert job.job_url is None  # kept as plain posting text, never used as a link
    assert job.description_raw == link


def test_the_limit_per_user_is_explained(auth_client, session, user, monkeypatch):
    monkeypatch.setattr(apps, "MAX_APPLICATIONS_PER_USER", 1)
    post_job(auth_client)

    response = post_job(auth_client, company="One too many")

    assert routes.TOO_MANY_APPLICATIONS in response.text
    assert len(jobs(session, user)) == 1


# --- Who may add --------------------------------------------------------------------------


def test_logged_out_visitors_cannot_add_a_job(client, session, user):
    response = post_job(client)

    assert response.headers.get("HX-Redirect", "").startswith("/login")
    assert jobs(session, user) == []


def test_without_the_csrf_token_nothing_is_saved(auth_client, session, user):
    response = auth_client.post(
        "/applications", data={"company": "A", "job_title": "B", "status": "saved"}
    )

    assert response.status_code == 403
    assert jobs(session, user) == []


def test_adding_jobs_is_rate_limited(auth_client):
    codes = [post_job(auth_client, company=f"Company {n}").status_code for n in range(31)]

    assert codes[:30] == [200] * 30
    assert codes[30] == 429


# --- Moving a card: PATCH /applications/{id}/move (D-063, D-065) ----------------------------


def move(client, job_id, status, after_id=""):
    return client.patch(
        f"/applications/{job_id}/move",
        data={"status": status, "after_id": after_id},
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)},
    )


@pytest.fixture
def job(session, user) -> Application:
    return apps.create(session, user, company="Şişecam", job_title="PM")


def test_moving_to_another_column_saves_it_and_shows_a_toast(auth_client, session, job):
    response = move(auth_client, job.id, "interview")

    assert response.status_code == 204 and response.text == ""
    events = events_of(response)
    assert events["toast"]["message"] == "Şişecam moved to Interview."
    assert events["applications-changed"] is True
    session.refresh(job)
    assert job.status == "interview"


def test_the_card_above_is_respected(auth_client, session, user, job):
    first = apps.create(session, user, company="First", job_title="PM", status="applied")
    apps.create(session, user, company="Second", job_title="PM", status="applied")  # on top

    move(auth_client, job.id, "applied", after_id=str(first.id))

    assert [card.company for card in apps.column_of(session, user, "applied")] == [
        "Second",
        "First",
        "Şişecam",
    ]


def test_a_new_order_in_the_same_column_reloads_the_board_without_a_toast(
    auth_client, session, user, job
):
    other_job = apps.create(session, user, company="Other", job_title="PM")  # above `job`

    response = move(auth_client, job.id, "saved", after_id="")

    assert response.status_code == 204
    assert response.headers["HX-Trigger"] == "applications-changed"  # no toast (D-065)
    assert [card.id for card in apps.column_of(session, user, "saved")] == [job.id, other_job.id]


@pytest.mark.parametrize("status", ["", "withdrawn", "archived", "moon", "Saved"])
def test_a_status_that_is_not_a_column_is_refused(auth_client, session, job, status):
    assert move(auth_client, job.id, status).status_code == 422
    session.refresh(job)
    assert job.status == "saved"


@pytest.mark.parametrize("bad_id", ["abc", "0", "-1", "1.5", str(2**31), str(2**70)])
def test_an_id_that_is_not_one_is_not_found(auth_client, job, bad_id):
    assert move(auth_client, bad_id, "applied").status_code == 404


def test_another_users_card_cannot_be_moved_or_used_as_the_card_above(
    auth_client, session, user, job
):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = apps.create(session, other, company="Theirs", job_title="PM")

    assert move(auth_client, theirs.id, "applied").status_code == 404
    assert move(auth_client, job.id, "saved", after_id=str(theirs.id)).status_code == 404
    session.refresh(theirs)
    session.refresh(job)
    assert (theirs.status, theirs.position, job.status) == ("saved", 0, "saved")


def test_logged_out_visitors_cannot_move_a_card(client, session, job):
    response = move(client, job.id, "applied")

    assert response.headers.get("HX-Redirect", "").startswith("/login")
    session.refresh(job)
    assert job.status == "saved"


def test_moving_needs_the_csrf_token(auth_client, session, job):
    response = auth_client.patch(f"/applications/{job.id}/move", data={"status": "applied"})

    assert response.status_code == 403
    session.refresh(job)
    assert job.status == "saved"


@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_only_patch_moves_a_card(auth_client, job, method):
    response = getattr(auth_client, method)(
        f"/applications/{job.id}/move", headers={"X-CSRF-Token": csrf_token(auth_client)}
    )

    assert response.status_code == 405
