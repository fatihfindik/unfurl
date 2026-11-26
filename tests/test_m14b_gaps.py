"""M14b: the authorization and error cases the story bank tests don't cover yet:
requests without a session, HEAD, rate limits, Undo tokens of another user, reorder
with foreign ids, a story picked for another of my applications, and stories in the
export and the account deletion (SPEC §5.2, §6.4, §6.7; D-181…D-185)."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import select

from app.main import app
from app.models import Application, Experience, InterviewStory, Story
from app.services import application_service as apps
from app.services import interview_prep_service as prep
from app.services import profile_service
from app.services.auth_service import create_user
from tests.account_data import fill_account
from tests.helpers import USER_PASSWORD, csrf_token, log_in
from tests.test_application_documents import fresh, headers
from tests.test_export import exported

OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "another long password"


def add(session, row):
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@pytest.fixture
def profile(session, user):
    return profile_service.get_or_create_profile(session, user)


@pytest.fixture
def job(session, user) -> Application:
    job = apps.create(session, user, company="Kranich Health", job_title="Senior PM")
    job.status = "interview"
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


@pytest.fixture
def second_job(session, user) -> Application:
    second = apps.create(session, user, company="Second Co", job_title="PM")
    second.status = "interview"
    session.add(second)
    session.commit()
    return fresh(session, second)


@pytest.fixture
def story(session, profile) -> Story:
    return add(session, Story(profile_id=profile.id, title="Launch", result="Shipped"))


@pytest.fixture
def other(session):
    return create_user(session, email=OTHER_EMAIL, password=OTHER_PASSWORD, full_name="Other")


@pytest.fixture
def other_client(session, client, other) -> TestClient:
    """A second browser, logged in as another user (its own cookies)."""
    second = TestClient(app)  # the database override from `client` still applies
    assert log_in(second, OTHER_EMAIL, OTHER_PASSWORD).status_code == 303
    return second


def pick(client, job, story_id):
    return client.post(
        f"/applications/{job.id}/prep/stories",
        data={"story_id": str(story_id)},
        headers=headers(client),
    )


def remove(client, job, story_id):
    return client.post(
        f"/applications/{job.id}/prep/stories/{story_id}/remove", headers=headers(client)
    )


def links(session):
    session.expire_all()
    return [(link.application_id, link.story_id) for link in session.exec(select(InterviewStory))]


def titles(session):
    session.expire_all()
    return sorted(s.title for s in session.exec(select(Story)))


def delete_story(client, story_id) -> str:
    """Delete through My profile; returns the Undo token of the toast."""
    response = client.delete(f"/profile/stories/{story_id}", headers=headers(client))
    assert response.status_code == 200
    return json.loads(response.headers["HX-Trigger"])["toast"]["undo_token"]


def undo(client, token):
    return client.post("/undo/delete", data={"token": token}, headers=headers(client))


# --- Logged out ---------------------------------------------------------------------


def story_writes(job, story):
    """Both prep story POSTs: (url, form)."""
    base = f"/applications/{job.id}/prep/stories"
    return [(base, {"story_id": str(story.id)}), (f"{base}/{story.id}/remove", {})]


@pytest.mark.parametrize("index", [0, 1])
def test_logged_out_story_picks_go_to_login_and_change_nothing(
    client, session, user, job, story, index
):
    add(session, InterviewStory(user_id=user.id, application_id=job.id, story_id=story.id))
    url, data = story_writes(job, story)[index]

    response = client.post(
        url, data=data, headers={"X-CSRF-Token": csrf_token(client)}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    assert links(session) == [(job.id, story.id)]


@pytest.mark.parametrize("index", [0, 1])
def test_logged_out_htmx_story_picks_ask_for_a_full_page_login(client, job, story, index):
    url, data = story_writes(job, story)[index]

    response = client.post(
        url,
        data=data,
        headers={
            "HX-Request": "true",
            "HX-Current-URL": f"http://testserver/applications/{job.id}?tab=interview",
            "X-CSRF-Token": csrf_token(client),
        },
        follow_redirects=False,
    )

    # After login: back to the tab, not to the card fragment.
    assert response.headers["HX-Redirect"] == (
        f"/login?next=/applications/{job.id}%3Ftab%3Dinterview"
    )


def test_logged_out_undo_of_a_story_restores_nothing(client, session, user, job, story):
    from app.services import undo_service

    add(session, InterviewStory(user_id=user.id, application_id=job.id, story_id=story.id))
    copy_id = profile_service.delete_item(session, fresh(session, story), user, "stories")
    session.commit()
    token = undo_service.make_delete_token(user, copy_id)

    response = client.post(
        "/undo/delete",
        data={"token": token},
        headers={"X-CSRF-Token": csrf_token(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert titles(session) == [] and links(session) == []


# --- HEAD on the GETs ---------------------------------------------------------------


def story_gets(story_id):
    return [
        "/profile/stories",
        "/profile/stories/new",
        f"/profile/stories/{story_id}",
        f"/profile/stories/{story_id}/edit",
    ]


def test_head_on_the_story_gets_answers_like_get_without_a_body(auth_client, session, story):
    for url in story_gets(story.id):
        response = auth_client.head(url, headers={"HX-Request": "true"})
        assert response.status_code == 200, url
        assert response.content == b""
    assert titles(session) == ["Launch"]


def test_head_on_another_users_story_is_404(auth_client, session, other):
    their_profile = profile_service.get_or_create_profile(session, other)
    theirs = add(session, Story(profile_id=their_profile.id, title="Theirs"))

    for url in story_gets(theirs.id)[2:]:
        assert auth_client.head(url, headers={"HX-Request": "true"}).status_code == 404, url


# --- Rate limits --------------------------------------------------------------------


def test_picking_is_rate_limited(auth_client, job, story):
    # A second pick of the same story changes nothing (200); it still counts.
    codes = [pick(auth_client, job, story.id).status_code for _ in range(31)]

    assert 429 not in codes[:30] and codes[30] == 429


def test_removing_is_rate_limited(auth_client, job, story):
    # Every remove after the first is a 404 (nothing picked); it still counts.
    pick(auth_client, job, story.id)

    codes = [remove(auth_client, job, story.id).status_code for _ in range(31)]

    assert 429 not in codes[:30] and codes[30] == 429


def test_a_rate_limited_pick_saves_nothing(auth_client, session, profile, job, story):
    other_story = add(session, Story(profile_id=profile.id, title="Other"))
    for _ in range(30):
        pick(auth_client, job, story.id)

    assert pick(auth_client, job, other_story.id).status_code == 429
    assert links(session) == [(job.id, story.id)]


# --- Odd ids ------------------------------------------------------------------------


@pytest.mark.parametrize("bad_id", ["abc", "-1", "0", str(2**70)])
def test_odd_application_ids_on_story_picks_are_404(auth_client, session, story, bad_id):
    response = auth_client.post(
        f"/applications/{bad_id}/prep/stories",
        data={"story_id": str(story.id)},
        headers=headers(auth_client),
    )

    assert response.status_code == 404
    assert links(session) == []


@pytest.mark.parametrize("bad_id", [str(2**70), str(2**63)])
def test_a_huge_story_id_on_pick_is_404(auth_client, session, job, bad_id):
    assert pick(auth_client, job, bad_id).status_code == 404


# A number the database can't hold must be a 404 before any query, not a 500.
@pytest.mark.parametrize("bad_id", [str(2**70), str(2**63)])
def test_a_huge_story_id_on_remove_is_404(auth_client, job, bad_id):
    assert remove(auth_client, job, bad_id).status_code == 404


# --- A story picked for another of my applications ----------------------------------


def test_removing_through_my_other_application_is_404(
    auth_client, session, user, job, second_job, story
):
    pick(auth_client, job, story.id)

    response = remove(auth_client, second_job, story.id)

    assert response.status_code == 404
    assert links(session) == [(job.id, story.id)]


def test_removing_from_one_application_keeps_the_other(
    auth_client, session, job, second_job, story
):
    pick(auth_client, job, story.id)
    pick(auth_client, second_job, story.id)

    assert remove(auth_client, job, story.id).status_code == 200

    assert links(session) == [(second_job.id, story.id)]


def test_the_card_of_my_other_application_doesnt_show_the_pick(
    auth_client, session, user, job, second_job, story
):
    prep.start(session, user, second_job, with_starters=False)  # the card shows after start
    pick(auth_client, job, story.id)

    html = auth_client.get(f"/applications/{second_job.id}?tab=interview").text
    start = html.index('<section id="prep-stories"')
    card = html[start : html.index("</section>", start)]

    # Offered to pick, not shown as picked: no "Remove" and not the result text.
    assert f'<option value="{story.id}">' in card
    assert "Shipped" not in card


def test_another_users_card_never_lists_my_stories(other_client, session, other, story):
    theirs = apps.create(session, other, company="Other", job_title="PM")

    html = other_client.get(f"/applications/{theirs.id}?tab=interview").text

    assert "Launch" not in html


# --- Unlinking after the story was deleted ------------------------------------------


def test_removing_a_deleted_story_is_404(auth_client, session, job, story):
    pick(auth_client, job, story.id)
    delete_story(auth_client, story.id)

    response = remove(auth_client, job, story.id)

    assert response.status_code == 404
    assert links(session) == []


def test_picking_a_deleted_story_is_404(auth_client, session, job, story):
    delete_story(auth_client, story.id)

    assert pick(auth_client, job, story.id).status_code == 404
    assert links(session) == []


# --- Undo of a story delete ---------------------------------------------------------


def test_my_story_undo_token_does_nothing_for_another_user(
    auth_client, other_client, session, job, story
):
    pick(auth_client, job, story.id)
    token = delete_story(auth_client, story.id)

    response = undo(other_client, token)

    assert response.status_code == 204
    assert titles(session) == [] and links(session) == []
    assert "Nothing to undo." in other_client.get("/").text
    # The copy is still mine: my own Undo still works, with the link.
    assert undo(auth_client, token).status_code == 204
    assert titles(session) == ["Launch"]
    assert [app_id for app_id, _ in links(session)] == [job.id]


def test_a_story_undo_token_works_once(auth_client, session, job, story):
    pick(auth_client, job, story.id)
    token = delete_story(auth_client, story.id)

    undo(auth_client, token)
    undo(auth_client, token)

    assert titles(session) == ["Launch"]
    assert len(links(session)) == 1


def test_undo_never_lands_in_the_other_users_profile(
    auth_client, other_client, session, other, story
):
    token = delete_story(auth_client, story.id)
    their_profile = profile_service.get_or_create_profile(session, other)

    undo(other_client, token)

    session.expire_all()
    assert session.exec(select(Story).where(Story.profile_id == their_profile.id)).all() == []


# --- Editing: the experience choices ------------------------------------------------


def test_the_edit_form_lists_only_my_experiences(auth_client, session, profile, other, story):
    import datetime as dt

    their_profile = profile_service.get_or_create_profile(session, other)
    add(
        session,
        Experience(
            profile_id=their_profile.id,
            title="Secret role",
            company="Their Co",
            start_date=dt.date(2020, 1, 1),
        ),
    )

    for url in (f"/profile/stories/{story.id}/edit", "/profile/stories/new"):
        html = auth_client.get(url, headers={"HX-Request": "true"}).text
        assert "Their Co" not in html and "Secret role" not in html, url


# --- Reorder with foreign ids -------------------------------------------------------


def send_order(client, ids):
    return client.post(
        "/profile/stories/order", data={"ids": [str(i) for i in ids]}, headers=headers(client)
    )


def story_order(session, profile) -> list[int]:
    session.expire_all()
    return [row.id for row in profile_service.list_items(session, profile, Story)]


@pytest.fixture
def two_each(session, profile, other):
    """Two stories of mine and two of the other user."""
    mine = [add(session, Story(profile_id=profile.id, title=f"Mine {n}")) for n in range(2)]
    their_profile = profile_service.get_or_create_profile(session, other)
    theirs = [
        add(session, Story(profile_id=their_profile.id, title=f"Theirs {n}")) for n in range(2)
    ]
    return mine, theirs, their_profile


def test_another_users_story_ids_in_the_order_are_404(auth_client, session, profile, two_each):
    mine, theirs, their_profile = two_each
    my_before, their_before = story_order(session, profile), story_order(session, their_profile)

    ids = [theirs[1].id, mine[1].id, mine[0].id, theirs[0].id]
    assert send_order(auth_client, ids).status_code == 404

    assert story_order(session, profile) == my_before
    assert story_order(session, their_profile) == their_before


def test_only_another_users_story_ids_in_the_order_are_404(auth_client, session, profile, two_each):
    _, theirs, their_profile = two_each
    before = story_order(session, their_profile)

    assert send_order(auth_client, [theirs[1].id, theirs[0].id]).status_code == 404
    assert story_order(session, their_profile) == before


@pytest.mark.parametrize("direction", ["up", "down"])
def test_another_users_story_cannot_be_moved(auth_client, session, profile, two_each, direction):
    _, theirs, their_profile = two_each
    before = story_order(session, their_profile)
    version = fresh(session, their_profile).version

    response = auth_client.post(
        f"/profile/stories/{theirs[1].id}/move",
        data={"direction": direction},
        headers=headers(auth_client),
    )

    assert response.status_code == 404
    assert story_order(session, their_profile) == before
    assert fresh(session, their_profile).version == version


# --- Export and account deletion ----------------------------------------------------


def test_the_export_has_only_my_stories_and_picks(auth_client, session, user, other):
    mine = fill_account(session, user)
    theirs = fill_account(session, other)

    data = exported(auth_client)

    assert [s["id"] for s in data["stories"]] == [mine["story"].id]
    assert [s["id"] for s in data["interview_stories"]] == [mine["interview_story"].id]
    assert theirs["story"].id != mine["story"].id


def test_deleting_my_account_removes_my_stories_and_keeps_theirs(
    auth_client, session, engine, user, other
):
    fill_account(session, user)
    theirs = fill_account(session, other)

    response = auth_client.post(
        "/settings/delete-account",
        data={"password": USER_PASSWORD, "csrf_token": csrf_token(auth_client)},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    # Story has no user_id (it belongs to the profile), so it's counted directly.
    with engine.connect() as connection:
        stories = connection.execute(text("SELECT id FROM story")).scalars().all()
        picks = connection.execute(text("SELECT id FROM interviewstory")).scalars().all()
    assert stories == [theirs["story"].id]
    assert picks == [theirs["interview_story"].id]
