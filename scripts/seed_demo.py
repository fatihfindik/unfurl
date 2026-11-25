"""Fill an empty demo database with a fictional user, for screenshots (D-173).

    DATABASE_URL=sqlite:///./demo.db uv run alembic upgrade head
    DATABASE_URL=sqlite:///./demo.db uv run python scripts/seed_demo.py
    DATABASE_URL=sqlite:///./demo.db uv run fastapi dev app/main.py --port 8001

Everything here is made up: "Jana Novak" and every company. The repository is public,
so screenshots must never show real data. Two guards keep this away from real data:
the database must be a SQLite file named demo.db (checked before the app is even
imported), and it must have no users yet. The password is made now and only printed.
"""

import os
import sys
from pathlib import Path, PurePosixPath

# Run as a file (python scripts/seed_demo.py): the project root holds the app package.
ROOT = Path(__file__).resolve().parent.parent


def demo_database_url() -> str | None:
    """DATABASE_URL if it is a SQLite file called demo.db, else None."""
    url = os.environ.get("DATABASE_URL", "")
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    if PurePosixPath(url[len(prefix) :]).name != "demo.db":
        return None
    return url


def main() -> int:
    if demo_database_url() is None:
        print("Refused: set DATABASE_URL=sqlite:///./demo.db (a separate demo database).")
        return 1

    # Imported only now: app.db builds its engine from DATABASE_URL when imported.
    sys.path.insert(0, str(ROOT))
    import secrets

    from sqlmodel import Session, select

    from app.db import engine
    from app.models import User

    with Session(engine) as session:
        if session.exec(select(User)).first() is not None:
            print("Refused: this database already has users. Use a new, empty demo.db.")
            return 1
        password = secrets.token_urlsafe(12)
        seed(session, password)

    print("Demo data written. Sign in as jana.novak@example.com with this password:")
    print(password)
    return 0


