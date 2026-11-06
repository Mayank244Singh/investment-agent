# Phase 7B.1: Synthetic-only ML training and evaluation

## Boundary

Phase 7A validated research samples feed a **synthetic-only** safety gate,
chronological partitions, training-only preprocessing, baseline or optional
ML adapters, evaluation, and a separate local experiment registry. This
infrastructure never reads the production price or portfolio database,
broker credentials, or API responses. It never creates orders, changes the
deterministic risk gate, or designates a model fit for trading. A synthetic
fixture's scores are software checks, **not** investment evidence.

The CLI constructs tiny deterministic invented fixtures in memory. It does
not accept a user-provided `synthetic: true` flag or an arbitrary training
data path. The public training entry requires evidence from that generator
and a validated synthetic training partition; plain arrays are rejected.
This is an application safety boundary, not a sandbox against malicious
Python code running inside the same process.

## Strategy and dataset contracts

The experiment selects exactly one typed strategy, timeframe and horizon:

| Strategy | Bar | Horizon |
| --- | --- | --- |
| Intraday | 1, 5 or 15 minutes | 5, 15, 30 or 60 elapsed minutes, if divisible by the bar duration |
| Swing | Daily | 5 or 10 verified trading sessions |
| Long term | Daily | 60 or 120 verified trading sessions |

The Phase 7A sample retains a stable instrument ID, decision timestamp,
source availability, feature map, target end and availability, source
provenance, adjustment status, schema version and rejection reasons. The
gate requires the exact requested horizon and compatible strategy/timeframe,
valid known availability, finite values, no duplicate instrument/decision
key, and no feature whose name is a target or label. It rejects unverified
identity or corporate actions through Phase 7A rejection reasons. Missing
features follow the experiment's explicit reject-or-train-median policy;
they are never silently zero-filled. A finite label must be available by
the relevant training cutoff.

The synthetic calendar is invented, explicit and deterministic. It cannot
establish real NSE availability, survivorship safety, corporate-action
adjustment or licensed data rights. No real-data training switch exists.

## Splitting, preprocessing and walk-forward evaluation

All partitions use global UTC decision boundaries across instruments.
Labels crossing a boundary are purged; configurable embargo excludes nearby
decisions. A training example is eligible only when its label was actually
available by the training cutoff. Validation/test decisions do not feed
training features or fitted preprocessing. A final holdout is reserved
outside iterative walk-forward folds.

Feature order is explicit. The preprocessor fits missing-value medians and,
optionally, mean and population standard deviation **only on training**.
Validation and test use that frozen state. A feature with no finite training
values fails the fit. No random split or future-data scaler fit is allowed.
Rolling and expanding windows are both supported. Fixed model settings are
used across folds; Phase 7B.1 does not perform hyperparameter search.

## Models and metrics

Standard-library baselines are constant training-mean return, training
majority class (with a documented tie rule), and a simple feature-based
momentum baseline. LightGBM and XGBoost regression/classification adapters
are optional. If their pinned dependencies are unavailable, they fail with
an explicit dependency error; they do not substitute another algorithm.
No untrusted pickle is loaded. Model artifacts are bounded, checksummed and
stored separately from the database.

Regression evaluation reports MAE, RMSE, mean prediction error, directional
accuracy and correlation when defined. Classification reports accuracy,
precision, recall, F1, ROC-AUC, log loss and Brier score when defined.
Empty or single-class sets, non-finite predictions, constant series and
undefined probabilities are rejected or shown as JSON `null`, as appropriate,
rather than fabricated. A 0.5 classification probability maps to the positive
class for thresholded metrics; majority-class ties therefore select positive.
Classification accuracy is not a trading return.

## Local registry and commands

The Git-ignored `ml-experiments/` directory stores immutable experiment
records and model artifacts. Canonical configuration/data hashes and a fixed
seed support reproducibility. Writes use private permissions, atomic
staging and checksums. Duplicate IDs are refused; reads verify manifest and
artifact integrity. Records include strategy, horizon, feature order,
synthetic fixture provenance, schema version, split/fold definitions,
preprocessing, dependency versions, metrics, baselines and warnings. They
never contain tokens, account details or raw provider responses.

Run commands from the project root with the local `.venv`:

```bash
PYTHONPATH=backend .venv/bin/python -m app.cli research-ml-validate --strategy swing --timeframe 1d --horizon 5
PYTHONPATH=backend .venv/bin/python -m app.cli research-ml-train-synthetic --strategy swing --timeframe 1d --horizon 5 --model-family constant_return --task regression
PYTHONPATH=backend .venv/bin/python -m app.cli research-ml-list-experiments
PYTHONPATH=backend .venv/bin/python -m app.cli research-ml-evaluate YOUR_64_HEX_ID --partition test
.venv/bin/python -m pytest backend -q
.venv/bin/ruff check backend
.venv/bin/mypy backend
```

The CLI fixes the registry at the project-local `ml-experiments/` path; tests
redirect it to temporary directories. Repeat a run with the same strategy,
timeframe, horizon, seed, fixture length, instruments, feature order, model,
hyperparameters, preprocessing and fold settings to reproduce its fingerprint
and metrics. The same experiment ID cannot be written twice. The default
fold sizes expand with target horizon so purged validation partitions retain
samples; a final holdout remains unused until explicit evaluation.

Use a clean, private experimental directory only. Do not copy real market
data, credentials or financial records into it. The CLI prints fold/sample
counts, model and baseline metrics, `null` for undefined metrics, warnings and
the experiment ID. It never labels an experiment approved for live use.

## Dependencies and next gate

At the end of Phase 7B.1, the `.venv` had no NumPy, scikit-learn, LightGBM
or XGBoost. The standard-library baseline path required no new package, and
no package registry was accessed in that phase. Phase 7B.2A subsequently
installed pinned local dependencies and verified the optional adapters on
synthetic data; see `backend/requirements-ml.txt` and
`docs/PHASE_7B_2A_RUNTIME_VERIFICATION.md`.

Before any real-data training, resolve historical-data licensing and
retention/training rights; obtain years of adjusted daily history for
swing/long-term work or complete intraday OHLCV with verified calendars,
availability, identity and actions for intraday work; review survivorship
and leakage controls; then approve a separate real-data training phase.
Upstox's HTTP 403 remains unresolved. Phase 7B.1 is infrastructure only.
The current synthetic daily generator intentionally omits the optional
Phase 7A daily indicators for speed; it exposes the original base features.
Selection of an absent/warm-up-only feature is refused or requires the
explicit training-median policy. Long-term binary labels are derived as
`int(validated_target_return > 0)` by the synthetic ML gate; this is a
research classification target, not executable profit.

At the end of Phase 7B.1, the final holdout could be evaluated repeatedly;
the optional-adapter tests used mocks because LightGBM and XGBoost were not
installed. Phase 7B.2A added an explicit `--unlock-holdout` flag and a
one-time local evaluation receipt, and verified both libraries with real
native builds on synthetic data. This CLI guard is not a defense against
direct Python or filesystem access. Freeze model settings before opening
the holdout and do not tune on its metrics. Intraday synthetic validation
uses short invented sessions and may take several seconds at 1-minute
resolution.
