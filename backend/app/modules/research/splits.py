"""Chronological partitions with purged label overlap and session embargo."""

from dataclasses import dataclass
from datetime import date

from app.modules.research.core import ResearchSample


@dataclass(frozen=True)
class ResearchSplit:
    train: tuple[ResearchSample, ...]
    validation: tuple[ResearchSample, ...]
    test: tuple[ResearchSample, ...]
    excluded: tuple[tuple[ResearchSample, str], ...]


def chronological_split(
    samples: tuple[ResearchSample, ...],
    sessions: tuple[date, ...],
    train_end: date,
    validation_end: date,
    *,
    embargo_sessions: int = 0,
) -> ResearchSplit:
    """A label must finish before the next partition's first feature date."""
    if (
        embargo_sessions < 0
        or sessions != tuple(sorted(set(sessions)))
        or train_end not in sessions
        or validation_end not in sessions
        or train_end >= validation_end
    ):
        raise ValueError("invalid chronological split boundaries")
    train_end_index = sessions.index(train_end)
    validation_end_index = sessions.index(validation_end)
    if validation_end_index == len(sessions) - 1:
        raise ValueError("test partition needs at least one session")
    validation_start = sessions[train_end_index + 1]
    test_start = sessions[validation_end_index + 1]
    embargoed = set(
        sessions[train_end_index + 1:train_end_index + 1 + embargo_sessions]
    ) | set(sessions[
        validation_end_index + 1:validation_end_index + 1 + embargo_sessions
    ])
    train: list[ResearchSample] = []
    validation: list[ResearchSample] = []
    test: list[ResearchSample] = []
    excluded: list[tuple[ResearchSample, str]] = []
    for sample in samples:
        if not sample.usable or sample.label_end_date is None:
            excluded.append((sample, "sample is unusable"))
        elif sample.feature_date not in sessions:
            excluded.append((sample, "feature date is outside the calendar"))
        elif sample.feature_date in embargoed:
            excluded.append((sample, "session embargo"))
        elif sample.feature_date <= train_end:
            if sample.label_end_date >= validation_start:
                excluded.append((sample, "training label overlaps validation"))
            else:
                train.append(sample)
        elif sample.feature_date <= validation_end:
            if sample.label_end_date >= test_start:
                excluded.append((sample, "validation label overlaps test"))
            else:
                validation.append(sample)
        else:
            test.append(sample)
    return ResearchSplit(
        tuple(train), tuple(validation), tuple(test), tuple(excluded)
    )
