"""CV editor: autosave, live preview, read-only master and locked copies (SPEC §6.5, D-048)."""

import re
from datetime import date

import pytest
from markupsafe import escape

from app.models import CVDocument, Experience
from app.schemas.cv import CVContent
from app.services import document_service as docs
from app.services.auth_service import create_user
from app.services.profile_service import add_item, get_or_create_profile
from tests.helpers import unlock_master
from tests.test_profile_sections import htmx_send


@pytest.fixture
def profile(session, user):
    profile = get_or_create_profile(session, user)
    profile.headline = "Senior PM"
    profile.summary = "From the profile."
    session.add(profile)
    session.commit()
    add_item(
        session,
        profile,
        Experience,
        {
            "company": "Acme",
            "title": "PM",
            "start_date": date(2020, 1, 1),
            "bullets": ["Shipped it", "Grew it"],
        },
    )
    return profile


@pytest.fixture
def master(session, user, profile):
    return docs.create_cv(session, user, "Master CV", "en", "classic")


@pytest.fixture
def copy(session, user, master):
    return docs.create_cv(session, user, "For Acme", "en", "classic")


def save(client, cv, **values):
    form = {
        "title": cv.title,
        "headline": "Senior PM",
        "summary": "From the profile.",
        "visible_sections": [
            "summary",
            "experience",
            "education",
            "skills",
            "languages",
            "certifications",
            "projects",
        ],
        "bullets": ["0-0", "0-1"],
    } | values
    return htmx_send(client, "POST", f"/cvs/{cv.id}", form)


def text_of(response) -> str:
    """The answer as the user reads it: tags removed, entities like &#39; decoded."""
    import html

    return html.unescape(re.sub(r"<[^>]+>", "", response.text))


def stored(session, cv) -> CVContent:
    session.expire_all()
    return CVContent.model_validate(session.get(CVDocument, cv.id).content)


# --- The page -----------------------------------------------------------------------


def test_editor_shows_form_and_preview(auth_client, copy):
    page = auth_client.get(f"/cvs/{copy.id}").text

    assert '<link rel="stylesheet" href="/static/css/cv_classic.css?v=' in page
    assert 'id="cv-form"' in page and 'hx-trigger="input delay:500ms"' in page
    assert 'class="cv-paper cv-classic"' in page
    assert "Shipped it" in page
    assert "Copy based on Master CV. Your master CV stays unchanged." in page
    assert "Draft · v1" in page
    assert (
        '<fieldset class="m-0 flex flex-col gap-4 border-0 p-0" disabled>' not in page
    )  # editable


def test_later_buttons_are_not_shown_yet(auth_client, copy):
    page = auth_client.get(f"/cvs/{copy.id}").text

    for later in ["Tailor to a job", "Edit with assistant"]:  # Download PDF is here since M3b
        assert later not in page


def test_editor_page_is_404_for_another_users_cv(auth_client, session):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    get_or_create_profile(session, other)
    theirs = docs.create_cv(session, other, "Theirs", "en", "classic")

    assert auth_client.get(f"/cvs/{theirs.id}").status_code == 404
    assert auth_client.get("/cvs/abc").status_code == 404


# --- Autosave -----------------------------------------------------------------------


def test_autosave_updates_the_cv_and_the_preview_not_the_profile(
    auth_client, session, copy, profile
):
    response = save(auth_client, copy, summary="Tailored for Acme.", title="Acme CV")

    assert response.status_code == 200
    assert "Tailored for Acme." in response.text  # the new preview
    assert 'id="cv-form-errors"' in response.text and 'hx-swap-oob="true"' in response.text
    assert stored(session, copy).summary == "Tailored for Acme."
    assert session.get(CVDocument, copy.id).title == "Acme CV"
    session.refresh(profile)
    assert profile.summary == "From the profile."


