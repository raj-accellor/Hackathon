# How to run Markets Intelligence

This guide covers running the whole system from a clean checkout: demo mode, live mode, the
optional integrations, the tests, and what to do when something does not start.

All commands are run from the project root, `/Users/rajeshsanapala/Documents/Hackathon`, unless a
`cd` is shown.

---

## 1. What runs

| Process | Port | What it is |
|---|---|---|
| Web app (Vite) | 5173 | The React interface. It talks only to the API |
| API (FastAPI + scheduler) | 8000 | REST API, background jobs, schedule |
| Mock MCP servers (demo mode only) | 9100, 9101, 9102 | Stand-ins for NSE bhavcopy, NSE CM market and Kite, serving synthetic data |

`scripts/dev.sh` starts all of them and stops them together when you press Ctrl+C.

Data is stored in `backend/var/app.db` (SQLite). Uploaded statement files go to
`backend/var/fundamentals/`.

---

## 2. Prerequisites

| Tool | Version used | Check |
|---|---|---|
| uv (Python package manager) | 0.12 | `uv --version` |
| Node.js | 22 or newer | `node --version` |
| npm | comes with Node | `npm --version` |

uv downloads the right Python (3.12+) by itself. Docker is **not** needed for local runs.

Dependencies are declared in `backend/pyproject.toml` and pinned in `backend/uv.lock` (backend) and
in `frontend/package.json` / `package-lock.json` (frontend). `backend/requirements.txt` and
`backend/requirements-dev.txt` are exported from the lockfile for use with plain `pip`:

```bash
cd backend && python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt     # or requirements.txt for runtime only
uvicorn app.main:app --port 8000        # instead of `uv run uvicorn ...`
```

After changing dependencies in `pyproject.toml`, regenerate them with `./scripts/export-requirements.sh`.

One-time install of dependencies:

```bash
cd backend && uv sync && cd ..
cd frontend && npm install && cd ..
```

(`scripts/dev.sh` also installs frontend packages if `node_modules` is missing, and `uv run`
installs backend packages on first use, so this step is optional.)

---

## 3. Run in demo mode (no credentials needed)

```bash
cp .env.example .env      # only the first time; DATA_MODE=demo is the default
./scripts/dev.sh
```

Then open **http://127.0.0.1:5173**.

What happens on the first start:

1. The three mock MCP servers start.
2. The API creates the database tables, loads the 59 demo instruments and 70 built-in themes.
3. A background "full refresh" job ingests prices over MCP, loads demo financials, computes
   metrics and scores, builds the sector rotation snapshot and the first theme snapshots.
   This takes about 15 seconds; the Overview page shows "No market data yet" until it finishes.

Everything in demo mode is **synthetic**. Symbols start with `DEMO`, and an orange banner says so
on every page. The demo data deliberately includes a stock split, a few bars the NSE mock lacks,
and two prices where the broker mock disagrees, so the correlation features have something to show.

Stop everything with **Ctrl+C** in the terminal running `dev.sh`.

### A quick tour to confirm it works

| Page | What to look for |
|---|---|
| Overview | Benchmark, sector table with rotation quadrants, top scores |
| Screener | Add a filter such as ROE at least 15; sort a column; Export CSV |
| Themes | Open "Low realized volatility"; see Members, Methodology, Excluded, Snapshot history |
| Instrument → `DEMOAUTO01` → Price and provenance | Two recorded source disagreements of 2% |
| Instrument → `DEMOIT03` → Price and provenance | A split under "Corporate actions applied" |
| Sector rotation | The rotation graph and its companion table |
| Research board → pick `DEMOIT02` → Generate research | A rule-based summary that states the model and web search were not used |
| Concall follow-through → `DEMOIT01` → Run analysis | A follow-through score with per-commitment verdicts |
| Data status | Connector states, which tools are blocked, the schedule, job history |

---

