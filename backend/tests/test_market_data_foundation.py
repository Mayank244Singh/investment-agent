"""Provider-neutral observations on disposable databases only."""

import sqlite3
from collections.abc import Generator
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.models.prices import (
    DailyPrice,
    ImportBatch,
    IngestionRun,
    Instrument,
    InstrumentMapping,
    SourceObservation,
)
from app.services.market_data_service import (
    MarketDataError,
    ObservationInput,
    add_verified_mapping,
    decimal_paise,
    normalize_observation,
    record_mapped_observation,
    record_observation,
    resolve_instrument,
)

TRADE_DATE = date(2026, 10, 30)
ISIN = "IN0000000001"
BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def engine(tmp_path: Path) -> Generator[Engine, None, None]:
    database_engine = create_engine(f"sqlite:///{tmp_path / 'observations.db'}")

    @event.listens_for(database_engine, "connect")
    def enable_foreign_keys(connection: object, _: object) -> None:
        cursor = connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(database_engine)
    yield database_engine
    database_engine.dispose()


def _seed_canonical(session: Session) -> tuple[Instrument, DailyPrice]:
    instrument = Instrument(symbol="TESTA", series="EQ", isin=ISIN)
    batch = ImportBatch(
        file_name="synthetic.zip",
        file_sha256="a" * 64,
        source_label="synthetic NSE",
        total_rows=1,
        imported_rows=1,
        skipped_rows=0,
        rejected_rows=0,
        status="completed",
    )
    session.add_all((instrument, batch))
    session.flush()
    price = DailyPrice(
        instrument_id=instrument.id,
        import_batch_id=batch.id,
        trade_date=TRADE_DATE,
        open_paise=10_000,
        high_paise=11_000,
        low_paise=9_000,
        close_paise=10_500,
        previous_close_paise=9_900,
        volume=1_000,
        price_basis="unadjusted",
    )
    session.add(price)
    session.flush()
    return instrument, price


def _run(session: Session, provider: str) -> IngestionRun:
    run = IngestionRun(
        provider=provider,
        source_kind="provider",
        source_ref="synthetic-only",
        source_sha256=None,
        provenance_kind="source",
    )
    session.add(run)
    session.flush()
    return run


def _input(
    provider: str,
    *,
    key: str | None = None,
    close: str = "105.00",
    previous_close: str | None = None,
) -> ObservationInput:
    return ObservationInput(
        provider=provider,
        source_record_key=key or f"{provider}:TESTA:{TRADE_DATE}",
        provider_instrument_key=f"{provider}|TESTA",
        symbol=" testa ",
        exchange="nse",
        series="EQ",
        trade_date=TRADE_DATE,
        open_price="100.00",
        high_price="110.00",
        low_price="90.00",
        close_price=close,
        volume=1_000,
        previous_close_price=previous_close,
    )


