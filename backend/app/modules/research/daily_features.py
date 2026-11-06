"""Optional daily research indicators on contiguous, completed OHLCV history."""

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol


class DailyBar(Protocol):
    @property
    def close_paise(self) -> int: ...

    @property
    def volume(self) -> int: ...

    @property
    def open_paise(self) -> int | None: ...

    @property
    def high_paise(self) -> int | None: ...

    @property
    def low_paise(self) -> int | None: ...


EXTRA_FEATURES = (
    "log_return", "return_5", "return_10", "return_20", "return_60",
    "sma_5_paise", "sma_10_paise", "sma_20_paise", "sma_50_paise",
    "ema_12_paise", "ema_26_paise", "volatility_10", "volatility_20",
    "rolling_max_drawdown_20", "high_low_position_20",
    "volume_average_5", "volume_average_20", "relative_volume_20",
    "rsi_14", "macd_paise", "macd_signal_paise",
    "macd_histogram_paise", "atr_14_paise",
)


def _ema(values: list[Decimal], period: int) -> Decimal | None:
    if len(values) < period:
        return None
    result = sum(values[:period], Decimal(0)) / Decimal(period)
    alpha = Decimal(2) / Decimal(period + 1)
    for value in values[period:]:
        result += alpha * (value - result)
    return result


def _rsi(closes: list[int]) -> Decimal | None:
    if len(closes) < 15:
        return None
    changes = [closes[index] - closes[index - 1]
               for index in range(1, len(closes))]
    gains = [Decimal(max(value, 0)) for value in changes]
    losses = [Decimal(max(-value, 0)) for value in changes]
    gain = sum(gains[:14], Decimal(0)) / Decimal(14)
    loss = sum(losses[:14], Decimal(0)) / Decimal(14)
    for next_gain, next_loss in zip(gains[14:], losses[14:], strict=True):
        gain = (gain * 13 + next_gain) / Decimal(14)
        loss = (loss * 13 + next_loss) / Decimal(14)
    if loss == 0:
        return Decimal(100) if gain > 0 else Decimal(50)
    return Decimal(100) - Decimal(100) / (1 + gain / loss)


def calculate_daily_features(history: Sequence[DailyBar]) -> dict[str, Decimal | None]:
    """Return `None` during warm-up or when required OHLC is absent."""
    result: dict[str, Decimal | None] = dict.fromkeys(EXTRA_FEATURES)
    if not history:
        return result
    closes = [item.close_paise for item in history]
    values = [Decimal(value) for value in closes]
    if len(history) >= 2:
        result["log_return"] = (
            Decimal(closes[-1]) / Decimal(closes[-2])
        ).ln()
    for period in (5, 10, 20, 60):
        if len(history) > period:
            result[f"return_{period}"] = (
                Decimal(closes[-1]) / Decimal(closes[-period - 1]) - 1
            )
    for period in (5, 10, 20, 50):
        if len(history) >= period:
            result[f"sma_{period}_paise"] = (
                Decimal(sum(closes[-period:])) / Decimal(period)
            )
    for period in (12, 26):
        result[f"ema_{period}_paise"] = _ema(values, period)
    for period in (10, 20):
        if len(history) > period:
            returns = [
                Decimal(closes[index]) / Decimal(closes[index - 1]) - 1
                for index in range(len(closes) - period, len(closes))
            ]
            mean = sum(returns, Decimal(0)) / Decimal(period)
            result[f"volatility_{period}"] = (
                sum(((value - mean) ** 2 for value in returns), Decimal(0))
                / Decimal(period)
            ).sqrt()
    if len(history) >= 20:
        peak = closes[-20]
        drawdown = Decimal(0)
        for close in closes[-20:]:
            peak = max(peak, close)
            drawdown = min(drawdown, Decimal(close) / Decimal(peak) - 1)
        result["rolling_max_drawdown_20"] = drawdown
        recent = history[-20:]
        if all(item.high_paise is not None and item.low_paise is not None
               for item in recent):
            highs = [item.high_paise for item in recent
                     if item.high_paise is not None]
            lows = [item.low_paise for item in recent
                    if item.low_paise is not None]
            high, low = max(highs), min(lows)
            if high > low:
                result["high_low_position_20"] = (
                    Decimal(closes[-1] - low) / Decimal(high - low)
                )
    for period in (5, 20):
        if len(history) >= period:
            result[f"volume_average_{period}"] = (
                Decimal(sum(item.volume for item in history[-period:]))
                / Decimal(period)
            )
    if len(history) > 20:
        previous_mean = (
            Decimal(sum(item.volume for item in history[-21:-1])) / 20
        )
        if previous_mean:
            result["relative_volume_20"] = (
                Decimal(history[-1].volume) / previous_mean
            )
    result["rsi_14"] = _rsi(closes)
    if len(history) >= 26:
        macd_series: list[Decimal] = []
        for end in range(26, len(history) + 1):
            fast, slow = _ema(values[:end], 12), _ema(values[:end], 26)
            assert fast is not None and slow is not None
            macd_series.append(fast - slow)
        result["macd_paise"] = macd_series[-1]
        result["macd_signal_paise"] = _ema(macd_series, 9)
        if result["macd_signal_paise"] is not None:
            result["macd_histogram_paise"] = (
                macd_series[-1] - result["macd_signal_paise"]
            )
    if len(history) >= 14 and all(
        item.high_paise is not None and item.low_paise is not None
        for item in history
    ):
        true_ranges: list[Decimal] = []
        for index, item in enumerate(history):
            assert item.high_paise is not None and item.low_paise is not None
            prior_close = closes[index - 1] if index else closes[0]
            true_ranges.append(Decimal(max(
                item.high_paise - item.low_paise,
                abs(item.high_paise - prior_close),
                abs(item.low_paise - prior_close),
            )))
        atr = sum(true_ranges[:14], Decimal(0)) / 14
        for value in true_ranges[14:]:
            atr = (atr * 13 + value) / 14
        result["atr_14_paise"] = atr
    return result
