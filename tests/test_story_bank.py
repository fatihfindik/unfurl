"""M14b STAR story bank (D-181…D-185): the models. Section and tab come in later steps."""

import datetime as dt
import json

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.models import Application, Experience, InterviewStory, Story
from app.services.profile_service import get_or_create_profile


@pytest.fixture
def profile(session, user):
    return get_or_create_profile(session, user)


def add(session, row):
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_a_story_has_defaults(session, profile):
    story = add(session, Story(profile_id=profile.id, title="Conflict in a team"))

    assert (story.situation, story.task, story.action, story.result) == (None,) * 4
    assert story.tags is None and story.experience_id is None
    assert story.sort_order == 0 and story.created_at is not None


def test_deleting_the_experience_clears_the_link(session, profile):
    job = add(
        session,
        Experience(
            profile_id=profile.id, title="PM", company="Acme", start_date=dt.date(2024, 1, 1)
        ),
    )
    story = add(session, Story(profile_id=profile.id, title="Launch", experience_id=job.id))

    session.exec(delete(Experience).where(Experience.id == job.id))
    session.commit()
    session.refresh(story)

    assert story.experience_id is None


def test_a_story_is_picked_once_per_interview(session, user, profile):
    job = add(session, Application(user_id=user.id, company="Nordbau", job_title="PM"))
    story = add(session, Story(profile_id=profile.id, title="Launch"))
    add(session, InterviewStory(user_id=user.id, application_id=job.id, story_id=story.id))

    session.add(InterviewStory(user_id=user.id, application_id=job.id, story_id=story.id))
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.parametrize("deleted", [Story, Application])
def test_deleting_the_story_or_the_application_deletes_the_link(session, user, profile, deleted):
    job = add(session, Application(user_id=user.id, company="Nordbau", job_title="PM"))
    story = add(session, Story(profile_id=profile.id, title="Launch"))
    add(session, InterviewStory(user_id=user.id, application_id=job.id, story_id=story.id))

    row_id = story.id if deleted is Story else job.id
    session.exec(delete(deleted).where(deleted.id == row_id))
    session.commit()

    assert session.exec(select(InterviewStory)).all() == []


# --- The Stories section on My profile (D-181, D-182, D-184) -------------------------

from app.models import CVDocument, Profile  # noqa: E402
from app.services import application_service as apps  # noqa: E402
from app.services import document_service, profile_service  # noqa: E402
from app.services.auth_service import create_user  # noqa: E402
from tests.helpers import csrf_token  # noqa: E402


def send(client, method, url, data=None):
    headers = {"HX-Request": "true", "X-CSRF-Token": csrf_token(client)}
    return client.request(method, url, data=data or {}, headers=headers)


def experience(session, profile, company="Acme"):
    return add(
        session,
        Experience(
            profile_id=profile.id, title="PM", company=company, start_date=dt.date(2022, 1, 1)
        ),
    )


def test_the_section_is_on_my_profile_after_projects(auth_client, profile):
    html = auth_client.get("/profile").text

    assert html.index(">Projects</h2>") < html.index(">Stories</h2>")
    assert "No stories yet." in html


def test_add_a_story_with_an_experience_and_tags(auth_client, session, profile):
    job = experience(session, profile)

    form = send(auth_client, "GET", "/profile/stories/new")
    assert f'<option value="{job.id}">PM · Acme</option>' in form.text

    response = send(
        auth_client,
        "POST",
        "/profile/stories",
        {"title": "Launch", "experience_id": str(job.id), "tags": " sql,Leadership , SQL,, "},
    )

    assert response.status_code == 200
    story = session.exec(select(Story)).one()
    assert (story.experience_id, story.tags) == (job.id, "sql, Leadership")
    assert "at PM · Acme" in response.text
    assert 'text-text-2">Leadership</span>' in response.text


