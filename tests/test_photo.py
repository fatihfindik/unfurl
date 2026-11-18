"""Profile photo: upload checks, clean re-save, owner-only access (SPEC §10.1, D-038, D-039)."""

import io

import pytest
from markupsafe import escape  # the same escaping as the templates (Jinja)
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from sqlmodel import select
from starlette.requests import Request

from app.models import Profile
from app.services import photo_service
from app.services.auth_service import create_user
from tests.helpers import csrf_token, log_in

HTMX = {"HX-Request": "true"}


def image_bytes(fmt="JPEG", size=(1200, 900), mode="RGB", **save_args) -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, "white" if mode != "1" else 1).save(buffer, format=fmt, **save_args)
    return buffer.getvalue()


def upload(client, data: bytes, filename="photo.jpg", content_type="image/jpeg", headers=None):
    headers = HTMX | {"X-CSRF-Token": csrf_token(client)} | (headers or {})
    return client.post(
        "/profile/photo",
        files={"photo": (filename, data, content_type)},
        headers=headers,
        follow_redirects=False,
    )


def saved_profile(session) -> Profile:
    session.expire_all()
    return session.exec(select(Profile)).one()


def stored_files(upload_dir) -> list:
    folder = upload_dir / "photos"
    return sorted(folder.iterdir()) if folder.exists() else []


# --- Good uploads -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "filename", "extension"),
    [("JPEG", "me.jpg", ".jpg"), ("JPEG", "me.JPEG", ".jpg"), ("PNG", "me.png", ".png")],
)
def test_a_jpeg_or_png_is_saved_with_a_random_name(
    auth_client, session, upload_dir, fmt, filename, extension
):
    response = upload(auth_client, image_bytes(fmt), filename)

    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "profile-changed"
    assert 'src="/profile/photo?v=' in response.text
    files = stored_files(upload_dir)
    assert len(files) == 1
    assert files[0].suffix == extension
    assert "me" not in files[0].name  # the user's file name is never used
    assert saved_profile(session).photo_path == f"photos/{files[0].name}"


def test_a_png_called_jpg_is_kept_and_saved_as_png(auth_client, upload_dir):
    response = upload(auth_client, image_bytes("PNG"), "photo.jpg")

    assert 'role="alert"' not in response.text
    assert stored_files(upload_dir)[0].suffix == ".png"


def test_the_photo_is_made_smaller(auth_client, upload_dir):
    upload(auth_client, image_bytes(size=(3000, 1500)))

    with Image.open(stored_files(upload_dir)[0]) as saved:
        assert saved.size == (800, 400)


def test_exif_is_removed_from_a_jpeg(auth_client, upload_dir):
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # camera make
    exif[0x0131] = "SecretApp 1.0"  # software
    upload(auth_client, image_bytes(exif=exif.tobytes()))

    with Image.open(stored_files(upload_dir)[0]) as saved:
        assert dict(saved.getexif()) == {}
        assert "exif" not in saved.info


def test_metadata_is_removed_from_a_png(auth_client, upload_dir):
    info = PngInfo()
    info.add_text("Author", "Jana Novak")
    info.add_text("XML:com.adobe.xmp", "<x:xmpmeta>secret</x:xmpmeta>")
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"
    upload(auth_client, image_bytes("PNG", pnginfo=info, exif=exif.tobytes()), "me.png")

    with Image.open(stored_files(upload_dir)[0]) as saved:
        assert "Author" not in saved.info
        assert "XML:com.adobe.xmp" not in saved.info
        assert "exif" not in saved.info
        assert dict(saved.getexif()) == {}


@pytest.mark.parametrize(
    ("fmt", "mode", "filename"),
    [("JPEG", "CMYK", "print.jpg"), ("PNG", "P", "palette.png"), ("PNG", "I;16", "deep.png")],
)
def test_unusual_colour_modes_are_saved(auth_client, upload_dir, fmt, mode, filename):
    response = upload(auth_client, image_bytes(fmt, mode=mode), filename)

    assert response.status_code == 200
    assert 'role="alert"' not in response.text
    with Image.open(stored_files(upload_dir)[0]) as saved:
        assert saved.mode in ("RGB", "RGBA")


