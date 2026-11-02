# ADR 0003: Local authentication, verified backups, and ledger integrity

- Status: Accepted
- Date: 2026-11-02

## Context

The application is beginning to hold real local financial records. Phase 3 must reduce accidental exposure and data-loss risk without adding cloud services, broker connectivity, paid infrastructure, or new dependencies.

## Decision: authentication and local exposure

Every `/api` route except `GET /api/v1/health` requires one bearer token read from `AUTH_TOKEN`. The token is at least 32 characters, represented as a secret setting, encoded as bytes, and compared with `secrets.compare_digest`. Unauthorised responses are identical generic `401` responses with `WWW-Authenticate: Bearer`; they never reveal configuration state or token content.

Application startup fails when no token is configured unless `AUTH_DISABLED=true`. Disabled authentication is refused for non-loopback hosts and logs a prominent warning. A non-`127.0.0.1` configured host also logs an exposure warning. CORS permits only `http://127.0.0.1:5173`; `/docs` and `/openapi.json` exist only for the exact `127.0.0.1` host.

For local development, Vite binds to `127.0.0.1` and its Node-side proxy reads the non-`VITE_` token and adds the bearer header. The token is not part of frontend source or browser assets. This proxy is development-only because any client able to reach port 5173 can use its authenticated API access.

## Decision: backup and restore

The backup CLI uses SQLite's online backup API rather than copying a live database file. It writes a temporary database, verifies SQLite and foreign-key integrity, applies file mode `600`, atomically publishes a UTC-timestamped backup, and keeps the newest configurable number—10 by default—in the git-ignored `backups/` directory.

Restore requires `--confirm`. Before replacement it verifies the selected backup and rejects Alembic revisions unknown to the current code. It then creates a verified pre-restore safety backup, restores into a temporary database beside the destination, verifies that file, and atomically swaps it into place. The servers must be stopped before restore.

Backups contain complete financial records. The local directory protects against corruption and mistaken edits, not loss of the Mac. Backups must be copied regularly to a private external drive and must not be placed in a public or shared cloud folder.

## Decision: read-only integrity validation

The integrity CLI opens SQLite with URI `mode=ro`, never through a writable application session. It runs SQLite quick and foreign-key checks, independently replays FIFO lot matching and cost allocation, and reconciles buy lots, sell quantities, proceeds, fees, realised P&L, remaining lot quantities and costs, Profit Vault credits and balance, and final cash. It lists every discrepancy and exits non-zero if any check fails.

Negative final cash is an integrity failure whose message identifies a likely missing deposit entry. Only the final cash balance is checked; the command does not infer a historical running balance from event dates.

## Consequences and known limitations

- No new runtime or development dependency is required; the implementation uses Python, SQLite, FastAPI/Starlette, Alembic, and Vite facilities already present.
- Authentication is single-user and local; there are no accounts, roles, sessions, recovery flow, or automatic token rotation.
- The Vite proxy is not a production authentication boundary and must remain loopback-only.
- Local backups remain vulnerable to disk or device loss until copied to a private external drive.
- Backups are not application-encrypted and rely on filesystem, device, and external-drive protection.
- Restore assumes the application has been stopped and cannot protect against another process reopening the database during the swap.
- Integrity validation detects SQLite and accounting inconsistencies but is not cryptographic tamper evidence.
- The existing portfolio limitations remain: book-cost capital, realised-only daily loss, and no unrealised P&L.
