"""CV documents: snapshot, master, versions and locks (SPEC §5.3, D-043…D-048).

The rules for masters and locked documents live only here (CLAUDE.md). Routes and
the assistant (M12) change documents through these functions, never directly.
"""

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, or_, select

from app.models import (
    Certification,
    CVDocument,
    Education,
    Experience,
    LanguageSkill,
    Project,
    Skill,
    User,
)
from app.models.base import as_utc, utcnow
from app.models.profile import SHORT_TEXT
from app.schemas.cv import (
    CVBullet,
    CVCertification,
    CVContent,
    CVEducation,
    CVExperience,
    CVLanguage,
    CVPersonal,
    CVProject,
    CVSkill,
)
from app.services import photo_service, profile_service

MAX_CVS_PER_USER = 100
MAX_ID = profile_service.MAX_ID


class DocumentLocked(Exception):
    """The document is locked (sent with an application) and can never change."""


class MasterNotUnlocked(Exception):
    """A master is changed only after the user unlocks it in the editor (D-048)."""


class MasterHasCopies(Exception):
    """A master with copies can't be deleted (D-047)."""

    def __init__(self, copies: int):
        super().__init__(f"{copies} copies")
        self.copies = copies


class TooManyDocuments(Exception):
    """The user already has MAX_CVS_PER_USER documents."""


class NothingChanged(Exception):
    """A new version would be the same as the draft it comes from."""


def snapshot_from_profile(session: Session, user: User) -> tuple[CVContent, int]:
    """A copy of the user's profile as CV content, and the profile version it was
    taken from. Sections keep the order the user gave them in the profile."""
    profile = profile_service.get_or_create_profile(session, user)

    def rows(model):
        return profile_service.list_items(session, profile, model)

    content = CVContent(
        personal=CVPersonal(
            full_name=user.full_name,
            headline=profile.headline,
            email=profile.email,
            phone=profile.phone,
            location=profile.location,
            website=profile.website,
            linkedin_url=profile.linkedin_url,
            github_url=profile.github_url,
        ),
        summary=profile.summary,
        experience=[
            CVExperience(
                title=row.title,
                company=row.company,
                location=row.location,
                start_date=row.start_date,
                end_date=row.end_date,
                description=row.description,
                bullets=[CVBullet(text=text) for text in row.bullets],
            )
            for row in rows(Experience)
        ],
        education=[
            CVEducation(
                institution=row.institution,
                degree=row.degree,
                field=row.field,
                location=row.location,
                start_date=row.start_date,
                end_date=row.end_date,
                description=row.description,
            )
            for row in rows(Education)
        ],
        skills=[
            CVSkill(name=row.name, category=row.category, level=row.level) for row in rows(Skill)
        ],
        languages=[
            CVLanguage(language=row.language, level=row.level) for row in rows(LanguageSkill)
        ],
        certifications=[
            CVCertification(name=row.name, issuer=row.issuer, date=row.date, url=row.url)
            for row in rows(Certification)
        ],
        projects=[
            CVProject(name=row.name, description=row.description, url=row.url)
            for row in rows(Project)
        ],
    )
    return content, profile.version


# --- Reading ------------------------------------------------------------------------


def get_owned_cv(session: Session, user: User, cv_id) -> CVDocument:
    """The CV `cv_id` if it belongs to `user`, otherwise 404.

    Another user's CV and a CV that doesn't exist give the same answer (SPEC §10.1).
    `cv_id` comes from the URL as text; anything that isn't a valid id is a 404 too.
    """
    try:
        number = int(cv_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404) from None
    if not 0 < number <= MAX_ID:
        raise HTTPException(status_code=404)
    cv = session.exec(
        select(CVDocument).where(CVDocument.id == number, CVDocument.user_id == user.id)
    ).first()
    if cv is None:
        raise HTTPException(status_code=404)
    return cv


def list_cvs(session: Session, user: User) -> list[CVDocument]:
    """All of the user's CVs: masters first (by language), then copies by master and version."""
    return list(
        session.exec(
            select(CVDocument)
            .where(CVDocument.user_id == user.id)
            .order_by(
                CVDocument.is_master.desc(),
                CVDocument.language,
                CVDocument.parent_id,
                CVDocument.version,
            )
        )
    )


def current_master(session: Session, user: User, language: str) -> CVDocument | None:
    return session.exec(
        select(CVDocument).where(
            CVDocument.user_id == user.id,
            CVDocument.language == language,
            CVDocument.is_master,
        )
    ).first()


