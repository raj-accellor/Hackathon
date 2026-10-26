"""Tickertape fallback for fundamental metrics NSE filings do not provide.

Source precedence is unchanged: a metric computed from NSE filings always wins. A Tickertape value is used only when
the NSE-based value has status `missing` (no statements filed, or the needed line item absent). Values that are
`not_meaningful` (e.g. negative earnings) or `not_applicable` (e.g. debt/equity for a bank) are never overwritten, and
the bank/NBFC/insurer rules apply to Tickertape values too.

Field definitions are the ones Tickertape's own screener metadata (`in_screener_meta_read`, type=filters) advertises;
units were checked against this app's own NSE-based values (percentages as plain numbers, market cap in INR crore,
debt/equity as a ratio although Tickertape labels it "%").
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import ProviderUnavailable
from app.core.container import Container
from app.db.models import DataSourceRecord, Instrument, MetricSnapshot, ProviderRatios
from app.domain.financial_metrics import _NOT_FOR_FINANCIALS
from app.domain.result import MetricValue, Status, ok
from app.domain.theme_dsl import METRIC_CATALOG

PROVIDER = "tickertape_mcp"
VERSION = "tt-ratios-v1"
RESOLVE_BATCH = 50
SCREEN_BATCH = 10  # in_screen_stocks_read returns at most 10 rows per call
MAX_AGE = timedelta(days=7)

# metric -> (Tickertape screener field, Tickertape's definition, positive values only)
FIELDS: dict[str, tuple[str, str, bool]] = {
    "pe": ("ttmPe", "close price / TTM EPS excluding extraordinary items", True),
    "pb": ("pbr", "close price / book value per share, latest FY", True),
    "ps": ("ps", "close price / revenue per share, latest FY", True),
    "ev_ebitda": ("evebitd", "enterprise value / EBITDA, latest FY", True),
    "ev_ebit": ("evebit", "enterprise value / EBIT, latest FY", True),
    "ev_sales": ("evByRev", "enterprise value / revenue, latest FY", True),
    "price_to_fcf": ("lcpCafFcf", "market cap / free cash flow, latest FY", True),
    "dividend_yield": ("divDps", "DPS latest FY / close price", False),
    "market_cap": ("mrktCapf", "market capitalisation (INR crore)", True),
    "roe": ("roe", "net income / average common equity, latest FY", False),
    "roa": ("rtnAsts", "net income / average total assets, latest FY", False),
    "roce": ("roce", "EBIT / capital employed, latest FY", False),
    "pat_margin": ("pftMrg", "net income / revenue, latest FY", False),
    "ebitda_margin": ("aopm", "EBITDA / revenue, latest FY", False),
    "debt_equity": ("dbtEqt", "total debt / shareholder equity", False),
    "interest_coverage": ("aint", "EBIT / interest expense", False),
    "current_ratio": ("qcur", "current assets / current liabilities", False),
    "quick_ratio": ("aqui", "(current assets - inventory) / current liabilities", False),
    "asset_turnover": ("asstTurnR", "revenue / average total assets over 2 FYs", False),
    "revenue_growth_yoy": ("rvng", "revenue growth, latest FY over prior FY", False),
    "ebitda_growth_yoy": ("ebitg", "EBITDA growth, latest FY over prior FY", False),
    "eps_growth_yoy": ("epsg", "EPS growth, latest FY over prior FY", False),
    "revenue_cagr_5y": ("5YrevChg", "5-year revenue CAGR", False),
}
_FIN_NA = _NOT_FOR_FINANCIALS | {"enterprise_value", "ev_ebitda", "ev_ebit", "ev_sales", "price_to_fcf", "fcf_yield", "net_debt", "ps"}


def map_metrics(raw: dict[str, Any]) -> dict[str, float]:
    """Tickertape screener metrics -> this app's metric names. Nulls, non-numbers and non-positive multiples are dropped."""
    out: dict[str, float] = {}
    for name, (field, _, positive) in FIELDS.items():
        v = raw.get(field)
        if isinstance(v, bool) or not isinstance(v, int | float) or v != v:
            continue
        if positive and v <= 0:
            continue
        out[name] = float(v)
    return out


