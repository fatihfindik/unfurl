"""Rules for applications (SPEC §5.5, §5.6; D-061, D-062): adding a job, who owns it,
where it was found."""

import datetime as dt

import pytest
from fastapi import HTTPException
from freezegun import freeze_time

from app.models import Application
from app.services import application_service as apps
from app.services.auth_service import create_user


@pytest.fixture
def other(session):
    return create_user(session, email="other@example.com", password="x" * 12, full_name="Other")


def add(session, user, company="Kranich Health", **fields) -> Application:
    fields.setdefault("job_title", "Product Manager")
    return apps.create(session, user, company=company, **fields)


def column(session, user, status="saved") -> list[str]:
    return [card.company for card in apps.column_of(session, user, status)]


# --- Adding a job ---------------------------------------------------------------------


def test_a_new_job_is_saved_with_a_created_event(session, user):
    job = add(session, user, location="Vienna", remote_type="hybrid")

    assert (job.status, job.archived, job.applied_at) == ("saved", False, None)
    assert (job.location, job.remote_type, job.source) == ("Vienna", "hybrid", "manual")
    assert job.description_raw == ""
    events = apps.events_of(session, job)
    assert [(e.type, e.from_status, e.to_status) for e in events] == [("created", None, "saved")]


def test_a_new_job_goes_to_the_top_of_its_column(session, user):
    add(session, user, "First")
    add(session, user, "Second")
    add(session, user, "Elsewhere", status="applied")
    add(session, user, "Third")

    assert column(session, user) == ["Third", "Second", "First"]
    assert [card.position for card in apps.column_of(session, user, "saved")] == [0, 1, 2]
    assert column(session, user, "applied") == ["Elsewhere"]


@freeze_time("2026-11-16 23:30:00")
def test_a_job_added_as_applied_gets_todays_date(session, user):
    # 23:30 UTC is 00:30 on the 17th in Vienna: the user's day counts (D-071).
    assert add(session, user, status="applied").applied_at == dt.date(2026, 11, 17)
    assert add(session, user, status="saved").applied_at is None


def test_the_source_comes_from_the_link(session, user):
    job = add(session, user, job_url="https://www.linkedin.com/jobs/view/123")

    assert job.source == "linkedin"


def test_there_is_a_limit_per_user(session, user, other, monkeypatch):
    monkeypatch.setattr(apps, "MAX_APPLICATIONS_PER_USER", 2)
    add(session, user, "One")
    add(session, user, "Two")

    with pytest.raises(apps.TooManyApplications):
        add(session, user, "Three")
    add(session, other, "Theirs")  # someone else's count is their own


def test_other_users_columns_are_not_touched(session, user, other):
    theirs = add(session, other, "Theirs")
    add(session, user, "Mine")
    add(session, user, "Mine too")

    session.refresh(theirs)
    assert theirs.position == 0
    assert column(session, other) == ["Theirs"]
    assert column(session, user) == ["Mine too", "Mine"]


# --- Who owns it ----------------------------------------------------------------------


def test_the_owner_gets_the_application(session, user):
    job = add(session, user)

    assert apps.get_owned_application(session, user, str(job.id)).id == job.id


@pytest.mark.parametrize("bad_id", ["abc", "", "0", "-1", "1.5", str(2**31), str(2**70), None])
def test_an_id_that_is_not_one_is_a_404(session, user, bad_id):
    add(session, user)

    with pytest.raises(HTTPException) as error:
        apps.get_owned_application(session, user, bad_id)
    assert error.value.status_code == 404


def test_another_users_application_is_a_404(session, user, other):
    theirs = add(session, other)

    with pytest.raises(HTTPException) as error:
        apps.get_owned_application(session, user, theirs.id)
    assert error.value.status_code == 404


def test_deleting_a_user_deletes_their_applications_and_events(session, user):
    job = add(session, user)
    job_id = job.id

    session.delete(user)
    session.commit()
    session.expire_all()

    assert session.get(Application, job_id) is None
    assert apps.events_of(session, Application(id=job_id)) == []


# --- Where a job was found (D-062) ----------------------------------------------------


@pytest.mark.parametrize(
    ("url", "source"),
    [
        (None, "manual"),
        ("", "manual"),
        ("https://www.linkedin.com/jobs/view/123", "linkedin"),
        ("https://at.linkedin.com/jobs/view/123", "linkedin"),
        ("https://LINKEDIN.com/jobs", "linkedin"),
        ("https://www.stepstone.at/stellenangebote--pm-wien", "stepstone"),
        ("https://www.stepstone.de/job/1", "stepstone"),
        ("https://www.karriere.at/jobs/123", "karriere_at"),
        ("https://at.indeed.com/viewjob?jk=1", "indeed"),
        ("https://www.indeed.com/viewjob?jk=1", "indeed"),
        ("https://www.xing.com/jobs/wien-pm-1", "xing"),
        ("https://jobs.kranich-health.at/careers/pm", "company_site"),
        # Look-alikes are not the real site.
        ("https://linkedin.com.evil.example/jobs", "company_site"),
        ("https://notlinkedin.com/jobs", "company_site"),
        ("https://example.com/linkedin.com/jobs", "company_site"),
        ("https://my-stepstone.example/job", "company_site"),
        ("https://jobs.indeed", "company_site"),
    ],
)
def test_detect_source(url, source):
    assert apps.detect_source(url) == source


# --- Moving a card (D-063) ------------------------------------------------------------


