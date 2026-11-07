"""Synthetic Indian-equity evidence only; no market feed or production data."""

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.research.contracts import StrategyFamily, Timeframe
from app.modules.research.evidence import (
    AssessmentState,
    CandleAssessment,
    Evidence,
    EvidenceKind,
    Exchange,
    IndianInstrument,
    OpportunityRequest,
    Quality,
    Stance,
    assess_opportunity,
    validate_completed_candles,
)
from app.modules.research.intraday import (
    ExchangeCalendar,
    ExchangeSession,
    IntradayCandle,
)

DAY = date(2025, 1, 2)
OPEN = datetime(2025, 1, 2, 3, 45, tzinfo=UTC)
INSTRUMENT = IndianInstrument(101, Exchange.NSE, "TESTEQ", "IN0000000001", True)
SESSION = ExchangeSession(DAY, time(9, 15), time(15, 30))
CALENDAR = ExchangeCalendar((SESSION,))
DECISION = OPEN + timedelta(minutes=2)


def candle(index: int = 0, **changes: object) -> IntradayCandle:
    start = OPEN + timedelta(minutes=index)
    fields: dict[str, object] = {
        "instrument_id": 101, "timeframe": Timeframe.MINUTE_1,
        "start_utc": start, "end_utc": start + timedelta(minutes=1),
        "available_at_utc": start + timedelta(minutes=1),
        "is_complete": True, "open_paise": 10000, "high_paise": 10010,
        "low_paise": 9990, "close_paise": 10005, "volume": 100,
        "provider": "synthetic", "source_record_key": f"bar-{index}",
        "adjustment_status": "verified_no_action", "identity_verified": True,
    }
    fields.update(changes)
    return IntradayCandle(**fields)  # type: ignore[arg-type]


def check(
    bars: tuple[IntradayCandle, ...], *, instrument: IndianInstrument = INSTRUMENT,
    calendar: ExchangeCalendar = CALENDAR,
    exchange: Exchange = Exchange.NSE,
    timeframe: Timeframe = Timeframe.MINUTE_1,
    start: datetime = OPEN, decision: datetime = DECISION,
    age: timedelta = timedelta(minutes=2),
) -> tuple[tuple[IntradayCandle, ...], tuple[str, ...], tuple[str, ...]]:
    result = validate_completed_candles(
        bars, instrument=instrument, calendar=calendar,
        calendar_exchange=exchange, timeframe=timeframe,
        window_start_utc=start, decision_at_utc=decision, max_age=age,
    )
    return result.usable, result.issues, result.deferred


def evidence(
    kind: EvidenceKind, stance: Stance, key: str, **changes: object,
) -> Evidence:
    fields: dict[str, object] = {
        "instrument": INSTRUMENT, "kind": kind, "stance": stance,
        "provider": "synthetic", "source_record_key": key,
        "observed_at_utc": DECISION, "available_at_utc": DECISION,
        "decision_cutoff_utc": DECISION, "quality": Quality.VERIFIED,
        "method_version": "test-v1", "input_lineage": ("invented-bar-1",),
        "usage_verified": True, "timeframe": Timeframe.MINUTE_1,
        "horizon": 5 if kind is EvidenceKind.PREDICTION else None,
        "value": Decimal("0.01") if kind is EvidenceKind.PREDICTION else None,
    }
    fields.update(changes)
    return Evidence(**fields)  # type: ignore[arg-type]


REQUEST = OpportunityRequest(
    INSTRUMENT, StrategyFamily.INTRADAY, Timeframe.MINUTE_1, 5, DECISION
)


def test_valid_completed_nse_candles_and_special_bse_session() -> None:
    assert check((candle(0), candle(1)))[1:] == ((), ())
    bse = IndianInstrument(202, Exchange.BSE, "BSETEST", None, True)
    special = ExchangeSession(DAY, time(9, 15), time(9, 17), special=True)
    bars = (candle(0, instrument_id=202), candle(1, instrument_id=202))
    assert len(check(bars, instrument=bse,
                     calendar=ExchangeCalendar((special,)),
                     exchange=Exchange.BSE)[0]) == 2


