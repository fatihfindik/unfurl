"""Interview prep (SPEC §5.5b, M14a, D-177): the questions of one application.

Two kinds of question share one table:
- "likely": a question the interviewers may ask me, with my answer draft in `answer`
- "to_ask": a question I want to ask them, `asked` once I did, their answer in `answer`

The company and role notes are one text column on Application (`interview_notes`).
The rules live in app/services/interview_prep_service.py.
"""

import datetime as dt

from sqlalchemy import Column, Index, Text
from sqlmodel import Field, SQLModel

from app.models.base import created_at_field, updated_at_field

# Stored as text, not as database enums (like Application.status, D-025).
KINDS = ("likely", "to_ask")
# Only "likely" questions have a category; the list is also the order on the page.
CATEGORIES = ("general", "behavioural", "role", "company", "other")

MAX_QUESTIONS = 100  # per application, both kinds together
MAX_QUESTION_TEXT = 300
MAX_ANSWER = 5000
MAX_INTERVIEW_NOTES = 10_000  # Application.interview_notes


class InterviewQuestion(SQLModel, table=True):
    __tablename__ = "interviewquestion"
    __table_args__ = (
        # The tab reads one application's questions, kind by kind, in order.
        Index("ix_interviewquestion_list", "application_id", "kind", "position"),
    )

    id: int | None = Field(default=None, primary_key=True)
    # Every query filters by user_id too (CLAUDE.md), not only by application.
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    # No index of its own: ix_interviewquestion_list starts with application_id.
    application_id: int = Field(foreign_key="application.id", ondelete="CASCADE")

    kind: str = Field(max_length=10)  # one of KINDS
    category: str | None = Field(default=None, max_length=20)  # one of CATEGORIES, "likely" only
    text: str = Field(max_length=MAX_QUESTION_TEXT)
    answer: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    asked: bool = Field(default=False)  # "to_ask" only
    position: int = Field(default=0)

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()
