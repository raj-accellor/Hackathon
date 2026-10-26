"""Macro-economic dashboard and the headline index strip (Nifty 50, Sensex, Nifty Bank).

Each indicator names its source and the source's own as-of time. The "why it matters" notes describe
the usual transmission channel to Indian equities; they are context, not forecasts. Changes are
calculated here from the provider's last value and previous close: percent for prices, basis points
for yields and rates.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from app.connectors import nse_filings as nf
from app.connectors.base import ProviderUnavailable
from app.core.container import Container
from app.services import kite_fallback

MARKET_STALE_DAYS = 4  # a daily series older than this (weekend plus a holiday) is stale
MONTHLY_STALE_DAYS = 75  # a monthly print whose period started more than this long ago is stale


@dataclass(frozen=True)
class Indicator:
    key: str
    label: str
    group: str
    source: str  # chart | nse_index | cpi | fred
    ref: str  # provider symbol, index name or series id
    unit: str  # e.g. "USD/bbl", "%", "index"
    change: str  # pct | bps
    impact: str
    transform: str | None = None  # yoy: year-on-year change of a monthly index


GROUPS = ("Energy and commodities", "Currencies", "Rates and inflation", "Volatility", "Global equities")

CATALOG: tuple[Indicator, ...] = (
    Indicator("brent", "Brent crude", GROUPS[0], "chart", "BZ=F", "USD/bbl", "pct",
              "India imports most of its crude oil. Higher prices widen the current-account deficit, weaken the rupee and raise costs "
              "for oil marketers, paints, airlines, tyres and chemicals; upstream producers gain."),
    Indicator("wti", "WTI crude", GROUPS[0], "chart", "CL=F", "USD/bbl", "pct",
              "US benchmark crude; the Brent–WTI spread shows how tight seaborne supply is."),
    Indicator("natgas", "Natural gas (Henry Hub)", GROUPS[0], "chart", "NG=F", "USD/MMBtu", "pct",
              "Feeds fertiliser, city-gas distribution and power costs."),
    Indicator("gold", "Gold", GROUPS[0], "chart", "GC=F", "USD/oz", "pct",
              "Safe-haven demand; a large import item for India and a driver for jewellery retailers and gold lenders."),
    Indicator("silver", "Silver", GROUPS[0], "chart", "SI=F", "USD/oz", "pct", "Precious and industrial metal; moves with gold and solar demand."),
    Indicator("copper", "Copper", GROUPS[0], "chart", "HG=F", "USD/lb", "pct",
              "A gauge of global industrial demand; moves metal producers and cable makers."),
    Indicator("usdinr", "US dollar / Indian rupee", GROUPS[1], "chart", "INR=X", "INR per USD", "pct",
              "A weaker rupee lifts rupee revenue of IT and pharma exporters, raises imported inflation and costs for importers, "
              "and reduces returns for foreign investors."),
    Indicator("dxy", "US dollar index (DXY)", GROUPS[1], "chart", "DX-Y.NYB", "index", "pct",
              "The dollar against six major currencies. A strong dollar usually coincides with foreign outflows from emerging markets."),
    Indicator("usdjpy", "US dollar / Japanese yen", GROUPS[1], "chart", "JPY=X", "JPY per USD", "pct",
              "A sharp yen rally can unwind yen-funded carry trades and hit risk assets globally."),
    Indicator("us10y", "US 10-year Treasury yield", GROUPS[2], "chart", "^TNX", "%", "bps",
              "Higher US yields raise the global cost of capital and reduce the appeal of emerging-market equities, "
              "weighing on high-valuation growth stocks."),
    Indicator("india_cpi", "India CPI inflation (YoY)", GROUPS[2], "cpi", "all-india-combined", "%", "bps",
              "Shapes RBI policy. Inflation above the 4% target limits rate cuts; easing inflation supports rate-sensitive sectors "
              "(banks, NBFCs, autos, real estate)."),
    Indicator("india10y", "India 10-year government bond yield", GROUPS[2], "fred", "INDIRLTLT01STM", "%", "bps",
              "Benchmark for Indian borrowing costs and bank treasury gains or losses (OECD monthly series)."),
    Indicator("us_cpi", "US CPI inflation (YoY)", GROUPS[2], "fred", "CPIAUCSL", "%", "bps",
              "Drives Federal Reserve policy and, through it, global liquidity and the dollar.", transform="yoy"),
    Indicator("fedfunds", "US Fed funds rate", GROUPS[2], "fred", "FEDFUNDS", "%", "bps",
              "The interest-rate gap between the US and India influences foreign portfolio flows and the rupee."),
    Indicator("us_unemployment", "US unemployment rate", GROUPS[2], "fred", "UNRATE", "%", "bps",
              "Labour-market strength shapes Fed policy and US demand for Indian IT services."),
    Indicator("indiavix", "India VIX", GROUPS[3], "nse_index", "INDIA VIX", "index", "pct",
              "Expected 30-day Nifty volatility from option prices. A rising VIX means hedging demand and wider option premiums."),
    Indicator("vix", "CBOE VIX", GROUPS[3], "chart", "^VIX", "index", "pct", "US equity volatility; spikes tend to spill into Indian markets."),
    Indicator("sp500", "S&P 500", GROUPS[4], "chart", "^GSPC", "index", "pct", "Global risk appetite; Indian markets often open in its direction."),
    Indicator("nasdaq", "Nasdaq Composite", GROUPS[4], "chart", "^IXIC", "index", "pct",
              "US technology stocks; a sentiment read for Indian IT."),
    Indicator("nikkei", "Nikkei 225", GROUPS[4], "chart", "^N225", "index", "pct", "Asian session sentiment."),
    Indicator("hangseng", "Hang Seng", GROUPS[4], "chart", "^HSI", "index", "pct",
              "China and Hong Kong sentiment; competes with India for emerging-market allocations."),
)

SOURCE_LABEL = {"chart": "Market chart feed (delayed)", "nse_index": "NSE CM market MCP", "cpi": "MOSPI CPI API", "fred": "FRED (St. Louis Fed)"}


def _change(value: float | None, prev: float | None, kind: str) -> tuple[float | None, float | None]:
    """(absolute change, change in the indicator's unit: percent for prices, basis points for rates)."""
    if value is None or prev is None:
        return None, None
    if kind == "bps":
        return round(value - prev, 4), round((value - prev) * 100, 1)
    return round(value - prev, 4), round((value / prev - 1) * 100, 3) if prev else None


def freshness(as_of: str | None, monthly: bool, today: date) -> str:
    if not as_of:
        return "unknown"
    d = date.fromisoformat(as_of[:10])
    return "stale" if (today - d).days > (MONTHLY_STALE_DAYS if monthly else MARKET_STALE_DAYS) else ("fresh" if not monthly else "latest_release")


def yoy(obs_newest_first: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Year-on-year percent change of a monthly series (needs the same month a year earlier)."""
    by = dict(obs_newest_first)
    out = []
    for d, v in obs_newest_first:
        prior = f"{int(d[:4]) - 1}{d[4:]}"
        if prior in by and by[prior]:
            out.append((d, round((v / by[prior] - 1) * 100, 3)))
    return out


async def _one(c: Container, ind: Indicator, today: date) -> dict[str, Any]:
    base = {"key": ind.key, "label": ind.label, "group": ind.group, "unit": ind.unit, "impact": ind.impact, "change_unit": ind.change,
            "source": SOURCE_LABEL[ind.source], "source_ref": ind.ref}
    try:
        if ind.source == "chart":
            d = await c.chart.series(ind.ref)
            delta, chg = _change(d["value"], d["prev_close"], ind.change)
            key = "change_pct" if ind.change == "pct" else "change_bps"
            return {**base, "state": "ok", "value": d["value"], "prev_value": d["prev_close"], "change": delta, key: chg, "as_of": d["as_of"], "freshness": freshness(d["as_of"], False, today),
                    "year_high": d.get("year_high"), "year_low": d.get("year_low"), "currency": d.get("currency"),
                    "history": [{"date": x, "value": v} for x, v in d["history"][-60:]], "period": "daily"}
        if ind.source == "nse_index":
            try:
                try:
                    q = await asyncio.wait_for(c.nse.index_quote(ind.ref), INDEX_TIMEOUT_SECONDS)
                except TimeoutError as e:
                    raise ProviderUnavailable(f"NSE CM market MCP did not answer within {INDEX_TIMEOUT_SECONDS:g}s", "unavailable",
                                              "nse_mcp") from e
            except ProviderUnavailable as nse_err:  # the broker has NSE indices too
                got, _st = await kite_fallback.quotes(c, [f"NSE:{ind.ref}"])
                k = got.get(f"NSE:{ind.ref}")
                if k is None:
                    raise nse_err
                q = {"value": k["last_price"], "prev_close": k["prev_close"], "as_of": k["as_of"]}
                base = {**base, "source": kite_fallback.SOURCE}
            delta, chg = _change(q["value"], q["prev_close"], ind.change)
            return {**base, "state": "delayed", "value": q["value"], "prev_value": q["prev_close"], "change": delta, "change_pct": chg,
                    "as_of": q["as_of"], "freshness": freshness(q["as_of"], False, today), "year_high": q.get("year_high"),
                    "year_low": q.get("year_low"), "history": [], "period": "live (delayed)"}
        if ind.source == "cpi":
            rows = [r for r in await c.cpi.india_cpi() if r["inflation_yoy"] is not None]
            if not rows:
                raise ProviderUnavailable("MOSPI returned no all-India combined CPI rows", "unavailable", "mospi_cpi")
            cur, prev = rows[0], (rows[1] if len(rows) > 1 else None)
            delta, chg = _change(cur["inflation_yoy"], prev["inflation_yoy"] if prev else None, "bps")
            fr = freshness(cur["period"], True, today)
            return {**base, "state": "ok", "value": cur["inflation_yoy"], "prev_value": prev["inflation_yoy"] if prev else None,
                    "change": delta, "change_bps": chg, "as_of": cur["period"], "freshness": fr, "period": "monthly",
                    "note": f"CPI index {cur['index']} (base {cur['base_year']}=100), {cur['status']}."
                    + (" MOSPI's API has not published a newer month; a newer release may exist on mospi.gov.in." if fr == "stale" else ""),
                    "history": [{"date": r["period"], "value": r["inflation_yoy"]} for r in reversed(rows)]}
        # FRED
        obs = await c.fred.observations(ind.ref, 30 if ind.transform == "yoy" else 24)
        if ind.transform == "yoy":
            obs = yoy(obs)
        if not obs:
            raise ProviderUnavailable(f"FRED returned no observations for {ind.ref}", "unavailable", "fred")
        (d0, v0), prev_v = obs[0], (obs[1][1] if len(obs) > 1 else None)
        delta, chg = _change(v0, prev_v, "bps")
        return {**base, "state": "ok", "value": v0, "prev_value": prev_v, "change": delta, "change_bps": chg, "as_of": d0,
                "freshness": freshness(d0, True, today), "period": "monthly",
                "history": [{"date": d, "value": v} for d, v in reversed(obs[:24])]}
    except ProviderUnavailable as e:
        return {**base, "state": e.state, "reason": str(e)[:300], "value": None}


# ------------------------------------------------------------------ interpretation
# Each rule reads one indicator and scores it +1 (risk-off: risky for Indian equities), -1 (risk-on: supportive) or
# 0 (neutral). Thresholds are fixed and shown with the result; stale or missing indicators are skipped, not guessed.
INTERPRETATION_VERSION = "macro-regime-v1"
MIN_SIGNALS = 5
RISKY_AT = 3  # net score at or above this reads "Risky"; at or below -SAFE_AT reads "Safe"
SAFE_AT = 3


@dataclass(frozen=True)
class Rule:
    key: str
    label: str
    field: str  # value | change_pct | change_bps
    off: float  # at or above this: risk-off
    on: float  # at or below this: risk-on
    why_off: str
    why_on: str


RULES: tuple[Rule, ...] = (
    Rule("indiavix", "India VIX level", "value", 20, 13, "expected Nifty volatility is high", "expected Nifty volatility is low"),
    Rule("indiavix", "India VIX daily change", "change_pct", 8, -8, "hedging demand is rising fast", "hedging demand is easing fast"),
    Rule("vix", "CBOE VIX level", "value", 25, 15, "US equity stress is elevated", "US equity stress is low"),
    Rule("brent", "Brent crude level", "value", 95, 75, "costly oil strains India's import bill and inflation", "cheap oil eases India's import bill"),
    Rule("brent", "Brent crude daily change", "change_pct", 3, -3, "an oil spike raises input costs", "an oil drop lowers input costs"),
    Rule("usdinr", "Rupee vs dollar (daily)", "change_pct", 0.3, -0.3, "the rupee is weakening quickly", "the rupee is strengthening"),
    Rule("dxy", "Dollar index (daily)", "change_pct", 0.5, -0.5, "a strong dollar tends to pull money out of emerging markets",
         "a weak dollar tends to support emerging-market flows"),
    Rule("us10y", "US 10-year yield (daily)", "change_bps", 8, -8, "rising US yields raise the global cost of capital",
         "falling US yields ease the global cost of capital"),
    Rule("us10y", "US 10-year yield level", "value", 4.75, 3.75, "US yields are high", "US yields are low"),
    Rule("gold", "Gold (daily)", "change_pct", 1.5, -1.5, "a jump in gold signals flight to safety", "gold falling signals less fear"),
    Rule("india_cpi", "India CPI inflation", "value", 6, 4, "inflation is above RBI's tolerance band", "inflation is at or below RBI's 4% target"),
)
GLOBAL_EQUITIES = ("sp500", "nasdaq", "nikkei", "hangseng")


def interpret(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Risky / Neutral / Safe for Indian equities from the macro indicators, with every rule's reading."""
    by = {r["key"]: r for r in rows}
    signals: list[dict[str, Any]] = []
    skipped: list[str] = []
    for rule in RULES:
        r = by.get(rule.key) or {}
        v = r.get(rule.field)
        if v is None or r.get("freshness") == "stale":
            skipped.append(f"{rule.label} ({'stale' if r.get('freshness') == 'stale' else 'not available'})")
            continue
        score = 1 if v >= rule.off else -1 if v <= rule.on else 0
        unit = {"change_pct": "%", "change_bps": " bps"}.get(rule.field, f" {r.get('unit', '')}".rstrip())
        signals.append({"label": rule.label, "value": v, "unit": unit.strip(), "score": score,
                        "rule": f"risky at or above {rule.off:g}{unit}, supportive at or below {rule.on:g}{unit}",
                        "reading": rule.why_off if score > 0 else rule.why_on if score < 0 else "within the normal range"})
    eq = [by[k]["change_pct"] for k in GLOBAL_EQUITIES if by.get(k, {}).get("change_pct") is not None and by[k].get("freshness") != "stale"]
    if len(eq) >= 2:
        avg = round(sum(eq) / len(eq), 3)
        score = 1 if avg <= -1 else -1 if avg >= 1 else 0
        signals.append({"label": f"Global equities (average of {len(eq)})", "value": avg, "unit": "%", "score": score,
                        "rule": "risky at or below -1%, supportive at or above +1%",
                        "reading": "global markets are selling off" if score > 0 else "global markets are rallying" if score < 0
                        else "global markets are steady"})
    else:
        skipped.append("Global equities (fewer than two indices available)")
    net = sum(s["score"] for s in signals)
    if len(signals) < MIN_SIGNALS:
        stance, summary = "Insufficient data", f"Only {len(signals)} signals could be read; at least {MIN_SIGNALS} are needed."
    elif net >= RISKY_AT:
        stance, summary = "Risky", "Macro conditions lean risk-off for Indian equities."
    elif net <= -SAFE_AT:
        stance, summary = "Safe", "Macro conditions lean supportive (risk-on) for Indian equities."
    else:
        stance, summary = "Neutral", "Macro signals are mixed; no strong risk-on or risk-off lean."
    risky = [s for s in signals if s["score"] > 0]
    safe = [s for s in signals if s["score"] < 0]
    return {"stance": stance, "summary": summary, "net_score": net, "signals_read": len(signals),
            "risk_off": len(risky), "risk_on": len(safe), "neutral": len(signals) - len(risky) - len(safe),
            "drivers": {"risky": [f"{s['label']}: {s['reading']}" for s in risky], "safe": [f"{s['label']}: {s['reading']}" for s in safe]},
            "signals": signals, "skipped": skipped, "version": INTERPRETATION_VERSION,
            "method": (f"Each signal scores +1 (risk-off), -1 (risk-on) or 0. Net score >= {RISKY_AT} reads Risky, <= -{SAFE_AT} reads "
                       f"Safe, otherwise Neutral; fewer than {MIN_SIGNALS} readable signals reads Insufficient data. Stale or missing "
                       "indicators are skipped. A rule-based reading of current conditions, not a forecast or advice.")}


async def dashboard(c: Container) -> dict[str, Any]:
    if c.settings.is_demo:
        reason = "Macro data is off in demo mode, so live figures are never shown next to synthetic ones."
        rows = [{"key": i.key, "label": i.label, "group": i.group, "unit": i.unit, "impact": i.impact, "source": SOURCE_LABEL[i.source],
                 "state": "not_configured", "reason": reason, "value": None} for i in CATALOG]
    else:
        today = datetime.now(UTC).date()
        sem = asyncio.Semaphore(6)

        async def bounded(i: Indicator) -> dict[str, Any]:
            async with sem:
                return await _one(c, i, today)

        rows = list(await asyncio.gather(*(bounded(i) for i in CATALOG)))
    return {"interpretation": interpret(rows), "groups": [{"group": g, "indicators": [r for r in rows if r["group"] == g]} for g in GROUPS],
            "sources": [c.chart.status(), c.cpi.status(), c.fred.status()],
            "coverage": {"available": sum(1 for r in rows if r.get("value") is not None), "total": len(rows)},
            "notes": ["Changes are against the previous close (prices, in percent) or the previous print (rates and inflation, in basis "
                      "points).", "The market chart feed is delayed and unofficial; confirm its terms before production use."]}


# ------------------------------------------------------------------ headline indices
HEADLINE = (("NIFTY 50", "Nifty 50", "nse"), ("SENSEX", "Sensex", "bse"), ("NIFTY BANK", "Nifty Bank", "nse"))
SENSEX_CHART_SYMBOL = "^BSESN"
INDEX_TIMEOUT_SECONDS = 6.0


def _index_row(key: str, label: str, exchange: str, d: dict[str, Any], source: str, state: str) -> dict[str, Any]:
    delta, pct = _change(d.get("value"), d.get("prev_close"), "pct")
    return {"key": key, "label": label, "exchange": exchange.upper(), "state": state, "value": d.get("value"), "prev_close": d.get("prev_close"),
            "change": delta, "change_pct": pct, "open": d.get("open"), "high": d.get("high"), "low": d.get("low"),
            "year_high": d.get("year_high"), "year_low": d.get("year_low"), "as_of": d.get("as_of"), "source": source}


async def _kite_index(c: Container, key: str, label: str, exchange: str) -> dict[str, Any] | None:
    """The index from the Zerodha Kite MCP (live for the logged-in session), or None when Kite cannot answer."""
    got, _state = await kite_fallback.quotes(c, [f"{exchange.upper()}:{key}"])
    q = got.get(f"{exchange.upper()}:{key}")
    if q is None:
        return None
    return _index_row(key, label, exchange, {**q, "value": q["last_price"]}, kite_fallback.SOURCE, "live")


async def _nse_index(c: Container, name: str, label: str) -> dict[str, Any]:
    """NSE CM market MCP first; when it fails, the Zerodha Kite MCP; then NSE's own index feed."""
    try:
        try:
            q = await asyncio.wait_for(c.nse.index_quote(name), INDEX_TIMEOUT_SECONDS)
        except TimeoutError as e:
            raise ProviderUnavailable(f"NSE CM market MCP did not answer within {INDEX_TIMEOUT_SECONDS:g}s", "unavailable", "nse_mcp") from e
        return _index_row(name, label, "nse", q, "NSE CM market MCP", "delayed")
    except ProviderUnavailable as mcp_err:
        if (k := await _kite_index(c, name, label, "nse")) is not None:
            return {**k, "note": f"NSE MCP unavailable: {str(mcp_err)[:120]}"}
        if not c.filings.available:
            return {"key": name, "label": label, "exchange": "NSE", "state": mcp_err.state, "reason": str(mcp_err)[:300], "value": None}
        try:  # NSE's own index feed when neither MCP server can answer
            rows, stamp = await nf.all_indices(c.filings)
            r = next(x for x in rows if str(x.get("index", "")).upper() == name)
            d = {"value": r.get("last"), "prev_close": r.get("previousClose"), "open": r.get("open"), "high": r.get("high"), "low": r.get("low"),
                 "year_high": r.get("yearHigh"), "year_low": r.get("yearLow"), "as_of": stamp.isoformat() if stamp else None}
            return {**_index_row(name, label, "nse", d, "NSE allIndices feed", "delayed"), "note": f"NSE MCP unavailable: {str(mcp_err)[:120]}"}
        except (ProviderUnavailable, StopIteration) as e:
            return {"key": name, "label": label, "exchange": "NSE", "state": "unavailable",
                    "reason": f"NSE MCP: {str(mcp_err)[:150]}; NSE index feed: {str(e)[:120] or 'index not listed'}", "value": None}


async def _sensex(c: Container) -> dict[str, Any]:
    # Sensex is a BSE index; neither NSE source publishes it. The Kite MCP (a licensed broker feed) is preferred,
    # then the market chart feed.
    if (k := await _kite_index(c, "SENSEX", "Sensex", "bse")) is not None:
        return k
    try:
        d = await c.chart.series(SENSEX_CHART_SYMBOL, "5d")
        return _index_row("SENSEX", "Sensex", "bse", {**d, "open": None, "high": d.get("day_high"), "low": d.get("day_low")}, "Market chart feed (delayed)", "delayed")
    except ProviderUnavailable as e:
        return {"key": "SENSEX", "label": "Sensex", "exchange": "BSE", "state": e.state, "value": None,
                "reason": f"Sensex is a BSE index and no NSE source publishes it; Kite MCP unavailable; {str(e)[:200]}"}


async def headline_indices(c: Container) -> list[dict[str, Any]]:
    if c.settings.is_demo:
        return [{"key": k, "label": lbl, "exchange": ex.upper(), "state": "not_configured", "value": None,
                 "reason": "Live index values are not shown in demo mode."} for k, lbl, ex in HEADLINE]
    tasks = [_sensex(c) if ex == "bse" else _nse_index(c, k, lbl) for k, lbl, ex in HEADLINE]
    return list(await asyncio.gather(*tasks))