@pytest.mark.parametrize("bad", ["abc", "-1", "0", str(2**31), "1" * 30])
def test_a_bad_experience_id_is_a_form_error(auth_client, session, profile, bad):
    response = send(auth_client, "POST", "/profile/stories", {"title": "T", "experience_id": bad})

    assert response.status_code == 200
    assert "Choose an experience from the list." in response.text
    assert session.exec(select(Story)).all() == []


def test_another_profiles_experience_is_a_form_error(auth_client, session, profile):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = experience(session, profile_service.get_or_create_profile(session, other))
    story = add(session, Story(profile_id=profile.id, title="Mine"))

    added = send(
        auth_client, "POST", "/profile/stories", {"title": "T", "experience_id": str(theirs.id)}
    )
    edited = send(
        auth_client,
        "POST",
        f"/profile/stories/{story.id}",
        {"title": "T", "experience_id": str(theirs.id)},
    )

    for response in (added, edited):
        assert response.status_code == 200
        assert "Choose an experience from the list." in response.text
    session.refresh(story)
    assert (story.title, story.experience_id) == ("Mine", None)
    assert len(session.exec(select(Story)).all()) == 1


def test_story_text_limits_and_escaping(auth_client, session, profile):
    too_long = send(auth_client, "POST", "/profile/stories", {"title": "T", "action": "x" * 2001})
    assert "Use at most 2000 characters." in too_long.text

    send(
        auth_client,
        "POST",
        "/profile/stories",
        {"title": "<b>T</b>", "result": "<script>x</script>"},
    )
    html = auth_client.get("/profile").text
    assert "&lt;b&gt;T&lt;/b&gt;" in html and "&lt;script&gt;x&lt;/script&gt;" in html
    assert "<script>x</script>" not in html


def test_too_many_tags_after_cleaning(auth_client, session, profile):
    tags = ", ".join(f"tag{i:03d}" for i in range(40))  # 278 characters

    response = send(auth_client, "POST", "/profile/stories", {"title": "T", "tags": tags})

    assert "Use at most 200 characters." in response.text


def test_stories_never_reach_a_cv_or_the_strength(session, user, profile):
    add(session, Story(profile_id=profile.id, title="Secret story", situation="Only for me"))
    before = profile_service.profile_strength(session, profile)

    cv = document_service.create_cv(session, user, "Master", "en", "classic")

    assert "Secret story" not in str(session.get(CVDocument, cv.id).content)
    assert profile_service.profile_strength(session, profile) == before


def test_delete_and_undo_bring_the_story_and_its_links_back(auth_client, session, user, profile):
    job = experience(session, profile)
    story = add(session, Story(profile_id=profile.id, title="Launch", experience_id=job.id))
    first = apps.create(session, user, company="Alpha", job_title="PM")
    second = apps.create(session, user, company="Beta", job_title="PM")
    second.archived = True
    session.add(second)
    session.commit()
    for application in (first, second):
        add(
            session,
            InterviewStory(user_id=user.id, application_id=application.id, story_id=story.id),
        )

    response = send(auth_client, "DELETE", f"/profile/stories/{story.id}")
    token = json.loads(response.headers["HX-Trigger"])["toast"]["undo_token"]
    assert session.exec(select(InterviewStory)).all() == []

    send(auth_client, "POST", "/undo/delete", {"token": token})

    session.expire_all()
    back = session.exec(select(Story)).one()
    assert (back.title, back.experience_id) == ("Launch", job.id)
    links = session.exec(select(InterviewStory)).all()
    assert {link.application_id for link in links} == {first.id, second.id}  # archived too
    assert all(link.story_id == back.id for link in links)


def test_undo_clears_an_experience_that_is_gone(auth_client, session, profile):
    job = experience(session, profile)
    story = add(session, Story(profile_id=profile.id, title="Launch", experience_id=job.id))
    token = json.loads(
        send(auth_client, "DELETE", f"/profile/stories/{story.id}").headers["HX-Trigger"]
    )["toast"]["undo_token"]
    session.exec(delete(Experience).where(Experience.id == job.id))
    session.commit()

    send(auth_client, "POST", "/undo/delete", {"token": token})

    session.expire_all()
    back = session.exec(select(Story)).one()
    assert (back.title, back.experience_id) == ("Launch", None)


