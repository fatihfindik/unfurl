"""Cover letters: master, copies, versions and locks (SPEC §5.4, D-152…D-154).

The master / copy / version rules are the same as for CVs and live in
app/services/lineage.py (D-153); this module adds what is special to letters.
Routes change letters only through these functions (CLAUDE.md: never edit a locked
version, a master only after "Edit master").
"""

from fastapi import HTTPException
from sqlmodel import Session, or_, select

from app.i18n.document_labels import labels
from app.models import CoverLetter, User
from app.models.base import utcnow
from app.models.profile import SHORT_TEXT
from app.schemas.cover_letter import LETTER_FIELDS, CoverLetterForm
from app.services import lineage, profile_service

# The errors, shared with CVs (routes catch cover_letter_service.DocumentLocked …).
from app.services.lineage import (  # noqa: F401  (re-exported)
    DocumentLocked,
    MasterHasCopies,
    MasterNotUnlocked,
    NothingChanged,
    TooManyDocuments,
)

MAX_LETTERS_PER_USER = 100
MAX_ID = profile_service.MAX_ID
SENDER_FIELDS = ("sender_name", "sender_email", "sender_phone", "sender_address")


# --- Reading ------------------------------------------------------------------------


def get_owned_letter(session: Session, user: User, letter_id) -> CoverLetter:
    """The letter `letter_id` if it belongs to `user`, otherwise 404. Another user's
    letter, a missing one and an id that isn't a number all give the same answer."""
    try:
        number = int(letter_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404) from None
    if not 0 < number <= MAX_ID:
        raise HTTPException(status_code=404)
    letter = session.exec(
        select(CoverLetter).where(CoverLetter.id == number, CoverLetter.user_id == user.id)
    ).first()
    if letter is None:
        raise HTTPException(status_code=404)
    return letter


def list_letters(session: Session, user: User) -> list[CoverLetter]:
    """Masters first (by language), then copies by master and version."""
    return list(
        session.exec(
            select(CoverLetter)
            .where(CoverLetter.user_id == user.id)
            .order_by(
                CoverLetter.is_master.desc(),
                CoverLetter.language,
                CoverLetter.parent_id,
                CoverLetter.version,
            )
        )
    )


def current_master(session: Session, user: User, language: str) -> CoverLetter | None:
    return lineage.current_master(session, CoverLetter, user, language)


def versions_in_lineage(session: Session, user: User, letter: CoverLetter) -> list[CoverLetter]:
    return lineage.versions_in_lineage(session, CoverLetter, user, letter)


# --- Creating -----------------------------------------------------------------------


def _check_room(session: Session, user: User) -> None:
    # Read at call time: tests lower MAX_LETTERS_PER_USER.
    lineage.check_room(session, CoverLetter, user, MAX_LETTERS_PER_USER)


def sender_from_profile(session: Session, user: User) -> tuple[dict, int]:
    """The sender lines from the profile, and the profile version they come from.
    The address starts with the profile's location (a city); the user may add the
    street in the letter (D-152)."""
    profile = profile_service.get_or_create_profile(session, user)
    sender = {
        "sender_name": user.full_name,
        "sender_email": profile.email,
        "sender_phone": profile.phone,
        "sender_address": profile.location,
    }
    return sender, profile.version


def _content_of(letter: CoverLetter) -> dict:
    """Everything a copy takes over from `letter`, except the title."""
    return {name: getattr(letter, name) for name in LETTER_FIELDS if name != "title"}


def create_letter(session: Session, user: User, title: str, language: str) -> CoverLetter:
    """A new letter. The first one in a language becomes the master: sender from the
    profile, salutation and closing in that language, subject and body empty (D-154).
    Later ones are copies of that master, with all of its text."""
    _check_room(session, user)
    sender, profile_version = sender_from_profile(session, user)
    words = labels(language)
    fields = {
        **sender,
        "place": sender["sender_address"],
        "salutation": words["letter_salutation"],
        "closing": words["letter_closing"],
    }
    master = current_master(session, user, language)
    if master is not None:
        fields = _content_of(master)
        profile_version = master.source_profile_version
    fields |= {
        "user_id": user.id,
        "title": title,
        "language": language,
        "source_profile_version": profile_version,
    }
    return lineage.create_master_or_copy(session, CoverLetter, user, fields)


def _copy_of(source: CoverLetter) -> CoverLetter:
    """A new draft with the same text as `source`, under its master."""
    return lineage.stamped(
        CoverLetter(
            **_content_of(source),
            user_id=source.user_id,
            title=source.title,
            language=source.language,
            source_profile_version=source.source_profile_version,
            parent_id=source.id if source.is_master else source.parent_id,
        )
    )


def save_as_new_version(session: Session, user: User, source: CoverLetter) -> CoverLetter:
    """ "Save as new version" (draft) and "Duplicate as new version" (locked): the same
    text as the next version under the same master (D-050 "Nothing changed")."""
    lineage.check_new_version_allowed(source)
    _check_room(session, user)
    return lineage.save_copy(session, CoverLetter, _copy_of(source))


