"""Applications home page (`/`). In M0 it is an empty page; the board comes in M4."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.services.auth_service import require_user
from app.templating import templates

# Every page here needs a logged-in user; without one the visitor goes to /login.
router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/", response_class=HTMLResponse)
def applications_home(request: Request):
    return templates.TemplateResponse(request, "applications/index.html")
