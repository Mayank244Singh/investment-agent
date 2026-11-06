"""Offline reconciliation against synthetic NSE and Upstox daily prices."""

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.prices import DailyPrice, ImportBatch, IngestionRun, Instrument
from app.services.market_data_service import (
    ObservationInput,
    normalize_observation,
    record_observation,
)
from app.services.upstox_reconciliation_service import reconcile_prices

DAY = date(2026, 10, 30)


@pytest.fixture
def engine(tmp_path: Path) -> Generator[Engine, None, None]:
    temporary_engine = create_engine(f"sqlite:///{tmp_path / 'reconciliation.db'}")
    Base.metadata.create_all(temporary_engine)
    yield temporary_engine
    temporary_engine.dispose()


def _instrument(session: Session, symbol: str = "TESTA") -> Instrument:
    instrument = Instrument(symbol=symbol, series="EQ", isin="IN0000000003")
    session.add(instrument)
    session.flush()
    return instrument


def _nse(
    session: Session,
    instrument: Instrument,
    day: date = DAY,
    *,
    close_paise: int = 10_500,
    volume: int = 1_000,
) -> DailyPrice:
    batch = ImportBatch(
        file_name=f"mock-nse-{instrument.symbol}-{day.isoformat()}.zip",
        file_sha256=f"{instrument.id:032x}{day.toordinal():032x}",
        source_label="synthetic NSE",
        total_rows=1,
        imported_rows=1,
        skipped_rows=0,
        rejected_rows=0,
        status="completed",
    )
    session.add(batch)
    session.flush()
    price = DailyPrice(
        instrument_id=instrument.id,
        import_batch_id=batch.id,
        trade_date=day,
        open_paise=10_000,
        high_paise=11_000,
        low_paise=9_000,
        close_paise=close_paise,
        previous_close_paise=9_900,
        volume=volume,
        price_basis="unadjusted",
    )
    session.add(price)
    session.flush()
    return price


def _upstox(
    session: Session,
    instrument: Instrument,
    day: date = DAY,
    *,
    close: str = "105.00",
    volume: int = 1_000,
    record_key: str | None = None,
) -> None:
    run = IngestionRun(
        provider="upstox",
        source_kind="provider",
        source_ref="mock historical response",
        provenance_kind="source",
    )
    session.add(run)
    session.flush()
    value = normalize_observation(
        ObservationInput(
            provider="upstox",
            source_record_key=record_key or f"NSE_EQ|TESTA:{day.isoformat()}",
            provider_instrument_key=f"NSE_EQ|{instrument.symbol}",
            symbol=instrument.symbol,
            exchange="NSE",
            series="EQ",
            trade_date=day,
            open_price="100.00",
            high_price="110.00",
            low_price="90.00",
            close_price=close,
            volume=volume,
            previous_close_price=None,
            event_at_utc=datetime(2026, 10, 30, 10, 0, tzinfo=UTC),
        )
    )
    record_observation(
        session,
        ingestion_run=run,
        value=value,
        raw_payload={"close": close, "volume": str(volume)},
        instrument_id=instrument.id,
    )


def test_exact_match_keeps_nse_canonical_and_reports_provenance(engine: Engine) -> None:
    with Session(engine) as session:
        instrument = _instrument(session)
        canonical = _nse(session, instrument)
        _upstox(session, instrument)
        before = (canonical.id, canonical.close_paise, canonical.volume)
        report = reconcile_prices(session, {instrument.symbol: instrument.id}, (DAY,))
        row = report.symbols[0].rows[0]
        assert row.state == "EXACT_MATCH"
        assert row.differences == ()
        assert row.nse_import_batch_id == canonical.import_batch_id
        assert row.nse_previous_close_present is True
        assert row.upstox_previous_close_present is False
        assert row.upstox_event_at_utc == datetime(2026, 10, 30, 10, tzinfo=UTC)
        assert row.upstox_instrument_keys == ("NSE_EQ|TESTA",)
        assert report.aggregate.nse_rows == report.aggregate.upstox_rows == 1
        assert report.aggregate.exact_matches == 1
        assert report.aggregate.close_matches == 1
        assert report.aggregate.exact_ohlc_matches == 1
        assert session.get(DailyPrice, canonical.id) is not None
        assert (canonical.id, canonical.close_paise, canonical.volume) == before
        session.commit()
        assert session.scalar(select(DailyPrice.close_paise)) == 10_500


