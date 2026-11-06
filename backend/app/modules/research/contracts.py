"""Typed, research-only contracts shared across strategy timeframes.

These contracts do not submit orders or replace the production risk validator.
"""

from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from enum import StrEnum


class StrategyFamily(StrEnum):
    INTRADAY = "intraday"
    SWING = "swing"
    LONG_TERM = "long_term"


class Timeframe(StrEnum):
    MINUTE_1 = "1m"
    MINUTE_5 = "5m"
    MINUTE_15 = "15m"
    DAY = "1d"

    @property
    def minutes(self) -> int:
        if self is Timeframe.DAY:
            raise ValueError("daily candles have no fixed minute duration")
        return {Timeframe.MINUTE_1: 1, Timeframe.MINUTE_5: 5,
                Timeframe.MINUTE_15: 15}[self]


@dataclass(frozen=True)
class ResearchFeeSchedule:
    """Versioned rates, supplied by a verified source before execution research."""

    version: str
    rates_as_of: date
    source_note: str
    brokerage_rate: Decimal
    stt_rate: Decimal
    exchange_rate: Decimal
    gst_rate: Decimal
    sebi_rate: Decimal
    stamp_rate: Decimal

    def __post_init__(self) -> None:
        if not self.version or not self.source_note:
            raise ValueError("fee schedule needs version and source")
        for name in ("brokerage_rate", "stt_rate", "exchange_rate",
                     "gst_rate", "sebi_rate", "stamp_rate"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"invalid {name}")


@dataclass(frozen=True)
class IntradayExecutionContract:
    """Assumptions that a later simulator must model; no fills are generated here."""

    fee_schedule: ResearchFeeSchedule
    spread_paise: int | None
    slippage_bps: Decimal | None
    order_latency_ms: int | None
    partial_fills_supported: bool
    liquidity_limit_fraction: Decimal | None
    market_impact_model: str | None
    mandatory_square_off: bool

    def __post_init__(self) -> None:
        if self.spread_paise is not None and self.spread_paise < 0:
            raise ValueError("spread must be non-negative")
        if self.order_latency_ms is not None and self.order_latency_ms < 0:
            raise ValueError("latency must be non-negative")
        if self.slippage_bps is not None and (
            not self.slippage_bps.is_finite() or self.slippage_bps < 0
        ):
            raise ValueError("slippage must be non-negative")
        if self.liquidity_limit_fraction is not None and not (
            Decimal(0) < self.liquidity_limit_fraction <= Decimal(1)
        ):
            raise ValueError("liquidity fraction must be in (0, 1]")


@dataclass(frozen=True)
class IntradayRiskResearchContract:
    """Future research limits; production risk validation remains independent."""

    max_loss_per_trade_paise: int
    max_daily_loss_paise: int
    max_position_paise: int
    max_open_positions: int
    max_turnover_paise: int
    max_exposure_paise: int
    max_consecutive_losses: int
    trading_cutoff_local: time
    mandatory_square_off: bool
    halt_on_circuit_breaker: bool

    def __post_init__(self) -> None:
        for name in ("max_loss_per_trade_paise", "max_daily_loss_paise",
                     "max_position_paise", "max_open_positions",
                     "max_turnover_paise", "max_exposure_paise",
                     "max_consecutive_losses"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"invalid {name}")
        if self.trading_cutoff_local.tzinfo is not None:
            raise ValueError("cutoff is exchange-local wall time")
