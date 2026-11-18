"""Dates and times for people (D-071, D-109).

The database always stores UTC. People see and type dates and times in their own
time zone: User.timezone, or settings.default_timezone (Europe/Vienna) if they have
none yet.

The functions don't take the user: the zone of "the user we are working for right now"
sits in a ContextVar. A ContextVar is like a global variable, but each request (and
each thread that copies the context) has its own value, so two requests at the same
moment don't see each other's zone.
- In a request, app/main.py's UserTimezone middleware sets it from the session
  (auth_service.current_user keeps the session up to date with the database).
- In the scheduler, `with clock.use_timezone(user.timezone):` around each user.
"""

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache
from zoneinfo import ZoneInfo, available_timezones

from app.config import get_settings
from app.models.base import as_utc, utcnow

# The zone name of the current user; None means DEFAULT_TIMEZONE.
_current_zone: ContextVar[str | None] = ContextVar("current_zone", default=None)


@cache
def zone_names() -> frozenset[str]:
    """All valid IANA zone names ("Europe/Istanbul", "UTC", ...). Read from disk once."""
    return frozenset(available_timezones())


def valid_zone(name: str | None) -> str | None:
    """`name` if it is a known zone, else None (a value from a browser or a cookie)."""
    if isinstance(name, str) and name in zone_names():
        return name
    return None


def set_timezone(name: str | None) -> None:
    """Use `name` for the rest of this request (or thread). Unknown names mean the default."""
    _current_zone.set(valid_zone(name))


@contextmanager
def use_timezone(name: str | None) -> Iterator[None]:
    """`with use_timezone("Europe/Istanbul"):` works in that zone, then restores the old one."""
    token = _current_zone.set(valid_zone(name))
    try:
        yield
    finally:
        _current_zone.reset(token)


def tz() -> ZoneInfo:
    return ZoneInfo(_current_zone.get() or get_settings().default_timezone)


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
