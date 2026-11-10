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


def test_every_page_has_the_toast_live_region(client):
    html = client.get("/").text
    assert '<div role="status" x-data="toast"' in html
    assert ">Undo</button>" in html
    assert 'aria-label="Dismiss"' in html
