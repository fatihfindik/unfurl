"""App settings, read from environment variables and the `.env` file.

Every variable from `.env.example` becomes a typed attribute here, so a wrong
value (e.g. `SMTP_PORT=abc`) is caught when the app starts, not later.
"""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The placeholder value from .env.example. It must never be used in production.
DEFAULT_SECRET_KEY = "change-me"
MIN_SECRET_KEY_LENGTH = 32


class Settings(BaseSettings):
    # Env var names are matched case-insensitively: APP_ENV -> app_env.
    # extra="ignore": variables only used by other tools (e.g. POSTGRES_* for
    # docker compose) are allowed in .env without causing an error.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    # Required, no default: if a deploy forgot APP_ENV and we silently fell back to
    # "development", every production check below would be skipped.
    app_env: Literal["development", "production"]
    app_base_url: str = "http://localhost:8000"
    # SecretStr hides the value when printed or logged ("**********").
    secret_key: SecretStr = SecretStr(DEFAULT_SECRET_KEY)
    encryption_key: SecretStr = SecretStr("")
    debug: bool = False

    # Database
    database_url: str = "sqlite:///./unfurl.db"

    # Email
    email_backend: Literal["console", "smtp"] = "console"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    email_from: str = "Unfurl <no-reply@example.com>"

    # AI
    anthropic_api_key: SecretStr = SecretStr("")
    ai_model_fast: str = "claude-haiku-5-5"
    ai_model_smart: str = "claude-sonnet-5-5"
    ai_global_daily_credits: int = 3000

    # Billing (off in the MVP)
    billing_enabled: bool = False

    # Extension: comma-separated "chrome-extension://<id>" values (D-137)
    extension_origins: str = ""

    @property
    def allowed_hosts(self) -> list[str]:
        """Host header names the app answers to (D-168, against DNS rebinding): the
        name in APP_BASE_URL, and the local names the dev server, the personal
        instance, its Docker health check and the extension use. "testserver" is the
        test client's name, only in development (a rebinding page sends its own
        domain, never that name)."""
        hosts = ["localhost", "127.0.0.1"]
        base = urlsplit(self.app_base_url).hostname
        if base and base not in hosts:
            hosts.append(base)
        if self.app_env == "development":
            hosts.append("testserver")
        return hosts

    @property
    def extension_origin_list(self) -> frozenset[str]:
        origins = (origin.strip().rstrip("/") for origin in self.extension_origins.split(","))
        return frozenset(origin for origin in origins if origin)

    # Scheduler / reminders
    scheduler_enabled: bool = True
    default_timezone: str = "Europe/Vienna"

    # Uploads
    upload_dir: str = "./uploads"

    # Legal pages (D-133). Who runs the site, for the Impressum: only in .env, never
    # in the repository. Until a lawyer has reviewed the texts, the pages say "Draft".
    legal_operator_name: str = ""
    legal_address: str = ""
    legal_email: str = ""
    legal_reviewed: bool = False

    @field_validator("default_timezone")
    @classmethod
    def check_timezone(cls, value: str) -> str:
        """A wrong name (e.g. "Europe/Viena") stops the app at start, instead of a
        500 on every page that shows a date (security review M4b, 4)."""
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"DEFAULT_TIMEZONE is not a known time zone: {value!r}") from None
        return value

    @model_validator(mode="after")
    def check_production_settings(self) -> "Settings":
        """Refuse to start in production with debug on or with missing or placeholder
        secrets (SPEC §10.1, §11)."""
        if self.app_env != "production":
            return self

        # Debug mode shows a full stack trace on every server error.
        if self.debug:
            raise ValueError("DEBUG must be false in production.")

        secret_key = self.secret_key.get_secret_value()
        if secret_key == DEFAULT_SECRET_KEY or len(secret_key) < MIN_SECRET_KEY_LENGTH:
            raise ValueError(
                f"SECRET_KEY must be set to a random value of at least "
                f"{MIN_SECRET_KEY_LENGTH} characters in production."
            )
        if not self.encryption_key.get_secret_value():
            raise ValueError("ENCRYPTION_KEY must be set in production.")

        # Verification and reset emails must really be sent, not printed (D-029).
        if self.email_backend != "smtp":
            raise ValueError("EMAIL_BACKEND must be smtp in production.")
        if not self.smtp_host:
            raise ValueError("SMTP_HOST must be set in production.")
        return self


# lru_cache: the settings are read once and the same object is reused afterwards.
@lru_cache
def get_settings() -> Settings:
    return Settings()