def test_undo_skips_deleted_and_full_applications(session, user, profile):
    gone = apps.create(session, user, company="Gone", job_title="PM")
    full = apps.create(session, user, company="Full", job_title="PM")
    for number in range(20):
        filler = add(session, Story(profile_id=profile.id, title=f"S{number}"))
        add(session, InterviewStory(user_id=user.id, application_id=full.id, story_id=filler.id))
    gone_id = gone.id
    session.exec(delete(Application).where(Application.id == gone_id))
    session.commit()
    payload = {
        "profile_id": profile.id,
        "title": "Back",
        "sort_order": 99,
        "application_ids": [gone_id, full.id, "x", -1, 2**40],
    }

    story = profile_service.restore_story(session, user, payload)
    session.commit()

    links = session.exec(select(InterviewStory).where(InterviewStory.story_id == story.id)).all()
    assert links == []


def test_undo_never_links_another_users_application(session, user, profile):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = apps.create(session, other, company="Other", job_title="PM")
    payload = {"profile_id": profile.id, "title": "Back", "application_ids": [theirs.id]}

    profile_service.restore_story(session, user, payload)
    session.commit()

    assert session.exec(select(InterviewStory)).all() == []


def test_undo_into_another_profile_is_refused(session, user, profile):
    from app.services import trash_service

    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = profile_service.get_or_create_profile(session, other)

    with pytest.raises(trash_service.CannotRestore):
        profile_service.restore_story(session, user, {"profile_id": theirs.id, "title": "X"})
    assert session.exec(select(Profile).where(Profile.id == theirs.id)).one().version == 0


# --- Stories for this interview (D-183, D-184) --------------------------------------

from app.services import interview_prep_service as prep  # noqa: E402


@pytest.fixture
def job(session, user):
    application = apps.create(session, user, company="Kranich Health", job_title="Senior PM")
    application.status = "interview"
    session.add(application)
    session.commit()
    prep.start(session, user, application, with_starters=False)
    return application


def card_of(client, job) -> str:
    html = client.get(f"/applications/{job.id}?tab=interview").text
    start = html.index('<section id="prep-stories"')
    return html[start : html.index("</section>", start)]


def pick(client, job, story_id):
    return send(client, "POST", f"/applications/{job.id}/prep/stories", {"story_id": str(story_id)})


def remove(client, job, story_id):
    return send(client, "POST", f"/applications/{job.id}/prep/stories/{story_id}/remove")


def links(session):
    session.expire_all()
    return [(link.application_id, link.story_id) for link in session.exec(select(InterviewStory))]


def test_an_empty_library_links_to_my_profile(auth_client, profile, job):
    card = card_of(auth_client, job)

    assert "No stories yet." in card and 'href="/profile#profile-stories"' in card
    assert "Add a story" not in card


def test_the_card_sits_between_questions_and_notes(auth_client, profile, job):
    html = auth_client.get(f"/applications/{job.id}?tab=interview").text

    assert html.index('id="prep-likely"') < html.index('id="prep-stories"')
    assert html.index('id="prep-stories"') < html.index('id="prep-notes"')


def test_pick_and_remove_a_story(auth_client, session, profile, job):
    story = add(session, Story(profile_id=profile.id, title="Launch", tags="SQL", result="Shipped"))
    assert f'<option value="{story.id}">Launch · SQL</option>' in card_of(auth_client, job)

    response = pick(auth_client, job, story.id)

    assert response.status_code == 200
    assert response.text.lstrip().startswith('<section id="prep-stories"')
    assert "Shipped" in response.text and "Add a story" not in response.text  # none left
    assert "HX-Trigger" not in response.headers
    assert links(session) == [(job.id, story.id)]

    response = remove(auth_client, job, story.id)
    assert response.status_code == 200 and "HX-Trigger" not in response.headers
    assert links(session) == []
    assert session.get(Story, story.id) is not None  # only the link is gone


