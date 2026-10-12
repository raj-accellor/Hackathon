"""Composed refresh jobs. Each is a `JobFn(container, session) -> detail`."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.db.models import ThemeSnapshot
from app.domain.rrg import RRGError
from app.services import alerts, ingestion, metrics, sector_rotation, themes


async def instruments_job(c: Container, s: AsyncSession) -> dict[str, Any]:
    out = await ingestion.sync_instruments(c, s)
    out["builtin_themes_added"] = await themes.seed_builtin(s)
    return out


async def prices_job(c: Container, s: AsyncSession) -> dict[str, Any]:
    """Daily price refresh, then recompute metrics and invalidate caches."""
    prices = await ingestion.ingest_prices(c, s)
    await ingestion.resolve_broker_identifiers(c, s)
    fundamentals = await ingestion.ingest_fundamentals(c, s)
    m = await metrics.compute_all(c, s)
    await c.cache.delete_prefix("api:")
    return {"prices": prices, "fundamentals": fundamentals, "metrics": m}


async def rrg_job(c: Container, s: AsyncSession) -> dict[str, Any]:
    return await sector_rotation.refresh(c, s)


async def themes_job(c: Container, s: AsyncSession) -> dict[str, Any]:
    return await themes.refresh_all(c, s, "scheduled")


async def alerts_job(c: Container, s: AsyncSession) -> dict[str, Any]:
    return await alerts.evaluate_all(c, s)


async def full_refresh(c: Container, s: AsyncSession) -> dict[str, Any]:
    """Everything in dependency order. Used at first start and by the manual 'refresh all'."""
    out: dict[str, Any] = {"instruments": await instruments_job(c, s)}
    out.update(await prices_job(c, s))
    try:
        out["rrg"] = await rrg_job(c, s)
        out["metrics"] = await metrics.compute_all(c, s)  # pick up sector context from the new rotation data
    except RRGError as e:
        out["rrg"] = {"status": "failed", "error": str(e)}
    if not await s.scalar(select(func.count()).select_from(ThemeSnapshot)):
        try:
            out["themes"] = await themes.refresh_all(c, s, "initial")
        except themes.ThemeError as e:
            out["themes"] = {"status": "failed", "error": str(e)}
    out["alerts"] = await alerts.evaluate_all(c, s)
    return out
