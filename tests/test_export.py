"""Export my data (D-130, SPEC §10.2): one JSON file with everything of the user,
nothing secret, nothing of anyone else, and no table forgotten."""

import base64
import json

import pytest
from sqlalchemy import Table
from sqlmodel import SQLModel

from app.routers import settings as settings_routes
from app.services import export_service
from app.services.auth_service import create_user
from tests.account_data import fill_account
from tests.helpers import USER_PASSWORD, csrf_token
from tests.test_photo import image_bytes, upload


def export(client, token=None):
    return client.post("/settings/export", data={"csrf_token": token or csrf_token(client)})


def exported(client) -> dict:
    response = export(client)
    assert response.status_code == 200
    return json.loads(response.content)


@pytest.fixture(autouse=True)
def no_running_exports():
    settings_routes._exports_running.clear()


# --- No table is forgotten ------------------------------------------------------------------


def tables_of_a_user() -> set[str]:
    """Every table that points to `user`, directly or through other tables."""
    tables: dict[str, Table] = SQLModel.metadata.tables
    found = {"user"}
    changed = True
    while changed:
        changed = False
        for name, table in tables.items():
            if name in found:
                continue
            targets = {fk.column.table.name for fk in table.foreign_keys}
            # "Belongs to" means a cascade: a SET NULL link (e.g. a goal's source job)
            # doesn't make the table the user's.
            owners = {
                fk.column.table.name
                for fk in table.foreign_keys
                if (fk.ondelete or "").upper() == "CASCADE"
            }
            if owners & found and targets:
                found.add(name)
                changed = True
    return found


def test_every_table_of_a_user_is_exported_or_excluded_with_a_reason():
    missing = (
        tables_of_a_user() - export_service.EXPORTED_TABLES - set(export_service.EXCLUDED_TABLES)
    )

    assert missing == set(), f"Decide: export these tables or exclude them: {missing}"
    assert all(reason for reason in export_service.EXCLUDED_TABLES.values())


def test_the_walk_finds_tables_without_a_user_id():
    assert "applicationevent" in tables_of_a_user()  # only application_id
    assert "experience" in tables_of_a_user()  # only profile_id


# --- Content --------------------------------------------------------------------------------


def test_the_file_has_every_section(auth_client, session, user):
    rows = fill_account(session, user)

    data = exported(auth_client)

    assert data["format"] == "unfurl-export" and data["version"] == 1
    assert data["account"]["email"] == user.email
    assert data["profile"]["headline"] == "PM"
    for key in (
        "experience", "education", "skills", "languages", "certifications", "projects",
        "cvs", "cover_letters", "applications", "application_events", "application_documents",
        "interview_questions", "learning_goals", "learning_resources", "learning_tasks",
        "study_log", "notifications",
    ):  # fmt: skip
        assert len(data[key]) >= 1, key
    assert {cv["id"] for cv in data["cvs"]} == {rows["master"].id, rows["copy"].id}
    assert {letter["id"] for letter in data["cover_letters"]} == {
        rows["letter"].id,
        rows["letter_copy"].id,
    }
    assert data["application_events"][0]["message"] == "Called Mira"
    # The links between letters and applications are in it too (D-157, D-159).
    assert data["applications"][0]["cover_letter_id"] == rows["letter_copy"].id
    copy = next(x for x in data["cover_letters"] if x["id"] == rows["letter_copy"].id)
    assert copy["application_id"] == rows["job"].id
    sent = next(d for d in data["application_documents"] if d["doc_type"] == "cover_letter")
    assert sent["cover_letter_id"] == rows["letter_copy"].id
    assert data["application_documents"][0]["content_snapshot"] == {"title": "For Acme"}
    assert data["study_log"][0]["minutes"] == 30
    assert data["notification_preferences"]["rest_days"] == ["sat", "sun"]


def test_dates_are_iso_text(auth_client, session, user):
    fill_account(session, user)

    data = exported(auth_client)

    assert data["account"]["created_at"].startswith("20")
    assert data["learning_tasks"][0]["due_date"] == "2026-11-18"


def test_nothing_secret_or_internal_is_in_it(auth_client, session, user):
    rows = fill_account(session, user)

    text = export(auth_client).text

    assert user.password_hash not in text
    assert "password_hash" not in text
    assert "session_version" not in text
    assert '"user_id"' not in text
    assert "Old CV" not in text  # the Undo copy (deleteditem) is excluded
    assert rows["token"].token_hash not in text  # extension tokens are excluded (D-136)
    assert "token_hash" not in text


def test_nothing_of_another_user_is_in_it(auth_client, session, user):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="Ola")
    fill_account(session, other)

    data = exported(auth_client)

    assert data["profile"] is None
    assert all(data[section.key] == [] for section in export_service.SECTIONS)
    assert "other@example.com" not in json.dumps(data)


