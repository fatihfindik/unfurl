"""Jobs that run on a timer inside the app (SPEC §3, D-103).

APScheduler runs them on a background thread. Started from the app's lifespan in
app/main.py, only when SCHEDULER_ENABLED=true; with several workers or servers only
one of them may have it on. The rules live in the services (notification_service,
token_service): the jobs here only open a database session and call them with the
current time.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import Engine
from sqlmodel import Session

from app import db, security
from app.config import Settings
from app.services import clock, notification_service, token_service

logger = logging.getLogger(__name__)

GENERATE_EVERY_MINUTES = 15
PURGE_AT_HOUR = 3  # 03:00 in DEFAULT_TIMEZONE, when hardly anyone uses the app
# Dead extension tokens at 03:10, so the two clean-ups don't hit the database at once.
PURGE_TOKENS_AT_MINUTE = 10
# Old sign-in and email counters every hour at :20 (D-198): many failed sign-ins from
# many IPs add rows quickly, so once a night would let the table grow for a day.
PURGE_COUNTERS_AT_MINUTE = 20


def generate_notifications(engine: Engine | None = None) -> None:
    """Write the reminders that are due now (one commit per user)."""
    with Session(engine or db.engine) as session:
        notification_service.generate_due(session, clock.now())


def purge_notifications(engine: Engine | None = None) -> None:
    """Delete old read notifications; a failure is logged and tried again tomorrow."""
    with Session(engine or db.engine) as session:
        try:
            notification_service.purge(session, clock.now())
        except Exception:
            session.rollback()
            logger.exception("Purging notifications failed")


def purge_api_tokens(engine: Engine | None = None) -> None:
    """Delete dead extension tokens (D-148); a failure is logged and tried again tomorrow."""
    with Session(engine or db.engine) as session:
        try:
            deleted = token_service.purge_dead(session, clock.now())
            logger.info("Deleted %d dead extension tokens", deleted)
        except Exception:
            session.rollback()
            logger.exception("Purging extension tokens failed")


def purge_security_counters(engine: Engine | None = None) -> None:
    """Delete old sign-in and email counters (D-198); a failure is logged and tried
    again tomorrow."""
    with Session(engine or db.engine) as session:
        try:
            deleted = security.purge_security_counters(session, clock.now())
            logger.info("Deleted %d old security counters", deleted)
        except Exception:
            session.rollback()
            logger.exception("Purging security counters failed")


def build(settings: Settings) -> BackgroundScheduler:
    """The scheduler with its jobs, not started.

    - timezone: times like 03:00 are Vienna times, not the server's (UTC in Docker).
    - max_instances=1: a run that takes long is never started a second time on top.
    - coalesce=True: runs missed while the app was asleep are done once, not one by one.
    """
    scheduler = BackgroundScheduler(
        timezone=settings.default_timezone,
        job_defaults={"max_instances": 1, "coalesce": True},
    )
    scheduler.add_job(
        generate_notifications,
        # At :00, :15, :30 and :45, so a 19:00 study reminder comes at 19:00 (D-109),
        # not up to 15 minutes later depending on when the app was started.
        CronTrigger(minute=f"*/{GENERATE_EVERY_MINUTES}", timezone=settings.default_timezone),
        id="generate_notifications",
    )
    scheduler.add_job(
        purge_notifications,
        # The trigger needs the time zone too: without it APScheduler 3 uses the
        # machine's (UTC on CI and in Docker), not the scheduler's.
        CronTrigger(hour=PURGE_AT_HOUR, minute=0, timezone=settings.default_timezone),
        id="purge_notifications",
    )
    scheduler.add_job(
        purge_api_tokens,
        CronTrigger(
            hour=PURGE_AT_HOUR, minute=PURGE_TOKENS_AT_MINUTE, timezone=settings.default_timezone
        ),
        id="purge_api_tokens",
    )
    scheduler.add_job(
        purge_security_counters,
        CronTrigger(minute=PURGE_COUNTERS_AT_MINUTE, timezone=settings.default_timezone),
        id="purge_security_counters",
    )
    return scheduler


def start(settings: Settings) -> BackgroundScheduler | None:
    """Start the jobs if SCHEDULER_ENABLED is on; returns the running scheduler or None."""
    if not settings.scheduler_enabled:
        return None
    scheduler = build(settings)
    scheduler.start()
    logger.info("Scheduler started")
    return scheduler


def shutdown(scheduler: BackgroundScheduler | None) -> None:
    """Stop the jobs when the app stops (wait=False: a running job isn't waited for;
    each user's work is in its own commit, so nothing is half saved)."""
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=False)
