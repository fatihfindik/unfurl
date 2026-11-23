"""Apply › Final check without AI (SPEC §6.7, D-151): a short list of checks made in
code from what is stored. Each check is "ok", "warn" or "missing" and, when something
is wrong, links to the page where it is fixed. The list never blocks anything: the
user decides when to send (D-151).

The function only reads. It gets the application's CV and letter from the caller,
who already checked that they belong to the user (documents_context)."""

import datetime as dt
import re
from dataclasses import dataclass

from app.models import Application, CoverLetter, CVDocument
from app.schemas.cv import CVContent

OK, WARN, MISSING = "ok", "warn", "missing"

# Endings that don't change which company it is: "Kranich Health GmbH" and
# "Kranich Health" are the same company (D-151).
LEGAL_ENDINGS = ("gmbh", "ag", "kg", "se", "ltd", "inc", "e.u.", "og", "co.", "co")

LANGUAGE_NAMES = {"en": "English", "de": "German", "tr": "Turkish"}


@dataclass
class Check:
    id: str
    status: str  # OK, WARN or MISSING
    title: str
    detail: str = ""
    fix_url: str = ""  # empty: nothing to fix here (all fine, or the document is locked)
    fix_label: str = ""


def company_key(name: str | None) -> str:
    """A company name to compare: lower case, single spaces, legal ending removed."""
    words = re.sub(r"[\s,]+", " ", (name or "").casefold()).strip().split(" ")
    while len(words) > 1 and words[-1] in LEGAL_ENDINGS:
        words.pop()
    return " ".join(words)


def run(
    application: Application,
    cv: CVDocument | None,
    letter: CoverLetter | None,
    today: dt.date,
) -> list[Check]:
    """The checks for this application, in the order they are shown. `today` is the
    user's day (clock.today()), for the posting's closing date."""
    prepare = f"/applications/{application.id}#prepare"
    checks = [_cv_linked(cv, prepare)]
    if cv is not None:
        checks += _cv_checks(application, cv)
    checks.append(_letter_linked(letter, prepare))
    if letter is not None:
        checks += _letter_checks(application, cv, letter, prepare)
    checks.append(_posting_open(application, today))
    return checks


def summary(checks: list[Check]) -> tuple[int, int]:
    """(passed, total) for "Final check · 5 of 6 passed"."""
    return sum(check.status == OK for check in checks), len(checks)


# --- CV -------------------------------------------------------------------------------------


def _cv_linked(cv, prepare: str) -> Check:
    if cv is None:
        return Check("cv_linked", MISSING, "No CV for this job yet", "", prepare, "Prepare a CV")
    return Check("cv_linked", OK, "A CV is ready", f"{cv.title} · v{cv.version}")


def _cv_checks(application, cv: CVDocument) -> list[Check]:
    content = CVContent.model_validate(cv.content)
    editor = f"/cvs/{cv.id}?application={application.id}"
    profile_note = "Then use Refresh from profile in the CV."
    # A locked CV is what was sent: nothing can be fixed any more (D-082).
    fixable = not cv.is_locked

    missing = [
        label
        for label, value in (
            ("name", content.personal.full_name),
            ("email", content.personal.email),
        )
        if not (value or "").strip()
    ]
    if missing:
        contact = Check(
            "cv_contact",
            MISSING,
            f"Your CV has no {' and no '.join(missing)}",
            profile_note if fixable else "",
            "/profile" if fixable else "",
            "Edit your profile",
        )
    else:
        contact = Check("cv_contact", OK, "Your name and email are on the CV")

    hidden = set(content.hidden_sections)
    has_summary = bool((content.summary or "").strip()) and "summary" not in hidden
    has_experience = bool(content.experience) and "experience" not in hidden
    if has_summary or has_experience:
        body = Check("cv_content", OK, "Your CV has a summary or experience")
    else:
        # A summary is written (or shown again) in the CV editor; experience comes
        # from the profile. The editor's link to the profile covers both.
        body = Check(
            "cv_content",
            WARN,
            "Your CV shows no summary and no experience",
            "Add a summary in the CV, or experience in your profile." if fixable else "",
            editor if fixable else "",
            "Open the CV",
        )
    return [contact, body]


# --- Cover letter -----------------------------------------------------------------------------


def _letter_linked(letter, prepare: str) -> Check:
    if letter is None:
        # The letter is optional (D-165): a warning, not a missing piece.
        return Check(
            "letter_linked",
            WARN,
            "No cover letter",
            "Fine if the job doesn't ask for one.",
            prepare,
            "Prepare a cover letter",
        )
    return Check(
        "letter_linked", OK, "A cover letter is ready", f"{letter.title} · v{letter.version}"
    )


def _letter_checks(application, cv, letter: CoverLetter, prepare: str) -> list[Check]:
    editor = f"/cover-letters/{letter.id}?application={application.id}"
    fixable = not letter.is_locked
    checks = []

    if company_key(letter.recipient_company) == company_key(application.company):
        checks.append(
            Check("letter_recipient", OK, f"The letter is addressed to {application.company}")
        )
    else:
        written = (letter.recipient_company or "").strip()
        checks.append(
            Check(
                "letter_recipient",
                WARN,
                f"The letter is not addressed to {application.company}",
                f"It says: {written}" if written else "The company in the letter is empty.",
                editor if fixable else "",
                "Open the letter",
            )
        )

    if letter.body.strip():
        checks.append(Check("letter_body", OK, "The letter has a text"))
    else:
        checks.append(
            Check(
                "letter_body",
                MISSING,
                "The letter has no text yet",
                "",
                editor if fixable else "",
                "Write the letter",
            )
        )

    if cv is not None:
        if cv.language == letter.language:
            checks.append(Check("language", OK, "CV and letter are in the same language"))
        else:
            checks.append(
                Check(
                    "language",
                    WARN,
                    "CV and letter are in different languages",
                    f"CV: {LANGUAGE_NAMES.get(cv.language, cv.language)}, "
                    f"letter: {LANGUAGE_NAMES.get(letter.language, letter.language)}",
                    prepare if fixable and not cv.is_locked else "",
                    "Pick other copies",
                )
            )
    return checks


# --- The posting --------------------------------------------------------------------------------


def _posting_open(application, today: dt.date) -> Check:
    closes = application.posting_closes_at
    if closes is not None and closes < today:
        return Check(
            "posting_open",
            WARN,
            "The posting closed on " + closes.strftime("%a, %-d %b %Y"),
            "Check that the job is still open.",
            f"/applications/{application.id}?tab=details",
            "See the details",
        )
    detail = (
        "No closing date" if closes is None else "Open until " + closes.strftime("%a, %-d %b %Y")
    )
    return Check("posting_open", OK, "The posting is open", detail)
