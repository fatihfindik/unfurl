"""Apply › Final check without AI: the rule-based checks (SPEC §6.7; D-151).
Unit tests on plain objects: the service only reads what it is given."""

import datetime as dt

import pytest

from app.models import Application, CoverLetter, CVDocument
from app.services import final_check_service as fc

TODAY = dt.date(2026, 11, 23)
EXPERIENCE = {
    "title": "Senior PM",
    "company": "PlanRadar",
    "start_date": "2021-03-01",
}


def job(**fields) -> Application:
    values = {"id": 7, "user_id": 1, "company": "Kranich Health GmbH", "job_title": "PM"}
    return Application(**(values | fields))


def cv(content=None, **fields) -> CVDocument:
    content = content or {
        "personal": {"full_name": "Mara Test", "email": "mara@example.com"},
        "summary": "Product manager.",
        "experience": [EXPERIENCE],
    }
    values = {"id": 3, "user_id": 1, "title": "CV", "language": "en", "version": 1}
    return CVDocument(content=content, **(values | fields))


def letter(**fields) -> CoverLetter:
    values = {
        "id": 5,
        "user_id": 1,
        "title": "Letter",
        "language": "en",
        "version": 1,
        "recipient_company": "Kranich Health GmbH",
        "body": "Dear team, …",
    }
    return CoverLetter(**(values | fields))


def by_id(checks) -> dict:
    return {check.id: check for check in checks}


# --- Which checks are shown ---------------------------------------------------------------------


def test_all_fine_gives_eight_passed_checks():
    checks = fc.run(job(), cv(), letter(), TODAY)

    assert [c.id for c in checks] == [
        "cv_linked",
        "cv_contact",
        "cv_content",
        "letter_linked",
        "letter_recipient",
        "letter_body",
        "language",
        "posting_open",
    ]
    assert all(c.status == fc.OK and not c.fix_url for c in checks)
    assert fc.summary(checks) == (8, 8)


def test_without_documents_only_the_links_and_the_posting_are_checked():
    checks = by_id(fc.run(job(), None, None, TODAY))

    assert list(checks) == ["cv_linked", "letter_linked", "posting_open"]
    assert checks["cv_linked"].status == fc.MISSING
    assert checks["cv_linked"].fix_url == "/applications/7#prepare"
    # The letter is optional (D-165): a warning only.
    assert checks["letter_linked"].status == fc.WARN
    assert checks["letter_linked"].fix_url == "/applications/7#prepare"


# --- CV -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("personal", "title"),
    [
        ({"full_name": "", "email": "mara@example.com"}, "Your CV has no name"),
        ({"full_name": "Mara", "email": ""}, "Your CV has no email"),
        ({"full_name": " ", "email": None}, "Your CV has no name and no email"),
    ],
)
def test_name_and_email_come_from_the_profile(personal, title):
    check = by_id(fc.run(job(), cv({"personal": personal, "summary": "x"}), None, TODAY))[
        "cv_contact"
    ]

    assert (check.status, check.title) == (fc.MISSING, title)
    # The CV editor can't change them: they come from the profile (plan-reviewer Blocker).
    assert check.fix_url == "/profile"
    assert "Refresh from profile" in check.detail


@pytest.mark.parametrize(
    "content",
    [
        {"personal": {"full_name": "M", "email": "m@x.io"}},
        {"personal": {"full_name": "M", "email": "m@x.io"}, "summary": "  "},
        # Hidden sections are not on the CV.
        {
            "personal": {"full_name": "M", "email": "m@x.io"},
            "summary": "Text",
            "experience": [EXPERIENCE],
            "hidden_sections": ["summary", "experience"],
        },
    ],
)
def test_a_cv_without_summary_or_experience_is_a_warning(content):
    check = by_id(fc.run(job(), cv(content), None, TODAY))["cv_content"]

    assert check.status == fc.WARN
    assert check.fix_url == "/cvs/3?application=7"


def test_a_cv_with_only_experience_is_fine():
    content = {"personal": {"full_name": "M", "email": "m@x.io"}, "experience": [EXPERIENCE]}

    assert by_id(fc.run(job(), cv(content), None, TODAY))["cv_content"].status == fc.OK


def test_a_locked_cv_has_no_fix_links():
    locked = cv({"personal": {}}, status="locked")

    checks = by_id(fc.run(job(), locked, None, TODAY))

    assert checks["cv_contact"].status == fc.MISSING and checks["cv_contact"].fix_url == ""
    assert checks["cv_content"].status == fc.WARN and checks["cv_content"].fix_url == ""


# --- Cover letter ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "written",
    [
        "Kranich Health GmbH",
        "kranich  health",
        "KRANICH HEALTH",
        "Kranich Health, GmbH",
        "Kranich Health AG",
    ],
)
def test_the_same_company_written_differently_is_fine(written):
    checks = by_id(fc.run(job(), cv(), letter(recipient_company=written), TODAY))

    assert checks["letter_recipient"].status == fc.OK


@pytest.mark.parametrize("written", ["Nordbau Software", "", None, "Kranich"])
def test_another_or_no_company_is_a_warning(written):
    check = by_id(fc.run(job(), cv(), letter(recipient_company=written), TODAY))["letter_recipient"]

    assert check.status == fc.WARN
    assert check.fix_url == "/cover-letters/5?application=7"


def test_an_empty_letter_is_missing():
    check = by_id(fc.run(job(), cv(), letter(body="  \n "), TODAY))["letter_body"]

    assert check.status == fc.MISSING and check.fix_url == "/cover-letters/5?application=7"


def test_cv_and_letter_in_different_languages_is_a_warning():
    check = by_id(fc.run(job(), cv(), letter(language="de"), TODAY))["language"]

    assert check.status == fc.WARN
    assert check.detail == "CV: English, letter: German"
    assert check.fix_url == "/applications/7#prepare"


def test_a_locked_letter_has_no_fix_links():
    sent = letter(status="locked", body="", recipient_company="Other")

    checks = by_id(fc.run(job(), cv(), sent, TODAY))

    assert checks["letter_body"].fix_url == "" and checks["letter_recipient"].fix_url == ""


# --- The posting ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("closes", "status"),
    [
        (None, fc.OK),
        (TODAY, fc.OK),
        (TODAY + dt.timedelta(days=3), fc.OK),
        (TODAY - dt.timedelta(days=1), fc.WARN),
    ],
)
def test_a_closed_posting_is_a_warning(closes, status):
    check = by_id(fc.run(job(posting_closes_at=closes), None, None, TODAY))["posting_open"]

    assert check.status == status
    if status == fc.WARN:
        assert check.fix_url == "/applications/7?tab=details"


def test_company_key_keeps_a_company_that_is_only_a_legal_word():
    assert fc.company_key("AG") == "ag"
    assert fc.company_key("  Müller &  Söhne KG ") == "müller & söhne"
