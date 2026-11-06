"""Synthetic, isolated checks for real-data readiness reporting."""

import hashlib
import sqlite3
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.cli import main
from app.db.base import Base
from app.db.models.prices import (
    DailyPrice,
    IngestionRun,
    Instrument,
    InstrumentMapping,
    SourceObservation,
)
from app.modules.research.contracts import StrategyFamily, Timeframe
from app.modules.research.intraday import ExchangeCalendar, ExchangeSession
from app.modules.research.readiness import (
    CalendarEvidence,
    Eligibility,
    ReadinessBar,
    ReadinessPolicy,
    TradingHalt,
    assess_readiness,
)
from app.modules.research.readiness_io import load_calendar_evidence
from app.services.data_readiness_service import assess_stored_readiness

DAY = date(2026, 10, 30)
SESSION = ExchangeSession(DAY, time(9, 15), time(9, 25), special=True)
CALENDAR = CalendarEvidence(
    ExchangeCalendar((SESSION,), frozenset({date(2026, 10, 29)})),
    source_note="reviewed synthetic calendar",
)


def _policy(**changes: Any) -> ReadinessPolicy:
    base = ReadinessPolicy(
        StrategyFamily.INTRADAY,
        Timeframe.MINUTE_5,
        1,
        CALENDAR,
        licensing_evidence="synthetic fixture rights",
        historical_universe_evidence="invented stable membership",
        as_of_date=DAY,
    )
    return replace(base, **changes)


def _bar(offset: int = 0, **changes: Any) -> ReadinessBar:
    start = SESSION.opens_utc + timedelta(minutes=offset)
    base = ReadinessBar(
        instrument_id=1,
        symbol="TESTA",
        timeframe=Timeframe.MINUTE_5,
        session=DAY,
        open_paise=10000,
        high_paise=10100,
        low_paise=9900,
        close_paise=10050,
        volume=100,
        provider="synthetic-a",
        source_record_key=f"TESTA:{offset}",
        identity_verified=True,
        adjustment_status="verified_no_action",
        available_at_utc=start + timedelta(minutes=5),
        start_utc=start,
        end_utc=start + timedelta(minutes=5),
    )
    return replace(base, **changes)


def test_complete_synthetic_intraday_can_be_ready_for_research_only() -> None:
    report = assess_readiness((_bar(), _bar(5)), _policy())
    assert report.statuses == (Eligibility.READY_FOR_RESEARCH,)
    assert report.eligible
    assert report.as_dict()["training_enabled"] is False
    assert report.missing_candles == 0
    assert report.instrument_coverage["TESTA"]["sessions"] == 1


@pytest.mark.parametrize(
    "timeframe",
    [
        Timeframe.MINUTE_1,
        Timeframe.MINUTE_5,
        Timeframe.MINUTE_15,
    ],
)
def test_each_intraday_timeframe_uses_its_own_completed_grid(
    timeframe: Timeframe,
) -> None:
    session = ExchangeSession(DAY, time(9, 15), time(9, 45), special=True)
    calendar = CalendarEvidence(
        ExchangeCalendar((session,)), source_note="synthetic special session"
    )
    step = timeframe.minutes
    bars = tuple(
        replace(
            _bar(offset),
            timeframe=timeframe,
            end_utc=session.opens_utc + timedelta(minutes=offset + step),
            available_at_utc=session.opens_utc + timedelta(minutes=offset + step),
        )
        for offset in range(0, 30, step)
    )
    report = assess_readiness(bars, _policy(timeframe=timeframe, calendar=calendar))
    assert report.missing_candles == 0
    assert report.eligible


def test_missing_candle_is_not_confused_with_holiday_or_verified_halt() -> None:
    missing = assess_readiness((_bar(),), _policy())
    assert missing.missing_candles == 1
    assert missing.missing_sessions == 1
    assert Eligibility.INSUFFICIENT_COVERAGE in missing.statuses
    halt = TradingHalt(SESSION.opens_utc + timedelta(minutes=5), SESSION.closes_utc)
    calendar = replace(CALENDAR, halts=(halt,))
    complete = assess_readiness((_bar(),), _policy(calendar=calendar))
    assert complete.missing_candles == 0
    assert complete.eligible


