"""The detail page of an application (SPEC §6.7, D-073, D-078; M4b step 2)."""

import datetime as dt
import re

import pytest

from app.models import Application
from app.models.base import utcnow
from app.services import application_service as apps
from app.services import clock
from app.services.auth_service import create_user


def add(session, user, company="Kranich Health GmbH", **fields) -> Application:
    fields.setdefault("job_title", "Senior Product Manager")
    return apps.create(session, user, company=company, **fields)


def page(client, job, tab: str = "") -> str:
    response = client.get(f"/applications/{job.id}" + (f"?tab={tab}" if tab else ""))
    assert response.status_code == 200
    return response.text


def details_page(client, job) -> str:
    """The Job & details tab (D-164): the details card and the saved posting."""
    return page(client, job, "details")


def save(session, job, **fields) -> Application:
    for name, value in fields.items():
        setattr(job, name, value)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


# --- Header ----------------------------------------------------------------------------------


def test_the_header_shows_the_job(auth_client, session, user):
    job = add(
        session,
        user,
        location="Vienna",
        remote_type="hybrid",
        job_url="https://www.linkedin.com/jobs/view/1",
    )

    html = page(auth_client, job)

    assert "<h1 class" in html and ">Senior Product Manager</h1>" in html
    assert "Kranich Health GmbH · Vienna · Hybrid ·" in html
    assert "saved from LinkedIn today" in html
    assert ">KH</span>" in html  # the company avatar
    # The same "← Parent" link as the editor and the profile (SPEC §6.0/3).
    assert re.search(r'<a href="/" class="[^"]*text-link[^"]*">← Applications</a>', html)


def test_view_posting_opens_the_link_safely(auth_client, session, user):
    job = add(session, user, job_url="https://jobs.example.com/pm?a=1&b=2")

    html = page(auth_client, job)

    link = re.search(r'<a href="([^"]+)" target="_blank" rel="noopener noreferrer"', html)
    assert link and link.group(1) == "https://jobs.example.com/pm?a=1&amp;b=2"
    assert "View posting ↗" in html


def test_without_a_link_there_is_no_view_posting(auth_client, session, user):
    assert "View posting" not in page(auth_client, add(session, user))


def test_position_in_the_column(auth_client, session, user):
    first = add(session, user, "First")
    middle = add(session, user, "Middle")
    top = add(session, user, "Top")  # Saved reads Top, Middle, First
    add(session, user, "Elsewhere", status="applied")

    html = page(auth_client, middle)

    assert "2 of 3 in Saved" in html
    assert f'href="/applications/{top.id}" aria-label="Previous application"' in html
    assert f'href="/applications/{first.id}" aria-label="Next application"' in html
    assert "1 of 3 in Saved" in page(auth_client, top)
    assert 'aria-label="Previous application"' not in page(auth_client, top)


def test_an_archived_job_counts_among_the_archived(auth_client, session, user):
    job = add(session, user, "Old")
    add(session, user, "Active")
    apps.archive(session, user, job)

    html = page(auth_client, job)

    assert "1 of 1 in archived Saved" in html
    assert '<option value="archived" selected>Archived</option>' in html  # the status picker


# --- Tabs (D-164) ----------------------------------------------------------------------------


def tab_links(html: str) -> str:
    return re.search(r'<nav aria-label="Application sections".*?</nav>', html, re.S).group(0)


def test_a_saved_job_opens_on_apply(auth_client, session, user):
    job = add(session, user)
    html = page(auth_client, job)

    tabs = tab_links(html)
    assert ">Apply</a>" in tabs and ">Job &amp; details</a>" in tabs and "Progress" not in tabs
    assert re.search(r'aria-current="page"\s+class="[^"]*">Apply<', tabs)
    assert 'id="apply"' in html and 'id="application-details"' not in html


@pytest.mark.parametrize("status", ["applied", "interview", "offer", "rejected"])
def test_a_sent_or_answered_job_opens_on_progress(auth_client, session, user, status):
    job = save(session, add(session, user), status=status)
    html = page(auth_client, job)

    assert ">Progress</a>" in tab_links(html) and ">Apply</a>" not in tab_links(html)
    assert 'id="progress"' in html and 'id="apply"' not in html


def test_details_are_on_their_own_tab(auth_client, session, user):
    job = add(session, user)
    html = details_page(auth_client, job)

    assert re.search(r'aria-current="page"\s+class="[^"]*">Job &amp; details<', tab_links(html))
    assert 'id="application-details"' in html and 'id="apply"' not in html


@pytest.mark.parametrize("tab", ["apply", "progress", "nonsense", "<script>"])
def test_an_unknown_tab_shows_the_first_one(auth_client, session, user, tab):
    html = page(auth_client, add(session, user), tab)

    assert 'id="apply"' in html and "<script>" not in tab_links(html)


def test_the_page_reloads_the_tab_it_shows(auth_client, session, user):
    job = add(session, user)

    assert f'hx-get="/applications/{job.id}?tab=details"' in details_page(auth_client, job)
    assert f'hx-get="/applications/{job.id}" hx-trigger' in page(auth_client, job)


def test_an_archived_job_opens_on_the_tab_of_its_status(auth_client, session, user):
    job = save(session, add(session, user), status="applied", archived=True)

    assert 'id="progress"' in page(auth_client, job)


def test_progress_says_when_the_job_was_not_applied_to(auth_client, session, user):
    job = save(session, add(session, user), status="rejected")

    assert "You did not apply to this job." in page(auth_client, job)


def test_details_show_dashes_when_empty(auth_client, session, user):
    html = details_page(auth_client, add(session, user))

    details = re.search(r'<section id="application-details".*?</section>', html, re.S).group(0)
    # Contact, email, salary, work mode, closing date and notes are empty; the source is known.
    assert details.count("—") == 6
    assert "Added by hand" in details


