"""M2b error cases not covered elsewhere: CSRF on every changing route, bad ids on
move / order, odd photo uploads, and one user's photo delete never touching another's."""

import pytest
from markupsafe import escape
from sqlmodel import select

from app.models import Profile, Project
from app.services import photo_service
from app.services.auth_service import create_user
from tests.helpers import csrf_token, log_in
from tests.test_photo import HTMX, image_bytes, saved_profile, stored_files, upload
from tests.test_profile_order import SORTABLE, make_rows, order, send_order
from tests.test_profile_sections import htmx_send

PROJECT = SORTABLE["projects"][1]


# --- CSRF ---------------------------------------------------------------------------


def test_removing_the_photo_needs_a_csrf_token(auth_client, session, upload_dir):
    upload(auth_client, image_bytes())

    response = auth_client.delete("/profile/photo", headers=HTMX)

    assert response.status_code == 403
    assert len(stored_files(upload_dir)) == 1
    assert saved_profile(session).photo_path is not None


def test_drag_and_drop_needs_a_csrf_token(auth_client, session, user):
    a, b, c = make_rows(session, user, Project, PROJECT)

    response = auth_client.post(
        "/profile/projects/order",
        data={"ids": [str(c.id), str(a.id), str(b.id)]},
        headers=HTMX,
    )

    assert response.status_code == 403
    assert order(session, user, Project) == [a.id, b.id, c.id]


def test_a_wrong_csrf_token_is_refused(auth_client, session, user, upload_dir):
    # A token that is present but not the session's one: same 403 as a missing token.
    a, b, c = make_rows(session, user, Project, PROJECT)
    upload(auth_client, image_bytes())
    headers = HTMX | {"X-CSRF-Token": "not-the-real-token"}

    requests = [
        (
            "POST",
            "/profile/photo",
            {"files": {"photo": ("x.png", image_bytes("PNG"), "image/png")}},
        ),
        ("DELETE", "/profile/photo", {}),
        ("POST", "/profile/projects/order", {"data": {"ids": [str(c.id), str(a.id), str(b.id)]}}),
        ("POST", f"/profile/projects/{c.id}/move", {"data": {"direction": "up"}}),
    ]
    for method, url, body in requests:
        response = auth_client.request(method, url, headers=headers, **body)
        assert response.status_code == 403, (method, url)

    assert order(session, user, Project) == [a.id, b.id, c.id]
    files = stored_files(upload_dir)
    assert len(files) == 1 and files[0].suffix == ".jpg"  # the first photo is still there


# --- Move / order: ids and sections that don't exist --------------------------------


@pytest.mark.parametrize("item_id", ["abc", "0", "999", str(2**40)])
def test_moving_an_id_that_does_not_exist_is_404(auth_client, session, user, item_id):
    rows = make_rows(session, user, Project, PROJECT)
    before = order(session, user, Project)

    response = htmx_send(
        auth_client, "POST", f"/profile/projects/{item_id}/move", {"direction": "up"}
    )

    assert response.status_code == 404
    assert order(session, user, Project) == before == [row.id for row in rows]


@pytest.mark.parametrize("url", ["/profile/hobbies/order", "/profile/hobbies/1/move"])
def test_unknown_section_cannot_be_reordered(auth_client, url):
    assert htmx_send(auth_client, "POST", url, {"direction": "up", "ids": ["1"]}).status_code == 404


def test_only_another_users_ids_in_the_order_is_404(auth_client, session, user):
    # A has no rows: B's full list must not be accepted as "A's whole section".
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = make_rows(session, other, Project, PROJECT)
    before = order(session, other, Project)

    response = send_order(auth_client, "projects", [theirs[2].id, theirs[0].id, theirs[1].id])

    assert response.status_code == 404
    assert order(session, other, Project) == before


# --- Photo: odd uploads -------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"files": {"picture": ("me.jpg", image_bytes(), "image/jpeg")}},  # wrong field name
        {"data": {"photo": "not a file"}},  # a text field, not a file
    ],
)
def test_an_upload_without_a_photo_file_is_refused(auth_client, session, upload_dir, body):
    headers = HTMX | {"X-CSRF-Token": csrf_token(auth_client)}
    if "data" in body:
        # Force multipart, so the request looks like it came from the upload form.
        body = {"data": body["data"], "files": {"other": ("x.txt", b"x", "text/plain")}}

    response = auth_client.post("/profile/photo", headers=headers, **body)

    assert response.status_code == 200
    assert escape(photo_service.UNREADABLE) in response.text
    assert "HX-Trigger" not in response.headers
    assert stored_files(upload_dir) == []
    assert saved_profile(session).photo_path is None


def test_an_upload_with_two_files_is_refused(auth_client, session, upload_dir):
    # The form reader allows one file (max_files=1). Two files must not crash the app
    # (500) or save anything.
    headers = HTMX | {"X-CSRF-Token": csrf_token(auth_client)}
    files = [
        ("photo", ("a.jpg", image_bytes(), "image/jpeg")),
        ("photo", ("b.png", image_bytes("PNG"), "image/png")),
    ]

    response = auth_client.post("/profile/photo", headers=headers, files=files)

    assert response.status_code == 400  # refused while reading the form
    assert stored_files(upload_dir) == []
    assert session.exec(select(Profile).where(Profile.photo_path.is_not(None))).first() is None


# --- Photo: another user ------------------------------------------------------------


def test_removing_my_photo_keeps_another_users_photo(client, session, upload_dir):
    create_user(session, email="a@example.com", password="a" * 12, full_name="A")
    create_user(session, email="b@example.com", password="b" * 12, full_name="B")

    log_in(client, "a@example.com", "a" * 12)
    upload(client, image_bytes())
    a_file = stored_files(upload_dir)[0]
    client.post("/logout", data={"csrf_token": csrf_token(client)})

    log_in(client, "b@example.com", "b" * 12)
    upload(client, image_bytes("PNG"), "b.png", "image/png")
    response = client.delete("/profile/photo", headers=HTMX | {"X-CSRF-Token": csrf_token(client)})

    assert response.status_code == 200
    assert stored_files(upload_dir) == [a_file]  # only B's file is gone