def test_upload_and_remove_raise_the_profile_version(auth_client, session):
    auth_client.get("/profile")  # creates the profile
    start = saved_profile(session).version

    upload(auth_client, image_bytes())
    assert saved_profile(session).version == start + 1

    auth_client.delete("/profile/photo", headers=HTMX | {"X-CSRF-Token": csrf_token(auth_client)})
    assert saved_profile(session).version == start + 2


def test_a_new_photo_replaces_the_old_file(auth_client, upload_dir):
    upload(auth_client, image_bytes())
    first = stored_files(upload_dir)

    upload(auth_client, image_bytes("PNG"), "new.png")

    second = stored_files(upload_dir)
    assert len(second) == 1
    assert second != first


def test_remove_deletes_the_file(auth_client, session, upload_dir):
    upload(auth_client, image_bytes())

    response = auth_client.delete(
        "/profile/photo", headers=HTMX | {"X-CSRF-Token": csrf_token(auth_client)}
    )

    assert response.status_code == 200
    assert "Upload a photo…" in response.text
    assert "Remove photo" not in response.text
    assert stored_files(upload_dir) == []
    assert saved_profile(session).photo_path is None


# --- Refused uploads --------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "filename", "message"),
    [
        (image_bytes("GIF"), "anim.gif", photo_service.WRONG_TYPE),
        (image_bytes("GIF"), "anim.jpg", photo_service.WRONG_TYPE),  # content decides
        (image_bytes("WEBP"), "photo.webp", photo_service.WRONG_TYPE),
        (image_bytes(), "photo.heic", photo_service.WRONG_TYPE),
        (b"just some text, not an image", "notes.jpg", photo_service.WRONG_TYPE),
        (b"", "empty.jpg", photo_service.UNREADABLE),
        (image_bytes()[:300], "broken.jpg", photo_service.UNREADABLE),  # cut off
        (b"x" * (photo_service.MAX_BYTES + 1), "big.jpg", photo_service.TOO_BIG),
        # ~30 million pixels in a small file ("decompression bomb")
        (image_bytes("PNG", size=(6000, 5000), mode="1"), "bomb.png", photo_service.TOO_BIG),
    ],
)
def test_bad_files_are_refused_with_a_message(
    auth_client, session, upload_dir, data, filename, message
):
    response = upload(auth_client, data, filename)

    assert response.status_code == 200  # htmx only shows 2xx answers
    assert escape(message) in response.text  # the page escapes ' as &#39;
    assert 'role="alert"' in response.text
    assert "HX-Trigger" not in response.headers
    assert stored_files(upload_dir) == []
    assert saved_profile(session).photo_path is None


def test_a_too_big_request_is_refused_before_it_is_read(auth_client, upload_dir, monkeypatch):
    calls = []
    real_form = Request.form

    def watched_form(self, *args, **kwargs):
        calls.append(self.url.path)
        return real_form(self, *args, **kwargs)

    monkeypatch.setattr(Request, "form", watched_form)

    response = upload(auth_client, b"x" * (photo_service.MAX_REQUEST_BYTES + 1))

    assert escape(photo_service.TOO_BIG) in response.text
    assert "/profile/photo" not in calls  # the body was never parsed
    assert stored_files(upload_dir) == []


def test_a_request_without_content_length_is_refused(auth_client, upload_dir):
    def body():  # a generator: the request is sent in chunks, without Content-Length
        yield b"--x\r\n"

    response = auth_client.post(
        "/profile/photo",
        content=body(),
        headers=HTMX
        | {
            "X-CSRF-Token": csrf_token(auth_client),
            "Content-Type": "multipart/form-data; boundary=x",
        },
    )

    assert escape(photo_service.UNREADABLE) in response.text
    assert stored_files(upload_dir) == []


def test_uploading_needs_a_csrf_token(auth_client, upload_dir):
    response = auth_client.post(
        "/profile/photo", files={"photo": ("me.jpg", image_bytes(), "image/jpeg")}, headers=HTMX
    )

    assert response.status_code == 403
    assert stored_files(upload_dir) == []


