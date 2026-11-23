"""Loading feedback and the double-click guard (D-126): while a request runs, the button
that sent it is disabled and shows a spinner. Checked in the templates, like the
CSP checks in test_security_headers.py (the browser part is in the manual test)."""

import re
from pathlib import Path

from tests.test_security_headers import without_comments

TEMPLATES = Path(__file__).parent.parent / "app" / "templates"
CSS = Path(__file__).parent.parent / "app" / "static" / "css" / "input.css"
WRITES = re.compile(r"\shx-(post|put|patch|delete)=")


def opening_tags(text: str):
    """(line, tag name, the whole tag) for each opening tag. Quotes and Jinja's
    {{ }} / {% %} are skipped, so a ">" inside them doesn't end the tag."""
    i = 0
    while (i := text.find("<", i)) >= 0:
        name = re.match(r"<([a-zA-Z][\w-]*)", text[i:])
        if not name:
            i += 1
            continue
        j, quote = i + name.end(), None
        while j < len(text):
            if quote:
                quote = None if text[j] == quote else quote
            elif text.startswith(("{{", "{%"), j):
                j = text.find("}}" if text[j + 1] == "{" else "%}", j) + 1
            elif text[j] in "\"'":
                quote = text[j]
            elif text[j] == ">":
                break
            j += 1
        yield text[:i].count("\n") + 1, name.group(1).lower(), text[i : j + 1]
        i = j + 1


def writing_tags():
    for path in sorted(TEMPLATES.rglob("*.html")):
        for line, name, tag in opening_tags(without_comments(path.read_text())):
            if WRITES.search(tag):
                yield f"{path.relative_to(TEMPLATES)}:{line}", name, tag


def test_the_page_sets_no_guard_for_everything():
    # Inherited from <body>, the rule logged a console error for every request that
    # has no submit button (badge, links, tabs; #90). Each element sets its own (D-170).
    base = (TEMPLATES / "base.html").read_text()

    assert "hx-disabled-elt" not in without_comments(base)


def test_every_element_that_sends_a_change_is_guarded():
    # A form disables its submit button ("find [type=submit]") or, when it saves while
    # typing, nothing ("unset"); a button, checkbox or select disables itself ("this").
    unguarded = [where for where, name, tag in writing_tags() if "hx-disabled-elt" not in tag]

    assert unguarded == []


def test_forms_guard_their_submit_button_or_nothing():
    wrong = [
        where
        for where, name, tag in writing_tags()
        if name == "form"
        and 'hx-disabled-elt="find [type=submit]"' not in tag
        and 'hx-disabled-elt="unset"' not in tag
        # The photo form sends on change and has no button: its file field is locked.
        and 'hx-disabled-elt="find input[type=file]"' not in tag
    ]

    assert wrong == []


def test_a_form_that_disables_its_submit_button_has_one():
    # Otherwise htmx logs "returned no matches" on every request (#90; the photo form
    # had no submit button, security review T1, W4).
    missing = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = without_comments(path.read_text())
        for line, name, tag in opening_tags(text):
            if name != "form" or 'hx-disabled-elt="find [type=submit]"' not in tag:
                continue
            start = text.index(tag)
            body = text[start : text.index("</form>", start)]
            # A <button> without a type is a submit button too; macros render one.
            has_submit = re.search(r'type="submit"|<button(?![^>]*type=)|primary_button\(', body)
            if not has_submit:
                missing.append(f"{path.relative_to(TEMPLATES)}:{line}")

    assert missing == []


def test_the_scan_finds_the_known_cases():
    found = {(where.split(":")[0], name) for where, name, _ in writing_tags()}

    assert ("learning/_task_form.html", "form") in found
    assert ("learning/_parts.html", "input") in found  # the task's checkbox
    assert ("applications/detail.html", "select") in found  # the status select


def test_the_cv_editor_keeps_its_buttons_while_typing():
    form = (TEMPLATES / "cvs" / "_form.html").read_text()

    assert 'hx-trigger="input delay:500ms"' in form
    assert 'hx-disabled-elt="unset"' in form


def test_the_spinner_waits_and_respects_reduced_motion():
    css = CSS.read_text()

    assert "button.htmx-request::after" in css
    assert 'form.htmx-request [type="submit"]::after' in css
    assert "300ms" in css
    assert "prefers-reduced-motion" in css


def test_modals_have_a_loading_skeleton(auth_client):
    html = auth_client.get("/learning").text

    assert '<template id="dialog-skeleton">' in html
    assert "data-dialog-skeleton" in html
    assert "Loading…" in html
