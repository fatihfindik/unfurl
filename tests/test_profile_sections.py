"""Profile list sections: add, edit, delete, errors (SPEC §6.4, M2a, D-031)."""

import pytest
from sqlmodel import select

from app.models import Certification, LanguageSkill, Profile, Project, Skill
from app.services.profile_service import MAX_ITEMS_PER_SECTION
from tests.helpers import csrf_token

HTMX = {"HX-Request": "true"}

# For each section: the table, a valid new row, a change, and text the page shows.
SECTIONS = {
    "skills": (Skill, {"name": "SQL", "level": "4"}, {"name": "PostgreSQL"}, "PostgreSQL"),
    "languages": (
        LanguageSkill,
        {"language": "German", "level": "C1"},
        {"language": "German", "level": "native"},
        "Native",
    ),
    "certifications": (
        Certification,
        {"name": "CSPO", "issuer": "Scrum Alliance", "date": "2022-06"},
        {"name": "PSPO I", "date": "2023-01"},
        "01/2023",
    ),
    "projects": (
        Project,
        {"name": "Unfurl", "url": "https://github.com/x/unfurl", "description": "Job search app"},
        {"name": "Unfurl 2", "description": "Changed"},
        "Unfurl 2",
    ),
}
# A required field left empty, per section.
REQUIRED_FIELD = {
    "skills": "name",
    "languages": "language",
    "certifications": "name",
    "projects": "name",
}


def htmx_get(client, url):
    return client.get(url, headers=HTMX, follow_redirects=False)


def htmx_send(client, method, url, data=None):
    headers = HTMX | {"X-CSRF-Token": csrf_token(client)}
    return client.request(method, url, data=data, headers=headers, follow_redirects=False)


def rows(session, model):
    session.expire_all()
    return session.exec(select(model)).all()


def version(session) -> int:
    session.expire_all()
    return session.exec(select(Profile)).one().version


@pytest.fixture
def profile_client(auth_client):
    auth_client.get("/profile")  # creates the profile
    return auth_client


def test_page_shows_every_section_with_its_empty_text(profile_client):
    html = profile_client.get("/profile").text
    for heading in ("Skills", "Languages", "Certifications", "Projects"):
        assert f">{heading}</h2>" in html
    assert "No certifications yet." in html


@pytest.mark.parametrize("key", SECTIONS)
def test_add_edit_delete(profile_client, session, key):
    model, new, change, shown = SECTIONS[key]

    # Add: the form opens inside the section, saving returns the section with the row.
    form = htmx_get(profile_client, f"/profile/{key}/new")
    assert form.status_code == 200
    assert f'hx-post="/profile/{key}"' in form.text
    response = htmx_send(profile_client, "POST", f"/profile/{key}", new)
    assert response.status_code == 200
    assert f'id="profile-{key}"' in response.text
    assert f'hx-post="/profile/{key}"' not in response.text  # the form closed
    [item] = rows(session, model)
    assert version(session) == 1

    # Edit: the form replaces the row, saving returns the row.
    edit = htmx_get(profile_client, f"/profile/{key}/{item.id}/edit")
    assert f'hx-post="/profile/{key}/{item.id}"' in edit.text
    response = htmx_send(profile_client, "POST", f"/profile/{key}/{item.id}", change)
    assert response.status_code == 200
    assert shown in response.text
    assert version(session) == 2

    # Cancel shows the row again.
    assert shown in htmx_get(profile_client, f"/profile/{key}/{item.id}").text

    # Delete: the section comes back without the row.
    response = htmx_send(profile_client, "DELETE", f"/profile/{key}/{item.id}")
    assert response.status_code == 200
    assert f'id="profile-{key}"' in response.text
    assert rows(session, model) == []
    assert version(session) == 3


