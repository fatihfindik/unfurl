"""The MVP has no AI features (D-150): the sign-up and verification pages must not
promise them."""

import re

from app.services.auth_service import make_verify_token
from tests.test_verification import confirm, register

# "AI" as a word, not inside other words ("maintain", "detail").
AI_WORD = re.compile(r"\bAI\b")


def visible_text(html: str) -> str:
    """The page without scripts, styles and tags, so only what the user reads is checked."""
    html = re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def test_login_and_register_pages_do_not_mention_ai(client):
    for path in ["/login", "/register"]:
        assert not AI_WORD.search(visible_text(client.get(path).text)), path


def test_check_inbox_and_the_banner_do_not_mention_ai(client, outbox):
    register(client)

    check_inbox = client.get("/check-inbox").text
    assert not AI_WORD.search(visible_text(check_inbox))
    assert 'href="/profile"' in check_inbox  # "Continue to setup" (the wizard is V2)
    assert not AI_WORD.search(visible_text(client.get("/").text))  # with the banner


def test_the_verify_page_and_its_result_do_not_mention_ai(client, session, user):
    token = make_verify_token(user)

    page = client.get(f"/verify-email?token={token}")
    assert not AI_WORD.search(visible_text(page.text))

    result = confirm(client, token)
    assert "reminder and account emails" in result.text
    assert not AI_WORD.search(visible_text(result.text))
