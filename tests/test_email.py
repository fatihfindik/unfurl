import asyncio
import logging

import pytest

from app.config import Settings
from app.services import email_service
from app.services.email_service import Email, render_email, send_email

EMAIL = Email(to="jana@example.com", subject="Hello", body="Line one\n")


def use_settings(monkeypatch, **overrides) -> None:
    """Make send_email/render_email see these settings instead of the cached ones."""
    settings = Settings(_env_file=None, app_env="development", **overrides)
    monkeypatch.setattr(email_service, "get_settings", lambda: settings)


@pytest.mark.parametrize(
    ("name", "greeting"), [("verify_email", "Hi,"), ("reset_password", "Hi Jana,")]
)
def test_templates_have_a_subject_and_an_absolute_link(monkeypatch, name, greeting):
    use_settings(monkeypatch, app_base_url="https://unfurl.example/")
    email = render_email(name, to="jana@example.com", full_name="Jana", path="/x?token=a&b=1")

    assert email.to == "jana@example.com"
    assert email.subject and "\n" not in email.subject
    assert not email.subject.startswith("Subject")
    assert email.body.startswith(greeting + "\n")
    # No double slash, and "&" is not HTML-escaped in a plain text email.
    assert "https://unfurl.example/x?token=a&b=1" in email.body


def test_template_with_a_missing_variable_is_an_error():
    with pytest.raises(Exception, match="path"):
        render_email("verify_email", to="jana@example.com", full_name="Jana")


def test_console_backend_prints_the_email(monkeypatch, capsys):
    use_settings(monkeypatch, email_backend="console")
    asyncio.run(send_email(EMAIL))

    printed = capsys.readouterr().out
    assert "jana@example.com" in printed
    assert "Subject: Hello" in printed
    assert "Line one" in printed


def test_smtp_backend_sends_with_aiosmtplib(monkeypatch):
    use_settings(
        monkeypatch,
        email_backend="smtp",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="mailer",
        smtp_password="secret",
        email_from="Unfurl <no-reply@unfurl.example>",
    )
    calls = []

    async def fake_send(message, **kwargs):
        calls.append((message, kwargs))

    monkeypatch.setattr(email_service.aiosmtplib, "send", fake_send)
    asyncio.run(send_email(EMAIL))

    message, kwargs = calls[0]
    assert message["To"] == "jana@example.com"
    assert message["From"] == "Unfurl <no-reply@unfurl.example>"
    assert message["Subject"] == "Hello"
    assert "Line one" in message.get_content()
    assert kwargs["hostname"] == "smtp.example.com"
    assert kwargs["port"] == 587
    assert kwargs["username"] == "mailer"
    assert kwargs["password"] == "secret"
    assert kwargs["start_tls"] is True


def test_smtp_error_is_logged_not_raised(monkeypatch, caplog):
    use_settings(monkeypatch, email_backend="smtp", smtp_host="smtp.example.com")

    async def failing_send(message, **kwargs):
        # SMTP errors often repeat the address; it must not reach the log.
        raise OSError("recipient jana@example.com refused")

    monkeypatch.setattr(email_service.aiosmtplib, "send", failing_send)
    with caplog.at_level(logging.ERROR, logger="unfurl.email"):
        asyncio.run(send_email(EMAIL))  # no exception

    assert "Could not send email: Hello (OSError)" in caplog.text
    # Logs should not hold personal data.
    assert "jana@example.com" not in caplog.text


def test_outbox_fixture_catches_emails(outbox):
    asyncio.run(email_service.send_email(EMAIL))
    assert outbox == [EMAIL]