@pytest.mark.parametrize("key", SECTIONS)
def test_missing_required_field_keeps_the_add_form_open(profile_client, session, key):
    model, new, _, _ = SECTIONS[key]
    data = new | {REQUIRED_FIELD[key]: "  "}
    response = htmx_send(profile_client, "POST", f"/profile/{key}", data)

    assert response.status_code == 200
    assert "This field is required." in response.text
    assert f'hx-post="/profile/{key}"' in response.text  # still open
    assert rows(session, model) == []
    assert version(session) == 0


@pytest.mark.parametrize(
    ("key", "data", "message"),
    [
        ("skills", {"name": "SQL", "level": "6"}, "Choose a level from 1 to 5."),
        ("languages", {"language": "German", "level": "D1"}, "Choose a level from the list."),
        ("certifications", {"name": "CSPO", "date": "June 2022"}, "Use the format YYYY-MM."),
        (
            "projects",
            {"name": "X", "url": "javascript:alert(1)"},
            "Enter a full link starting with https://.",
        ),
        ("projects", {"name": "x" * 201}, "Use at most 200 characters."),
    ],
)
def test_invalid_input_shows_a_message(profile_client, session, key, data, message):
    response = htmx_send(profile_client, "POST", f"/profile/{key}", data)
    assert message in response.text
    assert rows(session, SECTIONS[key][0]) == []


def test_invalid_edit_keeps_the_row_unchanged(profile_client, session):
    htmx_send(profile_client, "POST", "/profile/skills", {"name": "SQL"})
    [skill] = rows(session, Skill)
    response = htmx_send(profile_client, "POST", f"/profile/skills/{skill.id}", {"name": ""})

    assert "This field is required." in response.text
    assert rows(session, Skill)[0].name == "SQL"


def test_a_section_takes_at_most_50_entries(profile_client, session):
    profile = session.exec(select(Profile)).one()
    for i in range(MAX_ITEMS_PER_SECTION):
        session.add(Skill(profile_id=profile.id, name=f"Skill {i}", sort_order=i))
    session.commit()

    response = htmx_send(profile_client, "POST", "/profile/skills", {"name": "One more"})
    assert "You can add up to 50 entries here." in response.text
    assert len(rows(session, Skill)) == MAX_ITEMS_PER_SECTION


def test_user_text_is_escaped(profile_client):
    response = htmx_send(profile_client, "POST", "/profile/projects", {"name": "<b>x</b>"})
    assert "<b>x</b>" not in response.text
    assert "&lt;b&gt;x&lt;/b&gt;" in response.text


def test_new_rows_are_added_at_the_end(profile_client, session):
    for name in ("SQL", "Figma", "Jira"):
        htmx_send(profile_client, "POST", "/profile/skills", {"name": name})
    html = profile_client.get("/profile").text
    # Each chip's delete button is labelled with the skill's name.
    assert html.index('"Delete SQL"') < html.index('"Delete Figma"') < html.index('"Delete Jira"')


# --- Paths ------------------------------------------------------------------------


def test_new_is_not_read_as_an_id(profile_client):
    # /new is registered before /{item_id}.
    response = htmx_get(profile_client, "/profile/skills/new")
    assert response.status_code == 200
    assert 'hx-post="/profile/skills"' in response.text


@pytest.mark.parametrize("item_id", ["abc", "0", "999", str(2**40)])
def test_ids_that_dont_exist_are_404(profile_client, item_id):
    assert htmx_get(profile_client, f"/profile/skills/{item_id}").status_code == 404
    assert htmx_send(profile_client, "DELETE", f"/profile/skills/{item_id}").status_code == 404


@pytest.mark.parametrize("url", ["/profile/hobbies", "/profile/hobbies/new", "/profile/hobbies/1"])
def test_unknown_section_is_404(profile_client, url):
    assert htmx_get(profile_client, url).status_code == 404


def test_section_fragments_need_htmx(profile_client):
    response = profile_client.get("/profile/skills/new", follow_redirects=False)
    assert response.headers["location"] == "/profile"
