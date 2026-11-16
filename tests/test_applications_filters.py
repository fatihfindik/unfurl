"""Search and filters on the Applications page (SPEC §6.3, §6.7, D-067, D-070)."""

import re

import pytest

from app.services import application_service as apps
from app.services.auth_service import create_user


def add(session, user, company, **fields):
    fields.setdefault("job_title", "Product Manager")
    return apps.create(session, user, company=company, **fields)


def rows(html: str) -> list[str]:
    return re.findall(r'<tr data-row="\d+"[^>]*>\s*<td[^>]*>([^<]*)</td>', html)


def cards(html: str) -> list[str]:
    return re.findall(r'data-card="\d+".*?<strong[^>]*>([^<]*)</strong>', html, re.S)


@pytest.fixture
def jobs(session, user):
    add(
        session,
        user,
        "Kranich Health",
        location="Wien",
        remote_type="hybrid",
        job_url="https://www.linkedin.com/jobs/view/1",
    )
    add(
        session,
        user,
        "Şişecam",
        job_title="Data Analyst",
        location="İstanbul",
        remote_type="onsite",
        job_url="https://www.stepstone.at/job/2",
    )
    add(session, user, "Österreich Post", location="Graz", remote_type="remote", status="applied")
    add(session, user, "Acme", job_title="Head of Data", status="interview")


def listed(client, query) -> list[str]:
    return sorted(rows(client.get(f"/?view=list&{query}").text))


# --- Search (D-067) --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("q", "found"),
    [
        ("kranich", ["Kranich Health"]),
        ("KRANICH", ["Kranich Health"]),
        ("istanbul", ["Şişecam"]),  # finds "İstanbul"
        ("İSTANBUL", ["Şişecam"]),
        ("sisecam", ["Şişecam"]),
        ("osterreich", ["Österreich Post"]),
        ("data", ["Acme", "Şişecam"]),  # the job title counts too
        ("graz", ["Österreich Post"]),  # and the city
        ("head data", ["Acme"]),  # every word must be found
        ("  wien  ", ["Kranich Health"]),
        ("nothing like this", []),
    ],
)
def test_search(auth_client, jobs, q, found):
    assert listed(auth_client, f"q={q}") == found


def test_search_works_on_the_board_too(auth_client, jobs):
    html = auth_client.get("/?q=data").text

    assert sorted(cards(html)) == ["Acme", "Şişecam"]
    column = re.search(r'data-column="interview".*?</h2>', html, re.S).group(0)
    assert ">1</span>" in column  # the column counts what it shows


def test_a_very_long_search_is_cut(auth_client, jobs):
    html = auth_client.get("/?view=list&q=" + "a" * 500).text

    assert 'value="' + "a" * 100 + '"' in html
    assert "a" * 101 not in html


# --- Filters ------------------------------------------------------------------------------


def test_filter_by_source(auth_client, jobs):
    assert listed(auth_client, "source=linkedin") == ["Kranich Health"]
    assert listed(auth_client, "source=manual") == ["Acme", "Österreich Post"]


def test_filter_by_work_mode(auth_client, jobs):
    assert listed(auth_client, "mode=remote") == ["Österreich Post"]
    assert listed(auth_client, "mode=hybrid&source=linkedin") == ["Kranich Health"]
    assert listed(auth_client, "mode=hybrid&source=stepstone") == []


def test_unknown_filter_values_mean_no_filter(auth_client, jobs):
    everything = listed(auth_client, "")

    assert listed(auth_client, "source=moon&mode=space&status=gone") == everything
    assert listed(auth_client, "mode=unknown") == everything  # not a choice in the menu


# --- Summary cards filter the list by status (D-070) ----------------------------------------


def test_a_summary_card_filters_the_list(auth_client, jobs):
    html = auth_client.get("/?view=list&status=applied").text

    assert rows(html) == ["Österreich Post"]
    card = re.search(r'<a [^>]*data-summary="applied"[^>]*>', html).group(0)
    assert 'aria-current="true"' in card
    assert 'href="/?view=list"' in card  # clicking it again removes the filter


def test_summary_cards_count_everything_whatever_the_filters(auth_client, jobs):
    html = auth_client.get("/?view=list&status=applied&q=zzz").text

    for status, count in [("saved", 2), ("applied", 1), ("interview", 1)]:
        assert re.search(rf'data-summary="{status}".*?<strong[^>]*>{count}</strong>', html, re.S)


def test_the_status_filter_belongs_to_the_list(auth_client, jobs):
    html = auth_client.get("/?status=applied").text  # the board

    assert sorted(cards(html)) == ["Acme", "Kranich Health", "Österreich Post", "Şişecam"]


def test_no_match_says_so(auth_client, jobs):
    html = auth_client.get("/?view=list&q=zzz").text

    assert "No jobs match these filters." in html
    assert "Showing 0–0 of 0" in html


# --- The toolbar --------------------------------------------------------------------------


def test_the_toolbar_shows_the_chosen_filters(auth_client, jobs):
    html = auth_client.get("/?view=list&q=acme&source=linkedin&mode=remote").text

    assert 'name="q" value="acme"' in html
    assert '<option value="linkedin" selected>LinkedIn</option>' in html
    assert '<option value="remote" selected>Remote</option>' in html
    assert ">Clear filters</a>" in html


def test_clear_filters_only_when_a_filter_is_on(auth_client, jobs):
    assert ">Clear filters<" not in auth_client.get("/?view=list&sort=company").text

    html = auth_client.get("/?view=list&sort=company&q=x&status=saved").text
    link = re.search(r'<a href="([^"]+)"[^>]*>Clear filters</a>', html, re.S)
    assert link.group(1) == "/?view=list&amp;sort=company"  # keeps the view and the sort


def test_no_fit_filter_until_m7(auth_client, jobs):
    html = auth_client.get("/").text

    assert "fit score" not in html.lower() and 'name="score"' not in html


def test_the_region_sends_clear_filters_to_the_toolbar(auth_client, jobs):
    html = auth_client.get("/?q=acme", headers={"HX-Request": "true"}).text

    assert '<span id="clear-filters" hx-swap-oob="true">' in html
    assert ">Clear filters</a>" in html


def test_choices_inside_the_region_go_along_with_the_form(auth_client, jobs):
    html = auth_client.get("/?view=list&sort=company&status=saved").text

    state = re.search(r'<div id="board-state" hidden>(.*?)</div>', html, re.S).group(1)
    assert 'name="sort" value="company"' in state and 'name="status" value="saved"' in state


def test_links_inside_the_region_keep_the_filters(auth_client, session, user):
    for number in range(30):
        add(session, user, f"Acme {number}")

    html = auth_client.get("/?view=list&q=acme&source=manual").text

    assert 'href="/?view=list&amp;q=acme&amp;source=manual&amp;page=2"' in html
    assert 'href="/?view=list&amp;q=acme&amp;source=manual&amp;sort=company"' in html


# --- Only the user's own jobs ------------------------------------------------------------------


def test_search_and_filters_never_find_another_users_jobs(auth_client, session, user, jobs):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    add(
        session,
        other,
        "Kranich Secret",
        location="Wien",
        remote_type="hybrid",
        job_url="https://www.linkedin.com/jobs/view/9",
        status="applied",
    )

    for query in ["q=kranich", "source=linkedin", "mode=hybrid", "status=applied"]:
        html = auth_client.get(f"/?view=list&{query}").text
        assert "Kranich Secret" not in html
    assert "Kranich Secret" not in auth_client.get("/?q=secret").text