def seed(session, password: str) -> None:
    """The demo user and her data, written through the app's own services, so locks,
    timelines and reminders look exactly like real use. Dates are relative to today."""
    import datetime as dt

    from app.models import Education, Experience, LanguageSkill, Skill
    from app.services import application_service as apps
    from app.services import clock, learning_service, notification_service, profile_service
    from app.services import cover_letter_service as letters
    from app.services import document_service as docs
    from app.services.auth_service import create_user

    today = clock.today()

    def days_ago(n: int) -> dt.date:
        return today - dt.timedelta(days=n)

    user = create_user(
        session,
        email="jana.novak@example.com",
        password=password,
        full_name="Jana Novak",
        timezone="Europe/Vienna",
    )
    user.is_verified = True
    session.add(user)
    session.commit()

    # --- Profile -----------------------------------------------------------------------
    profile = profile_service.get_or_create_profile(session, user)
    profile.headline = "Senior Product Manager · Mobile and B2B SaaS"
    profile.summary = (
        "Product manager with 8 years in B2B SaaS and mobile apps. I lead discovery and "
        "delivery with cross-functional squads, care about accessibility, and like turning "
        "messy field work into simple tools."
    )
    profile.email = "jana.novak@example.com"
    profile.phone = "+43 1 000 0000"  # clearly fictional, not a real line
    profile.location = "Wien, Austria"
    session.add(profile)
    session.commit()

    experiences = [
        (
            "Brightfield Software",
            "Senior Product Manager",
            dt.date(2021, 3, 1),
            None,
            [
                "Led the mobile app for 40,000 field workers; rating from 3.6 to 4.7",
                "Ran discovery with 60+ customer interviews a year",
                "Cut onboarding time from two weeks to three days",
            ],
        ),
        (
            "Donau Logistics",
            "Product Manager",
            dt.date(2018, 1, 1),
            dt.date(2021, 2, 28),
            [
                "Launched route planning for 300 depots in five countries",
                "Introduced quarterly OKRs and a shared roadmap",
            ],
        ),
        (
            "Alpenblick Health",
            "Business Analyst",
            dt.date(2016, 9, 1),
            dt.date(2017, 12, 31),
            ["Mapped patient journeys for three hospital clients"],
        ),
    ]
    for company, title, start, end, bullets in experiences:
        profile_service.add_item(
            session,
            profile,
            Experience,
            {
                "company": company,
                "title": title,
                "location": "Wien",
                "start_date": start,
                "end_date": end,
                "description": None,
                "bullets": bullets,
            },
        )
    profile_service.add_item(
        session,
        profile,
        Education,
        {
            "institution": "TU Wien",
            "degree": "MSc",
            "field": "Business Informatics",
            "location": "Wien",
            "start_date": dt.date(2014, 10, 1),
            "end_date": dt.date(2016, 6, 30),
        },
    )
    for name, category in [
        ("Product discovery", "Product"),
        ("Roadmapping", "Product"),
        ("User research", "Product"),
        ("Accessibility (WCAG)", "Product"),
        ("SQL", "Tools"),
        ("Figma", "Tools"),
        ("Jira", "Tools"),
    ]:
        profile_service.add_item(session, profile, Skill, {"name": name, "category": category})
    for language, level in [("Czech", "native"), ("English", "C2"), ("German", "C1")]:
        profile_service.add_item(
            session, profile, LanguageSkill, {"language": language, "level": level}
        )

    # --- Documents ----------------------------------------------------------------------
    master_cv = docs.create_cv(session, user, "Master CV (EN)", "en", "modern")
    docs.create_cv(session, user, "Lebenslauf (DE)", "de", "classic")
    master_letter = letters.create_letter(session, user, "Master letter (EN)", "en")
    master_letter.body = (
        "I build products that people in the field actually use. At Brightfield Software "
        "I lead the mobile app for 40,000 field workers, and I would love to bring that "
        "experience to your team.\n\n"
        "What I enjoy most is the step from a messy problem to a simple tool: talking to "
        "users, testing early, and shipping in small steps with the team.\n\n"
        "I would be glad to tell you more in a conversation."
    )
    session.add(master_letter)
    session.commit()

    def prepared(company: str, job_title: str, **fields):
        """A job with a CV copy and a cover letter copy for it."""
        job = apps.create(session, user, company=company, job_title=job_title, **fields)
        cv = docs.copy_for_application(session, user, master_cv, job, f"{company} · CV")
        apps.link_cv(session, user, job, cv)
        letter = letters.copy_for_application(
            session, user, master_letter, job, f"{company} · Cover letter"
        )
        apps.link_letter(session, user, job, letter)
        return job

    def backdate(job, created_days_ago: int, applied_days_ago: int | None = None):
        job.created_at = clock.now() - dt.timedelta(days=created_days_ago)
        job.last_activity_at = job.created_at
        if applied_days_ago is not None:
            job.applied_at = days_ago(applied_days_ago)
        session.add(job)
        session.commit()

    # Saved, ready to apply: the Apply tab with a passing Final check.
    kranich = prepared(
        "Kranich Health",
        "Senior Product Manager",
        location="Wien",
        remote_type="hybrid",
        salary_text="€ 72,000 – 85,000 per year",
        posting_closes_at=today + dt.timedelta(days=6),
        description_raw=(
            "Kranich Health builds a patient app used by 40 hospitals in Austria and "
            "Germany. You own the mobile experience end to end and lead a squad of 7."
        ),
    )
    backdate(kranich, 3)

    # Saved, nothing prepared yet.
    stadtwerk = apps.create(
        session,
        user,
        company="Stadtwerk Digital",
        job_title="Product Manager, Data",
        location="Graz",
        remote_type="remote",
    )
    backdate(stadtwerk, 1)

    # Applied 12 days ago: the follow-up reminder is due.
    alpine = prepared(
        "Alpine Mobility", "Lead Product Manager", location="Innsbruck", remote_type="onsite"
    )
    apps.set_status(session, user, alpine, "applied")
    backdate(alpine, 15, applied_days_ago=12)

    # Interview tomorrow at 10:00.
    fernweh = prepared("Fernweh Travel", "Product Manager", location="Wien", remote_type="hybrid")
    apps.set_status(session, user, fernweh, "applied")
    tomorrow_ten = clock.to_utc(today + dt.timedelta(days=1), dt.time(10, 0))
    apps.set_interview(
        session,
        user,
        fernweh,
        at=tomorrow_ten,
        interview_type="video",
        interview_with="Mira Hofer, Head of Product",
    )
    backdate(fernweh, 20, applied_days_ago=14)

    # An offer and a rejection.
    nordbau = prepared(
        "Nordbau Software", "Senior Product Owner", location="Linz", remote_type="hybrid"
    )
    apps.set_status(session, user, nordbau, "applied")
    apps.set_status(session, user, nordbau, "offer")
    backdate(nordbau, 30, applied_days_ago=25)

    lumen = prepared("Lumen Insurance", "Product Manager", location="Wien", remote_type="onsite")
    apps.set_status(session, user, lumen, "applied")
    apps.set_status(session, user, lumen, "rejected")
    backdate(lumen, 40, applied_days_ago=35)

    # --- Learning -----------------------------------------------------------------------
    german = learning_service.create_goal(
        session,
        user,
        title="German C1 for job interviews",
        target_date=today + dt.timedelta(days=70),
        weekly_hours=2,
        resource={
            "name": "Business German C1",
            "provider": "Volkshochschule Wien",
            "kind": "course",
            "unit_label": "Week",
            "units_total": 10,
        },
        plan_weekly=True,
    )
    sql = learning_service.create_goal(
        session,
        user,
        title="Product analytics with SQL",
        target_date=today + dt.timedelta(days=45),
        weekly_hours=1.5,
        resource=None,
        plan_weekly=False,
    )
    for offset, title, minutes in [
        (0, "Practice window functions", 45),
        (2, "Write a retention query", 60),
        (5, "Build a funnel dashboard", 60),
    ]:
        learning_service.add_task(
            session,
            user,
            sql,
            title=title,
            due_date=today + dt.timedelta(days=offset),
            minutes=minutes,
        )
    # Week 1 of the course is done today: the streak and the hours start.
    first_week = min(
        (task for task in learning_service.tasks(session, user) if task.goal_id == german.id),
        key=lambda task: task.due_date,
    )
    learning_service.set_done(session, user, first_week, True)

    # --- Reminders ----------------------------------------------------------------------
    notification_service.generate_for_user(session, user, clock.now())


if __name__ == "__main__":
    sys.exit(main())
