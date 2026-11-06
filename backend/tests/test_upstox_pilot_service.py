"""Offline five-instrument pilot against synthetic, disposable NSE prices."""

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.db.models  # noqa: F401 - register every table
from app.db.base import Base
from app.db.models.prices import (
    DailyPrice,
    ImportBatch,
    IngestionRun,
    Instrument,
    InstrumentMapping,
    SourceObservation,
)
from app.services.integrity_service import _valid_isin, check_ledger_integrity
from app.services.market_data_service import MarketDataError
from app.services.upstox_historical_provider import (
    HistoricalBatch,
    UpstoxHistoricalDataProvider,
)
from app.services.upstox_pilot_service import (
    PilotError,
    ingest_batch,
    prepare_reference_subset,
    validate_temporary_target,
    verify_mock_metadata,
)

SYMBOLS = ("TESTA", "TESTB", "TESTC", "TESTD", "TESTE")
FIRST_DAY = date(2026, 10, 26)
RETRIEVED = datetime(2026, 11, 1, 12, tzinfo=UTC)


def _isin(index: int) -> str:
    prefix = f"IN{index:09d}"
    return next(
        prefix + str(check) for check in range(10) if _valid_isin(prefix + str(check))
    )


def _seed_reference(path: Path, days: int = 5) -> None:
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            instruments = [
                Instrument(symbol=symbol, series="EQ", isin=_isin(index))
                for index, symbol in enumerate(SYMBOLS, 1)
            ]
            session.add_all(instruments)
            session.flush()
            for instrument in instruments:
                session.add(
                    InstrumentMapping(
                        instrument_id=instrument.id,
                        provider="nse_zip",
                        provider_instrument_key=None,
                        symbol=instrument.symbol,
                        exchange="NSE",
                        series="EQ",
                        valid_from=None,
                        valid_to=None,
                        identity_status="legacy_unverified",
                    )
                )
            for offset in range(days):
                day = FIRST_DAY + timedelta(days=offset)
                batch = ImportBatch(
                    file_name=f"synthetic-{day:%Y%m%d}.zip",
                    file_sha256=f"{offset + 1:064x}",
                    source_label="SYNTHETIC NSE",
                    total_rows=5,
                    imported_rows=5,
                    skipped_rows=0,
                    rejected_rows=0,
                    status="completed",
                )
                session.add(batch)
                session.flush()
                run = IngestionRun(
                    provider="nse_zip",
                    source_kind="zip",
                    source_ref=batch.file_name,
                    source_sha256=batch.file_sha256,
                    import_batch_id=batch.id,
                    provenance_kind="source",
                )
                session.add(run)
                session.flush()
                for index, instrument in enumerate(instruments):
                    opened = 10_000 + index * 500 + offset * 100
                    observation = SourceObservation(
                        instrument_id=instrument.id,
                        ingestion_run_id=run.id,
                        provider="nse_zip",
                        provider_instrument_key=None,
                        source_record_key=f"synthetic:{instrument.symbol}:{day}",
                        payload_sha256=hashlib.sha256(
                            f"{instrument.symbol}:{day}".encode()
                        ).hexdigest(),
                        symbol=instrument.symbol,
                        exchange="NSE",
                        series="EQ",
                        trade_date=day,
                        open_paise=opened,
                        high_paise=opened + 500,
                        low_paise=opened - 500,
                        close_paise=opened + 100,
                        previous_close_paise=opened - 100,
                        volume=1_000 + index,
                        origin="source",
                        reconciliation="selected",
                        raw_payload_json='{"synthetic":"true"}',
                    )
                    session.add(observation)
                    session.flush()
                    session.add(
                        DailyPrice(
                            instrument_id=instrument.id,
                            import_batch_id=batch.id,
                            source_observation_id=observation.id,
                            trade_date=day,
                            open_paise=opened,
                            high_paise=opened + 500,
                            low_paise=opened - 500,
                            close_paise=opened + 100,
                            previous_close_paise=opened - 100,
                            volume=1_000 + index,
                            price_basis="unadjusted",
                        )
                    )
            session.commit()
    finally:
        engine.dispose()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE alembic_version "
            "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO alembic_version VALUES ('0004_market_data_foundation')"
        )


