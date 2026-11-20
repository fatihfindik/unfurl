"""One canonical link per job (SPEC §8.5, D-138), and its use when a job is saved."""

import pytest

from app.services import application_service as apps
from app.services.url_service import canonical_url

# (link, canonical link). tests/test_migrations.py runs the copy in migration 0015
# on these too, so the copy can't drift.
EXAMPLES = [
    # LinkedIn: the job page, a slug before the id, the search page with currentJobId.
    (
        "https://www.linkedin.com/jobs/view/3912345678/?refId=abc&trackingId=xyz",
        "https://linkedin.com/jobs/view/3912345678",
    ),
    (
        "https://at.linkedin.com/jobs/view/backend-developer-at-acme-3912345678",
        "https://linkedin.com/jobs/view/3912345678",
    ),
    (
        "https://www.linkedin.com/jobs/search/?currentJobId=3912345678&keywords=python",
        "https://linkedin.com/jobs/view/3912345678",
    ),
    # A LinkedIn page that isn't one job: the query is dropped.
    (
        "https://www.linkedin.com/jobs/collections/?start=25",
        "https://linkedin.com/jobs/collections",
    ),
    # StepStone, karriere.at, XING: the id is in the path.
    (
        "https://www.stepstone.at/stellenangebote--Senior-Developer-Wien-Acme--1234567-inline.html?rltr=1_1_25",
        "https://stepstone.at/stellenangebote--Senior-Developer-Wien-Acme--1234567-inline.html",
    ),
    ("https://www.karriere.at/jobs/7654321?utm_source=mail", "https://karriere.at/jobs/7654321"),
    (
        "https://www.xing.com/jobs/wien-developer-123456?ijt=x",
        "https://xing.com/jobs/wien-developer-123456",
    ),
    # Indeed: "jk", or "vjk" next to search results.
    (
        "https://at.indeed.com/viewjob?jk=abc123&from=serp",
        "https://at.indeed.com/viewjob?jk=abc123",
    ),
    (
        "https://at.indeed.com/jobs?q=python&l=Wien&vjk=abc123",
        "https://at.indeed.com/viewjob?jk=abc123",
    ),
    # Company and ATS pages keep their query (it is the job), sorted, without tracking.
    (
        "https://careers.acme.com/jobs?jobId=17&utm_source=linkedin&utm_medium=social",
        "https://careers.acme.com/jobs?jobId=17",
    ),
    ("https://careers.acme.com/jobs?jobId=18", "https://careers.acme.com/jobs?jobId=18"),
    (
        "https://boards.greenhouse.io/acme?gh_jid=4567&gh_src=abc",
        "https://boards.greenhouse.io/acme?gh_jid=4567",
    ),
    (
        "https://jobs.acme.com/apply?b=2&a=1&fbclid=x&gclid=y&ref=z&source=w",
        "https://jobs.acme.com/apply?a=1&b=2",
    ),
    # www, upper-case host, http, fragment, "/" at the end, another port.
    ("http://WWW.Acme.COM/Careers/Developer/#apply", "https://acme.com/Careers/Developer"),
    ("http://localhost:8001/job/5", "https://localhost:8001/job/5"),
    # Not web links.
    ("", None),
    (None, None),
    ("not a link", None),
    ("mailto:jobs@acme.com", None),
    ("ftp://acme.com/job", None),
    ("https://acme.com:notaport/job", None),
]


@pytest.mark.parametrize(("url", "expected"), EXAMPLES)
def test_canonical_url(url, expected):
    assert canonical_url(url) == expected


# Added after migration 0015, so not in EXAMPLES (its copy doesn't know them).
NEWER_EXAMPLES = [
    # karriere.at search pages: the job shown next to the list is in the fragment (D-144).
    (
        "https://www.karriere.at/jobs?keywords=product+manager&locations=vienna#10035685",
        "https://karriere.at/jobs/10035685",
    ),
    ("https://www.karriere.at/jobs/python/wien#10035685", "https://karriere.at/jobs/10035685"),
    # A job page wins over the fragment; a fragment that isn't an id is dropped.
    ("https://www.karriere.at/jobs/123#456", "https://karriere.at/jobs/123"),
    ("https://www.karriere.at/jobs#abc", "https://karriere.at/jobs"),
    ("https://www.karriere.at/jobs#1234567890123", "https://karriere.at/jobs"),
    (
        "https://www.karriere.at/jobs#\u0661\u0662\u0663",
        "https://karriere.at/jobs",
    ),  # Arabic digits
    # LinkedIn's newer search page.
    (
        "https://www.linkedin.com/jobs/search-results/?currentJobId=4490740687&keywords=pm",
        "https://linkedin.com/jobs/view/4490740687",
    ),
]


@pytest.mark.parametrize(("url", "expected"), NEWER_EXAMPLES)
def test_canonical_url_newer_rules(url, expected):
    assert canonical_url(url) == expected


def test_two_karriere_jobs_from_one_search_page_stay_two_jobs():
    search = "https://www.karriere.at/jobs?keywords=product+manager"
    assert canonical_url(search + "#10035685") != canonical_url(search + "#10036885")


def test_two_job_ids_on_one_company_page_stay_two_jobs():
    first = canonical_url("https://careers.acme.com/jobs?jobId=17")
    second = canonical_url("https://careers.acme.com/jobs?jobId=18")
    assert first != second


def test_a_very_long_link_has_no_canonical_form():
    # Postgres can't index very long values; such a link is simply never matched.
    assert canonical_url("https://acme.com/job?q=" + "é" * 1500) is None


def test_saving_a_job_stores_its_canonical_link(session, user):
    job = apps.create(
        session,
        user,
        company="Acme",
        job_title="Developer",
        job_url="https://www.linkedin.com/jobs/view/3912345678/?trk=abc",
    )
    assert job.canonical_url == "https://linkedin.com/jobs/view/3912345678"

    no_link = apps.create(session, user, company="Acme", job_title="Developer")
    assert no_link.canonical_url is None


def test_a_new_link_in_details_gives_a_new_canonical_link(session, user):
    job = apps.create(
        session, user, company="Acme", job_title="Developer", job_url="https://acme.com/jobs?id=1"
    )
    # The Details form sends every field; only the link changes here.
    values = {field: getattr(job, field) for field in apps.DETAIL_FIELDS}
    values["job_url"] = "https://acme.com/jobs?id=2"

    apps.update_details(session, user, job, values)
    assert job.canonical_url == "https://acme.com/jobs?id=2"

    apps.update_details(session, user, job, {**values, "job_url": None})
    assert job.canonical_url is None
