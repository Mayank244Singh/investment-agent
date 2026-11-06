"""Invented intraday bars only; no real market data or database access."""

import json
import socket
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.cli import main
from app.modules.research.contracts import StrategyFamily, Timeframe
from app.modules.research.intraday import (
    ExchangeCalendar,
    ExchangeSession,
    IntradayCandle,
    IntradayConfig,
    build_intraday_dataset,
    chronological_intraday_split,
)

DAY = date(2025, 1, 2)
OPEN = datetime(2025, 1, 2, 3, 45, tzinfo=UTC)  # 09:15 Asia/Kolkata


def invented(
    *, timeframe: Timeframe = Timeframe.MINUTE_1, count: int = 90,
    session: ExchangeSession | None = None, constant: bool = False,
) -> tuple[tuple[IntradayCandle, ...], IntradayConfig]:
    exchange_session = session or ExchangeSession(DAY, time(9, 15), time(15, 30))
    start = exchange_session.opens_utc
    bars = []
    for index in range(count):
        begin = start + timedelta(minutes=index * timeframe.minutes)
        end = begin + timedelta(minutes=timeframe.minutes)
        close = 10_000 if constant else 10_000 + index * 10
        bars.append(IntradayCandle(
            instrument_id=1, timeframe=timeframe, start_utc=begin,
            end_utc=end, available_at_utc=end, is_complete=True,
            open_paise=close, high_paise=close + 10,
            low_paise=close - 10, close_paise=close,
            volume=100, provider="invented", source_record_key=f"bar-{index}",
            adjustment_status="verified_no_action", identity_verified=True,
        ))
    config = IntradayConfig(
        dataset_version="invented-v1",
        calendar=ExchangeCalendar((exchange_session,)),
        timeframe=timeframe, built_at_utc=exchange_session.closes_utc
        + timedelta(hours=1),
    )
    return tuple(bars), config


def sample_at(
    bars: tuple[IntradayCandle, ...], config: IntradayConfig,
    index: int, horizon: int = 5,
):
    return next(item for item in build_intraday_dataset(bars, config).samples
                if item.decision_at_utc == bars[index].end_utc
                and item.target_horizon_minutes == horizon)


def test_strategy_and_timeframe_contracts() -> None:
    assert set(StrategyFamily) == {
        StrategyFamily.INTRADAY, StrategyFamily.SWING, StrategyFamily.LONG_TERM
    }
    assert Timeframe.MINUTE_1.minutes == 1
    assert Timeframe.MINUTE_5.minutes == 5
    assert Timeframe.MINUTE_15.minutes == 15
    with pytest.raises(ValueError, match="no fixed minute"):
        _ = Timeframe.DAY.minutes


@pytest.mark.parametrize("timeframe,minutes", [
    (Timeframe.MINUTE_1, 1), (Timeframe.MINUTE_5, 5),
    (Timeframe.MINUTE_15, 15),
])
def test_completed_candle_returns_on_each_supported_timeframe(
    timeframe: Timeframe, minutes: int,
) -> None:
    bars, config = invented(timeframe=timeframe, count=20)
    sample = sample_at(bars, config, 3, 15)
    assert sample.features[f"return_{minutes}m"] == Decimal(10030) / 10020 - 1
    if timeframe is Timeframe.MINUTE_15:
        assert sample.features["return_1m"] is None
        assert "horizon incompatible with timeframe" in sample_at(
            bars, config, 3, 5
        ).rejection_reasons


