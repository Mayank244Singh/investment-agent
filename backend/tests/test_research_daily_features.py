"""Daily Phase 7A indicator coverage on invented, contiguous sessions."""

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from app.modules.research.contracts import StrategyFamily, Timeframe
from app.modules.research.core import (
    ResearchConfig,
    ResearchObservation,
    build_dataset,
)
from app.modules.research.daily_features import calculate_daily_features


def invented_daily(count: int = 130) -> tuple[ResearchObservation, ...]:
    first = date(2025, 1, 1)
    last = first + timedelta(days=count - 1)
    return tuple(ResearchObservation(
        instrument_id=1, session=first + timedelta(days=index),
        close_paise=10_000 + index * 10, volume=100 + index,
        provider="invented", source_record_key=f"bar-{index}",
        available_at_utc=datetime.combine(
            first + timedelta(days=index), time(12), UTC
        ),
        adjustment_status="verified_no_action",
        identity_valid_from=first, identity_valid_to=last,
        identity_verified=True,
        open_paise=10_000 + index * 10,
        high_paise=10_010 + index * 10,
        low_paise=9_990 + index * 10,
    ) for index in range(count))


def test_daily_indicators_and_warmup() -> None:
    bars = invented_daily()
    early = calculate_daily_features(bars[:4])
    assert early["return_5"] is None
    assert early["sma_5_paise"] is None
    assert early["atr_14_paise"] is None
    values = calculate_daily_features(bars[:70])
    assert values["log_return"] == (Decimal(10_690) / 10_680).ln()
    for period in (5, 10, 20, 60):
        assert values[f"return_{period}"] == (
            Decimal(10_690) / Decimal(10_690 - period * 10) - 1
        )
    for period in (5, 10, 20, 50):
        assert values[f"sma_{period}_paise"] == (
            Decimal(10_690) - Decimal(period - 1) * 5
        )
    assert values["ema_12_paise"] is not None
    assert values["ema_26_paise"] is not None
    assert values["volatility_10"] is not None
    assert values["volatility_20"] is not None
    assert values["rolling_max_drawdown_20"] == 0
    assert values["high_low_position_20"] is not None
    assert values["volume_average_5"] == 167
    assert values["volume_average_20"] == Decimal("159.5")
    assert values["relative_volume_20"] is not None
    assert values["rsi_14"] == 100
    assert values["macd_paise"] is not None
    assert values["macd_signal_paise"] is not None
    assert values["macd_histogram_paise"] is not None
    assert values["atr_14_paise"] == 20


def test_daily_extra_features_are_point_in_time_and_selectable() -> None:
    bars = invented_daily()
    sessions = tuple(item.session for item in bars)
    config = ResearchConfig(
        dataset_version="invented-daily", sessions=sessions,
        decision_times_utc={day: datetime.combine(day, time(13), UTC)
                            for day in sessions},
        built_at_utc=datetime.combine(sessions[-1], time(23), UTC),
        window=2, required_features=("atr_14_paise", "return_5"),
    )
    before = build_dataset(bars, config)
    sample = next(item for item in before.samples
                  if item.feature_date == sessions[30]
                  and item.target_horizon_sessions == 5)
    assert sample.usable
    assert sample.features["atr_14_paise"] == 20
    changed = list(bars)
    changed[35] = replace(changed[35], close_paise=10_400,
                          high_paise=10_410)
    after = next(item for item in build_dataset(tuple(changed), config).samples
                 if item.feature_date == sessions[30]
                 and item.target_horizon_sessions == 5)
    assert after.features == sample.features
    assert after.target_return != sample.target_return
    absent_ohlc = tuple(replace(item, open_paise=None, high_paise=None,
                                low_paise=None) for item in bars)
    unpriced = next(item for item in build_dataset(absent_ohlc, config).samples
                    if item.feature_date == sessions[30]
                    and item.target_horizon_sessions == 5)
    assert "missing required feature" in unpriced.rejection_reasons


def test_invalid_daily_ohlcv_is_rejected() -> None:
    bar = invented_daily(1)[0]
    with pytest.raises(ValueError, match="invalid daily OHLCV"):
        replace(bar, low_paise=bar.close_paise + 1)
    with pytest.raises(ValueError, match="invalid daily OHLCV"):
        replace(bar, high_paise=None)


def test_daily_strategy_contracts_keep_swing_and_long_term_independent() -> None:
    bars = invented_daily()
    sessions = tuple(item.session for item in bars)
    base = ResearchConfig(
        dataset_version="invented-daily", sessions=sessions,
        decision_times_utc={day: datetime.combine(day, time(13), UTC)
                            for day in sessions},
        built_at_utc=datetime.combine(sessions[-1], time(23), UTC),
        window=2,
    )
    swing = replace(base, strategy=StrategyFamily.SWING,
                    horizons_sessions=(5, 10))
    long_term = replace(base, strategy=StrategyFamily.LONG_TERM,
                        horizons_sessions=(60, 120))
    swing_samples = build_dataset(bars, swing).samples
    long_samples = build_dataset(bars, long_term).samples
    assert {item.target_horizon_sessions for item in swing_samples} == {5, 10}
    assert {item.target_horizon_sessions for item in long_samples} == {60, 120}
    assert all(item.strategy is StrategyFamily.SWING
               and item.timeframe is Timeframe.DAY for item in swing_samples)
    with pytest.raises(ValueError, match="swing horizons"):
        replace(swing, horizons_sessions=(5, 60))
