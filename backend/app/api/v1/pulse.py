"""Market pulse routes: headline indices, stocks in the news, macro indicators, FII/DII flows and F&O open interest."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.deps import Actor, ApiError, Ctx, Db, meta, rate_limit
from app.services import derivatives, flows, macro, news
from app.services.jobs import audit

router = APIRouter()


@router.get("/markets/indices")
async def indices(c: Ctx) -> dict[str, Any]:
    """Nifty 50, Sensex and Nifty Bank: latest delayed value, change against the previous close, day and 52-week range."""
    return {**meta(c), "indices": await macro.headline_indices(c)}


@router.get("/markets/nifty50")
async def nifty50(c: Ctx) -> dict[str, Any]:
    """Nifty 50 constituents with latest price and change (NSE, then Zerodha Kite for gaps), plus the index value."""
    return {**meta(c), **await derivatives.nifty50_ticker(c)}


@router.get("/markets/news", dependencies=[rate_limit("default")])
async def stocks_in_news(c: Ctx, s: Db, days: Annotated[int, Query(ge=1, le=7)] = 1, include_routine: bool = False,
                         refresh: bool = False) -> dict[str, Any]:
    """Stocks named in NSE announcements and news headlines in the last `days` days, with the move since the news.
    `refresh=true` bypasses the caches and asks every source again."""
    return {**meta(c), **await news.stocks_in_news(c, s, days, include_routine, refresh)}


@router.get("/macro")
async def macro_dashboard(c: Ctx) -> dict[str, Any]:
    return {**meta(c), **await macro.dashboard(c)}


@router.get("/flows/fii-dii")
async def fii_dii(c: Ctx, s: Db, days: Annotated[int, Query(ge=1, le=730)] = 30) -> dict[str, Any]:
    """Latest published FII/DII cash-market day (recorded on every call) and the recorded history for `days` days."""
    return {**meta(c), **await flows.summary(c, s, days)}


def _range(c: Any, start: date | None, end: date | None) -> tuple[date, date]:
    end = end or c.clock.now().date()
    start = start or end - timedelta(days=90)
    if start > end:
        raise ApiError(422, "invalid_range", "start must be on or before end")
    if (end - start).days > flows.MAX_HISTORY_DAYS:
        raise ApiError(422, "range_too_long", f"choose a range of at most {flows.MAX_HISTORY_DAYS} days")
    return start, end


@router.get("/flows/history")
async def flows_history(c: Ctx, s: Db, start: date | None = None, end: date | None = None) -> dict[str, Any]:
    """Stored FII/DII history for a date range (default: the last 90 days): NSDL FPI and NSE's provisional FII/DII."""
    a, b = _range(c, start, end)
    return {**meta(c), **await flows.history(c, s, a, b)}


class HistoryRefreshIn(BaseModel):
    start: date | None = None
    end: date | None = None


@router.post("/flows/history/refresh", dependencies=[rate_limit("refresh")])
async def flows_history_refresh(c: Ctx, s: Db, actor: Actor, body: HistoryRefreshIn | None = None) -> dict[str, Any]:
    """Fetch the range again from NSDL (FPI) and NSE (latest FII/DII day), store it, and return the updated history."""
    a, b = _range(c, body.start if body else None, body.end if body else None)
    result = await flows.refresh_history(c, s, a, b)
    await audit(s, actor, "flows.history_refresh", "flows", None, {"start": a.isoformat(), "end": b.isoformat(), "state": result.get("state"),
                                                                    "fpi_days": result.get("fpi_days")})
    await s.commit()
    return {**meta(c), "refresh": result, **await flows.history(c, s, a, b)}


@router.get("/flows/participant-oi")
async def participant_oi(c: Ctx, on: date | None = None) -> dict[str, Any]:
    """Participant-wise (Client, DII, FII, Pro) derivatives open interest for a trading day; latest when `on` is omitted."""
    return {**meta(c), **await flows.participant_oi(c, on)}


@router.get("/derivatives/movers")
async def movers(c: Ctx, segment: Literal["all", "nifty", "banknifty", "niftynext50"] = "all",
                 limit: Annotated[int, Query(ge=1, le=50)] = 15) -> dict[str, Any]:
    """Live (delayed) top gainers and losers in the cash market."""
    return {**meta(c), **await derivatives.live_movers(c, segment, limit)}


@router.get("/derivatives/oi")
async def open_interest(c: Ctx, on: date | None = None, limit: Annotated[int, Query(ge=5, le=50)] = 15) -> dict[str, Any]:
    """End-of-day F&O analytics: OI gainers and losers, and long/short buildup, short covering and long unwinding."""
    return {**meta(c), **await derivatives.oi_dashboard(c, on, limit)}