def versions_in_lineage(session: Session, user: User, cv: CVDocument) -> list[CVDocument]:
    """The master and all its copies, for the version picker (master first)."""
    master_id = cv.id if cv.is_master else cv.parent_id
    if master_id is None:
        return [cv]
    return list(
        session.exec(
            select(CVDocument)
            .where(
                CVDocument.user_id == user.id,
                or_(CVDocument.id == master_id, CVDocument.parent_id == master_id),
            )
            .order_by(CVDocument.version)
        )
    )


def copy_count(session: Session, master: CVDocument) -> int:
    return session.exec(
        select(func.count()).where(
            CVDocument.parent_id == master.id, CVDocument.user_id == master.user_id
        )
    ).one()


# --- Creating -----------------------------------------------------------------------


def _check_room(session: Session, user: User) -> None:
    count = session.exec(select(func.count()).where(CVDocument.user_id == user.id)).one()
    if count >= MAX_CVS_PER_USER:
        raise TooManyDocuments


def _next_version(session: Session, user_id: int, master_id: int | None) -> int:
    """The next free number under a master: the highest one + 1 (D-045)."""
    highest = session.exec(
        select(func.max(CVDocument.version)).where(
            CVDocument.parent_id == master_id, CVDocument.user_id == user_id
        )
    ).one()
    return (highest or 0) + 1


def _save_copy(session: Session, copy: CVDocument) -> CVDocument:
    """Save a new copy with the next version number. If another request took the same
    number at the same moment, the unique index refuses it; try once more."""
    for attempt in range(2):
        copy.version = _next_version(session, copy.user_id, copy.parent_id)
        session.add(copy)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            if attempt == 1:
                raise
            continue
        session.refresh(copy)
        return copy
    raise AssertionError("unreachable")


def create_cv(session: Session, user: User, title: str, language: str, template: str) -> CVDocument:
    """A new CV from the profile. The first CV in a language becomes the master;
    later ones are copies under that master (D-045)."""
    _check_room(session, user)
    content, profile_version = snapshot_from_profile(session, user)
    fields = {
        "user_id": user.id,
        "title": title,
        "language": language,
        "template": template,
        "content": content.to_json(),
        "source_profile_version": profile_version,
    }

    master = current_master(session, user, language)
    if master is None:
        cv = _stamped(CVDocument(**fields, is_master=True, version=0))
        session.add(cv)
        try:
            session.commit()
            session.refresh(cv)
            return cv
        except IntegrityError:
            # Two "New CV" clicks at the same moment: the other one became the master.
            session.rollback()
            master = current_master(session, user, language)
            if master is None:
                raise  # some other database error, not the race: don't hide it

    return _save_copy(session, _stamped(CVDocument(**fields, parent_id=master.id)))


def _copy_of(source: CVDocument, title: str) -> CVDocument:
    """A new draft with the same content and settings as `source`, under its master."""
    return _stamped(
        CVDocument(
            user_id=source.user_id,
            title=title,
            language=source.language,
            template=source.template,
            content=dict(source.content),
            show_photo=source.show_photo,
            show_birth_date=source.show_birth_date,
            show_nationality=source.show_nationality,
            source_profile_version=source.source_profile_version,
            parent_id=source.id if source.is_master else source.parent_id,
            # A new version stays with the application it was made for (D-080).
            application_id=source.application_id,
        )
    )


def save_as_new_version(session: Session, user: User, source: CVDocument) -> CVDocument:
    """For "Save as new version" (draft) and "Duplicate as new version" (locked): the
    same content as the next version under the same master. Works on locked documents too:
    that is how a sent CV is changed (into a new draft)."""
    # A draft that was never edited would only give an identical copy (Fatih's test:
    # v8, v9, v10 all the same). A locked copy is different: its new draft is where
    # the changes go, so it is always allowed.
    if not source.is_locked and not source.is_master and never_edited(source):
        raise NothingChanged
    _check_room(session, user)
    return _save_copy(session, _copy_of(source, source.title))


def never_edited(cv: CVDocument) -> bool:
    """True while a document is exactly as it was created. New documents get the same
    created_at and updated_at (_stamped); any save moves updated_at."""
    return as_utc(cv.updated_at) == as_utc(cv.created_at)


def _stamped(cv: CVDocument) -> CVDocument:
    """Give a new document one moment for created_at and updated_at (see never_edited)."""
    now = utcnow()
    cv.created_at = now
    cv.updated_at = now
    return cv


