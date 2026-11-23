"""Cover letters: master, copies, versions and locks (SPEC §5.4, D-152…D-154).

The master / copy / version rules are the same as for CVs and live in
app/services/lineage.py (D-153); this module adds what is special to letters.
Routes change letters only through these functions (CLAUDE.md: never edit a locked
version, a master only after "Edit master").
"""

from fastapi import HTTPException
from sqlmodel import Session, or_, select

from app.i18n.document_labels import full_date, labels
from app.models import Application, ApplicationDocument, CoverLetter, User
from app.models.base import as_utc, utcnow
from app.models.profile import SHORT_TEXT
from app.schemas.cover_letter import LETTER_FIELDS, CoverLetterForm
from app.services import clock, lineage, profile_service

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
            # A new version stays with the application it was made for (D-157).
            application_id=source.application_id,
        )
    )


def save_as_new_version(session: Session, user: User, source: CoverLetter) -> CoverLetter:
    """ "Save as new version" (draft) and "Duplicate as new version" (locked): the same
    text as the next version under the same master (D-050 "Nothing changed")."""
    lineage.check_new_version_allowed(source)
    _check_room(session, user)
    return lineage.save_copy(session, CoverLetter, _copy_of(source))


def duplicate(session: Session, user: User, source: CoverLetter) -> CoverLetter:
    """ "Duplicate": the same text as a separate copy "… (copy)", for another
    application, so it belongs to none (D-050, D-162)."""
    _check_room(session, user)
    copy = _copy_of(source)
    # "… (copy)" once, not "… (copy) (copy)"; shortened if needed to fit 200 characters.
    if not source.title.endswith(" (copy)"):
        copy.title = f"{source.title[: SHORT_TEXT - len(' (copy)')]} (copy)"
    copy.application_id = None
    return lineage.save_copy(session, CoverLetter, copy)


def plain_text(letter: CoverLetter, paragraphs: list[str]) -> str:
    """ "Copy text" (D-162): what goes into an application form's message box, as plain
    text: salutation, the paragraphs, closing and the name. No addresses, no date."""
    parts = [letter.salutation, *paragraphs]
    ending = "\n".join(line for line in (letter.closing, letter.sender_name) if line)
    return "\n\n".join(part for part in (*parts, ending) if part)


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
    payload = trash_service.payload_of(letter)
    used_by = session.exec(
        select(Application.id).where(
            Application.cover_letter_id == letter.id, Application.user_id == user.id
        )
    ).first()
    payload["_used_by"] = used_by  # to give the application its letter back on Undo (D-163)
    copy = trash_service.remember(session, user, "cover_letter", payload)
    session.delete(letter)
    session.commit()
    return copy.id


def restore_letter(session: Session, user: User, payload: dict) -> CoverLetter:
    """Undo of a delete: the letter comes back as a new row, if it still fits (a
    master only while its language has no master again, a copy only while its
    master exists; D-086, D-153). A linked letter goes back to its application under
    the same three conditions as a CV (D-163)."""
    from app.services import trash_service

    if payload.get("user_id") != user.id:
        raise trash_service.CannotRestore
    letter = trash_service.row_from(CoverLetter, payload)
    if not lineage.fits_for_restore(session, CoverLetter, user, letter):
        raise trash_service.CannotRestore
    if letter.application_id is not None:
        owner = session.get(Application, letter.application_id)
        if owner is None or owner.user_id != user.id:
            letter.application_id = None
    try:
        _check_room(session, user)
    except TooManyDocuments:
        raise trash_service.CannotRestore from None
    session.add(letter)
    session.flush()
    used_by = payload.get("_used_by")
    if type(used_by) is int:
        application = session.get(Application, used_by)
        # Only into an empty place, and not into a job that was sent meanwhile.
        if (
            application is not None
            and application.user_id == user.id
            and application.cover_letter_id is None
            and application.status == "saved"
        ):
            application.cover_letter_id = letter.id
            session.add(application)
    return letter


def lock(session: Session, letter: CoverLetter, *, commit: bool = True) -> CoverLetter:
    """Lock a copy for good: it was sent with an application (D-159). Masters are
    never locked: only copies are sent (SPEC §5.3). commit=False: part of a bigger
    change (status + locks + sent copies in one transaction)."""
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


def unlock_after_undo(session: Session, letter: CoverLetter, locked_at) -> bool:
    """The only way back from "locked" (D-082, D-159): Undo of the action that locked
    it, within its minute. The text never changes; only the status goes back to draft,
    and only if the lock is the one that action made (same locked_at)."""
    if not letter.is_locked or letter.locked_at is None:
        return False
    if as_utc(letter.locked_at).isoformat() != locked_at:
        return False
    letter.status = "draft"
    letter.locked_at = None
    session.add(letter)
    return True


# --- The date line (D-154, D-160) ------------------------------------------------------


def letter_date(session: Session, letter: CoverLetter) -> str:
    """The date line: today where the user is while the letter is a draft; once sent,
    the date it was sent. That is read from the copy kept when it was sent (exactly
    the text that went out), else from locked_at in the user's time zone."""
    if not letter.is_locked:
        return full_date(clock.today(), letter.language)
    sent = session.exec(
        select(ApplicationDocument)
        .where(
            ApplicationDocument.user_id == letter.user_id,
            ApplicationDocument.cover_letter_id == letter.id,
        )
        .order_by(ApplicationDocument.id.desc())
    ).first()
    if sent is not None and isinstance(sent.content_snapshot.get("date"), str):
        return sent.content_snapshot["date"]
    day = clock.local(letter.locked_at).date() if letter.locked_at else clock.today()
    return full_date(day, letter.language)


def snapshot(letter: CoverLetter) -> dict:
    """What was sent (SPEC §5.4b): every field of the letter and its date line. Called
    right after locking, so the date is the day it was sent."""
    fields = {name: getattr(letter, name) for name in LETTER_FIELDS}
    day = clock.local(letter.locked_at).date() if letter.locked_at else clock.today()
    return fields | {"language": letter.language, "date": full_date(day, letter.language)}