# --- Applications (D-157, D-158) ---------------------------------------------------


def subject_for(application, language: str) -> str:
    """ "Bewerbung als {job title}" in the letter's language, cut to fit 200 characters
    (a job title can be 200 characters long itself, plan-reviewer M8b #1)."""
    pattern = labels(language)["letter_subject"]
    room = SHORT_TEXT - len(pattern.replace("{job_title}", ""))
    return pattern.replace("{job_title}", application.job_title[:room])


def copy_for_application(
    session: Session, user: User, master: CoverLetter, application, title: str
) -> CoverLetter:
    """A new copy of `master` made for `application`; the caller links it (D-157).
    Fields the master left empty are filled from the job (D-158): company, contact
    person and subject. They are set before the copy is saved, so it still counts
    as never edited and the Undo of "Copy my master letter" can delete it."""
    if not master.is_master or master.user_id != user.id:
        raise ValueError("Copies are made from the user's own master.")
    _check_room(session, user)
    copy = _copy_of(master)
    copy.title = title[:SHORT_TEXT]
    copy.application_id = application.id
    if not copy.recipient_company:
        copy.recipient_company = application.company
    if not copy.recipient_name and application.contact_name:
        copy.recipient_name = application.contact_name
    if not copy.subject:
        copy.subject = subject_for(application, copy.language)
    return lineage.save_copy(session, CoverLetter, copy)


def copies_for_linking(session: Session, user: User, application) -> list[CoverLetter]:
    """Drafts that may be used for `application`: the user's copies (never a master,
    never a sent one) that belong to no other application."""
    return list(
        session.exec(
            select(CoverLetter)
            .where(
                CoverLetter.user_id == user.id,
                CoverLetter.is_master.is_(False),
                CoverLetter.status == "draft",
                or_(
                    CoverLetter.application_id.is_(None),
                    CoverLetter.application_id == application.id,
                ),
            )
            .order_by(CoverLetter.language, CoverLetter.parent_id, CoverLetter.version)
        )
    )


# --- Changing -----------------------------------------------------------------------


def update_letter(
    session: Session, letter: CoverLetter, form: CoverLetterForm, *, master_unlocked: bool = False
) -> CoverLetter:
    """Save the editor's fields. Only this letter changes: never its master, never
    the copies of a master. `master_unlocked`: only the editor routes may pass True
    (the user clicked "Edit master" and confirmed, D-048)."""
    lineage.ensure_changeable(letter, master_unlocked)
    for name, value in form.columns().items():
        setattr(letter, name, value)
    session.add(letter)
    session.commit()
    session.refresh(letter)
    return letter


def refresh_sender(
    session: Session, user: User, letter: CoverLetter, *, master_unlocked: bool = False
) -> CoverLetter:
    """ "Refresh from profile": only the sender lines, from the profile now (the user
    confirmed that their own sender lines are overwritten, D-154)."""
    lineage.ensure_changeable(letter, master_unlocked)
    sender, profile_version = sender_from_profile(session, user)
    for name, value in sender.items():
        setattr(letter, name, value)
    letter.source_profile_version = profile_version
    session.add(letter)
    session.commit()
    session.refresh(letter)
    return letter


def delete_letter(session: Session, letter: CoverLetter, user: User) -> int:
    """Delete a draft, keeping a short-lived copy for Undo (D-086). Locked letters
    can't be deleted; a master only when it has no copies (D-047).
    Returns the copy's id for the Undo token."""
    from app.services import trash_service

    lineage.ensure_deletable(session, CoverLetter, letter)
    copy = trash_service.remember(session, user, "cover_letter", trash_service.payload_of(letter))
    session.delete(letter)
    session.commit()
    return copy.id


def restore_letter(session: Session, user: User, payload: dict) -> CoverLetter:
    """Undo of a delete: the letter comes back as a new row, if it still fits (a
    master only while its language has no master again, a copy only while its
    master exists; D-086, D-153)."""
    from app.services import trash_service

    if payload.get("user_id") != user.id:
        raise trash_service.CannotRestore
    letter = trash_service.row_from(CoverLetter, payload)
    if not lineage.fits_for_restore(session, CoverLetter, user, letter):
        raise trash_service.CannotRestore
    try:
        _check_room(session, user)
    except TooManyDocuments:
        raise trash_service.CannotRestore from None
    session.add(letter)
    session.flush()
    return letter


def lock(session: Session, letter: CoverLetter, *, commit: bool = True) -> CoverLetter:
    """Lock a copy for good: it was sent with an application (M8b). Masters are
    never locked: only copies are sent (SPEC §5.3)."""
    if letter.is_master:
        raise ValueError("A master letter is never locked; lock the copy that was sent.")
    if not letter.is_locked:
        letter.status = "locked"
        letter.locked_at = utcnow()
        session.add(letter)
        if commit:
            session.commit()
            session.refresh(letter)
    return letter
