# Phase 3: Hardening (backups, authentication, integrity)

## Goal
Make the backend safe to hold real records. No market data, prediction,
strategies, broker, Groww, paid APIs or orders.

## Scope
1. Backups
   - CLI command that makes a consistent copy of the SQLite database using
     SQLite's online backup API (never a plain file copy while running).
   - Timestamped files in a configurable, git-ignored directory; keep the
     newest N (configurable).
   - Documented restore procedure, and a test that restores a backup and
     checks the data matches.
2. Authentication (single user, local use)
   - All /api endpoints except GET /api/v1/health require a bearer token
     read from the environment. Compare in constant time.
   - Fail closed: if no token is configured, refuse to start unless an
     explicit AUTH_DISABLED=true is set for development, and log a loud
     warning when it is.
   - Never log or return the token. Frontend must not embed it in the
     built bundle; propose a safe approach in the plan (for example the
     Vite dev proxy adding the header from a non-VITE_ env variable).
   - Restrict CORS to the known frontend origin. Warn at startup if the
     server is bound to anything other than 127.0.0.1.
3. Ledger integrity check
   - CLI command that recomputes totals from raw records and verifies:
     lot remaining quantities, realisations, vault balance and cash all
     reconcile. Exit non-zero and list every discrepancy.
4. Documentation: README security section, restore steps, ADR 0003,
   known limitations.

## Rules
- Secrets only from environment; only .env.example is committed.
- Tests for: valid token, missing token, wrong token, health stays open,
  auth-unconfigured startup refusal, backup restore round trip, retention,
  integrity check detecting a deliberately corrupted record.
- Do not commit or push. I do that myself.
- Update docs/PROJECT_STATUS.md after each step.
