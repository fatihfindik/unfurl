"""The Settings › Notifications form (SPEC §6.9, D-114).

A plain function instead of a pydantic model: the form is mostly checkboxes, and an
unticked checkbox is simply missing from what the browser sends.
"""

import datetime as dt
from dataclasses import dataclass

from starlette.datastructures import FormData

from app.schemas.learning import check_rest_days
from app.services import clock

# "Remind me at": every half hour from 06:00 to 23:00 (D-107).
REMINDER_TIMES = [dt.time(hour, minute) for hour in range(6, 24) for minute in (0, 30)][:-1]

# The on/off switches, in the order the page shows them.
SWITCHES = (
    "study_reminder_enabled",
    "study_reminder_email",
    "streak_enabled",
    "follow_up_enabled",
    "interview_enabled",
    "stale_saved_enabled",
)


@dataclass
class NotificationSettings:
    switches: dict[str, bool]
    study_reminder_time: dt.time
    rest_days: list[str]
    timezone: str


def time_value(value: dt.time) -> str:
    """How a reminder time is written in the form: "19:00"."""
    return value.strftime("%H:%M")


def parse_notification_settings(data: FormData) -> tuple[NotificationSettings | None, dict]:
    """The checked form, or None and {field: error message}."""
    errors = {}

    times = {time_value(value): value for value in REMINDER_TIMES}
    reminder_time = times.get(str(data.get("study_reminder_time", "")))
    if reminder_time is None:
        errors["study_reminder_time"] = "Choose a time from the list."

    try:
        rest_days = check_rest_days([str(day) for day in data.getlist("rest_days")])
    except ValueError as exc:
        rest_days = []
        errors["rest_days"] = str(exc)

    timezone = clock.valid_zone(str(data.get("timezone", "")))
    if timezone is None:
        errors["timezone"] = "Choose a time zone from the list."

    if errors:
        return None, errors
    switches = {name: data.get(name) == "yes" for name in SWITCHES}
    return NotificationSettings(switches, reminder_time, rest_days, timezone), {}
