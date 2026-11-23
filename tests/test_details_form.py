"""The Details form on the detail page (D-075)."""

import datetime as dt
import json

import pytest

from app.models import Application
from app.services import application_service as apps
from app.services import clock
from app.services.auth_service import create_user
from tests.helpers import csrf_token

IN_A_WEEK = (clock.today() + dt.timedelta(days=7)).isoformat()


def add(session, user, company="Kranich Health", **fields) -> Application:
    fields.setdefault("job_title", "PM")
    return apps.create(session, user, company=company, **fields)


def save(client, job, **fields):
    data = {"company": "Kranich Health GmbH", "job_title": "Senior PM"} | fields
    return client.post(
        f"/applications/{job.id}/details",
        data=data,
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)},
    )


def fresh(session, job) -> Application:
    session.expire_all()
    return session.get(Application, job.id)


def test_the_card_has_edit_and_the_form_is_filled(auth_client, session, user):
    job = add(session, user, location="Wien", remote_type="hybrid")

    page = auth_client.get(f"/applications/{job.id}?tab=details").text
    form = auth_client.get(f"/applications/{job.id}/details/edit").text

    assert f'hx-get="/applications/{job.id}/details/edit"' in page
    assert 'value="Kranich Health"' in form and 'value="Wien"' in form
    assert '<option value="hybrid" selected>Hybrid</option>' in form
    for label in [
        "Company",
        "Role",
        "Location",
        "Work mode",
        "Link to the posting",
        "Posting closes",
        "Contact person",
        "Contact email",
        "Salary (from posting)",
        "Notes",
    ]:
        assert f">{label}</label>" in form


def test_saving_updates_everything(auth_client, session, user):
    job = add(session, user)

    response = save(
        auth_client,
        job,
        location="Wien",
        remote_type="remote",
        job_url="https://www.linkedin.com/jobs/view/42",
        posting_closes_at=IN_A_WEEK,
        contact_name="Mira Hofer",
        contact_email="mira@example.com",
        salary_text="ab € 72.000 brutto / Jahr",
        notes="Referral via Lukas.\nCall Friday.",
    )

    assert response.status_code == 200
    events = json.loads(response.headers["HX-Trigger"])
    assert events["toast"]["message"] == "Details saved." and events["application-changed"]
    assert response.text.lstrip().startswith('<section id="application-details"')
    job = fresh(session, job)
    assert (job.company, job.job_title, job.location, job.remote_type) == (
        "Kranich Health GmbH",
        "Senior PM",
        "Wien",
        "remote",
    )
    assert (job.job_url, job.source) == ("https://www.linkedin.com/jobs/view/42", "linkedin")
    assert job.posting_closes_at == dt.date.fromisoformat(IN_A_WEEK)
    assert (job.contact_name, job.contact_email) == ("Mira Hofer", "mira@example.com")
    assert job.notes == "Referral via Lukas.\nCall Friday."


def test_a_closing_date_changes_the_apply_by_step(auth_client, session, user):
    job = add(session, user)

    save(auth_client, job, posting_closes_at=IN_A_WEEK)

    day = (dt.date.fromisoformat(IN_A_WEEK) - dt.timedelta(days=1)).strftime("%a, %-d %b")
    assert f"Apply by {day}" in auth_client.get(f"/applications/{job.id}").text


def test_removing_the_link_makes_the_source_manual_again(auth_client, session, user):
    job = add(session, user, job_url="https://www.xing.com/jobs/1")

    save(auth_client, job, job_url="")

    job = fresh(session, job)
    assert (job.job_url, job.source) == (None, "manual")


def test_empty_fields_are_cleared(auth_client, session, user):
    job = add(session, user, location="Wien")

    save(auth_client, job, location="  ", notes="")

    job = fresh(session, job)
    assert (job.location, job.notes, job.remote_type) == (None, None, "unknown")


def test_fields_the_form_does_not_have_are_ignored(auth_client, session, user):
    job = add(session, user)

    save(
        auth_client,
        job,
        status="offer",
        archived="true",
        user_id="999",
        source="linkedin",
        description_raw="injected",
    )

    job = fresh(session, job)
    assert (job.status, job.archived, job.user_id, job.source) == (
        "saved",
        False,
        user.id,
        "manual",
    )
    assert job.description_raw == ""


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"company": ""}, "This field is required."),
        ({"job_title": "x" * 201}, "Use at most 200 characters."),
        ({"remote_type": "moon"}, "Choose a work mode from the list."),
        ({"job_url": "javascript:alert(1)"}, "Enter a full link starting with https://."),
        ({"job_url": "https://a.example/ b"}, "Enter a full link starting with https://."),
        ({"posting_closes_at": "2001-01-01"}, "Pick a date between"),
        ({"posting_closes_at": "soon"}, "Use the format YYYY-MM-DD."),
        ({"contact_email": "not an email"}, "Enter a valid email address."),
        ({"notes": "x" * 5001}, "Use at most 5000 characters."),
    ],
)
def test_wrong_values_are_explained(auth_client, session, user, fields, message):
    job = add(session, user)

    response = save(auth_client, job, **fields)

    assert message in response.text
    assert "HX-Trigger" not in response.headers
    assert fresh(session, job).company == "Kranich Health"


def test_user_text_is_escaped(auth_client, session, user):
    job = add(session, user)

    response = save(auth_client, job, contact_name="<script>x</script>", notes="<b>hi</b>")

    assert "<script>x</script>" not in response.text and "<b>hi</b>" not in response.text


def test_another_users_application_is_not_found(auth_client, session):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = add(session, other, "Theirs")

    assert auth_client.get(f"/applications/{theirs.id}/details/edit").status_code == 404
    assert auth_client.get(f"/applications/{theirs.id}/details").status_code == 404
    assert save(auth_client, theirs).status_code == 404
    assert fresh(session, theirs).company == "Theirs"


def test_logged_out_and_without_csrf_nothing_is_saved(client, auth_client, session, user):
    job = add(session, user)
    assert (
        auth_client.post(
            f"/applications/{job.id}/details", data={"company": "X", "job_title": "Y"}
        ).status_code
        == 403
    )
    auth_client.post("/logout", data={"csrf_token": csrf_token(auth_client)})
    assert save(client, job).headers["HX-Redirect"].startswith("/login")
    assert fresh(session, job).company == "Kranich Health"
