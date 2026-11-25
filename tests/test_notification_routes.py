"""The bell: badge, panel, opening a notification, mark all as read (D-105).

M11a acceptance: a click marks the notification read and opens the application;
"Mark all as read" clears the count."""

import datetime as dt
import json

import pytest
from sqlmodel import select

from app.models import Notification
from app.services import notification_service as notes
from app.services.auth_service import create_user
from tests.helpers import csrf_token


def add(session, user, number=1, read=False, link="/applications/7", **fields) -> Notification:
    note = Notification(
        user_id=user.id,
        type=fields.pop("type", "follow_up"),
        title=fields.pop("title", f"Time to follow up {number}"),
        body=fields.pop("body", f"Acme {number} · no reply for 10 days"),
        link_url=link,
        dedupe_key=f"key{number}",
        read_at=dt.datetime(2026, 11, 1, tzinfo=dt.UTC) if read else None,
        **fields,
    )
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


def post(client, url, htmx=True):
    headers = {"X-CSRF-Token": csrf_token(client)}
    if htmx:
        headers["HX-Request"] = "true"
    return client.post(url, headers=headers, follow_redirects=False)


@pytest.fixture
def other(session):
    return create_user(session, email="other@example.test", password="x" * 12, full_name="O")


# --- The bell on every page ----------------------------------------------------------------


def test_the_bell_loads_its_badge_and_panel(auth_client):
    html = auth_client.get("/").text

    assert 'hx-get="/notifications/badge" hx-trigger="load, every 120s"' in html
    assert 'hx-get="/notifications/panel"' in html
    assert 'id="notification-panel"' in html


def test_the_badge_swaps_only_itself(auth_client):
    """The badge sits inside the bell button, whose hx-target is the panel body.
    htmx children inherit hx-target, so without hx-target="this" the badge replaced
    the panel body on load and the bell opened an empty panel."""
    page = auth_client.get("/").text
    badge = auth_client.get("/notifications/badge").text

    assert 'hx-trigger="load, every 120s" hx-target="this" hx-swap="outerHTML"' in page
    assert 'hx-target="this" hx-swap="outerHTML"' in badge


def test_no_bell_without_a_login(client):
    html = client.get("/login").text

    assert "/notifications/badge" not in html


# --- Badge ---------------------------------------------------------------------------------


def test_badge_is_empty_without_unread(auth_client, session, user):
    add(session, user, read=True)

    html = auth_client.get("/notifications/badge").text

    assert 'id="notification-badge"' in html
    assert "unread" not in html
    assert 'hx-trigger="every 120s, notifications-changed from:body"' in html


def test_badge_counts_unread(auth_client, session, user, other):
    for number in range(3):
        add(session, user, number)
    add(session, user, 9, read=True)
    add(session, other, 1)

    html = auth_client.get("/notifications/badge").text

    assert ">3</span>" in html and ", 3 unread" in html


def test_badge_over_99(auth_client, session, user):
    for number in range(120):
        session.add(
            Notification(
                user_id=user.id, type="follow_up", title="T", body="B",
                link_url="/", dedupe_key=f"k{number}",
            )
        )  # fmt: skip
    session.commit()

    html = auth_client.get("/notifications/badge").text

    assert ">99+</span>" in html and ", 120 unread" in html


# --- Panel ---------------------------------------------------------------------------------


def test_panel_lists_newest_first(auth_client, session, user, other):
    first = add(session, user, 1, created_at=dt.datetime(2026, 11, 10, tzinfo=dt.UTC))
    second = add(session, user, 2, read=True, created_at=dt.datetime(2026, 11, 12, tzinfo=dt.UTC))
    add(session, other, 3, title="Their reminder")

    html = auth_client.get("/notifications/panel").text

    assert html.index(f'data-notification="{second.id}"') < html.index(
        f'data-notification="{first.id}"'
    )
    assert "Their reminder" not in html
    assert f'action="/notifications/{first.id}/open"' in html
    assert 'name="csrf_token"' in html
    assert "Mark all as read" in html
    assert html.count("Unread</span>") == 1


