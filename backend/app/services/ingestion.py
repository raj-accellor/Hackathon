"""Instrument master, price history and fundamentals ingestion with lineage.

Network fetches run with bounded concurrency; database writes are sequential in one session so the
job is safe on SQLite and idempotent on re-run (bars are upserted by instrument + date).
"""

from __future__ import annotations

import asyncio
import csv
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.db.models import (
    CorporateActionRow,
    DataSourceRecord,
    FinancialPeriod,
    Instrument,
    InstrumentIdentifier,
    PriceBar,
    SourceConflict,
)
from app.services.market_data import DataUnavailable, HistoryBundle, SourceStatus

OVERLAP_DAYS = 10


def _dec(v: Any, nd: int = 4) -> Decimal | None:
    if v is None or (isinstance(v, float) and v != v):
        return None
    return Decimal(str(round(float(v), nd)))


def universe_rows(c: Container) -> list[dict[str, Any]]:
    """Instrument master input. Demo: the synthetic universe. Live: the reference CSV."""
    if c.settings.is_demo:
        from app.devtools import demo_data as dd

        idx = {i.sector: i.symbol for i in dd.indices() if i.symbol != dd.BENCHMARK}
        rows = [{"symbol": i.symbol, "name": i.name, "sector": i.sector, "industry": None, "sector_kind": i.sector_kind,
                 "instrument_type": "EQ", "sector_index_symbol": idx.get(i.sector), "classification_source": "demo_generator"}
                for i in dd.instruments()]
        rows += [{"symbol": i.symbol, "name": i.name, "sector": None if i.symbol == dd.BENCHMARK else i.sector, "industry": None,
                  "sector_kind": "index", "instrument_type": "INDEX", "sector_index_symbol": None,
                  "classification_source": "demo_generator"} for i in dd.indices()]
        return rows
    path = Path(c.settings.universe_file)
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [{k: (v.strip() or None) if isinstance(v, str) else v for k, v in r.items()}
                for r in csv.DictReader(line for line in f if not line.startswith("#"))]


async def sync_instruments(c: Container, s: AsyncSession) -> dict[str, int]:
    rows = universe_rows(c)
    existing = {i.symbol: i for i in (await s.scalars(select(Instrument).where(Instrument.exchange == "NSE"))).all()}
    created = updated = 0
    for r in rows:
        sym = str(r["symbol"]).upper()
        inst = existing.get(sym)
        if inst is None:
            inst = Instrument(exchange="NSE", symbol=sym)
            s.add(inst)
            created += 1
        else:
            updated += 1
        inst.name = r.get("name")
        inst.sector, inst.industry = r.get("sector"), r.get("industry")
        inst.sector_kind = r.get("sector_kind") or "industrial"
        inst.instrument_type = r.get("instrument_type") or "EQ"
        inst.sector_index_symbol = r.get("sector_index_symbol")
        inst.isin = r.get("isin") or inst.isin
        inst.classification_source = r.get("classification_source") or "reference_file"
        inst.is_demo = c.settings.is_demo
    # A database must never mix demo and live instruments.
    stale = [i for sym, i in existing.items() if i.is_demo != c.settings.is_demo]
    for i in stale:
        await s.delete(i)
    await s.commit()
    return {"created": created, "updated": updated, "removed_other_mode": len(stale), "total": len(rows)}


async def _record_sources(s: AsyncSession, inst_id: int, sources: list[SourceStatus], detail: dict[str, Any] | None = None) -> None:
    for st in sources:
        s.add(DataSourceRecord(instrument_id=inst_id, provider=st.provider, source_id=st.source_id, dataset=st.dataset,
                               status=st.status, reason=st.reason, rows=st.rows, adjustment=st.adjustment,
                               as_of=date.fromisoformat(st.last_bar) if st.last_bar else None,
                               detail=detail if st.dataset == "history" and st.status == "ok" else None))


