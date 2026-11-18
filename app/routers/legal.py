"""Legal pages: privacy policy, terms and imprint (SPEC §6.12).

Open to everyone, logged in or not. Claude drafted the texts (M13b, D-133); until a
lawyer has reviewed them (LEGAL_REVIEWED) the pages say "Draft". Who runs the site
comes from the settings (LEGAL_*), so no personal data is in the repository.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.services.auth_service import current_user
from app.templating import templates

# current_user (not require_user): open to everyone, but a logged-in user is known,
# so the page uses their theme and the theme picker saves to the account (D-068).
router = APIRouter(dependencies=[Depends(current_user)])


MINIMUM_AGE = 16  # Fatih's choice (D-133)
UPDATED = "18 November 2026"  # change it with the texts


def operator() -> dict:
    """The Impressum's details; "[to be added]" until they are in .env."""
    settings = get_settings()
    missing = "[to be added]"
    return {
        "name": settings.legal_operator_name or missing,
        "address": settings.legal_address or missing,
        "email": settings.legal_email or missing,
        "has_email": bool(settings.legal_email),
    }


def legal_page(request: Request, page: str, title: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        f"legal/{page}.html",
        {
            "page_title": title,
            "active_page": page,
            "operator": operator(),
            "reviewed": get_settings().legal_reviewed,
            "minimum_age": MINIMUM_AGE,
            "updated": UPDATED,
        },
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
