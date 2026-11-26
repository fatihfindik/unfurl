"""Interview prep (SPEC §6.7, M14a, D-175…D-180): the rules behind the Interview prep tab.

An application's prep has three parts:
- likely questions (kind "likely"), grouped by category, each with my answer draft
- the company and role notes (Application.interview_notes)
- questions to ask them (kind "to_ask"), ticked once asked

Nothing here is AI (D-175). Every query filters by user_id (CLAUDE.md). Writes on an
archived application are refused (Archived → 409 in the router), like the Apply tab.
"""

from fastapi import HTTPException
from sqlalchemy import func, update
from sqlmodel import Session, col, select

from app.models import Application, InterviewQuestion, User
from app.models.base import utcnow
from app.models.interview import (
    CATEGORIES,
    KINDS,
    MAX_ANSWER,
    MAX_INTERVIEW_NOTES,
    MAX_QUESTION_TEXT,
    MAX_QUESTIONS,
)
from app.services import clock, next_step_service, trash_service
from app.services.profile_service import MAX_ID

# The tab is shown once the user applied (D-176). A saved job has nothing to prepare yet.
TAB_STATUSES = ("applied", "interview", "offer", "rejected")

CATEGORY_TITLES = {
    "general": "General",
    "behavioural": "Behavioural",
    "role": "About the role",
    "company": "About the company",
    "other": "Other",
}

# The starter set (D-178): added once, by the "Start interview prep" button.
# "{company}" is replaced with the application's company.
STARTER_QUESTIONS = (
    ("general", "Tell me about yourself."),
    ("general", "Why are you looking for a new role?"),
    ("general", "Where do you see yourself in a few years?"),
    ("behavioural", "Tell me about a time you solved a difficult problem."),
    ("behavioural", "Tell me about a mistake you made and what you learned."),
    ("behavioural", "Describe a conflict in a team and how you handled it."),
    ("behavioural", "Tell me about a time you had to learn something quickly."),
    ("role", "What are your strongest skills for this role?"),
    ("role", "What would you do in your first 90 days?"),
    ("role", "What are your salary expectations?"),
    ("company", "Why do you want to work at {company}?"),
    ("company", "What do you know about {company}?"),
)
STARTER_TO_ASK = (
    "What does success look like in the first 90 days?",
    "What does a typical week look like for the team?",
    "What are the biggest challenges for this role right now?",
    "What are the next steps in the process?",
)


class Archived(Exception):
    """The application is archived: its prep can be read but not changed (D-176)."""


class TooManyQuestions(Exception):
    """The application already has MAX_QUESTIONS questions (D-179)."""


class InvalidInput(ValueError):
    """A value from the form that can't be stored (empty, too long, unknown kind)."""


# --- Small helpers ------------------------------------------------------------------


def has_tab(application: Application) -> bool:
    """Whether the Interview prep tab is shown (D-176). Archived jobs keep their tab."""
    return application.status in TAB_STATUSES


def _check_writable(application: Application) -> None:
    if application.archived:
        raise Archived


def clean_question(text) -> str:
    """A question's text: trimmed, at least one character, at most MAX_QUESTION_TEXT."""
    value = str(text or "").strip()
    if not value or len(value) > MAX_QUESTION_TEXT:
        raise InvalidInput
    return value


def clean_long_text(text, limit: int) -> str | None:
    """An answer or the notes: may be empty (stored as None), at most `limit` characters.
    Not trimmed inside: line breaks are the user's own."""
    value = str(text or "")
    if len(value) > limit:
        raise InvalidInput
    return value if value.strip() else None


def clean_kind_and_category(kind, category) -> tuple[str, str | None]:
    """A likely question needs a known category; a question to ask has none (D-179)."""
    if kind not in KINDS:
        raise InvalidInput
    category = category or None
    if kind == "to_ask":
        if category is not None:
            raise InvalidInput
        return kind, None
    if category not in CATEGORIES:
        raise InvalidInput
    return kind, category


def _count(session: Session, user: User, application_id: int) -> int:
    return session.exec(
        select(func.count())
        .select_from(InterviewQuestion)
        .where(
            InterviewQuestion.user_id == user.id,
            InterviewQuestion.application_id == application_id,
        )
    ).one()


