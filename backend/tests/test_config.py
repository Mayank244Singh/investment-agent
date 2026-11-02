"""Settings loading tests."""

from collections.abc import Generator

import pytest
from _pytest.monkeypatch import MonkeyPatch

from app.core.config import DEFAULT_DATABASE_PATH, PROJECT_ROOT, get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None, None, None]:
    """Prevent cached settings from leaking between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_settings_use_defaults_without_environment_variables(
    monkeypatch: MonkeyPatch,
) -> None:
    """Settings fall back to safe local-development defaults."""
    for variable in (
        "APP_NAME",
        "ENVIRONMENT",
        "LOG_LEVEL",
        "DATABASE_URL",
        "BACKUP_DIRECTORY",
        "BACKUP_RETENTION_COUNT",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("AUTH_TOKEN", "")
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("BACKEND_HOST", "127.0.0.1")

    settings = get_settings()

    assert settings.app_name == "Investment Agent"
    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.database_url == f"sqlite:///{DEFAULT_DATABASE_PATH}"
    assert settings.backup_directory == PROJECT_ROOT / "backups"
    assert settings.backup_retention_count == 10
    assert settings.auth_token is None
    assert settings.auth_disabled is True
    assert settings.backend_host == "127.0.0.1"


def test_settings_read_environment_variables(monkeypatch: MonkeyPatch) -> None:
    """Settings read supported values from the environment."""
    monkeypatch.setenv("APP_NAME", "Test Investment Agent")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///data/test.db")
    monkeypatch.setenv("BACKUP_DIRECTORY", "local-backups")
    monkeypatch.setenv("BACKUP_RETENTION_COUNT", "3")
    monkeypatch.setenv("AUTH_TOKEN", "configured-test-token-0123456789")
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("BACKEND_HOST", "127.0.0.1")

    settings = get_settings()

    assert settings.app_name == "Test Investment Agent"
    assert settings.environment == "test"
    assert settings.log_level == "DEBUG"
    assert settings.database_url == "sqlite:///data/test.db"
    assert settings.backup_directory == PROJECT_ROOT / "local-backups"
    assert settings.backup_retention_count == 3
    assert settings.auth_token is not None
    assert settings.auth_token.get_secret_value() == (
        "configured-test-token-0123456789"
    )
    assert settings.auth_disabled is False
