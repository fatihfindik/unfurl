"""Profile routes: owner and error cases the other profile tests don't reach (M2a)."""

from datetime import date

import pytest
from sqlmodel import select

from app.models import Experience, Profile, Skill
from app.schemas.profile import ExperienceForm
from app.services.auth_service import create_user
from tests.helpers import csrf_token

HTMX = {"HX-Request": "true"}
EXPERIENCE = {"title": "PM", "company": "Ostwind", "start_date": "2018-04"}


def htmx_send(client, method, url, data=None):
    headers = HTMX | {"X-CSRF-Token": csrf_token(client)}
    return client.request(method, url, data=data, headers=headers, follow_redirects=False)


def plain_send(client, method, url, data=None):
    # A normal browser request: CSRF token in the form, no HX-Request header.
    data = (data or {}) | {"csrf_token": csrf_token(client)}
    headers = {"X-CSRF-Token": data["csrf_token"]}
    return client.request(method, url, data=data, headers=headers, follow_redirects=False)


def rows(session, model):
    session.expire_all()
    return session.exec(select(model)).all()


def profile_of(session, user_id) -> Profile:
    session.expire_all()
    return session.exec(select(Profile).where(Profile.user_id == user_id)).one()


@pytest.fixture
def profile_client(auth_client):
    auth_client.get("/profile")  # creates the profile
    return auth_client


@pytest.fixture
def other_profile(session) -> Profile:
    """B's profile, with one Experience row."""
    other = create_user(session, email="b@example.com", password="b-password-1", full_name="B")
    profile = Profile(user_id=other.id, headline="B's headline")
    session.add(profile)
    session.commit()
    session.add(
        Experience(profile_id=profile.id, title="B", company="B Corp", start_date=date(2020, 1, 1))
    )
    session.commit()
    session.refresh(profile)
    return profile


# --- Ids from another section -----------------------------------------------------


def test_own_skill_id_in_the_experience_url_is_404(profile_client, session, other_profile):
    # A's skill and B's experience have the same id (each table counts from 1).
    htmx_send(profile_client, "POST", "/profile/skills", {"name": "SQL"})
    [skill] = rows(session, Skill)
    [b_experience] = rows(session, Experience)
    assert skill.id == b_experience.id

    url = f"/profile/experience/{skill.id}"
    assert htmx_send(profile_client, "POST", url, EXPERIENCE).status_code == 404
    assert htmx_send(profile_client, "DELETE", url).status_code == 404
    assert rows(session, Experience)[0].title == "B"
    assert [s.name for s in rows(session, Skill)] == ["SQL"]


def test_deleting_a_row_leaves_rows_with_the_same_id_in_other_sections(profile_client, session):
    htmx_send(profile_client, "POST", "/profile/skills", {"name": "SQL"})
    htmx_send(profile_client, "POST", "/profile/experience", EXPERIENCE)
    [skill] = rows(session, Skill)
    [experience] = rows(session, Experience)
    assert skill.id == experience.id

    htmx_send(profile_client, "DELETE", f"/profile/skills/{skill.id}")
    assert rows(session, Skill) == []
    assert len(rows(session, Experience)) == 1


@pytest.mark.parametrize("key", ["personal", "summary"])
@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_fixed_sections_have_no_rows(profile_client, key, method):
    # "personal" and "summary" are not in SECTIONS, so /{key}/{id} is an unknown section.
    response = htmx_send(profile_client, method, f"/profile/{key}/1", {"name": "X"})
    assert response.status_code == 404


# --- Fields the forms don't know are ignored -------------------------------------


def test_personal_info_cannot_reach_another_users_profile(
    profile_client, session, user, other_profile
):
    data = {
        "full_name": "Jana",
        "headline": "Mine",
        "id": str(other_profile.id),
        "user_id": str(other_profile.user_id),
        "version": "99",
    }
    response = htmx_send(profile_client, "POST", "/profile/personal", data)

    assert response.status_code == 200
    mine = profile_of(session, user.id)
    assert mine.headline == "Mine"
    assert mine.version == 1  # raised by the save, not set from the form
    theirs = profile_of(session, other_profile.user_id)
    assert theirs.headline == "B's headline"
    assert theirs.version == 0


def test_summary_ignores_unknown_fields(profile_client, session, user, other_profile):
    data = {"summary": "Mine", "user_id": str(other_profile.user_id), "version": "99"}
    htmx_send(profile_client, "POST", "/profile/summary", data)

    assert profile_of(session, user.id).summary == "Mine"
    assert profile_of(session, user.id).version == 1
    assert profile_of(session, other_profile.user_id).summary is None


def test_new_row_cannot_be_added_to_another_users_profile(
    profile_client, session, user, other_profile
):
    data = EXPERIENCE | {"profile_id": str(other_profile.id), "id": "999", "sort_order": "-5"}
    htmx_send(profile_client, "POST", "/profile/experience", data)

    mine = profile_of(session, user.id)
    new = session.exec(select(Experience).where(Experience.profile_id == mine.id)).one()
    assert new.id != 999
    assert new.sort_order == 1
    assert len(rows(session, Experience)) == 2  # B's row + the new one, on A's profile