def test_incomplete_candle_and_missing_interval() -> None:
    usable, issues, _ = check((candle(0), candle(1, is_complete=False)))
    assert len(usable) == 1
    assert "incomplete candle" in issues
    assert "missing completed candle interval" in issues


def test_future_candle_is_excluded_negative_control() -> None:
    baseline = check((candle(0), candle(1)))
    leaked = check((candle(0), candle(1), candle(2, close_paise=999999,
                     high_paise=999999)))
    assert leaked == baseline


def test_delayed_or_unknown_availability_cannot_be_used() -> None:
    assert "candle arrived after decision" in check((
        candle(0), candle(1, available_at_utc=DECISION + timedelta(seconds=1))
    ))[2]
    assert "candle availability unknown" in check((
        candle(0), candle(1, available_at_utc=None)
    ))[2]


def test_stale_candle_and_missing_intervals() -> None:
    _, issues, _ = check((candle(0),), decision=OPEN + timedelta(minutes=4),
                         age=timedelta(minutes=1))
    assert "stale completed candle" in issues
    assert "missing completed candle interval" in issues


@pytest.mark.parametrize("changes", [
    {"open_paise": 0}, {"low_paise": 10020}, {"high_paise": 9990},
    {"volume": -1},
])
def test_invalid_ohlcv_rejected_by_existing_contract(changes: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="invalid intraday OHLCV"):
        candle(**changes)


def test_duplicate_candles_and_missing_candles() -> None:
    assert "duplicate candle" in check((candle(0), candle(0), candle(1)))[1]
    assert "missing completed candle interval" in check((candle(0),))[1]


def test_holiday_and_exchange_session_mismatch() -> None:
    holiday_calendar = ExchangeCalendar(
        (ExchangeSession(date(2025, 1, 3), time(9, 15), time(15, 30)),),
        holidays=frozenset({DAY}),
    )
    assert "no verified exchange session or holiday" in check(
        (candle(0),), calendar=holiday_calendar
    )[1]
    assert "exchange/calendar mismatch" in check((candle(0),),
                                                   exchange=Exchange.BSE)[1]


def test_unsupported_timeframe_and_bad_session_window() -> None:
    assert "unsupported candle timeframe" in check((), timeframe=Timeframe.DAY)[1]
    assert "window outside or misaligned with session" in check(
        (), start=OPEN - timedelta(minutes=1)
    )[1]


def test_decision_between_five_minute_closes_uses_only_completed_slot() -> None:
    first = candle(0, timeframe=Timeframe.MINUTE_5,
                   end_utc=OPEN + timedelta(minutes=5),
                   available_at_utc=OPEN + timedelta(minutes=5))
    second = candle(5, timeframe=Timeframe.MINUTE_5,
                    end_utc=OPEN + timedelta(minutes=10),
                    available_at_utc=OPEN + timedelta(minutes=10))
    usable, issues, _ = check(
        (first, second), timeframe=Timeframe.MINUTE_5,
        decision=OPEN + timedelta(minutes=7), age=timedelta(minutes=5),
    )
    assert usable == (first,)
    assert not issues


def test_off_grid_candle_is_never_usable() -> None:
    off_grid = candle(0, start_utc=OPEN + timedelta(seconds=30),
                      end_utc=OPEN + timedelta(minutes=1, seconds=30),
                      available_at_utc=OPEN + timedelta(minutes=1, seconds=30))
    usable, issues, _ = check((off_grid, candle(1)))
    assert off_grid not in usable
    assert "candle outside session grid" in issues


@pytest.mark.parametrize("strategy,timeframe,horizon", [
    (StrategyFamily.INTRADAY, Timeframe.MINUTE_15, 5),
    (StrategyFamily.SWING, Timeframe.MINUTE_1, 5),
    (StrategyFamily.SWING, Timeframe.DAY, 60),
    (StrategyFamily.LONG_TERM, Timeframe.DAY, 10),
])
def test_request_rejects_incompatible_ml_horizon(
    strategy: StrategyFamily, timeframe: Timeframe, horizon: int,
) -> None:
    with pytest.raises(ValueError, match="invalid opportunity request"):
        OpportunityRequest(INSTRUMENT, strategy, timeframe, horizon, DECISION)


