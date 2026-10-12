#!/usr/bin/env bash
# Start the whole stack for local development.
#   DATA_MODE=demo (default): local mock MCP servers with synthetic data, labelled as demo.
#   DATA_MODE=live: connectors use the URLs in .env; no mock servers are started.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${DATA_MODE:-$(grep -E '^DATA_MODE=' "$ROOT/.env" 2>/dev/null | cut -d= -f2 || true)}"
MODE="${MODE:-demo}"
API_PORT="${API_PORT:-8000}"
pids=()
cleanup() { for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

cd "$ROOT/backend"
if [ "$MODE" = "demo" ]; then
  echo "[dev] demo mode: starting mock MCP servers (synthetic data)"
  uv run python -m app.devtools.mock_mcp --port "${MOCK_MCP_PORT:-9100}" & pids+=($!)
  sleep 2
fi
echo "[dev] API on http://127.0.0.1:$API_PORT (DATA_MODE=$MODE)"
DATA_MODE="$MODE" uv run uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" & pids+=($!)
cd "$ROOT/frontend"
[ -d node_modules ] || npm install
echo "[dev] web app on http://127.0.0.1:5173"
VITE_API_PROXY="http://127.0.0.1:$API_PORT" npm run dev -- --host 127.0.0.1 & pids+=($!)
wait