def test_volume_only_difference_has_exact_price_match(engine: Engine) -> None:
    with Session(engine) as session:
        instrument = _instrument(session)
        _nse(session, instrument)
        _upstox(session, instrument, volume=1_100)
        row = (
            reconcile_prices(session, {"TESTA": instrument.id}, (DAY,))
            .symbols[0]
            .rows[0]
        )
        assert row.state == "PRICE_MATCH_VOLUME_DIFF"
        assert len(row.differences) == 1
        difference = row.differences[0]
        assert (difference.field, difference.nse_value, difference.upstox_value) == (
            "volume",
            1_000,
            1_100,
        )
        assert difference.absolute_difference == 100
        assert difference.percentage_difference == Decimal(10)


def test_price_difference_shows_integer_paise_and_percentage(engine: Engine) -> None:
    with Session(engine) as session:
        instrument = _instrument(session)
        canonical = _nse(session, instrument)
        _upstox(session, instrument, close="106.00")
        report = reconcile_prices(session, {"TESTA": instrument.id}, (DAY,))
        row = report.symbols[0].rows[0]
        assert row.state == "PRICE_DIFF"
        assert len(row.differences) == 1
        difference = row.differences[0]
        assert (difference.field, difference.nse_value, difference.upstox_value) == (
            "close_paise",
            10_500,
            10_600,
        )
        assert difference.difference == difference.absolute_difference == 100
        assert difference.percentage_difference == Decimal(10000) / Decimal(10500)
        assert report.aggregate.price_disagreements == 1
        assert report.aggregate.close_matches == 0
        assert canonical.close_paise == 10_500


def test_missing_sessions_are_reported_separately(engine: Engine) -> None:
    next_day = DAY + timedelta(days=1)
    with Session(engine) as session:
        instrument = _instrument(session)
        _nse(session, instrument, DAY)
        _upstox(session, instrument, next_day)
        report = reconcile_prices(session, {"TESTA": instrument.id}, (DAY, next_day))
        assert [row.state for row in report.symbols[0].rows] == [
            "MISSING_UPSTOX",
            "MISSING_NSE",
        ]
        assert report.aggregate.nse_rows == report.aggregate.upstox_rows == 1
        assert report.aggregate.missing_nse == report.aggregate.missing_upstox == 1


def test_extra_upstox_session_inside_reference_range_is_reported(
    engine: Engine,
) -> None:
    final_day = DAY + timedelta(days=2)
    extra_day = DAY + timedelta(days=1)
    with Session(engine) as session:
        instrument = _instrument(session)
        _nse(session, instrument, DAY)
        _nse(session, instrument, final_day)
        _upstox(session, instrument, extra_day)
        report = reconcile_prices(
            session, {"TESTA": instrument.id}, (DAY, final_day)
        )
        assert report.dates == (DAY, final_day)
        assert [(row.trade_date, row.state) for row in report.symbols[0].rows] == [
            (DAY, "MISSING_UPSTOX"),
            (extra_day, "MISSING_NSE"),
            (final_day, "MISSING_UPSTOX"),
        ]
        assert report.aggregate.missing_nse == 1
        assert report.aggregate.missing_upstox == 2


def test_changed_payload_is_an_explicit_ambiguous_revision(engine: Engine) -> None:
    with Session(engine) as session:
        instrument = _instrument(session)
        canonical = _nse(session, instrument)
        _upstox(session, instrument, close="105.00")
        _upstox(session, instrument, close="106.00")
        report = reconcile_prices(session, {"TESTA": instrument.id}, (DAY,))
        row = report.symbols[0].rows[0]
        assert row.state == "REVISION_AMBIGUOUS"
        assert len(row.upstox_observation_ids) == 2
        assert row.differences == ()
        assert report.aggregate.upstox_rows == 2
        assert report.aggregate.revision_ambiguous == 1
        assert report.aggregate.price_disagreements == 0
        assert canonical.close_paise == 10_500


def test_mismatched_instrument_identity_rejected(engine: Engine) -> None:
    with Session(engine) as session:
        instrument = _instrument(session)
        _nse(session, instrument)
        with pytest.raises(ValueError, match="identity is missing or mismatched"):
            reconcile_prices(session, {"WRONG": instrument.id}, (DAY,))