def overlay(bundle: dict[str, MetricValue], row: ProviderRatios | None, sector_kind: str) -> list[str]:
    """Fill `missing` metrics in `bundle` from a stored Tickertape row, in place. Returns the metric names filled."""
    filled: list[str] = []
    for name in FIELDS:
        cur = bundle.get(name)
        if cur is None or cur.status != Status.MISSING:
            continue
        if sector_kind == "financial" and name in _FIN_NA:
            bundle[name] = MetricValue(name, None, Status.NOT_APPLICABLE, "not applicable to banks, NBFCs or insurers", cur.unit, cur.formula)
            continue
        if row is None:
            continue
        field, definition, positive = FIELDS[name]
        rv = (row.raw or {}).get(field)
        if name not in (row.values or {}):
            if positive and isinstance(rv, int | float) and not isinstance(rv, bool) and rv <= 0:
                bundle[name] = MetricValue(name, None, Status.NOT_MEANINGFUL, f"Tickertape reports {field} = {rv:.2f} (non-positive); "
                                           "the ratio is not meaningful", cur.unit, f"Tickertape: {definition}",
                                           {"provider": PROVIDER, "tickertape_field": field, "sid": row.provider_ref})
            continue
        bundle[name] = ok(name, row.values[name], METRIC_CATALOG.get(name, {}).get("unit", cur.unit), f"Tickertape: {definition}",
                          {"provider": PROVIDER, "tickertape_field": field, "sid": row.provider_ref,
                           "retrieved_at": row.retrieved_at.isoformat(), "source_url": row.source_url, "version": VERSION},
                          reason=f"NSE filings value unavailable ({cur.reason}); filled from Tickertape")
        filled.append(name)
    return filled