async def _write_history(c: Container, s: AsyncSession, inst: Instrument, b: HistoryBundle, replace_all: bool) -> int:
    if replace_all:
        await s.execute(delete(PriceBar).where(PriceBar.instrument_id == inst.id))
        existing: dict[date, PriceBar] = {}
    else:
        lo = b.frame.index[0].date()
        existing = {p.date: p for p in (await s.scalars(select(PriceBar).where(PriceBar.instrument_id == inst.id, PriceBar.date >= lo))).all()}
    now = datetime.now(UTC)
    adj = {st.provider: st.adjustment for st in b.sources if st.dataset == "history" and st.status == "ok"}
    for ts, r in b.frame.iterrows():
        d = ts.date()
        bar = existing.get(d)
        if bar is None:
            bar = PriceBar(instrument_id=inst.id, date=d, close=_dec(r["close"]))
            s.add(bar)
        bar.open, bar.high, bar.low, bar.close = _dec(r.get("open")), _dec(r.get("high")), _dec(r.get("low")), _dec(r["close"])  # type: ignore[assignment]
        bar.volume, bar.traded_value = _dec(r.get("volume"), 2), _dec(r.get("traded_value"), 2)
        bar.provider, bar.adjustment = r["provider"], adj.get(r["provider"]) or "unadjusted"
        bar.retrieved_at, bar.is_demo = now, b.is_demo
    have = {(a.ex_date, a.kind, a.description) for a in (await s.scalars(select(CorporateActionRow).where(CorporateActionRow.instrument_id == inst.id))).all()}
    for a in b.actions:
        if (a.ex_date, a.kind, a.description[:300]) not in have:
            s.add(CorporateActionRow(instrument_id=inst.id, ex_date=a.ex_date, kind=a.kind, ratio=_dec(a.ratio, 6),
                                     description=a.description[:300], provider="nse_mcp"))
    known = {(x.field, x.obs_date): x for x in (await s.scalars(select(SourceConflict).where(SourceConflict.instrument_id == inst.id))).all()}
    for cf in b.conflicts:
        k = (cf["field"], date.fromisoformat(cf["date"]))
        row = known.get(k)
        if row is None:
            row = SourceConflict(instrument_id=inst.id, field=cf["field"], obs_date=k[1], values={}, diff_pct=0, tolerance_pct=0, chosen="")
            s.add(row)
        row.values, row.diff_pct, row.tolerance_pct = cf["values"], _dec(cf["diff_pct"]), _dec(cf["tolerance_pct"])  # type: ignore[assignment]
        row.chosen, row.detected_at = cf["chosen"], now
    await _record_sources(s, inst.id, b.sources, {"coverage": b.coverage, "warnings": b.warnings[:20]})
    return len(b.frame)


async def ingest_prices(c: Container, s: AsyncSession, symbols: list[str] | None = None, full: bool = False) -> dict[str, Any]:
    q = select(Instrument).where(Instrument.trading_status == "active")
    if symbols:
        q = q.where(Instrument.symbol.in_([x.upper() for x in symbols]))
    insts = list((await s.scalars(q)).all())
    today = c.clock.now().date()
    last = dict((await s.execute(select(PriceBar.instrument_id, func.max(PriceBar.date)).group_by(PriceBar.instrument_id))).all())
    n_actions = dict((await s.execute(select(CorporateActionRow.instrument_id, func.count()).group_by(CorporateActionRow.instrument_id))).all())
    full_start = today - timedelta(days=int(365.25 * c.settings.history_years) + 21)  # margin so N-year windows have enough bars
    sem = asyncio.Semaphore(c.settings.ingest_max_concurrency)

    async def fetch(inst: Instrument) -> tuple[Instrument, HistoryBundle | DataUnavailable, bool]:
        start = full_start if full or inst.id not in last else last[inst.id] - timedelta(days=OVERLAP_DAYS)
        async with sem:
            try:
                b = await c.market_data.history(inst.symbol, start, today, inst.instrument_type)
                # A newly seen split or bonus changes every earlier adjusted price: reload everything.
                splits = sum(1 for a in b.actions if a.kind in ("split", "bonus"))
                if start != full_start and splits and len(b.actions) != n_actions.get(inst.id, 0):
                    b = await c.market_data.history(inst.symbol, full_start, today, inst.instrument_type)
                    return inst, b, True
                return inst, b, start == full_start
            except DataUnavailable as e:
                return inst, e, False

    results = await asyncio.gather(*(fetch(i) for i in insts))
    out: dict[str, Any] = {"instruments": len(insts), "ok": 0, "failed": 0, "bars": 0, "conflicts": 0, "broker_filled_bars": 0,
                           "failures": [], "warnings": 0}
    for inst, res, replace in results:
        if isinstance(res, DataUnavailable):
            out["failed"] += 1
            out["failures"].append({"symbol": inst.symbol, "reason": str(res)[:300]})
            await _record_sources(s, inst.id, res.sources)
            continue
        out["bars"] += await _write_history(c, s, inst, res, replace)
        out["ok"] += 1
        out["conflicts"] += len(res.conflicts)
        out["warnings"] += len(res.warnings)
        out["broker_filled_bars"] += sum(v for k, v in res.coverage.items() if k == "kite_mcp")
    out["failures"] = out["failures"][:50]
    await s.commit()
    await c.cache.delete_prefix("quote:")
    return out