def test_panel_shows_at_most_20(auth_client, session, user):
    for number in range(25):
        add(session, user, number)

    html = auth_client.get("/notifications/panel").text

    assert html.count("data-notification=") == 20


def test_empty_panel(auth_client):
    html = auth_client.get("/notifications/panel").text

    assert "No notifications yet." in html
    assert "Mark all as read" not in html


def test_panel_escapes_user_text(auth_client, session, user):
    add(session, user, body="<b>Evil Co</b> · no reply")

    html = auth_client.get("/notifications/panel").text

    assert "<b>Evil Co</b>" not in html and "&lt;b&gt;Evil Co&lt;/b&gt;" in html


# --- Open (acceptance) ---------------------------------------------------------------------


def test_open_marks_read_and_goes_to_the_application(auth_client, session, user):
    note = add(session, user, link="/applications/7")

    response = post(auth_client, f"/notifications/{note.id}/open", htmx=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/applications/7"
    session.refresh(note)
    assert note.read_at is not None
    assert notes.unread_count(session, user) == 0


@pytest.mark.parametrize(
    "link", ["https://evil.example/x", "//evil.example", "/\\evil.example", "javascript:alert(1)"]
)
def test_open_never_leaves_the_site(auth_client, session, user, link):
    note = add(session, user, link=link)

    response = post(auth_client, f"/notifications/{note.id}/open", htmx=False)

    assert response.headers["location"] == "/"


def test_open_another_users_notification_is_404(auth_client, session, other):
    theirs = add(session, other)

    response = post(auth_client, f"/notifications/{theirs.id}/open", htmx=False)

    assert response.status_code == 404
    session.refresh(theirs)
    assert theirs.read_at is None


@pytest.mark.parametrize("note_id", ["abc", "0", "-1", "99999", str(2**40)])
def test_open_unknown_ids_are_404(auth_client, note_id):
    assert post(auth_client, f"/notifications/{note_id}/open").status_code == 404


def test_open_needs_the_csrf_token(auth_client, session, user):
    note = add(session, user)

    response = auth_client.post(f"/notifications/{note.id}/open", follow_redirects=False)

    assert response.status_code == 403
    session.refresh(note)
    assert note.read_at is None


# --- Mark all as read (acceptance) ---------------------------------------------------------


def test_read_all_clears_the_count(auth_client, session, user, other):
    for number in range(3):
        add(session, user, number)
    add(session, other, 9)

    response = post(auth_client, "/notifications/read-all")

    assert response.status_code == 200
    assert json.loads(response.headers["HX-Trigger"]) == {"notifications-changed": True}
    assert "Unread</span>" not in response.text
    assert notes.unread_count(session, user) == 0
    assert notes.unread_count(session, other) == 1
    assert "unread" not in auth_client.get("/notifications/badge").text


def test_read_all_needs_the_csrf_token(auth_client, session, user):
    add(session, user)

    assert auth_client.post("/notifications/read-all").status_code == 403
    assert notes.unread_count(session, user) == 1


def test_read_all_is_rate_limited(auth_client):
    codes = [post(auth_client, "/notifications/read-all").status_code for _ in range(61)]

    assert codes[:60] == [200] * 60 and codes[60] == 429


# --- Logged out ----------------------------------------------------------------------------


def test_logged_out_panel_goes_to_login(client):
    response = client.get("/notifications/panel", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.parametrize("htmx", [True, False])
def test_logged_out_badge_is_empty_not_a_redirect(client, htmx):
    # The badge is polled in the background: it must not send the page to /login
    # while someone types in a form (M11a review L2).
    headers = {"HX-Request": "true"} if htmx else {}

    response = client.get("/notifications/badge", headers=headers, follow_redirects=False)

    assert response.status_code == 204
    assert "HX-Redirect" not in response.headers


def test_logged_out_posts_change_nothing(client, session, user):
    note = add(session, user)

    for url in (f"/notifications/{note.id}/open", "/notifications/read-all"):
        response = client.post(
            url, headers={"X-CSRF-Token": csrf_token(client)}, follow_redirects=False
        )
        assert response.status_code == 303

    assert session.exec(select(Notification)).one().read_at is None