def test_all_core_indicators_and_session_vwap_use_completed_prefix() -> None:
    bars, config = invented(constant=True)
    sample = sample_at(bars, config, 40)
    assert sample.usable
    assert sample.features["return_1m"] == 0
    assert sample.features["return_5m"] == 0
    assert sample.features["return_15m"] == 0
    assert sample.features["momentum"] == 0
    assert sample.features["rolling_volatility"] == 0
    assert sample.features["sma_paise"] == Decimal(10_000)
    assert sample.features["ema_paise"] == Decimal(10_000)
    assert sample.features["rsi_14"] == 50
    assert sample.features["macd_paise"] == 0
    assert sample.features["macd_signal_paise"] == 0
    assert sample.features["macd_histogram_paise"] == 0
    assert sample.features["atr_14_paise"] == 20
    assert sample.features["relative_volume"] == 1
    assert sample.features["volume_spike"] == 0
    assert sample.features["session_vwap_paise"] == 10_000
    assert sample.features["distance_from_vwap"] == 0
    assert sample.features["intraday_range"] == Decimal(20) / 10_000
    assert sample.features["opening_range_high_paise"] == 10_010
    assert sample.features["opening_range_low_paise"] == 9_990
    assert sample.features["opening_range_breakout"] == 0
    assert sample.features["minutes_from_open"] == 41
    assert sample.features["minutes_to_close"] == 334


def test_future_bar_changes_label_but_never_features_or_vwap() -> None:
    bars, config = invented()
    before = sample_at(bars, config, 20)
    changed = list(bars)
    changed[25] = replace(changed[25], close_paise=10_300,
                          high_paise=10_310, volume=9_999)
    after = sample_at(tuple(changed), config, 20)
    assert after.features == before.features
    assert after.target_return != before.target_return
    assert after.provenance == before.provenance


def test_opening_range_does_not_use_future_high() -> None:
    bars, config = invented()
    early = sample_at(bars, config, 5)
    assert early.features["opening_range_high_paise"] is None
    changed = list(bars)
    changed[14] = replace(changed[14], high_paise=100_000)
    assert sample_at(tuple(changed), config, 5).features == early.features
    completed = sample_at(tuple(changed), config, 14)
    assert completed.features["opening_range_high_paise"] == 100_000


def test_incomplete_or_unavailable_candle_cannot_be_used() -> None:
    bars, config = invented()
    incomplete = list(bars)
    incomplete[10] = replace(incomplete[10], is_complete=False)
    sample = sample_at(tuple(incomplete), config, 10)
    assert "incomplete candle" in sample.rejection_reasons
    assert all(value is None for value in sample.features.values())
    delayed = list(bars)
    delayed[10] = replace(delayed[10], available_at_utc=bars[11].end_utc)
    assert "candle unavailable at required time" in sample_at(
        tuple(delayed), config, 10
    ).rejection_reasons
    unknown = list(bars)
    unknown[10] = replace(unknown[10], available_at_utc=None)
    assert "historical availability unknown" in sample_at(
        tuple(unknown), config, 10
    ).rejection_reasons


def test_missing_duplicate_and_out_of_order_bars_are_reported() -> None:
    bars, config = invented()
    missing = bars[:10] + bars[11:]
    result = build_intraday_dataset(missing, config)
    assert result.report.missing_expected_bars == 1
    assert "missing minute bars" in sample_at(missing, config, 12).rejection_reasons
    duplicate = build_intraday_dataset(bars + (bars[10],), config)
    assert duplicate.report.duplicate_timestamps == 1
    assert duplicate.report.accepted == 0
    shuffled = bars[:9] + (bars[10], bars[9]) + bars[11:]
    disordered = build_intraday_dataset(shuffled, config)
    assert disordered.report.out_of_order_observations == 1
    assert disordered.report.accepted == 0


def test_holiday_special_session_and_close_boundary() -> None:
    special = ExchangeSession(DAY, time(18), time(19), special=True)
    bars, config = invented(session=special, count=60)
    assert sample_at(bars, config, 20).usable
    assert "target crosses exchange session close" in sample_at(
        bars, config, 59
    ).rejection_reasons
    holiday_calendar = ExchangeCalendar(
        (ExchangeSession(DAY + timedelta(days=1), time(9, 15), time(15, 30)),),
        frozenset({DAY}),
    )
    holiday = build_intraday_dataset(bars, replace(config, calendar=holiday_calendar))
    assert holiday.report.accepted == 0
    assert holiday.report.rejection_reasons["market holiday"] > 0


