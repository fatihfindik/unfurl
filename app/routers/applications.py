"""Applications home page (`/`). In M0 it is an empty page; the board comes in M4."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def applications_home(request: Request):
    return templates.TemplateResponse(request, "applications/index.html")
