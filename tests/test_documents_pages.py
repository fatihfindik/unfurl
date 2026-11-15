"""Documents page and the New CV form (SPEC §6.5, M3a)."""

from datetime import UTC, datetime, timedelta

import pytest
from markupsafe import escape
from sqlmodel import select

from app.models import CVDocument
from app.services import document_service as docs
from app.services.profile_service import get_or_create_profile
from app.templating import time_ago
from tests.test_profile_sections import htmx_send


def new_cv(session, user, language="en", title="CV"):
    get_or_create_profile(session, user)
    return docs.create_cv(session, user, title, language, "classic")


def post_new_cv(client, **values):
    form = {"title": "Master CV (EN)", "language": "en", "template": "classic"} | values
    return htmx_send(client, "POST", "/cvs", form)


# --- The page -----------------------------------------------------------------------


def test_empty_documents_page(auth_client):
    page = auth_client.get("/documents").text

    assert "<h1" in page and "Documents</h1>" in page
    assert "No CVs yet. Create your first master CV from your profile." in page
    assert 'data-dialog-open="new-cv"' in page
    assert "Profile strength" in page
    assert 'href="/profile"' in page and "Complete my profile" in page


def test_masters_and_copies_are_listed(auth_client, session, user):
    master = new_cv(session, user, title="Main CV")
    copy = new_cv(session, user, title="For Acme")
    docs.lock(session, docs.duplicate(session, user, copy))

    page = auth_client.get("/documents").text

    assert "MASTER · EN" in page
    assert f'href="/cvs/{master.id}"' in page
    assert "Classic template · updated today" in page
    assert "Not linked to an application" in page
    assert "For Acme · v1" in page and "For Acme (copy) · v2" in page
    assert "based on Main CV" in page
    assert "Draft" in page and "Sent · locked" in page


def test_only_my_own_cvs_are_listed(auth_client, session, user):
    from app.services.auth_service import create_user

    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    new_cv(session, other, title="Their secret CV")

    assert "Their secret CV" not in auth_client.get("/documents").text


def test_cover_letters_tab_is_coming_soon(auth_client):
    page = auth_client.get("/documents?tab=letters").text

    assert "Coming soon." in page
    assert 'aria-current="page"' in page.split("Cover letters</a>")[0].rsplit("<a", 1)[1]
    assert 'data-dialog-open="new-cv"' not in page


def test_user_text_is_escaped(auth_client, session, user):
    new_cv(session, user, title="<script>alert(1)</script>")

    page = auth_client.get("/documents").text

    assert "<script>alert(1)</script>" not in page
    assert str(escape("<script>alert(1)</script>")) in page


# --- New CV -------------------------------------------------------------------------


def test_new_cv_creates_a_master_and_goes_on(auth_client, session):
    response = post_new_cv(auth_client)

    assert response.status_code == 200
    cv = session.exec(select(CVDocument)).one()
    assert response.headers["HX-Redirect"] == f"/cvs/{cv.id}"  # the editor opens
    assert cv.is_master and cv.title == "Master CV (EN)" and cv.language == "en"


@pytest.mark.parametrize(
    ("values", "field", "message"),
    [
        ({"title": "   "}, "title", "This field is required."),
        ({"title": "x" * 201}, "title", "Use at most 200 characters."),
        ({"language": "fr"}, "language", "Choose a language."),
        ({"template": "fancy"}, "template", "Choose a template."),
    ],
)
def test_new_cv_errors_keep_the_form_open(auth_client, session, values, field, message):
    response = post_new_cv(auth_client, **values)

    assert response.status_code == 200
    assert "HX-Redirect" not in response.headers
    assert 'id="new-cv-form"' in response.text
    assert str(escape(message)) in response.text
    assert session.exec(select(CVDocument)).all() == []


def test_new_cv_says_when_the_limit_is_reached(auth_client, session, user, monkeypatch):
    monkeypatch.setattr(docs, "MAX_CVS_PER_USER", 1)
    new_cv(session, user)

    response = post_new_cv(auth_client)

    assert "You can have up to" in response.text
    assert len(session.exec(select(CVDocument)).all()) == 1


def test_new_cv_needs_a_csrf_token(auth_client, session):
    response = auth_client.post(
        "/cvs", data={"title": "CV", "language": "en", "template": "classic"}
    )

    assert response.status_code == 403
    assert session.exec(select(CVDocument)).all() == []


@pytest.mark.parametrize(("method", "url"), [("GET", "/documents"), ("POST", "/cvs")])
def test_logged_out_visitors_go_to_login(client, method, url):
    from tests.helpers import csrf_token

    response = client.request(
        method, url, headers={"X-CSRF-Token": csrf_token(client)}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


# --- "updated 2 days ago" -----------------------------------------------------------


NOW = datetime(2026, 11, 15, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("days", "text"),
    [
        (0, "today"),
        (1, "yesterday"),
        (3, "3 days ago"),
        (8, "last week"),
        (20, "2 weeks ago"),
        (45, "1 Oct 2026"),
    ],
)
def test_time_ago(days, text):
    assert time_ago(NOW - timedelta(days=days), NOW) == text


def test_time_ago_reads_sqlite_dates_as_utc():
    assert time_ago(datetime(2026, 11, 15, 1), NOW) == "today"  # naive, as SQLite gives it


# --- Delete from the list, layout -----------------------------------------------------


def test_each_draft_row_can_be_deleted_after_a_confirmation(auth_client, session, user):
    master = new_cv(session, user, title="Main CV")
    copy = new_cv(session, user, title="For Acme")
    sent = docs.lock(session, docs.duplicate(session, user, copy))

    page = auth_client.get("/documents").text

    for cv in (master, copy):
        assert f'data-dialog-open="delete-cv-{cv.id}"' in page
        assert f'hx-delete="/cvs/{cv.id}"' in page
    assert f'data-dialog-open="delete-cv-{sent.id}"' not in page  # locked: never deleted


def test_deleting_from_the_list_reloads_it(auth_client, session, user):
    new_cv(session, user)
    copy = new_cv(session, user, title="For Acme")

    response = htmx_send(auth_client, "DELETE", f"/cvs/{copy.id}")

    assert response.headers["HX-Redirect"] == "/documents"


@pytest.mark.parametrize("url", ["/", "/documents", "/profile", "/learning"])
def test_pages_are_left_aligned_with_a_max_width(auth_client, url):
    # Left-aligned, not centred (Fatih, 2026-11-15); the width limit stays.
    page = auth_client.get(url).text

    assert "max-w-[1240px]" in page
    assert "mx-auto w-full" not in page
