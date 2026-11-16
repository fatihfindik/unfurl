"""The next step of an application (SPEC §6.13, D-073): one thing to do and its date.

The automatic next step is worked out from the status every time it is needed, not
stored: rules like "the interview time has passed, now send a thank-you note" then
need no background job and are always right. Only a next step the user set by hand
("Change", "Done") is stored, in Application.next_action_*.

Plain functions: nothing here reads or writes the database.
"""

import datetime as dt
from dataclasses import dataclass

from app.models import Application
from app.services import clock

# Days in the rules of SPEC §6.13.
APPLY_BY_DAYS_BEFORE_CLOSING = 1
APPLY_BY_DAYS_AFTER_SAVING = 7  # when the posting's closing date is not known
FOLLOW_UP_DAYS_AFTER_APPLYING = 10
FOLLOW_UP_DAYS_AFTER_THANK_YOU = 5

LABELS = {
    "apply_by": "Apply by",
    "follow_up": "Follow up",
    "interview": "Interview",
    "thank_you": "Send thank-you note",
    "reply_offer": "Reply to offer",
    "custom": "Custom",
}


@dataclass(frozen=True)
class NextStep:
    type: str  # one of LABELS
    at: dt.datetime | None  # UTC; None = no date yet (e.g. an offer without a deadline)
    automatic: bool  # True: from the status; False: set by hand
    note: str | None = None  # the text of a "custom" step
    # True for the automatic "Apply by, 7 days after saving": nothing is really due,
    # the job is just waiting (Needs attention calls it "Waiting", D-077).
    waiting: bool = False

    @property
    def label(self) -> str:
        return self.note if self.type == "custom" and self.note else LABELS[self.type]

    @property
    def all_day(self) -> bool:
        """True if the step has a date but no time."""
        return self.at is not None and clock.is_start_of_day(self.at)

    def is_due(self, today: dt.date) -> bool:
        """True if its day is today or has passed (SPEC §6.13 rule 4)."""
        return self.at is not None and clock.local(self.at).date() <= today


def next_step(application: Application, now: dt.datetime) -> NextStep | None:
    """The next step of `application` at the time `now` (UTC), or None."""
    if application.archived:
        return None  # archived jobs have no next step (a hand-set one comes back on Restore)
    if application.next_action_manual:
        if application.next_action_type is None:
            return None  # "Done": nothing until the status changes
        return NextStep(
            type=application.next_action_type,
            at=application.next_action_at,
            automatic=False,
            note=application.next_action_note,
        )
    return _automatic(application, now)


def _automatic(application: Application, now: dt.datetime) -> NextStep | None:
    status = application.status
    if status == "saved":
        if application.posting_closes_at:
            day = application.posting_closes_at - dt.timedelta(days=APPLY_BY_DAYS_BEFORE_CLOSING)
            return NextStep("apply_by", clock.to_utc(day), automatic=True)
        saved_on = clock.local(application.created_at).date()
        day = saved_on + dt.timedelta(days=APPLY_BY_DAYS_AFTER_SAVING)
        return NextStep("apply_by", clock.to_utc(day), automatic=True, waiting=True)

    if status == "applied":
        applied_on = application.applied_at or clock.local(application.created_at).date()
        day = applied_on + dt.timedelta(days=FOLLOW_UP_DAYS_AFTER_APPLYING)
        return NextStep("follow_up", clock.to_utc(day), automatic=True)

    if status == "interview":
        interview = application.interview_at
        if interview is None:
            return NextStep("interview", None, automatic=True)  # date not given yet
        if not interview_has_passed(interview, now):
            return NextStep("interview", interview, automatic=True)
        # After the interview: a thank-you note, the same day (an all-day interview:
        # the day after, because it only counts as over when that day ends).
        day = clock.local(interview).date()
        if clock.is_start_of_day(interview):
            day += dt.timedelta(days=1)
        return NextStep("thank_you", clock.to_utc(day), automatic=True)

    if status == "offer":
        return NextStep("reply_offer", None, automatic=True)  # the deadline comes from the user

    return None  # rejected, withdrawn


def interview_has_passed(interview_at: dt.datetime, now: dt.datetime) -> bool:
    """A timed interview has passed at its start time; an interview without a time
    (00:00, "all day") when its day is over."""
    if clock.is_start_of_day(interview_at):
        return clock.local(now).date() > clock.local(interview_at).date()
    return now >= interview_at


def after_done(application: Application, now: dt.datetime) -> tuple[str | None, dt.datetime | None]:
    """What "Done" leaves as the hand-set next step (type, date): after the thank-you
    note a follow-up 5 days later, after anything else nothing (D-073)."""
    step = next_step(application, now)
    if step is not None and step.type == "thank_you":
        day = clock.local(now).date() + dt.timedelta(days=FOLLOW_UP_DAYS_AFTER_THANK_YOU)
        return "follow_up", clock.to_utc(day)
    return None, None
