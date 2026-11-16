"""The list view of the Applications page (SPEC §6.3, §6.7, D-067, D-070): summary cards,
one row per job, sort by Company, 25 rows per page."""

import datetime as dt
import html
import re

import pytest

from app.models.base import utcnow
from app.schemas.application import BoardQuery
from app.services import application_service as apps
from app.services.auth_service import create_user


def add(session, user, company, days_ago=0, **fields):
    fields.setdefault("job_title", "Product Manager")
    job = apps.create(session, user, company=company, **fields)
    job.last_activity_at = utcnow() - dt.timedelta(days=days_ago)
    session.add(job)
    session.commit()
    return job


def rows(html: str) -> list[str]:
    """The company of every row, top to bottom."""
    return re.findall(r'<tr data-row="\d+"[^>]*>\s*<td[^>]*>([^<]*)</td>', html)


def get_list(client, query="") -> str:
    return client.get(f"/?view=list{query}").text


def test_the_list_shows_one_row_per_job_newest_change_first(auth_client, session, user):
    add(session, user, "Old", days_ago=9, location="Graz")
    add(session, user, "New", days_ago=0, status="applied", job_url="https://www.xing.com/jobs/1")
    add(session, user, "Middle", days_ago=3)

    html = get_list(auth_client)

    assert rows(html) == ["New", "Middle", "Old"]
    new_row = re.search(r"<tr data-row.*?</tr>", html, re.S).group(0)
    for text in ["New", "Product Manager", ">Applied<", "XING", "today"]:
        assert text in new_row
    assert "9 days ago" not in html and "last week" in html  # "Old", 9 days


def test_the_list_has_summary_cards_and_the_board_has_none(auth_client, session, user):
    add(session, user, "A")
    add(session, user, "B", status="applied")

    listed = get_list(auth_client)
    board = auth_client.get("/").text

    assert 'data-summary="saved"' in listed and "Waiting for a reply" in listed
    assert re.search(r'data-summary="applied".*?<strong[^>]*>1</strong>', listed, re.S)
    assert "data-summary=" not in board
    assert "data-column=" not in listed and "<table" not in board


def test_the_columns_of_the_list(auth_client, session, user):
    add(session, user, "A")

    html = get_list(auth_client)

    headers = re.findall(r'<th scope="col"[^>]*>(.*?)</th>', html, re.S)
    names = [re.sub(r"<[^>]+>|\s+|[↑↓]", "", header) for header in headers]
    assert names == ["Company", "Role", "Status", "Source", "Updated"]  # no Fit until M7


# --- Sorting by Company (D-067) ----------------------------------------------------------


@pytest.fixture
def companies(session, user):
    for name in ["zeta", "Österreich Post", "İstanbul Tech", "Alpha", "ılık", "Beta"]:
        add(session, user, name)


def test_company_sorts_a_to_z_with_accents_in_place(auth_client, companies):
    html = get_list(auth_client, "&sort=company")

    assert rows(html) == ["Alpha", "Beta", "ılık", "İstanbul Tech", "Österreich Post", "zeta"]
    assert 'aria-sort="ascending"' in html and "Company ↑" in html


def test_company_sorts_z_to_a(auth_client, companies):
    html = get_list(auth_client, "&sort=-company")

    assert rows(html)[0] == "zeta" and rows(html)[-1] == "Alpha"
    assert 'aria-sort="descending"' in html


def test_clicking_company_goes_a_to_z_then_z_to_a_then_back(auth_client, companies):
    links = [
        html.unescape(
            re.search(
                r'<th[^>]*aria-sort[^>]*>\s*<a href="([^"]+)"', get_list(auth_client, q)
            ).group(1)
        )
        for q in ["", "&sort=company", "&sort=-company"]
    ]

    assert links == ["/?view=list&sort=company", "/?view=list&sort=-company", "/?view=list"]


# --- Pages of 25 -------------------------------------------------------------------------


@pytest.fixture
def many(session, user):
    for number in range(27):
        add(session, user, f"Company {number:02d}", days_ago=number)  # 00 is the newest


