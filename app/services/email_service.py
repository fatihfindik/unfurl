"""Sending emails: verification and password reset for now (SPEC §6.1, D-028).

EMAIL_BACKEND=console prints the email in the terminal (development).
EMAIL_BACKEND=smtp sends it with aiosmtplib (production, D-029).

Routes send emails in the background, after the response (FastAPI BackgroundTasks),
and always call it as `email_service.send_email(...)`, so tests can swap it for
the `outbox` fixture in tests/conftest.py.
"""

import logging
from dataclasses import dataclass
from email.message import EmailMessage

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.config import get_settings
from app.templating import TEMPLATES_DIR

logger = logging.getLogger("unfurl.email")

SMTP_TIMEOUT_SECONDS = 10

# Plain text emails, so no HTML escaping (it would turn "&" in links into "&amp;").
# StrictUndefined: a missing variable is an error instead of an empty gap in the text.
_email_templates = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR / "emails"),
    autoescape=False,
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


@dataclass
class Email:
    to: str
    subject: str
    body: str


def render_email(name: str, to: str, **context) -> Email:
    """Build an email from app/templates/emails/<name>.txt.

    The template's first line is "Subject: ...", then an empty line, then the body.
    `base_url` (APP_BASE_URL) is available in every template, so links are absolute.
    """
    base_url = get_settings().app_base_url.rstrip("/")
    text = _email_templates.get_template(f"{name}.txt").render(base_url=base_url, **context)
    subject_line, _, body = text.partition("\n\n")
    return Email(to=to, subject=subject_line.removeprefix("Subject:").strip(), body=body)


async def send_email(email: Email) -> None:
    """Send the email with the configured backend.

    Errors are logged, never raised: this runs after the response is sent, so
    there is no page left to show an error on. The user can ask for the email again.
    """
    settings = get_settings()
    try:
        if settings.email_backend == "console":
            # print, not logger.info: the dev server only shows warnings and errors
            # from our loggers. Console is development only (D-029), so the address
            # being printed is fine here.
            print(f"\n--- Email to {email.to}\nSubject: {email.subject}\n\n{email.body}---\n")
            return

        message = EmailMessage()
        message["From"] = settings.email_from
        message["To"] = email.to
        message["Subject"] = email.subject
        message.set_content(email.body)
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user or None,
            password=settings.smtp_password.get_secret_value() or None,
            start_tls=True,  # encrypt the connection before logging in (port 587)
            timeout=SMTP_TIMEOUT_SECONDS,
        )
    except Exception:
        # Only the subject, not the address: logs should not hold personal data.
        logger.exception("Could not send email: %s", email.subject)