@pytest.fixture
def pilot(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    reference = tmp_path / "synthetic_reference.db"
    target = tmp_path / "synthetic_pilot.db"
    _seed_reference(reference)
    yield reference, target


def _metadata(symbol: str, index: int = 1) -> dict[str, object]:
    return {
        "instrument_key": f"NSE_EQ|{symbol}",
        "exchange": "NSE",
        "segment": "NSE_EQ",
        "instrument_type": "EQ",
        "trading_symbol": symbol,
        "isin": _isin(index),
        "source": "mock_instrument_master",
    }


def _rupees(paise: int) -> str:
    return f"{paise // 100}.{paise % 100:02d}"


def _batch(symbol: str, close_adjustment: int = 0) -> HistoricalBatch:
    candles = []
    for offset in range(5):
        opened = 10_000 + offset * 100
        candles.append(
            [
                f"{FIRST_DAY + timedelta(days=offset)}T00:00:00+05:30",
                _rupees(opened),
                _rupees(opened + 500),
                _rupees(opened - 500),
                _rupees(opened + 100 + (close_adjustment if offset == 0 else 0)),
                1000,
            ]
        )
    body = json.dumps({"status": "success", "data": {"candles": candles}}).encode()
    provider = UpstoxHistoricalDataProvider(token_getter=lambda: None)
    return provider.parse_response(
        body,
        f"NSE_EQ|{symbol}",
        FIRST_DAY,
        FIRST_DAY + timedelta(days=4),
        retrieved_at_utc=RETRIEVED,
        source_ref="offline-mock",
    )


def test_subset_preserves_only_selected_nse_facts_and_original_is_unchanged(
    pilot: tuple[Path, Path],
) -> None:
    source, target = pilot
    before = hashlib.sha256(source.read_bytes()).digest()
    reference = prepare_reference_subset(source, target, SYMBOLS)
    assert reference.dates == tuple(FIRST_DAY + timedelta(days=n) for n in range(5))
    assert reference.expected_nse_rows == 25
    assert len(reference.instruments) == 5
    assert check_ledger_integrity(target).is_valid
    with sqlite3.connect(target) as connection:
        for table, expected in (
            ("instruments", 5),
            ("daily_prices", 25),
            ("source_observations", 25),
            ("instrument_mappings", 5),
            ("import_batches", 5),
            ("ingestion_runs", 5),
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (
                expected,
            )
        assert connection.execute(
            "SELECT COUNT(*) FROM import_batches "
            "WHERE total_rows=5 AND imported_rows=5 AND skipped_rows=0"
        ).fetchone() == (5,)
    assert hashlib.sha256(source.read_bytes()).digest() == before


def test_subset_rejects_missing_common_history_and_unsafe_target(
    tmp_path: Path,
) -> None:
    source = tmp_path / "short.db"
    _seed_reference(source, days=4)
    target = tmp_path / "pilot.db"
    with pytest.raises(PilotError, match="fewer than five"):
        prepare_reference_subset(source, target, SYMBOLS)
    assert not target.exists()
    with pytest.raises(PilotError, match="temporary directory"):
        validate_temporary_target(
            Path("/Users/mayanksingh/Investment/investment-agent/data/pilot.db")
        )
    with pytest.raises(PilotError, match="must be new"):
        validate_temporary_target(source)


def test_metadata_requires_exact_identity_and_mapping_is_idempotent(
    pilot: tuple[Path, Path],
) -> None:
    source, target = pilot
    reference = prepare_reference_subset(source, target, SYMBOLS)
    engine = create_engine(f"sqlite:///{target}")
    try:
        with Session(engine) as session:
            bad = {**_metadata("TESTA"), "isin": _isin(2)}
            with pytest.raises(PilotError, match="failed verification"):
                verify_mock_metadata(session, bad, reference, "TESTA")
            first = verify_mock_metadata(
                session, _metadata("TESTA"), reference, "TESTA"
            )
            second = verify_mock_metadata(
                session, _metadata("TESTA"), reference, "TESTA"
            )
            assert first.id == second.id
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(InstrumentMapping)
                    .where(InstrumentMapping.provider == "upstox")
                )
                == 1
            )
    finally:
        engine.dispose()


