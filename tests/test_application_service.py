"""Rules for applications (SPEC §5.5, §5.6; D-061, D-062): adding a job, who owns it,
where it was found."""

import datetime as dt

import pytest
from fastapi import HTTPException
from freezegun import freeze_time

from app.models import Application
from app.services import application_service as apps
from app.services.auth_service import create_user


@pytest.fixture
def other(session):
    return create_user(session, email="other@example.com", password="x" * 12, full_name="Other")


def add(session, user, company="Kranich Health", **fields) -> Application:
    fields.setdefault("job_title", "Product Manager")
    return apps.create(session, user, company=company, **fields)


def column(session, user, status="saved") -> list[str]:
    return [card.company for card in apps.column_of(session, user, status)]


# --- Adding a job ---------------------------------------------------------------------


def test_a_new_job_is_saved_with_a_created_event(session, user):
    job = add(session, user, location="Vienna", remote_type="hybrid")

    assert (job.status, job.archived, job.applied_at) == ("saved", False, None)
    assert (job.location, job.remote_type, job.source) == ("Vienna", "hybrid", "manual")
    assert job.description_raw == ""
    events = apps.events_of(session, job)
    assert [(e.type, e.from_status, e.to_status) for e in events] == [("created", None, "saved")]


def test_a_new_job_goes_to_the_top_of_its_column(session, user):
    add(session, user, "First")
    add(session, user, "Second")
    add(session, user, "Elsewhere", status="applied")
    add(session, user, "Third")

    assert column(session, user) == ["Third", "Second", "First"]
    assert [card.position for card in apps.column_of(session, user, "saved")] == [0, 1, 2]
    assert column(session, user, "applied") == ["Elsewhere"]


@freeze_time("2026-11-16 23:30:00")
def test_a_job_added_as_applied_gets_todays_date(session, user):
    assert add(session, user, status="applied").applied_at == dt.date(2026, 11, 16)
    assert add(session, user, status="saved").applied_at is None


def test_the_source_comes_from_the_link(session, user):
    job = add(session, user, job_url="https://www.linkedin.com/jobs/view/123")

    assert job.source == "linkedin"


def test_there_is_a_limit_per_user(session, user, other, monkeypatch):
    monkeypatch.setattr(apps, "MAX_APPLICATIONS_PER_USER", 2)
    add(session, user, "One")
    add(session, user, "Two")

    with pytest.raises(apps.TooManyApplications):
        add(session, user, "Three")
    add(session, other, "Theirs")  # someone else's count is their own


def test_other_users_columns_are_not_touched(session, user, other):
    theirs = add(session, other, "Theirs")
    add(session, user, "Mine")
    add(session, user, "Mine too")

    session.refresh(theirs)
    assert theirs.position == 0
    assert column(session, other) == ["Theirs"]
    assert column(session, user) == ["Mine too", "Mine"]


# --- Who owns it ----------------------------------------------------------------------


def test_the_owner_gets_the_application(session, user):
    job = add(session, user)

    assert apps.get_owned_application(session, user, str(job.id)).id == job.id


@pytest.mark.parametrize("bad_id", ["abc", "", "0", "-1", "1.5", str(2**31), str(2**70), None])
def test_an_id_that_is_not_one_is_a_404(session, user, bad_id):
    add(session, user)

    with pytest.raises(HTTPException) as error:
        apps.get_owned_application(session, user, bad_id)
    assert error.value.status_code == 404


def test_another_users_application_is_a_404(session, user, other):
    theirs = add(session, other)

    with pytest.raises(HTTPException) as error:
        apps.get_owned_application(session, user, theirs.id)
    assert error.value.status_code == 404


def test_deleting_a_user_deletes_their_applications_and_events(session, user):
    job = add(session, user)
    job_id = job.id

    session.delete(user)
    session.commit()
    session.expire_all()

    assert session.get(Application, job_id) is None
    assert apps.events_of(session, Application(id=job_id)) == []


# --- Where a job was found (D-062) ----------------------------------------------------


@pytest.mark.parametrize(
    ("url", "source"),
    [
        (None, "manual"),
        ("", "manual"),
        ("https://www.linkedin.com/jobs/view/123", "linkedin"),
        ("https://at.linkedin.com/jobs/view/123", "linkedin"),
        ("https://LINKEDIN.com/jobs", "linkedin"),
        ("https://www.stepstone.at/stellenangebote--pm-wien", "stepstone"),
        ("https://www.stepstone.de/job/1", "stepstone"),
        ("https://www.karriere.at/jobs/123", "karriere_at"),
        ("https://at.indeed.com/viewjob?jk=1", "indeed"),
        ("https://www.indeed.com/viewjob?jk=1", "indeed"),
        ("https://www.xing.com/jobs/wien-pm-1", "xing"),
        ("https://jobs.kranich-health.at/careers/pm", "company_site"),
        # Look-alikes are not the real site.
        ("https://linkedin.com.evil.example/jobs", "company_site"),
        ("https://notlinkedin.com/jobs", "company_site"),
        ("https://example.com/linkedin.com/jobs", "company_site"),
        ("https://my-stepstone.example/job", "company_site"),
        ("https://jobs.indeed", "company_site"),
    ],
)
def test_detect_source(url, source):
    assert apps.detect_source(url) == source
