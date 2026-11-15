"""What a CV contains (D-043). `CVDocument.content` is this model as JSON.

A CV is a copy of the profile at one moment (a snapshot), so the fields mirror the
profile sections. Editing the CV only changes this copy, never the profile.

Old CVs stay readable when fields are added later: new fields get a default value,
and `schema_version` tells which shape a stored CV was written in.
"""

import datetime as dt

from pydantic import BaseModel, Field

# The sections a user can hide on a CV (the name and contact lines are always shown).
CV_SECTIONS = (
    "summary",
    "experience",
    "education",
    "skills",
    "languages",
    "certifications",
    "projects",
)


class CVPersonal(BaseModel):
    full_name: str = ""
    headline: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    website: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    # Photo, date of birth and nationality come with the PDF in M3b (show_photo,
    # show_birth_date); they are not copied yet.


class CVBullet(BaseModel):
    text: str
    selected: bool = True  # unticked bullets stay in the CV but are not shown


class CVExperience(BaseModel):
    title: str
    company: str
    location: str | None = None
    start_date: dt.date
    end_date: dt.date | None = None  # None = "present"
    description: str | None = None
    bullets: list[CVBullet] = Field(default_factory=list)


class CVEducation(BaseModel):
    institution: str
    degree: str | None = None
    field: str | None = None
    location: str | None = None
    start_date: dt.date
    end_date: dt.date | None = None
    description: str | None = None


class CVSkill(BaseModel):
    name: str
    category: str | None = None
    level: int | None = None


class CVLanguage(BaseModel):
    language: str
    level: str


class CVCertification(BaseModel):
    name: str
    issuer: str | None = None
    date: dt.date | None = None
    url: str | None = None


class CVProject(BaseModel):
    name: str
    description: str | None = None
    url: str | None = None


class CVContent(BaseModel):
    schema_version: int = 1
    personal: CVPersonal = Field(default_factory=CVPersonal)
    summary: str | None = None
    experience: list[CVExperience] = Field(default_factory=list)
    education: list[CVEducation] = Field(default_factory=list)
    skills: list[CVSkill] = Field(default_factory=list)
    languages: list[CVLanguage] = Field(default_factory=list)
    certifications: list[CVCertification] = Field(default_factory=list)
    projects: list[CVProject] = Field(default_factory=list)
    hidden_sections: list[str] = Field(default_factory=list)  # names from CV_SECTIONS

    def to_json(self) -> dict:
        """For CVDocument.content (dates become "2021-03-01" text)."""
        return self.model_dump(mode="json")
