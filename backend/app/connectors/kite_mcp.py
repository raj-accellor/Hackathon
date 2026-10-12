"""Zerodha Kite MCP adapter (broker fallback for data coverage).

Policy: this product never places, modifies or cancels orders and never reads holdings, positions,
margins or trades. Only the read-only market-data tools in `ALLOWED_TOOLS` can be called; the MCP
client refuses everything else before any request is sent.

Market-data tools need an authorised broker session. Authorisation is interactive: the `login`
tool returns a Kite URL, the account holder approves it in a browser, and the approval is bound to
this client's MCP session. No broker password or token is handled or stored by this application.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

from app.connectors import parsing as ps
from app.connectors.base import BarsResult, ProviderUnavailable, QuoteResult, capability
from app.connectors.binding import BindError, bind_args
from app.connectors.mcp_client import McpClient, McpError, McpToolError
from app.domain.provenance import DataPoint

ALLOWED_TOOLS = frozenset({"login", "get_profile", "search_instruments", "get_quotes", "get_ohlc", "get_ltp", "get_historical_data"})
QUOTE_BATCH = 250  # the tool documents a 500-instrument ceiling; stay well under it
_LOGIN_HINT = re.compile(r"log\s?in|logged in|session|unauthori[sz]ed|authenticat", re.I)
_URL = re.compile(r"https?://[^\s)\]>\"']+")
ORDER_POLICY = "blocked by policy: this product never places orders"


class KiteMcpProvider:
    name = "kite_mcp"

    def __init__(self, client: McpClient | None, is_demo: bool = False, exchange: str = "NSE") -> None:
        self.client = client
        self.is_demo = is_demo
        self.exchange = exchange
        self._instruments: dict[str, dict[str, Any]] = {}
        self._start_error: str | None = None
        self._auth: dict[str, Any] = {"state": "unknown"}
        self._pending_login_url: str | None = None  # a link has been issued and not yet approved

    async def start(self) -> None:
        if self.client is None:
            return
        try:
            await self.client.connect(refresh=True)
            self._start_error = None
        except McpError as e:
            self._start_error = str(e)

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()

    # ------------------------------------------------------------------ capabilities / health
    def capabilities(self) -> dict[str, dict[str, Any]]:
        c = self.client
        if c is None:
            reason = "broker MCP URL is not configured"
        elif not c.connected:
            reason = f"broker MCP unavailable: {c.health()['last_error'] or self._start_error or 'not connected'}"
        else:
            reason = None

        def cap(tool: str) -> dict[str, Any]:
            if reason:
                return capability(False, None, reason)
            return capability(True, tool) if c.has_tool(tool) else capability(False, None, "tool not advertised by the broker MCP")  # type: ignore[union-attr]

        return {
            "history": cap("get_historical_data"), "index_history": cap("get_historical_data"),
            "intraday_history": cap("get_historical_data"), "quote": cap("get_quotes"), "search": cap("search_instruments"),
            "corporate_actions": capability(False, None, "the broker MCP advertises no corporate-actions tool"),
            "fundamentals": capability(False, None, "the broker MCP advertises no fundamentals tool"),
            "order_placement": capability(False, None, ORDER_POLICY),
        }

    def health(self) -> dict[str, Any]:
        if self.client is None:
            return {"name": "kite_mcp", "state": "not_configured", "url": None}
        return {**self.client.health(), "auth": self._auth}

    async def _call(self, tool: str, intents: dict[str, Any]) -> Any:
        c = self.client
        if c is None:
            raise ProviderUnavailable("broker MCP URL is not configured", "not_configured", self.name)
        try:
            if not c.connected:
                await c.connect(refresh=True)
            if not c.has_tool(tool):
                raise ProviderUnavailable(f"broker MCP does not advertise {tool}", "not_supported", self.name)
            return await c.call(tool, bind_args(c.tools[tool].input_schema, intents))
        except BindError as e:
            raise ProviderUnavailable(f"{tool}: {e}", "not_supported", self.name) from e
        except McpToolError as e:
            if _LOGIN_HINT.search(str(e)) or (self._pending_login_url and tool not in ("login", "search_instruments")):
                self._auth = {**self._auth, "state": "login_required"}
                raise ProviderUnavailable("broker login required: authorise the Kite session from the Data status page",
                                          "login_required", self.name) from e
            # Before login some data tools fail with a generic message; confirm the session state once.
            if tool in ("get_historical_data", "get_quotes", "get_ohlc", "get_ltp") and self._auth.get("state") != "authorized":
                if (await self.auth_state()).get("state") == "login_required":
                    raise ProviderUnavailable("broker login required: authorise the Kite session from the Data status page",
                                              "login_required", self.name) from e
            raise ProviderUnavailable(str(e), "unavailable", self.name) from e
        except McpError as e:
            raise ProviderUnavailable(str(e), "unavailable", self.name) from e

    # ------------------------------------------------------------------ auth
    async def auth_state(self) -> dict[str, Any]:
        """authorized | login_required (with login_url) | unavailable | not_configured."""
        if self.client is None:
            self._auth = {"state": "not_configured"}
            return self._auth
        now = datetime.now(UTC).isoformat()
        try:
            profile = await self._call("get_profile", {})
            who = profile.get("user_id") if isinstance(profile, dict) else None
            self._auth = {"state": "authorized", "user_id": who, "checked_at": now}
            self._pending_login_url = None
        except ProviderUnavailable as e:
            note = "Open the link, approve access in Kite, then re-check. Access is read-only market data."
            if self._pending_login_url and e.state in ("login_required", "unavailable") and self.client.connected:
                # Until the link is approved the server answers with generic failures.
                self._auth = {"state": "login_required", "login_url": self._pending_login_url, "checked_at": now, "note": note}
                return self._auth
            if e.state != "login_required":
                self._auth = {"state": e.state, "reason": str(e), "checked_at": now}
                return self._auth
            url = None
            try:
                text = await self._call("login", {})
                m = _URL.search(text if isinstance(text, str) else str(text))
                url = m.group(0) if m else None
            except ProviderUnavailable:
                pass
            self._pending_login_url = url
            self._auth = {"state": "login_required", "login_url": url, "checked_at": now, "note": note}
        return self._auth

    # ------------------------------------------------------------------ data
    async def resolve(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper()
        if sym in self._instruments:
            return self._instruments[sym]
        key = f"{self.exchange}:{sym}"
        # `id` is the exchange:tradingsymbol key, so this is an exact lookup rather than a text search.
        payload = await self._call("search_instruments", {"query": key, "filter_on": "id", "limit": 20})
        rows = ps.find_rows(payload)
        exact = [r for r in rows if str(r.get("id", "")).upper() == key
                 or (str(r.get("tradingsymbol", "")).upper() == sym and str(r.get("exchange", "")).upper() == self.exchange)]
        if not exact:
            raise ProviderUnavailable(f"{key} not found at the broker", "unavailable", self.name)
        self._instruments[sym] = exact[0]
        return exact[0]

    async def search(self, query: str) -> list[dict[str, Any]]:
        rows = ps.find_rows(await self._call("search_instruments", {"query": query, "limit": 50}))
        return [{"symbol": str(r.get("tradingsymbol", "")).upper(), "name": r.get("name"), "exchange": r.get("exchange"),
                 "segment": r.get("segment"), "instrument_type": r.get("instrument_type"), "lot_size": r.get("lot_size"),
                 "tick_size": r.get("tick_size"), "isin": r.get("isin"), "provider": self.name}
                for r in rows if str(r.get("exchange", "")).upper() == self.exchange]

    async def get_history(self, symbol: str, start: date, end: date) -> BarsResult:
        inst = await self.resolve(symbol)
        payload = await self._call("get_historical_data", {"instrument_token": int(inst["instrument_token"]), "start_date": start,
                                                           "end_date": end, "interval": "day"})
        rows = ps.find_rows(payload)
        if rows and not isinstance(rows[0], dict):
            rows = []
        if not rows and isinstance(payload, dict):  # Kite Connect style: {"candles": [[ts, o, h, l, c, v], ...]}
            candles = payload.get("candles") or (payload.get("data") or {}).get("candles") or []
            rows = [dict(zip(("date", "open", "high", "low", "close", "volume"), c, strict=False)) for c in candles]
        df = ps.bars_frame(rows)
        if df.empty:
            raise ProviderUnavailable(f"get_historical_data: no rows returned for {symbol}", "unavailable", self.name)
        return BarsResult(symbol.upper(), df, self.name, "get_historical_data", datetime.now(UTC), "split_adjusted", "INR", self.is_demo)

    async def get_quotes(self, symbols: list[str]) -> dict[str, QuoteResult]:
        out: dict[str, QuoteResult] = {}
        syms = [s.upper() for s in symbols]
        for i in range(0, len(syms), QUOTE_BATCH):
            keys = [f"{self.exchange}:{s}" for s in syms[i: i + QUOTE_BATCH]]
            payload = await self._call("get_quotes", {"symbols": keys})
            data = payload.get("data", payload) if isinstance(payload, dict) else {}
            now = datetime.now(UTC)
            for key, q in (data or {}).items():
                if not isinstance(q, dict) or ":" not in key:
                    continue
                sym = key.split(":", 1)[1].upper()
                ohlc = q.get("ohlc") or {}
                as_of = ps.parse_datetime(q.get("timestamp") or q.get("last_trade_time"))
                raw = {"last_price": q.get("last_price"), "open": ohlc.get("open"), "high": ohlc.get("high"), "low": ohlc.get("low"),
                       "prev_close": ohlc.get("close"), "volume": q.get("volume"),
                       "upper_circuit": q.get("upper_circuit_limit"), "lower_circuit": q.get("lower_circuit_limit")}
                fields = {
                    f: DataPoint(field=f, value=ps.to_float(v), provider=self.name, retrieved_at=now, as_of=as_of,
                                 currency=None if f == "volume" else "INR", unit="shares" if f == "volume" else "price",
                                 source_id="get_quotes", status="ok" if ps.to_float(v) is not None else "missing",
                                 reason=None if ps.to_float(v) is not None else f"get_quotes did not return {f}", is_demo=self.is_demo)
                    for f, v in raw.items()
                }
                out[sym] = QuoteResult(sym, fields, self.name, now, q)
        return out
