"""Read-only valuation and coverage tests on disposable SQLite databases."""

from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base
from app.db.models.prices import DailyPrice, ImportBatch, Instrument
from app.db.session import get_db_session
from app.main import create_app
from app.modules.portfolio import LedgerService

TOKEN = "valuation-test-token-0123456789abcdef"
TRADE_DATE = date(2026, 10, 30)
OCCURRED_AT = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
VALUATION_ROUTE = "/api/v1/portfolio/valuation"
COVERAGE_ROUTE = "/api/v1/prices/coverage"


@dataclass(frozen=True)
class ApiContext:
    client: TestClient
    session_factory: sessionmaker[Session]


@pytest.fixture
def api(tmp_path: Path) -> Generator[ApiContext, None, None]:
    """Provide an authenticated app backed only by a temporary database."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'valuation.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    application = create_app(
        Settings.model_validate(
            {
                "auth_token": TOKEN,
                "auth_disabled": False,
                "backend_host": "127.0.0.1",
                "price_staleness_days": 5,
            }
        )
    )

    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    application.dependency_overrides[get_db_session] = override_session
    try:
        with TestClient(application) as client:
            yield ApiContext(client, session_factory)
    finally:
        application.dependency_overrides.clear()
        engine.dispose()


def _get(api: ApiContext, route: str, *, as_of: date | None = None) -> dict[str, Any]:
    parameters = {"as_of": as_of.isoformat()} if as_of is not None else None
    response = api.client.get(
        route,
        params=parameters,
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _holding(api: ApiContext, symbol: str, quantity: int = 3) -> None:
    with api.session_factory() as session:
        ledger = LedgerService(session)
        ledger.record_deposit(1_000_000, OCCURRED_AT)
        ledger.record_buy(symbol, quantity, 10_000, OCCURRED_AT, fees_paise=30)


def _price(
    api: ApiContext,
    symbol: str,
    trade_date: date,
    close_paise: int = 12_000,
) -> None:
    """Insert an invented EQ close with matching synthetic provenance."""
    with api.session_factory() as session:
        instrument = session.scalar(
            select(Instrument).where(
                Instrument.symbol == symbol, Instrument.series == "EQ"
            )
        )
        if instrument is None:
            instrument = Instrument(symbol=symbol, series="EQ", isin=None)
            session.add(instrument)
            session.flush()
        batch = ImportBatch(
            file_name=f"synthetic-{symbol}-{trade_date.isoformat()}.zip",
            file_sha256=f"{symbol}{trade_date.isoformat()}".encode()
            .hex()
            .ljust(64, "0"),
            source_label="synthetic test source",
            total_rows=1,
            imported_rows=1,
            skipped_rows=0,
            rejected_rows=0,
            status="completed",
        )
        session.add(batch)
        session.flush()
        session.add(
            DailyPrice(
                instrument_id=instrument.id,
                import_batch_id=batch.id,
                trade_date=trade_date,
                open_paise=10_000,
                high_paise=max(12_000, close_paise),
                low_paise=9_000,
                close_paise=close_paise,
                previous_close_paise=10_000,
                volume=100,
                price_basis="unadjusted",
            )
        )
        session.commit()


def test_latest_eq_close_is_dated_normalized_and_requires_auth(api: ApiContext) -> None:
    route = "/api/v1/prices/latest"
    assert api.client.get(route, params={"symbol": "TESTA"}).status_code == 401
    assert _get(api, route + "?symbol=unknown") == {
        "symbol": "UNKNOWN",
        "status": "no_eq_price",
        "price_date": None,
        "close_paise": None,
        "source_label": None,
        "file_name": None,
        "staleness_limit_days": 5,
    }
    _price(api, "TESTA", TRADE_DATE, 12_000)
    _price(api, "TESTA", TRADE_DATE + timedelta(days=1), 11_000)
    result = _get(api, route + "?symbol=%20testa%20")
    assert result["symbol"] == "TESTA"
    assert result["status"] == "available"
    assert result["price_date"] == "2026-10-31"
    assert result["close_paise"] == 11_000
    assert result["source_label"] == "synthetic test source"
    assert result["staleness_limit_days"] == 5
    invalid = api.client.get(
        route,
        params={"symbol": "bad symbol"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert invalid.status_code == 422


def test_priced_holding_uses_latest_close_and_open_fifo_cost(api: ApiContext) -> None:
    _holding(api, "TESTA")
    _price(api, "TESTA", TRADE_DATE)

    payload = _get(api, VALUATION_ROUTE, as_of=TRADE_DATE)

    assert payload["as_of_date"] == "2026-10-30"
    assert payload["staleness_limit_days"] == 5
    assert payload["holdings"] == [
        {
            "symbol": "TESTA",
            "quantity": 3,
            "cost_basis_paise": 30_030,
            "status": "priced",
            "unavailable_reason": None,
            "last_price_date": "2026-10-30",
            "price_age_days": 0,
            "close_paise": 12_000,
            "market_value_paise": 36_000,
            "unrealized_pnl_paise": 5_970,
            "source_label": "synthetic test source",
            "file_name": "synthetic-TESTA-2026-10-30.zip",
        }
    ]
    assert payload["totals"] == {
        "priced_holdings_count": 1,
        "unpriced_holdings_count": 0,
        "priced_cost_basis_paise": 30_030,
        "priced_market_value_paise": 36_000,
        "priced_unrealized_pnl_paise": 5_970,
        "unpriced_cost_basis_paise": 0,
        "total_open_cost_basis_paise": 30_030,
    }


def test_missing_eq_price_is_unavailable(api: ApiContext) -> None:
    _holding(api, "TESTA")
    payload = _get(api, VALUATION_ROUTE, as_of=TRADE_DATE)
    holding = payload["holdings"][0]
    assert holding["status"] == "unavailable"
    assert holding["unavailable_reason"] == "no_eq_price"
    assert holding["last_price_date"] is None
    assert holding["market_value_paise"] is None
    assert payload["totals"]["unpriced_cost_basis_paise"] == 30_030


def test_instrument_without_any_eq_price_is_unavailable(api: ApiContext) -> None:
    _holding(api, "TESTA")
    with api.session_factory() as session:
        session.add(Instrument(symbol="TESTA", series="EQ"))
        session.commit()
    payload = _get(api, VALUATION_ROUTE, as_of=TRADE_DATE)
    assert payload["holdings"][0]["unavailable_reason"] == "no_eq_price"


@pytest.mark.parametrize(
    ("days_after", "status", "reason"),
    [(5, "priced", None), (6, "unavailable", "stale_price")],
)
def test_staleness_boundary_is_inclusive_for_five_calendar_days(
    api: ApiContext, days_after: int, status: str, reason: str | None
) -> None:
    _holding(api, "TESTA")
    _price(api, "TESTA", TRADE_DATE)
    payload = _get(api, VALUATION_ROUTE, as_of=TRADE_DATE + timedelta(days=days_after))
    holding = payload["holdings"][0]
    assert holding["status"] == status
    assert holding["unavailable_reason"] == reason
    assert holding["last_price_date"] == "2026-10-30"
    assert holding["price_age_days"] == days_after
    if days_after == 6:
        assert holding["close_paise"] is None
        assert holding["market_value_paise"] is None
        assert holding["unrealized_pnl_paise"] is None


def test_as_of_before_latest_price_uses_only_earlier_close(api: ApiContext) -> None:
    _holding(api, "TESTA")
    _price(api, "TESTA", TRADE_DATE - timedelta(days=1), 11_000)
    _price(api, "TESTA", TRADE_DATE, 12_000)

    payload = _get(api, VALUATION_ROUTE, as_of=TRADE_DATE - timedelta(days=1))
    holding = payload["holdings"][0]
    assert holding["last_price_date"] == "2026-10-29"
    assert holding["close_paise"] == 11_000
    assert holding["market_value_paise"] == 33_000


def test_as_of_before_first_price_is_unavailable(api: ApiContext) -> None:
    _holding(api, "TESTA")
    _price(api, "TESTA", TRADE_DATE)
    payload = _get(api, VALUATION_ROUTE, as_of=TRADE_DATE - timedelta(days=1))
    holding = payload["holdings"][0]
    assert holding["unavailable_reason"] == "no_price_on_or_before_as_of"
    assert holding["last_price_date"] is None


def test_partially_priced_portfolio_keeps_unpriced_cost_separate(
    api: ApiContext,
) -> None:
    _holding(api, "TESTA")
    _holding(api, "TESTB", quantity=2)
    _price(api, "TESTA", TRADE_DATE)

    payload = _get(api, VALUATION_ROUTE, as_of=TRADE_DATE)

    assert [item["status"] for item in payload["holdings"]] == ["priced", "unavailable"]
    assert payload["totals"] == {
        "priced_holdings_count": 1,
        "unpriced_holdings_count": 1,
        "priced_cost_basis_paise": 30_030,
        "priced_market_value_paise": 36_000,
        "priced_unrealized_pnl_paise": 5_970,
        "unpriced_cost_basis_paise": 20_030,
        "total_open_cost_basis_paise": 50_060,
    }


def test_empty_portfolio_totals_are_explicit(api: ApiContext) -> None:
    payload = _get(api, VALUATION_ROUTE, as_of=TRADE_DATE)
    assert payload["holdings"] == []
    assert payload["totals"]["priced_holdings_count"] == 0
    assert payload["totals"]["unpriced_holdings_count"] == 0


def test_default_as_of_date_uses_asia_kolkata_today(api: ApiContext) -> None:
    before = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    payload = _get(api, VALUATION_ROUTE)
    after = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    assert payload["as_of_date"] in {before.isoformat(), after.isoformat()}


def test_price_coverage_lists_latest_eq_date_and_import_provenance(
    api: ApiContext,
) -> None:
    _price(api, "TESTA", TRADE_DATE - timedelta(days=1), 11_000)
    _price(api, "TESTA", TRADE_DATE, 12_000)
    _price(api, "TESTB", TRADE_DATE)

    payload = _get(api, COVERAGE_ROUTE)

    assert payload["symbols"] == [
        {"symbol": "TESTA", "series": "EQ", "latest_price_date": "2026-10-30"},
        {"symbol": "TESTB", "series": "EQ", "latest_price_date": "2026-10-30"},
    ]
    assert len(payload["import_batches"]) == 3
    batch = payload["import_batches"][0]
    assert batch["source_label"] == "synthetic test source"
    assert batch["file_name"].startswith("synthetic-")
    assert (batch["total_rows"], batch["imported_rows"], batch["skipped_rows"]) == (
        1,
        1,
        0,
    )
    assert datetime.fromisoformat(batch["imported_at_utc"]).utcoffset() == (
        timedelta(0)
    )


@pytest.mark.parametrize("route", [VALUATION_ROUTE, COVERAGE_ROUTE])
def test_price_read_routes_require_authentication(api: ApiContext, route: str) -> None:
    missing = api.client.get(route)
    wrong = api.client.get(route, headers={"Authorization": "Bearer wrong"})
    for response in (missing, wrong):
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


def test_price_staleness_setting_must_be_positive() -> None:
    with pytest.raises(ValidationError, match="price_staleness_days"):
        Settings.model_validate({"price_staleness_days": 0})


def test_valuation_does_not_change_cost_based_risk_capital(api: ApiContext) -> None:
    _holding(api, "TESTA")
    ledger_before = _get(api, "/api/v1/portfolio/ledger")
    _price(api, "TESTA", TRADE_DATE)
    _get(api, VALUATION_ROUTE, as_of=TRADE_DATE)
    ledger_after = _get(api, "/api/v1/portfolio/ledger")
    assert (
        ledger_before["summary"]["risk_capital_paise"]
        == (ledger_after["summary"]["risk_capital_paise"])
    )
