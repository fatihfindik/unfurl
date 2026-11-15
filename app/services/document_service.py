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
from app.services import profile_service

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
            source_profile_version=source.source_profile_version,
            parent_id=source.id if source.is_master else source.parent_id,
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
    return _save_copy(session, _copy_of(source, title))


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
    master_unlocked: bool = False,
) -> CVDocument:
    """Save the editor's changes. Only this document changes: never the profile,
    never the master of a copy, never the copies of a master. `template` must already
    be checked against the user's plan (the router does it, entitlements)."""
    _ensure_changeable(cv, master_unlocked)
    cv.title = title
    if template is not None:
        cv.template = template
    cv.content = content.to_json()  # a new dict, so SQLAlchemy sees the change
    session.add(cv)
    session.commit()
    session.refresh(cv)
    return cv


def refresh_from_profile(
    session: Session, user: User, cv: CVDocument, *, master_unlocked: bool = False
) -> CVDocument:
    """For "Refresh from profile": replace the content with a new snapshot (the user
    confirmed that their edits on this CV are overwritten)."""
    _ensure_changeable(cv, master_unlocked)
    content, profile_version = snapshot_from_profile(session, user)
    cv.content = content.to_json()
    cv.source_profile_version = profile_version
    session.add(cv)
    session.commit()
    session.refresh(cv)
    return cv


def delete_cv(session: Session, cv: CVDocument) -> None:
    """Delete a draft. Locked documents can't be deleted; a master only when it has
    no copies (D-047). No Undo until M4 (D-046: the user confirmed in a modal)."""
    if cv.is_locked:
        raise DocumentLocked
    if cv.is_master:
        copies = copy_count(session, cv)
        if copies:
            raise MasterHasCopies(copies)
    session.delete(cv)
    session.commit()


def lock(session: Session, cv: CVDocument) -> CVDocument:
    """Lock a copy for good (M4 calls this when an application is marked "applied").
    Masters are never locked: only copies are sent (SPEC §5.3)."""
    if cv.is_master:
        raise ValueError("A master CV is never locked; lock the copy that was sent.")
    if not cv.is_locked:
        cv.status = "locked"
        cv.locked_at = utcnow()
        session.add(cv)
        session.commit()
        session.refresh(cv)
    return cv
