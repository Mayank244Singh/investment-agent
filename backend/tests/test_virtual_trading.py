"""Synthetic-only virtual trading regression tests; all databases are temporary."""

import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select

import app.db.models  # noqa: F401
from alembic import command
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.models.ledger import LedgerTransaction, ProfitVaultEntry
from app.db.models.virtual import VirtualOrderAttempt
from app.main import create_app
from app.services.virtual_trading_service import (
    FIXTURE_SHA256,
    IST,
    SyntheticQuote,
    VirtualIdempotencyConflict,
    VirtualOrder,
    VirtualUnavailable,
    account_summary,
    preview_order,
    seed_virtual_account,
    submit_order,
    virtual_session,
)

NOW = datetime(2026, 11, 9, 7, 0, tzinfo=UTC)


def _json(value: dict[str, object]) -> dict[str, Any]:
    """Narrow the service's JSON-shaped result for response assertions."""
    return cast("dict[str, Any]", value)


@pytest.fixture
def setup(tmp_path: Path) -> tuple[Path, Settings]:
    path = tmp_path / "virtual.db"
    Base.metadata.create_all(create_engine(f"sqlite:///{path}"))
    real = tmp_path / "real.db"
    real.write_bytes(b"untouched-production-stand-in")
    settings = Settings(  # type: ignore[call-arg]  # pydantic-settings runtime option
        _env_file=None,
        database_url=f"sqlite:///{real}",
        virtual_database_url=f"sqlite:///{path}",
        auth_disabled=True,
    )
    assert seed_virtual_account(path, settings.database_url, now=NOW)
    return path, settings


def test_seed_is_idempotent_and_isolated(setup: tuple[Path, Settings]) -> None:
    path, settings = setup
    real = path.parent / "real.db"
    before = sha256(real.read_bytes()).hexdigest()
    assert not seed_virtual_account(path, settings.database_url, now=NOW)
    with virtual_session(settings) as session:
        assert (
            _json(account_summary(session, settings, now=NOW))["cash_balance_paise"]
            == 1_000_000
        )
    assert sha256(real.read_bytes()).hexdigest() == before
    with pytest.raises(VirtualUnavailable):
        seed_virtual_account(real, settings.database_url)


def test_provisional_or_canonical_path_cannot_be_used_for_virtual_seed(
    tmp_path: Path,
) -> None:
    for name in ("pilot.db", "provisional.db", "investment_agent.db"):
        path = tmp_path / name
        Base.metadata.create_all(create_engine(f"sqlite:///{path}"))
        with pytest.raises(VirtualUnavailable):
            seed_virtual_account(path, "sqlite:////tmp/other-real.db")


