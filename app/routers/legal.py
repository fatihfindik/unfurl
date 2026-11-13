"""Legal pages: privacy policy, terms and imprint (SPEC §6.12).

Open to everyone, logged in or not. The texts are placeholders until a legal
review before launch (M13).
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.templating import templates

router = APIRouter()


def legal_page(request: Request, page: str, title: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, f"legal/{page}.html", {"page_title": title, "active_page": page}
    )


@router.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return legal_page(request, "privacy", "Privacy policy")


@router.get("/terms", response_class=HTMLResponse)
def terms(request: Request):
    return legal_page(request, "terms", "Terms of use")


@router.get("/imprint", response_class=HTMLResponse)
def imprint(request: Request):
    # Impressum: required for websites run from Austria.
    return legal_page(request, "imprint", "Imprint")
