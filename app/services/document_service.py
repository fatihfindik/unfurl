"""CV documents: snapshot, master, versions and locks (SPEC §5.3, D-043…D-048).

The rules for masters and locked documents live only here (CLAUDE.md). Routes and
the assistant (M12) change documents through these functions, never directly.
"""

from sqlmodel import Session

from app.models import (
    Certification,
    Education,
    Experience,
    LanguageSkill,
    Project,
    Skill,
    User,
)
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
