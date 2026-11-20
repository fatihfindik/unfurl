"""Deleting an account (D-131, SPEC §10.2).

Every table of the user has ON DELETE CASCADE (D-034), so deleting the user row
deletes everything else in the same transaction. Files are not in the database:
the profile photo is deleted after the commit.
"""

import logging
from dataclasses import dataclass

from sqlmodel import Session, delete, select

from app.models import Profile, User
from app.security import login_lockout
from app.services import photo_service

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeletedAccount:
    """What is still needed after the user row is gone: where the confirmation
    email goes (only if the address was verified, D-131)."""

    email: str
    full_name: str
    verified: bool


def delete_account(session: Session, user: User) -> DeletedAccount | None:
    """Delete `user` and everything of theirs, for good. There is no Undo. None if
    another request deleted the account a moment earlier (a double click): then
    there is nothing left to do, and no second confirmation email."""
    gone = DeletedAccount(email=user.email, full_name=user.full_name, verified=user.is_verified)
    user_id = user.id  # read now: the object is expired after the commit
    photo_path = session.exec(select(Profile.photo_path).where(Profile.user_id == user.id)).first()

    result = session.exec(delete(User).where(User.id == user.id))
    session.commit()
    if result.rowcount != 1:
        return None

    # After the commit: if it failed, the photo is still needed.
    try:
        photo_service.delete_photo_file(photo_path)
    except OSError:
        # The data is gone already; a file left behind is logged, not shown.
        logger.exception("Could not delete a photo file of a deleted account")
    login_lockout.clear_email(gone.email, user_id)
    return gone
