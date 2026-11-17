"""The next step rules (SPEC §6.13, D-073). Plain functions: no database, a fixed `now`."""

import datetime as dt

import pytest

from app.models import Application
from app.services import clock
from app.services.next_step_service import after_done, interview_has_passed, next_step

UTC = dt.UTC
# Monday 16 November 2026, 10:00 in Vienna.
NOW = dt.datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
TODAY = dt.date(2026, 11, 16)


def job(status="saved", **fields) -> Application:
    fields.setdefault("created_at", NOW)
    return Application(user_id=1, company="Acme", job_title="PM", status=status, **fields)


def vienna(day: dt.date, hour=None, minute=0) -> dt.datetime:
    return clock.to_utc(day, None if hour is None else dt.time(hour, minute))


def day_of(step) -> dt.date:
    return clock.local(step.at).date()


# --- Saved: Apply by ----------------------------------------------------------------------


def test_saved_without_a_closing_date_waits_seven_days():
    step = next_step(job(created_at=vienna(dt.date(2026, 11, 3), 22)), NOW)

    assert (step.type, day_of(step), step.all_day) == ("apply_by", dt.date(2026, 11, 10), True)
    assert step.automatic and step.waiting
    assert step.is_due(TODAY)


def test_saved_with_a_closing_date_is_one_day_before_it():
    step = next_step(job(posting_closes_at=dt.date(2026, 11, 20)), NOW)

    assert (step.type, day_of(step)) == ("apply_by", dt.date(2026, 11, 19))
    assert not step.waiting  # a real deadline
    assert not step.is_due(TODAY)
    assert step.is_due(dt.date(2026, 11, 19))


def test_saved_late_in_the_evening_counts_in_vienna():
    # 23:30 UTC on the 2nd is already the 3rd in Vienna: +7 is the 10th.
    step = next_step(job(created_at=dt.datetime(2026, 11, 2, 23, 30, tzinfo=UTC)), NOW)

    assert day_of(step) == dt.date(2026, 11, 10)


# --- Applied: Follow up -------------------------------------------------------------------


def test_applied_follows_up_ten_days_after_applying():
    step = next_step(job("applied", applied_at=dt.date(2026, 11, 6)), NOW)

    assert (step.type, day_of(step)) == ("follow_up", dt.date(2026, 11, 16))
    assert step.is_due(TODAY)


def test_applied_without_a_date_counts_from_saving():
    step = next_step(job("applied", created_at=vienna(dt.date(2026, 11, 1), 9)), NOW)

    assert day_of(step) == dt.date(2026, 11, 11)


# --- Interview → thank-you -----------------------------------------------------------------


def test_interview_without_a_date_asks_for_it():
    step = next_step(job("interview"), NOW)

    assert (step.type, step.at) == ("interview", None)
    assert not step.is_due(TODAY)


def test_an_upcoming_interview_is_the_next_step():
    at = vienna(dt.date(2026, 11, 18), 10)

    step = next_step(job("interview", interview_at=at), NOW)

    assert (step.type, step.at, step.all_day) == ("interview", at, False)


def test_after_the_interview_time_a_thank_you_note_the_same_day():
    at = vienna(TODAY, 9, 30)  # 30 minutes ago

    step = next_step(job("interview", interview_at=at), NOW)

    assert (step.type, day_of(step)) == ("thank_you", TODAY)
    assert step.is_due(TODAY)


def test_an_all_day_interview_is_over_when_its_day_ends():
    at = vienna(TODAY)  # today, no time

    assert next_step(job("interview", interview_at=at), NOW).type == "interview"
    tomorrow_morning = vienna(dt.date(2026, 11, 17), 8)
    step = next_step(job("interview", interview_at=at), tomorrow_morning)
    assert (step.type, day_of(step)) == ("thank_you", dt.date(2026, 11, 17))


@pytest.mark.parametrize(
    ("interview", "now", "passed"),
    [
        (vienna(TODAY, 10), vienna(TODAY, 9, 59), False),
        (vienna(TODAY, 10), vienna(TODAY, 10), True),
        (vienna(TODAY), vienna(TODAY, 23, 59), False),
        (vienna(TODAY), vienna(dt.date(2026, 11, 17)), True),
    ],
)
def test_interview_has_passed(interview, now, passed):
    assert interview_has_passed(interview, now) is passed


# --- Offer, Rejected, archived ---------------------------------------------------------------


def test_an_offer_needs_its_deadline_from_the_user():
    step = next_step(job("offer"), NOW)

    assert (step.type, step.at) == ("reply_offer", None)


@pytest.mark.parametrize("status", ["rejected", "withdrawn"])
def test_closed_jobs_have_no_next_step(status):
    assert next_step(job(status), NOW) is None


def test_an_archived_job_has_no_next_step_even_one_set_by_hand():
    archived = job(
        archived=True,
        next_action_manual=True,
        next_action_type="custom",
        next_action_at=vienna(TODAY),
    )

    assert next_step(archived, NOW) is None


# --- Set by hand (rule 3) ---------------------------------------------------------------------


def test_a_next_step_set_by_hand_wins_over_the_rules():
    at = vienna(dt.date(2026, 11, 20), 9)

    step = next_step(
        job(
            "applied",
            applied_at=dt.date(2026, 11, 1),
            next_action_manual=True,
            next_action_type="custom",
            next_action_at=at,
            next_action_note="Call Mira",
        ),
        NOW,
    )

    assert (step.type, step.at, step.automatic, step.label) == ("custom", at, False, "Call Mira")


def test_done_by_hand_means_no_next_step():
    assert next_step(job("applied", next_action_manual=True, next_action_type=None), NOW) is None


def test_labels():
    assert next_step(job("applied"), NOW).label == "Follow up"
    custom = job(next_action_manual=True, next_action_type="custom", next_action_note=None)
    assert next_step(custom, NOW).label == "Custom"


# --- What "Done" leaves behind ------------------------------------------------------------------


def test_done_on_the_thank_you_note_brings_a_follow_up_five_days_later():
    after_interview = job("interview", interview_at=vienna(dt.date(2026, 11, 12), 10))

    assert after_done(after_interview, NOW) == ("follow_up", vienna(dt.date(2026, 11, 21)))


@pytest.mark.parametrize(
    "application",
    [
        job("saved"),
        job("applied", applied_at=dt.date(2026, 11, 1)),
        job("interview", interview_at=vienna(dt.date(2026, 11, 20), 10)),  # before the interview
        job("offer"),
        # A follow-up that was itself set after a thank-you: Done ends it, no loop.
        job("interview", next_action_manual=True, next_action_type="follow_up"),
    ],
)
def test_done_on_anything_else_leaves_nothing(application):
    assert after_done(application, NOW) == (None, None)
