# PLAN

Design: `docs/superpowers/specs/2026-10-12-capital-markets-intelligence-design.md`
Details: `README.md`, `docs/architecture.md`, `docs/methodology.md`, `docs/operations.md`

## Status (2026-10-12)

| Phase | Scope | Status |
|---|---|---|
| 1 | Analysis, MCP discovery, design | Done |
| 2 | Foundation: structure, config, database, migration, health, logging, CI file | Done. Compose/PostgreSQL/Redis written, not run (no Docker here) |
| 3 | Connectors, correlation, data quality | Done. NSE MCP verified live. Kite quotes/history not verified live (needs login) |
| 4 | Deterministic metrics, scoring, screener | Done |
| 5 | Themes, DSL, monthly snapshots | Done (70 built-in definitions, 5 flagged unavailable) |
| 6 | Sector rotation / RRG | Done |
| 7 | LLM research, web evidence, concall analyzer | Done with mocked LLM/search; rule-based fallbacks verified. No real inference call made |
| 8 | Frontend | Done (9 pages) |
| 9 | Verification, documentation | Done for what can run on this machine; see "Not verified" |

## Test results (fresh run, 2026-10-12)

- `cd backend && uv run pytest` → 258 passed
- `uv run ruff check .` → clean; `uv run mypy app` → clean (49 `type: ignore` comments cover typing gaps that were not resolved)
- `uv run alembic upgrade head && uv run alembic check` on SQLite → applies, no drift
- `cd frontend && npm run typecheck && npm test && npm run build` → clean, 14 passed, builds
- `npm run e2e` from a clean database → 13 passed (12 desktop, 1 mobile)
- Live: `scripts/check_connectors.py --symbol INFY` and a full pipeline run on 12 NSE stocks + Nifty 50
  against the real NSE MCP servers → succeeded (3,263 bars, real quotes, corporate actions)

## Decisions taken without a user answer

- Broker: Zerodha Kite MCP, plus optional Kite Connect v3 REST behind the same interface.
- Local runtime: SQLite and in-process cache; PostgreSQL/Redis through Compose.
- No LLM or search credentials: both integrations built with mocked contract tests and honest
  "unavailable" states; rule-based fallbacks for research and concall analysis.
- Demo mode: same connector code against local mock MCP servers, synthetic data, fictional symbols.
- Fundamentals: file/upload provider, because no configured source supplies statements.
- Single-tenant auth with an optional bearer token.
- Partial score shown (labelled) when at least three of five pillars have data.
- Same-month theme refresh creates a new revision; earlier snapshots are never changed.
- No entry zone or target price, because there is no backtested method.

## What live discovery changed

The real NSE servers advertise more than public write-ups listed (21 + 15 tools). Adapters were
updated to the recorded schemas: `months`/`endDate` paging, required-but-defaultable arguments,
errors returned as `{"error": …}` payloads, quotes nested under `stock`/`index`, index history and
index quotes from NSE, Kite lookup by exact `id`. Recorded schemas live in
`backend/tests/fixtures/live/` and drive `tests/connectors/test_real_contracts.py`.

## Not verified

- Kite MCP quotes and history against the real server (needs the account holder's login).
- Kite Connect REST, Hugging Face inference, Tavily/Brave search (no credentials).
- Docker Compose, PostgreSQL, Redis, nginx config (Docker not installed). The migration has only
  been applied to SQLite.
- CI workflow (never executed on a runner).
- NSE split/bonus handling on a real split: parsing is tested on exchange-style purpose strings
  and the `adjustmentFactor` field the real tool returns is not used.

## Not implemented

- User accounts and roles; external alert delivery; backtesting; commodity data ingestion;
  quarterly/TTM valuation; point-in-time valuation history; UI benchmark selection for rotation;
  OpenTelemetry tracing (Prometheus metrics and structured logs only); a filings connector.

## Next steps

1. Log in to Kite from Data status and run a live refresh; confirm quote/history shapes, then
   record them as fixtures.
2. Add `HF_TOKEN`/`HF_MODEL_ID` and verify the model id on Data status; run research and a concall
   analysis with the model.
3. Supply statement files for the tracked universe.
4. Install Docker and run `docker compose --profile demo up --build`; apply the migration to PostgreSQL.