def test_hidden_sections_and_unticked_bullets_leave_the_preview(auth_client, session, copy):
    response = save(auth_client, copy, visible_sections=["experience"], bullets=["0-1"])

    assert "Grew it" in response.text
    assert "Shipped it" not in response.text
    assert "From the profile." not in response.text  # summary hidden
    content = stored(session, copy)
    assert "summary" in content.hidden_sections
    assert [b.selected for b in content.experience[0].bullets] == [False, True]


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"title": "  "}, "Title was not saved: This field is required."),
        ({"summary": "x" * 5001}, "Summary was not saved: Use at most 5000 characters."),
    ],
)
def test_a_bad_text_field_keeps_its_old_value_and_the_rest_is_saved(
    auth_client, session, copy, values, message
):
    response = save(auth_client, copy, headline="New headline", **values)

    assert response.status_code == 200
    assert message in text_of(response)
    assert "Your other changes are saved." in response.text
    session.expire_all()
    assert session.get(CVDocument, copy.id).title == "For Acme"  # old title kept
    assert stored(session, copy).summary == "From the profile."  # old summary kept
    assert stored(session, copy).personal.headline == "New headline"  # this one is saved


def test_switching_off_a_section_is_saved_even_with_an_empty_title(auth_client, session, copy):
    # Fatih's manual test: the title was empty, and switching off Experience did nothing.
    response = save(auth_client, copy, title="", visible_sections=["summary", "skills"])

    assert "Title was not saved" in response.text
    assert "Shipped it" not in response.text  # Experience is gone from the preview
    assert "experience" in stored(session, copy).hidden_sections


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (
            {"visible_sections": ["hobbies"]},
            "Sections was not saved: Choose sections from the list.",
        ),
        ({"bullets": ["0-0; drop table"]}, "Bullets was not saved: Choose bullets from the list."),
    ],
)
def test_sections_or_bullets_not_on_the_page_save_nothing(
    auth_client, session, copy, values, message
):
    response = save(auth_client, copy, summary="Changed", **values)

    assert message in text_of(response)
    assert "Your other changes are saved." not in response.text
    assert stored(session, copy).summary == "From the profile."


def test_read_only_forms_say_why(auth_client, session, master, copy):
    master_page = text_of(auth_client.get(f"/cvs/{master.id}"))
    assert 'Read only. Use "Edit master" above to change your master CV.' in master_page

    docs.lock(session, copy)
    assert "Read only. This CV was sent, so it can't change." in text_of(
        auth_client.get(f"/cvs/{copy.id}")
    )


def test_bullet_keys_outside_the_cv_are_ignored(auth_client, session, copy):
    save(auth_client, copy, bullets=["0-0", "7-3"])

    assert [b.selected for b in stored(session, copy).experience[0].bullets] == [True, False]


def test_user_text_is_escaped_in_the_preview(auth_client, copy):
    response = save(auth_client, copy, summary="<img src=x onerror=alert(1)>")

    assert "<img src=x" not in response.text
    assert str(escape("<img src=x onerror=alert(1)>")) in response.text


def test_autosave_needs_a_csrf_token(auth_client, session, copy):
    response = auth_client.post(f"/cvs/{copy.id}", data={"title": "x", "summary": "Hacked"})

    assert response.status_code == 403
    assert stored(session, copy).summary == "From the profile."


def test_autosave_on_another_users_cv_is_404(auth_client, session):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    get_or_create_profile(session, other)
    theirs = docs.create_cv(session, other, "Theirs", "en", "classic")

    response = save(auth_client, theirs, summary="Hacked")

    assert response.status_code == 404
    assert stored(session, theirs).summary is None


# --- Masters (D-048) ----------------------------------------------------------------


def test_a_master_opens_read_only_with_edit_master(auth_client, master):
    page = auth_client.get(f"/cvs/{master.id}").text

    assert "This is your master CV (English)." in page
    assert 'data-dialog-open="edit-master"' in page
    # A POST, not a link: a link from elsewhere can't skip the dialog (D-169).
    assert f'hx-post="/cvs/{master.id}/edit-master"' in page
    assert "edit_master=confirmed" not in page
    assert '<fieldset class="m-0 flex flex-col gap-4 border-0 p-0" disabled>' in page
    assert 'name="unlock"' not in page


