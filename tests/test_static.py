import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PROTOTYPE = ROOT / "docs" / "prototype" / "Applications.dc.html"
INPUT_CSS = ROOT / "app" / "static" / "css" / "input.css"


@pytest.mark.parametrize(
    "path",
    [
        "/static/js/htmx.min.js",
        "/static/js/alpine.min.js",
        "/static/js/app.js",
        "/static/fonts/inter-latin-wght-normal.woff2",
    ],
)
def test_static_file_is_served(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert len(response.content) > 0


def test_pages_link_the_logo_as_favicon(client):
    html = client.get("/").text
    assert '<link rel="icon" href="/static/img/logo.svg" type="image/svg+xml">' in html
    response = client.get("/static/img/logo.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")


@pytest.mark.parametrize("name", ["htmx.min.js", "alpine.min.js"])
def test_vendored_js_has_version_header(name):
    header = (ROOT / "app" / "static" / "js" / name).read_text()[:600]
    assert "Package:" in header
    assert "sha256" in header


# --- Theme variables must match the prototype exactly -------------------------


def css_block(pattern: str, css: str) -> dict[str, str]:
    """Find a CSS rule by regex and return its declarations as {name: value}.

    Whitespace inside values is removed, so formatting differences don't matter.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)  # drop comments
    match = re.search(pattern + r"\s*\{([^}]*)\}", css)
    assert match, f"CSS block not found: {pattern}"
    declarations = {}
    for declaration in match.group(1).split(";"):
        if declaration.strip():
            name, value = declaration.split(":", 1)
            declarations[name.strip()] = re.sub(r"\s+", "", value)
    return declarations


@pytest.mark.parametrize(
    ("prototype_selector", "our_selector"),
    [
        (r"\.ak-root", r":root,\s*\.theme-light"),
        (r"\.ak-root\.ak-dark", r"\.theme-dark"),
        (
            r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*\.ak-root\.ak-system",
            r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*\.theme-system",
        ),
    ],
)
def test_theme_variables_match_prototype(prototype_selector, our_selector):
    expected = css_block(prototype_selector, PROTOTYPE.read_text(encoding="utf-8"))
    actual = css_block(our_selector, INPUT_CSS.read_text(encoding="utf-8"))
    assert len(expected) > 20  # sanity check: the prototype block was really found
    assert actual == expected
