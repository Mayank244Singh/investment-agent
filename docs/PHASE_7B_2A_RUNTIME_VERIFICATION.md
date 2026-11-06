# Phase 7B.2A runtime verification: complete for synthetic research

## Boundary and environment

The existing Phase 7B.1 synthetic safety gate, chronological folds,
preprocessing, model adapters, registry and CLI remain in place. This work
does not read real market data, touch production databases, invoke Upstox,
place orders, alter the ledger or risk validator, or start Phase 7B.2B.

Runtime: macOS 26.5.2, arm64, CPython 3.12.16 in the existing `.venv`,
with Homebrew `libomp` 23.1.5.
The user approved local ML dependencies. The exact resolved versions are
in `backend/requirements-ml.txt`: LightGBM 4.7.0, XGBoost 3.4.1,
CatBoost 1.2.10, scikit-learn 1.9.1, skops 0.16.0, NumPy 2.5.4,
SciPy 1.18.1 and
their pinned supporting packages. PyPI lists matching Apple Silicon
wheels ([LightGBM](https://pypi.org/project/lightgbm/4.7.0/),
[XGBoost](https://pypi.org/project/xgboost/3.4.1/),
[CatBoost](https://pypi.org/project/catboost/1.2.10/),
[scikit-learn](https://pypi.org/project/scikit-learn/1.9.1/) and
[skops](https://pypi.org/project/skops/0.16.0/)).

Actual runs on invented arrays verify LightGBM, XGBoost and CatBoost
regression and binary classification. They also verify
scikit-learn Ridge, Elastic Net and Logistic Regression.
Random Forest, Extra Trees and HistGradientBoosting regression and binary
classification also run on invented data.
CatBoost, Ridge and Random Forest also train through the public trusted
synthetic permit and round-trip through the private experiment registry.
Regression and binary classification CLI training and re-evaluation passed
against temporary registries. Predictions are
finite and repeat within `1e-9` on the same machine, seed and installed
versions; reload agrees within `1e-8`. These tolerances are software
checks, not investment performance or cross-platform reproducibility.
The invented arrays have 56 rows, two named features and deterministic
binary or numerical targets. The trusted fixture test uses 90 invented
swing sessions and two instruments, with global chronological partitions.

The sklearn linear adapters serialize only finite coefficients and
intercepts in bounded versioned JSON; prediction after reload uses those
coefficients. CatBoost uses its JSON model export within a private temporary
directory, wrapped in the existing bounded checksummed artifact. LightGBM
uses its native model string and XGBoost native JSON. The three scikit-learn
tree families use `skops.io` with a fixed allowlist for the exact tree types
seen in their artifacts; any other untrusted type is refused before load.
Native model reload checks the exact library version. Registry checksums detect corruption,
but do not authenticate an attacker who can replace both artifact and
manifest. No pickle is loaded.

The default generated daily fixture remains an uptrend for compatibility.
The explicit `--scenario alternating` option produces invented positive
and negative labels with a distinct dataset fingerprint. LightGBM,
XGBoost, CatBoost and Logistic Regression classification now train and
re-evaluate through the public synthetic-only CLI with that scenario.
Previously saved default fixtures remain unchanged. No real data is used.

```bash
PYTHONPATH=backend .venv/bin/python -m app.cli research-ml-train-synthetic --sessions 80 --scenario alternating --model-family lightgbm --task classification
```

## Final holdout

`research-ml-evaluate --partition holdout` requires `--unlock-holdout`.
After validating the saved experiment and partition, the CLI atomically
claims one evaluation with a mode-0600 receipt inside a private
`.holdout-seals/` directory. A failed evaluation after the claim consumes
it; a second attempt is refused. Test-partition evaluation is unaffected.
This is an operational CLI guard, not a defense against direct Python or
filesystem access.

```bash
PYTHONPATH=backend .venv/bin/python -m app.cli research-ml-evaluate YOUR_64_HEX_ID --partition holdout --unlock-holdout
```

## Scope boundary

LightGBM and XGBoost both import and their four actual regression and
classification cases pass after the user installed `libomp` outside the
agent sandbox. Classification through the public synthetic gate is also
verified with the explicit alternating scenario.
No real-data training, broker integration, model promotion or later phase
is authorized by this verification.

## Verification

Native runtime and CLI cases pass. Full backend suite: 381 passed,
1 pre-existing skip, 18 dependency deprecation/runtime
warnings. Ruff passed; mypy found no issues in 103 source files; `pip check`
found no broken requirements. No model is approved for live use, and
synthetic results are not predictions or evidence of trading profitability.
