import pytest
from pydantic import ValidationError

from app.config import Settings

# A valid production secret: 32+ characters, not the placeholder.
GOOD_SECRET = "x" * 32


def make_settings(**overrides) -> Settings:
    # _env_file=None: ignore the developer's local .env so tests are repeatable.
    return Settings(_env_file=None, **overrides)


def test_development_defaults_are_valid():
    settings = make_settings()
    assert settings.app_env == "development"
    assert settings.database_url.startswith("sqlite")


def test_production_rejects_placeholder_secret_key():
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        make_settings(app_env="production", secret_key="change-me", encryption_key="k")


def test_production_rejects_short_secret_key():
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        make_settings(app_env="production", secret_key="too-short", encryption_key="k")


def test_production_rejects_empty_encryption_key():
    with pytest.raises(ValidationError, match="ENCRYPTION_KEY"):
        make_settings(app_env="production", secret_key=GOOD_SECRET, encryption_key="")


def test_production_accepts_real_secrets():
    settings = make_settings(app_env="production", secret_key=GOOD_SECRET, encryption_key="k")
    assert settings.app_env == "production"


def test_secrets_are_hidden_when_printed():
    settings = make_settings(secret_key=GOOD_SECRET)
    assert GOOD_SECRET not in repr(settings)
    assert GOOD_SECRET not in str(settings.secret_key)


def test_env_example_is_loadable():
    # Guards against .env.example drifting from the Settings fields or using a bad format.
    settings = Settings(_env_file=".env.example")
    assert settings.extension_origins.startswith("chrome-extension://")
    assert settings.email_from == "Unfurl <no-reply@example.com>"
    assert settings.debug is True
