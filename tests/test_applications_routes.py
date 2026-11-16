"""Adding a job by hand (SPEC §6.7 "Add job", D-062, D-065)."""

import json

import pytest

from app.models import Application
from app.routers import applications as routes
from app.services import application_service as apps
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
