"""Reordering experience, education and projects with ↑ / ↓ (SPEC §6.4, M2b)."""

from datetime import date

import pytest
from sqlmodel import select

from app.models import Education, Experience, Project, Skill
from app.services.auth_service import create_user
from app.services.profile_service import add_item, get_or_create_profile, list_items
from tests.test_profile_sections import htmx_send

SORTABLE = {
    "experience": (
        Experience,
        lambda n: {"company": f"Co {n}", "title": "Dev", "start_date": date(2020, 1, 1)},
    ),
    "education": (
        Education,
        lambda n: {"institution": f"School {n}", "start_date": date(2015, 1, 1)},
    ),
    "projects": (Project, lambda n: {"name": f"Project {n}"}),
}


def make_rows(session, user, model, data, count=3):
    profile = get_or_create_profile(session, user)
    return [add_item(session, profile, model, data(n)) for n in range(count)]


def order(session, user, model) -> list[int]:
    session.expire_all()
    return [row.id for row in list_items(session, get_or_create_profile(session, user), model)]


def version(session, user) -> int:
    session.expire_all()
    return get_or_create_profile(session, user).version


def move(client, key, item_id, direction):
    return htmx_send(client, "POST", f"/profile/{key}/{item_id}/move", {"direction": direction})


@pytest.mark.parametrize("key", SORTABLE)
def test_up_and_down_change_the_order(auth_client, session, user, key):
    model, data = SORTABLE[key]
    a, b, c = make_rows(session, user, model, data)

    response = move(auth_client, key, c.id, "up")
    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "profile-changed"
    assert order(session, user, model) == [a.id, c.id, b.id]

    move(auth_client, key, a.id, "down")
    assert order(session, user, model) == [c.id, a.id, b.id]


@pytest.mark.parametrize("key", SORTABLE)
def test_a_move_raises_the_version(auth_client, session, user, key):
    model, data = SORTABLE[key]
    rows = make_rows(session, user, model, data)
    before = version(session, user)

    move(auth_client, key, rows[1].id, "up")

    assert version(session, user) == before + 1


@pytest.mark.parametrize(
    ("position", "direction"), [(0, "up"), (2, "down"), (1, "sideways"), (1, "")]
)
def test_moves_that_change_nothing(auth_client, session, user, position, direction):
    rows = make_rows(session, user, Project, SORTABLE["projects"][1])
    before_order, before_version = order(session, user, Project), version(session, user)

    response = move(auth_client, "projects", rows[position].id, direction)

    assert response.status_code == 200
    assert "HX-Trigger" not in response.headers
    assert order(session, user, Project) == before_order
    assert version(session, user) == before_version


def test_rows_with_the_same_number_can_still_be_moved(auth_client, session, user):
    # Two rows added at the same moment can get the same sort_order (max + 1).
    a, b = make_rows(session, user, Project, SORTABLE["projects"][1], count=2)
    b.sort_order = a.sort_order
    session.add(b)
    session.commit()
    assert order(session, user, Project) == [a.id, b.id]  # same number: id decides

    move(auth_client, "projects", b.id, "up")

    assert order(session, user, Project) == [b.id, a.id]


@pytest.mark.parametrize("key", ["skills", "languages", "certifications"])
def test_other_sections_cannot_be_reordered(auth_client, session, user, key):
    skill = make_rows(session, user, Skill, lambda n: {"name": f"S{n}"}, count=1)[0]

    assert move(auth_client, key, skill.id, "up").status_code == 404


def test_another_users_row_cannot_be_moved(auth_client, session, user):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = make_rows(session, other, Project, SORTABLE["projects"][1])
    before = order(session, other, Project)

    assert move(auth_client, "projects", theirs[1].id, "up").status_code == 404
    assert order(session, other, Project) == before