def test_picking_twice_keeps_one_link(auth_client, session, profile, job):
    story = add(session, Story(profile_id=profile.id, title="Launch"))

    pick(auth_client, job, story.id)
    assert pick(auth_client, job, story.id).status_code == 200

    assert links(session) == [(job.id, story.id)]


def test_picking_starts_the_prep(auth_client, session, user, profile):
    fresh_job = apps.create(session, user, company="Alpha", job_title="PM")
    story = add(session, Story(profile_id=profile.id, title="Launch"))

    pick(auth_client, fresh_job, story.id)

    session.refresh(fresh_job)
    assert fresh_job.interview_prep_started_at is not None


def test_at_most_20_stories(auth_client, session, user, profile, job):
    stories = [add(session, Story(profile_id=profile.id, title=f"S{n}")) for n in range(21)]
    for story in stories[:20]:
        add(session, InterviewStory(user_id=user.id, application_id=job.id, story_id=story.id))

    response = pick(auth_client, job, stories[20].id)

    assert response.status_code == 409
    toast = json.loads(response.headers["HX-Trigger"])["toast"]
    assert toast["message"] == "An interview can have up to 20 stories."
    assert len(links(session)) == 20
    assert "Add a story" not in card_of(auth_client, job)


def test_archived_job_is_409_and_read_only(auth_client, session, user, profile, job):
    story = add(session, Story(profile_id=profile.id, title="Launch"))
    other = add(session, Story(profile_id=profile.id, title="Other"))
    add(session, InterviewStory(user_id=user.id, application_id=job.id, story_id=story.id))
    job.archived = True
    session.add(job)
    session.commit()

    assert pick(auth_client, job, other.id).status_code == 409
    assert remove(auth_client, job, story.id).status_code == 409
    card = card_of(auth_client, job)
    assert "Remove" not in card and "Add a story" not in card
    assert links(session) == [(job.id, story.id)]


def test_another_users_story_or_job_is_404(auth_client, session, user, profile, job):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    their_profile = profile_service.get_or_create_profile(session, other)
    their_story = add(session, Story(profile_id=their_profile.id, title="Theirs"))
    their_job = apps.create(session, other, company="Other", job_title="PM")
    mine = add(session, Story(profile_id=profile.id, title="Mine"))
    add(
        session,
        InterviewStory(user_id=other.id, application_id=their_job.id, story_id=their_story.id),
    )

    assert pick(auth_client, job, their_story.id).status_code == 404
    assert pick(auth_client, their_job, mine.id).status_code == 404
    assert remove(auth_client, their_job, their_story.id).status_code == 404
    assert links(session) == [(their_job.id, their_story.id)]


@pytest.mark.parametrize("bad", ["", "abc", "0", str(2**31)])
def test_a_bad_story_id_is_404(auth_client, profile, job, bad):
    assert pick(auth_client, job, bad).status_code == 404
    assert remove(auth_client, job, bad or "x").status_code == 404


def test_removing_a_story_that_isnt_picked_is_404(auth_client, session, profile, job):
    story = add(session, Story(profile_id=profile.id, title="Launch"))

    assert remove(auth_client, job, story.id).status_code == 404


def test_story_writes_need_csrf_and_few_fields(auth_client, session, profile, job):
    story = add(session, Story(profile_id=profile.id, title="Launch"))
    url = f"/applications/{job.id}/prep/stories"

    assert auth_client.post(url, data={"story_id": str(story.id)}).status_code == 403
    many = {"story_id": str(story.id)} | {f"f{n}": "x" for n in range(20)}
    assert send(auth_client, "POST", url, many).status_code == 400
    assert links(session) == []


def test_picked_stories_are_escaped(auth_client, session, profile, job):
    story = add(
        session, Story(profile_id=profile.id, title="<i>T</i>", action="<script>x</script>")
    )
    pick(auth_client, job, story.id)

    card = card_of(auth_client, job)

    assert "&lt;i&gt;T&lt;/i&gt;" in card and "&lt;script&gt;" in card
