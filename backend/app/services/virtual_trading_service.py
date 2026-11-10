"""Isolated, synthetic-only virtual orders using the existing FIFO ledger."""

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, Settings
from app.db.models.ledger import CapitalMovement, LedgerTransaction, LotRealization
from app.db.models.prices import DailyPrice, SourceObservation
from app.db.models.virtual import VirtualAccount, VirtualOrderAttempt
from app.modules.backtest.costs import CostProfile, charges, slipped_price
from app.modules.portfolio import LedgerService
from app.modules.risk import (
    HoldingSnapshot,
    PortfolioSnapshot,
    ProposedTrade,
    RealizedPnlEvent,
    RiskLimits,
    RiskValidator,
)

IST = ZoneInfo("Asia/Kolkata")
FIXTURE_PRICES: dict[str, int] = {
    "RELIANCE": 10_000,
    "TCS": 12_000,
    "INFY": 8_000,
    "HDFCBANK": 9_000,
    "ICICIBANK": 7_000,
}
FIXTURE_SHA256 = hashlib.sha256(
    json.dumps(FIXTURE_PRICES, sort_keys=True).encode("utf-8")
).hexdigest()
PROFILE_PATH = PROJECT_ROOT / "backend/config/backtest-cost-placeholder-2026-11-02.json"


class VirtualUnavailable(ValueError):
    """Virtual storage or its identity marker is absent or unsafe."""


class VirtualIdempotencyConflict(ValueError):
    """A key was already used with another request."""


@dataclass(frozen=True)
class SyntheticQuote:
    symbol: str
    price_paise: int
    price_date: date
    source_label: str = "SYNTHETIC DEMO — NOT MARKET DATA"
    fixture_sha256: str = FIXTURE_SHA256


@dataclass(frozen=True)
class VirtualOrder:
    side: Literal["buy", "sell"]
    symbol: str
    quantity: int
    stop_loss_paise: int | None = None
    order_type: Literal["market"] = "market"


def virtual_path(url: str | None, real_url: str) -> Path:
    """Accept only a preexisting, dedicated SQLite virtual file."""
    if not url or not url.startswith("sqlite:////"):
        raise VirtualUnavailable("VIRTUAL_DATABASE_URL must be an absolute SQLite path")
    path = Path(url.removeprefix("sqlite:///"))
    path = path.resolve()
    real_path = (
        Path(real_url.removeprefix("sqlite:///")).resolve()
        if real_url.startswith("sqlite:///")
        else None
    )
    if (
        path == real_path
        or path.name in {"investment_agent.db", "pilot.db", "provisional.db"}
        or "InvestmentAgentData/pilots" in str(path)
        or not path.is_file()
    ):
        raise VirtualUnavailable(
            "virtual database is absent or overlaps protected storage"
        )
    return path


@lru_cache(maxsize=8)
def _engine(path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 10})
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection: object, _: object) -> None:
        connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[attr-defined]

    return engine


def virtual_session(settings: Settings) -> Session:
    path = virtual_path(settings.virtual_database_url, settings.database_url)
    session = Session(_engine(path), autoflush=False)
    try:
        _require_account(session)
        session.rollback()
    except Exception:
        session.close()
        raise
    return session


def _require_account(session: Session) -> VirtualAccount:
    try:
        account = session.get(VirtualAccount, 1)
        if (
            account is None or account.fixture_sha256 != FIXTURE_SHA256
            or account.currency != "INR" or account.initial_capital_paise != 1_000_000
        ):
            raise VirtualUnavailable(
                "virtual account is not seeded with the expected fixture"
            )
        if (
            session.scalar(select(DailyPrice.id).limit(1)) is not None
            or session.scalar(select(SourceObservation.id).limit(1)) is not None
        ):
            raise VirtualUnavailable("virtual database contains canonical market data")
        return account
    except VirtualUnavailable:
        raise
    except Exception as exc:
        raise VirtualUnavailable("virtual database schema is not ready") from exc


