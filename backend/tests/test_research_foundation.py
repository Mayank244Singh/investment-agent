"""Offline point-in-time research contract tests with invented observations."""

import json
import socket
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.cli import main
from app.modules.research.core import (
    ResearchConfig,
    ResearchObservation,
    ResearchSample,
    build_dataset,
)
from app.modules.research.splits import chronological_split


def fixture(
    count: int = 130, *, provider: str = "synthetic-a",
) -> tuple[tuple[ResearchObservation, ...], ResearchConfig]:
    sessions = tuple(date(2025, 1, 1) + timedelta(days=index) for index in range(count))
    decisions = {
        day: datetime.combine(day, time(18), UTC)
        for day in sessions
    }
    observations = tuple(
        ResearchObservation(
            instrument_id=1,
            session=day,
            close_paise=10_000 + 100 * index,
            volume=100 + index,
            provider=provider,
            source_record_key=f"{provider}:{index}",
            available_at_utc=datetime.combine(day, time(12), UTC),
            adjustment_status="verified_no_action",
            identity_valid_from=sessions[0],
            identity_valid_to=sessions[-1],
            identity_verified=True,
        )
        for index, day in enumerate(sessions)
    )
    config = ResearchConfig(
        dataset_version="synthetic-v1",
        sessions=sessions,
        decision_times_utc=decisions,
        built_at_utc=datetime.combine(sessions[-1] + timedelta(days=1), time(), UTC),
        window=2,
    )
    return observations, config


def sample_at(
    observations: tuple[ResearchObservation, ...], config: ResearchConfig,
    index: int, horizon: int = 5,
) -> ResearchSample:
    return next(
        sample for sample in build_dataset(observations, config).samples
        if sample.feature_date == config.sessions[index]
        and sample.target_horizon_sessions == horizon
    )


def test_features_and_forward_labels_are_exact() -> None:
    observations, config = fixture()
    sample = sample_at(observations, config, 2)
    assert sample.usable
    assert sample.features["daily_return"] == Decimal(10200) / 10100 - 1
    assert sample.features["momentum"] == Decimal("0.02")
    assert sample.features["moving_average_paise"] == Decimal(10150)
    assert sample.features["price_to_moving_average"] == Decimal(10200) / 10150 - 1
    assert sample.features["volatility"] is not None
    assert sample.features["volatility"] > 0
    assert sample.features["volume_change"] == Decimal(102) / 101 - 1
    assert sample.features["average_volume"] == Decimal("101.5")
    assert sample.features["drawdown"] == 0
    assert sample.target_return == Decimal(10700) / 10200 - 1
    assert sample.positive_return_label == 1
    assert sample.label_end_date == config.sessions[7]
    long_term = sample_at(observations, config, 2, 120)
    assert long_term.target_return == Decimal(22200) / 10200 - 1
    assert long_term.positive_return_label is None
    assert sample.target_horizon_sessions == 5


@pytest.mark.parametrize("horizon", [5, 10, 60, 120])
def test_each_horizon_counts_sessions_not_calendar_duration(horizon: int) -> None:
    observations, config = fixture()
    sample = sample_at(observations, config, 2, horizon)
    assert sample.label_end_date == config.sessions[2 + horizon]
    assert sample.target_return == (
        Decimal(observations[2 + horizon].close_paise)
        / observations[2].close_paise - 1
    )
    assert (sample.positive_return_label == 1) == (horizon in (5, 10))


def test_future_changes_cannot_change_features_but_change_forward_label() -> None:
    observations, config = fixture()
    original = sample_at(observations, config, 2)
    changed = list(observations)
    changed[7] = replace(changed[7], close_paise=changed[7].close_paise + 10)
    after = sample_at(tuple(changed), config, 2)
    assert after.features == original.features
    assert after.target_return != original.target_return
    assert all(day <= config.sessions[2] for day in (
        observation.session for observation in observations[:3]
    ))


def test_missing_sessions_and_short_history_are_reported() -> None:
    observations, config = fixture(12)
    missing = tuple(item for item in observations if item.session != config.sessions[5])
    result = build_dataset(missing, config)
    assert "missing label session" in sample_at(missing, config, 2).rejection_reasons
    assert "missing required feature" in sample_at(missing, config, 0).rejection_reasons
    assert "insufficient future sessions" in result.samples[-1].rejection_reasons
    assert result.report.invalid_labels > 0
    assert result.report.missing_features["momentum"] > 0


def test_unknown_or_late_availability_fails_closed() -> None:
    observations, config = fixture()
    unknown = (replace(observations[2], available_at_utc=None),) + observations[3:]
    result = build_dataset(unknown, config)
    assert result.report.unknown_historical_availability > 0
    late = list(observations)
    late[2] = replace(
        late[2], available_at_utc=config.decision_times_utc[config.sessions[2]]
        + timedelta(seconds=1),
    )
    late_sample = sample_at(tuple(late), config, 2)
    assert "observation unavailable at decision time" in late_sample.rejection_reasons
    assert all(value is None for value in late_sample.features.values())
    assert late_sample.target_return is None


