"""Form field limits (#88, D-171): one app-wide limit, and a route's smaller limit
still applies when the CSRF check has already parsed the form."""

from datetime import date

import pytest

from app.models import Experience
from app.schemas.cv import CVContent
from app.security import MAX_ANY_FORM_FIELDS
from app.services import cover_letter_service as letters
from app.services import document_service as docs
from app.services.profile_service import add_item, get_or_create_profile
from tests.helpers import csrf_token
from tests.test_application_documents import fresh

SECTIONS = [
    "summary",
    "experience",
    "education",
    "skills",
    "languages",
    "certifications",
    "projects",
]


@pytest.fixture
def letter(session, user):
    get_or_create_profile(session, user)
    letters.create_letter(session, user, "Master", "en")
    return letters.create_letter(session, user, "For Acme", "en")  # a copy


def padded(form: dict, total: int) -> dict:
    """The form, filled up with extra fields until it has `total` fields."""
    return form | {f"extra{i}": "x" for i in range(total - len(form))}


# --- A route's own, smaller limit ----------------------------------------------------------------


@pytest.mark.parametrize("token_in", ["form", "header"])
def test_a_letter_form_with_too_many_fields_is_refused(auth_client, session, letter, token_in):
    token = csrf_token(auth_client)
    form = {"title": "Changed", "body": "Changed"}
    headers = {"HX-Request": "true"}
    if token_in == "form":
        # A normal form: the CSRF check parses it first (with the app-wide limit).
        form["csrf_token"] = token
    else:
        headers["X-CSRF-Token"] = token

    response = auth_client.post(
        f"/cover-letters/{letter.id}", data=padded(form, 51), headers=headers
    )

    assert response.status_code == 400
    assert fresh(session, letter).title == "For Acme"


def test_a_letter_form_within_the_limit_is_saved(auth_client, session, letter):
    form = {"csrf_token": csrf_token(auth_client), "title": "Changed"}

    response = auth_client.post(f"/cover-letters/{letter.id}", data=padded(form, 50))

    assert response.status_code != 400
    assert fresh(session, letter).title == "Changed"


# --- The app-wide limit -------------------------------------------------------------------------


def test_a_normal_form_over_the_app_wide_limit_is_refused(auth_client, session, user):
    get_or_create_profile(session, user)
    docs.create_cv(session, user, "Master", "en", "classic")
    cv = docs.create_cv(session, user, "CV", "en", "classic")  # a copy: editable
    form = {"csrf_token": csrf_token(auth_client), "title": "Changed"}

    response = auth_client.post(f"/cvs/{cv.id}", data=padded(form, MAX_ANY_FORM_FIELDS + 1))

    assert response.status_code == 400
    assert fresh(session, cv).title == "CV"


def test_the_token_as_a_form_field_still_works(client):
    from tests.helpers import USER_EMAIL

    response = client.post(
        "/login",
        data={"csrf_token": csrf_token(client), "email": USER_EMAIL, "password": "wrong"},
        follow_redirects=False,
    )

    assert response.status_code != 403  # the token was read from the form


def test_the_biggest_cv_form_is_saved(auth_client, session, user):
    # 50 experiences × 20 bullets: the editor sends 1000 bullet checkboxes plus its
    # other fields, more than Starlette's default of 1000 fields.
    profile = get_or_create_profile(session, user)
    for i in range(50):
        add_item(
            session,
            profile,
            Experience,
            {
                "company": f"Company {i}",
                "title": "PM",
                "start_date": date(2000 + i % 25, 1, 1),
                "bullets": [f"Bullet {j}" for j in range(20)],
            },
        )
    docs.create_cv(session, user, "Master", "en", "classic")
    cv = docs.create_cv(session, user, "CV", "en", "classic")  # a copy: editable
    form = {
        "title": "Big",
        "headline": "",
        "summary": "",
        "visible_sections": SECTIONS,
        "bullets": [f"{i}-{j}" for i in range(50) for j in range(20)],
    }
    assert sum(len(v) if isinstance(v, list) else 1 for v in form.values()) > 1000

    response = auth_client.post(
        f"/cvs/{cv.id}",
        data=form,
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(auth_client)},
    )

    assert response.status_code == 200
    saved = CVContent.model_validate(fresh(session, cv).content)
    assert fresh(session, cv).title == "Big"
    assert sum(bullet.selected for job in saved.experience for bullet in job.bullets) == 1000
