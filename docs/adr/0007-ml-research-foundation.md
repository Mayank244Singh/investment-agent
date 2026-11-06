# ADR 0007: Point-in-time ML research dataset foundation

- Status: Phase 7A, synthetic validation only
- Date: 2026-11-06

## Boundary

The research layer under `backend/app/modules/research/` is pure and has no
provider client, database writer, broker path, model training, or order path.
`ResearchObservation` is the supplied source fact. `ResearchSample` is
derived from observations; it is never written back into source observations
or canonical daily prices. This keeps Phase 6A's source/canonical separation.
The current SQLite price history is **not** an approved training dataset:
legacy availability timestamps are unknown, prices are unadjusted, and
corporate-action and historic identity clearance are not established.

## Contract

Each observation has a stable internal `instrument_id`, trading `session`,
integer-paise close, integer volume, provider and source record key,
`available_at_utc` (nullable), adjustment status, explicit historical
identity-valid interval and verification flag, and a corporate-action flag.
Null availability means unknown, not the import time. Every derived sample
has an observation date, explicit decision time, target horizon in trading
sessions, feature map, missing-feature names, forward label when available,
source provenance, input availability timestamps, adjustment status,
dataset version and all rejection reasons. Candidate counts count
instrument/date/horizon samples, not unique dates.

The caller supplies an ordered, unique trading-session calendar and a
timezone-aware decision timestamp for each session. The calendar is not
inferred from calendar days or a provider's partial price series. For now,
the CLI accepts only JSON labelled `"synthetic": true`; it reads no
database or `.env` and makes no network request. One example observation:

```json
{
  "instrument_id": 1,
  "session": "2025-01-02",
  "close_paise": 10000,
  "volume": 100,
  "provider": "invented-fixture",
  "source_record_key": "invented-1",
  "available_at_utc": "2025-01-02T12:00:00+00:00",
  "adjustment_status": "verified_no_action",
  "identity_valid_from": "2025-01-01",
  "identity_valid_to": "2025-12-31",
  "identity_verified": true,
  "corporate_action_flag": false
}
```

The containing JSON object also requires `synthetic`, `dataset_version`,
`sessions` (ISO dates), `decision_times_utc` (date-to-ISO-timestamp map),
`built_at_utc`, and `observations`; `window` defaults to 5.
`benchmark_instrument_id` and `require_benchmark` are optional. Fixture
claims of identity or adjustment clearance are invented test conditions,
not evidence for real instruments.

## Features and targets

All ratios are `Decimal`; money stays in integer paise. At session *t*,
daily return is close(t)/close(t-1)-1; window momentum is
close(t)/close(t-window)-1; volatility is the population standard deviation
of the last `window` session returns; moving average is the mean of the
last `window` closes in paise; price relative to that average is
close(t)/average-1. Volume change is volume(t)/volume(t-1)-1 (missing if
previous volume is zero); rolling average volume is the mean over the
window. Drawdown is close(t)/maximum close in the window-1. Relative
strength is window momentum minus benchmark momentum when aligned,
point-in-time safe benchmark history exists. Missing/insufficient inputs
produce `None` and a named missing indicator. Relative strength is
optional unless `require_benchmark` is true.

Each feature date produces separate 5-, 10-, 60- and 120-trading-session
forward-return targets: close(t+h)/close(t)-1. For 5 and 10 sessions,
the binary positive-return label is 1 only when the forward return is
strictly positive; a future model may estimate its probability. The code
does not itself estimate probabilities. These are research labels, never
guaranteed predictions.

## Leakage and quality rules

Feature inputs are limited to supplied sessions through the decision date
and must have known availability no later than that decision. Forward-label
inputs must be available by the declared dataset-build time. Missing
sessions, unknown availability, unverified historical identity, unresolved
corporate actions or unverified adjustment status reject affected samples.
Even identical duplicate instrument/session observations are reported and
rejected; conflicting values are additionally counted. No provider wins
silently. No target spans a missing session or unresolved action.

`chronological_split` partitions by feature date; it purges a training
sample if its label reaches validation and a validation sample if its label
reaches test. An optional session-count embargo excludes the first sessions
after each boundary. Unusable samples are excluded with reasons. A real
training pipeline must additionally fit any scalers and imputers on the
training partition only; none exist in Phase 7A.

## Offline command and checks

From the project root, validate a deliberately invented JSON fixture:

```bash
PYTHONPATH=backend .venv/bin/python -m app.cli research-validate --synthetic-json /tmp/invented-research.json
PYTHONPATH=backend .venv/bin/python -m app.cli research-validate --synthetic-json /tmp/invented-research.json --train-end 2025-04-01 --validation-end 2025-05-01 --embargo-sessions 2
.venv/bin/python -m pytest backend/tests/test_research_foundation.py -q
```

The CLI prints coverage, candidates, usable samples, missing features,
invalid labels, action/adjustment blockers, unknown availability, duplicate
and conflicting observations, and rejection reasons. It exits nonzero for
no usable sample or dataset-wide source, action or availability blockers.
It never writes a dataset or model artifact.

## Limitations and next gate

Phase 7A supplies an offline research contract, not a real-data adapter or
ML training. No authoritative NSE trading calendar, historical ticker/ISIN
review, corporate-action adjustment/version lineage, point-in-time source
availability verification, benchmark data, survivorship-safe universe,
model registry or training pipeline is supplied. The caller's claimed
identity and adjustment clearance cannot be independently verified here.
Before Phase 7B can use real data, establish those facts, a licensed
historical source and permissible retention/training rights, then review
the dataset and leakage controls on that source. Upstox's HTTP 403 remains
unresolved; this phase does not access it.
