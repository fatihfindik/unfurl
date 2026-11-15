"""Reading and changing a user's profile (SPEC §5.2, §6.4, D-032).

Every query on profile rows goes through this module, and every one of them checks
the owner: section rows have no user_id, so they are joined to the profile and
filtered by `Profile.user_id` (CLAUDE.md: every query on user data filters by user).

Every change also raises `Profile.version` by 1 (`_touch`), so cached AI results can
tell that the profile changed (SPEC §5.2).
"""

from fastapi import HTTPException
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from app.models import Profile, User
from app.schemas.profile import PersonalInfoForm, SummaryForm

MAX_ITEMS_PER_SECTION = 50
# Database ids are 32-bit numbers. A bigger number in a URL can't be an id, and
# Postgres would raise an error for it instead of simply finding nothing.
MAX_ID = 2**31 - 1


class TooManyItems(Exception):
    """A section already has MAX_ITEMS_PER_SECTION rows."""


def get_or_create_profile(session: Session, user: User) -> Profile:
    """The user's profile; an empty one is created on the first visit (D-032)."""
    profile = session.exec(select(Profile).where(Profile.user_id == user.id)).first()
    if profile is not None:
        return profile

    # The profile email starts as the account email; the user can change it.
    profile = Profile(user_id=user.id, email=user.email)
    session.add(profile)
    try:
        session.commit()
    except IntegrityError:
        # Two first requests at the same moment: the unique user_id lets only one
        # profile in. Use the one that was saved.
        session.rollback()
        return session.exec(select(Profile).where(Profile.user_id == user.id)).one()
    session.refresh(profile)
    return profile


def get_owned_item(session: Session, user: User, model: type[SQLModel], item_id) -> SQLModel:
    """The row `item_id` of a section table if it belongs to `user`, otherwise 404.

    Another user's row and a row that doesn't exist give the same answer, so nobody
    can find out which ids exist (SPEC §10.1). `item_id` comes from the URL as text;
    anything that isn't a valid id is a 404 too (not a 422 validation error).
    """
    try:
        number = int(item_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404) from None
    if not 0 < number <= MAX_ID:
        raise HTTPException(status_code=404)

    item = session.exec(
        select(model)
        .join(Profile, model.profile_id == Profile.id)
        .where(model.id == number, Profile.user_id == user.id)
    ).first()
    if item is None:
        raise HTTPException(status_code=404)
    return item


def list_items(session: Session, profile: Profile, model: type[SQLModel]) -> list:
    """A section's rows in the order the user sees them."""
    return list(
        session.exec(
            select(model).where(model.profile_id == profile.id).order_by(model.sort_order, model.id)
        )
    )


def _touch(session: Session, profile_id: int) -> None:
    """Raise the profile's version by 1 (not committed yet).

    Done in SQL (`version = version + 1`), so two changes at the same moment both count.
    """
    session.exec(
        update(Profile).where(Profile.id == profile_id).values(version=Profile.version + 1)
    )


def add_item(session: Session, profile: Profile, model: type[SQLModel], data: dict) -> SQLModel:
    """Add a row at the end of a section. Raises TooManyItems when the section is full."""
    count = session.exec(select(func.count()).where(model.profile_id == profile.id)).one()
    if count >= MAX_ITEMS_PER_SECTION:
        raise TooManyItems
    last = session.exec(
        select(func.max(model.sort_order)).where(model.profile_id == profile.id)
    ).one()

    item = model(profile_id=profile.id, sort_order=(last or 0) + 1, **data)
    session.add(item)
    _touch(session, profile.id)
    session.commit()
    session.refresh(item)
    return item


def update_item(session: Session, item: SQLModel, data: dict) -> SQLModel:
    """Change a row that get_owned_item returned."""
    for name, value in data.items():
        # Lists (bullets) arrive as new list objects, so SQLAlchemy sees the change.
        setattr(item, name, value)
    session.add(item)
    _touch(session, item.profile_id)
    session.commit()
    session.refresh(item)
    return item


def delete_item(session: Session, item: SQLModel) -> None:
    """Delete a row that get_owned_item returned. No Undo until M4 (D-035)."""
    profile_id = item.profile_id
    session.delete(item)
    _touch(session, profile_id)
    session.commit()


def update_personal_info(
    session: Session, user: User, profile: Profile, form: PersonalInfoForm
) -> None:
    user.full_name = form.full_name  # the name lives on the account (also used in the menu)
    for name, value in form.model_dump(exclude={"full_name"}).items():
        setattr(profile, name, value)
    session.add_all([user, profile])
    _touch(session, profile.id)
    session.commit()
    session.refresh(profile)


def update_summary(session: Session, profile: Profile, form: SummaryForm) -> None:
    profile.summary = form.summary
    session.add(profile)
    _touch(session, profile.id)
    session.commit()
    session.refresh(profile)