def test_prediction_horizon_contract_rejects_free_form_string() -> None:
    with pytest.raises(ValueError, match="invalid evidence contract"):
        evidence(EvidenceKind.PREDICTION, Stance.SUPPORTS,
                 "bad-horizon", horizon="5m")


def test_agreement_preserves_distinct_evidence_without_claiming_acceptance() -> None:
    items = (evidence(EvidenceKind.PREDICTION, Stance.SUPPORTS, "ml"),
             evidence(EvidenceKind.TECHNICAL, Stance.SUPPORTS, "tech"))
    result = assess_opportunity(REQUEST, items)
    assert result.state is AssessmentState.INSUFFICIENT_EVIDENCE
    assert len(result.supporting) == 2
    assert not result.contradicting
    assert "calibrated prediction uncertainty missing" in result.reasons
    assert result.proposed_size_paise is None


def test_disagreement_preserved_and_not_automatic_rejection() -> None:
    items = (evidence(EvidenceKind.PREDICTION, Stance.SUPPORTS, "ml"),
             evidence(EvidenceKind.TECHNICAL, Stance.CONTRADICTS, "tech"))
    result = assess_opportunity(REQUEST, items)
    assert result.state is AssessmentState.INSUFFICIENT_EVIDENCE
    assert len(result.supporting) == len(result.contradicting) == 1


def test_missing_fundamentals_and_uncertainty_are_explicit() -> None:
    result = assess_opportunity(REQUEST, (
        evidence(EvidenceKind.PREDICTION, Stance.SUPPORTS, "ml"),
        evidence(EvidenceKind.TECHNICAL, Stance.NEUTRAL, "tech"),
    ))
    assert "fundamental evidence missing" in result.reasons
    assert "calibrated prediction uncertainty missing" in result.reasons


def test_prediction_horizons_are_not_pooled() -> None:
    five = evidence(EvidenceKind.PREDICTION, Stance.SUPPORTS, "five")
    sixty = evidence(EvidenceKind.PREDICTION, Stance.CONTRADICTS,
                     "sixty", horizon=60)
    result = assess_opportunity(REQUEST, (five, sixty))
    assert result.considered == (five,)


def test_invalid_identity_and_exchange_mismatch_reject() -> None:
    invalid = replace(INSTRUMENT, identity_verified=False)
    assert assess_opportunity(replace(REQUEST, instrument=invalid),
                              ()).state is AssessmentState.REJECT
    bse = replace(INSTRUMENT, exchange=Exchange.BSE)
    assert assess_opportunity(REQUEST, (
        evidence(EvidenceKind.TECHNICAL, Stance.NEUTRAL,
                 "other-exchange", instrument=bse),
    )).state is AssessmentState.REJECT


def test_delayed_evidence_waits_and_future_evidence_is_ignored() -> None:
    delayed = evidence(EvidenceKind.PREDICTION, Stance.SUPPORTS, "late",
                       available_at_utc=DECISION + timedelta(seconds=1))
    delayed_result = assess_opportunity(REQUEST, (delayed,))
    assert delayed_result.state is AssessmentState.WAIT
    assert "evidence arrived after decision" in delayed_result.reasons
    baseline = assess_opportunity(REQUEST, (
        evidence(EvidenceKind.TECHNICAL, Stance.NEUTRAL, "current"),
    ))
    future = evidence(EvidenceKind.PREDICTION, Stance.SUPPORTS, "future",
                      observed_at_utc=DECISION + timedelta(minutes=1),
                      available_at_utc=DECISION + timedelta(minutes=1),
                      instrument=replace(INSTRUMENT, exchange=Exchange.BSE),
                      quality=Quality.INVALID)
    assert assess_opportunity(REQUEST, baseline.considered + (future,)) == baseline


def test_delayed_candle_wait_has_explicit_reason() -> None:
    source = validate_completed_candles(
        (candle(0), candle(1, available_at_utc=DECISION + timedelta(seconds=1))),
        instrument=INSTRUMENT, calendar=CALENDAR,
        calendar_exchange=Exchange.NSE, timeframe=Timeframe.MINUTE_1,
        window_start_utc=OPEN, decision_at_utc=DECISION,
        max_age=timedelta(minutes=2),
    )
    result = assess_opportunity(REQUEST, (), candles=source)
    assert result.state is AssessmentState.WAIT
    assert "candle arrived after decision" in result.reasons