def test_an_unlocked_master_can_be_edited(auth_client, session, master):
    value = unlock_master(auth_client, "cv", master.id)
    page = auth_client.get(f"/cvs/{master.id}?unlock={value}").text
    assert f'name="unlock" value="{value}"' in page
    assert "You are editing your master CV." in page

    response = save(auth_client, master, summary="Better master.", unlock=value)

    assert response.status_code == 200
    assert stored(session, master).summary == "Better master."


def test_a_master_is_not_changed_without_the_confirmation(auth_client, session, master):
    response = save(auth_client, master, summary="Sneaky change.")

    assert response.status_code == 409
    assert stored(session, master).summary == "From the profile."


def test_editing_the_master_leaves_copies_alone(auth_client, session, master, copy):
    save(
        auth_client,
        master,
        summary="Better master.",
        unlock=unlock_master(auth_client, "cv", master.id),
    )

    assert stored(session, copy).summary == "From the profile."


# --- Locked copies ------------------------------------------------------------------


def test_a_locked_copy_is_read_only_and_refuses_saves(auth_client, session, copy):
    docs.lock(session, copy)

    page = auth_client.get(f"/cvs/{copy.id}").text
    assert "Sent · locked. You can't change a sent CV." in page
    assert '<fieldset class="m-0 flex flex-col gap-4 border-0 p-0" disabled>' in page

    response = save(auth_client, copy, summary="Changed after sending.")
    assert response.status_code == 409
    assert stored(session, copy).summary == "From the profile."


# --- Template (M3b) -------------------------------------------------------------------


def test_the_form_offers_the_three_templates(auth_client, copy):
    page = auth_client.get(f"/cvs/{copy.id}").text

    assert 'name="template"' in page
    for value, label in [("classic", "Classic"), ("modern", "Modern"), ("compact", "Compact")]:
        assert f'<option value="{value}"' in page and f">{label}</option>" in page
    assert '<option value="classic" selected>' in page


def test_changing_the_template_saves_and_redraws_the_preview(auth_client, session, copy):
    response = save(auth_client, copy, template="modern", summary="Still saved.")

    assert response.status_code == 200
    assert 'class="cv-paper cv-modern"' in response.text
    session.expire_all()
    assert session.get(CVDocument, copy.id).template == "modern"
    assert stored(session, copy).summary == "Still saved."


@pytest.mark.parametrize("template", ["fancy", "../../secret", ""])
def test_an_unknown_template_keeps_the_old_one(auth_client, session, copy, template):
    response = save(auth_client, copy, template=template, summary="Other change.")

    session.expire_all()
    assert session.get(CVDocument, copy.id).template == "classic"
    assert stored(session, copy).summary == "Other change."  # the rest is saved
    if template:  # an empty value just means "unchanged"
        assert "Template was not saved: Choose a template." in text_of(response)


def test_a_template_the_plan_does_not_allow_is_refused(auth_client, session, copy, monkeypatch):
    # Plan checks go through entitlements (CLAUDE.md), so a plan without "modern"
    # can't pick it even with a hand-made request.
    from app.routers import cvs

    monkeypatch.setattr(cvs, "allowed_templates", lambda user: ["classic"])

    response = save(auth_client, copy, template="modern")

    session.expire_all()
    assert session.get(CVDocument, copy.id).template == "classic"
    assert "Template was not saved" in text_of(response)


def test_a_locked_cv_keeps_its_template(auth_client, session, copy):
    docs.lock(session, copy)

    assert save(auth_client, copy, template="compact").status_code == 409
    session.expire_all()
    assert session.get(CVDocument, copy.id).template == "classic"
