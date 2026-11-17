"""profile_service: one profile per user, owner checks, version, limits (D-032)."""

from datetime import date

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app.models import Experience, Profile, Skill, User
from app.schemas.profile import PersonalInfoForm, SummaryForm
from app.services import profile_service
from app.services.auth_service import create_user
from app.services.profile_service import (
    MAX_ITEMS_PER_SECTION,
    TooManyItems,
    add_item,
    delete_item,
    get_or_create_profile,
    get_owned_item,
    list_items,
    update_item,
)


@pytest.fixture
def profile(session, user) -> Profile:
    return get_or_create_profile(session, user)


@pytest.fixture
def other_user(session) -> User:
    return create_user(session, email="other@example.com", password="x" * 12, full_name="Other")


def version(session, profile) -> int:
    session.refresh(profile)
    return profile.version


def test_profile_is_created_once_with_the_account_email(session, user):
    first = get_or_create_profile(session, user)
    second = get_or_create_profile(session, user)

    assert first.id == second.id
    assert first.email == user.email
    assert len(session.exec(select(Profile)).all()) == 1


def test_items_are_added_at_the_end(session, profile):
    a = add_item(session, profile, Skill, {"name": "SQL"})
    b = add_item(session, profile, Skill, {"name": "Figma"})

    assert b.sort_order > a.sort_order
    assert [s.name for s in list_items(session, profile, Skill)] == ["SQL", "Figma"]


def test_every_change_raises_the_version(session, user, profile):
    assert version(session, profile) == 0
    item = add_item(session, profile, Skill, {"name": "SQL"})
    assert version(session, profile) == 1
    update_item(session, item, {"name": "PostgreSQL"})
    assert version(session, profile) == 2
    delete_item(session, item, user, "skills")
    assert version(session, profile) == 3
    profile_service.update_summary(session, profile, SummaryForm(summary="Hi"))
    assert version(session, profile) == 4
    profile_service.update_personal_info(
        session, user, profile, PersonalInfoForm(full_name="Jana N.", headline="PM")
    )
    assert version(session, profile) == 5


def test_personal_info_changes_the_account_name(session, user, profile):
    form = PersonalInfoForm(full_name="Jana Nováková", headline="Senior PM")
    profile_service.update_personal_info(session, user, profile, form)

    assert session.get(User, user.id).full_name == "Jana Nováková"
    assert profile.headline == "Senior PM"


def test_bullets_can_be_changed(session, profile):
    item = add_item(
        session,
        profile,
        Experience,
        {"company": "A", "title": "B", "start_date": date(2020, 1, 1), "bullets": ["one"]},
    )
    update_item(session, item, {"bullets": ["one", "two"]})
    session.expire_all()
    assert session.get(Experience, item.id).bullets == ["one", "two"]


def test_a_section_holds_at_most_50_items(session, profile):
    for i in range(MAX_ITEMS_PER_SECTION):
        add_item(session, profile, Skill, {"name": f"Skill {i}"})
    with pytest.raises(TooManyItems):
        add_item(session, profile, Skill, {"name": "One too many"})


def test_owner_gets_their_item(session, user, profile):
    item = add_item(session, profile, Skill, {"name": "SQL"})
    assert get_owned_item(session, user, Skill, str(item.id)).id == item.id


@pytest.mark.parametrize("item_id", ["abc", "", None, "0", "-1", "1.5", str(2**31), "9" * 30])
def test_ids_that_cant_exist_are_404(session, user, item_id):
    with pytest.raises(HTTPException) as exc:
        get_owned_item(session, user, Skill, item_id)
    assert exc.value.status_code == 404


def test_another_users_item_is_404(session, profile, other_user):
    item = add_item(session, profile, Skill, {"name": "SQL"})
    with pytest.raises(HTTPException) as exc:
        get_owned_item(session, other_user, Skill, item.id)
    assert exc.value.status_code == 404


def test_item_of_the_wrong_section_is_404(session, user, profile):
    # A Skill id used as an Experience id finds nothing (or someone else's row).
    item = add_item(session, profile, Skill, {"name": "SQL"})
    with pytest.raises(HTTPException):
        get_owned_item(session, user, Experience, item.id)