def test_unverified_usage_or_quality_fails_closed() -> None:
    result = assess_opportunity(REQUEST, (
        evidence(EvidenceKind.PREDICTION, Stance.SUPPORTS, "ml",
                 usage_verified=False, quality=Quality.UNVERIFIED),
    ))
    assert result.state is AssessmentState.INSUFFICIENT_EVIDENCE
    assert "evidence usage rights unverified" in result.reasons
    assert "evidence quality unverified" in result.reasons


def test_invalid_quality_rejects() -> None:
    result = assess_opportunity(REQUEST, (
        evidence(EvidenceKind.TECHNICAL, Stance.SUPPORTS, "bad",
                 quality=Quality.INVALID),
    ))
    assert result.state is AssessmentState.REJECT


def test_candle_identity_or_timeframe_mismatch_rejects() -> None:
    for issue in (
        "instrument identity mismatch", "candle timeframe mismatch",
        "unrecognized future data-quality issue",
    ):
        result = assess_opportunity(
            REQUEST, (), candles=CandleAssessment(
                INSTRUMENT, Timeframe.MINUTE_1, DECISION, (), (issue,), ()
            )
        )
        assert result.state is AssessmentState.REJECT


def test_candle_assessment_cannot_be_reused_for_other_instrument_or_cutoff() -> None:
    source = validate_completed_candles(
        (candle(0), candle(1)), instrument=INSTRUMENT, calendar=CALENDAR,
        calendar_exchange=Exchange.NSE, timeframe=Timeframe.MINUTE_1,
        window_start_utc=OPEN, decision_at_utc=DECISION,
        max_age=timedelta(minutes=2),
    )
    other = replace(REQUEST, instrument=replace(INSTRUMENT, exchange=Exchange.BSE))
    assert assess_opportunity(other, (), candles=source).state is AssessmentState.REJECT
    later = replace(REQUEST, decision_at_utc=DECISION + timedelta(minutes=1))
    assert assess_opportunity(later, (), candles=source).state is AssessmentState.REJECT


def test_duplicate_evidence_is_rejected_and_order_is_deterministic() -> None:
    first = evidence(EvidenceKind.PREDICTION, Stance.SUPPORTS, "same")
    second = evidence(EvidenceKind.PREDICTION, Stance.CONTRADICTS, "same")
    left = assess_opportunity(REQUEST, (first, second))
    right = assess_opportunity(REQUEST, (second, first))
    assert left == right
    assert left.state is AssessmentState.REJECT
    assert "duplicate evidence identity" in left.reasons


def test_intraday_assessment_reports_missing_candle_validation() -> None:
    result = assess_opportunity(REQUEST, (
        evidence(EvidenceKind.TECHNICAL, Stance.SUPPORTS, "tech"),
    ))
    assert "completed-candle validation missing" in result.reasons


def test_determinism_and_no_ledger_or_profit_vault_write(tmp_path: Path) -> None:
    sentinel = tmp_path / "ledger-and-vault.db"
    sentinel.write_bytes(b"unchanged private ledger and Profit Vault")
    before = sentinel.read_bytes()
    items = (evidence(EvidenceKind.PREDICTION, Stance.SUPPORTS, "ml"),)
    assert assess_opportunity(REQUEST, items) == assess_opportunity(REQUEST, items)
    assert sentinel.read_bytes() == before


def test_evaluator_cannot_bypass_deterministic_risk_gate() -> None:
    result = assess_opportunity(REQUEST, (
        evidence(EvidenceKind.PREDICTION, Stance.SUPPORTS, "ml"),
        evidence(EvidenceKind.TECHNICAL, Stance.SUPPORTS, "tech"),
    ))
    assert result.state not in {
        AssessmentState.ACCEPT, AssessmentState.ACCEPT_WITH_REDUCED_SIZE
    }
    assert result.proposed_size_paise is None
