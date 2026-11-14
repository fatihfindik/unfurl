from app.templating import templates


def render(source: str) -> str:
    """Render a small template string with the app's Jinja environment (autoescape on)."""
    return templates.env.from_string(source).render()


def test_page_header_without_back_link():
    html = render(
        '{% from "components/page_header.html" import page_header %}'
        '{{ page_header("Applications", subtitle="Every job.") }}'
    )
    assert "<h1" in html and ">Applications</h1>" in html
    assert "Every job." in html
    assert "←" not in html


def test_page_header_with_back_link():
    html = render(
        '{% from "components/page_header.html" import page_header %}'
        '{{ page_header("My profile", back_url="/", back_label="Applications") }}'
    )
    assert '<a href="/"' in html
    assert "← Applications" in html


def test_page_header_escapes_the_title():
    html = render(
        '{% from "components/page_header.html" import page_header %}'
        '{{ page_header("<script>x</script>") }}'
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_modal_is_a_labelled_dialog_with_close_button():
    html = render(
        '{% from "components/modal.html" import modal %}'
        '{% call modal("add-job", "Add job") %}<p>Form here</p>{% endcall %}'
    )
    assert '<dialog id="add-job" aria-labelledby="add-job-title"' in html
    assert '<h2 id="add-job-title"' in html
    assert '<form method="dialog">' in html
    assert 'aria-label="Close"' in html
    assert "<p>Form here</p>" in html


def test_drawer_is_a_labelled_dialog_on_the_right():
    html = render(
        '{% from "components/drawer.html" import drawer %}'
        '{% call drawer("cv-preview", "CV preview") %}<p>Preview</p>{% endcall %}'
    )
    assert '<dialog id="cv-preview" aria-labelledby="cv-preview-title"' in html
    assert "right-0" in html
    assert '<form method="dialog">' in html
    assert "<p>Preview</p>" in html


def test_every_app_page_has_the_toast_live_region(auth_client):
    html = auth_client.get("/").text
    assert '<div role="status" x-data="toast"' in html
    assert ">Undo</button>" in html
    assert 'aria-label="Dismiss"' in html


# --- Form building blocks (components/forms.html) ----------------------------------

FORMS = (
    '{% from "components/forms.html" import'
    " csrf_field, text_field, checkbox_field, primary_button %}"
)


def test_csrf_field_is_a_hidden_input():
    html = render(FORMS + '{{ csrf_field("abc123") }}')
    assert '<input type="hidden" name="csrf_token" value="abc123">' in html


def test_text_field_links_label_hint_and_error():
    html = render(
        FORMS + '{{ text_field("email", "Email", type="email", value="jana@example.com",'
        ' hint="We never share it.", error="Enter a valid email.", autocomplete="email") }}'
    )
    assert '<label for="field-email"' in html
    assert 'id="field-email" name="email" type="email"' in html
    assert 'value="jana@example.com"' in html
    assert 'autocomplete="email"' in html
    assert 'aria-invalid="true"' in html
    assert 'aria-describedby="field-email-hint field-email-error"' in html
    assert 'id="field-email-error"' in html and "Enter a valid email." in html


def test_text_field_without_error_is_not_invalid():
    html = render(FORMS + '{{ text_field("full_name", "Full name") }}')
    assert "aria-invalid" not in html
    assert "aria-describedby" not in html
    assert " required" in html


def test_password_field_never_shows_its_value():
    html = render(
        FORMS + '{{ text_field("password", "Password", type="password", value="secret") }}'
    )
    assert "secret" not in html


def test_text_field_escapes_the_value():
    html = render(FORMS + '{{ text_field("full_name", "Name", value="\\"><script>x</script>") }}')
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_checkbox_field_with_html_label_from_call():
    html = render(
        FORMS + '{% call checkbox_field("privacy", "") %}'
        'I agree to the <a href="/privacy">privacy policy</a>.{% endcall %}'
    )
    assert 'type="checkbox"' in html and 'name="privacy"' in html
    assert '<a href="/privacy">privacy policy</a>' in html


def test_checkbox_field_plain_label_is_escaped():
    html = render(FORMS + '{{ checkbox_field("x", "<b>bold</b>") }}')
    assert "<b>" not in html


def test_primary_button_submits():
    html = render(FORMS + '{{ primary_button("Sign in") }}')
    assert '<button type="submit"' in html and "Sign in" in html