@pytest.fixture
def saved(session, user) -> dict[str, Application]:
    """Three saved jobs; the column reads C, B, A from the top."""
    return {name: add(session, user, name) for name in "ABC"}


def positions(session, user, status="saved") -> list[tuple[str, int]]:
    return [(card.company, card.position) for card in apps.column_of(session, user, status)]


def test_moving_to_another_column_changes_the_status_and_writes_an_event(session, user, saved):
    event = apps.move(session, user, saved["B"], "applied")

    assert (event.type, event.from_status, event.to_status) == (
        "status_changed",
        "saved",
        "applied",
    )
    assert positions(session, user) == [("C", 0), ("A", 1)]  # the gap is closed
    assert positions(session, user, "applied") == [("B", 0)]
    assert [e.type for e in apps.events_of(session, saved["B"])] == ["created", "status_changed"]


def test_a_card_lands_below_the_card_it_was_dropped_under(session, user, saved):
    for name in "XY":
        add(session, user, name, status="applied")  # applied reads Y, X

    apps.move(
        session,
        user,
        saved["A"],
        "applied",
        after_id=apps.column_of(session, user, "applied")[0].id,
    )

    assert positions(session, user, "applied") == [("Y", 0), ("A", 1), ("X", 2)]


def test_without_a_card_above_it_goes_to_the_top(session, user, saved):
    add(session, user, "X", status="applied")

    apps.move(session, user, saved["A"], "applied", after_id="")

    assert column(session, user, "applied") == ["A", "X"]


def test_reordering_inside_a_column_writes_no_event(session, user, saved):
    before = saved["A"].last_activity_at

    event = apps.move(session, user, saved["A"], "saved", after_id=saved["C"].id)

    assert event is None
    assert positions(session, user) == [("C", 0), ("A", 1), ("B", 2)]
    assert [e.type for e in apps.events_of(session, saved["A"])] == ["created"]
    assert saved["A"].last_activity_at == before


def test_dropping_a_card_on_itself_changes_nothing(session, user, saved):
    assert apps.move(session, user, saved["B"], "saved", after_id=saved["B"].id) is None
    assert column(session, user) == ["C", "B", "A"]


def test_moving_one_card_does_not_make_the_others_look_updated(session, user, saved):
    # Renumbering touches the neighbours' rows; "Updated" must not change (D-061).
    with freeze_time("2026-11-01"):
        old = {name: add(session, user, f"old {name}") for name in "PQ"}
    before = {name: card.last_activity_at for name, card in old.items()}

    with freeze_time("2026-11-16"):
        apps.move(session, user, saved["A"], "applied")
        apps.move(session, user, saved["B"], "saved", after_id=old["P"].id)

    for name, card in old.items():
        session.refresh(card)
        assert card.last_activity_at == before[name]
    session.refresh(saved["A"])
    assert saved["A"].last_activity_at.date() == dt.date(2026, 11, 16)


@freeze_time("2026-11-16 09:00:00")
def test_applied_at_is_set_the_first_time_only(session, user, saved):
    job = saved["A"]

    apps.move(session, user, job, "applied")
    assert job.applied_at == dt.date(2026, 11, 16)

    with freeze_time("2026-11-20"):
        apps.move(session, user, job, "interview")
        apps.move(session, user, job, "applied")
    assert job.applied_at == dt.date(2026, 11, 16)  # the first date stays


def test_a_card_above_from_an_old_screen_sends_the_card_to_the_top(session, user, saved):
    add(session, user, "X", status="applied")

    # "C" is in Saved, not in Applied: the browser's picture of the column was old.
    apps.move(session, user, saved["A"], "applied", after_id=saved["C"].id)

    assert column(session, user, "applied") == ["A", "X"]


def test_another_users_card_cannot_be_the_card_above(session, user, other, saved):
    theirs = add(session, other, "Theirs", status="applied")

    with pytest.raises(HTTPException) as error:
        apps.move(session, user, saved["A"], "applied", after_id=theirs.id)

    assert error.value.status_code == 404
    session.expire_all()
    assert column(session, user) == ["C", "B", "A"]
    assert column(session, other, "applied") == ["Theirs"]


@pytest.mark.parametrize("status", ["withdrawn", "archived", "", "SAVED", "saved; drop table"])
def test_only_board_columns_are_accepted(session, user, saved, status):
    with pytest.raises(apps.NotOnBoard):
        apps.move(session, user, saved["A"], status)


def test_an_archived_card_cannot_be_moved(session, user, saved):
    saved["A"].archived = True
    session.add(saved["A"])
    session.commit()

    with pytest.raises(apps.NotOnBoard):
        apps.move(session, user, saved["A"], "applied")


def test_moving_does_not_touch_another_users_columns(session, user, other, saved):
    theirs = [add(session, other, name) for name in ("T1", "T2")]

    apps.move(session, user, saved["A"], "applied")
    apps.move(session, user, saved["B"], "saved", after_id="")

    assert positions(session, other) == [("T2", 0), ("T1", 1)]
    assert all(card.status == "saved" for card in theirs)


def test_positions_stay_unique_after_many_moves(session, user, saved):
    for name, status in [
        ("A", "applied"),
        ("B", "applied"),
        ("A", "saved"),
        ("C", "offer"),
        ("B", "saved"),
    ]:
        apps.move(session, user, saved[name], status)

    for status in ("saved", "applied", "offer"):
        numbers = [position for _, position in positions(session, user, status)]
        assert numbers == list(range(len(numbers)))
