"""CLI: give a user a plan by hand (SPEC §17.2), e.g. to test Pro.

    uv run python -m app.billing.grant --email jana@example.com --plan pro --days 90

Without --days the plan has no end date. Use --plan free to take Pro away.
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import timedelta

from sqlmodel import Session, select

from app.billing.billing_service import set_plan
from app.billing.plans import PLANS
from app.models import User
from app.models.base import utcnow
from app.models.user import normalize_email


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Give a user a plan by hand.")
    parser.add_argument("--email", required=True, help="Email of an existing user")
    parser.add_argument("--plan", required=True, choices=list(PLANS))
    parser.add_argument("--days", type=int, help="How many days the plan lasts (default: no end)")
    args = parser.parse_args(argv)
    if args.days is not None and args.days <= 0:
        parser.error("--days must be a positive number")
    return args


def main(argv: Sequence[str] | None = None, session: Session | None = None) -> int:
    """Run the command. Returns the exit code (0 = success).

    Tests pass their own `argv` and `session`; from the terminal both come
    from the command line and the app's database.
    """
    args = parse_args(argv)

    if session is None:
        # Imported here so tests that pass a session never touch the app's database.
        from app.db import engine

        with Session(engine) as session:
            return grant(session, args)
    return grant(session, args)


def grant(session: Session, args: argparse.Namespace) -> int:
    email = normalize_email(args.email)
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None:
        print(f"No user with email {email}.", file=sys.stderr)
        return 1

    expires_at = utcnow() + timedelta(days=args.days) if args.days else None
    set_plan(session, user, args.plan, expires_at, reason="manual grant via CLI")
    until = f"until {expires_at:%Y-%m-%d %H:%M} UTC" if expires_at else "with no end date"
    print(f"{email} now has the {args.plan} plan {until}.")
    return 0


if __name__ == "__main__":
    # Show the plan-change log line in the terminal.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
