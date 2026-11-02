"""Read-only price integrity checks against deliberately corrupted temp DBs."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine

from app.cli import main
from app.db.base import Base
from app.services.integrity_service import check_ledger_integrity


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "price-integrity.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO instruments (id, symbol, series, isin) "
            "VALUES (1, 'TESTA', 'EQ', 'IN0000000003')"
        )
        connection.execute(
            """INSERT INTO import_batches
               (id, file_name, file_sha256, source_label, total_rows,
                imported_rows, skipped_rows, rejected_rows, status)
               VALUES (1, 'synthetic.zip', ?, 'synthetic', 1, 1, 0, 0, 'completed')""",
            ("a" * 64,),
        )
        connection.execute(
            """INSERT INTO daily_prices
               (id, instrument_id, import_batch_id, trade_date, open_paise,
                high_paise, low_paise, close_paise, previous_close_paise, volume)
               VALUES (1, 1, 1, '2026-10-30', 10000, 12000, 9000, 11000, 10000, 100)"""
        )
    return path


def _corrupt(path: Path, statement: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(statement)


def test_clean_price_database_passes_without_writing(tmp_path: Path) -> None:
    path = _database(tmp_path)
    before = path.read_bytes()
    result = check_ledger_integrity(path)
    assert result.is_valid
    assert path.read_bytes() == before


def test_orphan_daily_price_instrument_is_reported(tmp_path: Path) -> None:
    path = _database(tmp_path)
    _corrupt(path, "UPDATE daily_prices SET instrument_id = 999")
    result = check_ledger_integrity(path)
    assert any(
        "Daily price 1 has no valid instrument" in item for item in result.discrepancies
    )


@pytest.mark.parametrize(
    "column",
    ["open_paise", "high_paise", "low_paise", "close_paise", "previous_close_paise"],
)
def test_nonpositive_price_columns_are_reported(tmp_path: Path, column: str) -> None:
    path = _database(tmp_path)
    _corrupt(path, f"UPDATE daily_prices SET {column} = 0")
    result = check_ledger_integrity(path)
    assert any(
        column in item and "non-positive" in item for item in result.discrepancies
    )


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        ("UPDATE daily_prices SET low_paise = 13000", "low greater than high"),
        ("UPDATE daily_prices SET open_paise = 8000", "open outside low-high"),
        ("UPDATE daily_prices SET close_paise = 13000", "close outside low-high"),
    ],
)
def test_invalid_ohlc_range_is_reported(
    tmp_path: Path, statement: str, message: str
) -> None:
    path = _database(tmp_path)
    _corrupt(path, statement)
    result = check_ledger_integrity(path)
    assert any(message in item for item in result.discrepancies)


def test_negative_volume_is_reported(tmp_path: Path) -> None:
    path = _database(tmp_path)
    _corrupt(path, "UPDATE daily_prices SET volume = -1")
    result = check_ledger_integrity(path)
    assert any("negative or invalid volume" in item for item in result.discrepancies)


def test_duplicate_instrument_date_is_reported(tmp_path: Path) -> None:
    path = _database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE duplicate_prices AS SELECT * FROM daily_prices"
        )
        connection.execute("DROP TABLE daily_prices")
        connection.execute("ALTER TABLE duplicate_prices RENAME TO daily_prices")
        connection.execute(
            "INSERT INTO daily_prices SELECT 2, instrument_id, import_batch_id, "
            "trade_date, open_paise, high_paise, low_paise, close_paise, "
            "previous_close_paise, volume, price_basis FROM daily_prices WHERE id = 1"
        )
    result = check_ledger_integrity(path)
    assert any("Duplicate daily prices" in item for item in result.discrepancies)


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        ("UPDATE import_batches SET imported_rows = 2", "linked daily prices"),
        ("UPDATE import_batches SET total_rows = 2", "row counts do not reconcile"),
    ],
)
def test_import_batch_count_mismatch_is_reported(
    tmp_path: Path, statement: str, message: str
) -> None:
    path = _database(tmp_path)
    _corrupt(path, statement)
    result = check_ledger_integrity(path)
    assert any(message in item for item in result.discrepancies)


def test_future_dated_price_is_reported(tmp_path: Path) -> None:
    path = _database(tmp_path)
    future = datetime.now(ZoneInfo("Asia/Kolkata")).date() + timedelta(days=1)
    _corrupt(path, f"UPDATE daily_prices SET trade_date = '{future.isoformat()}'")
    result = check_ledger_integrity(path)
    assert any("dated in the future" in item for item in result.discrepancies)


@pytest.mark.parametrize(
    ("isin", "message"),
    [("NULL", "missing ISIN"), ("'IN0000000001'", "inconsistent ISIN")],
)
def test_missing_or_inconsistent_isin_is_reported(
    tmp_path: Path, isin: str, message: str
) -> None:
    path = _database(tmp_path)
    _corrupt(path, f"UPDATE instruments SET isin = {isin}")
    result = check_ledger_integrity(path)
    assert any(message in item for item in result.discrepancies)


def test_cli_reports_every_price_discrepancy_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _database(tmp_path)
    _corrupt(path, "UPDATE daily_prices SET close_paise = 0, volume = -1")
    _corrupt(path, "UPDATE instruments SET isin = NULL")
    before = path.read_bytes()
    result = check_ledger_integrity(path)
    exit_code = main(["integrity", "--database", str(path)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert len(result.discrepancies) >= 3
    for discrepancy in result.discrepancies:
        assert f"- {discrepancy}" in captured.err
    assert path.read_bytes() == before
