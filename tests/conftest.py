"""Shared test setup.

The environment variables are set before `app` is imported, because the settings
and the database engine are created at import time.
"""

import os

os.environ["APP_ENV"] = "development"
os.environ["DATABASE_URL"] = "sqlite://"  # in-memory SQLite, nothing written to disk
os.environ["SCHEDULER_ENABLED"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    """An HTTP client that calls the app directly, without a running server."""
    return TestClient(app)