def test_editing_cannot_move_a_row_to_another_users_profile(
    profile_client, session, user, other_profile
):
    htmx_send(profile_client, "POST", "/profile/skills", {"name": "SQL"})
    [skill] = rows(session, Skill)
    data = {"name": "PostgreSQL", "profile_id": str(other_profile.id), "sort_order": "77"}
    htmx_send(profile_client, "POST", f"/profile/skills/{skill.id}", data)

    [skill] = rows(session, Skill)
    assert skill.name == "PostgreSQL"
    assert skill.profile_id == profile_of(session, user.id).id
    assert skill.sort_order == 1


def test_bullets_on_a_section_without_bullets_are_ignored(profile_client, session):
    response = htmx_send(
        profile_client, "POST", "/profile/skills", {"name": "SQL", "bullets": ["a", "b"]}
    )
    assert response.status_code == 200
    assert [s.name for s in rows(session, Skill)] == ["SQL"]


# --- Odd input on edit ------------------------------------------------------------


def test_21_bullets_on_edit_are_rejected_and_the_row_kept(profile_client, session):
    htmx_send(profile_client, "POST", "/profile/experience", EXPERIENCE | {"bullets": ["One"]})
    [experience] = rows(session, Experience)
    data = EXPERIENCE | {"bullets": [f"Bullet {i}" for i in range(21)]}
    response = htmx_send(profile_client, "POST", f"/profile/experience/{experience.id}", data)

    assert response.status_code == 200
    assert "Use at most 20 bullets." in response.text
    assert rows(session, Experience)[0].bullets == ["One"]


def test_too_long_bullet_on_edit_is_rejected(profile_client, session):
    htmx_send(profile_client, "POST", "/profile/experience", EXPERIENCE | {"bullets": ["One"]})
    [experience] = rows(session, Experience)
    data = EXPERIENCE | {"bullets": ["x" * 301]}
    response = htmx_send(profile_client, "POST", f"/profile/experience/{experience.id}", data)

    assert "Each bullet can have at most 300 characters." in response.text
    assert rows(session, Experience)[0].bullets == ["One"]


def test_a_single_bullet_string_is_one_bullet():
    form = ExperienceForm.model_validate(EXPERIENCE | {"bullets": "Launched"})
    assert form.bullets == ["Launched"]


# --- Deleting twice ---------------------------------------------------------------


def test_deleting_twice_gives_404_the_second_time(profile_client, session, user):
    htmx_send(profile_client, "POST", "/profile/skills", {"name": "SQL"})
    [skill] = rows(session, Skill)
    url = f"/profile/skills/{skill.id}"

    assert htmx_send(profile_client, "DELETE", url).status_code == 200
    assert htmx_send(profile_client, "DELETE", url).status_code == 404
    assert profile_of(session, user.id).version == 2  # add + one delete


# --- Normal (non-htmx) requests to fragment routes -------------------------------


def test_plain_post_saves_and_goes_to_the_profile_page(profile_client, session):
    response = plain_send(profile_client, "POST", "/profile/skills", {"name": "SQL"})
    assert response.status_code == 303
    assert response.headers["location"] == "/profile"
    assert [s.name for s in rows(session, Skill)] == ["SQL"]


def test_plain_invalid_post_saves_nothing(profile_client, session):
    response = plain_send(profile_client, "POST", "/profile/skills", {"name": ""})
    assert response.status_code == 303
    assert rows(session, Skill) == []


def test_plain_delete_deletes_and_goes_to_the_profile_page(profile_client, session):
    htmx_send(profile_client, "POST", "/profile/skills", {"name": "SQL"})
    [skill] = rows(session, Skill)
    response = plain_send(profile_client, "DELETE", f"/profile/skills/{skill.id}")
    assert response.status_code == 303
    assert response.headers["location"] == "/profile"
    assert rows(session, Skill) == []


def test_plain_personal_info_post_redirects_to_the_page(profile_client, session, user):
    response = plain_send(profile_client, "POST", "/profile/personal", {"full_name": "Jana N."})
    assert response.status_code == 303
    assert response.headers["location"] == "/profile"
    assert profile_of(session, user.id).version == 1


def test_error_form_shows_at_most_21_bullets(profile_client, session):
    # A request with many bullets is rejected, and only 21 inputs come back
    # (enough to show "Use at most 20 bullets."), not all of them.
    data = EXPERIENCE | {"bullets": [f"B{i}" for i in range(500)]}
    response = htmx_send(profile_client, "POST", "/profile/experience", data)

    assert "Use at most 20 bullets." in response.text
    # 21 sent back + the empty row in <template>
    assert response.text.count('name="bullets"') == 22
