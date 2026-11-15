"""Profile tables (SPEC §5.2): one profile per user, cascading deletes, JSON bullets."""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.models import Experience, Profile, Skill, User


def make_profile(session, user) -> Profile:
    profile = Profile(user_id=user.id, email=user.email)
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


def test_a_user_has_at_most_one_profile(session, user):
    make_profile(session, user)
    session.add(Profile(user_id=user.id))
    with pytest.raises(IntegrityError):
        session.commit()


def test_new_profile_starts_at_version_0(session, user):
    assert make_profile(session, user).version == 0


def test_bullets_are_stored_as_a_list(session, user):
    profile = make_profile(session, user)
    session.add(
        Experience(
            profile_id=profile.id,
            company="Baufeld Software",
            title="Senior Product Manager",
            start_date=date(2021, 3, 1),
            bullets=["Led the rebuild of the field app.", "Owned pricing."],
        )
    )
    session.commit()
    session.expire_all()  # read it back from the database, not from memory

    experience = session.exec(select(Experience)).one()
    assert experience.bullets == ["Led the rebuild of the field app.", "Owned pricing."]
    assert experience.end_date is None  # still working there


def test_foreign_keys_are_checked_on_sqlite(session):
    # Without PRAGMA foreign_keys=ON (app/db.py, D-034) SQLite would accept this row.
    session.add(Skill(profile_id=999, name="SQL"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_deleting_a_user_deletes_their_profile_and_its_rows(session, user):
    profile = make_profile(session, user)
    session.add(Skill(profile_id=profile.id, name="SQL"))
    session.add(
        Experience(profile_id=profile.id, company="A", title="B", start_date=date(2020, 1, 1))
    )
    session.commit()

    session.delete(session.get(User, user.id))
    session.commit()

    assert session.exec(select(Profile)).all() == []
    assert session.exec(select(Skill)).all() == []
    assert session.exec(select(Experience)).all() == []
