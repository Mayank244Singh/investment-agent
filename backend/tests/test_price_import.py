"""Offline import tests using synthetic CSVs and disposable SQLite databases."""

import csv
import io
import socket
import zipfile
from collections.abc import Generator
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.cli import main
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.models.prices import DailyPrice, ImportBatch, Instrument
from app.services.price_import_service import (
    HEADERS,
    ImportResult,
    PriceImportError,
    import_price_file,
)

FILE_NAME = "BhavCopy_NSE_CM_0_0_0_20261030_F_0000.csv.zip"
DATE = "2026-10-30"
ISIN_A = "IN0000000001"
ISIN_B = "IN0000000002"


@pytest.fixture
def engine(tmp_path: Path) -> Generator[Engine, None, None]:
    """Create only a temporary database, without loading local settings."""
    database_engine = create_engine(f"sqlite:///{tmp_path / 'prices.db'}")
    Base.metadata.create_all(database_engine)
    yield database_engine
    database_engine.dispose()


def _row(**changes: str) -> dict[str, str]:
    row = dict.fromkeys(HEADERS, "")
    row.update(
        {
            "TradDt": DATE,
            "BizDt": DATE,
            "Sgmt": "CM",
            "Src": "NSE",
            "FinInstrmTp": "STK",
            "ISIN": ISIN_A,
            "TckrSymb": "TESTA",
            "SctySrs": "EQ",
            "OpnPric": "100.00",
            "HghPric": "110.00",
            "LwPric": "90.00",
            "ClsPric": "105.00",
            "PrvsClsgPric": "99.00",
            "TtlTradgVol": "1000",
        }
    )
    row.update(changes)
    return row


def _archive(
    directory: Path,
    rows: list[dict[str, str]] | None = None,
    *,
    file_name: str = FILE_NAME,
    member_name: str | None = None,
    headers: tuple[str, ...] = HEADERS,
    extra_member: bool = False,
    bom: bool = False,
) -> Path:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows if rows is not None else [_row()])
    data = output.getvalue().encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    path = directory / file_name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member_name or file_name.removesuffix(".zip"), data)
        if extra_member:
            archive.writestr("extra.txt", b"extra")
    return path


def _import(
    engine: Engine,
    path: Path,
    *,
    max_uncompressed_bytes: int = 50 * 1024 * 1024,
    skip_bad_rows: bool = False,
) -> ImportResult:
    with Session(engine) as session:
        return import_price_file(
            session,
            path,
            "synthetic test source",
            max_uncompressed_bytes=max_uncompressed_bytes,
            skip_bad_rows=skip_bad_rows,
        )


def _counts(engine: Engine) -> tuple[int, int, int]:
    with Session(engine) as session:
        return (
            session.scalar(select(func.count()).select_from(Instrument)) or 0,
            session.scalar(select(func.count()).select_from(DailyPrice)) or 0,
            session.scalar(select(func.count()).select_from(ImportBatch)) or 0,
        )


def test_valid_import_stores_paise_provenance_and_normalized_symbol(
    engine: Engine, tmp_path: Path
) -> None:
    path = _archive(tmp_path, [_row(TckrSymb=" testa ")])
    result = _import(engine, path)
    assert result.imported_rows == 1
    assert result.skipped_rows == 0
    assert len(result.file_sha256) == 64
    assert _counts(engine) == (1, 1, 1)
    with Session(engine) as session:
        instrument = session.scalar(select(Instrument))
        price = session.scalar(select(DailyPrice))
        batch = session.scalar(select(ImportBatch))
        assert instrument is not None and instrument.symbol == "TESTA"
        assert price is not None and price.close_paise == 10_500
        assert price.price_basis == "unadjusted"
        assert batch is not None and batch.file_name == FILE_NAME
        assert batch.source_label == "synthetic test source"


def test_bom_is_accepted(engine: Engine, tmp_path: Path) -> None:
    assert _import(engine, _archive(tmp_path, bom=True)).imported_rows == 1


