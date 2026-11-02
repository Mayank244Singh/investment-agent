"""Bearer authentication and startup security tests."""

import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app

TOKEN = "auth-test-token-0123456789abcdef"
PROTECTED_ROUTE = "/api/v1/risk/rules"


def test_valid_token_allows_protected_route() -> None:
    with TestClient(create_app(_settings())) as client:
        response = client.get(
            PROTECTED_ROUTE, headers={"Authorization": f"Bearer {TOKEN}"}
        )

    assert response.status_code == 200


def test_missing_token_returns_generic_bearer_challenge() -> None:
    with TestClient(create_app(_settings())) as client:
        response = client.get(PROTECTED_ROUTE)

    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthorized"
    assert response.headers["www-authenticate"] == "Bearer"


def test_wrong_token_returns_same_generic_bearer_challenge() -> None:
    with TestClient(create_app(_settings())) as client:
        response = client.get(
            PROTECTED_ROUTE,
            headers={"Authorization": "Bearer definitely-the-wrong-token-value"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthorized"
    assert response.headers["www-authenticate"] == "Bearer"


def test_non_ascii_authorization_header_returns_401_not_500() -> None:
    with TestClient(create_app(_settings())) as client:
        response = client.get(
            PROTECTED_ROUTE,
            headers=[(b"authorization", b"Bearer non-ascii-\xff-token")],
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_health_remains_open_without_a_token() -> None:
    with TestClient(create_app(_settings())) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200


def test_startup_configuration_refuses_missing_token() -> None:
    settings = Settings.model_validate(
        {
            "auth_token": None,
            "auth_disabled": False,
            "backend_host": "127.0.0.1",
        }
    )
    with pytest.raises(RuntimeError, match="AUTH_TOKEN is required"):
        create_app(settings)


def test_startup_configuration_refuses_short_token() -> None:
    with pytest.raises(ValidationError, match="at least 32"):
        Settings.model_validate(
            {
                "auth_token": "too-short",
                "auth_disabled": False,
                "backend_host": "127.0.0.1",
            }
        )


def test_auth_disabled_is_refused_on_non_loopback_host() -> None:
    settings = Settings.model_validate(
        {
            "auth_token": None,
            "auth_disabled": True,
            "backend_host": "0.0.0.0",
        }
    )
    with pytest.raises(RuntimeError, match="loopback BACKEND_HOST"):
        create_app(settings)


def test_auth_disabled_logs_prominent_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    create_app(
        Settings.model_validate(
            {
                "auth_token": None,
                "auth_disabled": True,
                "backend_host": "127.0.0.1",
            }
        )
    )

    assert "AUTHENTICATION IS DISABLED" in capsys.readouterr().out


def test_non_loopback_disables_docs_warns_and_never_logs_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = create_app(_settings(backend_host="0.0.0.0"))
    with TestClient(application) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
        response = client.get(
            PROTECTED_ROUTE, headers={"Authorization": f"Bearer {TOKEN}"}
        )

    captured = capsys.readouterr()
    logs = captured.out + captured.err
    assert response.status_code == 200
    assert "not 127.0.0.1" in logs
    assert TOKEN not in logs


def test_cors_allows_only_the_known_frontend_origin() -> None:
    application = create_app(_settings())
    with TestClient(application) as client:
        allowed = client.options(
            PROTECTED_ROUTE,
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        disallowed = client.options(
            PROTECTED_ROUTE,
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert allowed.headers["access-control-allow-origin"] == (
        "http://127.0.0.1:5173"
    )
    assert "access-control-allow-origin" not in disallowed.headers


def _settings(*, backend_host: str = "127.0.0.1") -> Settings:
    return Settings.model_validate(
        {
            "auth_token": TOKEN,
            "auth_disabled": False,
            "backend_host": backend_host,
            "log_level": logging.getLevelName(logging.INFO),
        }
    )
