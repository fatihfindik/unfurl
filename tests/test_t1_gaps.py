"""Gaps in the T1 tech-debt tests: odd unlock values and ids (#89, D-169), form field
limits on the photo upload (#88, D-171) and odd Host headers (#85, D-168)."""

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape

from app.main import app
from app.services import cover_letter_service as letters
from app.services import document_service as docs
from app.services import photo_service
from app.services.profile_service import get_or_create_profile
from tests.helpers import csrf_token, log_in, unlock_master
from tests.test_application_documents import fresh
from tests.test_ext_api import ORIGIN, api_login
from tests.test_photo import HTMX, image_bytes, saved_profile, stored_files
from tests.test_profile_sections import htmx_send

READ_ONLY = '<fieldset class="m-0 flex flex-col gap-4 border-0 p-0" disabled>'
KINDS = [("cv", "/cvs"), ("letter", "/cover-letters")]


@pytest.fixture
def masters(session, user):
    """A CV master and a letter master, by kind."""
    get_or_create_profile(session, user)
    return {
        "cv": docs.create_cv(session, user, "Master CV", "en", "classic"),
        "letter": letters.create_letter(session, user, "Master letter", "en"),
    }


def post(client, path, **headers):
    return client.post(
        path, headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)} | headers
    )


# --- Odd unlock values ----------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "base"), KINDS)
@pytest.mark.parametrize(
    "sent",
    [
        "x" * 10_000,  # very long
        "<script>alert(1)</script>",
        "' OR 1=1 --",
        "%00",
        " ",
        "confirmed",  # the old fixed value (before D-169)
    ],
)
def test_an_odd_unlock_value_opens_the_master_read_only(
    client, auth_client, masters, kind, base, sent
):
    master = masters[kind]
    unlock_master(auth_client, kind, master.id)  # the session has a real value

    response = auth_client.get(f"{base}/{master.id}?unlock={quote(sent)}")

    assert response.status_code == 200
    assert 'name="unlock"' not in response.text
    assert "<script>alert(1)</script>" not in response.text


@pytest.mark.parametrize(("kind", "base"), KINDS)
def test_a_non_ascii_unlock_value_opens_the_master_read_only(auth_client, masters, kind, base):
    master = masters[kind]
    unlock_master(auth_client, kind, master.id)

    response = auth_client.get(f"{base}/{master.id}?unlock={quote('äöü')}")

    assert response.status_code == 200
    assert 'name="unlock"' not in response.text


@pytest.mark.parametrize(("kind", "base"), KINDS)
def test_a_non_ascii_unlock_value_in_a_save_is_refused(auth_client, session, masters, kind, base):
    master = masters[kind]
    unlock_master(auth_client, kind, master.id)

    response = htmx_send(auth_client, "POST", f"{base}/{master.id}", {"title": "X", "unlock": "ä"})

    assert response.status_code == 409
    assert fresh(session, master).title == master.title


@pytest.mark.parametrize(("kind", "base"), KINDS)
def test_a_non_ascii_unlock_value_without_a_session_value_is_read_only(
    auth_client, masters, kind, base
):
    # Nothing in the session yet: is_unlocked returns before compare_digest.
    response = auth_client.get(f"{base}/{masters[kind].id}?unlock={quote('äöü')}")

    assert response.status_code == 200
    assert 'name="unlock"' not in response.text


@pytest.mark.parametrize(("kind", "base"), KINDS)
def test_repeated_wrong_unlock_params_do_not_unlock(auth_client, masters, kind, base):
    master = masters[kind]
    unlock_master(auth_client, kind, master.id)

    page = auth_client.get(f"{base}/{master.id}?unlock=a&unlock=b&unlock=confirmed").text

    assert READ_ONLY in page


@pytest.mark.parametrize(("kind", "base"), KINDS)
def test_a_save_with_wrong_repeated_unlock_fields_is_refused(
    auth_client, session, masters, kind, base
):
    master = masters[kind]
    unlock_master(auth_client, kind, master.id)

    response = htmx_send(
        auth_client, "POST", f"{base}/{master.id}", {"title": "X", "unlock": ["a", "b"]}
    )

    assert response.status_code == 409
    assert fresh(session, master).title == master.title


@pytest.mark.parametrize(("kind", "base"), KINDS)
def test_the_value_in_the_save_routes_query_does_not_unlock(
    auth_client, session, masters, kind, base
):
    # Only the form field counts for a save, never the address.
    master = masters[kind]
    value = unlock_master(auth_client, kind, master.id)

    save = htmx_send(auth_client, "POST", f"{base}/{master.id}?unlock={value}", {"title": "X"})
    refresh = htmx_send(auth_client, "POST", f"{base}/{master.id}/refresh?unlock={value}", {})

    assert save.status_code == 409 and refresh.status_code == 409
    assert fresh(session, master).title == master.title


