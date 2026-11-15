"""CVDocument table rules (SPEC §5.3, D-043, D-045, D-047)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.models import CVDocument
from app.models.base import as_utc
from app.schemas.cv import CVContent, CVPersonal
from app.services.auth_service import create_user


def make_cv(session, user, **values) -> CVDocument:
    fields = {"title": "CV", "language": "en", "content": CVContent().to_json()} | values
    cv = CVDocument(user_id=user.id, **fields)
    session.add(cv)
    session.commit()
    session.refresh(cv)
    return cv


def test_one_master_per_user_and_language(session, user):
    make_cv(session, user, is_master=True)

    with pytest.raises(IntegrityError):
        make_cv(session, user, is_master=True)
    session.rollback()


def test_masters_in_other_languages_and_for_other_users_are_fine(session, user):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    make_cv(session, user, is_master=True, language="en")
    make_cv(session, user, is_master=True, language="de")
    make_cv(session, other, is_master=True, language="en")

    assert len(session.exec(select(CVDocument).where(CVDocument.is_master)).all()) == 3


def test_many_copies_per_language_are_fine(session, user):
    master = make_cv(session, user, is_master=True)
    make_cv(session, user, parent_id=master.id, version=1)
    make_cv(session, user, parent_id=master.id, version=2)

    assert len(session.exec(select(CVDocument)).all()) == 3


def test_a_version_number_is_used_once_per_master(session, user):
    master = make_cv(session, user, is_master=True)
    make_cv(session, user, parent_id=master.id, version=1)

    with pytest.raises(IntegrityError):
        make_cv(session, user, parent_id=master.id, version=1)
    session.rollback()


def test_parent_is_set_to_null_if_the_master_row_goes(session, user):
    # The app never deletes a master with copies (D-047); the database is the safety net.
    master = make_cv(session, user, is_master=True)
    copy = make_cv(session, user, parent_id=master.id, version=1)

    session.exec(text("DELETE FROM cvdocument WHERE id = :id").bindparams(id=master.id))
    session.commit()
    session.expire_all()

    assert session.get(CVDocument, copy.id).parent_id is None


def test_deleting_the_user_deletes_their_cvs(session, user):
    make_cv(session, user, is_master=True)

    session.delete(user)
    session.commit()

    assert session.exec(select(CVDocument)).all() == []


def test_content_goes_in_and_out_as_json(session, user):
    content = CVContent(personal=CVPersonal(full_name="Jana"), summary="Hello")
    cv = make_cv(session, user, content=content.to_json())
    session.expire_all()

    loaded = CVContent.model_validate(session.get(CVDocument, cv.id).content)
    assert loaded.personal.full_name == "Jana"
    assert loaded.summary == "Hello"
    assert loaded.schema_version == 1


def test_old_content_without_new_fields_still_loads(session, user):
    # A CV saved before a field existed gets the default value (D-043).
    cv = make_cv(session, user, content={"personal": {"full_name": "Jana"}})

    loaded = CVContent.model_validate(cv.content)
    assert loaded.hidden_sections == []
    assert loaded.experience == []


def test_dates_from_sqlite_are_read_as_utc(session, user):
    cv = make_cv(session, user)
    session.expire_all()
    stored = session.get(CVDocument, cv.id).updated_at

    assert as_utc(stored).tzinfo == UTC
    assert as_utc(stored) <= datetime.now(UTC)


def test_new_documents_are_drafts_with_the_classic_template(session, user):
    cv = make_cv(session, user)

    assert cv.status == "draft"
    assert not cv.is_locked
    assert cv.template == "classic"
    assert cv.version == 0