def test_virtual_order_does_not_open_a_network_socket(
    setup: tuple[Path, Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, settings = setup

    def forbidden_socket(*_: object, **__: object) -> None:
        raise AssertionError("virtual trading attempted a network socket")

    monkeypatch.setattr(socket, "socket", forbidden_socket)
    with virtual_session(settings) as session:
        outcome = submit_order(
            session,
            VirtualOrder("buy", "INFY", 1, 7_900),
            "offline-only",
            settings,
            now=NOW,
        )
    assert outcome["status"] == "filled"


def test_approved_buy_preview_is_read_only_and_fill_is_once(
    setup: tuple[Path, Settings],
) -> None:
    _, settings = setup
    order = VirtualOrder("buy", "INFY", 10, 7_900)
    with virtual_session(settings) as session:
        preview = _json(preview_order(session, order, settings, now=NOW))
        assert preview["approved"] is True
        assert preview["fill_paise"] > preview["reference_paise"]
        assert preview["fees_paise"] > 0
        assert session.scalar(select(func.count(LedgerTransaction.id))) == 0
    with virtual_session(settings) as session:
        first = _json(submit_order(session, order, "once", settings, now=NOW))
        assert first["status"] == "filled"
        assert first["account"]["cash_balance_paise"] == (
            1_000_000 - 10 * first["fill_paise"] - first["fees_paise"]
        )
    with virtual_session(settings) as session:
        repeated = _json(submit_order(session, order, "once", settings, now=NOW))
        assert repeated["id"] == first["id"]
        assert session.scalar(select(func.count(LedgerTransaction.id))) == 1
        assert repeated["account"]["holdings"][0]["cost_basis_paise"] == (
            10 * first["fill_paise"] + first["fees_paise"]
        )


def test_changed_idempotency_payload_conflicts(setup: tuple[Path, Settings]) -> None:
    _, settings = setup
    with virtual_session(settings) as session:
        submit_order(
            session, VirtualOrder("buy", "INFY", 1, 7_900), "key", settings, now=NOW
        )
    with (
        virtual_session(settings) as session,
        pytest.raises(VirtualIdempotencyConflict),
    ):
        submit_order(
            session, VirtualOrder("buy", "INFY", 2, 7_900), "key", settings, now=NOW
        )


@pytest.mark.parametrize(
    ("order", "code"),
    [
        (VirtualOrder("buy", "INFY", 200, 7_900), "insufficient_cash"),
        (VirtualOrder("buy", "INFY", 26, 7_900), "virtual_stock_exposure"),
        (VirtualOrder("buy", "INFY", 10, 7_500), "virtual_planned_risk"),
        (VirtualOrder("sell", "INFY", 1), "insufficient_holdings"),
        (VirtualOrder("buy", "INFY", 1), "stop_loss_required"),
        (VirtualOrder("buy", "UNKNOWN", 1, 1), "missing_synthetic_quote"),
    ],
)
def test_risk_rejections_write_no_fill(
    setup: tuple[Path, Settings],
    order: VirtualOrder,
    code: str,
) -> None:
    _, settings = setup
    with virtual_session(settings) as session:
        outcome = _json(submit_order(session, order, code, settings, now=NOW))
        assert outcome["status"] == "rejected"
        assert code in {reason["code"] for reason in outcome["reasons"]}
        assert session.scalar(select(func.count(LedgerTransaction.id))) == 0


def test_stale_quote_rejected(setup: tuple[Path, Settings]) -> None:
    _, settings = setup
    stale = {"INFY": SyntheticQuote("INFY", 8_000, date(2026, 11, 1))}
    with virtual_session(settings) as session:
        result = _json(preview_order(
            session,
            VirtualOrder("buy", "INFY", 1, 7_900),
            settings,
            now=NOW,
            quotes=stale,
        ))
        assert {x["code"] for x in result["reasons"]} >= {
            "invalid_or_stale_synthetic_quote"
        }


def test_loss_limit_blocks_buy_but_not_sell(setup: tuple[Path, Settings]) -> None:
    _, settings = setup
    with virtual_session(settings) as session:
        assert (
            submit_order(
                session,
                VirtualOrder("buy", "INFY", 10, 7_900),
                "buy",
                settings,
                now=NOW,
            )["status"]
            == "filled"
        )
    cheap = {"INFY": SyntheticQuote("INFY", 6_000, NOW.astimezone().date())}
    with virtual_session(settings) as session:
        assert (
            submit_order(
                session,
                VirtualOrder("sell", "INFY", 5),
                "loss",
                settings,
                now=NOW,
                quotes=cheap,
            )["status"]
            == "filled"
        )
    with virtual_session(settings) as session:
        rejected = _json(submit_order(
            session,
            VirtualOrder("buy", "INFY", 1, 7_900),
            "after-loss",
            settings,
            now=NOW,
        ))
        assert "virtual_daily_loss" in {x["code"] for x in rejected["reasons"]}
        assert (
            submit_order(
                session, VirtualOrder("sell", "INFY", 5), "exit", settings, now=NOW
            )["status"]
            == "filled"
        )


def test_daily_loss_resets_at_midnight_asia_kolkata(
    setup: tuple[Path, Settings],
) -> None:
    _, settings = setup
    with virtual_session(settings) as session:
        submit_order(
            session,
            VirtualOrder("buy", "INFY", 10, 7_900),
            "before-midnight-buy",
            settings,
            now=NOW,
        )
    cheap = {"INFY": SyntheticQuote("INFY", 6_000, NOW.astimezone(IST).date())}
    with virtual_session(settings) as session:
        submit_order(
            session,
            VirtualOrder("sell", "INFY", 10),
            "before-midnight-loss",
            settings,
            now=NOW,
            quotes=cheap,
        )
    next_ist_day = datetime(2026, 11, 9, 18, 31, tzinfo=UTC)
    with virtual_session(settings) as session:
        result = _json(submit_order(
            session,
            VirtualOrder("buy", "INFY", 1, 7_900),
            "after-midnight",
            settings,
            now=next_ist_day,
        ))
    assert result["status"] == "filled"


def test_profit_vault_and_fifo_remain_consistent(setup: tuple[Path, Settings]) -> None:
    _, settings = setup
    with virtual_session(settings) as session:
        submit_order(
            session, VirtualOrder("buy", "INFY", 10, 7_900), "buy", settings, now=NOW
        )
    high = {"INFY": SyntheticQuote("INFY", 10_000, NOW.astimezone().date())}
    with virtual_session(settings) as session:
        result = _json(submit_order(
            session,
            VirtualOrder("sell", "INFY", 5),
            "profit",
            settings,
            now=NOW,
            quotes=high,
        ))
        assert result["status"] == "filled"
        assert session.scalar(select(func.count(ProfitVaultEntry.id))) == 1
        assert result["account"]["profit_vault_balance_paise"] > 0
        assert result["account"]["holdings"][0]["quantity"] == 5


def test_rollback_when_audit_commit_fails(
    setup: tuple[Path, Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, settings = setup
    with virtual_session(settings) as session:

        def fail() -> None:
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(session, "commit", fail)
        with pytest.raises(RuntimeError):
            submit_order(
                session,
                VirtualOrder("buy", "INFY", 1, 7_900),
                "rollback",
                settings,
                now=NOW,
            )
    with virtual_session(settings) as session:
        assert session.scalar(select(func.count(LedgerTransaction.id))) == 0
        assert session.scalar(select(func.count(VirtualOrderAttempt.id))) == 0


def test_concurrent_orders_cannot_spend_same_cash(setup: tuple[Path, Settings]) -> None:
    _, original = setup
    settings = original.model_copy(
        update={
            "risk_max_order_percent": 100,
            "risk_max_stock_allocation_percent": 100,
            "risk_max_loss_per_trade_percent": 100,
            "virtual_max_planned_loss_paise": 1_000_000,
            "virtual_max_stock_exposure_paise": 1_000_000,
        }
    )

    def buy(key: str) -> str:
        with virtual_session(settings) as session:
            return str(
                submit_order(
                    session,
                    VirtualOrder("buy", "INFY", 90, 7_900),
                    key,
                    settings,
                    now=NOW,
                )["status"]
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(buy, ("a", "b")))
    assert sorted(results) == ["filled", "rejected"]


def test_quote_future_date_rejected(setup: tuple[Path, Settings]) -> None:
    _, settings = setup
    future = {
        "INFY": SyntheticQuote(
            "INFY", 8_000, NOW.date() + timedelta(days=1), fixture_sha256=FIXTURE_SHA256
        )
    }
    with virtual_session(settings) as session:
        result = _json(preview_order(
            session,
            VirtualOrder("buy", "INFY", 1, 7_900),
            settings,
            now=NOW,
            quotes=future,
        ))
    assert "invalid_or_stale_synthetic_quote" in {x["code"] for x in result["reasons"]}


def test_virtual_routes_require_auth_and_fill_only_after_preview(
    setup: tuple[Path, Settings],
) -> None:
    _, base = setup
    token = "virtual-test-token-0123456789abcdef"
    settings = Settings(  # type: ignore[call-arg]  # pydantic-settings runtime option
        _env_file=None,
        database_url=base.database_url,
        virtual_database_url=base.virtual_database_url,
        auth_token=SecretStr(token),
        auth_disabled=False,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/virtual/account").status_code == 401
        headers = {"Authorization": f"Bearer {token}"}
        account = client.get("/api/v1/virtual/account", headers=headers)
        assert account.status_code == 200
        assert account.json()["initial_capital_paise"] == 1_000_000
        assert len(client.get("/api/v1/virtual/quotes", headers=headers).json()) == 5
        order = {
            "side": "buy",
            "symbol": "INFY",
            "quantity": 1,
            "stop_loss_paise": 7_900,
            "order_type": "market",
        }
        preview = client.post(
            "/api/v1/virtual/orders/preview", headers=headers, json=order
        )
        assert preview.status_code == 200
        assert preview.json()["approved"]
        assert client.get("/api/v1/virtual/orders", headers=headers).json() == []
        assert (
            client.post(
                "/api/v1/virtual/orders", headers=headers, json=order
            ).status_code
            == 422
        )
        filled = client.post(
            "/api/v1/virtual/orders",
            headers={**headers, "Idempotency-Key": "api-one"},
            json=order,
        )
        assert filled.status_code == 200
        assert filled.json()["status"] == "filled"
        assert len(client.get("/api/v1/virtual/orders", headers=headers).json()) == 1


def test_virtual_migration_upgrade_and_downgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "virtual_migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    try:
        command.upgrade(config, "head")
        with sqlite3.connect(database) as connection:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert {"virtual_accounts", "virtual_order_attempts"} <= names
        protected_url = "sqlite:////tmp/real-never-opened.db"
        assert seed_virtual_account(database, protected_url, now=NOW)
        assert not seed_virtual_account(database, protected_url, now=NOW)
        command.downgrade(config, "0004_market_data_foundation")
        with sqlite3.connect(database) as connection:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "virtual_accounts" not in names
            assert "virtual_order_attempts" not in names
    finally:
        get_settings.cache_clear()
