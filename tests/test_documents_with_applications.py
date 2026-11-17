"""Documents with their application (D-084, D-085): editor back link and band, version
picker, Duplicate, grouping on the Documents page, company in the PDF name."""

import pytest

from app.models import Application, CVDocument
from app.services import application_service as apps
from app.services import document_service as docs
from app.services import pdf_service
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile
from tests.helpers import csrf_token


@pytest.fixture
def master(session, user) -> CVDocument:
    get_or_create_profile(session, user)
    return docs.create_cv(session, user, "Master CV", "en", "classic")


@pytest.fixture
def job(session, user) -> Application:
    return apps.create(session, user, company="Kranich Health", job_title="Senior PM")


@pytest.fixture
def linked(session, user, master, job) -> CVDocument:
    cv = docs.copy_for_application(session, user, master, job, "Kranich Health · CV")
    apps.link_cv(session, user, job, cv)
    return cv


def test_the_editor_goes_back_to_the_application(auth_client, linked, job):
    html = auth_client.get(f"/cvs/{linked.id}").text

    assert f'href="/applications/{job.id}"' in html and "← Kranich Health" in html
    assert ">All documents</a>" in html
    assert "Copy for Kranich Health, based on Master CV. Your master CV stays unchanged." in html


def test_the_address_cannot_name_another_application(auth_client, session, user, master, job):
    plain = docs.create_cv(session, user, "Plain copy", "en", "classic")

    html = auth_client.get(f"/cvs/{plain.id}?application={job.id}").text

    assert "Kranich Health" not in html and "← Documents" in html


def test_the_version_picker_shows_this_applications_versions(
    auth_client, session, user, master, linked
):
    other = docs.create_cv(session, user, "Unrelated copy", "en", "classic")

    html = auth_client.get(f"/cvs/{linked.id}").text

    picker = html.split("<details", 1)[1].split("</details>", 1)[0]
    assert f'href="/cvs/{master.id}"' in picker and f'href="/cvs/{linked.id}"' in picker
    assert f'href="/cvs/{other.id}"' not in picker


def test_a_new_version_of_the_sent_cv_becomes_the_one_in_use(
    auth_client, session, user, linked, job
):
    apps.move(session, user, job, "applied")  # locks it

    response = auth_client.post(
        f"/cvs/{linked.id}/versions",
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(auth_client)},
    )

    new_id = int(response.headers["HX-Redirect"].rsplit("/", 1)[1])
    session.expire_all()
    assert session.get(Application, job.id).cv_id == new_id
    assert session.get(CVDocument, new_id).application_id == job.id
    assert len(apps.sent_documents(session, user, job)) == 1  # what was sent stays


def test_the_documents_page_groups_copies_by_application(auth_client, session, user, linked):
    docs.create_cv(session, user, "Loose copy", "en", "classic")

    html = auth_client.get("/documents").text

    assert "Kranich Health · Senior PM" in html and "Not linked to an application" in html
    assert html.index("Kranich Health · Senior PM") < html.index("Not linked to an application")


def test_another_users_applications_never_name_a_group(auth_client, session, user, master):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    apps.create(session, other, company="Their Secret GmbH", job_title="PM")

    assert "Their Secret" not in auth_client.get("/documents").text


def test_the_pdf_name_has_the_company(linked):
    assert pdf_service.pdf_filename(linked, "Kranich Health")[0].endswith("_Kranich_Health_en.pdf")
    assert pdf_service.pdf_filename(linked)[0].endswith("_en.pdf")
    assert "Kranich" not in pdf_service.pdf_filename(linked)[0]


@pytest.mark.parametrize(
    ("company", "ascii_part", "real_part"),
    [
        ("Şişecam Çevre", "Sisecam_Cevre", "Şişecam_Çevre"),
        ('Evil"; filename=x.exe', "Evil_filename_x_exe", "Evil_filename_x_exe"),
        ("K" * 80, "K" * 40, "K" * 40),
    ],
)
def test_the_company_in_the_name_is_cleaned(linked, company, ascii_part, real_part):
    ascii_name, real_name = pdf_service.pdf_filename(linked, company)

    assert f"_{ascii_part}_en.pdf" in ascii_name and ascii_name.isascii()
    assert f"_{real_part}_en.pdf" in real_name


def test_the_download_uses_the_company(auth_client, linked, monkeypatch):
    monkeypatch.setattr(pdf_service, "render_cv_pdf", lambda cv: b"%PDF-1.7")

    response = auth_client.get(f"/cvs/{linked.id}/pdf")

    assert "Kranich_Health_en.pdf" in response.headers["content-disposition"]
