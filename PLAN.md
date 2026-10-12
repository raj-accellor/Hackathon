# PLAN

Design: `docs/superpowers/specs/2026-10-12-capital-markets-intelligence-design.md`

## Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Analysis, MCP discovery, design | done |
| 2 | Foundation: structure, config, DB, migrations, health, logging | in progress |
| 3 | Connectors (NSE MCP, Kite MCP, Kite Connect), correlation, data quality | pending |
| 4 | Deterministic metrics, scoring, screener | pending |
| 5 | Themes, DSL, monthly snapshots | pending |
| 6 | Sector rotation / RRG | pending |
| 7 | LLM research, web evidence, concall analyzer | pending |
| 8 | Frontend | pending |
| 9 | Verification, docs, release readiness | pending |

## Decisions taken without a user answer

The clarification round was declined, so these defaults apply. Each is reversible through config.

- Broker: Zerodha Kite MCP, plus optional Kite Connect v3 REST.
- NSE MCP URLs: as added to `CLAUDE.md`.
- Local runtime: SQLite + in-memory cache natively; PostgreSQL/Redis via Compose (not verified
  here: Docker is not installed).
- No LLM or web-search credentials: integrations built with mocked contract tests and honest
  "unavailable" states.
- Demo mode uses local mock MCP servers and labels all data as demo.

## Blockers and unverified items

- NSE MCP origin returned HTTP 504 on 2026-10-12, so its tool schemas were not read live.
- Kite MCP market data needs the user's browser login per session.
- Financial statements have no configured source.

## Log

- 2026-10-12: discovery complete; Kite MCP tool list recorded (22 tools).
