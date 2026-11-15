"""Temporary "Coming soon" pages for menu items that are not built yet.

Each route is removed when its milestone adds the real page:
/learning (M10), /settings (M11, first section: Notifications). /documents is in
app/routers/cvs.py since M3a.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.services.auth_service import require_user
from app.templating import templates

# Every page here needs a logged-in user; without one the visitor goes to /login.
router = APIRouter(dependencies=[Depends(require_user)])


def coming_soon(request: Request, section: str) -> HTMLResponse:
    return templates.TemplateResponse(request, "coming_soon.html", {"section": section})


@router.get("/learning", response_class=HTMLResponse)
def learning(request: Request):
    return coming_soon(request, "Learning")


@router.get("/settings", response_class=HTMLResponse)
def settings(request: Request):
    return coming_soon(request, "Settings")
