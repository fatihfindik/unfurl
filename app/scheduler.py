"""Jobs that run on a timer inside the app (SPEC §3, D-103).

APScheduler runs them on a background thread. Started from the app's lifespan in
app/main.py, only when SCHEDULER_ENABLED=true; with several workers or servers only
one of them may have it on. The rules live in app/services/notification_service.py:
the jobs here only open a database session and call them with the current time.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import Engine
from sqlmodel import Session

from app import db
from app.config import Settings
from app.services import clock, notification_service

logger = logging.getLogger(__name__)

GENERATE_EVERY_MINUTES = 15
PURGE_AT_HOUR = 3  # 03:00 in DEFAULT_TIMEZONE, when hardly anyone uses the app


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
        IntervalTrigger(minutes=GENERATE_EVERY_MINUTES),
        id="generate_notifications",
    )
    scheduler.add_job(
        purge_notifications,
        CronTrigger(hour=PURGE_AT_HOUR, minute=0),
        id="purge_notifications",
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
