"""FII/DII activity.

- Cash market: NSE's provisional FII/FPI and DII buy, sell and net values (INR crore, NSE + BSE + MSEI)
  for the latest trading day. NSE serves only that day, so each retrieval is stored in `fii_dii_flows`
  (one row per day and category) and the history is what this system has recorded. Days before the first
  recording are reported as not recorded; nothing is backfilled or estimated.
- Derivatives: NSE's participant-wise open interest file (Client, DII, FII, Pro), available from NSE's
  archive for any past trading day.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import nse_filings as nf
from app.connectors.base import ProviderUnavailable
from app.core.container import Container
from app.db.models import FiiDiiFlow, FpiDaily
from app.domain import derivatives as dv

SOURCE = "NSE fiidiiTradeReact (provisional, NSE+BSE+MSEI)"
CATEGORIES = {"FII/FPI": "fii", "FII": "fii", "FPI": "fii", "DII": "dii"}
MAX_LOOKBACK_DAYS = 10  # a holiday-heavy stretch; beyond this a missing archive file is an outage, not a holiday


def _dec(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v).replace(",", "").strip())
    except (ArithmeticError, ValueError, TypeError):
        return None


def _f(v: Decimal | None) -> float | None:
    return float(v) if v is not None else None


def parse_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        cat = CATEGORIES.get(str(r.get("category", "")).strip().upper().replace(" ", ""))
        try:
            day = datetime.strptime(str(r.get("date", "")).strip(), "%d-%b-%Y").date()
        except ValueError:
            continue
        if cat is None:
            continue
        out.append({"trade_date": day, "category": "FII/FPI" if cat == "fii" else "DII", "buy_value": _dec(r.get("buyValue")),
                    "sell_value": _dec(r.get("sellValue")), "net_value": _dec(r.get("netValue"))})
    return out


async def record_latest(c: Container, s: AsyncSession) -> dict[str, Any]:
    """Fetch the latest published day and store it (idempotent: a re-fetch updates the same rows)."""
    rows = parse_rows(await nf.fii_dii(c.filings))
    if not rows:
        raise ProviderUnavailable("NSE returned no FII/DII rows", "unavailable", "nse_filings")
    now = c.clock.now()
    for r in rows:
        existing = await s.scalar(select(FiiDiiFlow).where(FiiDiiFlow.trade_date == r["trade_date"], FiiDiiFlow.category == r["category"]))
        if existing is None:
            s.add(FiiDiiFlow(**r, source=SOURCE, retrieved_at=now))
        else:
            existing.buy_value, existing.sell_value, existing.net_value = r["buy_value"], r["sell_value"], r["net_value"]
            existing.retrieved_at = now
    await s.commit()
    return {"trade_date": max(r["trade_date"] for r in rows).isoformat(), "rows": len(rows)}


async def fii_dii_job(c: Container, s: AsyncSession) -> dict[str, Any]:
    """Each trading evening: NSE's provisional FII/DII day and NSDL's FPI figures for the current month."""
    if c.settings.is_demo:
        return {"status": "skipped", "reason": "demo mode"}
    today = c.clock.now().date()
    return await refresh_history(c, s, today.replace(day=1), today)


def _day_rows(flows: list[FiiDiiFlow]) -> list[dict[str, Any]]:
    by: dict[date, dict[str, Any]] = {}
    for f in flows:
        d = by.setdefault(f.trade_date, {"date": f.trade_date.isoformat(), "fii": None, "dii": None, "retrieved_at": None})
        key = "fii" if f.category == "FII/FPI" else "dii"
        d[key] = {"buy": _f(f.buy_value), "sell": _f(f.sell_value), "net": _f(f.net_value)}
        d["retrieved_at"] = max(filter(None, [d["retrieved_at"], f.retrieved_at.isoformat()]))
    out = sorted(by.values(), key=lambda r: r["date"], reverse=True)
    for r in out:
        nets = [x["net"] for x in (r["fii"], r["dii"]) if x and x["net"] is not None]
        r["combined_net"] = round(sum(nets), 2) if len(nets) == 2 else None
    return out


def streak(days: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    """Consecutive recorded days (newest first) on which `key` was a net buyer or a net seller."""
    vals = [d[key]["net"] for d in days if d.get(key) and d[key]["net"] is not None]
    if not vals or vals[0] == 0:
        return None
    sign = vals[0] > 0
    n = 0
    for v in vals:
        if (v > 0) != sign or v == 0:
            break
        n += 1
    return {"side": "buying" if sign else "selling", "days": n}


async def summary(c: Container, s: AsyncSession, days: int = 30) -> dict[str, Any]:
    latest_state: dict[str, Any] = {"state": "ok"}
    if c.settings.is_demo:
        latest_state = {"state": "not_configured", "reason": "FII/DII figures are real NSE data and are not shown in demo mode."}
    elif not c.filings.available:
        latest_state = {"state": "not_configured", "reason": "NSE_FILINGS_BASE_URL is not set"}
    else:
        try:
            latest_state = {"state": "ok", **await record_latest(c, s)}
        except ProviderUnavailable as e:
            await s.rollback()
            latest_state = {"state": e.state, "reason": str(e)[:300]}
    since = c.clock.now().date() - timedelta(days=days)
    flows = (await s.scalars(select(FiiDiiFlow).where(FiiDiiFlow.trade_date >= since).order_by(FiiDiiFlow.trade_date.desc()))).all()
    hist = _day_rows(list(flows))
    first = await s.scalar(select(FiiDiiFlow.trade_date).order_by(FiiDiiFlow.trade_date).limit(1))

    def total(key: str) -> float | None:
        v = [d[key]["net"] for d in hist if d.get(key) and d[key]["net"] is not None]
        return round(sum(v), 2) if v else None

    return {"latest": hist[0] if hist else None, "latest_state": latest_state, "history": hist, "window_days": days,
            "recorded_days": len(hist), "first_recorded": first.isoformat() if first else None,
            "totals": {"fii_net": total("fii"), "dii_net": total("dii")},
            "streaks": {"fii": streak(hist, "fii"), "dii": streak(hist, "dii")},
            "unit": "INR crore", "source": SOURCE,
            "notes": ["Provisional figures as published by NSE after the close; final NSDL/CDSL data can differ.",
                      "NSE publishes only the latest day. History covers the days this system retrieved, starting "
                      f"{first.isoformat() if first else 'with the first retrieval'}; the scheduled job records each trading evening."]}


async def participant_oi(c: Container, day: date | None = None) -> dict[str, Any]:
    """Participant-wise derivatives open interest for `day` (or the latest published day), with the change
    against the previous published day."""
    tpl = c.settings.nse_participant_oi_url_template
    if c.settings.is_demo or not tpl:
        return {"state": "not_configured", "reason": "demo mode" if c.settings.is_demo else "NSE_PARTICIPANT_OI_URL_TEMPLATE is not set"}
    want = day or c.clock.now().date()
    found: list[tuple[date, list[dict[str, Any]]]] = []
    d = want
    try:
        while len(found) < 2 and (want - d).days <= MAX_LOOKBACK_DAYS + 5:
            if d.weekday() < 5 and (rows := await nf.participant_oi(c.filings, tpl, d)) is not None:
                found.append((d, dv.participant_positions(rows)))
            d -= timedelta(days=1)
    except ProviderUnavailable as e:
        if not found:
            return {"state": e.state, "reason": str(e)[:300]}
    if not found:
        return {"state": "unavailable", "reason": f"NSE has no participant OI file in the {MAX_LOOKBACK_DAYS} days up to {want.isoformat()}"}
    (d0, cur), prev = found[0], (found[1] if len(found) > 1 else None)
    prev_by = {p["participant"]: p for p in prev[1]} if prev else {}
    for p in cur:
        q = prev_by.get(p["participant"])
        for k in ("future_index_net", "future_stock_net", "option_index_call_net", "option_index_put_net"):
            p[f"{k}_change"] = (p[k] - q[k]) if q and p[k] is not None and q.get(k) is not None else None
    return {"state": "ok", "date": d0.isoformat(), "requested": want.isoformat(), "previous_date": prev[0].isoformat() if prev else None,
            "participants": cur, "unit": "contracts", "source": "NSE participant-wise open interest (archive file)"}


# ------------------------------------------------------------------ history (NSDL FPI + recorded NSE FII/DII)
NSDL_SOURCE = "NSDL FPI daily trends (archive)"
MAX_HISTORY_DAYS = 3 * 366
FPI_COLUMNS = {"equity_exchange_buy", "equity_exchange_sell", "equity_exchange_net", "equity_primary_net", "equity_net",
               "equity_net_usd_mn", "total_net", "usd_inr"}


def _months(start: date, end: date) -> list[date]:
    out, d = [], start.replace(day=1)
    while d <= end:
        out.append(d)
        d = (d + timedelta(days=32)).replace(day=1)
    return out


async def refresh_history(c: Container, s: AsyncSession, start: date, end: date) -> dict[str, Any]:
    """Fetch NSDL's daily FPI figures for every month in [start, end] and NSE's latest FII/DII day, and store them.
    Idempotent: a re-fetch updates the same rows. Months that fail are reported, never filled in."""
    if c.settings.is_demo:
        return {"state": "not_configured", "reason": "FII/DII figures are real data and are not fetched in demo mode."}
    out: dict[str, Any] = {"months": [], "failed": [], "fpi_days": 0}
    now = c.clock.now()
    for m in _months(start, end):
        try:
            rows = await c.nsdl.month(m)
        except ProviderUnavailable as e:
            out["failed"].append({"month": m.strftime("%Y-%m"), "state": e.state, "reason": str(e)[:200]})
            if e.state == "not_configured":
                break
            continue
        for r in rows:
            vals = {k: (Decimal(str(v)) if isinstance(v, int | float) else v) for k, v in r.items() if k in FPI_COLUMNS}
            existing = await s.scalar(select(FpiDaily).where(FpiDaily.reporting_date == r["reporting_date"]))
            if existing is None:
                s.add(FpiDaily(reporting_date=r["reporting_date"], **vals, source=NSDL_SOURCE, retrieved_at=now))
            else:
                for k, v in vals.items():
                    setattr(existing, k, v)
                existing.retrieved_at = now
        out["months"].append(m.strftime("%Y-%m"))
        out["fpi_days"] += len(rows)
    await s.commit()
    try:
        out["nse_latest"] = await record_latest(c, s) if c.filings.available else {"state": "not_configured"}
    except ProviderUnavailable as e:
        await s.rollback()
        out["nse_latest"] = {"state": e.state, "reason": str(e)[:200]}
    out["state"] = "ok" if out["months"] else ("not_configured" if out["failed"] and out["failed"][0]["state"] == "not_configured"
                                              else "unavailable")
    return out


async def history(c: Container, s: AsyncSession, start: date, end: date) -> dict[str, Any]:
    """Per date: NSDL FPI equity flows (by NSDL reporting date) and NSE's provisional FII/FPI and DII figures where recorded."""
    fpi = (await s.scalars(select(FpiDaily).where(FpiDaily.reporting_date.between(start, end)))).all()
    nse = _day_rows(list((await s.scalars(select(FiiDiiFlow).where(FiiDiiFlow.trade_date.between(start, end)))).all()))
    by: dict[str, dict[str, Any]] = {d["date"]: {"date": d["date"], "nse": d, "fpi": None} for d in nse}
    for f in fpi:
        k = f.reporting_date.isoformat()
        by.setdefault(k, {"date": k, "nse": None, "fpi": None})["fpi"] = {
            "buy": _f(f.equity_exchange_buy), "sell": _f(f.equity_exchange_sell), "net": _f(f.equity_exchange_net),
            "equity_net": _f(f.equity_net), "equity_net_usd_mn": _f(f.equity_net_usd_mn), "primary_net": _f(f.equity_primary_net),
            "total_net": _f(f.total_net), "usd_inr": _f(f.usd_inr), "retrieved_at": f.retrieved_at.isoformat()}
    rows = sorted(by.values(), key=lambda r: r["date"], reverse=True)

    def total(get: Any) -> float | None:
        vals = [v for r in rows if (v := get(r)) is not None]
        return round(sum(vals), 2) if vals else None

    first_fpi = await s.scalar(select(FpiDaily.reporting_date).order_by(FpiDaily.reporting_date).limit(1))
    last_fetch = await s.scalar(select(FpiDaily.retrieved_at).order_by(FpiDaily.retrieved_at.desc()).limit(1))
    return {"start": start.isoformat(), "end": end.isoformat(), "rows": rows, "unit": "INR crore",
            "totals": {"fpi_equity_exchange_net": total(lambda r: (r["fpi"] or {}).get("net")),
                       "fpi_equity_net": total(lambda r: (r["fpi"] or {}).get("equity_net")),
                       "nse_fii_net": total(lambda r: ((r["nse"] or {}).get("fii") or {}).get("net")),
                       "nse_dii_net": total(lambda r: ((r["nse"] or {}).get("dii") or {}).get("net"))},
            "coverage": {"fpi_days": len(fpi), "nse_days": len(nse), "first_fpi_date": first_fpi.isoformat() if first_fpi else None,
                         "last_fetched_at": last_fetch.isoformat() if last_fetch else None},
            "sources": {"fpi": NSDL_SOURCE, "nse": SOURCE, "nsdl": c.nsdl.status()},
            "notes": ["FPI (NSDL): equity bought and sold through stock exchanges, by NSDL reporting date. NSDL states each day's "
                      "report covers FPI trades of the previous trading day.",
                      "FII/FPI and DII (NSE): provisional figures for the trading day, recorded by this app each evening. No public "
                      "source publishes past DII figures, so DII history starts with this app's first recording.",
                      "Use Refresh historical data to fetch the selected range from NSDL and NSE again."]}
