"""Protect existing prices while adding provider-neutral observation provenance."""

import csv
import io
import sqlite3
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import Settings, get_settings
from app.db.models.prices import DailyPrice, SourceObservation
from app.services.integrity_service import check_ledger_integrity
from app.services.price_import_service import HEADERS, import_price_file

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def upgraded_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Config, Path]]:
    """Seed a disposable 0003 database, then migrate only that database."""
    database = tmp_path / "market_data_migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    try:
        command.upgrade(config, "0003_daily_prices")
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                "INSERT INTO instruments (id, symbol, series, isin) "
                "VALUES (11, 'TESTA', 'EQ', 'IN0000000003')"
            )
            connection.execute(
                """INSERT INTO import_batches
                   (id, file_name, file_sha256, source_label, total_rows,
                    imported_rows, skipped_rows, rejected_rows, status,
                    imported_at_utc)
                   VALUES (17, 'synthetic.zip', ?, 'synthetic', 1, 1, 0, 0,
                           'completed', '2026-10-31 01:00:00')""",
                ("a" * 64,),
            )
            connection.execute(
                """INSERT INTO daily_prices
                   (id, instrument_id, import_batch_id, trade_date,
                    open_paise, high_paise, low_paise, close_paise,
                    previous_close_paise, volume, price_basis)
                   VALUES (23, 11, 17, '2026-10-30',
                           10000, 10500, 9900, 10200, 9950, 500, 'unadjusted')"""
            )
        command.upgrade(config, "0004_market_data_foundation")
        yield config, database
    finally:
        get_settings.cache_clear()


def test_upgrade_backfills_without_changing_existing_prices(
    upgraded_database: tuple[Config, Path],
) -> None:
    _, database = upgraded_database
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "0004_market_data_foundation"
        )
        instrument = connection.execute("SELECT * FROM instruments").fetchone()
        assert instrument is not None
        assert (
            instrument["id"],
            instrument["symbol"],
            instrument["series"],
            instrument["isin"],
        ) == (11, "TESTA", "EQ", "IN0000000003")

        batch = connection.execute("SELECT * FROM import_batches").fetchone()
        assert batch is not None
        assert (
            batch["id"],
            batch["file_name"],
            batch["total_rows"],
            batch["imported_rows"],
            batch["skipped_rows"],
            batch["rejected_rows"],
        ) == (17, "synthetic.zip", 1, 1, 0, 0)

        price = connection.execute("SELECT * FROM daily_prices").fetchone()
        assert price is not None
        assert (
            price["id"],
            price["instrument_id"],
            price["import_batch_id"],
            price["trade_date"],
            price["open_paise"],
            price["high_paise"],
            price["low_paise"],
            price["close_paise"],
            price["previous_close_paise"],
            price["volume"],
            price["price_basis"],
        ) == (
            23,
            11,
            17,
            "2026-10-30",
            10000,
            10500,
            9900,
            10200,
            9950,
            500,
            "unadjusted",
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM instrument_mappings").fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0] == 1
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0]
            == 1
        )

        mapping = connection.execute("SELECT * FROM instrument_mappings").fetchone()
        assert mapping is not None
        assert (
            mapping["instrument_id"],
            mapping["provider"],
            mapping["symbol"],
            mapping["series"],
            mapping["identity_status"],
            mapping["valid_from"],
            mapping["valid_to"],
            mapping["provider_instrument_key"],
        ) == (
            11,
            "nse_zip",
            "TESTA",
            "EQ",
            "legacy_unverified",
            None,
            None,
            None,
        )

        run = connection.execute("SELECT * FROM ingestion_runs").fetchone()
        assert run is not None
        assert (
            run["import_batch_id"],
            run["source_kind"],
            run["source_ref"],
            run["source_sha256"],
            run["provenance_kind"],
        ) == (
            17,
            "zip",
            "synthetic.zip",
            "a" * 64,
            "reconstructed",
        )

        observation = connection.execute("SELECT * FROM source_observations").fetchone()
        assert observation is not None
        assert price["source_observation_id"] == observation["id"]
        assert (
            observation["instrument_id"],
            observation["ingestion_run_id"],
            observation["source_record_key"],
            observation["origin"],
            observation["reconciliation"],
            observation["raw_payload_json"],
            observation["event_at_utc"],
            observation["retrieved_at_utc"],
            observation["available_at_utc"],
        ) == (
            11,
            run["id"],
            "legacy-canonical:23",
            "reconstructed",
            "selected",
            None,
            None,
            None,
            None,
        )
        assert (
            observation["open_paise"],
            observation["high_paise"],
            observation["low_paise"],
            observation["close_paise"],
            observation["previous_close_paise"],
            observation["volume"],
        ) == (
            10000,
            10500,
            9900,
            10200,
            9950,
            500,
        )
    assert check_ledger_integrity(database).discrepancies == ()


