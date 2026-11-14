import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app
from tests.helpers import PRODUCTION_SETTINGS

# A valid production secret: 32+ characters, not the placeholder.
GOOD_SECRET = PRODUCTION_SETTINGS["secret_key"]


def make_settings(**overrides) -> Settings:
    # _env_file=None: ignore the developer's local .env so tests are repeatable.
    return Settings(_env_file=None, **overrides)


def make_production_settings(**overrides) -> Settings:
    """Valid production settings, with some values changed."""
    return make_settings(**{**PRODUCTION_SETTINGS, **overrides})


def test_development_defaults_are_valid():
    settings = make_settings(app_env="development")
    assert settings.app_env == "development"
    assert settings.database_url.startswith("sqlite")


def test_missing_app_env_refuses_to_start(monkeypatch):
    # conftest.py sets APP_ENV for the whole test run; remove it for this test only.
    monkeypatch.delenv("APP_ENV")
    with pytest.raises(ValidationError, match="app_env"):
        make_settings()


def test_production_rejects_debug():
    with pytest.raises(ValidationError, match="DEBUG"):
        make_production_settings(debug=True)


def test_production_rejects_placeholder_secret_key():
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        make_production_settings(secret_key="change-me")


def test_production_rejects_short_secret_key():
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        make_production_settings(secret_key="too-short")


def test_production_rejects_empty_encryption_key():
    with pytest.raises(ValidationError, match="ENCRYPTION_KEY"):
        make_production_settings(encryption_key="")


def test_production_rejects_console_email_backend():
    with pytest.raises(ValidationError, match="EMAIL_BACKEND"):
        make_production_settings(email_backend="console")


def test_production_rejects_empty_smtp_host():
    with pytest.raises(ValidationError, match="SMTP_HOST"):
        make_production_settings(smtp_host="")


def test_production_accepts_real_secrets_and_smtp():
    settings = make_production_settings()
    assert settings.app_env == "production"


API_DOCS_URLS = ["/docs", "/redoc", "/openapi.json"]


@pytest.mark.parametrize("url", API_DOCS_URLS)
def test_api_docs_are_shown_in_development(client, url):
    assert client.get(url).status_code == 200


@pytest.mark.parametrize("url", API_DOCS_URLS)
def test_api_docs_are_hidden_in_production(url):
    settings = make_production_settings()
    production_client = TestClient(create_app(settings))
    assert production_client.get(url).status_code == 404


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
