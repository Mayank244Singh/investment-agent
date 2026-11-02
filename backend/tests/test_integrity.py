"""Read-only ledger integrity reconciliation tests."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.cli import main
from app.db.base import Base
from app.modules.portfolio import LedgerService
from app.services.integrity_service import check_ledger_integrity

NOW = datetime(2026, 11, 2, 2, 0, tzinfo=UTC)


def test_integrity_check_accepts_valid_multi_lot_fifo_ledger_read_only(
    tmp_path: Path,
) -> None:
    database = _create_valid_database(tmp_path / "valid.db")
    before = database.read_bytes()

    result = check_ledger_integrity(database)

    assert result.is_valid
    assert result.discrepancies == ()
    assert database.read_bytes() == before


def test_integrity_cli_reports_every_deliberately_corrupted_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = _create_valid_database(tmp_path / "corrupt.db")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE holding_lots SET remaining_quantity = remaining_quantity + 1 "
            "WHERE id = 2"
        )
        connection.execute(
            "UPDATE profit_vault_entries SET amount_paise = amount_paise + 1"
        )

    result = check_ledger_integrity(database)
    exit_code = main(["integrity", "--database", str(database)])
    captured = capsys.readouterr()

    assert not result.is_valid
    assert exit_code == 1
    assert len(result.discrepancies) >= 3
    for discrepancy in result.discrepancies:
        assert f"- {discrepancy}" in captured.err


def test_integrity_check_detects_non_fifo_realization_matches(
    tmp_path: Path,
) -> None:
    database = _create_valid_database(tmp_path / "non-fifo.db")
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            UPDATE lot_realizations
            SET holding_lot_id = CASE holding_lot_id WHEN 1 THEN 2 ELSE 1 END
            """
        )

    result = check_ledger_integrity(database)

    assert any("not FIFO" in item for item in result.discrepancies)
    assert any("remaining quantity" in item for item in result.discrepancies)
    assert any("FIFO cost" in item for item in result.discrepancies)


def test_integrity_check_rejects_negative_final_cash_as_likely_missing_deposit(
    tmp_path: Path,
) -> None:
    database = tmp_path / "negative-cash.db"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        LedgerService(session).record_buy("INFY", 1, 1_000, NOW)
    engine.dispose()

    result = check_ledger_integrity(database)

    assert any(
        "Final cash balance is negative" in item
        and "likely means a deposit entry is missing" in item
        for item in result.discrepancies
    )


def _create_valid_database(path: Path) -> Path:
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ledger = LedgerService(session)
        ledger.record_deposit(100_000, NOW)
        ledger.record_buy("TCS", 5, 1_000, NOW, fees_paise=5)
        ledger.record_buy("TCS", 5, 2_000, NOW + timedelta(minutes=1), fees_paise=5)
        ledger.record_sell("TCS", 7, 3_000, NOW + timedelta(minutes=2), fees_paise=7)
    engine.dispose()
    return path
