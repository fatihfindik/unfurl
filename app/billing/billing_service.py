"""Changing a user's plan (SPEC §17.2).

set_plan() is the only place that writes `User.plan`. In the MVP only the grant
CLI calls it; later a payment provider's webhook will call it too (§17.5).
"""

import logging
from datetime import datetime

from sqlmodel import Session

from app.billing.plans import DEFAULT_PLAN, PLANS
from app.models import User

logger = logging.getLogger("unfurl.billing")


def set_plan(
    session: Session, user: User, plan: str, expires_at: datetime | None, reason: str
) -> None:
    """Give the user `plan` until `expires_at` (None = no end date) and log the change.

    The free plan never has an end date, so expires_at is ignored for it.
    """
    if plan not in PLANS:
        raise ValueError(f"Unknown plan: {plan!r}. Valid plans: {', '.join(PLANS)}.")
    if plan == DEFAULT_PLAN:
        expires_at = None

    old_plan, old_expires_at = user.plan, user.plan_expires_at
    user.plan = plan
    user.plan_expires_at = expires_at
    session.add(user)
    session.commit()

    # Every plan change is logged (later this may become a PlanChange table).
    # Only the user id, no email: logs should not hold personal data.
    logger.info(
        "Plan changed: user_id=%s %s (expires %s) -> %s (expires %s), reason: %s",
        user.id,
        old_plan,
        old_expires_at,
        plan,
        expires_at,
        reason,
    )
