"""Macro-economic and cross-market data adapters.

- `ChartProvider`: market-traded series (Brent and WTI crude, dollar index, USD/INR, US yields, gold,
  global indices, Sensex) from a service answering the v8 chart format, configured by
  MACRO_CHART_BASE_URL. Values are the provider's delayed last price and daily closes.
- `MospiCpiProvider`: India's all-India combined CPI and year-on-year inflation as published by MOSPI.
- `FredProvider`: US and OECD series from the St. Louis Fed API (needs FRED_API_KEY).

Every adapter answers with the provider's own timestamps; nothing is estimated or carried forward. An
unconfigured adapter raises `ProviderUnavailable` with state `not_configured`.
"""

from __future__ import annotations

import asyncio
import ssl
import time
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote

import certifi
import httpx

from app.connectors.base import ProviderUnavailable
from app.core.resilience import CircuitBreaker, CircuitOpenError

MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august", "september",
                                       "october", "november", "december"), start=1)}


class _Http:
    name = "macro"

    def __init__(self, timeout: float, user_agent: str, transport: httpx.AsyncBaseTransport | None = None,
                 verify: ssl.SSLContext | bool = True) -> None:
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False, verify=verify,
                                       headers={"User-Agent": user_agent, "Accept": "application/json, */*"})
        self._breaker = CircuitBreaker(5, 120.0)
        self._cache: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.stats: dict[str, Any] = {"calls": 0, "errors": 0, "last_success_at": None, "last_error": None}

    async def close(self) -> None:
        await self._http.aclose()

    async def get_json(self, url: str, params: dict[str, Any] | None = None, ttl: float = 300.0) -> Any:
        """GET a JSON document, cached in process for `ttl` seconds; concurrent callers share one request."""
        key = str(httpx.URL(url, params=params or {}))
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < ttl:
                return hit[1]
            try:
                self._breaker.before_call()
            except CircuitOpenError as e:
                raise ProviderUnavailable(f"{self.name} paused after repeated failures: {e}", "unavailable", self.name) from e
            self.stats["calls"] += 1
            try:
                r = await self._http.get(url, params=params)
                if r.status_code != 200:
                    raise ProviderUnavailable(f"{self.name} returned HTTP {r.status_code}", "unavailable", self.name)
                data = r.json()
            except (httpx.HTTPError, ValueError) as e:
                self._fail(f"{type(e).__name__}")
                raise ProviderUnavailable(f"{self.name} request failed: {type(e).__name__}", "unavailable", self.name) from e
            except ProviderUnavailable as e:
                self._fail(str(e))
                raise
            self._breaker.success()
            self.stats["last_success_at"] = datetime.now(UTC).isoformat()
            self._cache[key] = (time.monotonic(), data)
            return data

    def _fail(self, msg: str) -> None:
        self._breaker.failure()
        self.stats["errors"] += 1
        self.stats["last_error"] = msg[:200]


class ChartProvider(_Http):
    name = "macro_chart"

    def __init__(self, base_url: str | None, timeout: float, user_agent: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(timeout, user_agent, transport)
        self.base_url = base_url.rstrip("/") if base_url else None

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "state": "ok" if self.base_url else "not_configured",
                "reason": None if self.base_url else "MACRO_CHART_BASE_URL is not set", "base_url": self.base_url, **self.stats}

    async def series(self, symbol: str, rng: str = "3mo") -> dict[str, Any]:
        """Last price, previous close and daily closes for one symbol."""
        if not self.base_url:
            raise ProviderUnavailable("MACRO_CHART_BASE_URL is not set", "not_configured", self.name)
        data = await self.get_json(f"{self.base_url}/{quote(symbol, safe='')}", {"range": rng, "interval": "1d"}, ttl=300)
        return parse_chart(data, symbol)


def parse_chart(data: Any, symbol: str) -> dict[str, Any]:
    try:
        res = data["chart"]["result"][0]
    except (KeyError, IndexError, TypeError) as e:
        err = (data or {}).get("chart", {}).get("error") if isinstance(data, dict) else None
        raise ProviderUnavailable(f"no chart data for {symbol}: {err or 'empty response'}", "unavailable", "macro_chart") from e
    m = res.get("meta") or {}
    stamps = res.get("timestamp") or []
    closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    history = [(datetime.fromtimestamp(t, UTC).date().isoformat(), round(float(c), 4)) for t, c in zip(stamps, closes, strict=False)
               if c is not None]
    last = m.get("regularMarketPrice")
    if last is None and history:
        last = history[-1][1]
    if last is None:
        raise ProviderUnavailable(f"no price for {symbol}", "unavailable", "macro_chart")
    as_of_ts = m.get("regularMarketTime")
    as_of = datetime.fromtimestamp(as_of_ts, UTC) if as_of_ts else None
    # The provider states the day's change against its own reference close (for futures, the settlement, which
    # can differ from the last daily bar's close), so that reference is used when given. Otherwise the previous
    # close is the last daily close dated before the latest market time.
    prev = None
    stated = m.get("fulldayChange", m.get("regularMarketChange"))
    if isinstance(stated, int | float) and last is not None:
        prev = round(float(last) - float(stated), 6)
    elif as_of is not None:
        earlier = [c for d, c in history if d < as_of.date().isoformat()]
        prev = earlier[-1] if earlier else None
    if prev is None:
        prev = m.get("chartPreviousClose") if len(history) < 2 else history[-2][1]
    return {"symbol": symbol, "name": m.get("longName") or m.get("shortName"), "value": float(last),
            "prev_close": float(prev) if prev is not None else None, "currency": m.get("currency"),
            "as_of": as_of.isoformat() if as_of else None, "exchange": m.get("fullExchangeName") or m.get("exchangeName"),
            "year_high": m.get("fiftyTwoWeekHigh"), "year_low": m.get("fiftyTwoWeekLow"),
            "day_high": m.get("regularMarketDayHigh"), "day_low": m.get("regularMarketDayLow"), "history": history}