def test_details_show_what_is_known(auth_client, session, user):
    job = save(
        session,
        add(session, user, remote_type="remote"),
        contact_name="Mira Hofer",
        contact_email="mira@example.com",
        salary_text="ab € 72.000 brutto / Jahr",
        posting_closes_at=dt.date(2026, 12, 3),
        notes="Referral via Lukas.\nCall before Friday.",
    )

    html = details_page(auth_client, job)

    for text in [
        "Mira Hofer",
        "mira@example.com",
        "ab € 72.000 brutto / Jahr",
        "Remote",
        "Thu, 3 Dec",
        "Referral via Lukas.\nCall before Friday.",
    ]:
        assert text in html


def test_the_saved_posting_is_plain_text_and_folded(auth_client, session, user):
    text = "Deine Aufgaben:\n<script>alert(1)</script>\nWir bieten: ab € 72.000"
    job = add(session, user, description_raw=text)

    html = details_page(auth_client, job)

    assert "<details" in html and "Original posting (saved copy)" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_without_a_saved_posting_the_box_is_hidden(auth_client, session, user):
    assert "Original posting" not in page(auth_client, add(session, user))


# --- Next step card ----------------------------------------------------------------------------


def next_step_html(html: str) -> str:
    return re.search(r'<section id="next-step".*?</section>', html, re.S).group(0)


def test_next_step_of_a_saved_job(auth_client, session, user):
    job = add(session, user)
    in_seven_days = clock.local(job.created_at).date() + dt.timedelta(days=7)

    card = next_step_html(page(auth_client, job))

    assert f"Apply by {in_seven_days.strftime('%a, %-d %b')}" in card
    assert "Set automatically from the status." in card


def test_next_step_of_an_applied_job(auth_client, session, user):
    job = save(session, add(session, user, status="applied"), applied_at=dt.date(2026, 11, 10))

    card = next_step_html(page(auth_client, job))

    assert "Follow up on Fri, 20 Nov" in card
    assert "10 days after applying, if you hear nothing." in card


def test_next_step_of_an_interview(auth_client, session, user):
    job = add(session, user)
    apps.move(session, user, job, "interview")
    upcoming = clock.to_utc(clock.today() + dt.timedelta(days=2), dt.time(10, 0))
    save(session, job, interview_at=upcoming, interview_type="video", interview_with="Mira Hofer")

    card = next_step_html(page(auth_client, job))

    assert "Interview ·" in card and "10:00" in card
    assert "Video call with Mira Hofer." in card


def test_closed_and_archived_jobs_say_why_there_is_no_next_step(auth_client, session, user):
    rejected = add(session, user, "Rejected Co")
    apps.move(session, user, rejected, "rejected")
    archived = add(session, user, "Archived Co")
    apps.archive(session, user, archived)

    assert "Rejected jobs have no next step." in page(auth_client, rejected)
    assert "Archived jobs have no next step." in page(auth_client, archived)


# --- Timeline ----------------------------------------------------------------------------------


def test_the_timeline_lists_what_happened_newest_first(auth_client, session, user):
    job = add(session, user, job_url="https://www.linkedin.com/jobs/view/1")
    apps.move(session, user, job, "applied")
    apps.archive(session, user, job)
    apps.restore(session, user, job)

    html = page(auth_client, job)

    timeline = re.search(r'<section id="timeline".*?</section>', html, re.S).group(0)
    texts = re.findall(r'text-\[13px\] text-text">([^<]*)</span>', timeline)
    assert texts == ["Restored", "Archived", "Status: Saved → Applied", "Saved from LinkedIn"]
    assert "Today, " in timeline


def test_the_timeline_says_when(auth_client, session, user):
    job = add(session, user)
    event = apps.events_of(session, job)[0]
    event.created_at = utcnow() - dt.timedelta(days=1)
    session.add(event)
    session.commit()

    assert "Yesterday, " in page(auth_client, job)


# --- Board and list link here ------------------------------------------------------------------


def test_cards_and_rows_link_to_the_detail_page(auth_client, session, user):
    job = add(session, user)

    assert f'href="/applications/{job.id}"' in auth_client.get("/").text
    assert f'href="/applications/{job.id}"' in auth_client.get("/?view=list").text


# --- Who may see it ----------------------------------------------------------------------------


def test_user_text_is_escaped(auth_client, session, user):
    job = add(
        session, user, "<b>Evil</b> Co", job_title="<script>x</script>", location="<i>Wien</i>"
    )

    html = page(auth_client, job)

    assert "<script>x</script>" not in html and "<b>Evil</b>" not in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html


def test_another_users_application_is_not_found(auth_client, session):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = add(session, other, "Their Secret GmbH")

    response = auth_client.get(f"/applications/{theirs.id}")

    assert response.status_code == 404
    assert "Their Secret" not in response.text


@pytest.mark.parametrize("bad_id", ["abc", "0", "-1", "1.5", str(2**31), str(2**70), "undo"])
def test_bad_ids_are_not_found(auth_client, bad_id):
    assert auth_client.get(f"/applications/{bad_id}").status_code == 404


def test_logged_out_visitors_go_to_login(client, session, user):
    job = add(session, user)

    response = client.get(f"/applications/{job.id}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_buttons_inside_the_page_do_not_inherit_its_hx_select(auth_client, session, user):
    # Without hx-disinherit, "Change" on the Next step card took over hx-select and its
    # answer was filtered down to nothing: the card disappeared (found in the browser).
    html = page(auth_client, add(session, user))

    assert 'hx-select="#application-page" hx-swap="outerHTML" hx-disinherit="*"' in html
