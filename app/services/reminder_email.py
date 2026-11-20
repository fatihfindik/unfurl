"""The study reminder email and its "Turn off these emails" link (D-098, D-112).

The link carries a signed token (like the verify and reset links, D-027): it works
without a login, for 90 days. Opening it only shows a page with a button; the POST
turns the emails off. In-app reminders stay on.
"""

import asyncio

from sqlmodel import Session

from app.models import User
from app.services import auth_service, email_service

OFF_TOKEN_SALT = "study-reminder-off"
OFF_TOKEN_MAX_AGE = 90 * 24 * 60 * 60  # 90 days, in seconds


def make_off_token(user: User) -> str:
    # The email is in the token: a link sent to an old address stops working.
    return auth_service.token_serializer(OFF_TOKEN_SALT).dumps(
        {"user_id": user.id, "email": user.email}
    )


def user_from_off_token(session: Session, token: str) -> User | None:
    """The user a "Turn off these emails" link belongs to, or None if it isn't valid."""
    data = auth_service.read_token(token, OFF_TOKEN_SALT, OFF_TOKEN_MAX_AGE)
    if data is None or type(data.get("user_id")) is not int:
        return None
    user = session.get(User, data["user_id"])
    if user is None or user.email != data.get("email"):
        return None
    return user


def masked(email: str) -> str:
    """ "jana@example.com" -> "j***@example.com": the page shows whose emails stop,
    without showing the whole address to anyone who has the link."""
    name, _, domain = email.partition("@")
    return f"{name[:1]}***@{domain}"


def build(user: User, title: str, body: str) -> email_service.Email:
    return email_service.render_email(
        "study_reminder",
        to=user.email,
        title=title,
        body=body,
        off_path="/reminders/off?token=" + make_off_token(user),
    )


SEND_AT_ONCE = 5  # emails sent at the same time, so a slow server doesn't take minutes


def send_all(emails: list[email_service.Email]) -> None:
    """Send the emails, up to SEND_AT_ONCE at a time. Called from the scheduler's
    thread, which has no event loop, so asyncio.run starts one. send_email never
    raises: errors are logged without the address."""
    if not emails:
        return

    async def send_them() -> None:
        limit = asyncio.Semaphore(SEND_AT_ONCE)

        async def send_one(email: email_service.Email) -> None:
            async with limit:
                await email_service.send_email(email)

        await asyncio.gather(*(send_one(email) for email in emails))

    asyncio.run(send_them())