def test_an_empty_account_is_a_valid_file(auth_client, user):
    data = exported(auth_client)

    assert data["account"]["full_name"] == "Jana Novak"
    assert data["profile_photo"] is None


def test_the_photo_is_in_it_as_base64(auth_client, user):
    upload(auth_client, image_bytes())

    photo = exported(auth_client)["profile_photo"]

    assert photo["media_type"] == "image/jpeg"
    assert base64.b64decode(photo["data"])[:3] == b"\xff\xd8\xff"  # a JPEG


def test_a_missing_photo_file_is_null(auth_client, session, user):
    fill_account(session, user, photo_path="photos/gone.jpg")

    assert exported(auth_client)["profile_photo"] is None


def test_a_photo_path_outside_the_folder_is_never_read(auth_client, session, user):
    fill_account(session, user, photo_path="../../pyproject.toml")

    assert exported(auth_client)["profile_photo"] is None


def test_text_with_quotes_and_unicode_stays_valid_json(auth_client, session, user):
    rows = fill_account(session, user)
    rows["job"].company = 'Şişecam "Glass" \\ <b>\n'
    session.add(rows["job"])
    session.commit()

    data = exported(auth_client)

    assert data["applications"][0]["company"] == 'Şişecam "Glass" \\ <b>\n'


def test_many_rows_are_streamed(auth_client, session, user):
    rows = fill_account(session, user)
    for number in range(export_service.BATCH * 2 + 5):
        session.add(
            export_service.ApplicationEvent(
                application_id=rows["job"].id, type="note", message=f"Note {number}"
            )
        )
    session.commit()

    response = export(auth_client)

    assert len(json.loads(response.content)["application_events"]) == export_service.BATCH * 2 + 6


def test_the_stream_comes_in_pieces(session, user):
    fill_account(session, user)

    pieces = list(export_service.stream(session.get_bind(), user.id))

    assert len(pieces) > 10
    json.loads("".join(pieces))


# --- The download ---------------------------------------------------------------------------


def test_it_is_a_download(auth_client, user):
    response = export(auth_client)

    assert response.headers["content-type"].startswith("application/json")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="unfurl-export-')
    assert disposition.endswith('.json"')
    assert response.headers["Cache-Control"] == "no-store"


def test_the_settings_page_has_the_button(auth_client):
    page = auth_client.get("/settings").text

    assert 'action="/settings/export"' in page
    assert "Export my data" in page


def test_one_export_at_a_time(auth_client, user):
    settings_routes._start_export(user.id)

    response = export(auth_client)

    assert response.status_code == 429
    assert "already running" in response.text


def test_at_most_two_exports_run_at_once(auth_client, user):
    settings_routes._start_export(1001)
    settings_routes._start_export(1002)

    assert export(auth_client).status_code == 429


def test_photo_path_is_not_in_the_file(auth_client, session, user):
    fill_account(session, user, photo_path="photos/abc.jpg")

    assert "photos/abc.jpg" not in export(auth_client).text


def test_a_failure_midway_leaves_a_broken_file_and_frees_the_slot(
    auth_client, session, user, monkeypatch
):
    fill_account(session, user)

    def broken(*args):
        yield '{"format": "unfurl-export"'
        raise RuntimeError("database gone")

    monkeypatch.setattr(export_service, "stream", broken)

    response = export(auth_client)

    with pytest.raises(json.JSONDecodeError):
        json.loads(response.content)
    assert user.id not in settings_routes._exports_running


def test_a_stuck_export_does_not_block_for_ever(auth_client, user, monkeypatch):
    settings_routes._start_export(user.id)
    monkeypatch.setattr(settings_routes, "EXPORT_STALE_SECONDS", 0)

    assert export(auth_client).status_code == 200


def test_a_finished_export_frees_the_slot(auth_client, user):
    export(auth_client)

    assert user.id not in settings_routes._exports_running


def test_it_needs_a_csrf_token(auth_client):
    assert auth_client.post("/settings/export").status_code == 403


def test_it_needs_a_login(client, user):
    response = client.post(
        "/settings/export", data={"csrf_token": csrf_token(client)}, follow_redirects=False
    )

    assert response.status_code == 303
    assert USER_PASSWORD not in response.text


def test_get_is_not_allowed(auth_client):
    assert auth_client.get("/settings/export").status_code == 405


def test_it_is_rate_limited(auth_client, user):
    token = csrf_token(auth_client)
    codes = [export(auth_client, token).status_code for _ in range(6)]

    assert codes[:5] == [200] * 5 and codes[5] == 429
