"""Saving Settings › Notifications (SPEC §6.9, D-114)."""

from sqlmodel import Session

from app.config import get_settings
from app.models import NotificationPreference, User
from app.schemas.settings import NotificationSettings
from app.services import application_service, clock
from app.services import learning_service as learning

# Shown first in the time zone list, before all the others (D-108).
COMMON_ZONES = ("Europe/Vienna", "Europe/Berlin", "Europe/Istanbul", "Europe/London", "UTC")


def user_zone(user: User) -> str:
    """The user's time zone name, or the default one."""
    return user.timezone or get_settings().default_timezone


def zone_choices(current: str) -> list[tuple[str, str]]:
    """(value, label) pairs for the time zone list: the user's zone and a few common
    ones first, then every zone A–Z. Names are shown with spaces ("New York")."""
    first = [current] + [zone for zone in COMMON_ZONES if zone != current]
    rest = sorted(clock.zone_names() - set(first))
    return [(zone, zone.replace("_", " ")) for zone in first + rest]


def save_notifications(
    session: Session, user: User, form: NotificationSettings
) -> NotificationPreference:
    """Save the form. If the time zone changes, "all day" dates move with it (D-108);
    everything is saved in one commit."""
    prefs = learning.preferences(session, user)
    for name, value in form.switches.items():
        setattr(prefs, name, value)
    prefs.study_reminder_time = form.study_reminder_time
    prefs.rest_days = form.rest_days
    session.add(prefs)

    if form.timezone != user_zone(user):
        application_service.move_all_day_times(session, user, user.timezone, form.timezone)
    if form.timezone != user.timezone:
        user.timezone = form.timezone
        session.add(user)
    session.commit()
    session.refresh(prefs)
    return prefs
