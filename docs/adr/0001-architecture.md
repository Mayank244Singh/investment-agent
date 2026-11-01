# ADR 0001: Layered architecture, risk-first validation, and migrations

- Status: Accepted
- Date: 2026-11-01

## Context

The application will grow from a local portfolio foundation into research, analysis, risk, and potentially paper-trading capabilities. It must remain safe to extend without allowing a model, forecast, or API route to make an unchecked trade decision.

## Decision

The backend uses a layered structure: API routes translate HTTP requests, services contain application behavior, schemas define contracts, and database code owns SQLAlchemy sessions and persistence. Routes call services rather than embedding business logic.

Risk validation is a mandatory, independent control. Every future proposed trade—whether initiated by a user workflow, strategy, forecasting component, AI-assisted research feature, paper-trading flow, or broker integration—must be validated by the risk module before it can be accepted or executed. No AI model, LLM, forecasting engine, or broker adapter may bypass this control. LLMs may assist research and context; they must not independently decide or execute trades.

SQLite is used locally for the foundation. Alembic is deliberately not added yet because the project has no durable domain models or schema history to migrate. Alembic must be introduced before the first persistent portfolio, trade, market-data, or other domain schema is released. That change must include a reviewed baseline migration and make migrations, rather than implicit table creation, the supported path for future schema changes.

## Consequences

- Future modules add service-level interfaces and must pass trade proposals to risk validation.
- APIs and UI components cannot directly invoke broker or execution behavior.
- Schema evolution will become explicit, reviewable, and reproducible once durable data models are introduced.
- The current SQLite database remains suitable only for foundation development, not a substitute for a future migration-managed production data strategy.
