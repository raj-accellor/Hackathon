"""Stocks in the news and how far each has moved since.

News comes from two places, each kept with its source:
- NSE corporate announcements filed by every listed company (primary source). Routine filings
  (newspaper copies, trading-window notices, meeting schedules and the like) are hidden by default.
- Headlines from the configured web search provider (secondary), linked to a stock only when the stock's
  symbol or company name appears in the headline.

"Moved since the news" is the latest delayed NSE price against a reference close: the previous close
when the news came before or during the session it is compared with, otherwise the close of the session
in which (or after which) the news came. The reference rule is reported with every row.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import nse_filings as nf
from app.connectors.base import ProviderUnavailable
from app.core.container import Container
from app.db.models import Instrument, PriceBar
from app.domain import news_sentiment as ns
from app.services import kite_fallback

IST = ZoneInfo("Asia/Kolkata")
MARKET_CLOSE = time(15, 30)
CACHE_SECONDS = 60  # the page polls every 2 minutes, so each poll sees data at most a minute old
ANNOUNCEMENTS_TTL_SECONDS = 60
LAST_GOOD_SECONDS = 7 * 24 * 3600  # shown, marked stale, while a source is down
MAX_REFERENCE_LOOKUPS = 40
MAX_LIVE_QUOTES = 80
PREFIX_SNAPSHOT_FROM = 25
QUOTE_TIMEOUT_SECONDS = 8.0
NEWS_QUERIES = ("Indian stock market news today shares NSE", "stocks in news today India Sensex Nifty")
ROUTINE = ("copy of newspaper publication", "trading window", "certificate under sebi", "monitoring agency report", "esop/esos/esps",
           "corrigendum", "statement of deviation", "committee meeting updates", "amendment to aoa", "loss of share certificate",
           "duplicate share certificate", "analysts/institutional investor meet", "shareholders meeting", "record date",
           "allotment of securities", "investor complaints", "compliance certificate", "reg. 74", "book closure", "postal ballot")
_NAME_NOISE = re.compile(r"\b(limited|ltd|\(india\)|india|\(i\)|corporation|company|co|the|and|&)\b\.?", re.I)


def is_routine(category: str) -> bool:
    c = category.lower()
    return any(r in c for r in ROUTINE)


def _parse_nse_time(v: Any) -> datetime | None:
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(v).strip(), fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def announcement_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        sym = str(r.get("symbol") or "").strip().upper()
        at = _parse_nse_time(r.get("an_dt") or r.get("sort_date"))
        if not sym or at is None:
            continue
        cat = re.sub(r"\s+", " ", str(r.get("desc") or "Announcement")).strip()
        text = re.sub(r"\s+", " ", str(r.get("attchmntText") or "")).strip()
        url = str(r.get("attchmntFile") or "").strip() or None
        out.append({"id": f"nse-{r.get('seq_id') or sym + at.isoformat()}", "kind": "exchange_filing", "source_type": "primary",
                    "source": "NSE corporate announcement", "headline": f"{r.get('sm_name') or sym}: {cat}", "category": cat,
                    "summary": text[:1200] or None, "url": url if url and url.startswith("https://") else None,
                    "published_at": at.isoformat(), "symbols": [sym], "industry": (str(r.get("smIndustry") or "").strip() or None),
                    "routine": is_routine(cat)})
    return out


def short_name(name: str | None) -> str | None:
    """Company name without legal suffixes, e.g. "Reliance Industries Limited" -> "Reliance Industries"."""
    if not name:
        return None
    s = re.sub(r"\s+", " ", _NAME_NOISE.sub(" ", name)).strip(" .,-")
    return s if len(s) >= 5 else None


def match_symbols(text: str, names: dict[str, str], symbols: set[str]) -> list[str]:
    """Stocks named in a headline: an exact upper-case symbol of 4+ letters, or the company's short name."""
    found = {w for w in re.findall(r"\b[A-Z][A-Z0-9&]{3,}\b", text) if w in symbols}
    low = text.lower()
    for sym, nm in names.items():
        if nm and re.search(rf"(?<![a-z0-9]){re.escape(nm.lower())}(?![a-z0-9])", low):
            found.add(sym)
    return sorted(found)