@pytest.mark.parametrize(("kind", "base"), KINDS)
def test_a_value_from_another_session_does_not_unlock(
    client, auth_client, session, masters, kind, base
):
    # The same user signed in on a second browser: its value is not this one's.
    master = masters[kind]
    other_browser = TestClient(app)
    assert log_in(other_browser).status_code == 303
    theirs = unlock_master(other_browser, kind, master.id)

    assert READ_ONLY in auth_client.get(f"{base}/{master.id}?unlock={theirs}").text
    response = htmx_send(
        auth_client, "POST", f"{base}/{master.id}", {"title": "X", "unlock": theirs}
    )
    assert response.status_code == 409
    assert fresh(session, master).title == master.title


def test_many_unlocked_masters_all_keep_their_own_value(auth_client, session, user, masters):
    # One master per language and kind: 6 keys in the session, each still works.
    unlocked = [("cv", masters["cv"]), ("letter", masters["letter"])]
    for language in ("de", "tr"):
        unlocked.append(
            ("cv", docs.create_cv(session, user, f"CV {language}", language, "classic"))
        )
        unlocked.append(("letter", letters.create_letter(session, user, f"L {language}", language)))
    values = {(kind, doc.id): unlock_master(auth_client, kind, doc.id) for kind, doc in unlocked}

    assert len(set(values.values())) == 6
    for kind, doc in unlocked:
        base = "/cvs" if kind == "cv" else "/cover-letters"
        response = htmx_send(
            auth_client,
            "POST",
            f"{base}/{doc.id}",
            {"title": f"Saved {doc.id}", "unlock": values[(kind, doc.id)]},
        )
        assert response.status_code == 200, (kind, doc.id)
        assert fresh(session, doc).title == f"Saved {doc.id}"


# --- The unlock route with odd or locked ids -------------------------------------------------


@pytest.mark.parametrize(("kind", "base"), KINDS)
@pytest.mark.parametrize("doc_id", ["abc", "0", "-1", "999999", str(2**63), "1.5", "%20"])
def test_unlocking_an_odd_id_is_404(auth_client, masters, kind, base, doc_id):
    response = post(auth_client, f"{base}/{doc_id}/edit-master")

    assert response.status_code == 404
    assert "HX-Redirect" not in response.headers


def test_a_locked_cv_has_nothing_to_unlock(auth_client, session, user, masters):
    copy = docs.create_cv(session, user, "Sent", "en", "classic")
    docs.lock(session, copy)

    assert post(auth_client, f"/cvs/{copy.id}/edit-master").status_code == 409
    assert fresh(session, copy).is_locked


def test_a_locked_letter_has_nothing_to_unlock(auth_client, session, user, masters):
    copy = letters.create_letter(session, user, "Sent", "en")
    letters.lock(session, copy)

    assert post(auth_client, f"/cover-letters/{copy.id}/edit-master").status_code == 409
    assert fresh(session, copy).is_locked


@pytest.mark.parametrize(("kind", "base"), KINDS)
def test_unlocking_with_get_is_not_allowed(auth_client, masters, kind, base):
    # A link (GET) can never unlock; only the dialog's POST can.
    response = auth_client.get(f"{base}/{masters[kind].id}/edit-master")

    assert response.status_code in (404, 405)
    assert "master_unlock" not in str(auth_client.cookies)


@pytest.mark.parametrize(("kind", "base"), KINDS)
def test_logged_out_unlock_is_refused_for_both_kinds(client, masters, kind, base):
    response = client.post(
        f"{base}/{masters[kind].id}/edit-master",
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)},
        follow_redirects=False,
    )

    assert response.status_code in (200, 303)
    target = response.headers.get("HX-Redirect") or response.headers.get("location", "")
    assert target.startswith("/login")


@pytest.mark.parametrize(("kind", "base"), KINDS)
def test_unlocking_with_a_wrong_csrf_token_is_refused(auth_client, masters, kind, base):
    response = auth_client.post(
        f"{base}/{masters[kind].id}/edit-master",
        headers={"HX-Request": "true", "X-CSRF-Token": "not-the-real-token"},
    )

    assert response.status_code == 403


# --- Photo upload: field limits (#88) --------------------------------------------------------


def photo_post(client, data=None, files=None, headers=None):
    return client.post(
        "/profile/photo",
        data=data,
        files=files,
        headers=HTMX | {"X-CSRF-Token": csrf_token(client)} | (headers or {}),
        follow_redirects=False,
    )


def test_a_photo_upload_with_more_than_5_fields_is_refused(auth_client, session, user, upload_dir):
    get_or_create_profile(session, user)
    extra = {f"extra{i}": "x" for i in range(5)}  # 5 fields + the file = 6

    response = photo_post(
        auth_client, data=extra, files={"photo": ("me.jpg", image_bytes(), "image/jpeg")}
    )

    assert response.status_code == 400
    assert stored_files(upload_dir) == []
    assert saved_profile(session).photo_path is None