@pytest.mark.parametrize(
    ("headers", "message"),
    [
        (tuple(h for h in HEADERS if h != "ClsPric"), "missing=['ClsPric']"),
        ((*HEADERS, "Extra"), "unexpected=['Extra']"),
        ((HEADERS[1], HEADERS[0], *HEADERS[2:]), "exact order required"),
    ],
)
def test_headers_must_match_exactly(
    engine: Engine, tmp_path: Path, headers: tuple[str, ...], message: str
) -> None:
    with pytest.raises(PriceImportError, match="CSV headers differ") as error:
        _import(engine, _archive(tmp_path, headers=headers))
    assert message in str(error.value)
    assert _counts(engine) == (0, 0, 0)


@pytest.mark.parametrize(
    "name",
    ["wrong.zip", "BhavCopy_NSE_CM_0_0_0_20260230_F_0000.csv.zip"],
)
def test_wrong_file_name_is_rejected(engine: Engine, tmp_path: Path, name: str) -> None:
    with pytest.raises(PriceImportError, match="file name"):
        _import(engine, _archive(tmp_path, file_name=name))
    assert _counts(engine) == (0, 0, 0)


def test_wrong_member_name_is_rejected(engine: Engine, tmp_path: Path) -> None:
    with pytest.raises(PriceImportError, match="member name does not match"):
        _import(engine, _archive(tmp_path, member_name="other.csv"))


def test_extra_member_is_rejected(engine: Engine, tmp_path: Path) -> None:
    with pytest.raises(PriceImportError, match="exactly one"):
        _import(engine, _archive(tmp_path, extra_member=True))


def test_path_separator_in_member_is_rejected(engine: Engine, tmp_path: Path) -> None:
    with pytest.raises(PriceImportError, match="path separators"):
        _import(engine, _archive(tmp_path, member_name="folder/price.csv"))


def test_oversized_member_is_rejected(engine: Engine, tmp_path: Path) -> None:
    path = _archive(tmp_path)
    with pytest.raises(PriceImportError, match="size limit"):
        _import(engine, path, max_uncompressed_bytes=20)
    assert _counts(engine) == (0, 0, 0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("TradDt", "2026-10-29"),
        ("BizDt", "2026-10-29"),
        ("Sgmt", "FO"),
        ("Src", "OTHER"),
        ("FinInstrmTp", "FUT"),
    ],
)
def test_file_identity_mismatch_rejects_even_in_skip_mode(
    engine: Engine, tmp_path: Path, field: str, value: str
) -> None:
    with pytest.raises(PriceImportError, match=field):
        _import(
            engine, _archive(tmp_path, [_row(**{field: value})]), skip_bad_rows=True
        )
    assert _counts(engine) == (0, 0, 0)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("TckrSymb", "  ", "symbol"),
        ("ISIN", "SHORT", "ISIN"),
        ("OpnPric", "0", "OpnPric"),
        ("HghPric", "-1", "HghPric"),
        ("LwPric", "0", "LwPric"),
        ("ClsPric", "0", "ClsPric"),
        ("PrvsClsgPric", "0", "PrvsClsgPric"),
        ("ClsPric", "1.001", "two decimal places"),
        ("ClsPric", "NaN", "finite"),
        ("ClsPric", "invalid", "decimal price"),
        ("LwPric", "111.00", "low price exceeds"),
        ("OpnPric", "89.00", "open price"),
        ("ClsPric", "111.00", "close price"),
        ("TtlTradgVol", "-1", "volume"),
        ("TtlTradgVol", "1.5", "volume"),
    ],
)
def test_bad_eq_row_rejected_with_line_number(
    engine: Engine, tmp_path: Path, field: str, value: str, reason: str
) -> None:
    with pytest.raises(PriceImportError, match="line 2") as error:
        _import(engine, _archive(tmp_path, [_row(**{field: value})]))
    assert reason in str(error.value)
    assert _counts(engine) == (0, 0, 0)


def test_non_eq_rows_reported_by_series(engine: Engine, tmp_path: Path) -> None:
    path = _archive(
        tmp_path,
        [
            _row(TckrSymb="TESTA", SctySrs="EQ"),
            _row(TckrSymb="TESTB", SctySrs="BE", ISIN=ISIN_B),
            _row(TckrSymb="TESTB", SctySrs="GB", ISIN=ISIN_B),
        ],
    )
    result = _import(engine, path)
    assert result.skipped_by_series == {"BE": 1, "GB": 1}
    assert result.skipped_rows == 2
    assert _counts(engine) == (1, 1, 1)