def test_ingest_is_idempotent_revisioned_and_never_changes_canonical(
    pilot: tuple[Path, Path],
) -> None:
    source, target = pilot
    reference = prepare_reference_subset(source, target, SYMBOLS)
    engine = create_engine(f"sqlite:///{target}")
    try:
        with Session(engine) as session:
            verify_mock_metadata(session, _metadata("TESTA"), reference, "TESTA")
        with Session(engine) as session:
            assert ingest_batch(session, _batch("TESTA"), "TESTA", reference) == 5
        with Session(engine) as session:
            assert ingest_batch(session, _batch("TESTA"), "TESTA", reference) == 0
        with Session(engine) as session:
            assert ingest_batch(session, _batch("TESTA", 100), "TESTA", reference) == 1
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(DailyPrice)) == 25
            assert (
                session.scalar(select(func.count()).select_from(SourceObservation))
                == 31
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(IngestionRun)
                    .where(IngestionRun.provider == "upstox")
                )
                == 2
            )
            upstox = list(
                session.scalars(
                    select(SourceObservation)
                    .where(SourceObservation.provider == "upstox")
                    .order_by(SourceObservation.id)
                )
            )
            assert len(upstox) == 6
            assert all(item.previous_close_paise is None for item in upstox)
            assert all(item.event_at_utc is None for item in upstox)
            assert all(item.available_at_utc is None for item in upstox)
            assert all(item.retrieved_at_utc is not None for item in upstox)
            assert upstox[0].reconciliation == "agreement"
            assert upstox[-1].reconciliation == "conflict"
            assert upstox[0].source_record_key == upstox[-1].source_record_key
            assert upstox[0].close_paise != upstox[-1].close_paise
            assert (
                session.scalar(
                    select(DailyPrice.close_paise).where(
                        DailyPrice.instrument_id == reference.instrument_ids["TESTA"],
                        DailyPrice.trade_date == FIRST_DAY,
                    )
                )
                == 10_100
            )
        assert check_ledger_integrity(target).is_valid
    finally:
        engine.dispose()


def test_mapping_ambiguity_and_invalid_candle_leave_nothing_written(
    pilot: tuple[Path, Path],
) -> None:
    source, target = pilot
    reference = prepare_reference_subset(source, target, SYMBOLS)
    engine = create_engine(f"sqlite:///{target}")
    try:
        with Session(engine) as session:
            verify_mock_metadata(session, _metadata("TESTA"), reference, "TESTA")
        with Session(engine) as session:
            session.add(
                InstrumentMapping(
                    instrument_id=reference.instrument_ids["TESTA"],
                    provider="upstox",
                    provider_instrument_key="NSE_EQ|TESTA",
                    symbol="TESTA",
                    exchange="NSE",
                    series="EQ",
                    valid_from=reference.from_date,
                    valid_to=reference.to_date,
                    identity_status="verified",
                )
            )
            session.commit()
        with Session(engine) as session:
            with pytest.raises(MarketDataError, match="missing or ambiguous"):
                ingest_batch(session, _batch("TESTA"), "TESTA", reference)
        with Session(engine) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(SourceObservation)
                    .where(SourceObservation.provider == "upstox")
                )
                == 0
            )
    finally:
        engine.dispose()

    fresh_target = target.with_name("fresh_pilot.db")
    reference = prepare_reference_subset(source, fresh_target, SYMBOLS)
    engine = create_engine(f"sqlite:///{fresh_target}")
    try:
        with Session(engine) as session:
            verify_mock_metadata(session, _metadata("TESTA"), reference, "TESTA")
        batch = _batch("TESTA")
        broken = replace(batch.candles[1], close_price="999.00")
        invalid_batch = replace(batch, candles=(batch.candles[0], broken))
        with Session(engine) as session:
            with pytest.raises(MarketDataError, match="close price"):
                ingest_batch(session, invalid_batch, "TESTA", reference)
        with Session(engine) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(SourceObservation)
                    .where(SourceObservation.provider == "upstox")
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(IngestionRun)
                    .where(IngestionRun.provider == "upstox")
                )
                == 0
            )
    finally:
        engine.dispose()


def test_copied_phase_6a_triggers_reject_observation_mutation(
    pilot: tuple[Path, Path],
) -> None:
    source, target = pilot
    reference = prepare_reference_subset(source, target, SYMBOLS)
    engine = create_engine(f"sqlite:///{target}")
    try:
        with Session(engine) as session:
            verify_mock_metadata(session, _metadata("TESTA"), reference, "TESTA")
        with Session(engine) as session:
            assert ingest_batch(session, _batch("TESTA"), "TESTA", reference) == 5
    finally:
        engine.dispose()
    with sqlite3.connect(target) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE source_observations SET close_paise=1 WHERE provider='upstox'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM source_observations WHERE provider='upstox'"
            )