def _next_position(session: Session, user: User, application_id: int, kind: str) -> int:
    highest = session.exec(
        select(func.max(InterviewQuestion.position)).where(
            InterviewQuestion.user_id == user.id,
            InterviewQuestion.application_id == application_id,
            InterviewQuestion.kind == kind,
        )
    ).one()
    return 0 if highest is None else highest + 1


# --- Reading ------------------------------------------------------------------------


def get_owned_question(
    session: Session, user: User, application: Application, question_id
) -> InterviewQuestion:
    """The question `question_id` of this application and user, otherwise 404. A
    question of another application (even the user's own) is a 404 too."""
    try:
        number = int(question_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404) from None
    if not 0 < number <= MAX_ID:
        raise HTTPException(status_code=404)
    question = session.exec(
        select(InterviewQuestion).where(
            InterviewQuestion.id == number,
            InterviewQuestion.user_id == user.id,
            InterviewQuestion.application_id == application.id,
        )
    ).first()
    if question is None:
        raise HTTPException(status_code=404)
    return question


def questions_of(session: Session, user: User, application: Application) -> dict[str, list]:
    """The questions by kind. Likely ones are in category order (CATEGORIES), then by
    position; questions to ask by position. Reads only."""
    rows = session.exec(
        select(InterviewQuestion)
        .where(
            InterviewQuestion.user_id == user.id,
            InterviewQuestion.application_id == application.id,
        )
        .order_by(col(InterviewQuestion.position), col(InterviewQuestion.id))
    ).all()
    likely = sorted(
        (row for row in rows if row.kind == "likely"),
        key=lambda row: CATEGORIES.index(row.category) if row.category in CATEGORIES else 99,
    )  # sorted() keeps the position order inside a category
    return {"likely": likely, "to_ask": [row for row in rows if row.kind == "to_ask"]}


def answered_count(likely: list[InterviewQuestion]) -> int:
    """How many likely questions have an answer draft ("{n} of {m} answered")."""
    return sum(1 for question in likely if question.answer)


def interview_line(application: Application) -> str:
    """ "Wed, 2 Dec 2026, 14:00 · Video call · with Mira Huber" for the top of the tab;
    "" without a date. An all-day interview has no time (like the next step)."""
    if application.interview_at is None:
        return ""
    local = clock.local(application.interview_at)
    when = local.strftime("%a, %-d %b %Y")
    if not clock.is_start_of_day(application.interview_at):
        when += local.strftime(", %H:%M")
    parts = [when]
    if application.interview_type:
        parts.append(next_step_service.INTERVIEW_TYPE_NAMES.get(application.interview_type, ""))
    if application.interview_with:
        parts.append(f"with {application.interview_with}")
    return " · ".join(part for part in parts if part)


def tab_context(session: Session, user: User, application: Application) -> dict:
    """What the Interview prep tab and its cards show (D-176…D-179). Reads only (D-178)."""
    questions = questions_of(session, user, application)
    likely = questions["likely"]
    groups = [
        (key, CATEGORY_TITLES[key], [question for question in likely if question.category == key])
        for key in CATEGORIES
    ]
    return {
        "prep_started": application.interview_prep_started_at is not None,
        "prep_editable": not application.archived,
        "prep_groups": [group for group in groups if group[2]],
        "prep_likely_count": len(likely),
        "prep_answered": answered_count(likely),
        "prep_to_ask": questions["to_ask"],
        "prep_interview_line": interview_line(application),
        "prep_categories": CATEGORY_TITLES,
        "max_question_text": MAX_QUESTION_TEXT,
        "max_answer": MAX_ANSWER,
        "max_interview_notes": MAX_INTERVIEW_NOTES,
    }


# --- Writing ------------------------------------------------------------------------