def test_timezone_and_session_grid_are_validated() -> None:
    bars, config = invented()
    with pytest.raises(ValueError, match="explicit UTC"):
        replace(bars[0], start_utc=datetime(2025, 1, 2, 9, 15))
    with pytest.raises(ValueError, match="explicit UTC"):
        replace(bars[0], start_utc=datetime(2025, 1, 2, 9, 15,
                                            tzinfo=ZoneInfo("Asia/Kolkata")))
    shifted = replace(
        bars[0], start_utc=bars[0].start_utc - timedelta(minutes=1),
        end_utc=bars[0].end_utc - timedelta(minutes=1),
        available_at_utc=bars[0].available_at_utc,
    )
    assert "candle outside session grid" in build_intraday_dataset(
        (shifted,) + bars[1:], config
    ).samples[0].rejection_reasons


def test_corporate_action_and_unverified_adjustment_block_research() -> None:
    bars, config = invented()
    unsafe = list(bars)
    unsafe[10] = replace(unsafe[10], corporate_action_flag=True,
                         adjustment_status="unverified", identity_verified=False)
    reasons = sample_at(tuple(unsafe), config, 10).rejection_reasons
    assert "corporate action unresolved" in reasons
    assert "adjustment status unverified" in reasons
    assert "historical identity unverified" in reasons


def test_forward_labels_and_chronological_purging() -> None:
    bars, config = invented(count=130)
    sample = sample_at(bars, config, 20, 60)
    assert sample.target_end_utc == bars[80].end_utc
    assert sample.target_return == (
        Decimal(bars[80].close_paise) / bars[20].close_paise - 1
    )
    assert sample.positive_return_label == 1
    split = chronological_intraday_split(
        build_intraday_dataset(bars, config).samples,
        bars[40].end_utc, bars[80].end_utc,
        embargo=timedelta(minutes=5),
    )
    assert split.train and split.validation and split.test
    assert all(item.target_end_utc is not None
               and item.target_end_utc <= bars[40].end_utc for item in split.train)
    assert all(item.target_end_utc is not None
               and item.target_end_utc <= bars[80].end_utc for item in split.validation)
    assert any(reason == "training label overlaps validation"
               for _, reason in split.excluded)


def test_invalid_ohlcv_is_rejected() -> None:
    bars, _ = invented()
    with pytest.raises(ValueError, match="invalid intraday OHLCV"):
        replace(bars[0], high_paise=bars[0].close_paise - 1)
    with pytest.raises(ValueError, match="invalid intraday OHLCV"):
        replace(bars[0], volume=-1)


def test_synthetic_cli_never_opens_socket_or_database(
    tmp_path, monkeypatch, capsys,
) -> None:
    bars, config = invented()
    payload = {
        "synthetic": True, "dataset_version": config.dataset_version,
        "timeframe": config.timeframe.value,
        "built_at_utc": config.built_at_utc.isoformat(),
        "sessions": [{"date": DAY.isoformat(), "opens_local": "09:15",
                      "closes_local": "15:30"}],
        "candles": [{
            "instrument_id": item.instrument_id,
            "start_utc": item.start_utc.isoformat(),
            "end_utc": item.end_utc.isoformat(),
            "available_at_utc": item.available_at_utc.isoformat()
            if item.available_at_utc else None,
            "is_complete": item.is_complete,
            "open_paise": item.open_paise, "high_paise": item.high_paise,
            "low_paise": item.low_paise, "close_paise": item.close_paise,
            "volume": item.volume, "provider": item.provider,
            "source_record_key": item.source_record_key,
            "adjustment_status": item.adjustment_status,
            "identity_verified": item.identity_verified,
        } for item in bars],
    }
    path = tmp_path / "invented.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("research CLI accessed network or database")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("app.cli.get_settings", forbidden)
    monkeypatch.setattr("app.cli.create_engine", forbidden)
    assert main(["research-intraday-validate", "--synthetic-json", str(path)]) == 0
    assert "Synthetic intraday 1m" in capsys.readouterr().out
    payload["synthetic"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert main(["research-intraday-validate", "--synthetic-json", str(path)]) == 1
