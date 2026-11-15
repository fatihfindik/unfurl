"""A CV starts as a snapshot of the profile (SPEC §5.3, D-043)."""

from datetime import date

from app.models import Certification, Education, Experience, LanguageSkill, Project, Skill
from app.services.document_service import snapshot_from_profile
from app.services.profile_service import add_item, get_or_create_profile, list_items, move_item


def fill_profile(session, user):
    profile = get_or_create_profile(session, user)
    profile.headline = "Senior PM"
    profile.summary = "I build products."
    profile.phone = "+43 1"
    profile.location = "Vienna"
    profile.linkedin_url = "https://linkedin.com/in/jana"
    profile.date_of_birth = date(1990, 5, 17)
    profile.nationality = "Czech"
    profile.photo_path = "photos/abc.jpg"
    session.add(profile)
    session.commit()
    add_item(
        session,
        profile,
        Experience,
        {
            "company": "Acme",
            "title": "PM",
            "start_date": date(2020, 1, 1),
            "bullets": ["Shipped it", "Grew it"],
        },
    )
    add_item(
        session,
        profile,
        Experience,
        {"company": "Beta", "title": "Dev", "start_date": date(2015, 1, 1)},
    )
    add_item(
        session, profile, Education, {"institution": "TU Wien", "start_date": date(2010, 1, 1)}
    )
    add_item(session, profile, Skill, {"name": "SQL", "level": 4})
    add_item(session, profile, LanguageSkill, {"language": "German", "level": "B2"})
    add_item(session, profile, Certification, {"name": "AWS", "date": date(2022, 3, 1)})
    add_item(session, profile, Project, {"name": "Unfurl"})
    return profile


def test_snapshot_copies_every_section(session, user):
    fill_profile(session, user)

    content, _version = snapshot_from_profile(session, user)

    assert content.personal.full_name == user.full_name
    assert content.personal.headline == "Senior PM"
    assert content.personal.email == user.email  # copied into the profile on first visit
    assert content.personal.location == "Vienna"
    assert content.summary == "I build products."
    assert [job.company for job in content.experience] == ["Acme", "Beta"]
    assert [b.text for b in content.experience[0].bullets] == ["Shipped it", "Grew it"]
    assert all(b.selected for b in content.experience[0].bullets)
    assert content.education[0].institution == "TU Wien"
    assert content.skills[0].level == 4
    assert content.languages[0].level == "B2"
    assert content.certifications[0].date == date(2022, 3, 1)
    assert content.projects[0].name == "Unfurl"
    assert content.hidden_sections == []


def test_snapshot_keeps_the_profile_order(session, user):
    profile = fill_profile(session, user)
    beta = [row for row in list_items(session, profile, Experience) if row.company == "Beta"][0]
    move_item(session, profile, Experience, beta, "up")

    content, _version = snapshot_from_profile(session, user)

    assert [job.company for job in content.experience] == ["Beta", "Acme"]


def test_snapshot_remembers_the_profile_version(session, user):
    profile = fill_profile(session, user)
    session.refresh(profile)

    _content, version = snapshot_from_profile(session, user)

    assert version == profile.version > 0


def test_private_fields_are_not_copied_yet(session, user):
    # Photo, date of birth and nationality come with the PDF in M3b.
    fill_profile(session, user)

    dumped = snapshot_from_profile(session, user)[0].to_json()

    text = str(dumped)
    assert "1990-05-17" not in text
    assert "Czech" not in text
    assert "photos/abc.jpg" not in text


def test_changing_the_profile_later_does_not_change_a_snapshot(session, user):
    profile = fill_profile(session, user)
    content, _version = snapshot_from_profile(session, user)
    saved = content.to_json()

    profile.headline = "Head of Product"
    session.add(profile)
    session.commit()

    assert saved["personal"]["headline"] == "Senior PM"
