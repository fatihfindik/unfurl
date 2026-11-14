import logging
from datetime import timedelta

import pytest

from app.billing import grant
from app.billing.billing_service import set_plan
from app.billing.entitlements import effective_plan
from app.models.base import as_utc, utcnow


def test_set_plan_changes_plan_and_logs_it(session, user, caplog):
    expires_at = utcnow() + timedelta(days=90)
    with caplog.at_level(logging.INFO, logger="unfurl.billing"):
        set_plan(session, user, "pro", expires_at, reason="test")

    session.refresh(user)
    assert effective_plan(user) == "pro"
    assert as_utc(user.plan_expires_at) == expires_at
    assert "free" in caplog.text and "pro" in caplog.text
    assert f"user_id={user.id}" in caplog.text
    assert "reason: test" in caplog.text
    # No personal data in the log line.
    assert user.email not in caplog.text


def test_set_plan_rejects_unknown_plan(session, user):
    with pytest.raises(ValueError, match="Unknown plan"):
        set_plan(session, user, "platinum", None, reason="test")
    session.refresh(user)
    assert user.plan == "free"


def test_free_plan_has_no_end_date(session, user):
    set_plan(session, user, "free", utcnow() + timedelta(days=5), reason="test")
    session.refresh(user)
    assert user.plan_expires_at is None


def test_grant_cli_gives_pro_for_some_days(session, user, capsys):
    exit_code = grant.main(
        ["--email", "JANA@example.com", "--plan", "pro", "--days", "90"], session=session
    )

    assert exit_code == 0
    session.refresh(user)
    assert effective_plan(user) == "pro"
    days_left = as_utc(user.plan_expires_at) - utcnow()
    assert timedelta(days=89) < days_left <= timedelta(days=90)
    assert "now has the pro plan until" in capsys.readouterr().out


def test_grant_cli_without_days_gives_no_end_date(session, user):
    assert grant.main(["--email", user.email, "--plan", "pro"], session=session) == 0
    session.refresh(user)
    assert user.plan == "pro"
    assert user.plan_expires_at is None


def test_grant_cli_unknown_email_fails(session, user, capsys):
    exit_code = grant.main(["--email", "nobody@example.com", "--plan", "pro"], session=session)
    assert exit_code == 1
    assert "No user with email" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--email", "jana@example.com", "--plan", "platinum"],
        ["--email", "jana@example.com", "--plan", "pro", "--days", "0"],
        ["--plan", "pro"],
    ],
)
def test_grant_cli_rejects_bad_arguments(session, user, argv):
    # argparse stops the program with exit code 2 on bad arguments.
    with pytest.raises(SystemExit) as error:
        grant.main(argv, session=session)
    assert error.value.code == 2