def test_downgrade_preserves_backfilled_legacy_data(
    upgraded_database: tuple[Config, Path],
) -> None:
    config, database = upgraded_database
    command.downgrade(config, "0003_daily_prices")
    with sqlite3.connect(database) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == ("0003_daily_prices",)
        assert connection.execute(
            "SELECT id, symbol, series FROM instruments"
        ).fetchone() == (11, "TESTA", "EQ")
        assert connection.execute(
            "SELECT id, total_rows, imported_rows, skipped_rows, rejected_rows "
            "FROM import_batches"
        ).fetchone() == (17, 1, 1, 0, 0)
        assert connection.execute(
            "SELECT id, instrument_id, import_batch_id, open_paise, high_paise, "
            "low_paise, close_paise, previous_close_paise, volume "
            "FROM daily_prices"
        ).fetchone() == (23, 11, 17, 10000, 10500, 9900, 10200, 9950, 500)
        for table in ("instrument_mappings", "ingestion_runs", "source_observations"):
            assert connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone() == (0,)


def test_zip_import_after_legacy_migration(
    upgraded_database: tuple[Config, Path], tmp_path: Path
) -> None:
    """A migrated database still accepts the strict offline ZIP importer."""
    _, database = upgraded_database
    file_name = "BhavCopy_NSE_CM_0_0_0_20261029_F_0000.csv.zip"
    archive_path = tmp_path / file_name
    row = dict.fromkeys(HEADERS, "")
    row.update(
        {
            "TradDt": "2026-10-29",
            "BizDt": "2026-10-29",
            "Sgmt": "CM",
            "Src": "NSE",
            "FinInstrmTp": "STK",
            "ISIN": "IN0000000003",
            "TckrSymb": "TESTA",
            "SctySrs": "EQ",
            "OpnPric": "99.00",
            "HghPric": "101.00",
            "LwPric": "98.00",
            "ClsPric": "100.00",
            "PrvsClsgPric": "97.00",
            "TtlTradgVol": "1000",
        }
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=HEADERS)
    writer.writeheader()
    writer.writerow(row)
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(file_name.removesuffix(".zip"), output.getvalue())

    engine = create_engine(f"sqlite:///{database}")
    try:
        with Session(engine) as session:
            result = import_price_file(session, archive_path, "synthetic NSE")
            assert result.imported_rows == 1
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(DailyPrice)) == 2
            assert (
                session.scalar(select(func.count()).select_from(SourceObservation)) == 2
            )
            new_price = session.scalar(
                select(DailyPrice).where(DailyPrice.trade_date == date(2026, 10, 29))
            )
            assert new_price is not None
            assert new_price.close_paise == 10_000
            assert new_price.previous_close_paise == 9_700
            assert new_price.source_observation_id is not None
    finally:
        engine.dispose()
    assert check_ledger_integrity(database).discrepancies == ()


@pytest.mark.parametrize("unsafe_change", ["new_observation", "unknown_previous_close"])
def test_downgrade_refuses_data_loss(
    upgraded_database: tuple[Config, Path], unsafe_change: str
) -> None:
    config, database = upgraded_database
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        if unsafe_change == "new_observation":
            connection.execute(
                """INSERT INTO source_observations
                   (instrument_id, ingestion_run_id, provider, source_record_key,
                    payload_sha256, symbol, exchange, series, trade_date,
                    open_paise, high_paise, low_paise, close_paise, volume,
                    origin, reconciliation, raw_payload_json)
                   SELECT instrument_id, ingestion_run_id, provider,
                          'new-provider-observation', ?, symbol, exchange,
                          series, trade_date, open_paise, high_paise, low_paise,
                          close_paise, volume, 'source', 'unselected', '{}'
                   FROM source_observations
                   WHERE source_record_key='legacy-canonical:23'""",
                ("b" * 64,),
            )
        else:
            connection.execute(
                "UPDATE daily_prices SET previous_close_paise=NULL WHERE id=23"
            )

    with pytest.raises(RuntimeError, match="downgrade would discard"):
        command.downgrade(config, "0003_daily_prices")
    with sqlite3.connect(database) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        assert revision == ("0004_market_data_foundation",)
        if unsafe_change == "new_observation":
            assert connection.execute(
                "SELECT COUNT(*) FROM source_observations"
            ).fetchone() == (2,)
        else:
            assert connection.execute(
                "SELECT previous_close_paise FROM daily_prices WHERE id=23"
            ).fetchone() == (None,)