# --- Who can see it ---------------------------------------------------------------


def test_the_owner_gets_their_photo(auth_client, upload_dir):
    upload(auth_client, image_bytes())

    response = auth_client.get("/profile/photo")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == stored_files(upload_dir)[0].read_bytes()


def test_no_photo_is_404(auth_client):
    assert auth_client.get("/profile/photo").status_code == 404


def test_each_user_only_ever_gets_their_own_photo(client, session, upload_dir):
    create_user(session, email="a@example.com", password="a" * 12, full_name="A")
    create_user(session, email="b@example.com", password="b" * 12, full_name="B")

    log_in(client, "a@example.com", "a" * 12)
    upload(client, image_bytes("PNG", size=(10, 10)), "a.png", "image/png")
    a_photo = client.get("/profile/photo").content
    client.post("/logout", data={"csrf_token": csrf_token(client)})

    # B has no photo: B never gets A's.
    log_in(client, "b@example.com", "b" * 12)
    assert client.get("/profile/photo").status_code == 404

    upload(client, image_bytes("JPEG", size=(20, 20)), "b.jpg")
    b_photo = client.get("/profile/photo").content
    assert b_photo != a_photo
    assert len(stored_files(upload_dir)) == 2


def test_logged_out_visitors_get_no_photo(client):
    response = client.get("/profile/photo", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.parametrize(
    "path", ["../secret.jpg", "photos/../../secret.jpg", "/etc/passwd", "other/x.jpg"]
)
def test_paths_outside_the_photo_folder_are_never_served(upload_dir, path):
    (upload_dir / "photos").mkdir(parents=True)
    (upload_dir / "secret.jpg").write_bytes(b"secret")
    (upload_dir / "other").mkdir()
    (upload_dir / "other" / "x.jpg").write_bytes(b"x")

    assert photo_service.photo_file(path) is None


def test_the_page_shows_upload_or_the_photo(auth_client):
    page = auth_client.get("/profile").text
    assert "Upload a photo…" in page
    assert 'hx-encoding="multipart/form-data"' in page
    assert "JPEG or PNG, up to 2 MB." in page

    upload(auth_client, image_bytes())
    page = auth_client.get("/profile").text
    assert "Remove photo" in page  # the menu offers removing once there is a photo
    assert 'src="/profile/photo?v=' in page


# --- Hardening from the M2b security review ---------------------------------------


def test_a_phone_mpo_jpeg_is_kept_as_jpeg(auth_client, upload_dir):
    # Some phones save JPEGs with a second picture inside; Pillow calls them MPO.
    first = Image.new("RGB", (40, 30), "red")
    buffer = io.BytesIO()
    first.save(buffer, format="MPO", save_all=True, append_images=[Image.new("RGB", (40, 30))])

    response = upload(auth_client, buffer.getvalue(), "phone.jpg")

    assert 'role="alert"' not in response.text
    assert stored_files(upload_dir)[0].suffix == ".jpg"


def test_any_pillow_failure_is_a_message_and_leaves_no_file(auth_client, upload_dir, monkeypatch):
    # A damaged EXIF block can make Pillow raise unusual errors (not only OSError).
    def broken(image):
        raise SyntaxError("bad EXIF")

    monkeypatch.setattr(photo_service.ImageOps, "exif_transpose", broken)

    response = upload(auth_client, image_bytes())

    assert response.status_code == 200
    assert escape(photo_service.UNREADABLE) in response.text
    assert stored_files(upload_dir) == []


def test_a_failed_save_removes_the_new_file(auth_client, upload_dir, monkeypatch):
    from app.services import profile_service

    def failing_set_photo(*args, **kwargs):
        raise RuntimeError("database is down")

    monkeypatch.setattr(profile_service, "set_photo", failing_set_photo)

    with pytest.raises(RuntimeError):
        upload(auth_client, image_bytes())

    assert stored_files(upload_dir) == []  # no file without a profile pointing to it
