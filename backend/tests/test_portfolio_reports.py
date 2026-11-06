"""Read-only report, CSV export, date, and replay API tests."""

import csv
import io
from collections.abc import Generator
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base
from app.db.models.ledger import LedgerTransaction
from app.db.session import get_db_session
from app.main import create_app
from app.modules.portfolio import LedgerService
from app.schemas import portfolio as portfolio_schemas
from app.services.portfolio_report_service import _safe_cell

TOKEN = "report-test-token-0123456789abcdef"
INDIA = ZoneInfo("Asia/Kolkata")
BUY_DAY = datetime(2026, 3, 31, 10, 0, tzinfo=INDIA)
SELL_DAY = datetime(2026, 4, 1, 10, 0, tzinfo=INDIA)


@pytest.fixture
def api(
    tmp_path: Path,
) -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    database = tmp_path / "reports.db"
    engine = create_engine(
        f"sqlite:///{database}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    def override_session() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    application = create_app(
        Settings.model_validate(
            {
                "auth_token": TOKEN,
                "auth_disabled": False,
                "backend_host": "127.0.0.1",
                "database_url": f"sqlite:///{database}",
            }
        )
    )
    application.dependency_overrides[get_db_session] = override_session
    try:
        with TestClient(application) as client:
            yield client, factory
    finally:
        engine.dispose()


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _seed(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        ledger = LedgerService(session)
        ledger.record_deposit(100_000, BUY_DAY)
        ledger.record_buy("TESTA", 2, 1_000, BUY_DAY, fees_paise=20)
        ledger.record_sell("TESTA", 1, 1_500, SELL_DAY, fees_paise=10)


def test_report_groups_fifo_pnl_and_holding_days(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    _seed(factory)
    response = client.get("/api/v1/portfolio/realized-pnl", headers=_auth())
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_realized_pnl_paise"] == 480
    assert payload["symbols"] == [
        {
            "symbol": "TESTA",
            "quantity_sold": 1,
            "cost_basis_paise": 1010,
            "gross_proceeds_paise": 1500,
            "sell_fees_paise": 10,
            "net_proceeds_paise": 1490,
            "realized_pnl_paise": 480,
            "shortest_holding_period_days": 1,
            "longest_holding_period_days": 1,
        }
    ]
    assert payload["matches"][0]["holding_period_days"] == 1


def test_report_uses_ist_fiscal_boundary_and_validates_filters(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    _seed(factory)
    base = "/api/v1/portfolio/realized-pnl"
    assert (
        client.get(base, params={"financial_year": 2025}, headers=_auth()).json()[
            "matches"
        ]
        == []
    )
    assert (
        len(
            client.get(base, params={"financial_year": 2026}, headers=_auth()).json()[
                "matches"
            ]
        )
        == 1
    )
    assert (
        len(
            client.get(
                base, params={"from": "2026-04-01", "to": "2026-04-01"}, headers=_auth()
            ).json()["matches"]
        )
        == 1
    )
    assert (
        client.get(base, params={"from": "2026-04-02"}, headers=_auth()).json()[
            "matches"
        ]
        == []
    )
    assert (
        client.get(
            base, params={"from": "2026-04-02", "to": "2026-04-01"}, headers=_auth()
        ).status_code
        == 422
    )
    assert (
        client.get(
            base, params={"financial_year": 2026, "from": "2026-04-01"}, headers=_auth()
        ).status_code
        == 422
    )


def test_report_and_exports_require_auth(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = api
    for path in ("realized-pnl", "ledger.csv", "fifo-matches.csv"):
        response = client.get(f"/api/v1/portfolio/{path}")
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


def test_ledger_csv_is_one_row_per_event_safe_and_not_cached(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    _seed(factory)
    with factory() as session:
        LedgerService(session).record_withdrawal(
            100, SELL_DAY + timedelta(hours=1), '=SUM(1,2) "hi"\nnext'
        )
    response = client.get("/api/v1/portfolio/ledger.csv", headers=_auth())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "attachment" in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [row["event_type"] for row in rows] == [
        "deposit",
        "buy",
        "sell",
        "withdrawal",
    ]
    assert rows[2]["realized_pnl_paise"] == "480"
    assert rows[2]["profit_vault_credit_paise"] == "480"
    assert rows[3]["note"] == '\'=SUM(1,2) "hi"\nnext'
    assert response.text.count('"\'=SUM(1,2) ""hi""\nnext"') == 1


def test_negative_pnl_csv_cell_is_neutralized(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    with factory() as session:
        ledger = LedgerService(session)
        ledger.record_buy("LOSS", 1, 1000, BUY_DAY)
        ledger.record_sell("LOSS", 1, 900, SELL_DAY)
    rows = list(
        csv.DictReader(
            io.StringIO(
                client.get("/api/v1/portfolio/ledger.csv", headers=_auth()).text
            )
        )
    )
    assert rows[1]["realized_pnl_paise"] == "'-100"


@pytest.mark.parametrize("value", ["=1+1", "+cmd", "-100", "@SUM(A1)"])
def test_csv_formula_prefixes_are_neutralized(value: str) -> None:
    assert _safe_cell(value) == "'" + value
    assert _safe_cell("  " + value) == "'  " + value


def test_fifo_export_has_each_match_and_holding_period(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    with factory() as session:
        ledger = LedgerService(session)
        ledger.record_buy("TESTB", 1, 1000, BUY_DAY)
        ledger.record_buy("TESTB", 1, 1200, BUY_DAY + timedelta(hours=1))
        ledger.record_sell("TESTB", 2, 1500, SELL_DAY, fees_paise=20)
    response = client.get("/api/v1/portfolio/fifo-matches.csv", headers=_auth())
    assert response.headers["cache-control"] == "no-store"
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 2
    assert [int(row["cost_basis_paise"]) for row in rows] == [1000, 1200]
    assert [row["holding_period_days"] for row in rows] == ["1", "1"]
    assert [row["sell_fees_paise"] for row in rows] == ["10", "10"]
    report = client.get("/api/v1/portfolio/realized-pnl", headers=_auth()).json()
    assert len(report["matches"]) == 2
    assert report["symbols"][0]["quantity_sold"] == 2
    assert report["symbols"][0]["cost_basis_paise"] == 2200
    assert report["symbols"][0]["realized_pnl_paise"] == 780


def test_same_trade_key_with_different_payload_is_rejected_without_write(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    request = {
        "symbol": "TESTA",
        "quantity": 1,
        "price_paise": 1000,
        "fees_paise": 0,
        "transacted_at_utc": BUY_DAY.isoformat(),
    }
    headers = {**_auth(), "Idempotency-Key": "same-trade"}
    first = client.post("/api/v1/portfolio/buys", json=request, headers=headers)
    replay = client.post("/api/v1/portfolio/buys", json=request, headers=headers)
    conflict = client.post(
        "/api/v1/portfolio/buys",
        json={**request, "quantity": 2},
        headers=headers,
    )
    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(LedgerTransaction)) == 1


def test_entry_dates_use_ist_calendar_and_allow_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            fixed = datetime(2026, 11, 3, 0, 5, tzinfo=INDIA)
            return cls.fromtimestamp(fixed.timestamp(), tz)

    monkeypatch.setattr(portfolio_schemas, "datetime", FixedDatetime)
    previous = datetime(2026, 11, 2, 23, 59, tzinfo=INDIA)
    today = datetime(2026, 11, 3, 23, 59, tzinfo=INDIA)
    future = datetime(2026, 11, 4, 0, 0, tzinfo=INDIA)
    for value in (previous, today):
        assert (
            portfolio_schemas.CapitalMovementCreate(
                amount_paise=1, occurred_at_utc=value
            ).occurred_at_utc
            == value
        )
    with pytest.raises(ValueError, match="future in Asia/Kolkata"):
        portfolio_schemas.CapitalMovementCreate(amount_paise=1, occurred_at_utc=future)
