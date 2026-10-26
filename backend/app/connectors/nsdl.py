"""NSDL FPI daily trends (official depository data on foreign portfolio investment), from NSDL's public archive.

The archive page answers a date with every reporting day of that month. Each day lists FPI gross purchases,
gross sales and net investment (INR crore and USD million) by asset class and route. NSDL notes that a day's
report "constitutes FPIs trading/position of the previous trading day", so dates here are NSDL reporting dates.

Past months never change and are cached on disk; the current month is re-read after `CURRENT_MONTH_TTL`.
Requests are spaced and a circuit breaker stops retrying an unreachable site.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.connectors.base import ProviderUnavailable
from app.core.resilience import CircuitBreaker, CircuitOpenError, MinIntervalLimiter

NAME = "nsdl_fpi"
CURRENT_MONTH_TTL = 3600
_DATE = re.compile(r"^\d{2}-[A-Z][a-z]{2}-\d{4}$")
_HIDDEN = re.compile(r'<input type="hidden" name="([^"]+)" id="[^"]+" value="([^"]*)"')


def _num(tok: str) -> float | None:
    t = tok.replace(",", "").strip()
    neg = t.startswith("(") and t.endswith(")")
    try:
        v = float(t.strip("()"))
    except ValueError:
        return None
    return -v if neg else v


def _tokens(page: str) -> list[str]:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S | re.I)
    cells = re.split(r"<[^>]+>", text)
    return [t for t in (html.unescape(re.sub(r"\s+", " ", c)).strip() for c in cells) if t]


def parse_archive(page: str) -> list[dict[str, Any]]:
    """Daily FPI rows from the investment table (the derivatives table that follows is not read)."""
    toks = _tokens(page)
    try:
        start = next(i for i, t in enumerate(toks) if t.startswith("Daily Trends in FPI Investments"))
    except StopIteration:
        return []
    end = next((i for i, t in enumerate(toks) if i > start and t.startswith("Daily Trends in FPI Derivative")), len(toks))
    toks = toks[start:end]
    rows: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    section: str | None = None
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.startswith("Total for"):  # month-to-date / year-to-date summary blocks follow the last day
            cur = None
        elif _DATE.match(t):
            cur = {"reporting_date": datetime.strptime(t, "%d-%b-%Y").date()}
            rows.append(cur)
            section = None
        elif cur is not None and t in ("Equity", "Debt-General Limit", "Debt-VRR", "Debt-FAR", "Hybrid", "Mutual Funds", "AIFs"):
            section = t
        elif cur is not None and t in ("Stock Exchange", "Primary market & others", "Sub-total", "Total"):
            nums = [_num(x) for x in toks[i + 1: i + 5]]
            if len(nums) == 4 and all(n is not None for n in nums):
                buy, sell, net, usd = nums
                if t == "Total":
                    cur.update({"total_net": net, "total_net_usd_mn": usd})
                elif section == "Equity" and t == "Stock Exchange":
                    cur.update({"equity_exchange_buy": buy, "equity_exchange_sell": sell, "equity_exchange_net": net})
                    fx = toks[i + 5] if i + 5 < len(toks) else ""
                    if fx.startswith("Rs."):
                        cur["usd_inr"] = _num(fx[3:])
                elif section == "Equity" and t == "Primary market & others":
                    cur["equity_primary_net"] = net
                elif section == "Equity" and t == "Sub-total":
                    cur.update({"equity_net": net, "equity_net_usd_mn": usd})
                i += 4
        i += 1
    return [r for r in rows if "equity_exchange_net" in r]


class NsdlFpiProvider:
    name = NAME

    def __init__(self, url: str | None, cache_dir: Path, *, user_agent: str, timeout: float = 30.0, min_interval: float = 1.0,
                 enabled: bool = True, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.url = url
        self.cache_dir = Path(cache_dir)
        self.enabled = enabled
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False, headers={"User-Agent": user_agent})
        self._limiter = MinIntervalLimiter(min_interval)
        self._breaker = CircuitBreaker(4, 300.0)
        self._lock = asyncio.Lock()
        self.stats: dict[str, Any] = {"calls": 0, "errors": 0, "last_success_at": None, "last_error": None}

    @property
    def available(self) -> bool:
        return bool(self.enabled and self.url)

    def status(self) -> dict[str, Any]:
        reason = None if self.available else ("disabled in demo mode" if not self.enabled else "NSDL_FPI_ARCHIVE_URL is not set")
        return {"name": self.name, "state": "ok" if self.available else "not_configured", "reason": reason, "base_url": self.url,
                "breaker": self._breaker.state, **self.stats}

    async def close(self) -> None:
        await self._http.aclose()

    def _cache(self, month: date) -> Path:
        return self.cache_dir / f"nsdl_fpi_{hashlib.sha256(f'{self.url}{month:%Y-%m}'.encode()).hexdigest()[:16]}.json"

    async def month(self, any_day: date) -> list[dict[str, Any]]:
        """Every NSDL reporting day in the month of `any_day`."""
        if not self.available:
            raise ProviderUnavailable(self.status()["reason"] or "not configured", "not_configured", NAME)
        first = any_day.replace(day=1)
        today = datetime.now(UTC).date()
        current = (first.year, first.month) == (today.year, today.month)
        p = self._cache(first)
        if p.exists() and (not current or time.time() - p.stat().st_mtime < CURRENT_MONTH_TTL):
            return [{**r, "reporting_date": date.fromisoformat(r["reporting_date"])} for r in json.loads(p.read_text())]
        # the archive takes any day of the month; a past month uses its last day so the whole month is returned
        ask = today if current else (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        rows = parse_archive(await self._post(ask))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps([{**r, "reporting_date": r["reporting_date"].isoformat()} for r in rows]))
        return rows

    async def _post(self, day: date) -> str:
        assert self.url is not None
        try:
            self._breaker.before_call()
        except CircuitOpenError as e:
            raise ProviderUnavailable(f"NSDL paused after repeated failures: {e}", "unavailable", NAME) from e
        async with self._lock:
            await self._limiter.acquire()
            self.stats["calls"] += 1
            try:
                form = await self._http.get(self.url)
                fields = dict(_HIDDEN.findall(form.text))
                if "__VIEWSTATE" not in fields:
                    raise ProviderUnavailable("NSDL archive page did not return its form", "unavailable", NAME)
                d = day.strftime("%d-%b-%Y")
                r = await self._http.post(self.url, data={**fields, "__EVENTTARGET": "btnSubmit1", "__EVENTARGUMENT": "",
                                                          "txtDate": d, "hdnDate": d})
                if r.status_code != 200:
                    raise ProviderUnavailable(f"NSDL archive returned HTTP {r.status_code}", "unavailable", NAME)
            except httpx.HTTPError as e:
                self._fail(type(e).__name__)
                raise ProviderUnavailable(f"NSDL request failed: {type(e).__name__}", "unavailable", NAME) from e
            except ProviderUnavailable as e:
                self._fail(str(e))
                raise
        self._breaker.success()
        self.stats["last_success_at"] = datetime.now(UTC).isoformat()
        return r.text

    def _fail(self, msg: str) -> None:
        self._breaker.failure()
        self.stats["errors"] += 1
        self.stats["last_error"] = msg[:200]
