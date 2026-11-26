"""A user with a row in every table and every link between tables, for the export
(D-130) and account deletion (D-131) tests, on SQLite and on Postgres."""

import datetime as dt
import hashlib

from sqlmodel import Session

from app.models import (
    ApiToken,
    Application,
    ApplicationDocument,
    ApplicationEvent,
    Certification,
    CoverLetter,
    CVDocument,
    DeletedItem,
    Education,
    Experience,
    InterviewQuestion,
    InterviewStory,
    LanguageSkill,
    LearningGoal,
    LearningResource,
    LearningTask,
    Notification,
    NotificationPreference,
    Profile,
    Project,
    Skill,
    Story,
    StudyLog,
    User,
)

DAY = dt.date(2026, 11, 18)


def fill_account(session: Session, user: User, photo_path: str | None = None) -> dict:
    """Add one row to every table for `user`, linked the way the app links them.
    Returns the rows by name."""

    def add(row):
        session.add(row)
        session.commit()
        session.refresh(row)
        return row

    rows = {}
    profile = add(Profile(user_id=user.id, headline="PM", photo_path=photo_path))
    rows["profile"] = profile
    for name, row in {
        "experience": Experience(profile_id=profile.id, title="PM", company="Acme", start_date=DAY),
        "education": Education(profile_id=profile.id, institution="TU Wien", start_date=DAY),
        "skill": Skill(profile_id=profile.id, name="SQL"),
        "language": LanguageSkill(profile_id=profile.id, language="German", level="C1"),
        "certification": Certification(profile_id=profile.id, name="PSM I"),
        "project": Project(profile_id=profile.id, name="Unfurl"),
    }.items():
        rows[name] = add(row)

    master = add(CVDocument(user_id=user.id, title="Master", language="en", is_master=True))
    job = add(Application(user_id=user.id, company="Acme Secret Corp", job_title="PM"))
    copy = add(
        CVDocument(
            user_id=user.id,
            title="For Acme",
            language="en",
            parent_id=master.id,
            application_id=job.id,
            version=1,
        )
    )
    job.cv_id = copy.id
    add(job)
    rows |= {"master": master, "job": job, "copy": copy}
    letter = add(CoverLetter(user_id=user.id, title="Master letter", language="de", is_master=True))
    rows["letter"] = letter
    # A sent letter: linked both ways and locked, like CVs (D-157, D-159).
    rows["letter_copy"] = add(
        CoverLetter(
            user_id=user.id,
            title="For Acme",
            language="de",
            parent_id=letter.id,
            version=1,
            application_id=job.id,
            status="locked",
            locked_at=dt.datetime(2026, 11, 18, 9, tzinfo=dt.UTC),
        )
    )
    job.cover_letter_id = rows["letter_copy"].id
    add(job)
    rows["event"] = add(ApplicationEvent(application_id=job.id, type="note", message="Called Mira"))
    rows["sent"] = add(
        ApplicationDocument(
            user_id=user.id,
            application_id=job.id,
            doc_type="cv",
            document_id=copy.id,
            content_snapshot={"title": "For Acme"},
        )
    )
    rows["sent_letter"] = add(
        ApplicationDocument(
            user_id=user.id,
            application_id=job.id,
            doc_type="cover_letter",
            cover_letter_id=rows["letter_copy"].id,
            content_snapshot={"title": "For Acme", "date": "18.11.2026"},
        )
    )

    rows["story"] = add(
        Story(
            profile_id=profile.id,
            title="Migration under deadline",
            situation="Legacy system",
            tags="leadership, SQL",
            experience_id=rows["experience"].id,
        )
    )
    rows["interview_story"] = add(
        InterviewStory(user_id=user.id, application_id=job.id, story_id=rows["story"].id)
    )
    rows["question"] = add(
        InterviewQuestion(
            user_id=user.id,
            application_id=job.id,
            kind="likely",
            category="general",
            text="Tell me about yourself.",
            answer="I build things.",
        )
    )

    goal = add(LearningGoal(user_id=user.id, title="Python", source_application_id=job.id))
    resource = add(LearningResource(user_id=user.id, goal_id=goal.id, name="CS50P"))
    task = add(
        LearningTask(
            user_id=user.id, goal_id=goal.id, resource_id=resource.id, title="Week 1", due_date=DAY
        )
    )
    rows |= {"goal": goal, "resource": resource, "task": task}
    rows["study"] = add(
        StudyLog(user_id=user.id, goal_id=goal.id, task_id=task.id, date=DAY, minutes=30)
    )
    rows["prefs"] = add(NotificationPreference(user_id=user.id))
    rows["notification"] = add(
        Notification(
            user_id=user.id,
            application_id=job.id,
            type="follow_up",
            title="Time to follow up",
            body="Acme",
            link_url=f"/applications/{job.id}",
            dedupe_key=f"follow_up:{job.id}",
        )
    )
    rows["deleted"] = add(DeletedItem(user_id=user.id, kind="cv", payload={"title": "Old CV"}))
    rows["token"] = add(
        ApiToken(
            user_id=user.id,
            name="Chrome extension",
            token_hash=hashlib.sha256(user.email.encode()).hexdigest(),  # unique per user
            session_version=user.session_version,
            expires_at=dt.datetime(2027, 2, 16, tzinfo=dt.UTC),
        )
    )
    return rows
