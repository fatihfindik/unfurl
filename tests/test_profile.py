"""My profile page: personal info and summary (SPEC §6.4, M2a)."""

import pytest
from sqlmodel import select

from app.models import Profile, User
from tests.helpers import USER_EMAIL, csrf_token

HTMX = {"HX-Request": "true"}


def htmx_get(client, url):
    return client.get(url, headers=HTMX, follow_redirects=False)


def htmx_post(client, url, data):
    headers = HTMX | {"X-CSRF-Token": csrf_token(client)}
    return client.post(url, data=data, headers=headers, follow_redirects=False)


def saved_profile(session) -> Profile:
    session.expire_all()
    return session.exec(select(Profile)).one()


# --- The page ---------------------------------------------------------------------


def test_first_visit_creates_the_profile_with_the_account_email(auth_client, session):
    response = auth_client.get("/profile")

    assert response.status_code == 200
    assert "My profile" in response.text
    assert "← Documents" in response.text
    for heading in ("Personal info", "Summary"):
        assert f">{heading}</h2>" in response.text
    assert saved_profile(session).email == USER_EMAIL
    assert USER_EMAIL in response.text


def test_second_visit_reuses_the_profile(auth_client, session):
    auth_client.get("/profile")
    auth_client.get("/profile")
    assert len(session.exec(select(Profile)).all()) == 1


def test_account_menu_links_to_my_profile(auth_client):
    assert '<a href="/profile"' in auth_client.get("/").text


@pytest.mark.parametrize(
    "url",
    ["/profile/personal", "/profile/personal/edit", "/profile/summary", "/profile/summary/edit"],
)
def test_fragments_need_htmx_otherwise_the_page_opens(auth_client, url):
    response = auth_client.get(url, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/profile"


# --- Personal info ----------------------------------------------------------------


def test_edit_shows_the_form_with_saved_values(auth_client):
    response = htmx_get(auth_client, "/profile/personal/edit")

    assert response.status_code == 200
    assert "<html" not in response.text  # only the section
    assert 'hx-post="/profile/personal"' in response.text
    assert 'value="Jana Novak"' in response.text
    assert f'value="{USER_EMAIL}"' in response.text


def test_saving_personal_info(auth_client, session, user):
    response = htmx_post(
        auth_client,
        "/profile/personal",
        {
            "full_name": "  Jana Nováková ",
            "headline": "Senior Product Manager",
            "email": "jana@work.example",
            "phone": "+43 660 1234567",
            "location": "Vienna",
            "linkedin_url": "https://www.linkedin.com/in/jana",
            "date_of_birth": "1990-05-17",
            "nationality": "Czech",
        },
    )

    assert response.status_code == 200
    assert "Senior Product Manager" in response.text
    assert "hx-post" not in response.text  # back to the read view
    profile = saved_profile(session)
    assert profile.headline == "Senior Product Manager"
    assert profile.email == "jana@work.example"
    assert str(profile.date_of_birth) == "1990-05-17"
    assert profile.version == 1
    assert session.get(User, user.id).full_name == "Jana Nováková"


def test_invalid_personal_info_shows_messages_and_saves_nothing(auth_client, session):
    auth_client.get("/profile")
    response = htmx_post(
        auth_client,
        "/profile/personal",
        {"full_name": "", "headline": "Kept in the form", "website": "javascript:alert(1)"},
    )

    # 200, not 400: htmx only puts 2xx responses into the page.
    assert response.status_code == 200
    assert "This field is required." in response.text
    assert "Enter a full link starting with https://." in response.text
    assert 'value="Kept in the form"' in response.text  # what the user typed stays
    profile = saved_profile(session)
    assert profile.headline is None
    assert profile.version == 0


def test_cancel_shows_the_read_view(auth_client):
    response = htmx_get(auth_client, "/profile/personal")
    assert response.status_code == 200
    assert 'hx-get="/profile/personal/edit"' in response.text


def test_saving_needs_a_csrf_token(auth_client, session):
    auth_client.get("/profile")
    response = auth_client.post(
        "/profile/personal", data={"full_name": "X"}, headers=HTMX, follow_redirects=False
    )
    assert response.status_code == 403
    assert saved_profile(session).version == 0


# --- Summary ----------------------------------------------------------------------


def test_saving_the_summary(auth_client, session):
    response = htmx_post(auth_client, "/profile/summary", {"summary": "Line one\nLine two"})

    assert response.status_code == 200
    assert "Line one\nLine two" in response.text
    assert saved_profile(session).summary == "Line one\nLine two"


def test_summary_is_escaped(auth_client):
    response = htmx_post(auth_client, "/profile/summary", {"summary": "<script>alert(1)</script>"})
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "&lt;script&gt;" in auth_client.get("/profile").text


def test_too_long_summary_is_rejected(auth_client, session):
    response = htmx_post(auth_client, "/profile/summary", {"summary": "x" * 5001})
    assert "Use at most 5000 characters." in response.text
    assert saved_profile(session).summary is None


def test_empty_summary_clears_it(auth_client, session):
    htmx_post(auth_client, "/profile/summary", {"summary": "Something"})
    htmx_post(auth_client, "/profile/summary", {"summary": "   "})
    assert saved_profile(session).summary is None