def test_daily_bar_on_full_halt_is_a_quality_failure() -> None:
    calendar = CalendarEvidence(
        ExchangeCalendar((SESSION,)),
        halts=(TradingHalt(SESSION.opens_utc, SESSION.closes_utc),),
        source_note="synthetic verified halt",
    )
    policy = ReadinessPolicy(
        StrategyFamily.SWING,
        Timeframe.DAY,
        1,
        calendar,
        licensing_evidence="synthetic",
        historical_universe_evidence="synthetic",
        as_of_date=DAY,
    )
    bar = replace(
        _bar(),
        timeframe=Timeframe.DAY,
        start_utc=None,
        end_utc=None,
        available_at_utc=SESSION.closes_utc,
    )
    report = assess_readiness((bar,), policy)
    assert report.missing_candles == 0
    assert Eligibility.DATA_QUALITY_FAILURE in report.statuses
    assert any("full halt" in issue for issue in report.quality_issues)


def test_duplicate_and_mixed_provider_observations() -> None:
    duplicate = assess_readiness(
        (_bar(), replace(_bar(), source_record_key="second"), _bar(5)),
        _policy(),
    )
    assert duplicate.duplicate_observations == 1
    assert Eligibility.DATA_QUALITY_FAILURE in duplicate.statuses
    other = replace(
        _bar(),
        provider="synthetic-b",
        source_record_key="b:0",
        reconciliation="agreement",
    )
    agreeing = assess_readiness((_bar(), other, _bar(5)), _policy())
    assert agreeing.mixed_provider_slots == 1
    assert agreeing.duplicate_observations == 0
    assert agreeing.eligible
    conflicting = assess_readiness(
        (_bar(), replace(other, close_paise=10060), _bar(5)), _policy()
    )
    assert Eligibility.DATA_QUALITY_FAILURE in conflicting.statuses
    assert any("providers disagree" in issue for issue in conflicting.quality_issues)
    unselected = assess_readiness(
        (replace(_bar(), reconciliation="unselected"), _bar(5)), _policy()
    )
    assert Eligibility.DATA_QUALITY_FAILURE in unselected.statuses
    assert any("selection" in issue for issue in unselected.quality_issues)


def test_invalid_ohlc_volume_timestamps_and_identity_fail_closed() -> None:
    bad = replace(
        _bar(),
        low_paise=10100,
        volume=-1,
        start_utc=datetime(2026, 10, 30, 9, 15),
        available_at_utc=datetime(2026, 10, 30, 9, 20),
        identity_verified=False,
    )
    report = assess_readiness((bad, _bar(5)), _policy())
    assert Eligibility.DATA_QUALITY_FAILURE in report.statuses
    assert any("OHLCV" in issue for issue in report.quality_issues)
    assert any("mapping" in issue for issue in report.quality_issues)
    assert any("timezone" in issue for issue in report.quality_issues)
    assert any("candle time" in issue for issue in report.quality_issues)


def test_unknown_actions_licensing_history_and_access_report_all_reasons() -> None:
    report = assess_readiness(
        (replace(_bar(), adjustment_status="unverified", available_at_utc=None),),
        _policy(
            min_sessions=2,
            calendar=None,
            licensing_evidence=None,
            historical_universe_evidence=None,
            provider_access_blocked=True,
        ),
    )
    assert set(report.statuses) == {
        Eligibility.INSUFFICIENT_COVERAGE,
        Eligibility.CORPORATE_ACTION_UNVERIFIED,
        Eligibility.POINT_IN_TIME_UNVERIFIED,
        Eligibility.LICENSING_UNVERIFIED,
        Eligibility.PROVIDER_ACCESS_BLOCKED,
    }
    assert not report.eligible


def test_long_term_requires_fundamentals_and_daily_sessions() -> None:
    policy = ReadinessPolicy(
        StrategyFamily.LONG_TERM,
        Timeframe.DAY,
        2,
        CalendarEvidence(ExchangeCalendar((SESSION,)), source_note="synthetic"),
        licensing_evidence="synthetic",
        historical_universe_evidence="synthetic",
    )
    bar = replace(
        _bar(),
        timeframe=Timeframe.DAY,
        start_utc=None,
        end_utc=None,
        available_at_utc=SESSION.closes_utc,
    )
    report = assess_readiness((bar,), policy)
    assert Eligibility.INSUFFICIENT_COVERAGE in report.statuses
    assert any("fundamental" in reason for reason in report.reasons)