def test_a_photo_upload_with_5_fields_is_saved(auth_client, session, upload_dir):
    extra = {f"extra{i}": "x" for i in range(4)}  # 4 fields + the file = 5

    response = photo_post(
        auth_client, data=extra, files={"photo": ("me.jpg", image_bytes(), "image/jpeg")}
    )

    assert response.status_code == 200
    assert len(stored_files(upload_dir)) == 1
    assert saved_profile(session).photo_path is not None


def test_an_htmx_urlencoded_form_to_the_photo_route_is_unreadable(auth_client, upload_dir):
    response = photo_post(auth_client, data={"photo": "not a file"})

    assert response.status_code == 200
    assert escape(photo_service.UNREADABLE) in response.text
    assert stored_files(upload_dir) == []


def test_two_photo_files_with_the_csrf_header_are_refused(auth_client, session, upload_dir):
    files = [
        ("photo", ("a.jpg", image_bytes(), "image/jpeg")),
        ("other", ("b.jpg", image_bytes(), "image/jpeg")),  # a different field name
    ]

    response = photo_post(auth_client, files=files)

    assert response.status_code == 400
    assert stored_files(upload_dir) == []


def test_a_multipart_photo_with_the_token_only_as_a_field_is_refused(
    auth_client, session, upload_dir
):
    # A multipart body is never parsed for the token: the header is needed.
    response = auth_client.post(
        "/profile/photo",
        data={"csrf_token": csrf_token(auth_client)},
        files={"photo": ("me.jpg", image_bytes(), "image/jpeg")},
        headers=HTMX,
    )

    assert response.status_code == 403
    assert stored_files(upload_dir) == []


def test_a_non_htmx_urlencoded_form_to_the_photo_route_saves_nothing(
    auth_client, session, upload_dir
):
    # Not htmx, the token in the form: the CSRF check parses it first, then the
    # route finds no file and sends a normal page load back to /profile.
    response = auth_client.post(
        "/profile/photo",
        data={"csrf_token": csrf_token(auth_client), "photo": "not a file"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/profile"
    assert stored_files(upload_dir) == []
    assert saved_profile(session).photo_path is None


def test_a_urlencoded_photo_form_with_too_many_fields_is_refused(auth_client, session, upload_dir):
    # The CSRF check parsed it with the app-wide limit (1100); the route's own limit
    # of 5 must still apply (#88).
    form = {"csrf_token": csrf_token(auth_client)} | {f"extra{i}": "x" for i in range(5)}

    response = auth_client.post("/profile/photo", data=form, follow_redirects=False)

    assert response.status_code == 400
    assert stored_files(upload_dir) == []


# --- Host header (#85) -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "EVIL.EXAMPLE",
        "localhost.",  # a trailing dot is another name
        "localhost.evil.example",
        "evil.example:localhost",
        "localhost@evil.example",
        "",
        " ",
    ],
)
def test_an_odd_host_is_refused(client, host):
    response = client.get("/login", headers={"host": host})

    assert response.status_code == 400
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("host", ["LOCALHOST", "LocalHost:8000"])
def test_an_uppercase_own_host_does_not_crash(client, host):
    # Browsers send the host in lowercase; another client may not. Either answer is
    # safe, but never a server error.
    assert client.get("/login", headers={"host": host}).status_code in (200, 400)


def test_head_with_a_bad_host_is_refused(client):
    response = client.head("/login", headers={"host": "evil.example"})

    assert response.status_code == 400
    assert response.content == b""


def test_a_post_with_a_bad_host_changes_nothing(auth_client, session, masters):
    master = masters["cv"]
    value = unlock_master(auth_client, "cv", master.id)

    response = auth_client.post(
        f"/cvs/{master.id}",
        data={"title": "X", "unlock": value},
        headers=HTMX | {"X-CSRF-Token": csrf_token(auth_client), "host": "evil.example"},
    )

    assert response.status_code == 400
    assert fresh(session, master).title == master.title


def test_the_extension_api_with_our_origin_and_a_bad_host_is_refused(client, user):
    response = client.post(
        "/api/ext/auth/login",
        json={"email": "a@b.c", "password": "x"},
        headers={"Origin": ORIGIN, "host": "evil.example"},
    )

    assert response.status_code == 400


def test_the_extension_api_checks_the_host_before_the_origin(client):
    # The Host check is outside the extension guard: 400, not the guard's 403.
    response = client.get(
        "/api/ext/me", headers={"Origin": "https://evil.example", "host": "evil.example"}
    )

    assert response.status_code == 400


def test_the_extension_api_with_our_origin_and_host_still_works(client, user):
    assert api_login(client).status_code == 200
