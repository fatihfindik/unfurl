"""Personal info read view as a business card (D-041)."""

from tests.test_profile import htmx_get, htmx_post


def save(client, **values):
    form = {"full_name": "Jana Novak"} | values
    response = htmx_post(client, "/profile/personal", form)
    assert response.status_code == 200
    return response.text


def test_filled_contacts_are_shown_and_empty_ones_listed_once(auth_client):
    html = save(
        auth_client,
        headline="Senior Product Manager",
        email="jana@work.example",
        location="Vienna",
        linkedin_url="https://www.linkedin.com/in/jana",
    )

    assert "Jana Novak" in html
    assert "Senior Product Manager" in html
    assert "jana@work.example" in html
    assert "Vienna" in html
    assert "https://www.linkedin.com/in/jana" in html
    assert "Not added yet: Phone, GitHub, Website. Use Edit to add them." in html
    assert "Not added" not in html.replace("Not added yet", "")  # no "Not added" rows


def test_links_are_text_not_clickable(auth_client):
    html = save(auth_client, website="https://jana.example")

    assert "https://jana.example" in html
    assert 'href="https://jana.example"' not in html


def test_missing_headline_has_a_placeholder(auth_client):
    assert "No headline yet" in save(auth_client)


def test_all_contacts_filled_means_no_hint(auth_client):
    html = save(
        auth_client,
        email="a@example.com",
        phone="+43 1",
        location="Vienna",
        linkedin_url="https://linkedin.com/in/a",
        github_url="https://github.com/a",
        website="https://a.example",
    )

    assert "Not added yet" not in html


def test_date_of_birth_and_nationality_in_a_muted_line(auth_client):
    html = save(auth_client, date_of_birth="1990-05-17", nationality="Czech")

    assert "Date of birth: 17.05.1990 · Nationality: Czech" in html
    assert "(hidden on CVs by default)" in html


def test_no_dach_line_when_both_are_empty(auth_client):
    assert "Date of birth" not in htmx_get(auth_client, "/profile/personal").text


def test_screen_readers_hear_what_each_contact_is(auth_client):
    html = save(auth_client, phone="+43 660 1234567")

    assert '<span class="sr-only">Phone:</span>' in html


def test_the_photo_sits_in_the_card(auth_client):
    html = htmx_get(auth_client, "/profile/personal").text

    assert 'id="profile-photo"' in html
    assert "Upload a photo…" in html


def test_the_photo_has_an_edit_menu_like_github(auth_client):
    html = htmx_get(auth_client, "/profile/personal").text

    # "Edit" on the circle opens a <details> menu with the upload, and the rules as a
    # note in the menu (also read by screen readers through aria-describedby).
    assert "<details" in html and 'Edit<span class="sr-only"> profile photo</span>' in html
    assert "Upload a photo…" in html and 'type="file" name="photo"' in html
    assert 'aria-describedby="profile-photo-hint"' in html
    assert "JPEG or PNG, up to 2 MB. Hidden on CVs unless you switch it on." in html
    assert "Remove photo" not in html  # nothing to remove yet
