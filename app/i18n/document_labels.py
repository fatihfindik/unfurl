"""Words printed on CVs, in the CV's language (SPEC §6.5, D-055).

The app's own screens stay English; only the documents a user sends to employers
come in German, English or Turkish. Every language has the same keys (a test checks
this), so a template can use any key for any CV.
"""

import datetime as dt

LABELS: dict[str, dict[str, str]] = {
    "en": {
        "summary": "Summary",
        "experience": "Experience",
        "education": "Education",
        "skills": "Skills",
        "languages": "Languages",
        "certifications": "Certifications",
        "projects": "Projects",
        "present": "present",
        "native": "Native",
        "date_of_birth": "Date of birth",
        "nationality": "Nationality",
    },
    "de": {
        "summary": "Profil",
        "experience": "Berufserfahrung",
        "education": "Ausbildung",
        "skills": "Kenntnisse",
        "languages": "Sprachen",
        "certifications": "Zertifikate",
        "projects": "Projekte",
        "present": "heute",
        "native": "Muttersprache",
        "date_of_birth": "Geburtsdatum",
        "nationality": "Staatsangehörigkeit",
    },
    "tr": {
        "summary": "Özet",
        "experience": "İş Deneyimi",
        "education": "Eğitim",
        "skills": "Yetenekler",
        "languages": "Diller",
        "certifications": "Sertifikalar",
        "projects": "Projeler",
        "present": "devam ediyor",
        "native": "Ana dil",
        "date_of_birth": "Doğum tarihi",
        "nationality": "Uyruk",
    },
}

ENGLISH_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip


def labels(language: str) -> dict[str, str]:
    """The words for a CV language; English if the language is unknown."""
    return LABELS.get(language, LABELS["en"])


def full_date(value: dt.date, language: str) -> str:
    """A full date as CVs write it (D-055): 01.03.1990 in German and Turkish,
    1 March 1990 in English. (Months and years elsewhere are 03/2021 in every language.)"""
    if language == "en":
        return f"{value.day} {ENGLISH_MONTHS[value.month - 1]} {value.year}"
    return value.strftime("%d.%m.%Y")