## 4. Run in live mode (real NSE and Kite data)

### 4.1 Configure

Edit `.env`:

```bash
DATA_MODE=live
```

The three MCP URLs are already filled in from `.env.example`:

```bash
NSE_BHAVCOPY_MCP_URL=https://mcp.nseindia.in/bhavcopy/cm/mcp
NSE_CM_MARKET_MCP_URL=https://mcp.nseindia.in/cmmkt/mcp
BROKER_MCP_URL=https://mcp.kite.trade/mcp
```

Demo and live data never share a database. When you switch mode, either let the app remove the
other mode's instruments on start, or start clean:

```bash
rm -f backend/var/app.db backend/var/app.db-wal backend/var/app.db-shm
```

### 4.2 Choose what to track

Edit `backend/data/reference/nse_universe.csv`. One row per instrument:

```
symbol,name,sector,industry,sector_kind,instrument_type,sector_index_symbol,isin
```

It ships with about 50 large NSE stocks and 8 indices. Fewer rows means a faster first refresh.
The sector labels in this file are a manually maintained reference, not exchange data.

### 4.3 Check the real servers first (optional but useful)

```bash
cd backend
DATA_MODE=live uv run python ../scripts/check_connectors.py --symbol INFY
cd ..
```

This uses the application's own connectors to list the tools each server advertises, show which
capabilities map to which tool, print the broker session state, and fetch one real quote and some
history. It makes read-only calls only.

### 4.4 Start and refresh

```bash
./scripts/dev.sh
```

Open **http://127.0.0.1:5173/status** (Data status):

1. **Check broker session.** If it says login is needed, open the Kite link shown, approve access
   in Kite, then check again. Access is read-only market data; this app cannot place orders and
   never sees your password. The Kite session expires daily.
2. **Refresh all data.** Progress appears under Job history.

How long the first live refresh takes: the NSE history tool returns three months per call, so
three years of history is about 12 calls per stock. In testing, 12 stocks with one year of history
took a little over three minutes. To shorten it, set `HISTORY_YEARS=1` in `.env` or trim the
universe file. Later refreshes only fetch recent bars.

The app works without the Kite login: it runs on NSE data alone and marks the broker source as
"login required".

### 4.5 What live mode shows without the optional pieces

| Missing piece | Effect on screen |
|---|---|
| Financial statements | Fundamentals and valuation are "Not available"; scores are marked incomplete or "Insufficient data" |
| Kite login | Broker shown as "login required"; no gap filling or cross-checking |
| Language model | Research and concall analysis use rule-based fallbacks and say so |
| Web search switched off or rate-limited | News and catalysts are empty, with a notice that web research is unavailable |

---

## 5. Optional integrations

All are set in `.env`. Restart `dev.sh` after changing it. Never commit `.env`.

### Financial statements

Neither NSE MCP nor Kite provides them. Upload one JSON document per company:

```bash
curl -X PUT "http://127.0.0.1:8000/api/v1/instruments/NSE:INFY/financials" \
  -H "Content-Type: application/json" \
  --data @infy_statements.json
```

The file format is in `docs/data-formats.md`. After an upload, metrics are recomputed
automatically.

### Language model (GLM through Hugging Face)

```bash
LLM_ENABLED=true
HF_TOKEN=<your token>
HF_MODEL_ID=zai-org/GLM-5.3-Flash     # the exact id the Hugging Face router lists
# HF_ENDPOINT_URL=<only if you use a dedicated Inference Endpoint>
```

After restarting, Data status → Language model shows whether the model id was verified against
your account, the provider's own error if not, and similar ids if the id looks mistyped.
`LLM_DAILY_BUDGET` caps calls per day.

The Hugging Face account needs Inference Providers credits. Without them the provider answers
HTTP 402 ("no remaining credits") and the app falls back to rule-based summaries, saying so.

### Web research

On by default in live mode through `ddgs` (DuckDuckGo-backed search, no API key needed):