def test_stored_report_uses_read_only_database_and_does_not_invent_evidence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "market.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32))"
        )
        connection.exec_driver_sql(
            "INSERT INTO alembic_version VALUES ('0004_market_data_foundation')"
        )
    with Session(engine) as session:
        instrument = Instrument(symbol="TESTA", series="EQ", isin="IN0000000001")
        run = IngestionRun(
            provider="nse_zip",
            source_kind="zip",
            source_ref="synthetic-only",
            provenance_kind="reconstructed",
        )
        session.add_all((instrument, run))
        session.flush()
        session.add(
            SourceObservation(
                instrument_id=instrument.id,
                ingestion_run_id=run.id,
                provider="nse_zip",
                provider_instrument_key=None,
                source_record_key="synthetic:1",
                payload_sha256="a" * 64,
                symbol="TESTA",
                exchange="NSE",
                series="EQ",
                trade_date=DAY,
                open_paise=10000,
                high_paise=10100,
                low_paise=9900,
                close_paise=10050,
                volume=100,
                origin="reconstructed",
                reconciliation="selected",
            )
        )
        session.commit()
    engine.dispose()
    before = hashlib.sha256(path.read_bytes()).digest()
    policy = ReadinessPolicy(StrategyFamily.SWING, Timeframe.DAY, 504)
    report = assess_stored_readiness(path, policy, symbols=("TESTA",))
    assert report.observations == 1
    assert report.provenance_counts == {"reconstructed": 1}
    assert Eligibility.INSUFFICIENT_COVERAGE in report.statuses
    assert Eligibility.CORPORATE_ACTION_UNVERIFIED in report.statuses
    assert Eligibility.POINT_IN_TIME_UNVERIFIED in report.statuses
    assert Eligibility.LICENSING_UNVERIFIED in report.statuses
    assert Eligibility.DATA_QUALITY_FAILURE in report.statuses
    assert any("lacks canonical link" in issue for issue in report.quality_issues)
    assert (
        main(
            [
                "data-readiness",
                "--database",
                str(path),
                "--strategy",
                "swing",
                "--timeframe",
                "1d",
                "--symbols",
                "TESTA",
            ]
        )
        == 1
    )
    output = capsys.readouterr().out
    assert '"training_enabled": false' in output
    assert "LICENSING_UNVERIFIED" in output
    assert (
        main(
            [
                "data-readiness",
                "--database",
                str(path),
                "--strategy",
                "swing",
                "--timeframe",
                "1d",
                "--provider",
                "upstox",
            ]
        )
        == 1
    )
    assert "PROVIDER_ACCESS_BLOCKED" in capsys.readouterr().out
    assert hashlib.sha256(path.read_bytes()).digest() == before
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    # Exact canonical linkage and a reviewed mapping clear quality, but
    # cannot clear independent availability, action or licensing blockers.
    engine = create_engine(f"sqlite:///{path}")
    with Session(engine) as session:
        observation = session.query(SourceObservation).one()
        session.add_all(
            (
                InstrumentMapping(
                    instrument_id=observation.instrument_id,
                    provider="nse_zip",
                    provider_instrument_key=None,
                    symbol="TESTA",
                    exchange="NSE",
                    series="EQ",
                    valid_from=DAY,
                    valid_to=DAY,
                    identity_status="verified",
                ),
                DailyPrice(
                    instrument_id=observation.instrument_id,
                    source_observation_id=observation.id,
                    trade_date=DAY,
                    open_paise=10000,
                    high_paise=10100,
                    low_paise=9900,
                    close_paise=10050,
                    volume=100,
                    price_basis="unadjusted",
                ),
            )
        )
        session.commit()
    engine.dispose()
    verified_report = assess_stored_readiness(path, policy, symbols=("TESTA",))
    assert Eligibility.DATA_QUALITY_FAILURE not in verified_report.statuses
    assert Eligibility.CORPORATE_ACTION_UNVERIFIED in verified_report.statuses


def test_calendar_loader_requires_explicit_sessions_holidays_and_halts(
    tmp_path: Path,
) -> None:
    import json

    path = tmp_path / "calendar.json"
    path.write_text(
        json.dumps(
            {
                "source_note": "invented reviewed fixture",
                "sessions": [
                    {
                        "date": DAY.isoformat(),
                        "opens_local": "09:15:00",
                        "closes_local": "09:25:00",
                        "special": True,
                    }
                ],
                "holidays": ["2026-10-29"],
                "halts": [
                    {
                        "start_utc": (
                            SESSION.opens_utc + timedelta(minutes=5)
                        ).isoformat(),
                        "end_utc": SESSION.closes_utc.isoformat(),
                    }
                ],
            }
        )
    )
    loaded = load_calendar_evidence(path)
    assert loaded.calendar.holidays == frozenset({date(2026, 10, 29)})
    assert len(loaded.halts) == 1
    path.write_text('{"sessions": []}')
    with pytest.raises(ValueError, match="calendar requires"):
        load_calendar_evidence(path)
