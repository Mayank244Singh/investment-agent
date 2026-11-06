"""Offline CLI pilot over invented NSE rows in disposable SQLite files."""

import hashlib
import json
import socket
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.db.models  # noqa: F401 - register every table
from app.cli import main
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

FIRST_DAY = date(2026, 10, 26)
SYMBOLS = ("TESTA", "TESTB", "TESTC", "TESTD", "TESTE")


def _isin(index: int) -> str:
    prefix = f"IN{index:09d}"
    return next(
        prefix + str(check) for check in range(10) if _valid_isin(prefix + str(check))
    )


def _rupees(paise: int) -> str:
    return f"{paise // 100}.{paise % 100:02d}"


def _seed_reference(path: Path) -> None:
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
            for offset in range(5):
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


def _fixture(path: Path) -> None:
    metadata = []
    responses: dict[str, object] = {}
    for index, symbol in enumerate(SYMBOLS):
        metadata.append(
            {
                "instrument_key": f"NSE_EQ|{symbol}",
                "exchange": "NSE",
                "segment": "NSE_EQ",
                "instrument_type": "EQ",
                "trading_symbol": symbol,
                "isin": _isin(index + 1),
                "source": "mock_instrument_master",
            }
        )
        candles = []
        for offset in range(5):
            day = FIRST_DAY + timedelta(days=offset)
            opened = 10_000 + index * 500 + offset * 100
            candles.append(
                [
                    f"{day}T00:00:00+05:30",
                    _rupees(opened),
                    _rupees(opened + 500),
                    _rupees(opened - 500),
                    _rupees(opened + 100),
                    1_000 + index,
                ]
            )
        responses[symbol] = {"status": "success", "data": {"candles": candles}}
    path.write_text(
        json.dumps(
            {"mode": "synthetic_mock", "instruments": metadata, "responses": responses}
        ),
        encoding="utf-8",
    )


def _canonical_hash(path: Path) -> str:
    columns = (
        "id,instrument_id,import_batch_id,trade_date,open_paise,high_paise,"
        "low_paise,close_paise,previous_close_paise,volume,price_basis"
    )
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            f"SELECT {columns} FROM daily_prices ORDER BY id"
        ).fetchall()
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def test_offline_cli_reconciles_and_replays_without_network_or_source_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "invented_nse.db"
    target = tmp_path / "pilot.db"
    mock_file = tmp_path / "invented_upstox.json"
    _seed_reference(source)
    _fixture(mock_file)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    canonical_hash = _canonical_hash(source)

    def forbid_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline pilot opened a network socket")

    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    exit_code = main(
        [
            "pilot-upstox", "--mode", "offline", "--reference-database",
            str(source), "--database", str(target), "--mock-file", str(mock_file),
            "--symbols", *SYMBOLS,
        ]
    )
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "OFFLINE MOCK PILOT" in output
    assert "25 canonical observations" in output
    assert "Identical redelivery added=0 observations" in output
    assert "Aggregate: NSE=25, mock=25, exact=25" in output
    assert "Temporary database integrity: passed" in output
    for symbol in SYMBOLS:
        assert f"{symbol}: mocked observations added=5" in output
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    assert _canonical_hash(source) == canonical_hash
    assert _canonical_hash(target) == canonical_hash
    assert check_ledger_integrity(target).is_valid
    with sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM source_observations WHERE provider='upstox'"
        ).fetchone() == (25,)
        assert connection.execute(
            "SELECT COUNT(*) FROM source_observations WHERE provider='nse_zip'"
        ).fetchone() == (25,)
        assert connection.execute("SELECT COUNT(*) FROM daily_prices").fetchone() == (
            25,
        )


def test_live_mode_refuses_without_opt_in_or_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("app.cli._analytics_token", lambda: None)
    args = [
        "pilot-upstox", "--mode", "live", "--reference-database",
        str(tmp_path / "unused.db"), "--database", str(tmp_path / "pilot.db"),
    ]
    assert main(args) == 1
    assert "requires --allow-live" in capsys.readouterr().err
    assert main([*args, "--allow-live"]) == 1
    assert "requires UPSTOX_ANALYTICS_TOKEN" in capsys.readouterr().err
    fake_token = "synthetic-test-token-never-send"
    monkeypatch.setattr("app.cli._analytics_token", lambda: fake_token)
    assert main([*args, "--allow-live"]) == 1
    refusal = capsys.readouterr().err
    assert "reference database does not exist" in refusal
    assert fake_token not in refusal
    assert not (tmp_path / "pilot.db").exists()


@pytest.mark.parametrize(
    "payload",
    [
        "{not-json",
        '{"mode":"unlabelled","instruments":[],"responses":{}}',
    ],
)
def test_bad_mock_fixture_is_rejected_before_copying_reference(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    payload: str,
) -> None:
    source = tmp_path / "invented_nse.db"
    target = tmp_path / "pilot.db"
    mock_file = tmp_path / "bad.json"
    _seed_reference(source)
    mock_file.write_text(payload, encoding="utf-8")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    assert main(
        [
            "pilot-upstox", "--mode", "offline", "--reference-database",
            str(source), "--database", str(target), "--mock-file", str(mock_file),
            "--symbols", *SYMBOLS,
        ]
    ) == 1
    assert "mock fixture" in capsys.readouterr().err
    assert not target.exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