def test_a_page_has_25_rows(auth_client, many):
    html = get_list(auth_client)

    assert len(rows(html)) == 25
    assert rows(html)[0] == "Company 00"
    assert "Showing 1–25 of 27 · 25 per page" in html
    assert 'href="/?view=list&amp;page=2"' in html  # Next
    assert '<span aria-disabled="true"' in html and ">Previous<" in html


def test_the_second_page_has_the_rest(auth_client, many):
    html = get_list(auth_client, "&page=2")

    assert rows(html) == ["Company 25", "Company 26"]
    assert "Showing 26–27 of 27" in html
    assert 'href="/?view=list"' in html  # Previous goes back to page 1


@pytest.mark.parametrize("page", ["99", "10001", "-3", "0", "abc", "1e9"])
def test_a_page_number_out_of_range_shows_a_real_page(auth_client, many, page):
    html = get_list(auth_client, f"&page={page}")

    assert html.count("<tr data-row=") in (25, 2)  # page 1 or the last page
    assert "Showing" in html


def test_sorting_starts_again_at_page_one(auth_client, many):
    page = get_list(auth_client, "&page=2")

    link = re.search(r'aria-sort="none">\s*<a href="([^"]+)"', page).group(1)
    assert html.unescape(link) == "/?view=list&sort=company"


# --- Who sees what ------------------------------------------------------------------------


def test_another_users_jobs_are_never_listed_or_counted(auth_client, session, user):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    for number in range(30):
        add(session, other, f"Theirs {number}")
    add(session, user, "Mine")

    html = get_list(auth_client)

    assert rows(html) == ["Mine"]
    assert "Theirs" not in html
    assert "Showing 1–1 of 1" in html


def test_user_text_is_escaped_in_the_list(auth_client, session, user):
    add(session, user, "<script>alert(1)</script>", job_title="<b>x</b>")

    html = get_list(auth_client)

    assert "<script>alert(1)" not in html and "<b>x</b>" not in html


def test_the_view_switch_marks_the_current_view(auth_client, session, user):
    add(session, user, "A")

    board = auth_client.get("/").text
    listed = get_list(auth_client)

    assert re.search(r'data-view="board"[^>]*aria-current="page"', board)
    assert re.search(r'data-view="list"[^>]*aria-current="page"', listed)
    assert not re.search(r'data-view="list"[^>]*aria-current', board)


def test_the_region_reloads_with_the_same_choices(auth_client, session, user):
    add(session, user, "A")

    html = auth_client.get("/?view=list&sort=company&page=1", headers={"HX-Request": "true"}).text

    assert 'hx-get="/?view=list&amp;sort=company"' in html
    assert 'hx-history="false"' in html


# --- The address (BoardQuery) --------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "url"),
    [
        ({}, "/"),
        ({"view": "list"}, "/?view=list"),
        ({"view": "list", "sort": "company", "page": "3"}, "/?view=list&sort=company&page=3"),
        ({"view": "LIST", "sort": "fit", "page": "x"}, "/"),
        ({"view": "board", "sort": "company", "page": "2"}, "/"),  # sort/page belong to the list
        ({"view": "list", "more": "saved"}, "/?view=list"),  # "more" belongs to the board
        ({"more": "saved,moon,applied"}, "/?more=saved%2Capplied"),
        ({"view": "<script>"}, "/"),
    ],
)
def test_board_query_keeps_only_known_values(params, url):
    assert BoardQuery.from_params(params).url == url


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("İstanbul", "istanbul"),
        ("ISTANBUL", "istanbul"),
        ("ılık", "ilik"),
        ("Österreich", "osterreich"),
        ("Straße", "strasse"),
        ("Çelik Ğ", "celik g"),
        ("", ""),
        (None, ""),
    ],
)
def test_search_key(text, key):
    assert apps.search_key(text) == key


def test_the_back_button_gets_the_whole_page(auth_client, session, user):
    # htmx asks again for a page it didn't keep (hx-history="false"); it must get the
    # whole page, or the toolbar and the menu would be lost.
    add(session, user, "A")

    html = auth_client.get(
        "/?view=list", headers={"HX-Request": "true", "HX-History-Restore-Request": "true"}
    ).text

    assert "<html" in html and 'aria-label="View"' in html
