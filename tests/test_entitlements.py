from datetime import timedelta

import pytest

from app.billing.entitlements import allowed_templates, can, effective_plan, limit
from app.billing.plans import PLANS
from app.models.base import utcnow


def test_new_user_is_on_the_free_plan(user):
    assert effective_plan(user) == "free"
    assert limit(user, "ai_daily_credits") == PLANS["free"]["ai_daily_credits"]
    assert can(user, "premium_templates") is False


def test_active_pro_plan_counts(user):
    user.plan = "pro"
    user.plan_expires_at = utcnow() + timedelta(days=30)
    assert effective_plan(user) == "pro"
    assert limit(user, "ai_daily_credits") == PLANS["pro"]["ai_daily_credits"]
    assert can(user, "premium_templates") is True


def test_expired_pro_plan_counts_as_free(user):
    user.plan = "pro"
    user.plan_expires_at = utcnow() - timedelta(seconds=1)
    assert effective_plan(user) == "free"
    assert limit(user, "ai_daily_credits") == PLANS["free"]["ai_daily_credits"]
    assert can(user, "premium_templates") is False


def test_expired_pro_plan_read_back_from_the_database_counts_as_free(session, user):
    # SQLite gives back dates without a timezone; the check must still work.
    user.plan = "pro"
    user.plan_expires_at = utcnow() - timedelta(days=1)
    session.commit()
    session.refresh(user)
    assert effective_plan(user) == "free"


def test_pro_plan_without_end_date_stays_active(user):
    user.plan = "pro"
    user.plan_expires_at = None
    assert effective_plan(user) == "pro"


def test_unknown_plan_name_counts_as_free(user):
    user.plan = "platinum"
    assert effective_plan(user) == "free"


def test_unknown_feature_is_not_allowed(user):
    assert can(user, "time_travel") is False


def test_unknown_limit_key_fails_loudly(user):
    with pytest.raises(KeyError):
        limit(user, "ai_daily_credit")  # typo on purpose


def test_no_limit_is_none(user):
    assert limit(user, "max_active_applications") is None


def test_allowed_templates_is_a_copy(user):
    templates = allowed_templates(user)
    assert templates == PLANS["free"]["cv_templates"]
    templates.append("hacked")
    assert "hacked" not in PLANS["free"]["cv_templates"]
