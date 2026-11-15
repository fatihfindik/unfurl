"""CV as PDF with WeasyPrint (SPEC §6.5, plan M3b, D-051, D-054, D-059).

The PDF is made from the same HTML as the editor's live preview (pdf/cv_*.html),
inside a small page (pdf/_pdf_page.html) that adds the print stylesheet: A4 page,
margins and the fonts. So what the user sees is what they download.

WeasyPrint is imported only inside this module's functions: it needs the Pango
system library, and the rest of the app (and most tests) don't.
"""

import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from app.i18n.document_labels import labels
from app.models import CVDocument
from app.models.cv import CV_TEMPLATES
from app.schemas.cv import CVContent
from app.templating import TEMPLATES_DIR, templates

STATIC_DIR = TEMPLATES_DIR.parent / "static"
# The only local folders WeasyPrint may read from (D-059).
ALLOWED_DIRS = (STATIC_DIR / "css", STATIC_DIR / "fonts")
# The only inline data allowed: the CV photo (D-052).
ALLOWED_DATA_PREFIX = "data:image/jpeg;base64,"
HOMEBREW_LIBRARIES = "/opt/homebrew/lib"


class PDFError(Exception):
    """WeasyPrint could not make the PDF (the message for the user is in the router)."""


def _help_macos_find_pango() -> None:
    """On a Mac with Homebrew (Apple Silicon), Pango is in /opt/homebrew/lib, a folder
    the system doesn't search. Setting the variable here, before WeasyPrint loads,
    is enough. (Setting it in the shell doesn't work with `uv run`: macOS removes
    DYLD_* variables when it starts a protected program such as uv.) Linux (Docker,
    CI) finds Pango on its own, so nothing happens there."""
    if sys.platform != "darwin" or not Path(HOMEBREW_LIBRARIES).is_dir():
        return
    folders = [p for p in os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "").split(":") if p]
    if HOMEBREW_LIBRARIES not in folders:
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join([*folders, HOMEBREW_LIBRARIES])


def is_allowed_url(url: str) -> bool:
    """True for the only resources a CV PDF may load (D-059): our own stylesheets and
    fonts, and the inline JPEG photo. Never http(s), never other local files."""
    if url.startswith(ALLOWED_DATA_PREFIX):
        return True
    parts = urlsplit(url)
    if parts.scheme != "file" or parts.netloc not in ("", "localhost"):
        return False
    # resolve() follows "..", so "static/css/../../config.py" is caught too.
    path = Path(url2pathname(unquote(parts.path))).resolve()
    return any(path.is_relative_to(folder.resolve()) for folder in ALLOWED_DIRS)


def _fetcher_class():
    from weasyprint.urls import URLFetcher

    class SafeURLFetcher(URLFetcher):
        """WeasyPrint's fetcher, limited to is_allowed_url (D-059)."""

        def fetch(self, url, headers=None):
            if not is_allowed_url(url):
                # WeasyPrint skips a resource that raises; the CV is still made.
                raise ValueError(f"Blocked resource in a CV PDF: {url[:60]}")
            return super().fetch(url, headers)

    return SafeURLFetcher


def template_name(cv: CVDocument) -> str:
    """The CV's template, or "classic" for anything unknown (the name comes from the
    database and is used in a template path, so it is checked against the list)."""
    return cv.template if cv.template in CV_TEMPLATES else "classic"


def cv_context(cv: CVDocument) -> dict:
    """What the CV templates need: the content, the words in the CV's language and
    the template file (pdf/cv_<template>.html)."""
    return {
        "cv": cv,
        "content": CVContent.model_validate(cv.content),
        "labels": labels(cv.language),
        "cv_template": f"pdf/cv_{template_name(cv)}.html",
        "cv_stylesheet": f"css/cv_{template_name(cv)}.css",
    }


def render_cv_html(cv: CVDocument, *, for_pdf: bool) -> str:
    """The CV as a full HTML page for WeasyPrint (for_pdf) or the preview piece."""
    context = cv_context(cv)
    template = "pdf/_pdf_page.html" if for_pdf else context["cv_template"]
    return templates.get_template(template).render(**context, for_pdf=for_pdf)


def render_cv_pdf(cv: CVDocument) -> bytes:
    """The CV as PDF bytes. Raises PDFError if WeasyPrint fails."""
    _help_macos_find_pango()
    try:
        import weasyprint

        html = weasyprint.HTML(
            string=render_cv_html(cv, for_pdf=True),
            base_url=STATIC_DIR.as_uri() + "/",
            url_fetcher=_fetcher_class()(),
        )
        return html.write_pdf()
    except Exception as exc:  # Pango missing, broken CSS, …: never a 500 with details
        raise PDFError(type(exc).__name__) from exc
