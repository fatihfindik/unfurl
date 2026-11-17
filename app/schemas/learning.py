"""Forms of the Learning page (SPEC §6.10). The limits are D-094.

Numbers and dates are checked by our own validators (mode="before"), so every error
has a message that fits the field.
"""

import datetime as dt

from pydantic import Field, field_validator

from app.models.learning import UNIT_LABELS, WEEKDAYS
from app.models.profile import SHORT_TEXT, URL_TEXT
from app.schemas.profile import ProfileForm, check_url
from app.services import clock
from app.services.learning_service import MAX_TASK_MINUTES

MAX_HOURS_PER_WEEK = 40
MAX_UNITS = 52
MIN_TASK_MINUTES = 5
TARGET_YEARS = 5  # a target date at most 5 years ahead
TASK_DAYS_BACK = 30  # a task can be dated up to 30 days back …
TASK_YEARS = 2  # … and 2 years ahead
MAX_REST_DAYS = 6  # at least one day a week counts


def _number(value, kind, low, high, message: str):
    """`value` from a form field as int or float, within low…high, else ValueError.
    Our validators run before ProfileForm.clean_text, so "" still arrives as ""."""
    if value is None or str(value).strip() == "":
        return None
    try:
        number = kind(str(value).replace(",", "."))  # "1,5" works like "1.5"
    except ValueError:
        raise ValueError(message) from None
    if not low <= number <= high:
        raise ValueError(message)
    return number


def _date(value, low: dt.date, high: dt.date, message: str) -> dt.date | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        day = value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))
    except ValueError:
        raise ValueError("Use the format YYYY-MM-DD.") from None
    if not low <= day <= high:
        raise ValueError(message)
    return day


def years_ahead(day: dt.date, years: int) -> dt.date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


class GoalForm(ProfileForm):
    """New goal (D-093, D-094). The resource is optional; a link or total without a
    name is an error, so nothing the user typed is silently dropped."""

    title: str = Field(max_length=SHORT_TEXT)
    target_date: dt.date | None = None
    weekly_hours: float | None = None
    resource_name: str | None = Field(default=None, max_length=SHORT_TEXT)
    resource_url: str | None = Field(default=None, max_length=URL_TEXT)
    unit_label: str = "Week"
    units_total: int | None = None
    plan_weekly: bool = False

    @field_validator("target_date", mode="before")
    @classmethod
    def check_target_date(cls, value):
        today = clock.today()
        message = f"Pick a date between today and {TARGET_YEARS} years from now."
        return _date(value, today, years_ahead(today, TARGET_YEARS), message)

    @field_validator("weekly_hours", mode="before")
    @classmethod
    def check_hours(cls, value):
        message = f"Enter hours from 0.5 to {MAX_HOURS_PER_WEEK}."
        return _number(value, float, 0.5, MAX_HOURS_PER_WEEK, message)

    @field_validator("resource_url")
    @classmethod
    def check_link(cls, value):
        return check_url(value)

    @field_validator("unit_label", mode="before")
    @classmethod
    def check_unit(cls, value):
        value = str(value or "").strip() or "Week"
        if value not in UNIT_LABELS:
            raise ValueError("Choose from the list.")
        return value

    @field_validator("units_total", mode="before")
    @classmethod
    def check_units(cls, value):
        return _number(value, int, 1, MAX_UNITS, f"Enter a whole number from 1 to {MAX_UNITS}.")

    @field_validator("plan_weekly", mode="before")
    @classmethod
    def check_box(cls, value):
        return value == "yes"  # an unticked checkbox is not sent at all

    def field_errors(self) -> dict[str, str]:
        """Checks across fields, shown next to the field they concern."""
        errors = {}
        if self.resource_name is None and (self.resource_url or self.units_total):
            errors["resource_name"] = "Add the name of the course, book or platform."
        if self.plan_weekly and not self.units_total:
            errors["units_total"] = "Add how many there are to plan one task per week."
        return errors

    @property
    def resource(self) -> dict | None:
        """What learning_service.create_goal needs for the resource, or None."""
        if self.resource_name is None:
            return None
        return {
            "name": self.resource_name,
            "url": self.resource_url,
            "unit_label": self.unit_label,
            "units_total": self.units_total,
        }


class TaskForm(ProfileForm):
    """Add task. The goal is checked against the user in the route (get_owned)."""

    goal_id: str
    title: str = Field(max_length=SHORT_TEXT)
    due_date: dt.date
    minutes: int

    @field_validator("goal_id", mode="before")
    @classmethod
    def check_goal(cls, value):
        if not str(value or "").strip():
            raise ValueError("Choose a goal.")
        return str(value)

    @field_validator("due_date", mode="before")
    @classmethod
    def check_due_date(cls, value):
        return check_task_date(value)

    @field_validator("minutes", mode="before")
    @classmethod
    def check_minutes(cls, value):
        message = f"Enter minutes from {MIN_TASK_MINUTES} to {MAX_TASK_MINUTES}."
        number = _number(value, int, MIN_TASK_MINUTES, MAX_TASK_MINUTES, message)
        if number is None:
            raise ValueError(message)
        return number


def check_task_date(value) -> dt.date:
    """A task's date: from 30 days back to 2 years ahead (D-094). Also used to reschedule."""
    if value is None or str(value).strip() == "":
        raise ValueError("Pick a date.")
    today = clock.today()
    low = today - dt.timedelta(days=TASK_DAYS_BACK)
    message = f"Pick a date from {TASK_DAYS_BACK} days ago to {TASK_YEARS} years from now."
    return _date(value, low, years_ahead(today, TASK_YEARS), message)


def check_rest_days(days: list[str]) -> list[str]:
    """Rest days from the checkboxes (D-090): known days only, at most six."""
    if any(day not in WEEKDAYS for day in days):
        raise ValueError("Choose days from the list.")
    chosen = [day for day in WEEKDAYS if day in days]
    if len(chosen) > MAX_REST_DAYS:
        raise ValueError("Keep at least one day to study.")
    return chosen