def test_unadjusted_actions_and_unverified_identity_block_samples() -> None:
    observations, config = fixture()
    changed = list(observations)
    changed[3] = replace(
        changed[3], adjustment_status="unverified", corporate_action_flag=True,
        identity_valid_from=None,
    )
    sample = sample_at(tuple(changed), config, 2)
    assert "adjustment status unverified" in sample.rejection_reasons
    assert "corporate action unresolved" in sample.rejection_reasons
    assert "historical identity unverified" in sample.rejection_reasons
    report = build_dataset(tuple(changed), config).report
    assert report.corporate_action_or_adjustment_blockers > 0


def test_duplicate_and_conflicting_sources_reject_without_selection() -> None:
    observations, config = fixture()
    duplicate = replace(
        observations[3], provider="synthetic-b", source_record_key="other:3"
    )
    result = build_dataset(observations + (duplicate,), config)
    assert result.report.duplicate_observations == 1
    assert result.report.conflicting_observations == 0
    assert "duplicate source observation" in sample_at(
        observations + (duplicate,), config, 3
    ).rejection_reasons
    conflict = replace(duplicate, close_paise=duplicate.close_paise + 1)
    result = build_dataset(observations + (conflict,), config)
    assert result.report.conflicting_observations == 1
    assert "conflicting source observation" in sample_at(
        observations + (conflict,), config, 3
    ).rejection_reasons


def test_provider_independence_and_deterministic_reproducibility() -> None:
    first, config = fixture(provider="synthetic-a")
    second, _ = fixture(provider="synthetic-b")
    left = build_dataset(first, config)
    right = build_dataset(second, config)
    assert left == build_dataset(first, config)
    assert left.report == right.report
    assert left.samples[20].features == right.samples[20].features
    assert left.samples[20].target_return == right.samples[20].target_return
    assert left.samples[20].provenance != right.samples[20].provenance


def test_relative_strength_requires_valid_benchmark_when_configured() -> None:
    observations, config = fixture()
    benchmark = tuple(
        replace(item, instrument_id=2, provider="benchmark")
        for item in observations
    )
    configured = replace(
        config, benchmark_instrument_id=2, require_benchmark=True
    )
    sample = sample_at(observations + benchmark, configured, 2)
    assert sample.usable
    assert sample.features["relative_strength"] == 0
    missing = sample_at(observations + benchmark[1:], configured, 2)
    assert "missing benchmark session" in missing.rejection_reasons


def test_chronological_split_purges_overlap_and_embargoes() -> None:
    observations, config = fixture()
    dataset = build_dataset(observations, config)
    split = chronological_split(
        dataset.samples, config.sessions, config.sessions[70], config.sessions[100],
        embargo_sessions=2,
    )
    assert split.train and split.validation and split.test
    assert all(
        item.label_end_date is not None
        and item.label_end_date < config.sessions[71] for item in split.train
    )
    assert all(
        item.label_end_date is not None
        and item.label_end_date < config.sessions[101] for item in split.validation
    )
    assert all(item.feature_date > config.sessions[100] for item in split.test)
    assert any(reason == "training label overlaps validation"
               for _, reason in split.excluded)
    assert any(reason == "session embargo" for _, reason in split.excluded)


def test_synthetic_cli_never_loads_settings_or_opens_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observations, config = fixture(10)
    payload = {
        "synthetic": True,
        "dataset_version": config.dataset_version,
        "sessions": [day.isoformat() for day in config.sessions],
        "decision_times_utc": {
            day.isoformat(): timestamp.isoformat()
            for day, timestamp in config.decision_times_utc.items()
        },
        "built_at_utc": config.built_at_utc.isoformat(),
        "window": config.window,
        "observations": [
            {
                **{
                    name: getattr(item, name) for name in (
                        "instrument_id", "close_paise", "volume", "provider",
                        "source_record_key", "adjustment_status",
                        "identity_verified", "corporate_action_flag",
                    )
                },
                "session": item.session.isoformat(),
                "available_at_utc": item.available_at_utc.isoformat()
                if item.available_at_utc else None,
                "identity_valid_from": item.identity_valid_from.isoformat()
                if item.identity_valid_from else None,
                "identity_valid_to": item.identity_valid_to.isoformat()
                if item.identity_valid_to else None,
            }
            for item in observations
        ],
    }
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network or app settings accessed")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr("app.cli.get_settings", forbidden)
    assert main(["research-validate", "--synthetic-json", str(path)]) == 0
    output = capsys.readouterr().out
    assert "candidates:" in output
    assert "Rejected reasons:" in output
    payload["synthetic"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert main(["research-validate", "--synthetic-json", str(path)]) == 1
    assert "must explicitly be synthetic" not in capsys.readouterr().err
