"""Dates and times for people (D-071).

The database always stores UTC. People see and type dates and times in one time
zone: settings.default_timezone (Europe/Vienna) until users get their own (M10/M11).
Then only this module changes: the functions will take the user.
"""

import datetime as dt
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.models.base import as_utc, utcnow


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().default_timezone)


def now() -> dt.datetime:
    """The current time, UTC (as stored)."""
    return utcnow()


def today() -> dt.date:
    """Today's date where the user is: at 00:30 in Vienna it is already tomorrow
    there, while UTC still says today."""
    return local(utcnow()).date()


def local(value: dt.datetime) -> dt.datetime:
    """A stored (UTC) time as the user sees it."""
    return as_utc(value).astimezone(tz())


def to_utc(day: dt.date, time: dt.time | None = None) -> dt.datetime:
    """A date (and time) the user typed, as UTC for the database. Without a time the
    start of that day is used.

    Twice a year a local time is odd: in October 02:30 happens twice (the first one is
    used, fold=0), in March 02:30 doesn't exist (it comes out an hour later, 03:30)."""
    wall = dt.datetime.combine(day, time or dt.time(0)).replace(tzinfo=tz(), fold=0)
    return wall.astimezone(dt.UTC)


def is_start_of_day(value: dt.datetime) -> bool:
    """True if a stored time is 00:00 for the user: such times mean "this day, no
    time" (a date picked without a time)."""
    return local(value).time() == dt.time(0)
