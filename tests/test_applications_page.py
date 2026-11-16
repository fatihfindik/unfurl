"""The Applications home page (SPEC §6.3, §6.7, plan M4a): summary cards and the board."""

import datetime as dt
import re

import pytest

from app.models.base import utcnow
from app.services import application_service as apps
from app.services.auth_service import create_user


def add(session, user, company="Kranich Health", **fields):
    fields.setdefault("job_title", "Product Manager")
    return apps.create(session, user, company=company, **fields)


def column_html(html: str, status: str) -> str:
    """The HTML of one board column."""
    return re.search(rf'<section[^>]*data-column="{status}".*?</section>', html, re.S).group(0)


def column_count(html: str, status: str) -> int:
    """The number in a column's heading."""
    heading = re.search(r"<h2.*?</h2>", column_html(html, status), re.S).group(0)
    return int(re.findall(r">(\d+)</span>", heading)[-1])


def test_without_jobs_the_page_says_so(auth_client):
    html = auth_client.get("/").text

    assert "No applications yet." in html
    assert "data-column=" not in html  # no empty board


def test_the_board_has_five_columns_in_order(auth_client, session, user):
    add(session, user)

    html = auth_client.get("/").text

    assert re.findall(r'data-column="(\w+)"', html) == [
        "saved",
        "applied",
        "interview",
        "offer",
        "rejected",
    ]
    assert "No applications yet." not in html
    assert "No jobs here" in column_html(html, "offer")
    assert "No jobs here" not in column_html(html, "saved")


def test_cards_are_in_their_column_newest_on_top(auth_client, session, user):
    add(session, user, "First")
    add(session, user, "Second")
    add(session, user, "Sent", status="applied")

    html = auth_client.get("/").text

    saved = column_html(html, "saved")
    assert saved.index("Second") < saved.index("First")
    assert "Sent" in column_html(html, "applied") and "Sent" not in saved
    assert (column_count(html, "saved"), column_count(html, "applied")) == (2, 1)
    assert column_count(html, "offer") == 0


def test_a_card_shows_place_source_and_when(auth_client, session, user):
    jobs = [
        add(
            session,
            user,
            "Nordbau Software",
            job_title="Senior PM",
            location="Vienna",
            remote_type="hybrid",
            job_url="https://www.linkedin.com/jobs/view/1",
        ),
        add(session, user, "By hand"),
    ]
    for job in jobs:  # as if they were added three days ago
        job.last_activity_at = utcnow() - dt.timedelta(days=3)
        session.add(job)
    session.commit()

    html = auth_client.get("/").text

    assert "Nordbau Software" in html and "Senior PM" in html
    assert "Vienna · Hybrid" in html
    assert "LinkedIn · 3 days ago" in html
    assert "Added by hand · 3 days ago" in html


def test_a_card_without_a_place_has_no_empty_line(auth_client, session, user):
    add(session, user, location="Graz")  # work mode unknown
    add(session, user, "Remote only", remote_type="remote")

    html = auth_client.get("/").text

    assert ">Graz<" in html and ">Remote<" in html
    assert "· Unknown" not in html and "unknown" not in column_html(html, "saved")


def test_user_text_is_escaped(auth_client, session, user):
    add(session, user, "<script>alert(1)</script>", job_title="<b>PM</b>", location="<i>x</i>")

    html = auth_client.get("/").text

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<b>PM</b>" not in html and "<i>x</i>" not in html


def test_another_users_jobs_are_never_shown(auth_client, session, user):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    add(session, other, "Their Secret GmbH")
    add(session, user, "Mine")

    html = auth_client.get("/").text

    assert "Their Secret GmbH" not in html
    assert column_count(html, "saved") == 1


def test_the_board_has_no_summary_cards(auth_client, session, user):
    # The column headings already show each status and its count (D-070).
    add(session, user)

    html = auth_client.get("/").text

    assert "data-summary=" not in html and "Pipeline summary" not in html
    assert "Jobs you are considering" not in html


def test_things_from_later_milestones_are_not_on_the_page(auth_client, session, user):
    add(session, user)

    html = auth_client.get("/").text

    for later in ["Fit", "fit score", "Auto-fill", "Needs attention", "Snooze", "extension"]:
        assert later not in html
    assert "/applications/" not in html  # cards don't link anywhere until M4b


def test_logged_out_visitors_go_to_login(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


# --- Dragging, "Show more" and the closed Rejected column (SPEC §6.7) ------------------


def test_every_column_is_a_drop_list_and_cards_carry_their_id(auth_client, session, user):
    job = add(session, user)

    html = auth_client.get("/").text

    for status in ("saved", "applied", "interview", "offer", "rejected"):
        assert f'data-board-list data-status="{status}"' in html
    assert f'data-card="{job.id}"' in column_html(html, "saved")


def test_rejected_starts_closed_but_still_takes_a_dropped_card(auth_client, session, user):
    add(session, user, "Declined GmbH", status="saved")
    job = apps.column_of(session, user, "saved")[0]
    apps.move(session, user, job, "rejected")

    html = auth_client.get("/").text

    assert 'data-rejected="closed"' in html  # the page starts with the column closed
    rejected = column_html(html, "rejected")
    assert "Hidden to keep the board calm. Drop a card here to reject it." in rejected
    assert ">Show</span>" in rejected and ">Hide</span>" in rejected
    assert 'data-board-list data-status="rejected"' in rejected  # still a drop list
    assert column_count(html, "rejected") == 1  # the count stays visible
    # Only Rejected can be closed.
    assert "toggleRejected" not in column_html(html, "saved")


def test_a_column_shows_twenty_cards_then_show_more(auth_client, session, user):
    for number in range(23):
        add(session, user, f"Company {number:02d}")
    add(session, user, "Sent", status="applied")

    html = auth_client.get("/").text

    saved = column_html(html, "saved")
    assert saved.count("data-card=") == 20
    assert column_count(html, "saved") == 23  # the heading counts all of them
    assert "Show more (3)" in saved and 'hx-get="/?more=saved"' in saved
    assert "Company 22" in saved and "Company 02" not in saved  # newest first
    assert "Show more" not in column_html(html, "applied")


def test_show_more_shows_the_whole_column_and_keeps_it_open_on_reload(auth_client, session, user):
    for number in range(23):
        add(session, user, f"Company {number:02d}")

    html = auth_client.get("/?more=saved", headers={"HX-Request": "true"}).text

    saved = column_html(html, "saved")
    assert saved.count("data-card=") == 23 and "Show more" not in saved
    # After the next change the board reloads with the same choice.
    assert 'hx-get="/?more=saved"' in html.split(">", 1)[0]


@pytest.mark.parametrize(
    "query",
    ["?more=moon", "?more=%22%3E%3Cscript%3Ealert(1)%3C/script%3E", "?more=saved%22+onload%3D%22x"],
)
def test_unknown_values_in_the_address_are_dropped(auth_client, session, user, query):
    add(session, user)

    html = auth_client.get("/" + query).text

    assert "alert(1)" not in html and "onload" not in html
    assert 'hx-get="/"' in html  # the region reloads the plain page
