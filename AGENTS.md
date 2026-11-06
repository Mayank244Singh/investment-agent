# Standing rules

Read docs/PROJECT_BRIEF.md at the start of every session.

- Work only on the current milestone. Do not start the next one without my approval.
- Never commit or push to Git.
- Never install global packages. Use the existing .venv for Python.
- Never overwrite a file without inspecting it first.
- Never fabricate financial data, prices, signals or performance numbers.
- Never hardcode secrets. Only .env.example is committed.
- Stop and ask before any destructive operation, cost, credential need,
  or unresolved architectural decision.
- The risk module must independently validate every proposed trade before any
  future execution. Manual append-only ledger entries record completed events
  and do not pass through the risk validator. No AI or forecasting component
  may bypass the execution risk gate.
Current milestone: Phase 7B.2A synthetic ML runtime verification is complete.
Read docs/PROJECT_STATUS.md and docs/PHASE_7B_2A_RUNTIME_VERIFICATION.md.
Next planned phase: Phase 7B.2B real-data readiness; wait for approval before starting it.
- Never run git commit or git push. The user does that.