async def web_items(c: Container, names: dict[str, str], symbols: set[str],
                    since: date | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Search headlines dated on or after `since` (undated results are dropped: their age cannot be shown)."""
    status = c.search.status()
    if status.get("state") not in ("ok", None):
        return [], {"state": status.get("state"), "reason": status.get("reason")}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        for q in NEWS_QUERIES:
            for it in await c.search.search(q, c.settings.web_search_max_results):
                if it["url"] in seen or (since and not (it.get("published_at") or "")[:10] >= since.isoformat()):
                    continue
                seen.add(it["url"])
                out.append({"id": f"web-{len(out) + 1}", "kind": "news", "source_type": it.get("source_type", "secondary"),
                            "source": it.get("provider", "web search"), "headline": it["title"], "category": "News",
                            "summary": it.get("snippet") or None, "url": it["url"], "published_at": it.get("published_at"),
                            "retrieved_at": it.get("retrieved_at"), "symbols": match_symbols(it["title"], names, symbols), "routine": False})
    except ProviderUnavailable as e:
        return out, {"state": e.state, "reason": str(e)[:200]}
    return out, {"state": "ok"}


def news_time(iso: str) -> datetime:
    """A sortable IST time for a news timestamp; a date without a time counts as the start of that day."""
    t = datetime.fromisoformat(iso)
    return t.replace(tzinfo=IST) if t.tzinfo is None else t.astimezone(IST)


def reference_day(published: datetime) -> tuple[date, bool]:
    """(news day in IST, whether it came after the close). A date without a time (some search results)
    counts as the start of that day."""
    local = published.replace(tzinfo=IST) if published.tzinfo is None else published.astimezone(IST)
    return local.date(), local.time() >= MARKET_CLOSE


async def _reference_close(c: Container, s: AsyncSession, iid: int | None, symbol: str, upto: date,
                           allow_mcp: bool = True) -> tuple[str | None, float | None, str]:
    """The last close on or before `upto`: stored bars first, then (when allowed) the NSE bhavcopy MCP."""
    if iid is not None:
        row = (await s.execute(select(PriceBar.date, PriceBar.close).where(PriceBar.instrument_id == iid, PriceBar.date <= upto,
                                                                           PriceBar.date >= upto - timedelta(days=10))
                               .order_by(PriceBar.date.desc()).limit(1))).first()
        if row is not None:
            return row[0].isoformat(), float(row[1]), "stored daily bars"
    if not allow_mcp:
        return None, None, "budget"
    try:
        res = await c.nse.close_on(symbol, upto)
    except ProviderUnavailable:
        return None, None, "unavailable"
    if res is None:
        return None, None, "unavailable"
    return (res[0].isoformat() if res[0] else None), res[1], "NSE bhavcopy MCP (get_ltp_by_date)"


async def live_quotes(c: Container, symbols: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Prices in order of preference: delayed live quotes from the NSE CM market MCP, then live quotes from the
    Zerodha Kite MCP for symbols NSE could not price, then NSE's end-of-day bulk snapshot. The end-of-day request
    runs alongside the live ones, so an NSE outage costs one timeout, not two."""
    if not symbols:
        return {}, {"state": "ok", "live": 0, "end_of_day": 0}
    health = c.nse.health() if hasattr(c.nse, "health") else {}
    paused = {k for k, h in health.items() if isinstance(h, dict) and h.get("breaker") == "open"}

    async def end_of_day() -> dict[str, dict[str, Any]]:
        if "nse_bhavcopy" in paused:
            return {}
        try:
            return await asyncio.wait_for(c.nse.bulk_quotes(symbols), QUOTE_TIMEOUT_SECONDS)
        except (ProviderUnavailable, TimeoutError):
            return {}

    eod_task = asyncio.create_task(end_of_day())
    live: dict[str, dict[str, Any]] = {}
    # once the live server fails (or its circuit breaker is open), the remaining live calls are skipped
    down = "nse_cm_market" in paused
    if len(symbols) > PREFIX_SNAPSHOT_FROM and not down:
        # many symbols: one equity-list call per leading letter is cheaper than one quote call each
        for letter in sorted({x[0] for x in symbols}):
            try:
                snap, _capped = await asyncio.wait_for(c.nse.equity_snapshot(letter), QUOTE_TIMEOUT_SECONDS)
            except (ProviderUnavailable, TimeoutError):
                down = True
                break
            live.update({sym: {**snap[sym], "basis": "live (delayed)"} for sym in symbols if sym in snap})

    async def one(sym: str) -> None:
        nonlocal down
        if down:
            return
        try:
            q = await asyncio.wait_for(c.nse.get_quote(sym), QUOTE_TIMEOUT_SECONDS)
        except (ProviderUnavailable, TimeoutError) as e:
            down = down or isinstance(e, TimeoutError) or getattr(e, "state", "") == "unavailable"
            return
        f = q.fields
        if f["last_price"].value is None:
            return
        as_of = f["last_price"].as_of
        live[sym] = {"last_price": f["last_price"].value, "prev_close": f["prev_close"].value, "change_pct": f["change_pct"].value,
                     "as_of": as_of.isoformat() if as_of else None, "basis": "live (delayed)", "source_id": f["last_price"].source_id}

    await asyncio.gather(*(one(x) for x in [x for x in symbols if x not in live][:MAX_LIVE_QUOTES]))
    # NSE could not price some symbols: the broker (Zerodha Kite MCP) fills them with its live quotes
    need = [x for x in symbols if x not in live]
    broker: dict[str, dict[str, Any]] = {}
    broker_state: dict[str, Any] = {"state": "not_needed"}
    if need:
        got, broker_state = await kite_fallback.equity_quotes(c, need)
        broker = {k: {**v, "basis": "live (Zerodha Kite)"} for k, v in got.items() if k in need}
    eod = await eod_task
    out = {**live, **broker}
    for sym in symbols:
        if sym not in out and sym in eod:
            out[sym] = {**eod[sym], "basis": "end of day"}
    n_eod = len(out) - len(live) - len(broker)
    state = "ok" if len(out) == len(symbols) else ("degraded" if out else "unavailable")
    return out, {"state": state, "live": len(live), "broker": len(broker), "broker_state": broker_state, "end_of_day": n_eod,
                 "missing": len(symbols) - len(out)}


def _prev_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


async def _with_last_good(c: Container, key: str, fetch: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run a source fetch; keep a successful result, and when the source fails serve the last good one marked stale.
    An outage must not empty the page: the items stay, with the time they were retrieved and the reason."""
    items, status = await fetch()
    now = c.clock.now().isoformat()
    if status.get("state") == "ok":
        await c.cache.set(key, {"items": items, "retrieved_at": now}, LAST_GOOD_SECONDS)
        return items, {**status, "retrieved_at": now}
    if status.get("state") == "not_configured":
        return items, status
    last = await c.cache.get(key)
    if last and last.get("items") and len(last["items"]) > len(items):
        return last["items"], {**status, "state": "stale", "retrieved_at": last.get("retrieved_at"),
                               "reason": f"{status.get('reason') or 'source unavailable'}; showing the last items retrieved"}
    return items, status


async def stocks_in_news(c: Container, s: AsyncSession, days: int = 1, include_routine: bool = False, refresh: bool = False) -> dict[str, Any]:
    """`refresh=True` skips every cache and asks each source again (the page's re-fetch button)."""
    key = f"news:stocks:{days}:{int(include_routine)}"
    if not refresh and (hit := await c.cache.get(key)) is not None:
        return {**hit, "cache": "hit"}
    if c.settings.is_demo:
        return {"state": "not_configured", "reason": "Market news is real data and is not shown next to demo symbols.", "news": [], "stocks": []}
    today = c.clock.now().astimezone(IST).date()
    start = today - timedelta(days=days - 1)
    if days == 1 and today.weekday() >= 5:  # a weekend "today" also shows the last weekday's news
        start = _prev_weekday(today)
    insts = (await s.scalars(select(Instrument).where(Instrument.instrument_type.in_(("EQ", "ETF", "REIT", "INVIT"))))).all()
    by_sym = {i.symbol.upper(): i for i in insts}
    names = {sym: nm for sym, i in by_sym.items() if (nm := short_name(i.name))}
    sources: dict[str, Any] = {}

    async def nse_items() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not c.filings.available:
            return [], {"state": "not_configured", "reason": "NSE_FILINGS_BASE_URL is not set"}
        try:
            items = announcement_items(await nf.market_announcements(c.filings, start, today, 0 if refresh else ANNOUNCEMENTS_TTL_SECONDS))
        except ProviderUnavailable as e:
            return [], {"state": e.state, "reason": str(e)[:200]}
        return items, {"state": "ok", "items": len(items)}

    # both sources at once: neither depends on the other
    async def web() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        items, status = await web_items(c, names, set(by_sym), start)
        # a search that answers with nothing dated in the window is treated like an outage, so earlier headlines stay
        return items, status if items else {**status, "state": "empty" if status.get("state") == "ok" else status.get("state"),
                                            "reason": status.get("reason") or "web search returned no dated headlines for this window"}

    window = f"{start.isoformat()}:{today.isoformat()}"
    (filings, sources["nse_announcements"]), (web_news, sources["web_search"]) = await asyncio.gather(
        _with_last_good(c, f"news:last_good:nse:{window}", nse_items), _with_last_good(c, f"news:last_good:web:{window}", web))
    hidden = sum(1 for f in filings if f["routine"]) if not include_routine else 0
    news = [n for n in filings if include_routine or not n["routine"]] + web_news
    for n in news:
        n["tone"] = ns.item_tone(n)
    news.sort(key=lambda n: news_time(n["published_at"]) if n.get("published_at") else datetime.min.replace(tzinfo=IST), reverse=True)

    # one row per stock: its news, first mention in the window, and the move since
    stocks: dict[str, dict[str, Any]] = {}
    for n in news:
        for sym in n["symbols"]:
            st = stocks.setdefault(sym, {"symbol": sym, "news_ids": [], "first_news_at": None, "latest_news_at": None, "categories": []})
            st["news_ids"].append(n["id"])
            if n.get("published_at"):
                if st["first_news_at"] is None or news_time(n["published_at"]) < news_time(st["first_news_at"]):
                    st["first_news_at"] = n["published_at"]
                if st["latest_news_at"] is None or news_time(n["published_at"]) > news_time(st["latest_news_at"]):
                    st["latest_news_at"] = n["published_at"]
            if n["category"] not in st["categories"]:
                st["categories"].append(n["category"])
    quotes, sources["quotes"] = await live_quotes(c, sorted(stocks))

    lookups = 0

    async def fill(st: dict[str, Any]) -> None:
        nonlocal lookups
        inst = by_sym.get(st["symbol"])
        st.update({"name": inst.name if inst else None, "sector": inst.sector if inst else None, "in_universe": inst is not None})
        q = quotes.get(st["symbol"])
        st.update({"last_price": q.get("last_price") if q else None, "day_change_pct": q.get("change_pct") if q else None,
                   "quote_as_of": q.get("as_of") if q else None, "quote_basis": q.get("basis") if q else None, "ref_price": None, "ref_date": None, "move_pct": None})
        if q is None or st["first_news_at"] is None:
            st["move_reason"] = ("news time unknown" if q is not None else "no NSE quote for this symbol"
                                 if sources["quotes"]["state"] != "unavailable" else "NSE quotes are unavailable right now")
            return
        news_day, after_close = reference_day(datetime.fromisoformat(st["first_news_at"]))
        q_day = date.fromisoformat(q["as_of"][:10]) if q.get("as_of") else today
        if news_day > q_day or (news_day == q_day and after_close):
            st["move_reason"] = ("live price unavailable from NSE right now; only the last end-of-day close is known"
                                 if q.get("basis") == "end of day" and news_day <= today else "no trading session since the news yet")
            return
        if news_day == q_day:
            ref, ref_date, basis = q.get("prev_close"), None, "previous close (news came before or during the session)"
        else:
            upto = news_day if after_close else _prev_weekday(news_day)
            ref_date, ref, src = await _reference_close(c, s, inst.id if inst else None, st["symbol"], upto, lookups < MAX_REFERENCE_LOOKUPS)
            if src == "budget":
                st["move_reason"] = "reference close not looked up (lookup budget reached)"
                return
            if src != "stored daily bars":
                lookups += 1  # an MCP call was made
            basis = (f"close {'on the news day (news after the close)' if after_close else 'before the news day'}, from {src}")
        if ref in (None, 0):
            st["move_reason"] = "reference close unavailable"
            return
        st.update({"ref_price": ref, "ref_date": ref_date, "ref_basis": basis, "move_pct": round((q["last_price"] / ref - 1) * 100, 2)})

    tones = {n["id"]: n["tone"] for n in news}
    for st in stocks.values():  # sequential: shares one database session
        await fill(st)
        st["sentiment"] = ns.stock_sentiment([tones[i] for i in st["news_ids"]], st.get("move_pct"))
    # newest news first; a stock whose news has no time goes last
    rows = sorted(stocks.values(), key=lambda r: (news_time(r["latest_news_at"]) if r["latest_news_at"] else datetime.min.replace(tzinfo=IST),
                                                  r["symbol"]), reverse=True)
    out = {"state": "ok" if news else "empty", "window": {"from": start.isoformat(), "to": today.isoformat(), "days": days},
           "news": news, "stocks": rows, "routine_hidden": hidden,
           "sentiment_counts": {k: sum(1 for r in rows if r["sentiment"]["label"] == k) for k in ("Bullish", "Bearish", "Neutral")},
           "sentiment_method": (ns.__doc__ or "").strip(), "sources": sources, "computed_at": c.clock.now().isoformat(),
           "method": (__doc__ or "").strip()}
    # an outage or stale fallback is not cached; the next poll asks the sources again
    if all(sources[k].get("state") in ("ok", "not_configured") for k in ("nse_announcements", "web_search")) and sources["quotes"]["state"] != "unavailable":
        await c.cache.set(key, out, CACHE_SECONDS)
    return out
