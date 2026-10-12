# Markets Intelligence

Explainable research and screening for NSE-listed instruments: a screener, rule-based themes with
monthly snapshots, a sector rotation graph, an evidence-grounded research board, an entry-setup
checklist, a concall follow-through analyzer, watchlists and alerts.

It is a research tool. It cannot place orders, and nothing it shows is investment advice or a
prediction of returns.

## What it does with data

- **NSE MCP is the primary source** (end-of-day history, corporate actions, delayed quotes, index
  history). **Zerodha Kite MCP fills gaps** and is used to cross-check. When both report a value and
  differ beyond a tolerance, the NSE value is kept and the disagreement is stored and shown.
- Tool names and argument names are **read from each server at start-up**. A capability with no
  advertised tool is reported as not supported; nothing is assumed.
- Only read-only market-data tools of the broker are callable. Order, GTT, holdings, positions and
  margin tools are refused in code before any request is sent.
- Every number carries its source, timestamp and freshness. A value that cannot be computed is
  shown as missing with the reason, never as zero.
- All arithmetic is deterministic and versioned. The language model only writes summaries from
  supplied evidence and classifies concall statements; its output is schema-validated and
  grounding-checked.

## Quick start (demo mode, no credentials)

Requirements: [uv](https://docs.astral.sh/uv/) and Node 22+.

```bash
cp .env.example .env          # DATA_MODE=demo is the default
./scripts/dev.sh              # mock MCP servers + API + web app
```

Open <http://127.0.0.1:5173>. The first start ingests and scores the demo universe (about 15
seconds). Demo mode runs the real connector code against local mock MCP servers that serve
**synthetic** data; every screen carries a demo banner and the symbols are fictional (`DEMO…`).

## Live mode

1. In `.env` set `DATA_MODE=live`. The NSE and Kite MCP URLs are already filled in.
2. `./scripts/dev.sh`
3. Open **Data status**, choose **Check broker session**, open the Kite link and approve read-only
   access, then **Refresh all data**. Without the broker login the app still runs on NSE data alone.
4. Optional:
   - **Fundamentals**: neither NSE MCP nor Kite provides financial statements. Upload a normalised
     statements file per company (`PUT /api/v1/instruments/{ref}/financials`, format in
     `docs/data-formats.md`). Until then fundamentals and valuation are shown as missing and scores
     are marked incomplete.
   - **Language model**: set `LLM_ENABLED=true`, `HF_TOKEN`, `HF_MODEL_ID`.
   - **Web research**: set `WEB_SEARCH_PROVIDER` (`tavily` or `brave`) and `WEB_SEARCH_API_KEY`.
   - **Kite Connect REST**: set `KITE_API_KEY` / `KITE_API_SECRET`.
5. Edit `backend/data/reference/nse_universe.csv` to choose the instruments to track.

Check what the real servers advertise and whether the adapters can use them:

```bash
cd backend && DATA_MODE=live uv run python ../scripts/check_connectors.py --symbol INFY
```

NSE states that its MCP data is delayed and for informational and educational use, not commercial
deployment or AI model training. Confirm NSE, broker and search-provider terms before any
production or public use.

## Commands

| Task | Command |
|---|---|
| Start everything | `./scripts/dev.sh` |
| Backend tests | `cd backend && uv run pytest` |
| Backend lint and types | `cd backend && uv run ruff check . && uv run mypy app` |
| Create / apply migrations | `cd backend && uv run alembic revision --autogenerate -m "…"` / `uv run alembic upgrade head` |
| Frontend unit tests | `cd frontend && npm test` |
| Frontend type check and build | `cd frontend && npm run typecheck && npm run build` |
| End-to-end tests (starts the demo stack if needed) | `cd frontend && npm run e2e` |
| Full stack in containers | `docker compose --profile demo up --build` |

API reference: <http://127.0.0.1:8000/docs> while the API is running.

## Layout

```
backend/app/connectors   MCP client, NSE and Kite adapters, Kite Connect, fundamentals files, search, LLM
backend/app/domain       pure deterministic maths: ratios, valuation, technicals, risk, scoring, themes, RRG
backend/app/services     ingestion, correlation, metrics, themes, rotation, research, concall, alerts, jobs
backend/app/api/v1       REST API
backend/app/workers      scheduler
backend/app/devtools     demo-only mock MCP servers and synthetic data
frontend/src             React app
infra/                   Dockerfiles, nginx, alert rules
docs/                    architecture, methodology, data formats, operations
```

## Verification status

| Area | Status |
|---|---|
| Backend unit, contract, integration, security and scheduler tests | 258 passing (`uv run pytest`) |
| Frontend component tests | 14 passing |
| Browser end-to-end tests (demo stack, desktop and mobile) | 13 passing |
| NSE MCP, real servers | Verified on 2026-10-12 with the application's connectors: tool discovery, quotes, paged history, index history, corporate actions, and a full pipeline run on 12 stocks |
| Kite MCP, real server | Tool discovery, allowlist and instrument lookup verified. Quotes and history need an account login and were **not** verified against the real server; they are covered by mock contract tests only |
| Kite Connect REST | Mocked contract tests only; needs a developer app |
| Hugging Face LLM, web search | Mocked contract tests only; no credentials were available |
| Docker Compose, PostgreSQL, Redis | Written but **not run**: Docker was not available on the authoring machine. The Alembic migration was applied and checked on SQLite only |

## Known limitations

- Single-tenant: one optional bearer token (`API_AUTH_TOKEN`), no user accounts or roles.
- Fundamentals need uploaded files; there is no filings connector. Valuation uses the latest
  annual period; quarterly and trailing-twelve-month valuation are not computed.
- Sector labels come from a manually maintained reference file, not from the exchange.
- Prices are adjusted for splits and bonuses only, not for dividends (not total return).
- No entry zone or target price is produced: there is no backtested method, and no backtesting
  module exists.
- Themes needing analyst estimates, governance data, a corporate-actions calendar or commodity
  exposure are listed as unavailable.
- Alerts are recorded in the app only; no email or push delivery.
- The rotation benchmark is the configured index; it is not selectable in the UI.
- Screener filtering runs in memory over the latest snapshots, which suits a universe of a few
  thousand instruments. Saved screens live in the browser.
- Commodities: the instrument schema has contract metadata, but no commodity data is ingested.
- CSV export uses a plain link and does not send the bearer token.

More detail: `docs/architecture.md`, `docs/methodology.md`, `docs/operations.md`, `PLAN.md`.
