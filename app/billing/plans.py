"""Plan definitions: every limit and feature in one place (SPEC §17.2).

Code never checks a plan name directly (`user.plan == "pro"` is not allowed).
It asks app.billing.entitlements instead: can(), limit(), allowed_templates().
"""

DEFAULT_PLAN = "free"

PLANS = {
    "free": {
        "ai_daily_credits": 30,
        "cv_templates": ["classic", "modern", "compact"],
        "max_active_applications": None,  # None = no limit
        "features": set(),
    },
    "pro": {
        "ai_daily_credits": 150,
        "cv_templates": ["classic", "modern", "compact"],  # grows when premium templates exist
        "max_active_applications": None,
        "features": {"premium_templates"},
    },
}
