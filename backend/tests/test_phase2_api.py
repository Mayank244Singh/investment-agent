"""Phase 2 ledger-read and validation-only API tests."""

from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models.ledger import (
    CapitalMovement,
    HoldingLot,
    LedgerTransaction,
    LotRealization,
    ProfitVaultEntry,
)
from app.db.session import get_db_session
from app.main import app
from app.modules.portfolio import LedgerService

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)


@dataclass(frozen=True)
class ApiContext:
    client: TestClient
    session_factory: sessionmaker[Session]


@pytest.fixture
def api() -> Generator[ApiContext, None, None]:
    """Provide an isolated API and SQLite database."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    try:
        with TestClient(app) as client:
            yield ApiContext(client, session_factory)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_read_empty_ledger(api: ApiContext) -> None:
    response = api.client.get("/api/v1/portfolio/ledger")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == {
        "cash_balance_paise": 0,
        "deployable_cash_paise": 0,
        "open_cost_basis_paise": 0,
        "realized_pnl_paise": 0,
        "profit_vault_balance_paise": 0,
        "risk_capital_paise": 0,
    }
    assert payload["holdings"] == []
    assert payload["transactions"] == []
    assert payload["realizations"] == []


def test_read_ledger_returns_persisted_records_and_derived_totals(
    api: ApiContext,
) -> None:
    with api.session_factory() as session:
        ledger = LedgerService(session)
        ledger.record_deposit(100_000, NOW)
        ledger.record_buy("INFY", 10, 1_000, NOW, fees_paise=100)
        ledger.record_sell(
            "INFY", 4, 1_500, NOW + timedelta(days=1), fees_paise=40
        )

    response = api.client.get("/api/v1/portfolio/ledger")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["realized_pnl_paise"] == 1_920
    assert payload["summary"]["profit_vault_balance_paise"] == 1_920
    assert payload["holdings"] == [
        {"symbol": "INFY", "quantity": 6, "cost_basis_paise": 6_060}
    ]
    assert len(payload["capital_movements"]) == 1
    assert len(payload["transactions"]) == 2
    assert len(payload["lots"]) == 1
    assert len(payload["realizations"]) == 1
    assert len(payload["profit_vault_entries"]) == 1


def test_manual_ledger_post_endpoints_append_deposit_withdrawal_buy_and_sell(
    api: ApiContext,
) -> None:
    deposit = api.client.post(
        "/api/v1/portfolio/deposits",
        json={"amount_paise": 100_000, "occurred_at_utc": NOW.isoformat()},
    )
    withdrawal = api.client.post(
        "/api/v1/portfolio/withdrawals",
        json={
            "amount_paise": 10_000,
            "occurred_at_utc": (NOW + timedelta(minutes=1)).isoformat(),
        },
    )
    buy = api.client.post(
        "/api/v1/portfolio/buys",
        json={
            "symbol": "infy",
            "quantity": 5,
            "price_paise": 1_000,
            "fees_paise": 50,
            "transacted_at_utc": (NOW + timedelta(minutes=2)).isoformat(),
        },
    )
    sell = api.client.post(
        "/api/v1/portfolio/sells",
        json={
            "symbol": "INFY",
            "quantity": 2,
            "price_paise": 1_500,
            "fees_paise": 20,
            "transacted_at_utc": (NOW + timedelta(minutes=3)).isoformat(),
        },
    )

    assert [response.status_code for response in (deposit, withdrawal, buy, sell)] == [
        201,
        201,
        201,
        201,
    ]
    assert deposit.json()["movement_type"] == "deposit"
    assert withdrawal.json()["movement_type"] == "withdrawal"
    assert buy.json()["transaction_type"] == "buy"
    assert buy.json()["symbol"] == "INFY"
    assert sell.json()["transaction_type"] == "sell"


def test_historical_buy_bypasses_risk_validation(api: ApiContext) -> None:
    response = api.client.post(
        "/api/v1/portfolio/buys",
        json={
            "symbol": "INFY",
            "quantity": 100,
            "price_paise": 1_000,
            "fees_paise": 10,
            "transacted_at_utc": NOW.isoformat(),
        },
    )

    assert response.status_code == 201
    assert response.json()["transaction_type"] == "buy"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", ""),
        ("symbol", "BAD SYMBOL"),
        ("quantity", 0),
        ("quantity", 1.5),
        ("price_paise", 0),
        ("price_paise", 1.5),
        ("fees_paise", -1),
        ("fees_paise", 0.5),
        ("transacted_at_utc", datetime.now(UTC).replace(tzinfo=None).isoformat()),
        (
            "transacted_at_utc",
            (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        ),
    ],
)
def test_invalid_manual_trade_is_rejected_without_writes(
    api: ApiContext, field: str, value: object
) -> None:
    request: dict[str, object] = {
        "symbol": "INFY",
        "quantity": 5,
        "price_paise": 1_000,
        "fees_paise": 0,
        "transacted_at_utc": NOW.isoformat(),
    }
    request[field] = value

    response = api.client.post("/api/v1/portfolio/buys", json=request)

    assert response.status_code == 422
    with api.session_factory() as session:
        assert _transaction_count(session) == 0


def test_withdrawal_exceeding_cash_is_rejected_without_writes(
    api: ApiContext,
) -> None:
    with api.session_factory() as session:
        LedgerService(session).record_deposit(1_000, NOW)

    response = api.client.post(
        "/api/v1/portfolio/withdrawals",
        json={"amount_paise": 1_001, "occurred_at_utc": NOW.isoformat()},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "withdrawal exceeds cash balance"
    with api.session_factory() as session:
        assert _row_count(session, CapitalMovement) == 1


def test_sell_exceeding_holdings_is_rejected_without_writes(
    api: ApiContext,
) -> None:
    with api.session_factory() as session:
        ledger = LedgerService(session)
        ledger.record_deposit(10_000, NOW)
        ledger.record_buy("INFY", 2, 1_000, NOW)

    response = api.client.post(
        "/api/v1/portfolio/sells",
        json={
            "symbol": "INFY",
            "quantity": 3,
            "price_paise": 1_500,
            "fees_paise": 0,
            "transacted_at_utc": NOW.isoformat(),
        },
    )

    assert response.status_code == 409
    with api.session_factory() as session:
        lot = session.scalar(select(HoldingLot))
        assert lot is not None
        assert lot.remaining_quantity == 2
        assert _transaction_count(session) == 1
        assert _row_count(session, LotRealization) == 0
        assert _row_count(session, ProfitVaultEntry) == 0


def test_idempotent_deposit_returns_original_without_duplicate(api: ApiContext) -> None:
    request = {"amount_paise": 10_000, "occurred_at_utc": NOW.isoformat()}
    headers = {"Idempotency-Key": "deposit-001"}

    first = api.client.post(
        "/api/v1/portfolio/deposits", json=request, headers=headers
    )
    repeated = api.client.post(
        "/api/v1/portfolio/deposits", json=request, headers=headers
    )

    assert first.status_code == repeated.status_code == 201
    assert repeated.json() == first.json()
    assert first.json()["idempotency_key"] == "deposit-001"
    with api.session_factory() as session:
        assert _row_count(session, CapitalMovement) == 1


def test_idempotent_sell_does_not_duplicate_realization_or_vault_entry(
    api: ApiContext,
) -> None:
    with api.session_factory() as session:
        ledger = LedgerService(session)
        ledger.record_deposit(10_000, NOW)
        ledger.record_buy("INFY", 2, 1_000, NOW)
    request = {
        "symbol": "INFY",
        "quantity": 1,
        "price_paise": 1_500,
        "fees_paise": 0,
        "transacted_at_utc": NOW.isoformat(),
    }
    headers = {"Idempotency-Key": "sell-001"}

    first = api.client.post("/api/v1/portfolio/sells", json=request, headers=headers)
    repeated = api.client.post(
        "/api/v1/portfolio/sells", json=request, headers=headers
    )

    assert first.status_code == repeated.status_code == 201
    assert repeated.json() == first.json()
    with api.session_factory() as session:
        assert _transaction_count(session) == 2
        assert _row_count(session, LotRealization) == 1
        assert _row_count(session, ProfitVaultEntry) == 1


def test_idempotency_key_reuse_with_different_payload_is_rejected(
    api: ApiContext,
) -> None:
    headers = {"Idempotency-Key": "deposit-001"}
    first = api.client.post(
        "/api/v1/portfolio/deposits",
        json={"amount_paise": 10_000, "occurred_at_utc": NOW.isoformat()},
        headers=headers,
    )
    conflict = api.client.post(
        "/api/v1/portfolio/deposits",
        json={"amount_paise": 20_000, "occurred_at_utc": NOW.isoformat()},
        headers=headers,
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    with api.session_factory() as session:
        assert _row_count(session, CapitalMovement) == 1


def test_validation_endpoint_approves_without_storing_or_executing(
    api: ApiContext,
) -> None:
    with api.session_factory() as session:
        LedgerService(session).record_deposit(100_000, NOW)
        before = _transaction_count(session)

    response = api.client.post(
        "/api/v1/risk/validate",
        json={
            "side": "buy",
            "symbol": "INFY",
            "quantity": 5,
            "price_paise": 1_000,
            "fees_paise": 0,
            "stop_loss_paise": 900,
            "proposed_at_utc": NOW.isoformat(),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"approved": True, "reasons": []}
    with api.session_factory() as session:
        assert _transaction_count(session) == before


def test_risk_rules_endpoint_returns_validated_configuration(
    api: ApiContext,
) -> None:
    response = api.client.get("/api/v1/risk/rules")

    assert response.status_code == 200
    payload = response.json()
    assert payload["daily_loss_timezone"] == "Asia/Kolkata"
    limits = {
        item["code"]: item["limit_percent"]
        for item in payload["rules"]
        if item["limit_percent"] is not None
    }
    assert limits == {
        "max_order_size": "10",
        "max_stock_allocation": "20",
        "max_loss_per_trade": "1",
        "max_daily_loss": "2",
    }


def test_validation_endpoint_returns_all_rejections_without_persistence(
    api: ApiContext,
) -> None:
    with api.session_factory() as session:
        LedgerService(session).record_deposit(5_000, NOW)

    response = api.client.post(
        "/api/v1/risk/validate",
        json={
            "side": "buy",
            "symbol": "INFY",
            "quantity": 20,
            "price_paise": 1_000,
            "proposed_at_utc": NOW.isoformat(),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["approved"] is False
    codes = {reason["code"] for reason in payload["reasons"]}
    assert {"insufficient_cash", "max_order_size", "stop_loss_required"} <= codes
    with api.session_factory() as session:
        assert _transaction_count(session) == 0


def _transaction_count(session: Session) -> int:
    value = session.scalar(select(func.count()).select_from(LedgerTransaction))
    assert value is not None
    return value


def _row_count(session: Session, model: type[object]) -> int:
    value = session.scalar(select(func.count()).select_from(model))
    assert value is not None
    return value
