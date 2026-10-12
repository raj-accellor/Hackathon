"""Kite Connect v3 REST adapter (optional second broker path).

Implements only read-only market-data endpoints from https://kite.trade/docs/connect/v3/ :
the daily instrument dump, `/quote`, and `/instruments/historical`. No order, portfolio or margin
endpoint is implemented. Documented limits are respected: quote 1 request/second (up to 500
instruments each), historical 3 requests/second. The access token expires daily at 06:00 IST and
is held in memory only.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from app.connectors import parsing as ps
from app.connectors.base import BarsResult, ProviderUnavailable, QuoteResult, capability
from app.core.resilience import Clock, MinIntervalLimiter
from app.domain.provenance import DataPoint

QUOTE_BATCH = 500
MAX_DAY_RANGE = 2000


class KiteConnectProvider:
    name = "kite_connect"

    def __init__(self, api_key: str | None, api_secret: str | None, access_token: str | None, *,
                 base_url: str = "https://api.kite.trade", login_base: str = "https://kite.zerodha.com/connect/login",
                 exchange: str = "NSE", timeout: float = 20.0, min_quote_interval: float = 1.0, min_history_interval: float = 0.34,
                 transport: httpx.AsyncBaseTransport | None = None, clock: Clock | None = None, is_demo: bool = False) -> None:
        self._key, self._secret, self._token = api_key, api_secret, access_token
        self.exchange, self.login_base, self.is_demo = exchange, login_base, is_demo
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport, headers={"X-Kite-Version": "3"})
        self._quote_limiter = MinIntervalLimiter(min_quote_interval, clock)
        self._hist_limiter = MinIntervalLimiter(min_history_interval, clock)
        self._instruments: dict[str, dict[str, str]] | None = None
        self._stats: dict[str, Any] = {"calls": 0, "errors": 0, "last_success_at": None, "last_error": None, "last_error_at": None}
        self._auth = "unknown"

    def __repr__(self) -> str:
        return f"KiteConnectProvider(configured={self.configured}, has_token={bool(self._token)})"

    @property
    def configured(self) -> bool:
        return bool(self._key)

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ auth
    def login_url(self) -> str | None:
        return f"{self.login_base}?v=3&api_key={self._key}" if self._key else None

    def checksum(self, request_token: str) -> str:
        return hashlib.sha256(f"{self._key}{request_token}{self._secret}".encode()).hexdigest()

    async def exchange_token(self, request_token: str) -> dict[str, Any]:
        """Exchange a login `request_token` for an access token. The token stays in memory; only
        non-secret profile fields are returned."""
        if not (self._key and self._secret):
            raise ProviderUnavailable("KITE_API_KEY and KITE_API_SECRET are not configured", "not_configured", self.name)
        data = await self._request("POST", "/session/token", data={"api_key": self._key, "request_token": request_token,
                                                                 "checksum": self.checksum(request_token)}, auth=False)
        self._token = data.get("access_token")
        self._auth = "authorized" if self._token else "login_required"
        return {"user_id": data.get("user_id"), "user_name": data.get("user_name")}

    async def auth_state(self) -> dict[str, Any]:
        if not self.configured:
            return {"state": "not_configured"}
        if not self._token:
            return {"state": "login_required", "login_url": self.login_url()}
        return {"state": self._auth}

    # ------------------------------------------------------------------ plumbing
    async def _request(self, method: str, path: str, *, params: Any = None, data: Any = None, auth: bool = True, raw: bool = False) -> Any:
        if not self.configured:
            raise ProviderUnavailable("Kite Connect is not configured", "not_configured", self.name)
        headers = {}
        if auth:
            if not self._token:
                raise ProviderUnavailable("Kite Connect login required", "login_required", self.name)
            headers["Authorization"] = f"token {self._key}:{self._token}"
        self._stats["calls"] += 1
        try:
            r = await self._http.request(method, path, params=params, data=data, headers=headers)
        except httpx.HTTPError as e:
            raise self._fail(f"cannot reach Kite Connect: {type(e).__name__}", "unavailable") from e
        if r.status_code == 403:
            self._auth = "login_required"
            raise self._fail("Kite Connect session expired or invalid; login required", "login_required")
        if r.status_code != 200:
            msg = ""
            try:
                msg = r.json().get("message", "")
            except ValueError:
                pass
            raise self._fail(f"Kite Connect HTTP {r.status_code}: {msg[:200]}", "unavailable")
        self._stats["last_success_at"] = datetime.now(UTC)
        if auth:
            self._auth = "authorized"
        if raw:
            return r.content
        body = r.json()
        if body.get("status") != "success":
            raise self._fail(f"Kite Connect error: {body.get('message', 'unknown')[:200]}", "unavailable")
        return body.get("data")

    def _fail(self, msg: str, state: str) -> ProviderUnavailable:
        self._stats.update(errors=self._stats["errors"] + 1, last_error=msg, last_error_at=datetime.now(UTC))
        return ProviderUnavailable(msg, state, self.name)

    def capabilities(self) -> dict[str, dict[str, Any]]:
        if not self.configured:
            no = capability(False, None, "Kite Connect API key is not configured")
            return dict.fromkeys(("history", "index_history", "intraday_history", "quote", "search", "instrument_master"), no)
        yes = lambda ep: capability(True, ep)  # noqa: E731
        return {"history": yes("GET /instruments/historical"), "index_history": yes("GET /instruments/historical"),
                "intraday_history": yes("GET /instruments/historical"), "quote": yes("GET /quote"),
                "search": yes("GET /instruments"), "instrument_master": yes("GET /instruments"),
                "order_placement": capability(False, None, "blocked by policy: this product never places orders")}

    def health(self) -> dict[str, Any]:
        s = self._stats
        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        if not self.configured:
            state = "not_configured"
        elif not self._token or self._auth == "login_required":
            state = "login_required"
        elif s["last_error_at"] and (not s["last_success_at"] or s["last_error_at"] > s["last_success_at"]):
            state = "degraded"
        else:
            state = "ok" if s["last_success_at"] else "not_connected"
        return {"name": self.name, "url": str(self._http.base_url), "state": state, "calls": s["calls"], "errors": s["errors"],
                "last_success_at": iso(s["last_success_at"]), "last_error": s["last_error"], "last_error_at": iso(s["last_error_at"])}

    # ------------------------------------------------------------------ data
    async def instruments(self) -> dict[str, dict[str, str]]:
        """Instrument dump for the exchange keyed by tradingsymbol. Fetched once per process;
        the documentation asks clients to download it about once a day."""
        if self._instruments is None:
            content = await self._request("GET", f"/instruments/{self.exchange}", raw=True)
            try:
                content = gzip.decompress(content)
            except (OSError, EOFError):
                pass
            rows = csv.DictReader(io.StringIO(content.decode("utf-8", "replace")))
            self._instruments = {r["tradingsymbol"].upper(): r for r in rows if r.get("exchange", "").upper() == self.exchange}
        return self._instruments

    async def search(self, query: str) -> list[dict[str, Any]]:
        q = query.upper()
        return [{"symbol": s, "name": r.get("name"), "exchange": r.get("exchange"), "segment": r.get("segment"),
                 "instrument_type": r.get("instrument_type"), "lot_size": r.get("lot_size"), "tick_size": r.get("tick_size"),
                 "provider": self.name}
                for s, r in (await self.instruments()).items() if q in s or q in (r.get("name") or "").upper()][:50]

    async def get_history(self, symbol: str, start: date, end: date) -> BarsResult:
        inst = (await self.instruments()).get(symbol.upper())
        if inst is None:
            raise ProviderUnavailable(f"{self.exchange}:{symbol} not in the Kite instrument dump", "unavailable", self.name)
        rows: list[dict[str, Any]] = []
        cur = start
        while cur <= end:
            chunk_end = min(end, cur + timedelta(days=MAX_DAY_RANGE - 1))
            await self._hist_limiter.acquire()
            data = await self._request("GET", f"/instruments/historical/{inst['instrument_token']}/day",
                                       params={"from": f"{cur.isoformat()} 00:00:00", "to": f"{chunk_end.isoformat()} 23:59:59"})
            rows += [dict(zip(("date", "open", "high", "low", "close", "volume"), c, strict=False)) for c in (data or {}).get("candles", [])]
            cur = chunk_end + timedelta(days=1)
        df = ps.bars_frame(rows)
        if df.empty:
            raise ProviderUnavailable(f"no candles returned for {symbol}", "unavailable", self.name)
        return BarsResult(symbol.upper(), df, self.name, "GET /instruments/historical", datetime.now(UTC), "split_adjusted", "INR", self.is_demo)

    async def get_quotes(self, symbols: list[str]) -> dict[str, QuoteResult]:
        out: dict[str, QuoteResult] = {}
        syms = [s.upper() for s in symbols]
        for i in range(0, len(syms), QUOTE_BATCH):
            await self._quote_limiter.acquire()
            data = await self._request("GET", "/quote", params=[("i", f"{self.exchange}:{s}") for s in syms[i: i + QUOTE_BATCH]])
            now = datetime.now(UTC)
            for key, q in (data or {}).items():
                sym = key.split(":", 1)[1].upper()
                ohlc = q.get("ohlc") or {}
                as_of = ps.parse_datetime(q.get("timestamp"))
                raw = {"last_price": q.get("last_price"), "open": ohlc.get("open"), "high": ohlc.get("high"), "low": ohlc.get("low"),
                       "prev_close": ohlc.get("close"), "volume": q.get("volume"), "upper_circuit": q.get("upper_circuit_limit"),
                       "lower_circuit": q.get("lower_circuit_limit")}
                fields = {f: DataPoint(field=f, value=ps.to_float(v), provider=self.name, retrieved_at=now, as_of=as_of,
                                       currency=None if f == "volume" else "INR", unit="shares" if f == "volume" else "price",
                                       source_id="GET /quote", status="ok" if ps.to_float(v) is not None else "missing",
                                       is_demo=self.is_demo) for f, v in raw.items()}
                out[sym] = QuoteResult(sym, fields, self.name, now, q)
        return out
