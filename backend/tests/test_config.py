"""Settings loading tests."""

from collections.abc import Generator

import pytest
from _pytest.monkeypatch import MonkeyPatch

from app.core.config import DEFAULT_DATABASE_PATH, get_settings


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
    for variable in ("APP_NAME", "ENVIRONMENT", "LOG_LEVEL", "DATABASE_URL"):
        monkeypatch.delenv(variable, raising=False)

    settings = get_settings()

    assert settings.app_name == "Investment Agent"
    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.database_url == f"sqlite:///{DEFAULT_DATABASE_PATH}"


def test_settings_read_environment_variables(monkeypatch: MonkeyPatch) -> None:
    """Settings read supported values from the environment."""
    monkeypatch.setenv("APP_NAME", "Test Investment Agent")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///data/test.db")

    settings = get_settings()

    assert settings.app_name == "Test Investment Agent"
    assert settings.environment == "test"
    assert settings.log_level == "DEBUG"
    assert settings.database_url == "sqlite:///data/test.db"
