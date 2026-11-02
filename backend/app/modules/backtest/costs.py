"""Integer-paise delivery charge model with per-component half-up rounding."""

import json
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal


def _paise(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class CostProfile:
    broker: str
    rates_as_of: date
    source_note: str
    verified: bool
    brokerage_bps: Decimal
    stt_buy_bps: Decimal
    stt_sell_bps: Decimal
    exchange_bps: Decimal
    regulator_bps: Decimal
    stamp_buy_bps: Decimal
    gst_bps: Decimal
    depository_sell_paise: int
    slippage_bps: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.verified, bool):
            raise ValueError("cost profile verification flag must be boolean")
        if not isinstance(self.depository_sell_paise, int) or isinstance(
            self.depository_sell_paise, bool
        ):
            raise ValueError("depository charge must be integer paise")
        rates = (
            self.brokerage_bps,
            self.stt_buy_bps,
            self.stt_sell_bps,
            self.exchange_bps,
            self.regulator_bps,
            self.stamp_buy_bps,
            self.gst_bps,
            self.slippage_bps,
        )
        if (
            not self.broker
            or not self.source_note
            or any(not rate.is_finite() or rate < 0 for rate in rates)
            or self.depository_sell_paise < 0
        ):
            raise ValueError("invalid cost profile")

    @classmethod
    def from_json(cls, path: Path) -> "CostProfile":
        value = json.loads(path.read_text(encoding="utf-8"))
        rate_fields = (
            "brokerage_bps",
            "stt_buy_bps",
            "stt_sell_bps",
            "exchange_bps",
            "regulator_bps",
            "stamp_buy_bps",
            "gst_bps",
            "slippage_bps",
        )
        if any(not isinstance(value[field], str) for field in rate_fields):
            raise ValueError("cost rates must be decimal strings")
        return cls(
            broker=value["broker"],
            rates_as_of=date.fromisoformat(value["rates_as_of"]),
            source_note=value["source_note"],
            verified=value["verified"],
            brokerage_bps=Decimal(value["brokerage_bps"]),
            stt_buy_bps=Decimal(value["stt_buy_bps"]),
            stt_sell_bps=Decimal(value["stt_sell_bps"]),
            exchange_bps=Decimal(value["exchange_bps"]),
            regulator_bps=Decimal(value["regulator_bps"]),
            stamp_buy_bps=Decimal(value["stamp_buy_bps"]),
            gst_bps=Decimal(value["gst_bps"]),
            depository_sell_paise=value["depository_sell_paise"],
            slippage_bps=Decimal(value["slippage_bps"]),
        )


@dataclass(frozen=True)
class Charges:
    brokerage_paise: int
    stt_paise: int
    exchange_paise: int
    regulator_paise: int
    stamp_paise: int
    gst_paise: int
    depository_paise: int

    @property
    def total_paise(self) -> int:
        return sum(
            (
                self.brokerage_paise,
                self.stt_paise,
                self.exchange_paise,
                self.regulator_paise,
                self.stamp_paise,
                self.gst_paise,
                self.depository_paise,
            )
        )


def charges(
    profile: CostProfile, side: Literal["buy", "sell"], quantity: int, price_paise: int
) -> Charges:
    if quantity <= 0 or price_paise <= 0:
        raise ValueError("quantity and price must be positive")
    gross = Decimal(quantity * price_paise)

    def rate(bps: Decimal) -> int:
        return _paise(gross * bps / Decimal(10_000))

    brokerage = rate(profile.brokerage_bps)
    exchange = rate(profile.exchange_bps)
    regulator = rate(profile.regulator_bps)
    return Charges(
        brokerage,
        rate(profile.stt_buy_bps if side == "buy" else profile.stt_sell_bps),
        exchange,
        regulator,
        rate(profile.stamp_buy_bps) if side == "buy" else 0,
        _paise(
            Decimal(brokerage + exchange + regulator)
            * profile.gst_bps
            / Decimal(10_000)
        ),
        profile.depository_sell_paise if side == "sell" else 0,
    )


def slipped_price(
    profile: CostProfile, side: Literal["buy", "sell"], reference_paise: int
) -> int:
    factor = Decimal(1) + (Decimal(1) if side == "buy" else Decimal(-1)) * (
        profile.slippage_bps / Decimal(10_000)
    )
    result = _paise(Decimal(reference_paise) * factor)
    if result <= 0:
        raise ValueError("slippage produced non-positive price")
    return result