```bash
WEB_SEARCH_PROVIDER=ddgs        # default; use `none` to turn web research off
# WEB_SEARCH_PROVIDER=brave     # alternative, needs WEB_SEARCH_API_KEY
```

It is always off in demo mode, because demo symbols are fictional. `ddgs` uses public search
endpoints that rate-limit; when a search fails the research board says web research was
unavailable rather than showing no news.

### Kite Connect v3 REST (second broker path)

```bash
KITE_API_KEY=<key>
KITE_API_SECRET=<secret>
```

After the Kite login redirect gives you a `request_token`:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/sources/kite-connect/session \
  -H "Content-Type: application/json" -d '{"request_token": "<token>"}'
```

### Require a token for the API

```bash
API_AUTH_TOKEN=<a long random value>
```

Then enter the same value under Data status → Access in the web app.

### Concall transcripts

In the web app: Concall follow-through → choose a company → Add a transcript (PDF, text file or
pasted text). Upload two consecutive quarters, then Run analysis.

---

## 6. Running the parts separately

Useful when you want separate logs or are debugging one part.

```bash
# Terminal 1: mock MCP servers (demo mode only)
cd backend && uv run python -m app.devtools.mock_mcp --port 9100

# Terminal 2: API
cd backend && DATA_MODE=demo uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 3: web app
cd frontend && npm run dev -- --host 127.0.0.1
```

To exercise the broker login flow in demo mode, start the mock with `--no-auto-login`; the Data
status page will then show a (local) authorisation link.

Run the scheduler as its own process instead of inside the API:

```bash
cd backend && SCHEDULER_ENABLED=false uv run uvicorn app.main:app --port 8000   # API without schedule
cd backend && uv run python -m app.workers.run                                   # worker with schedule
```

With SQLite this is fine for experiments; for a real split set `REDIS_URL` so both processes share
locks and cache.

---

## 7. Triggering jobs by hand

```bash
# Everything, in order: instruments, prices, fundamentals, metrics, rotation, alerts
curl -X POST http://127.0.0.1:8000/api/v1/jobs/refresh-all

# Sector rotation only
curl -X POST http://127.0.0.1:8000/api/v1/sector-rotation/refresh

# One theme (id or slug)
curl -X POST http://127.0.0.1:8000/api/v1/themes/price-momentum/refresh

# Follow a job
curl http://127.0.0.1:8000/api/v1/jobs/1
curl http://127.0.0.1:8000/api/v1/jobs
```

Refresh triggers are limited to 3 per minute and research requests to 6 per minute by default.

Default schedule (India time, changeable in `.env`):

| Job | When |
|---|---|
| Prices, fundamentals reload, metrics | 19:30 on weekdays |
| Sector rotation | Every 12 hours |
| Themes | 06:30 on the 1st of each month |
| Instrument master | 07:00 on weekdays |
| Alerts | Every 15 minutes |

---

## 8. Running the tests

```bash
# Backend: unit, contract, integration, security, scheduler (about 20 seconds)
cd backend
uv run pytest
uv run ruff check .
uv run mypy app

# Frontend
cd ../frontend
npm run typecheck
npm test
npm run build