def test_skip_bad_rows_reports_each_rejection(engine: Engine, tmp_path: Path) -> None:
    path = _archive(
        tmp_path,
        [
            _row(TckrSymb="TESTA"),
            _row(TckrSymb="TESTB", ISIN=ISIN_B, ClsPric="1.001"),
        ],
    )
    result = _import(engine, path, skip_bad_rows=True)
    assert result.imported_rows == 1
    assert [(item.line, item.reason) for item in result.rejected_rows] == [
        (3, "ClsPric has more than two decimal places")
    ]
    assert _counts(engine) == (1, 1, 1)


def test_duplicate_rows_are_rejected_after_symbol_normalization(
    engine: Engine, tmp_path: Path
) -> None:
    path = _archive(tmp_path, [_row(), _row(TckrSymb=" testa ")])
    with pytest.raises(PriceImportError, match="duplicate symbol"):
        _import(engine, path)
    assert _counts(engine) == (0, 0, 0)


def test_same_file_hash_is_a_no_op(engine: Engine, tmp_path: Path) -> None:
    path = _archive(tmp_path)
    _import(engine, path)
    second = _import(engine, path)
    assert second.already_imported
    assert second.imported_rows == 0
    assert _counts(engine) == (1, 1, 1)


def test_same_price_from_a_different_file_is_skipped(
    engine: Engine, tmp_path: Path
) -> None:
    first = _archive(tmp_path, [_row()])
    _import(engine, first)
    second = _archive(tmp_path, [_row(), _row(TckrSymb="TESTB", ISIN=ISIN_B)])
    result = _import(engine, second)
    assert result.imported_rows == 1
    assert result.unchanged_rows == 1
    assert _counts(engine) == (2, 2, 2)


def test_conflicting_price_rolls_back(engine: Engine, tmp_path: Path) -> None:
    path = _archive(tmp_path)
    _import(engine, path)
    path = _archive(tmp_path, [_row(ClsPric="106.00")])
    with pytest.raises(PriceImportError, match="price conflict"):
        _import(engine, path)
    assert _counts(engine) == (1, 1, 1)


def test_changed_isin_rolls_back(engine: Engine, tmp_path: Path) -> None:
    path = _archive(tmp_path)
    _import(engine, path)
    path = _archive(tmp_path, [_row(ISIN=ISIN_B)])
    with pytest.raises(PriceImportError, match="ISIN conflict"):
        _import(engine, path)
    assert _counts(engine) == (1, 1, 1)


def test_mid_import_database_failure_rolls_back_all_rows(
    engine: Engine, tmp_path: Path
) -> None:
    path = _archive(tmp_path, [_row(), _row(TckrSymb="TESTB", ISIN=ISIN_B)])
    inserts = 0

    def fail_second_price(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal inserts
        if statement.startswith("INSERT INTO daily_prices"):
            inserts += 1
            if inserts == 2:
                raise RuntimeError("synthetic mid-import failure")

    event.listen(engine, "before_cursor_execute", fail_second_price)
    try:
        with pytest.raises(RuntimeError, match="mid-import failure"):
            _import(engine, path)
    finally:
        event.remove(engine, "before_cursor_execute", fail_second_price)
    assert _counts(engine) == (0, 0, 0)


def test_import_never_opens_a_network_socket(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _archive(tmp_path)

    def forbid_socket(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network socket opened")

    monkeypatch.setattr(socket, "socket", forbid_socket)
    assert _import(engine, path).imported_rows == 1


def test_cli_imports_into_explicit_temporary_database(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    try:
        path = _archive(tmp_path, [_row(), _row(SctySrs="BE")])
        assert (
            main(
                [
                    "import-prices",
                    str(path),
                    "--source-label",
                    "synthetic",
                    "--database",
                    str(engine.url.database),
                ]
            )
            == 0
        )
        output = capsys.readouterr().out
        assert "imported=1, skipped=1, rejected=0" in output
        assert "Skipped series BE: 1" in output
        assert _counts(engine) == (1, 1, 1)
    finally:
        get_settings.cache_clear()


def test_import_size_setting_is_validated_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("MAX_IMPORT_UNCOMPRESSED_MB", "0")
    with pytest.raises(ValidationError, match="max_import_uncompressed_mb"):
        Settings()
