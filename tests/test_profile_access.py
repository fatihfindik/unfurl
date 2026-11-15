"""Who may use the profile routes (SPEC §10.1, M2a acceptance criteria).

The general test in test_auth.py only checks GET pages without "{" in the path, so
every profile route is listed here explicitly.
"""

from datetime import date

import pytest
from sqlmodel import select

from app.models import (
    Certification,
    Education,
    Experience,
    LanguageSkill,
    Profile,
    Project,
    Skill,
)
from app.routers.profile import SECTIONS
from app.services.auth_service import create_user
from tests.helpers import csrf_token

# (method, url) for every profile route; "1" stands for any row id.
ROUTES = [
    ("GET", "/profile"),
    ("GET", "/profile/personal"),
    ("GET", "/profile/personal/edit"),
    ("POST", "/profile/personal"),
    ("GET", "/profile/summary"),
    ("GET", "/profile/summary/edit"),
    ("POST", "/profile/summary"),
    ("GET", "/profile/strength"),
]
for key in SECTIONS:
    ROUTES += [
        ("GET", f"/profile/{key}"),
        ("GET", f"/profile/{key}/new"),
        ("POST", f"/profile/{key}"),
        ("GET", f"/profile/{key}/1"),
        ("GET", f"/profile/{key}/1/edit"),
        ("POST", f"/profile/{key}/1"),
        ("DELETE", f"/profile/{key}/1"),
        ("POST", f"/profile/{key}/1/move"),
        ("POST", f"/profile/{key}/order"),
    ]


@pytest.mark.parametrize(("method", "url"), ROUTES)
def test_logged_out_visitors_are_sent_to_login(client, method, url):
    # A CSRF token from the anonymous session, so the request gets past the CSRF check
    # and the login check is what answers.
    headers = {"X-CSRF-Token": csrf_token(client)}
    response = client.request(method, url, headers=headers, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.parametrize(("method", "url"), ROUTES)
def test_logged_out_htmx_requests_get_hx_redirect(client, method, url):
    headers = {"X-CSRF-Token": csrf_token(client), "HX-Request": "true"}
    response = client.request(method, url, headers=headers, follow_redirects=False)
    assert response.headers["HX-Redirect"].startswith("/login")


# --- Another user's rows ------------------------------------------------------------
# B owns one row in every section. A, logged in, tries every route on B's rows:
# each answers 404 (the same as for an id that doesn't exist) and B's data stays.

ROWS = {
    "experience": lambda pid: Experience(
        profile_id=pid, company="B's company", title="B", start_date=date(2020, 1, 1)
    ),
    "education": lambda pid: Education(
        profile_id=pid, institution="B's university", start_date=date(2015, 1, 1)
    ),
    "skills": lambda pid: Skill(profile_id=pid, name="B's skill"),
    "languages": lambda pid: LanguageSkill(profile_id=pid, language="B's language", level="B2"),
    "certifications": lambda pid: Certification(profile_id=pid, name="B's certificate"),
    "projects": lambda pid: Project(profile_id=pid, name="B's project"),
}
CHANGE = {
    "experience": {"title": "Taken", "company": "Taken", "start_date": "2020-01"},
    "education": {"institution": "Taken", "start_date": "2015-01"},
    "skills": {"name": "Taken"},
    "languages": {"language": "Taken", "level": "A1"},
    "certifications": {"name": "Taken"},
    "projects": {"name": "Taken"},
}


@pytest.fixture
def other_users_rows(session) -> dict:
    """B's profile with one row per section. Returns {section key: row id}."""
    other = create_user(session, email="b@example.com", password="b-password-1", full_name="B")
    profile = Profile(user_id=other.id)
    session.add(profile)
    session.commit()
    ids = {}
    for key, make in ROWS.items():
        row = make(profile.id)
        session.add(row)
        session.commit()
        ids[key] = row.id
    return ids


def test_rows_list_matches_the_sections():
    assert set(ROWS) == set(SECTIONS)


@pytest.mark.parametrize("key", list(ROWS))
@pytest.mark.parametrize(
    ("method", "path"), [("GET", ""), ("GET", "/edit"), ("POST", ""), ("DELETE", "")]
)
def test_another_users_rows_are_404(auth_client, session, other_users_rows, key, method, path):
    row_id = other_users_rows[key]
    headers = {"HX-Request": "true", "X-CSRF-Token": csrf_token(auth_client)}
    response = auth_client.request(
        method, f"/profile/{key}/{row_id}{path}", data=CHANGE[key], headers=headers
    )

    assert response.status_code == 404
    session.expire_all()
    row = session.get(SECTIONS[key].model, row_id)
    assert row is not None  # not deleted
    assert "Taken" not in str(row.model_dump())  # not changed
    assert session.exec(select(Profile).where(Profile.id == row.profile_id)).one().version == 0


def test_own_rows_still_work_next_to_another_users(auth_client, session, other_users_rows):
    # The same request on A's own row works, so the 404s above are about the owner.
    headers = {"HX-Request": "true", "X-CSRF-Token": csrf_token(auth_client)}
    auth_client.post("/profile/skills", data={"name": "Mine"}, headers=headers)
    mine = session.exec(select(Skill).where(Skill.name == "Mine")).one()
    response = auth_client.get(f"/profile/skills/{mine.id}", headers=headers)
    assert response.status_code == 200


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_changes_need_a_csrf_token(auth_client, session, method):
    auth_client.get("/profile")
    headers = {"HX-Request": "true"}
    url = "/profile/skills" if method == "POST" else "/profile/skills/1"
    response = auth_client.request(method, url, data={"name": "X"}, headers=headers)
    assert response.status_code == 403
