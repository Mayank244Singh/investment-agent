"""Pure offline evaluation; undefined metrics are explicit ``None`` values."""

import math
from collections.abc import Sequence


def _pairs(
    truth: Sequence[float], predictions: Sequence[float],
) -> tuple[tuple[float, float], ...]:
    if len(truth) != len(predictions):
        raise ValueError("truth and prediction counts differ")
    if any(
        type(value) not in (int, float) or not math.isfinite(value)
        for value in (*truth, *predictions)
    ):
        raise ValueError("evaluation values must be finite and present")
    return tuple((float(left), float(right))
                 for left, right in zip(truth, predictions, strict=True))


def evaluate_regression(
    y_true: Sequence[float], predictions: Sequence[float],
) -> dict[str, float | None]:
    """Direction uses sign(return), and prediction error is predicted−actual."""
    pairs = _pairs(y_true, predictions)
    names = (
        "mae", "rmse", "mean_prediction_error", "directional_accuracy",
        "correlation",
    )
    if not pairs:
        return dict.fromkeys(names)
    count = len(pairs)
    errors = [predicted - actual for actual, predicted in pairs]
    mean_actual = math.fsum(actual for actual, _ in pairs) / count
    mean_predicted = math.fsum(predicted for _, predicted in pairs) / count
    centered = [
        (actual - mean_actual, predicted - mean_predicted)
        for actual, predicted in pairs
    ]
    actual_ss = math.fsum(left * left for left, _ in centered)
    predicted_ss = math.fsum(right * right for _, right in centered)
    correlation = (
        math.fsum(left * right for left, right in centered)
        / math.sqrt(actual_ss * predicted_ss)
        if count >= 2 and actual_ss > 0 and predicted_ss > 0 else None
    )
    def sign(value: float) -> int:
        return (value > 0) - (value < 0)

    return {
        "mae": math.fsum(abs(error) for error in errors) / count,
        "rmse": math.sqrt(math.fsum(error * error for error in errors) / count),
        "mean_prediction_error": math.fsum(errors) / count,
        "directional_accuracy": sum(
            sign(actual) == sign(predicted) for actual, predicted in pairs
        ) / count,
        "correlation": correlation,
    }


def _roc_auc(pairs: tuple[tuple[float, float], ...]) -> float | None:
    positive_count = sum(actual == 1 for actual, _ in pairs)
    negative_count = len(pairs) - positive_count
    if not positive_count or not negative_count:
        return None
    ordered = sorted(pairs, key=lambda pair: pair[1])
    positive_rank_sum = 0.0
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average_rank = (index + 1 + end) / 2
        positive_rank_sum += average_rank * sum(
            actual == 1 for actual, _ in ordered[index:end]
        )
        index = end
    return (
        positive_rank_sum - positive_count * (positive_count + 1) / 2
    ) / (positive_count * negative_count)


def evaluate_classification(
    y_true: Sequence[float], probabilities: Sequence[float],
) -> dict[str, float | None]:
    """Evaluate positive-class probabilities; threshold 0.5 for hard labels."""
    pairs = _pairs(y_true, probabilities)
    names = ("accuracy", "precision", "recall", "f1", "roc_auc",
             "log_loss", "brier_score")
    if not pairs:
        return dict.fromkeys(names)
    if any(actual not in (0, 1) for actual, _ in pairs):
        raise ValueError("classification truth must be binary")
    if any(not 0 <= probability <= 1 for _, probability in pairs):
        raise ValueError("classification probability must be in [0, 1]")
    predicted_positive = [probability >= 0.5 for _, probability in pairs]
    tp = sum(actual == 1 and predicted for (actual, _), predicted in zip(
        pairs, predicted_positive, strict=True
    ))
    fp = sum(actual == 0 and predicted for (actual, _), predicted in zip(
        pairs, predicted_positive, strict=True
    ))
    fn = sum(actual == 1 and not predicted for (actual, _), predicted in zip(
        pairs, predicted_positive, strict=True
    ))
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None
        and precision + recall > 0 else None
    )
    if precision == 0 and recall == 0:
        f1 = 0.0
    log_terms: list[float] = []
    for actual, probability in pairs:
        if actual == 1:
            if probability == 0:
                log_terms = []
                break
            log_terms.append(-math.log(probability))
        else:
            if probability == 1:
                log_terms = []
                break
            log_terms.append(-math.log1p(-probability))
    log_loss = math.fsum(log_terms) / len(pairs) if log_terms else None
    return {
        "accuracy": sum(actual == predicted for (actual, _), predicted
                        in zip(pairs, predicted_positive, strict=True))
        / len(pairs),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "roc_auc": _roc_auc(pairs),
        "log_loss": log_loss,
        "brier_score": math.fsum(
            (probability - actual) ** 2 for actual, probability in pairs
        ) / len(pairs),
    }