def parse_resolved(payload: Any) -> dict[str, str]:
    """`in_resolve_sid_read` output -> {NSE ticker: SID}. The tool returns `key:value|key:value` lines."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    out: dict[str, str] = {}
    for line in text.splitlines():
        kv = dict(p.split(":", 1) for p in line.strip().strip('"').split("|") if ":" in p)
        if kv.get("id") and kv.get("nseTicker"):
            out[kv["nseTicker"].upper()] = kv["id"]
    return out


def _chunks(xs: list[Any], n: int) -> Iterable[list[Any]]:
    for i in range(0, len(xs), n):
        yield xs[i: i + n]


async def targets(s: AsyncSession, now: Any) -> list[Instrument]:
    """Equities whose latest metrics miss a field Tickertape can supply, and whose stored Tickertape row (if any) is old."""
    rows = (await s.execute(select(MetricSnapshot, Instrument).join(Instrument, Instrument.id == MetricSnapshot.instrument_id)
                            .where(MetricSnapshot.is_latest.is_(True), Instrument.instrument_type != "INDEX"))).all()
    fresh = {iid for iid, at in (await s.execute(select(ProviderRatios.instrument_id, ProviderRatios.retrieved_at)
                                                 .where(ProviderRatios.provider == PROVIDER))).all() if now - at < MAX_AGE}
    out = []
    for m, i in rows:
        if i.id in fresh:
            continue
        if any((m.detail.get(n) or {}).get("status") == "missing" for n in FIELDS):
            out.append(i)
    return out


async def backfill(c: Container, s: AsyncSession, limit: int = 3000) -> dict[str, Any]:
    """Fetch Tickertape ratios for instruments with missing fundamentals and store them. Does not recompute metrics."""
    tt = c.tickertape
    if tt is None or not tt.configured:
        return {"status": "skipped", "reason": "Tickertape MCP is not configured"}
    if tt.auth is None or not tt.auth.authorised:
        return {"status": "skipped", "reason": "Tickertape login required"}
    now = c.clock.now()
    insts = (await targets(s, now))[:limit]
    if not insts:
        return {"status": "ok", "requested": 0, "stored": 0, "reason": "no instrument is missing a field Tickertape supplies"}
    known = dict((await s.execute(select(ProviderRatios.instrument_id, ProviderRatios.provider_ref)
                                  .where(ProviderRatios.provider == PROVIDER))).all())
    by_symbol = {i.symbol.upper(): i for i in insts}
    sid_of: dict[int, str] = {i.id: str(known[i.id]) for i in insts if known.get(i.id)}
    errors: list[str] = []
    unresolved = [sym for sym, i in by_symbol.items() if i.id not in sid_of]
    not_listed: list[str] = []

    async def resolve(batch: list[str]) -> None:
        # Tickertape fails a whole batch with 404 when one ticker is unknown, so a failing batch is halved until
        # the unknown tickers are isolated.
        try:
            got = parse_resolved(await tt.call("in_resolve_sid_read", {"identifiers": [{"ticker": t} for t in batch],
                                                                         "assetType": "stock", "exchange": "NSE"}))
        except ProviderUnavailable as e:
            if e.state == "login_required":
                raise
            if "404" in str(e) or "NOT_FOUND" in str(e):
                if len(batch) == 1:
                    not_listed.append(batch[0])
                    return
                mid = len(batch) // 2
                await resolve(batch[:mid])
                await resolve(batch[mid:])
                return
            errors.append(f"resolve: {e}"[:200])
            return
        for t, sid in got.items():
            if t in by_symbol:
                sid_of[by_symbol[t].id] = sid

    try:
        for batch in _chunks(unresolved, RESOLVE_BATCH):
            await resolve(batch)
    except ProviderUnavailable as e:
        errors.append(f"resolve: {e}"[:200])
    inst_by_sid = {sid: iid for iid, sid in sid_of.items()}
    existing = {r.instrument_id: r for r in (await s.scalars(select(ProviderRatios).where(ProviderRatios.provider == PROVIDER))).all()}
    project = sorted({f for f, _, _ in FIELDS.values()})
    stored = 0
    for batch in _chunks(sorted(inst_by_sid), SCREEN_BATCH):
        try:
            res = await tt.call("in_screen_stocks_read", {"match": "{}", "project": project, "sids": batch, "limit": SCREEN_BATCH})
        except ProviderUnavailable as e:
            errors.append(f"screen: {e}"[:200])
            if e.state == "login_required":
                break
            continue
        if isinstance(res, str):
            try:
                res = json.loads(res)
            except ValueError:
                errors.append("screen: response was not JSON")
                continue
        for r in (res or {}).get("results", []):
            iid = inst_by_sid.get(r.get("sid"))
            if iid is None:
                continue
            raw = r.get("metrics") or {}
            row = existing.get(iid)
            if row is None:
                row = ProviderRatios(instrument_id=iid, provider=PROVIDER)
                s.add(row)
                existing[iid] = row
            row.provider_ref, row.raw, row.values = r["sid"], raw, map_metrics(raw)
            row.source_url, row.retrieved_at = (r.get("slug") or "")[:500] or None, now
            stored += 1
    s.add(DataSourceRecord(provider=PROVIDER, dataset="fundamentals", status="ok" if stored else "unavailable",
                           reason="; ".join(errors)[:400] or None, rows=stored,
                           detail={"tool": "in_screen_stocks_read", "requested": len(insts), "resolved": len(sid_of),
                                   "not_listed_on_tickertape": not_listed[:200], "version": VERSION}))
    await s.commit()
    return {"status": "ok" if stored or not errors else "degraded", "requested": len(insts), "resolved": len(sid_of),
            "stored": stored, "not_listed_on_tickertape": len(not_listed), "errors": errors[:5]}


async def load(s: AsyncSession) -> dict[int, ProviderRatios]:
    return {r.instrument_id: r for r in (await s.scalars(select(ProviderRatios).where(ProviderRatios.provider == PROVIDER))).all()}