def seed_virtual_account(
    path: Path, real_url: str, *, now: datetime | None = None
) -> bool:
    """Explicit, idempotent seed after migrations; never reset existing records."""
    resolved = virtual_path(f"sqlite:///{path.resolve()}", real_url)
    with Session(_engine(resolved), autoflush=False) as session:
        try:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            existing = session.get(VirtualAccount, 1)
            if existing is not None:
                _require_account(session)
                opening = session.scalar(
                    select(CapitalMovement).where(
                        CapitalMovement.idempotency_key == "virtual-opening-v1"
                    )
                )
                if opening is None or opening.amount_paise != 1_000_000:
                    raise VirtualUnavailable("virtual opening deposit is inconsistent")
                session.commit()
                return False
            if any(
                session.scalar(select(model.id).limit(1)) is not None
                for model in (
                    CapitalMovement,
                    LedgerTransaction,
                    DailyPrice,
                    SourceObservation,
                )
            ):
                raise VirtualUnavailable("refusing to seed a nonempty database")
            timestamp = now or datetime.now(UTC)
            session.add(
                VirtualAccount(
                    id=1,
                    currency="INR",
                    initial_capital_paise=1_000_000,
                    fixture_sha256=FIXTURE_SHA256,
                    seeded_at_utc=timestamp,
                )
            )
            LedgerService(session).record_deposit(
                1_000_000,
                timestamp,
                "Synthetic virtual opening capital",
                "virtual-opening-v1",
                commit=False,
            )
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise


def _quote(
    symbol: str, today: date, quotes: dict[str, SyntheticQuote] | None
) -> SyntheticQuote | None:
    if quotes is not None:
        return quotes.get(symbol)
    price = FIXTURE_PRICES.get(symbol)
    return SyntheticQuote(symbol, price, today) if price is not None else None


def list_quotes(now: datetime | None = None) -> list[dict[str, object]]:
    today = (now or datetime.now(UTC)).astimezone(IST).date()
    return [
        asdict(SyntheticQuote(symbol, price, today))
        for symbol, price in FIXTURE_PRICES.items()
    ]


def _snapshot(session: Session) -> PortfolioSnapshot:
    summary = LedgerService(session).get_summary()
    events = session.scalars(select(LotRealization).order_by(LotRealization.id))
    return PortfolioSnapshot(
        summary.cash_balance_paise,
        summary.profit_vault_balance_paise,
        summary.open_cost_basis_paise,
        {
            item.symbol: HoldingSnapshot(item.quantity, item.cost_basis_paise)
            for item in summary.holdings
        },
        tuple(
            RealizedPnlEvent(
                item.realized_pnl_paise,
                item.realized_at_utc.replace(tzinfo=UTC)
                if item.realized_at_utc.tzinfo is None
                else item.realized_at_utc.astimezone(UTC),
            )
            for item in events
        ),
    )


def account_summary(
    session: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    quotes: dict[str, SyntheticQuote] | None = None,
) -> dict[str, object]:
    _require_account(session)
    summary = LedgerService(session).get_summary()
    today = (now or datetime.now(UTC)).astimezone(IST).date()
    holdings: list[dict[str, object]] = []
    for holding in summary.holdings:
        quote = _quote(holding.symbol, today, quotes)
        available = (
            quote is not None
            and quote.fixture_sha256 == FIXTURE_SHA256
            and quote.symbol == holding.symbol
            and quote.price_paise > 0
            and 0
            <= (today - quote.price_date).days
            <= settings.virtual_quote_staleness_days
        )
        market = holding.quantity * quote.price_paise if available and quote else None
        holdings.append(
            {
                "symbol": holding.symbol,
                "quantity": holding.quantity,
                "cost_basis_paise": holding.cost_basis_paise,
                "market_value_paise": market,
                "unrealized_pnl_paise": market - holding.cost_basis_paise
                if market is not None
                else None,
                "price_date": quote.price_date.isoformat() if quote else None,
                "status": "priced" if available else "unavailable",
            }
        )
    return {
        "mode": "VIRTUAL TRADING — SYNTHETIC DEMO — NOT MARKET DATA",
        "currency": "INR",
        "initial_capital_paise": 1_000_000,
        "cash_balance_paise": summary.cash_balance_paise,
        "deployable_cash_paise": summary.deployable_cash_paise,
        "realized_pnl_paise": summary.realized_pnl_paise,
        "profit_vault_balance_paise": summary.profit_vault_balance_paise,
        "holdings": holdings,
    }


def _payload_hash(order: VirtualOrder) -> str:
    return hashlib.sha256(
        json.dumps(asdict(order), sort_keys=True).encode()
    ).hexdigest()


