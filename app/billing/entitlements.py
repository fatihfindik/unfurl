"""What a user may do, based on their plan (SPEC §17.2).

The rest of the app asks these functions instead of reading `user.plan`,
so an expired plan, or a future payment provider, only changes code here.
"""

from datetime import datetime

from app.billing.plans import DEFAULT_PLAN, PLANS
from app.models import User
from app.models.base import as_utc, utcnow


def effective_plan(user: User, now: datetime | None = None) -> str:
    """The plan that counts right now.

    A plan whose `plan_expires_at` has passed counts as "free". A paid plan
    without an end date (given by hand) stays active. An unknown plan name
    also counts as "free", so a typo in the database never unlocks anything.
    """
    if user.plan not in PLANS:
        return DEFAULT_PLAN
    if user.plan_expires_at is not None:
        now = now or utcnow()
        if as_utc(user.plan_expires_at) <= now:
            return DEFAULT_PLAN
    return user.plan


def can(user: User, feature: str) -> bool:
    """True if the user's plan includes the feature. Unknown features are False."""
    return feature in PLANS[effective_plan(user)]["features"]


def limit(user: User, key: str) -> int | None:
    """A numeric limit of the user's plan, e.g. limit(user, "ai_daily_credits").

    None means no limit. An unknown key raises KeyError: a typo in a limit name
    should fail loudly in tests, not silently give "no limit".
    """
    return PLANS[effective_plan(user)][key]


def allowed_templates(user: User) -> list[str]:
    """CV templates the user's plan may use (a copy, so callers can't change PLANS)."""
    return list(PLANS[effective_plan(user)]["cv_templates"])