async def ingest_fundamentals(c: Container, s: AsyncSession) -> dict[str, Any]:
    insts = (await s.scalars(select(Instrument).where(Instrument.instrument_type != "INDEX"))).all()
    if c.settings.is_demo:
        from app.devtools import demo_data as dd

        for i in insts:
            if c.fundamentals.get_statements(i.symbol) is None and dd.by_symbol(i.symbol):
                c.fundamentals.save(i.symbol, dd.fundamentals(i.symbol))
    loaded = missing = periods = 0
    for inst in insts:
        doc = c.fundamentals.get_statements(inst.symbol)
        if doc is None:
            missing += 1
            continue
        loaded += 1
        await s.execute(delete(FinancialPeriod).where(FinancialPeriod.instrument_id == inst.id, FinancialPeriod.source == doc["source"]))
        for p in doc["periods"]:
            meta = ("period_end", "period_type", "fiscal_year", "published_at")
            s.add(FinancialPeriod(
                instrument_id=inst.id, period_end=date.fromisoformat(p["period_end"]), period_type=p["period_type"],
                fiscal_year=p.get("fiscal_year"), currency=doc["currency"], unit="crore", reported_unit=doc.get("reported_unit"),
                source=doc["source"], source_url=doc.get("source_url"),
                published_at=date.fromisoformat(p["published_at"]) if p.get("published_at") else None,
                ingested_at=datetime.fromisoformat(doc["ingested_at"]), facts={k: v for k, v in p.items() if k not in meta},
                is_demo=bool(doc.get("is_demo"))))
            periods += 1
        s.add(DataSourceRecord(instrument_id=inst.id, provider="fundamentals_file", source_id=doc["source"][:80], dataset="fundamentals",
                               status="ok", rows=len(doc["periods"])))
    await s.commit()
    return {"instruments": len(insts), "with_statements": loaded, "without_statements": missing, "periods": periods}


async def resolve_broker_identifiers(c: Container, s: AsyncSession) -> int:
    """Store broker instrument tokens discovered during history calls (lineage only)."""
    n = 0
    cached = getattr(c.kite, "_instruments", {})
    for inst in (await s.scalars(select(Instrument))).all():
        info = cached.get(inst.symbol)
        if not info or any(i.provider == "kite_mcp" for i in inst.identifiers):
            continue
        inst.identifiers.append(InstrumentIdentifier(provider="kite_mcp", kind="instrument_token", value=str(info.get("instrument_token"))))
        if info.get("isin") and not inst.isin:
            inst.isin = str(info["isin"])[:12]
        if info.get("lot_size") is not None:
            inst.lot_size = int(float(info["lot_size"]))
        n += 1
    await s.commit()
    return n


async def load_bars(s: AsyncSession, instrument_ids: list[int] | None = None, since: date | None = None) -> dict[int, pd.DataFrame]:
    q = select(PriceBar.instrument_id, PriceBar.date, PriceBar.open, PriceBar.high, PriceBar.low, PriceBar.close, PriceBar.volume,
               PriceBar.traded_value, PriceBar.provider)
    if instrument_ids is not None:
        q = q.where(PriceBar.instrument_id.in_(instrument_ids))
    if since:
        q = q.where(PriceBar.date >= since)
    rows = (await s.execute(q.order_by(PriceBar.instrument_id, PriceBar.date))).all()
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["iid", "date", "open", "high", "low", "close", "volume", "traded_value", "provider"])
    num = ["open", "high", "low", "close", "volume", "traded_value"]
    df[num] = df[num].astype(float)
    df["date"] = pd.to_datetime(df["date"])
    return {int(k): g.drop(columns="iid").set_index("date") for k, g in df.groupby("iid")}


def bundle_summary(b: HistoryBundle) -> dict[str, Any]:
    return {"coverage": b.coverage, "conflicts": b.conflicts[:20], "sources": [asdict(x) for x in b.sources], "warnings": b.warnings}
