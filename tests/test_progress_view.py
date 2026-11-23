"""The Progress tab on a sent or answered application (SPEC §6.7, §12 M9a; D-167)."""

import datetime as dt
import json
import re

import pytest

from app.models import Application, CVDocument
from app.services import application_service as apps
from app.services import clock
from app.services import document_service as docs
from app.services.profile_service import get_or_create_profile
from tests.test_application_documents import fresh, headers, move, use_master


@pytest.fixture
def master(session, user) -> CVDocument:
    get_or_create_profile(session, user)
    return docs.create_cv(session, user, "Master CV", "en", "classic")


@pytest.fixture
def job(session, user) -> Application:
    return apps.create(session, user, company="Kranich Health", job_title="Senior PM")


def progress(client, job) -> str:
    html = client.get(f"/applications/{job.id}").text
    match = re.search(r'<div id="progress".*', html, re.S)
    assert match, "no Progress tab"
    return html


def applied(client, session, job, master) -> Application:
    use_master(client, job, master)
    move(client, job, "applied")
    return fresh(session, job)


def test_progress_shows_the_day_and_the_sent_cv(auth_client, session, job, master):
    job = applied(auth_client, session, job, master)

    html = progress(auth_client, job)

    assert "Applied on " + job.applied_at.strftime("%a, %-d %b %Y") in html
    section = re.search(r'<div id="progress".*?</section>', html, re.S).group(0)
    assert "Sent · locked" in section
    assert f'data-dialog-url="/applications/{job.id}/drawer?doc=cv"' in section
    assert f'href="/cvs/{job.cv_id}/pdf"' in section


def test_what_happens_next_names_the_next_step_and_offers_the_interview(
    auth_client, session, job, master
):
    job = applied(auth_client, session, job, master)

    html = progress(auth_client, job)

    assert "What happens next" in html
    assert "Follow up on " in html  # the automatic next step after Applied (§6.13)
    assert "I got an interview" in html
    assert re.search(r'hx-vals=\'\{"status": "interview"\}\'', html)


def test_i_got_an_interview_moves_to_interview_and_opens_the_modal(
    auth_client, session, job, master
):
    job = applied(auth_client, session, job, master)

    response = auth_client.post(
        f"/applications/{job.id}/status", data={"status": "interview"}, headers=headers(auth_client)
    )

    assert response.status_code == 204
    assert "open-interview" in json.loads(response.headers["HX-Trigger"])
    assert fresh(session, job).status == "interview"


def test_a_next_step_changed_by_hand_keeps_its_own_name(auth_client, session, user, job, master):
    job = applied(auth_client, session, job, master)
    auth_client.post(
        f"/applications/{job.id}/next-step",
        data={
            "next_action_type": "custom",
            "note": "Call Mira",
            "date": (clock.today() + dt.timedelta(days=1)).isoformat(),
        },
        headers=headers(auth_client),
    )

    html = progress(auth_client, fresh(session, job))

    assert (
        "Call Mira" in html
        and "Follow up on " not in html.split("What happens next")[1].split("</section>")[0]
    )


def test_how_you_applied_lists_the_steps(auth_client, session, job, master):
    job = applied(auth_client, session, job, master)

    html = progress(auth_client, job)

    history = re.search(r"How you applied.*?</details>", html, re.S).group(0)
    assert "Added by hand" in history
    assert "locked: this is what you sent" in history
    assert "Status: Saved → Applied" in history


@pytest.mark.parametrize(
    ("status", "text"),
    [
        ("interview", "thank-you note"),
        ("offer", "You have an offer."),
        ("rejected", "This one didn't work out."),
    ],
)
def test_each_status_says_what_comes_next(auth_client, session, job, master, status, text):
    job = applied(auth_client, session, job, master)
    move(auth_client, job, status)

    html = progress(auth_client, fresh(session, job))

    assert text in html and "I got an interview" not in html


def test_rejected_without_applying_has_no_applied_day(auth_client, session, job):
    move(auth_client, job, "rejected")

    html = progress(auth_client, fresh(session, job))

    assert "Applied on" not in html and "You did not apply to this job." in html


def test_an_archived_job_offers_no_interview_button(auth_client, session, user, job, master):
    job = applied(auth_client, session, job, master)
    apps.set_status(session, user, job, "archived")

    assert "I got an interview" not in progress(auth_client, fresh(session, job))


def test_every_id_on_the_progress_page_is_unique(auth_client, session, user, job, master):
    docs.create_cv(session, user, "Spare CV", "en", "classic")
    move(auth_client, job, "applied")  # applied without a CV: the pick forms are shown

    html = progress(auth_client, fresh(session, job))

    ids = re.findall(r'\sid="([^"]+)"', html)
    assert "progress-link-cv" in ids and "card-link-cv" in ids
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})