def preview_order(
    session: Session,
    order: VirtualOrder,
    settings: Settings,
    *,
    now: datetime | None = None,
    quotes: dict[str, SyntheticQuote] | None = None,
) -> dict[str, object]:
    """Read-only preview. Submission must independently run this again."""
    _require_account(session)
    timestamp = now or datetime.now(UTC)
    today = timestamp.astimezone(IST).date()
    reasons: list[dict[str, str]] = []
    quote = _quote(order.symbol, today, quotes)
    if quote is None or order.symbol not in FIXTURE_PRICES:
        reasons.append(
            {
                "code": "missing_synthetic_quote",
                "message": "No approved synthetic EQ quote",
            }
        )
    elif (
        quote.symbol != order.symbol
        or quote.source_label != "SYNTHETIC DEMO — NOT MARKET DATA"
        or quote.fixture_sha256 != FIXTURE_SHA256
        or quote.price_paise <= 0
        or not 0
        <= (today - quote.price_date).days
        <= settings.virtual_quote_staleness_days
    ):
        reasons.append(
            {
                "code": "invalid_or_stale_synthetic_quote",
                "message": "Synthetic quote is invalid or stale",
            }
        )
    profile = replace(
        CostProfile.from_json(PROFILE_PATH), slippage_bps=settings.virtual_slippage_bps
    )
    fill = (
        slipped_price(profile, order.side, quote.price_paise)
        if quote and not reasons
        else None
    )
    fee = charges(profile, order.side, order.quantity, fill) if fill else None
    snapshot = _snapshot(session)
    proposed = ProposedTrade(
        order.side,
        order.symbol,
        order.quantity,
        fill,
        timestamp,
        order.stop_loss_paise,
        fee.total_paise if fee else None,
    )
    result = RiskValidator(RiskLimits.from_settings(settings)).validate(
        proposed, snapshot
    )
    reasons.extend(
        {"code": item.code.value, "message": item.message} for item in result.reasons
    )
    planned_loss: int | None = None
    if (
        order.side == "buy"
        and fill
        and fee
        and order.stop_loss_paise
        and 0 < order.stop_loss_paise < fill
    ):
        exit_price = slipped_price(profile, "sell", order.stop_loss_paise)
        exit_fee = charges(profile, "sell", order.quantity, exit_price).total_paise
        planned_loss = (fill - exit_price) * order.quantity + fee.total_paise + exit_fee
        if planned_loss > settings.virtual_max_planned_loss_paise:
            reasons.append(
                {
                    "code": "virtual_planned_risk",
                    "message": "Planned loss exceeds the configured virtual cap",
                }
            )
    if order.side == "buy" and fill and fee and snapshot.holdings is not None:
        held = snapshot.holdings.get(order.symbol)
        exposure = (
            (held.cost_basis_paise if held else 0)
            + order.quantity * fill
            + fee.total_paise
        )
        if exposure > settings.virtual_max_stock_exposure_paise:
            reasons.append(
                {
                    "code": "virtual_stock_exposure",
                    "message": "Post-buy exposure exceeds virtual cap",
                }
            )
        if snapshot.realized_events is not None:
            daily_pnl = sum(
                event.realized_pnl_paise
                for event in snapshot.realized_events
                if event.realized_at_utc.astimezone(IST).date() == today
            )
            if max(-daily_pnl, 0) >= settings.virtual_max_daily_loss_paise:
                reasons.append(
                    {
                        "code": "virtual_daily_loss",
                        "message": "Daily realised loss reached virtual threshold",
                    }
                )
    return {
        "approved": not reasons,
        "reasons": reasons,
        "symbol": order.symbol,
        "side": order.side,
        "quantity": order.quantity,
        "quote_date": quote.price_date.isoformat() if quote else None,
        "source_label": quote.source_label
        if quote
        else "SYNTHETIC DEMO — NOT MARKET DATA",
        "fixture_sha256": quote.fixture_sha256 if quote else None,
        "reference_paise": quote.price_paise if quote else None,
        "fill_paise": fill,
        "fees_paise": fee.total_paise if fee else 0,
        "charges": asdict(fee) if fee else None,
        "planned_loss_paise": planned_loss,
        "cash_impact_paise": (
            -(order.quantity * fill + fee.total_paise)
            if order.side == "buy"
            else order.quantity * fill - fee.total_paise
        )
        if fill and fee
        else None,
        "stop_warning": "Stop price does not guarantee the maximum realised loss.",
    }


