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


@pytest.mark.parametrize(
    "url",
    ["http://localhost:8000", "http://unfurl.example.com", "unfurl.example.com", "https://"],
)
def test_production_rejects_a_base_url_that_is_not_https(url):
    with pytest.raises(ValidationError, match="APP_BASE_URL"):
        make_production_settings(app_base_url=url)


@pytest.mark.parametrize("value", [None, "", "  "])
def test_production_needs_forwarded_allow_ips_set_on_purpose(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("FORWARDED_ALLOW_IPS")
    else:
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", value)
    with pytest.raises(ValidationError, match="FORWARDED_ALLOW_IPS"):
        make_production_settings()


def test_forwarded_allow_ips_only_in_an_env_file_is_not_enough(monkeypatch, tmp_path):
    # uvicorn doesn't read .env, so a value only there would be ignored.
    monkeypatch.delenv("FORWARDED_ALLOW_IPS")
    env_file = tmp_path / ".env"
    env_file.write_text("FORWARDED_ALLOW_IPS=*\n")
    with pytest.raises(ValidationError, match="FORWARDED_ALLOW_IPS"):
        Settings(_env_file=env_file, **PRODUCTION_SETTINGS)


def test_development_needs_neither_https_nor_forwarded_allow_ips(monkeypatch):
    monkeypatch.delenv("FORWARDED_ALLOW_IPS")
    settings = make_settings(app_env="development", app_base_url="http://127.0.0.1:8100")
    assert settings.app_base_url.startswith("http://")


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
    # A production app only answers to its own host names (D-168), not "testserver".
    production_client = TestClient(create_app(settings), base_url="http://localhost")
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