def test_moving_needs_a_csrf_token(auth_client, session, user):
    rows = make_rows(session, user, Project, SORTABLE["projects"][1])
    response = auth_client.post(
        f"/profile/projects/{rows[1].id}/move",
        data={"direction": "up"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 403


# --- The buttons ------------------------------------------------------------------


def test_first_row_has_up_turned_off_and_last_row_down(auth_client, session, user):
    make_rows(session, user, Project, SORTABLE["projects"][1])

    page = auth_client.get("/profile").text
    section = page[page.index('id="profile-projects"') :]
    buttons = section[: section.index("</section>")]

    assert buttons.count("Move Project 0 up") == 1
    assert buttons.count('<button type="button" disabled') == 2  # first ↑ and last ↓


def test_a_single_row_has_no_move_buttons(auth_client, session, user):
    make_rows(session, user, Project, SORTABLE["projects"][1], count=1)

    assert "Move Project 0" not in auth_client.get("/profile").text


def test_skills_have_no_move_buttons(auth_client, session, user):
    make_rows(session, user, Skill, lambda n: {"name": f"S{n}"}, count=3)

    assert "Move S0" not in auth_client.get("/profile").text


def test_focus_goes_back_to_the_pressed_button(auth_client, session, user):
    rows = make_rows(session, user, Project, SORTABLE["projects"][1])

    html = move(auth_client, "projects", rows[2].id, "up").text  # row 2 is now in the middle
    pressed = html[: html.index("Move Project 2 up")]
    assert pressed[pressed.rindex("<button") :].count("autofocus") == 1
    assert html.count("autofocus") == 1


def test_focus_moves_to_the_other_button_at_the_top(auth_client, session, user):
    rows = make_rows(session, user, Project, SORTABLE["projects"][1])

    html = move(auth_client, "projects", rows[1].id, "up").text  # row 1 is now first
    down = html[: html.index("Move Project 1 down")]
    assert down[down.rindex("<button") :].count("autofocus") == 1


def test_a_saved_row_keeps_its_buttons_right(auth_client, session, user):
    rows = make_rows(session, user, Project, SORTABLE["projects"][1])

    html = htmx_send(auth_client, "POST", f"/profile/projects/{rows[0].id}", {"name": "First"}).text

    assert "Move First up" in html
    assert html.count('<button type="button" disabled') == 1  # it is still the first row


def test_no_rows_are_lost_or_added(auth_client, session, user):
    rows = make_rows(session, user, Project, SORTABLE["projects"][1])
    move(auth_client, "projects", rows[2].id, "up")

    session.expire_all()  # read the rows again from the database
    numbers = [row.sort_order for row in session.exec(select(Project))]
    assert sorted(numbers) == [1, 2, 3]


# --- Drag and drop: the whole new order ---------------------------------------------


def send_order(client, key, ids):
    return htmx_send(client, "POST", f"/profile/{key}/order", {"ids": [str(i) for i in ids]})


@pytest.mark.parametrize("key", SORTABLE)
def test_drag_and_drop_saves_the_new_order(auth_client, session, user, key):
    model, data = SORTABLE[key]
    a, b, c = make_rows(session, user, model, data)
    before = version(session, user)

    response = send_order(auth_client, key, [c.id, a.id, b.id])

    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "profile-changed"
    assert order(session, user, model) == [c.id, a.id, b.id]
    assert version(session, user) == before + 1


def test_the_same_order_changes_nothing(auth_client, session, user):
    rows = make_rows(session, user, Project, SORTABLE["projects"][1])
    before = version(session, user)

    response = send_order(auth_client, "projects", [row.id for row in rows])

    assert "HX-Trigger" not in response.headers
    assert version(session, user) == before


@pytest.mark.parametrize(
    "make_ids",
    [
        lambda a, b, c: [a, b],  # one row missing: the page was out of date
        lambda a, b, c: [a, a, b, c],  # one row twice
        lambda a, b, c: ["abc", a, b],  # not an id
        lambda a, b, c: ["-1", a, b],
        lambda a, b, c: [a] * 51,  # more ids than a section can have
    ],
)
def test_wrong_lists_change_nothing(auth_client, session, user, make_ids):
    a, b, c = make_rows(session, user, Project, SORTABLE["projects"][1])
    before = order(session, user, Project)

    response = send_order(auth_client, "projects", make_ids(a.id, b.id, c.id))

    assert response.status_code == 200
    assert order(session, user, Project) == before
    # The section comes back in the stored order, so the screen is right again.
    assert response.text.index(f"profile-projects-{a.id}") < response.text.index(
        f"profile-projects-{c.id}"
    )


def test_another_users_id_in_the_list_is_404(auth_client, session, user):
    mine = make_rows(session, user, Project, SORTABLE["projects"][1], count=2)
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = make_rows(session, other, Project, SORTABLE["projects"][1], count=2)
    my_before, their_before = order(session, user, Project), order(session, other, Project)

    ids = [theirs[0].id, mine[1].id, mine[0].id, theirs[1].id]
    assert send_order(auth_client, "projects", ids).status_code == 404

    assert order(session, user, Project) == my_before
    assert order(session, other, Project) == their_before


def test_an_id_that_does_not_exist_is_404(auth_client, session, user):
    a, b = make_rows(session, user, Project, SORTABLE["projects"][1], count=2)

    assert send_order(auth_client, "projects", [b.id, a.id, 999999]).status_code == 404


@pytest.mark.parametrize("key", ["skills", "languages", "certifications"])
def test_other_sections_have_no_drag_and_drop(auth_client, session, user, key):
    assert send_order(auth_client, key, []).status_code == 404


def test_order_is_not_read_as_a_row_id(auth_client, session, user):
    make_rows(session, user, Project, SORTABLE["projects"][1], count=2)

    # GET /profile/projects/order is the item route with "order" as id: 404, not a crash.
    assert htmx_send(auth_client, "GET", "/profile/projects/order").status_code == 404


def test_sortable_lists_have_drag_and_drop_data(auth_client, session, user):
    make_rows(session, user, Project, SORTABLE["projects"][1], count=2)
    make_rows(session, user, Skill, lambda n: {"name": f"S{n}"}, count=2)

    page = auth_client.get("/profile").text

    assert 'data-sort-url="/profile/projects/order"' in page
    assert 'data-sort-url="/profile/skills/order"' not in page
    assert page.count("data-sort-handle") == 2  # one grip per project row
    assert '<script src="/static/js/sortable.min.js" defer></script>' in page


def test_a_single_row_has_no_grip(auth_client, session, user):
    make_rows(session, user, Project, SORTABLE["projects"][1], count=1)

    assert "data-sort-handle" not in auth_client.get("/profile").text