def duplicate(session: Session, user: User, source: CVDocument) -> CVDocument:
    """For "Duplicate": the same content as a separate copy, titled "… (copy)"."""
    _check_room(session, user)
    # "… (copy)" once, not "… (copy) (copy)"; shortened if needed to fit 200 characters.
    if source.title.endswith(" (copy)"):
        title = source.title
    else:
        title = f"{source.title[: SHORT_TEXT - len(' (copy)')]} (copy)"
    copy = _copy_of(source, title)
    copy.application_id = None  # "Duplicate" is a copy for another application (D-050, D-080)
    return _save_copy(session, copy)


# --- Applications (D-080, D-082) -------------------------------------------------------


def copy_for_application(
    session: Session, user: User, master: CVDocument, application, title: str
) -> CVDocument:
    """A new copy of `master` made for `application` (D-080). The caller links it."""
    if not master.is_master or master.user_id != user.id:
        raise ValueError("Copies are made from the user's own master.")
    _check_room(session, user)
    copy = _copy_of(master, title[:SHORT_TEXT])
    copy.application_id = application.id
    return _save_copy(session, copy)


def copies_for_linking(session: Session, user: User, application) -> list[CVDocument]:
    """Drafts that may be used for `application`: the user's copies (never a master,
    never a sent one) that belong to no other application."""
    return list(
        session.exec(
            select(CVDocument)
            .where(
                CVDocument.user_id == user.id,
                CVDocument.is_master.is_(False),
                CVDocument.status == "draft",
                or_(
                    CVDocument.application_id.is_(None),
                    CVDocument.application_id == application.id,
                ),
            )
            .order_by(CVDocument.language, CVDocument.parent_id, CVDocument.version)
        )
    )


def unlock_after_undo(session: Session, cv: CVDocument, locked_at) -> bool:
    """The only way back from "locked" (D-082): Undo of the action that locked it,
    within its minute. The content never changes; only the status goes back to draft,
    and only if the lock is the one that action made (same locked_at)."""
    if not cv.is_locked or cv.locked_at is None:
        return False
    if as_utc(cv.locked_at).isoformat() != locked_at:
        return False
    cv.status = "draft"
    cv.locked_at = None
    session.add(cv)
    return True


# --- Changing -----------------------------------------------------------------------


def _ensure_changeable(cv: CVDocument, master_unlocked: bool) -> None:
    """The rules for changing a document in place (CLAUDE.md, D-048):
    a locked document never changes; a master only after the user unlocked it.

    `master_unlocked` means: the user clicked "Edit master" and confirmed in the
    editor. Only the editor routes may pass True. The assistant (M12) and any other
    automatic caller must never pass it: they create a new draft instead (SPEC §9.8)."""
    if cv.is_locked:
        raise DocumentLocked
    if cv.is_master and not master_unlocked:
        raise MasterNotUnlocked


def update_content(
    session: Session,
    cv: CVDocument,
    *,
    title: str,
    content: CVContent,
    template: str | None = None,
    shown: dict[str, bool] | None = None,
    user: User | None = None,
    master_unlocked: bool = False,
) -> CVDocument:
    """Save the editor's changes. Only this document changes: never the profile,
    never the master of a copy, never the copies of a master. `template` must already
    be checked against the user's plan (the router does it, entitlements).
    `shown` (with `user`) switches the personal details on or off, e.g.
    {"show_photo": True, "show_birth_date": False} (D-052, D-053)."""
    _ensure_changeable(cv, master_unlocked)
    cv.title = title
    if template is not None:
        cv.template = template
    for box, on in (shown or {}).items():
        setattr(cv, box, on)
    if shown is not None:
        _set_private_details(session, user, cv, content)
    cv.content = content.to_json()  # a new dict, so SQLAlchemy sees the change
    session.add(cv)
    session.commit()
    session.refresh(cv)
    return cv


PRIVATE_BOXES = ("show_photo", "show_birth_date", "show_nationality")