def start(session: Session, user: User, application: Application, with_starters: bool) -> bool:
    """Start the prep (D-178): with the starter set, or with an empty list. Only the
    first call does anything; it returns False when the prep was already started (two
    tabs, a double click). The check and the write are one UPDATE, so two requests at
    the same moment can't both add the set (plan-reviewer M14a)."""
    _check_writable(application)
    result = session.exec(
        update(Application)
        .where(
            col(Application.id) == application.id,
            col(Application.user_id) == user.id,
            col(Application.interview_prep_started_at).is_(None),
        )
        .values(interview_prep_started_at=utcnow())
    )
    if result.rowcount != 1:
        session.rollback()
        return False
    if with_starters:
        company = application.company
        for position, (category, text) in enumerate(STARTER_QUESTIONS):
            session.add(
                InterviewQuestion(
                    user_id=user.id,
                    application_id=application.id,
                    kind="likely",
                    category=category,
                    text=text.format(company=company)[:MAX_QUESTION_TEXT],
                    position=position,
                )
            )
        for position, text in enumerate(STARTER_TO_ASK):
            session.add(
                InterviewQuestion(
                    user_id=user.id,
                    application_id=application.id,
                    kind="to_ask",
                    text=text,
                    position=position,
                )
            )
    session.commit()
    session.refresh(application)
    return True


def add(
    session: Session, user: User, application: Application, kind, category, text
) -> InterviewQuestion:
    """A new question at the end of its kind. Also starts the prep, so the empty
    state with its two buttons doesn't come back."""
    _check_writable(application)
    kind, category = clean_kind_and_category(kind, category)
    text = clean_question(text)
    if _count(session, user, application.id) >= MAX_QUESTIONS:
        raise TooManyQuestions
    question = InterviewQuestion(
        user_id=user.id,
        application_id=application.id,
        kind=kind,
        category=category,
        text=text,
        position=_next_position(session, user, application.id, kind),
    )
    session.add(question)
    if application.interview_prep_started_at is None:
        application.interview_prep_started_at = utcnow()
        session.add(application)
    session.commit()
    session.refresh(question)
    return question


def update_question(
    session: Session, application: Application, question: InterviewQuestion, text, category
) -> None:
    """Change a question's text (and category, for a likely question)."""
    _check_writable(application)
    question.text = clean_question(text)
    _, question.category = clean_kind_and_category(question.kind, category)
    session.add(question)
    session.commit()


def save_answer(
    session: Session, application: Application, question: InterviewQuestion, answer
) -> None:
    """My answer draft (likely) or their answer (to ask), saved while typing (D-179)."""
    _check_writable(application)
    question.answer = clean_long_text(answer, MAX_ANSWER)
    session.add(question)
    session.commit()


def set_asked(
    session: Session, application: Application, question: InterviewQuestion, asked: bool
) -> None:
    """Tick a question to ask as asked, or untick it. Likely questions have no tick."""
    _check_writable(application)
    if question.kind != "to_ask":
        raise InvalidInput
    question.asked = asked
    session.add(question)
    session.commit()


def save_notes(session: Session, application: Application, notes) -> None:
    """The company and role notes, saved while typing (D-179)."""
    _check_writable(application)
    application.interview_notes = clean_long_text(notes, MAX_INTERVIEW_NOTES)
    session.add(application)
    session.commit()


def delete(
    session: Session, user: User, application: Application, question: InterviewQuestion
) -> int:
    """Delete a question, keeping a copy for Undo (D-086). Returns the copy's id."""
    _check_writable(application)
    copy = trash_service.remember(
        session, user, "interview_question", trash_service.payload_of(question)
    )
    session.delete(question)
    session.commit()
    return copy.id


def restore(session: Session, user: User, payload: dict) -> None:
    """Undo a delete (not committed). The application must still be the user's, not
    archived, and have room (plan-reviewer M14a); otherwise CannotRestore."""
    if not isinstance(payload, dict):
        raise trash_service.CannotRestore
    application_id = payload.get("application_id")
    if type(application_id) is not int or not 0 < application_id <= MAX_ID:
        raise trash_service.CannotRestore
    application = session.exec(
        select(Application).where(Application.id == application_id, Application.user_id == user.id)
    ).first()
    if application is None or application.archived:
        raise trash_service.CannotRestore
    if _count(session, user, application.id) >= MAX_QUESTIONS:
        raise trash_service.CannotRestore
    question = trash_service.row_from(InterviewQuestion, payload)
    # The copy's values are checked like a form, so a broken copy can't store junk.
    try:
        question.kind, question.category = clean_kind_and_category(question.kind, question.category)
        question.text = clean_question(question.text)
        question.answer = clean_long_text(question.answer, MAX_ANSWER)
    except InvalidInput:
        raise trash_service.CannotRestore from None
    question.user_id = user.id
    question.application_id = application.id
    session.add(question)
    session.flush()
