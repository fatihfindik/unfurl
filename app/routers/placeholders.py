"""Temporary "Coming soon" pages for menu items that are not built yet.

Each route is removed when its milestone adds the real page:
/documents (M3), /learning (M10), /settings (M11, first section: Notifications).
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.templating import templates

router = APIRouter()


def coming_soon(request: Request, section: str) -> HTMLResponse:
    return templates.TemplateResponse(request, "coming_soon.html", {"section": section})


@router.get("/learning", response_class=HTMLResponse)
def learning(request: Request):
    return coming_soon(request, "Learning")


@router.get("/documents", response_class=HTMLResponse)
def documents(request: Request):
    return coming_soon(request, "Documents")


@router.get("/settings", response_class=HTMLResponse)
def settings(request: Request):
    return coming_soon(request, "Settings")