def _set_private_details(
    session: Session, user: User | None, cv: CVDocument, content: CVContent
) -> None:
    """The rule for photo, date of birth and nationality (D-052, D-053): a CV holds
    them only while their box is on. Switched on (or refreshed), the current value
    comes from the profile; switched off, it is removed. A value the CV already has
    is kept (so a sent CV never changes). Nothing is stored that isn't shown."""
    personal = content.personal
    if not cv.show_photo:
        personal.photo = None
        personal.photo_type = None
    if not cv.show_birth_date:
        personal.date_of_birth = None
    if not cv.show_nationality:
        personal.nationality = None
    if user is None:
        return

    profile = profile_service.get_or_create_profile(session, user)
    if cv.show_photo and not personal.photo:
        personal.photo = photo_service.photo_for_cv(profile.photo_path)
        personal.photo_type = "image/jpeg" if personal.photo else None
    if cv.show_birth_date and personal.date_of_birth is None:
        personal.date_of_birth = profile.date_of_birth
    if cv.show_nationality and not personal.nationality:
        personal.nationality = profile.nationality


def refresh_from_profile(
    session: Session, user: User, cv: CVDocument, *, master_unlocked: bool = False
) -> CVDocument:
    """For "Refresh from profile": replace the content with a new snapshot (the user
    confirmed that their edits on this CV are overwritten)."""
    _ensure_changeable(cv, master_unlocked)
    content, profile_version = snapshot_from_profile(session, user)
    _set_private_details(session, user, cv, content)  # current values, where shown
    cv.content = content.to_json()
    cv.source_profile_version = profile_version
    session.add(cv)
    session.commit()
    session.refresh(cv)
    return cv


def delete_cv(session: Session, cv: CVDocument, user: User) -> int:
    """Delete a draft, keeping a short-lived copy for Undo (D-086, closes D-046).
    Locked documents can't be deleted; a master only when it has no copies (D-047).
    Returns the copy's id for the Undo token."""
    from app.models import Application
    from app.services import trash_service

    if cv.is_locked:
        raise DocumentLocked
    if cv.is_master:
        copies = copy_count(session, cv)
        if copies:
            raise MasterHasCopies(copies)
    payload = trash_service.payload_of(cv)
    used_by = session.exec(
        select(Application.id).where(Application.cv_id == cv.id, Application.user_id == user.id)
    ).first()
    payload["_used_by"] = used_by  # to give the application its CV back on Undo
    copy = trash_service.remember(session, user, "cv", payload)
    session.delete(cv)
    session.commit()
    return copy.id


def restore_cv(session: Session, user: User, payload: dict) -> CVDocument:
    """Undo of a delete (D-086): the CV comes back as a new row, if that still fits.
    A master only while its language has no master again; a copy only while its
    master exists; its version number if free, else the next one."""
    from app.models import Application
    from app.services import trash_service

    if payload.get("user_id") != user.id:
        raise trash_service.CannotRestore
    cv = trash_service.row_from(CVDocument, payload)
    if cv.is_master:
        if current_master(session, user, cv.language) is not None:
            raise trash_service.CannotRestore
    else:
        parent = session.get(CVDocument, cv.parent_id) if cv.parent_id else None
        if parent is None or parent.user_id != user.id:
            raise trash_service.CannotRestore
        taken = session.exec(
            select(CVDocument.id).where(
                CVDocument.parent_id == cv.parent_id, CVDocument.version == cv.version
            )
        ).first()
        if taken is not None:
            cv.version = _next_version(session, user.id, cv.parent_id)
    if cv.application_id is not None:
        owner = session.get(Application, cv.application_id)
        if owner is None or owner.user_id != user.id:
            cv.application_id = None
    try:
        _check_room(session, user)
    except TooManyDocuments:
        raise trash_service.CannotRestore from None
    session.add(cv)
    session.flush()
    used_by = payload.get("_used_by")
    if type(used_by) is int:
        application = session.get(Application, used_by)
        # Only into an empty place, and not into a job that was sent meanwhile.
        if (
            application is not None
            and application.user_id == user.id
            and application.cv_id is None
            and application.status == "saved"
        ):
            application.cv_id = cv.id
            session.add(application)
    return cv


def lock(session: Session, cv: CVDocument, *, commit: bool = True) -> CVDocument:
    """Lock a copy for good: it was sent with an application (D-082). Masters are never
    locked: only copies are sent (SPEC §5.3). commit=False: part of a bigger change
    (status + lock + sent copy in one transaction)."""
    if cv.is_master:
        raise ValueError("A master CV is never locked; lock the copy that was sent.")
    if not cv.is_locked:
        cv.status = "locked"
        cv.locked_at = utcnow()
        session.add(cv)
        if commit:
            session.commit()
            session.refresh(cv)
    return cv
