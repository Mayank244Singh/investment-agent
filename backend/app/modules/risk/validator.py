"""Fail-closed validation of proposed trades against portfolio snapshots."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.modules.risk.models import (
    PortfolioSnapshot,
    ProposedTrade,
    RejectionCode,
    RejectionReason,
    RiskLimits,
    ValidationResult,
)


class RiskValidator:
    """Apply every applicable rule without querying external state."""

    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits

    def validate(
        self, trade: ProposedTrade, snapshot: PortfolioSnapshot
    ) -> ValidationResult:
        """Return approval or all detected rejection reasons."""
        reasons: list[RejectionReason] = []
        valid_limits = self._validate_limits(reasons)
        trade_facts = self._validate_trade(trade, reasons)
        snapshot_facts = self._validate_snapshot(snapshot, reasons)

        if trade_facts is not None and snapshot_facts is not None:
            try:
                self._apply_rules(
                    trade,
                    snapshot,
                    trade_facts,
                    snapshot_facts,
                    valid_limits,
                    reasons,
                )
            except Exception:
                self._reject(
                    reasons,
                    RejectionCode.VALIDATION_ERROR,
                    "risk validation failed closed due to an internal rule error",
                )

        return ValidationResult(approved=not reasons, reasons=tuple(reasons))

    def _validate_limits(
        self, reasons: list[RejectionReason]
    ) -> dict[str, Decimal | str]:
        values: dict[str, Decimal | str | None] = {
            "max_order_percent": self._limits.max_order_percent,
            "max_stock_allocation_percent": (
                self._limits.max_stock_allocation_percent
            ),
            "max_loss_per_trade_percent": self._limits.max_loss_per_trade_percent,
            "max_daily_loss_percent": self._limits.max_daily_loss_percent,
            "daily_loss_timezone": self._limits.daily_loss_timezone,
        }
        valid: dict[str, Decimal | str] = {}
        for name, value in values.items():
            if value is None:
                self._reject(
                    reasons,
                    RejectionCode.MISSING_CONFIG,
                    f"risk configuration is missing: {name}",
                )
            elif isinstance(value, Decimal) and (value <= 0 or value > 100):
                self._reject(
                    reasons,
                    RejectionCode.INVALID_CONFIG,
                    (
                        "risk configuration must be greater than 0 and at most 100: "
                        f"{name}"
                    ),
                )
            elif name == "daily_loss_timezone":
                try:
                    ZoneInfo(str(value))
                    valid[name] = str(value)
                except ZoneInfoNotFoundError:
                    self._reject(
                        reasons,
                        RejectionCode.INVALID_CONFIG,
                        "risk daily-loss timezone is invalid",
                    )
            else:
                valid[name] = value
        return valid

    def _validate_trade(
        self, trade: ProposedTrade, reasons: list[RejectionReason]
    ) -> tuple[str, str, int, int, int, datetime] | None:
        side = trade.side.lower() if isinstance(trade.side, str) else ""
        symbol = trade.symbol.strip().upper() if isinstance(trade.symbol, str) else ""
        if side not in {"buy", "sell"}:
            self._reject(
                reasons, RejectionCode.INVALID_SIDE, "side must be buy or sell"
            )
        if not symbol or len(symbol) > 20:
            self._reject(
                reasons,
                RejectionCode.INVALID_SYMBOL,
                "symbol must contain 1 to 20 characters",
            )
        quantity = trade.quantity
        if not self._is_positive_integer(quantity):
            self._reject(
                reasons,
                RejectionCode.INVALID_QUANTITY,
                "quantity must be a positive integer",
            )
        price = trade.price_paise
        if not self._is_positive_integer(price):
            self._reject(
                reasons, RejectionCode.INVALID_PRICE, "price_paise must be positive"
            )
        fees = trade.fees_paise
        if not self._is_nonnegative_integer(fees):
            self._reject(
                reasons, RejectionCode.INVALID_FEES, "fees_paise cannot be negative"
            )
        proposed_at = trade.proposed_at_utc
        if not self._is_aware(proposed_at):
            self._reject(
                reasons,
                RejectionCode.INVALID_TIMESTAMP,
                "proposed_at_utc must include a timezone",
            )
        invalid_trade_codes = {
            RejectionCode.INVALID_SIDE,
            RejectionCode.INVALID_SYMBOL,
            RejectionCode.INVALID_QUANTITY,
            RejectionCode.INVALID_PRICE,
            RejectionCode.INVALID_FEES,
            RejectionCode.INVALID_TIMESTAMP,
        }
        if any(reason.code in invalid_trade_codes for reason in reasons):
            return None
        assert quantity is not None
        assert price is not None
        assert fees is not None
        assert proposed_at is not None
        return side, symbol, quantity, price, fees, proposed_at

    def _validate_snapshot(
        self, snapshot: PortfolioSnapshot, reasons: list[RejectionReason]
    ) -> tuple[int, int, int] | None:
        fields = {
            "cash_balance_paise": snapshot.cash_balance_paise,
            "profit_vault_balance_paise": snapshot.profit_vault_balance_paise,
            "open_cost_basis_paise": snapshot.open_cost_basis_paise,
            "holdings": snapshot.holdings,
            "realized_events": snapshot.realized_events,
        }
        for name, value in fields.items():
            if value is None:
                self._reject(
                    reasons,
                    RejectionCode.MISSING_SNAPSHOT,
                    f"portfolio snapshot is missing: {name}",
                )
        if any(value is None for value in fields.values()):
            return None

        cash = snapshot.cash_balance_paise
        vault = snapshot.profit_vault_balance_paise
        open_cost = snapshot.open_cost_basis_paise
        assert cash is not None and vault is not None and open_cost is not None
        if cash < 0 or vault < 0 or open_cost < 0 or vault > cash:
            self._reject(
                reasons,
                RejectionCode.INVALID_SNAPSHOT,
                "portfolio cash, vault, or open cost is inconsistent",
            )
            return None
        risk_capital = cash + open_cost - vault
        if risk_capital <= 0:
            self._reject(
                reasons,
                RejectionCode.INVALID_SNAPSHOT,
                "risk capital must be positive",
            )
            return None
        return cash - vault, open_cost, risk_capital

    def _apply_rules(
        self,
        trade: ProposedTrade,
        snapshot: PortfolioSnapshot,
        trade_facts: tuple[str, str, int, int, int, datetime],
        snapshot_facts: tuple[int, int, int],
        limits: dict[str, Decimal | str],
        reasons: list[RejectionReason],
    ) -> None:
        side, symbol, quantity, price, fees, proposed_at = trade_facts
        deployable_cash, _, risk_capital = snapshot_facts
        holdings = snapshot.holdings
        events = snapshot.realized_events
        assert holdings is not None and events is not None
        holding = holdings.get(symbol)
        held_quantity = holding.quantity if holding is not None else 0
        held_cost = holding.cost_basis_paise if holding is not None else 0
        if held_quantity < 0 or held_cost < 0:
            self._reject(
                reasons,
                RejectionCode.INVALID_SNAPSHOT,
                f"holding snapshot is invalid for {symbol}",
            )
            return

        if side == "sell":
            if quantity > held_quantity:
                self._reject(
                    reasons,
                    RejectionCode.INSUFFICIENT_HOLDINGS,
                    f"cannot sell {quantity}; only {held_quantity} held",
                )
            return

        order_value = quantity * price
        if order_value + fees > deployable_cash:
            self._reject(
                reasons,
                RejectionCode.INSUFFICIENT_CASH,
                "buy cost exceeds deployable cash",
            )
        self._check_percent_limit(
            order_value,
            risk_capital,
            limits.get("max_order_percent"),
            RejectionCode.MAX_ORDER_SIZE,
            "proposed order exceeds the maximum order size",
            reasons,
        )
        self._check_percent_limit(
            held_cost + order_value + fees,
            risk_capital,
            limits.get("max_stock_allocation_percent"),
            RejectionCode.MAX_STOCK_ALLOCATION,
            "post-trade holding exceeds the maximum single-stock allocation",
            reasons,
        )

        stop_loss = trade.stop_loss_paise
        valid_stop = (
            stop_loss is not None
            and self._is_positive_integer(stop_loss)
            and stop_loss < price
        )
        if stop_loss is None:
            self._reject(
                reasons,
                RejectionCode.STOP_LOSS_REQUIRED,
                "a stop loss is required for buys",
            )
        elif not valid_stop:
            self._reject(
                reasons,
                RejectionCode.INVALID_STOP_LOSS,
                "stop loss must be positive and below the entry price",
            )
        if valid_stop:
            assert stop_loss is not None
            self._check_percent_limit(
                (price - stop_loss) * quantity,
                risk_capital,
                limits.get("max_loss_per_trade_percent"),
                RejectionCode.MAX_LOSS_PER_TRADE,
                "stop-loss exposure exceeds the maximum loss per trade",
                reasons,
            )

        timezone_name = limits.get("daily_loss_timezone")
        daily_limit = limits.get("max_daily_loss_percent")
        if isinstance(timezone_name, str) and isinstance(daily_limit, Decimal):
            timezone = ZoneInfo(timezone_name)
            local_trade_date = proposed_at.astimezone(timezone).date()
            daily_pnl = 0
            for event in events:
                if not self._is_aware(event.realized_at_utc):
                    raise ValueError("realised P&L timestamps must include a timezone")
                event_local_date = event.realized_at_utc.astimezone(timezone).date()
                if event_local_date == local_trade_date:
                    daily_pnl += event.realized_pnl_paise
            daily_loss = max(-daily_pnl, 0)
            if daily_loss >= self._percent_of(risk_capital, daily_limit):
                self._reject(
                    reasons,
                    RejectionCode.MAX_DAILY_LOSS,
                    "maximum daily realised loss has been reached",
                )

    def _check_percent_limit(
        self,
        value: int,
        capital: int,
        percent: Decimal | str | None,
        code: RejectionCode,
        message: str,
        reasons: list[RejectionReason],
    ) -> None:
        if isinstance(percent, Decimal) and value > self._percent_of(capital, percent):
            self._reject(reasons, code, message)

    @staticmethod
    def _percent_of(value: int, percent: Decimal) -> int:
        return int(Decimal(value) * percent / Decimal(100))

    @staticmethod
    def _is_positive_integer(value: int | None) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    @staticmethod
    def _is_nonnegative_integer(value: int | None) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    @staticmethod
    def _is_aware(value: datetime | None) -> bool:
        return (
            value is not None
            and value.tzinfo is not None
            and value.utcoffset() is not None
        )

    @staticmethod
    def _reject(
        reasons: list[RejectionReason], code: RejectionCode, message: str
    ) -> None:
        reasons.append(RejectionReason(code=code, message=message))
