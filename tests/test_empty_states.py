"""Every list has an empty state (D-127, SPEC §12 M13a): the inventory in
docs/plans/M13a.md, one case per row, the ones from earlier milestones included."""

import html

import pytest
from sqlmodel import delete

from app.models import ApplicationEvent, LearningGoal
from app.services import application_service as apps
from app.services import document_service as docs
from app.services.profile_service import get_or_create_profile


def text_of(client, url) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return html.unescape(response.text)


def a_job(session, user):
    return apps.create(session, user, company="Acme", job_title="PM")


def a_goal(session, user):
    session.add(LearningGoal(user_id=user.id, title="Python"))
    session.commit()


def a_master(session, user):
    get_or_create_profile(session, user)
    return docs.create_cv(session, user, "Master CV", "en", "classic")


# (url, what to create first, the empty text)
CASES = {
    "board": ("/", None, "No applications yet."),
    "board column": ("/", a_job, "No jobs here"),
    "filters": ("/?q=nothing-matches", a_job, "No jobs match these filters."),
    "list filters": ("/?view=list&q=nothing-matches", a_job, "No jobs match these filters."),
    "documents": ("/documents", None, "No CVs yet. Create your first master CV"),
    "copies": ("/documents", a_master, "No copies yet."),
    "cover letters": ("/documents?tab=letters", None, "Coming soon."),
    "profile experience": ("/profile", None, "No experience yet."),
    "profile education": ("/profile", None, "No education yet."),
    "profile certifications": ("/profile", None, "No certifications yet."),
    "profile projects": ("/profile", None, "No projects yet."),
    "profile skills": ("/profile", None, "No skills yet."),
    "profile languages": ("/profile", None, "No languages yet."),
    "goals": ("/learning", None, "No goals yet."),
    "today": ("/learning?view=today", a_goal, "Nothing planned for today."),
    "next 7 days": ("/learning?view=week", a_goal, "Nothing planned for the next 7 days."),
    "all tasks": ("/learning?view=all", a_goal, "No open tasks."),
    "catch-up": ("/learning?view=catchup", a_goal, "Nothing to catch up on."),
    "next reminder": ("/learning", a_goal, "No tasks planned"),
    "notifications": ("/notifications/panel", None, "No notifications yet."),
}


@pytest.mark.parametrize("name", CASES)
def test_an_empty_list_says_so(auth_client, session, user, name):
    url, prepare, text = CASES[name]
    if prepare:
        prepare(session, user)

    assert text in text_of(auth_client, url)


def test_a_job_without_a_cv(auth_client, session, user):
    job = a_job(session, user)

    assert "No master CV yet." in text_of(auth_client, f"/applications/{job.id}")

    apps.archive(session, user, job)  # archived: nothing can be linked any more
    assert "No CV for this job." in text_of(auth_client, f"/applications/{job.id}")


def test_a_board_with_only_archived_jobs(auth_client, session, user):
    apps.archive(session, user, a_job(session, user))

    assert "All your jobs are archived." in text_of(auth_client, "/")


def test_the_filter_note_links_to_clear_the_filters(auth_client, session, user):
    a_job(session, user)

    page = text_of(auth_client, "/?q=nothing-matches")

    assert "Clear filters" in page
    assert "All your jobs are archived." not in page
    assert "No jobs match these filters." not in text_of(auth_client, "/")


def test_a_timeline_without_events(auth_client, session, user):
    # Every job gets a "Saved" event; old data might not have one.
    job = a_job(session, user)
    session.exec(delete(ApplicationEvent).where(ApplicationEvent.application_id == job.id))
    session.commit()

    assert "Nothing here yet." in text_of(auth_client, f"/applications/{job.id}")