# Browser end-to-end tests. Starts the demo stack itself if it is not already running.
npx playwright install chromium     # first time only
npm run e2e
```

The backend tests start their own mock MCP servers on free ports and use a temporary database, so
they do not touch `backend/var/app.db` and can run while the app is running.

To run the integration tests on PostgreSQL and Redis instead of SQLite and the in-process cache,
point them at an **empty** database that has been migrated first:

```bash
cd backend
export TEST_DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/markets_test
DATABASE_URL=$TEST_DATABASE_URL uv run alembic upgrade head
TEST_REDIS_URL=redis://localhost:6379/15 uv run pytest tests/api tests/services
```

Security audits: `uvx pip-audit --disable-pip --no-deps -r requirements.txt` and, in `frontend`,
`npm audit --omit=dev`.

---

## 9. Database migrations

Local and test runs create tables automatically (`AUTO_CREATE_SCHEMA=true`). For a managed
database, turn that off and use Alembic:

```bash
cd backend
uv run alembic upgrade head                                   # apply
uv run alembic revision --autogenerate -m "describe change"   # after changing models
uv run alembic check                                          # confirm models and migrations match
```

---

## 10. Running with Docker

```bash
cp .env.example .env
docker compose --profile demo up --build      # demo: includes the mock MCP servers
# or, for live data: set DATA_MODE=live in .env, then
docker compose up --build
```

Web app on http://127.0.0.1:8080, API on http://127.0.0.1:8000. This starts PostgreSQL, Redis, a
one-off migration, the API, a worker and nginx.

**This path has not been run.** Docker was not installed on the machine where the project was
built, so the Compose file, Dockerfiles and nginx config are untested; expect to fix small issues
the first time. The application itself has been verified on PostgreSQL 16 and Redis 6 outside
Docker, including the migration and the separate worker process.

---

## 11. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Overview says "No market data yet" | The first refresh is still running (about 15 s in demo). Check Data status → Job history |
| `address already in use` on 8000, 5173 or 9100 | A previous run is still alive: `lsof -ti :8000,:5173,:9100,:9101,:9102 \| xargs kill` |
| Web app shows "The API could not be reached" | The API is not running or is on another port. `curl http://127.0.0.1:8000/health/ready` |
| Demo data appears in live mode, or the reverse | Stop, delete `backend/var/app.db*`, start again |
| NSE sources show "unavailable" with a 504, 502 or timeout | The NSE servers are intermittently down. Stored data keeps being served. Use "Reconnect and re-read advertised tools" and refresh later |
| Broker shows "login required" | Data status → Check broker session → open the link → approve → check again |
| Scores say "Insufficient data" in live mode | No financial statements uploaded, or less history than a metric needs. The page states which pillars lack data |
| 12-month return or 1-year volatility missing | Fewer than 253 daily bars. Use `HISTORY_YEARS=2` or more and run a refresh |
| "Too many refresh requests" | Rate limit. Wait the number of seconds shown |
| Research says the language model was not used | `LLM_ENABLED`, `HF_TOKEN` or `HF_MODEL_ID` not set, a wrong model id, no Hugging Face credits (HTTP 402), or the daily budget was reached. The notice and Data status show which |
| Every API call returns 401 | `API_AUTH_TOKEN` is set in `.env`. Enter the same value under Data status → Access, or clear it |
| Data status shows a cache warning about Redis | `REDIS_URL` is set but Redis is not running. Local runs fall back to the in-process cache; start Redis or clear `REDIS_URL` |
| A sector is missing from the rotation graph | Its index has no history and fewer than three of its stocks are tracked. The page lists the reason |
| Want to start completely fresh | `rm -rf backend/var` then `./scripts/dev.sh` |

Logs: `dev.sh` prints the API's JSON logs to the terminal. Each API error on screen shows a request
id that you can search for in those logs.

API reference while running: http://127.0.0.1:8000/docs

---

## 12. Things to know before relying on it

- It is a research tool. It cannot place orders and its output is not investment advice.
- NSE states its MCP data is delayed and for informational and educational use, not commercial
  deployment or AI model training. Check NSE, Kite and search-provider terms before any public use.
- Verified against real services: NSE MCP (discovery, quotes, history, index history, corporate
  actions, a full pipeline run), web search through `ddgs`, PostgreSQL and Redis.
- Not verified: Kite quotes and history (needs your login), Kite Connect REST, real output from the
  Hugging Face model (the account had no inference credits), and the Docker path.
- Full status and limitations: `README.md` and `PLAN.md`. Formulas: `docs/methodology.md`.
