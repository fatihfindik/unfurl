"""Profile form checks and their messages (plan M2a §2)."""

import datetime as dt

import pytest
from pydantic import ValidationError

from app.schemas.profile import (
    CertificationForm,
    EducationForm,
    ExperienceForm,
    LanguageForm,
    PersonalInfoForm,
    ProjectForm,
    SkillForm,
    form_errors,
)

EXPERIENCE = {"title": "Product Manager", "company": "Ostwind", "start_date": "2018-04"}


def errors(form_class, data) -> dict:
    with pytest.raises(ValidationError) as exc:
        form_class.model_validate(data)
    return form_errors(exc.value)


def test_text_is_trimmed_and_empty_means_not_filled_in():
    form = ExperienceForm.model_validate(EXPERIENCE | {"title": "  PM  ", "location": "   "})
    assert form.title == "PM"
    assert form.location is None


def test_month_becomes_the_first_day_of_the_month():
    form = ExperienceForm.model_validate(EXPERIENCE | {"end_date": "2021-02"})
    assert form.start_date == dt.date(2018, 4, 1)
    assert form.end_date == dt.date(2021, 2, 1)


def test_ongoing_ignores_the_end_date():
    form = ExperienceForm.model_validate(EXPERIENCE | {"end_date": "2010-01", "ongoing": "yes"})
    assert form.end_date is None


def test_empty_bullets_are_left_out():
    form = ExperienceForm.model_validate(EXPERIENCE | {"bullets": [" Led a team ", "", "  "]})
    assert form.bullets == ["Led a team"]


@pytest.mark.parametrize(
    ("form_class", "data", "field", "message"),
    [
        (ExperienceForm, EXPERIENCE | {"title": " "}, "title", "This field is required."),
        (ExperienceForm, EXPERIENCE | {"start_date": ""}, "start_date", "This field is required."),
        (
            ExperienceForm,
            EXPERIENCE | {"company": "x" * 201},
            "company",
            "Use at most 200 characters.",
        ),
        (
            ExperienceForm,
            EXPERIENCE | {"start_date": "2021-13"},
            "start_date",
            "Use the format YYYY-MM.",
        ),
        (
            ExperienceForm,
            EXPERIENCE | {"start_date": "March 2021"},
            "start_date",
            "Use the format YYYY-MM.",
        ),
        (
            ExperienceForm,
            EXPERIENCE | {"end_date": "2017-01"},
            "end_date",
            "The end date must be after the start date.",
        ),
        (
            ExperienceForm,
            EXPERIENCE | {"bullets": ["x"] * 21},
            "bullets",
            "Use at most 20 bullets.",
        ),
        (
            ExperienceForm,
            EXPERIENCE | {"bullets": ["x" * 301]},
            "bullets",
            "Each bullet can have at most 300 characters.",
        ),
        (EducationForm, {"institution": "TU Wien"}, "start_date", "This field is required."),
        (
            EducationForm,
            {"institution": "TU", "start_date": "2018-01", "end_date": "2016-01"},
            "end_date",
            "The end date must be after the start date.",
        ),
        (SkillForm, {"name": ""}, "name", "This field is required."),
        (SkillForm, {"name": "SQL", "level": "6"}, "level", "Choose a level from 1 to 5."),
        (SkillForm, {"name": "SQL", "level": "high"}, "level", "Choose a level from 1 to 5."),
        (
            LanguageForm,
            {"language": "German", "level": "D1"},
            "level",
            "Choose a level from the list.",
        ),
        (LanguageForm, {"language": "German", "level": ""}, "level", "This field is required."),
        (CertificationForm, {"name": "CSPO", "date": "abc"}, "date", "Use the format YYYY-MM."),
        (
            ProjectForm,
            {"name": "Unfurl", "url": "javascript:alert(1)"},
            "url",
            "Enter a full link starting with https://.",
        ),
        (PersonalInfoForm, {"full_name": ""}, "full_name", "This field is required."),
        (
            PersonalInfoForm,
            {"full_name": "J", "email": "nope"},
            "email",
            "Enter a valid email address.",
        ),
        (
            PersonalInfoForm,
            {"full_name": "J", "linkedin_url": "linkedin.com/in/jana"},
            "linkedin_url",
            "Enter a full link starting with https://.",
        ),
        (
            PersonalInfoForm,
            {"full_name": "J", "date_of_birth": "01.02.1990"},
            "date_of_birth",
            "Use the format YYYY-MM-DD.",
        ),
        (
            PersonalInfoForm,
            {"full_name": "J", "date_of_birth": "1899-12-31"},
            "date_of_birth",
            "Enter a real date of birth.",
        ),
        (
            PersonalInfoForm,
            {"full_name": "J", "date_of_birth": str(dt.date.today())},
            "date_of_birth",
            "Enter a real date of birth.",
        ),
    ],
)
def test_invalid_input_gets_a_short_message(form_class, data, field, message):
    assert errors(form_class, data)[field] == message


def test_valid_links_and_levels_pass():
    assert ProjectForm.model_validate({"name": "U", "url": "https://unfurl.app"}).url
    assert SkillForm.model_validate({"name": "SQL", "level": "3"}).level == 3
    for level in ("A1", "C2", "native"):
        assert LanguageForm.model_validate({"language": "German", "level": level})
