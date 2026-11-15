"""Reading and changing a user's profile (SPEC §5.2, §6.4, D-032).

Every query on profile rows goes through this module, and every one of them checks
the owner: section rows have no user_id, so they are joined to the profile and
filtered by `Profile.user_id` (CLAUDE.md: every query on user data filters by user).

Every change also raises `Profile.version` by 1 (`_touch`), so cached AI results can
tell that the profile changed (SPEC §5.2).
"""

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from app.models import (
    Certification,
    Education,
    Experience,
    LanguageSkill,
    Profile,
    Project,
    Skill,
    User,
)
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


def _write_order(session: Session, items: list) -> None:
    """Number the rows 1, 2, 3, ... in the given order (not committed yet)."""
    for number, row in enumerate(items, start=1):
        row.sort_order = number
        session.add(row)


def move_item(
    session: Session, profile: Profile, model: type[SQLModel], item: SQLModel, direction: str
) -> bool:
    """Move a row one place up or down (the ↑ / ↓ buttons, SPEC §6.4).

    Returns False when nothing changed: the first row can't go up, the last row
    can't go down, and an unknown direction is ignored. Then the version stays the same.
    """
    if direction not in ("up", "down"):
        return False
    items = list_items(session, profile, model)
    index = next(i for i, row in enumerate(items) if row.id == item.id)
    other = index - 1 if direction == "up" else index + 1
    if not 0 <= other < len(items):
        return False

    # Swap the two rows in the list, then number the whole list again. Swapping only
    # their sort_order would do nothing if two rows had the same number (two rows
    # added at the same moment can get the same max + 1).
    items[index], items[other] = items[other], items[index]
    _write_order(session, items)
    _touch(session, profile.id)
    session.commit()
    return True


def reorder_items(
    session: Session, profile: Profile, model: type[SQLModel], ids: list[int]
) -> bool:
    """Save a new order for a whole section (drag and drop, SPEC §6.4).

    `ids` is the full list of the section's row ids in the new order. An id that is not
    one of the user's rows (another user's, or one that doesn't exist) is a 404 and
    nothing changes. If the list doesn't match the rows (one missing or twice: the page
    was out of date) or the order is the same, nothing changes and False is returned.
    """
    items = list_items(session, profile, model)
    by_id = {row.id: row for row in items}
    if any(item_id not in by_id for item_id in ids):
        raise HTTPException(status_code=404)
    if sorted(ids) != sorted(by_id) or ids == [row.id for row in items]:
        return False

    _write_order(session, [by_id[item_id] for item_id in ids])
    _touch(session, profile.id)
    session.commit()
    return True


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


def set_photo(session: Session, profile: Profile, photo_path: str | None) -> str | None:
    """Store a new photo path (None removes the photo). Returns the old path, so the
    caller can delete the old file once the new value is saved."""
    old = profile.photo_path
    profile.photo_path = photo_path
    session.add(profile)
    _touch(session, profile.id)  # CV copies (M3) use the photo, so it is a change
    session.commit()
    session.refresh(profile)
    return old


def update_summary(session: Session, profile: Profile, form: SummaryForm) -> None:
    profile.summary = form.summary
    session.add(profile)
    _touch(session, profile.id)
    session.commit()
    session.refresh(profile)


# --- Profile strength (SPEC §6.4) -------------------------------------------------


@dataclass(frozen=True)
class Strength:
    percent: int  # 0, 10, ..., 100
    hint: str | None  # what to add next, e.g. "Add a project to reach 100%."; None at 100%


def _count(session: Session, profile: Profile, model: type[SQLModel]) -> int:
    return session.exec(select(func.count()).where(model.profile_id == profile.id)).one()


def profile_strength(session: Session, profile: Profile) -> Strength:
    """How complete the profile is: 10 items, 10% each, in the order of SPEC §6.4.

    The hint names the first missing item. Photo, date of birth and nationality don't
    count (they are optional). Also used by the Documents card in M3.
    """
    experiences = list_items(session, profile, Experience)
    checks = [
        (bool(profile.headline), "Add a headline"),
        (bool(profile.summary), "Add a summary"),
        (
            bool(profile.email) and bool(profile.phone or profile.location),
            "Add your email and a phone number or location",
        ),
        (len(experiences) >= 1, "Add an experience"),
        (any(experience.bullets for experience in experiences), "Add a bullet to an experience"),
        (_count(session, profile, Education) >= 1, "Add an education"),
        (_count(session, profile, Skill) >= 5, "Add at least 5 skills"),
        (_count(session, profile, LanguageSkill) >= 1, "Add a language"),
        (_count(session, profile, Certification) >= 1, "Add a certification"),
        (_count(session, profile, Project) >= 1, "Add a project"),
    ]
    percent = 10 * sum(1 for done, _ in checks if done)
    missing = [action for done, action in checks if not done]
    hint = f"{missing[0]} to reach {percent + 10}%." if missing else None
    return Strength(percent=percent, hint=hint)
