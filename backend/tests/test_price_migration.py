"""Exercise the price migration only against a disposable SQLite database."""

import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from app.core.config import Settings, get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PRICE_TABLES = {"instruments", "daily_prices", "import_batches"}


def test_price_migration_upgrade_and_downgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upgrade, verify constraints, downgrade, and retain the ledger."""
    database = tmp_path / "prices.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))

    try:
        command.upgrade(config, "head")
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert PRICE_TABLES <= tables
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone() == ("0003_daily_prices",)

            connection.execute(
                "INSERT INTO instruments (symbol, series, isin) VALUES (?, ?, ?)",
                ("FAKE", "EQ", "IN0000000001"),
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO instruments (symbol, series) VALUES (?, ?)",
                    ("FAKE", "EQ"),
                )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO instruments (symbol, series) VALUES (?, ?)",
                    ("lower", "EQ"),
                )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO instruments (symbol, series) VALUES (?, ?)",
                    ("OTHER", "BE"),
                )
            connection.execute(
                """INSERT INTO import_batches
                   (file_name, file_sha256, source_label, total_rows,
                    imported_rows, skipped_rows, rejected_rows, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("synthetic.zip", "a" * 64, "synthetic", 1, 1, 0, 0, "completed"),
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO daily_prices
                       (instrument_id, import_batch_id, trade_date, open_paise,
                        high_paise, low_paise, close_paise,
                        previous_close_paise, volume)
                       VALUES (1, 1, '2026-11-01', 100, 90, 95, 100, 100, 0)"""
                )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO daily_prices
                       (instrument_id, import_batch_id, trade_date, open_paise,
                        high_paise, low_paise, close_paise,
                        previous_close_paise, volume)
                       VALUES (999, 1, '2026-11-01', 100, 110, 90, 100, 100, 0)"""
                )
            connection.rollback()

        command.downgrade(config, "0002_ledger_idempotency")
        with sqlite3.connect(database) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert not PRICE_TABLES & tables
            assert "ledger_transactions" in tables
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone() == ("0002_ledger_idempotency",)
    finally:
        get_settings.cache_clear()