def _count(session: Session, model: type[SourceObservation]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_independent_identical_providers_keep_two_observations_one_canonical(
    engine: Engine,
) -> None:
    with Session(engine) as session:
        instrument, canonical = _seed_canonical(session)
        first, created_a = record_observation(
            session,
            ingestion_run=_run(session, "NSE"),
            value=normalize_observation(_input("NSE", previous_close="99.00")),
            raw_payload={"close": "105.00", "provider": "NSE"},
            instrument_id=instrument.id,
        )
        second, created_b = record_observation(
            session,
            ingestion_run=_run(session, "OTHER"),
            value=normalize_observation(_input("OTHER")),
            raw_payload={"close": "105.00", "provider": "OTHER"},
            instrument_id=instrument.id,
        )
        assert created_a and created_b
        assert first.id != second.id
        assert first.reconciliation == second.reconciliation == "agreement"
        assert first.previous_close_paise == 9_900
        assert second.previous_close_paise is None
        assert first.instrument_id == second.instrument_id == canonical.instrument_id
        assert _count(session, SourceObservation) == 2
        assert session.scalar(select(func.count()).select_from(DailyPrice)) == 1
        assert session.get(DailyPrice, canonical.id).close_paise == 10_500  # type: ignore[union-attr]


def test_same_source_delivery_is_idempotent_but_revision_is_append_only(
    engine: Engine,
) -> None:
    with Session(engine) as session:
        instrument, canonical = _seed_canonical(session)
        original = normalize_observation(_input("FAKE"))
        first, created = record_observation(
            session,
            ingestion_run=_run(session, "FAKE"),
            value=original,
            raw_payload={"close": "105.00"},
            instrument_id=instrument.id,
        )
        repeated, created_again = record_observation(
            session,
            ingestion_run=_run(session, "FAKE"),
            value=original,
            raw_payload={"close": "105.00"},
            instrument_id=instrument.id,
        )
        assert created and not created_again and repeated.id == first.id
        assert _count(session, SourceObservation) == 1

        revised = normalize_observation(_input("FAKE", close="106.00"))
        second, created_revision = record_observation(
            session,
            ingestion_run=_run(session, "FAKE"),
            value=revised,
            raw_payload={"close": "106.00"},
            instrument_id=instrument.id,
        )
        assert created_revision and second.id != first.id
        assert second.source_record_key == first.source_record_key
        assert second.payload_sha256 != first.payload_sha256
        assert second.reconciliation == "conflict"
        assert first.close_paise == 10_500
        assert second.close_paise == 10_600
        assert session.get(DailyPrice, canonical.id).close_paise == 10_500  # type: ignore[union-attr]
        assert _count(session, SourceObservation) == 2


def test_conflicting_provider_preserves_both_observations_and_canonical(
    engine: Engine,
) -> None:
    with Session(engine) as session:
        instrument, canonical = _seed_canonical(session)
        records = []
        for provider, close in (("A", "105.00"), ("B", "106.00")):
            record, _ = record_observation(
                session,
                ingestion_run=_run(session, provider),
                value=normalize_observation(_input(provider, close=close)),
                raw_payload={"close": close},
                instrument_id=instrument.id,
            )
            records.append(record)
        assert [item.reconciliation for item in records] == ["agreement", "conflict"]
        assert [item.close_paise for item in records] == [10_500, 10_600]
        assert session.get(DailyPrice, canonical.id).close_paise == 10_500  # type: ignore[union-attr]


def test_verified_temporal_mapping_resolves_only_within_dates(engine: Engine) -> None:
    with Session(engine) as session:
        instrument, _ = _seed_canonical(session)
        mapping = add_verified_mapping(
            session,
            instrument_id=instrument.id,
            provider="FAKE",
            provider_instrument_key="FAKE|TESTA",
            symbol=" testa ",
            exchange="nse",
            series="EQ",
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
        )
        assert mapping.instrument_id == instrument.id
        for day in (date(2026, 1, 1), date(2026, 12, 31)):
            assert (
                resolve_instrument(
                    session,
                    provider="FAKE",
                    provider_instrument_key="FAKE|TESTA",
                    symbol="TESTA",
                    exchange="NSE",
                    series="EQ",
                    trade_date=day,
                ).id
                == instrument.id
            )
        with pytest.raises(MarketDataError, match="missing or ambiguous"):
            resolve_instrument(
                session,
                provider="FAKE",
                provider_instrument_key="FAKE|TESTA",
                symbol="TESTA",
                exchange="NSE",
                series="EQ",
                trade_date=date(2027, 1, 1),
            )


def test_ticker_reuse_maps_to_distinct_stable_instruments_by_date(
    engine: Engine,
) -> None:
    with Session(engine) as session:
        original, _ = _seed_canonical(session)
        successor = Instrument(symbol="TESTB", series="EQ", isin="IN0000000002")
        session.add(successor)
        session.flush()
        for instrument, start, end in (
            (original, date(2025, 1, 1), date(2025, 12, 31)),
            (successor, date(2026, 1, 1), date(2026, 12, 31)),
        ):
            add_verified_mapping(
                session,
                instrument_id=instrument.id,
                provider="FAKE",
                provider_instrument_key="FAKE|ALIAS",
                symbol="ALIAS",
                exchange="NSE",
                series="EQ",
                valid_from=start,
                valid_to=end,
            )
        for day, expected_id in (
            (date(2025, 6, 1), original.id),
            (date(2026, 6, 1), successor.id),
        ):
            assert (
                resolve_instrument(
                    session,
                    provider="FAKE",
                    provider_instrument_key="FAKE|ALIAS",
                    symbol="ALIAS",
                    exchange="NSE",
                    series="EQ",
                    trade_date=day,
                ).id
                == expected_id
            )


def test_unknown_and_ambiguous_identity_mapping_fail_closed(engine: Engine) -> None:
    with Session(engine) as session:
        instrument, _ = _seed_canonical(session)
        value = _input("FAKE")
        with pytest.raises(MarketDataError, match="missing or ambiguous"):
            record_mapped_observation(
                session,
                ingestion_run=_run(session, "FAKE"),
                value=value,
                raw_payload={"close": "105.00"},
            )
        add_verified_mapping(
            session,
            instrument_id=instrument.id,
            provider="FAKE",
            provider_instrument_key="FAKE|TESTA",
            symbol="TESTA",
            exchange="NSE",
            series="EQ",
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
        )
        with pytest.raises(MarketDataError, match="ambiguous overlapping"):
            add_verified_mapping(
                session,
                instrument_id=instrument.id,
                provider="FAKE",
                provider_instrument_key="FAKE|TESTA",
                symbol="TESTA",
                exchange="NSE",
                series="EQ",
                valid_from=date(2026, 10, 1),
                valid_to=date(2027, 1, 1),
            )
        record, created = record_mapped_observation(
            session,
            ingestion_run=_run(session, "FAKE"),
            value=value,
            raw_payload={"close": "105.00"},
        )
        assert created and record.instrument_id == instrument.id
        # Directly inserted conflicting legacy data must also fail at lookup.
        session.add(
            InstrumentMapping(
                instrument_id=instrument.id,
                provider="FAKE",
                provider_instrument_key="FAKE|TESTA",
                symbol="TESTA",
                exchange="NSE",
                series="EQ",
                valid_from=date(2026, 1, 1),
                valid_to=date(2026, 12, 31),
                identity_status="verified",
            )
        )
        session.flush()
        with pytest.raises(MarketDataError, match="missing or ambiguous"):
            resolve_instrument(
                session,
                provider="FAKE",
                provider_instrument_key="FAKE|TESTA",
                symbol="TESTA",
                exchange="NSE",
                series="EQ",
                trade_date=TRADE_DATE,
            )


def test_decimal_exactness_optional_previous_close_and_unknown_times() -> None:
    normalized = normalize_observation(_input("FAKE", close="105.01"))
    assert normalized.close_paise == 10_501
    assert normalized.previous_close_paise is None
    assert normalized.event_at_utc is None
    assert normalized.retrieved_at_utc is None
    assert normalized.available_at_utc is None
    assert decimal_paise(Decimal("0.01"), "close") == 1
    with pytest.raises(MarketDataError, match="decimal string or Decimal"):
        normalize_observation(replace(_input("FAKE"), close_price=105.01))  # type: ignore[arg-type]
    with pytest.raises(MarketDataError, match="more than two decimal places"):
        normalize_observation(replace(_input("FAKE"), close_price="105.001"))


def test_unknown_source_times_remain_unknown_after_persistence(engine: Engine) -> None:
    with Session(engine) as session:
        instrument, _ = _seed_canonical(session)
        observation, _ = record_observation(
            session,
            ingestion_run=_run(session, "FAKE"),
            value=normalize_observation(_input("FAKE")),
            raw_payload={"close": "105.00"},
            instrument_id=instrument.id,
        )
        session.commit()
        session.refresh(observation)
        assert observation.event_at_utc is None
        assert observation.retrieved_at_utc is None
        assert observation.available_at_utc is None
        assert observation.ingested_at_utc is not None


def test_migrated_observations_reject_update_and_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "immutable.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    try:
        command.upgrade(config, "head")
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                "INSERT INTO instruments (symbol, series, isin) VALUES (?, ?, ?)",
                ("TESTA", "EQ", ISIN),
            )
            connection.execute(
                "INSERT INTO ingestion_runs "
                "(provider, source_kind, source_ref, provenance_kind) "
                "VALUES (?, ?, ?, ?)",
                ("FAKE", "provider", "synthetic", "source"),
            )
            connection.execute(
                """INSERT INTO source_observations
                   (instrument_id, ingestion_run_id, provider, source_record_key,
                    payload_sha256, symbol, exchange, series, trade_date,
                    open_paise, high_paise, low_paise, close_paise, volume,
                    origin, reconciliation)
                   VALUES (1, 1, 'FAKE', 'event-1', ?, 'TESTA', 'NSE', 'EQ',
                           '2026-10-30', 100, 110, 90, 105, 1000,
                           'source', 'unselected')""",
                ("a" * 64,),
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE source_observations SET close_paise = 106 WHERE id = 1"
                )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM source_observations WHERE id = 1")
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE ingestion_runs SET source_ref = 'changed' WHERE id = 1"
                )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM ingestion_runs WHERE id = 1")
            assert connection.execute(
                "SELECT close_paise FROM source_observations WHERE id = 1"
            ).fetchone() == (105,)
    finally:
        get_settings.cache_clear()
