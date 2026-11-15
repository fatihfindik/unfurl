"""A photo on a CV (D-052): copied in only while "Show photo" is on, kept by sent CVs."""

import base64
import io

import pytest
from PIL import Image
from pydantic import ValidationError
from pypdf import PdfReader

from app.models import CVDocument
from app.schemas.cv import CVContent
from app.services import document_service as docs
from app.services import pdf_service, photo_service
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile, set_photo
from tests.test_cv_editor import save, stored


def image_bytes(fmt="JPEG", size=(1200, 1600), mode="RGB", color="navy") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def give_profile_photo(session, user, data=None, filename="me.jpg"):
    profile = get_or_create_profile(session, user)
    path = photo_service.save_photo(data or image_bytes(), filename)
    set_photo(session, profile, path)
    return path


@pytest.fixture
def copy(session, user):
    get_or_create_profile(session, user)
    docs.create_cv(session, user, "Master", "en", "classic")
    return docs.create_cv(session, user, "For Acme", "en", "classic")


def photo_of(session, cv) -> Image.Image | None:
    data = stored(session, cv).personal.photo
    return Image.open(io.BytesIO(base64.b64decode(data))) if data else None


# --- Switching it on and off ----------------------------------------------------------


def test_a_new_cv_has_no_photo(session, user, copy):
    give_profile_photo(session, user)

    assert stored(session, copy).personal.photo is None  # off by default (Fatih, M3b)
    assert not copy.show_photo


def test_switching_on_copies_a_small_jpeg_of_the_profile_photo(auth_client, session, user, copy):
    give_profile_photo(session, user)

    save(auth_client, copy, show_photo="on")

    photo = photo_of(session, copy)
    assert photo.format == "JPEG"
    assert max(photo.size) <= 400
    assert len(stored(session, copy).personal.photo) < 80_000 * 4 / 3  # < 80 KB as base64
    assert stored(session, copy).personal.photo_type == "image/jpeg"


def test_a_transparent_png_becomes_a_white_backed_jpeg(auth_client, session, user, copy):
    give_profile_photo(session, user, image_bytes("PNG", mode="RGBA", color=(0, 0, 0, 0)), "me.png")

    save(auth_client, copy, show_photo="on")

    photo = photo_of(session, copy).convert("RGB")
    assert photo.getpixel((5, 5)) == (255, 255, 255)


def test_switching_off_removes_the_copy(auth_client, session, user, copy):
    give_profile_photo(session, user)
    save(auth_client, copy, show_photo="on")

    save(auth_client, copy)  # the box is not sent: off

    assert stored(session, copy).personal.photo is None
    session.expire_all()
    assert not session.get(CVDocument, copy.id).show_photo


def test_without_a_profile_photo_the_box_explains(auth_client, session, copy):
    response = save(auth_client, copy, show_photo="on")

    assert 'id="cv-details-hint" hx-swap-oob="true"' in response.text
    assert "Not in your profile yet: photo." in response.text
    assert stored(session, copy).personal.photo is None


# --- A CV keeps its own photo -------------------------------------------------------


def test_changing_or_deleting_the_profile_photo_leaves_the_cv_alone(
    auth_client, session, user, copy
):
    give_profile_photo(session, user, image_bytes(color="navy"))
    save(auth_client, copy, show_photo="on")
    before = stored(session, copy).personal.photo

    give_profile_photo(session, user, image_bytes(color="red"))
    set_photo(session, get_or_create_profile(session, user), None)

    assert stored(session, copy).personal.photo == before


def test_a_locked_cv_keeps_the_photo_it_was_sent_with(auth_client, session, user, copy):
    give_profile_photo(session, user)
    save(auth_client, copy, show_photo="on")
    session.refresh(copy)
    docs.lock(session, copy)

    assert save(auth_client, copy).status_code == 409  # can't switch it off
    assert stored(session, copy).personal.photo is not None


def test_refresh_takes_the_current_photo_when_shown(auth_client, session, user, copy):
    give_profile_photo(session, user, image_bytes(color="navy"))
    save(auth_client, copy, show_photo="on")
    before = stored(session, copy).personal.photo
    give_profile_photo(session, user, image_bytes(color="red"))

    session.expire_all()
    docs.refresh_from_profile(session, user, session.get(CVDocument, copy.id))

    assert stored(session, copy).personal.photo not in (None, before)


def test_new_versions_carry_the_photo(auth_client, session, user, copy):
    give_profile_photo(session, user)
    save(auth_client, copy, show_photo="on", summary="Edited.")
    session.expire_all()

    v2 = docs.save_as_new_version(session, user, session.get(CVDocument, copy.id))

    assert v2.show_photo
    assert stored(session, v2).personal.photo == stored(session, copy).personal.photo


# --- On the page and in the PDF -------------------------------------------------------


def test_preview_and_pdf_show_the_photo_only_when_on(auth_client, session, user, copy):
    give_profile_photo(session, user)
    off = save(auth_client, copy).text
    assert 'class="cv-photo"' not in off

    on = save(auth_client, copy, show_photo="on").text
    assert f'src="/cvs/{copy.id}/photo?v=' in on

    session.expire_all()
    cv = session.get(CVDocument, copy.id)
    html = pdf_service.render_cv_html(cv, for_pdf=True)
    assert 'src="data:image/jpeg;base64,' in html
    pdf = PdfReader(io.BytesIO(pdf_service.render_cv_pdf(cv)))
    assert len(pdf.pages[0].images) == 1  # the photo really is in the PDF


def test_the_photo_route_serves_the_owner_only(auth_client, session, user, copy):
    give_profile_photo(session, user)
    save(auth_client, copy, show_photo="on")

    response = auth_client.get(f"/cvs/{copy.id}/photo")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-cache"

    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    get_or_create_profile(session, other)
    docs.create_cv(session, other, "Master", "en", "classic")
    theirs = docs.create_cv(session, other, "Theirs", "en", "classic")
    assert auth_client.get(f"/cvs/{theirs.id}/photo").status_code == 404


def test_a_cv_without_a_photo_has_none_to_serve(auth_client, copy):
    assert auth_client.get(f"/cvs/{copy.id}/photo").status_code == 404


def test_logged_out_visitors_get_no_photo(client):
    response = client.get("/cvs/1/photo", follow_redirects=False)

    assert response.status_code == 303


# --- Odd content is refused (D-052) ----------------------------------------------------


@pytest.mark.parametrize(
    "personal",
    [
        {"photo": "not base64 <script>"},
        {"photo": "A" * 200_001},
        {"photo": "QUFB", "photo_type": "text/html"},
        {"photo": "QUFB", "photo_type": "image/svg+xml"},
    ],
)
def test_photo_fields_with_odd_values_are_refused(personal):
    with pytest.raises(ValidationError):
        CVContent.model_validate({"personal": {"full_name": "A"} | personal})
