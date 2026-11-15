"""Profile strength: 10 items, 10% each, first missing item as a hint (SPEC §6.4, D-040)."""

from datetime import date

import pytest

from app.models import Certification, Education, Experience, LanguageSkill, Project, Skill
from app.services.profile_service import add_item, get_or_create_profile, profile_strength
from tests.test_profile import htmx_get, htmx_post
from tests.test_profile_sections import htmx_send

JOB = {"company": "Acme", "title": "Developer", "start_date": date(2020, 1, 1)}


@pytest.fixture
def profile(session, user):
    profile = get_or_create_profile(session, user)
    profile.email = None  # start from a really empty profile (the account email is copied in)
    session.add(profile)
    session.commit()
    return profile


def set_fields(session, profile, **values):
    for name, value in values.items():
        setattr(profile, name, value)
    session.add(profile)
    session.commit()


def add_skills(session, profile, count):
    for n in range(count):
        add_item(session, profile, Skill, {"name": f"Skill {n}"})


# One way to complete each item, in the order of SPEC §6.4.
COMPLETE = {
    "headline": lambda s, p: set_fields(s, p, headline="Developer"),
    "summary": lambda s, p: set_fields(s, p, summary="I build things."),
    "contact": lambda s, p: set_fields(s, p, email="a@example.com", phone="+43 1"),
    "experience": lambda s, p: add_item(s, p, Experience, JOB),
    "bullet": lambda s, p: add_item(s, p, Experience, JOB | {"bullets": ["Shipped it"]}),
    "education": lambda s, p: add_item(
        s, p, Education, {"institution": "TU Wien", "start_date": date(2015, 1, 1)}
    ),
    "skills": lambda s, p: add_skills(s, p, 5),
    "language": lambda s, p: add_item(s, p, LanguageSkill, {"language": "German", "level": "B2"}),
    "certification": lambda s, p: add_item(s, p, Certification, {"name": "AWS"}),
    "project": lambda s, p: add_item(s, p, Project, {"name": "Unfurl"}),
}


def test_empty_profile_is_zero_and_asks_for_a_headline(session, profile):
    strength = profile_strength(session, profile)

    assert strength.percent == 0
    assert strength.hint == "Add a headline to reach 10%."


@pytest.mark.parametrize(
    "item",
    [
        "headline",
        "summary",
        "contact",
        "education",
        "skills",
        "language",
        "certification",
        "project",
    ],
)
def test_each_item_alone_adds_10_percent(session, profile, item):
    COMPLETE[item](session, profile)

    assert profile_strength(session, profile).percent == 10


def test_an_experience_counts_and_a_bullet_counts_once_more(session, profile):
    COMPLETE["experience"](session, profile)
    assert profile_strength(session, profile).percent == 10  # item 4, not item 5

    COMPLETE["bullet"](session, profile)
    assert profile_strength(session, profile).percent == 20  # items 4 and 5


def test_full_profile_is_100_percent_without_a_hint(session, profile):
    for complete in COMPLETE.values():
        complete(session, profile)

    strength = profile_strength(session, profile)
    assert strength.percent == 100
    assert strength.hint is None


def test_hint_names_the_first_missing_item(session, profile):
    for item, complete in COMPLETE.items():
        if item != "certification":
            complete(session, profile)

    assert profile_strength(session, profile).hint == "Add a certification to reach 100%."


@pytest.mark.parametrize(
    ("values", "counts"),
    [
        ({"email": "a@example.com"}, False),  # email alone is not enough
        ({"phone": "+43 1", "location": "Vienna"}, False),  # no email
        ({"email": "a@example.com", "phone": "+43 1"}, True),
        ({"email": "a@example.com", "location": "Vienna"}, True),
    ],
)
def test_contact_needs_email_and_phone_or_location(session, profile, values, counts):
    set_fields(session, profile, **values)

    assert profile_strength(session, profile).percent == (10 if counts else 0)


def test_four_skills_are_not_enough(session, profile):
    add_skills(session, profile, 4)
    assert profile_strength(session, profile).percent == 0

    add_skills(session, profile, 1)
    assert profile_strength(session, profile).percent == 10


def test_optional_fields_do_not_count(session, profile):
    set_fields(session, profile, date_of_birth=date(1995, 5, 1), nationality="Austrian")

    assert profile_strength(session, profile).percent == 0


# --- On the page ------------------------------------------------------------------


def test_profile_page_shows_the_card(auth_client):
    page = auth_client.get("/profile").text

    assert "Profile strength" in page
    assert 'hx-trigger="profile-changed from:body"' in page
    # The account email is copied into the profile, so contact is still missing.
    assert "Add a headline to reach 10%." in page


def test_strength_fragment_reloads_the_card(auth_client):
    response = htmx_get(auth_client, "/profile/strength")

    assert response.status_code == 200
    assert 'id="profile-strength"' in response.text
    assert "<html" not in response.text


@pytest.mark.parametrize(
    ("url", "data"),
    [
        ("/profile/personal", {"full_name": "Jana Novak", "headline": "PM"}),
        ("/profile/summary", {"summary": "Hello"}),
        ("/profile/projects", {"name": "Unfurl"}),
    ],
)
def test_saves_tell_the_page_the_profile_changed(auth_client, url, data):
    response = htmx_post(auth_client, url, data)

    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "profile-changed"


def test_a_form_error_does_not_trigger_a_reload(auth_client):
    response = htmx_post(auth_client, "/profile/projects", {"name": ""})

    assert "HX-Trigger" not in response.headers


def test_edit_and_delete_tell_the_page_the_profile_changed(auth_client, session, user):
    project = add_item(session, get_or_create_profile(session, user), Project, {"name": "A"})
    url = f"/profile/projects/{project.id}"

    assert htmx_post(auth_client, url, {"name": "B"}).headers["HX-Trigger"] == "profile-changed"
    assert htmx_send(auth_client, "DELETE", url).headers["HX-Trigger"] == "profile-changed"