def submit_order(
    session: Session,
    order: VirtualOrder,
    key: str,
    settings: Settings,
    *,
    now: datetime | None = None,
    quotes: dict[str, SyntheticQuote] | None = None,
) -> dict[str, object]:
    """Serialize snapshot, risk decision, FIFO fill and audit in one transaction."""
    if not key or len(key) > 128:
        raise ValueError("idempotency key must contain 1 to 128 characters")
    request_hash = _payload_hash(order)
    timestamp = now or datetime.now(UTC)
    try:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        _require_account(session)
        existing = session.scalar(
            select(VirtualOrderAttempt).where(
                VirtualOrderAttempt.idempotency_key == key
            )
        )
        if existing is not None:
            if existing.request_sha256 != request_hash:
                raise VirtualIdempotencyConflict(
                    "idempotency key belongs to another virtual order"
                )
            outcome = _attempt_response(existing)
            session.commit()
            outcome["account"] = account_summary(
                session, settings, now=timestamp, quotes=quotes
            )
            return outcome
        decision = preview_order(session, order, settings, now=timestamp, quotes=quotes)
        ledger_id: int | None = None
        if decision["approved"]:
            fill = decision["fill_paise"]
            assert isinstance(fill, int)
            fee = decision["fees_paise"]
            assert isinstance(fee, int)
            ledger = LedgerService(session)
            if order.side == "buy":
                ledger_id = ledger.record_buy(
                    order.symbol,
                    order.quantity,
                    fill,
                    timestamp,
                    fee,
                    "Synthetic virtual fill",
                    key,
                    commit=False,
                ).transaction_id
            else:
                ledger_id = ledger.record_sell(
                    order.symbol,
                    order.quantity,
                    fill,
                    timestamp,
                    fee,
                    "Synthetic virtual fill",
                    key,
                    commit=False,
                ).transaction_id
        attempt = VirtualOrderAttempt(
            idempotency_key=key,
            request_sha256=request_hash,
            attempted_at_utc=timestamp,
            side=order.side,
            symbol=order.symbol,
            quantity=order.quantity,
            stop_loss_paise=order.stop_loss_paise,
            quote_date=date.fromisoformat(decision["quote_date"])
            if isinstance(decision["quote_date"], str)
            else None,
            quote_sha256=decision["fixture_sha256"],
            reference_paise=decision["reference_paise"],
            fill_paise=decision["fill_paise"],
            fee_paise=decision["fees_paise"],
            charges_json=json.dumps(decision["charges"], sort_keys=True),
            planned_loss_paise=decision["planned_loss_paise"],
            rejection_codes_json=json.dumps(decision["reasons"], sort_keys=True),
            status="filled" if decision["approved"] else "rejected",
            ledger_transaction_id=ledger_id,
        )
        session.add(attempt)
        session.flush()
        result = _attempt_response(attempt)
        session.commit()
        result["account"] = account_summary(
            session, settings, now=timestamp, quotes=quotes
        )
        return result
    except Exception:
        session.rollback()
        raise


def _attempt_response(attempt: VirtualOrderAttempt) -> dict[str, object]:
    return {
        "id": attempt.id,
        "status": attempt.status,
        "side": attempt.side,
        "symbol": attempt.symbol,
        "quantity": attempt.quantity,
        "attempted_at_utc": attempt.attempted_at_utc.isoformat(),
        "quote_date": attempt.quote_date.isoformat() if attempt.quote_date else None,
        "reference_paise": attempt.reference_paise,
        "fill_paise": attempt.fill_paise,
        "fees_paise": attempt.fee_paise,
        "charges": json.loads(attempt.charges_json) if attempt.charges_json else None,
        "planned_loss_paise": attempt.planned_loss_paise,
        "reasons": json.loads(attempt.rejection_codes_json),
        "ledger_transaction_id": attempt.ledger_transaction_id,
        "source_label": "SYNTHETIC DEMO — NOT MARKET DATA",
    }


def order_history(session: Session) -> list[dict[str, object]]:
    _require_account(session)
    return [
        _attempt_response(item)
        for item in session.scalars(
            select(VirtualOrderAttempt).order_by(VirtualOrderAttempt.id.desc())
        )
    ]