def legacy_renegotiation_context() -> ssl.SSLContext:
    """MOSPI's API server only completes a TLS handshake with legacy renegotiation, which OpenSSL 3 refuses
    by default. This context allows that one option for this provider; certificates are still verified."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    return ctx


class MospiCpiProvider(_Http):
    name = "mospi_cpi"

    def __init__(self, base_url: str | None, timeout: float, user_agent: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(timeout, user_agent, transport, verify=legacy_renegotiation_context())
        self.base_url = base_url.rstrip("/") if base_url else None

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "state": "ok" if self.base_url else "not_configured",
                "reason": None if self.base_url else "MACRO_CPI_BASE_URL is not set", "base_url": self.base_url, **self.stats}

    async def india_cpi(self, months: int = 13) -> list[dict[str, Any]]:
        """All-India combined CPI (general index) and its year-on-year inflation, newest first."""
        if not self.base_url:
            raise ProviderUnavailable("MACRO_CPI_BASE_URL is not set", "not_configured", self.name)
        # sector_code 3 = Combined, group_code 0 = General, state_code 99 = All India (MOSPI codes)
        data = await self.get_json(f"{self.base_url}/getCPIIndex", {"base_year": "2012", "sector_code": "3", "group_code": "0",
                                                                    "state_code": "99", "Format": "JSON", "limit": str(months)},
                                   ttl=12 * 3600)
        return parse_cpi(data)


def parse_cpi(data: Any) -> list[dict[str, Any]]:
    out = []
    for r in (data or {}).get("data") or [] if isinstance(data, dict) else []:
        mo = MONTHS.get(str(r.get("month", "")).strip().lower())
        try:
            yr = int(r.get("year"))
        except (TypeError, ValueError):
            continue
        if mo is None or str(r.get("sector")) != "Combined" or str(r.get("group")) != "General":
            continue
        out.append({"period": date(yr, mo, 1).isoformat(), "index": _num(r.get("index")), "inflation_yoy": _num(r.get("inflation")),
                    "base_year": r.get("baseyear"), "status": "final" if r.get("status") == "F" else "provisional"})
    return sorted(out, key=lambda x: x["period"], reverse=True)


class FredProvider(_Http):
    name = "fred"

    def __init__(self, api_key: str | None, base_url: str, timeout: float, user_agent: str,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(timeout, user_agent, transport)
        self._key = api_key
        self.base_url = base_url.rstrip("/")

    def __repr__(self) -> str:
        return f"FredProvider(base_url={self.base_url!r}, key={'set' if self._key else 'unset'})"

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "state": "ok" if self._key else "not_configured",
                "reason": None if self._key else "FRED_API_KEY is not set", "base_url": self.base_url, **self.stats}

    async def observations(self, series_id: str, limit: int = 30) -> list[tuple[str, float]]:
        """Newest-first (date, value) pairs; FRED's "." (no value) rows are skipped."""
        if not self._key:
            raise ProviderUnavailable("FRED_API_KEY is not set", "not_configured", self.name)
        data = await self.get_json(f"{self.base_url}/series/observations",
                                   {"series_id": series_id, "api_key": self._key, "file_type": "json", "sort_order": "desc", "limit": limit},
                                   ttl=6 * 3600)
        out = []
        for o in (data or {}).get("observations") or []:
            v = _num(o.get("value"))
            if v is not None and o.get("date"):
                out.append((str(o["date"]), v))
        return out


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def build_macro(s: Any) -> tuple[ChartProvider, MospiCpiProvider, FredProvider]:
    key = s.fred_api_key.get_secret_value() if s.fred_api_key is not None else None
    return (ChartProvider(None if s.is_demo else s.macro_chart_base_url, s.macro_timeout_seconds, s.macro_user_agent),
            MospiCpiProvider(None if s.is_demo else s.macro_cpi_base_url, s.macro_timeout_seconds, s.macro_user_agent),
            FredProvider(None if s.is_demo else key, s.fred_base_url, s.macro_timeout_seconds, s.macro_user_agent))
