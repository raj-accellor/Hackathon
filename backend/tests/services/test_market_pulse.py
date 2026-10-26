"""Market pulse services: FII/DII recording and history, participant OI, F&O dashboard, stocks in the news and
macro calculations. NSE's public files are served from responses recorded on 2026-10-23/26."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.connectors.base import ProviderUnavailable, QuoteResult
from app.connectors.nse_filings import NseFilingsProvider
from app.connectors.web_search import NullSearch
from app.core.cache import MemoryCache
from app.core.config import Settings
from app.db.models import FiiDiiFlow, Instrument, PriceBar
from app.db.session import create_all, make_engine, make_sessionmaker
from app.domain.provenance import DataPoint
from app.services import derivatives, flows, macro, news

FX = Path(__file__).parents[1] / "fixtures" / "live" / "market_pulse"
ARCH = "https://nsearchives.nseindia.com"
NOW = datetime(2026, 10, 26, 6, 30, tzinfo=UTC)  # Monday 12:00 IST


class Clock:
    def __init__(self, now=NOW):
        self._now = now

    def now(self):
        return self._now


def _handler(calls, down=False):
    def h(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        p = req.url.path
        if down:
            return httpx.Response(503)
        if p.endswith("/fiidiiTradeReact"):
            return httpx.Response(200, content=(FX / "fiidii_2026-10-26.json").read_bytes())
        if p.endswith("/allIndices"):
            return httpx.Response(200, json={"timestamp": "26-Oct-2026 12:09", "data": [
                {"index": "NIFTY 50", "last": 22595.05, "previousClose": 22600.0, "open": 22524.4, "high": 22706.15, "low": 22387.35}]})
        if p.endswith("/corporate-announcements"):
            return httpx.Response(200, content=(FX / "announcements_2026-10-23.json").read_bytes())
        if "fao_participant_oi_" in p:
            day = p.rsplit("_", 1)[1][:8]
            return httpx.Response(200, content=(FX / "fao_participant_oi_23102026.csv").read_bytes()) if day in ("23102026", "22102026") \
                else httpx.Response(404)
        if "BhavCopy_NSE_FO" in p:
            return httpx.Response(200, content=(FX / "fo_bhavcopy_20261023_subset.csv.zip").read_bytes()) if "20261023" in p \
                else httpx.Response(404)
        return httpx.Response(404)
    return h


@pytest.fixture
async def env(tmp_path):
    settings = Settings(_env_file=None, app_env="test", data_mode="live", database_url=f"sqlite+aiosqlite:///{tmp_path}/p.db",
                        nse_filings_base_url="https://www.nseindia.com/api",
                        nse_fo_bhavcopy_url_template=f"{ARCH}/content/fo/BhavCopy_NSE_FO_0_0_0_{{yyyymmdd}}_F_0000.csv.zip",
                        nse_participant_oi_url_template=f"{ARCH}/content/nsccl/fao_participant_oi_{{ddmmyyyy}}.csv")
    calls: list[str] = []
    state = {"down": False}

    def make_filings():
        return NseFilingsProvider(settings.nse_filings_base_url, ["nsearchives.nseindia.com"], tmp_path / "cache", user_agent="t",
                                  min_interval=0, transport=httpx.MockTransport(lambda r: _handler(calls, state["down"])(r)))

    engine = make_engine(settings.database_url)  # type: ignore[arg-type]
    await create_all(engine)
    c = SimpleNamespace(settings=settings, filings=make_filings(), engine=engine, sessions=make_sessionmaker(engine), clock=Clock(),
                        cache=MemoryCache(), search=NullSearch("not configured in tests"), nse=None)
    yield c, calls, state
    await c.filings.close()
    await engine.dispose()


# ------------------------------------------------------------------ FII/DII
async def test_fii_dii_is_recorded_once_per_day_and_category(env):
    c, _, _ = env
    async with c.sessions() as s:
        out = await flows.record_latest(c, s)
        await flows.record_latest(c, s)  # idempotent
        rows = (await s.scalars(select(FiiDiiFlow).order_by(FiiDiiFlow.category))).all()
    assert out == {"trade_date": "2026-10-23", "rows": 2}
    assert [(r.category, r.net_value) for r in rows] == [("DII", Decimal("3235.36")), ("FII/FPI", Decimal("887.65"))]
    assert rows[1].buy_value - rows[1].sell_value == rows[1].net_value  # as published: 14445.54 - 13557.89


async def test_summary_history_totals_streaks_and_outage(env):
    c, _, state = env
    async with c.sessions() as s:
        for d, fii, dii in ((date(2026, 10, 21), "-500", "700"), (date(2026, 10, 22), "-250.5", "100")):
            for cat, v in (("FII/FPI", fii), ("DII", dii)):
                s.add(FiiDiiFlow(trade_date=d, category=cat, net_value=Decimal(v), source="test"))
        await s.commit()
        out = await flows.summary(c, s, 30)
        assert out["latest"]["date"] == "2026-10-23" and out["latest"]["fii"]["net"] == 887.65
        assert out["latest"]["combined_net"] == pytest.approx(887.65 + 3235.36)
        assert [d["date"] for d in out["history"]] == ["2026-10-23", "2026-10-22", "2026-10-21"]
        assert out["totals"]["fii_net"] == pytest.approx(887.65 - 250.5 - 500)
        assert out["streaks"]["fii"] == {"side": "buying", "days": 1} and out["streaks"]["dii"] == {"side": "buying", "days": 3}
        assert out["first_recorded"] == "2026-10-21"

        # NSE down: the stored history is still served, with the failure stated
        state["down"] = True
        for f in (c.filings.cache_dir).glob("*.json"):
            f.unlink()
        down = await flows.summary(c, s, 30)
    assert down["latest_state"]["state"] == "unavailable" and down["recorded_days"] == 3


def test_streak_stops_at_sign_change():
    days = [{"fii": {"net": -1}}, {"fii": {"net": -2}}, {"fii": {"net": 3}}, {"fii": {"net": -4}}]
    assert flows.streak(days, "fii") == {"side": "selling", "days": 2}
    assert flows.streak([], "fii") is None


async def test_participant_oi_walks_back_over_weekend_and_compares_with_previous_file(env):
    c, calls, _ = env
    out = await flows.participant_oi(c)
    assert out["state"] == "ok" and out["date"] == "2026-10-23" and out["previous_date"] == "2026-10-22"
    assert not any("24102026" in u or "25102026" in u for u in calls)  # weekend days are not requested
    fii = next(p for p in out["participants"] if p["participant"] == "FII")
    assert fii["future_index_net_change"] == 0  # the same file served for both days


# ------------------------------------------------------------------ F&O open interest
async def test_oi_dashboard_uses_latest_published_day(env):
    c, _, _ = env
    out = await derivatives.oi_dashboard(c, None, 5)
    assert out["state"] == "ok" and out["date"] == "2026-10-23" and out["requested"] == "2026-10-26"
    assert out["underlyings"] == 5 and {r["symbol"] for r in out["index_futures"]} == {"NIFTY", "BANKNIFTY"}
    assert sum(out["buildup_counts"].values()) <= out["underlyings"]
    for k in ("price_gainers", "price_losers", "oi_gainers", "oi_losers"):
        assert all(r["kind"] == "stock" for r in out[k])


async def test_oi_dashboard_not_configured_without_template(env):
    c, _, _ = env
    c.settings = c.settings.model_copy(update={"nse_fo_bhavcopy_url_template": None})
    out = await derivatives.oi_dashboard(c)
    assert out["state"] == "not_configured" and "NSE_FO_BHAVCOPY_URL_TEMPLATE" in out["reason"]


# ------------------------------------------------------------------ stocks in the news
def test_announcements_parse_and_routine_filter():
    items = news.announcement_items(json.loads((FX / "announcements_2026-10-23.json").read_text()))
    assert items and all(i["kind"] == "exchange_filing" and i["source_type"] == "primary" for i in items)
    assert all(i["published_at"].endswith("+05:30") for i in items)
    routine = {i["category"] for i in items if i["routine"]}
    assert "Copy of Newspaper Publication" in routine and "Trading Window" in routine
    assert not any(i["routine"] for i in items if i["category"] in ("Outcome of Board Meeting", "Acquisition", "Press Release"))


def test_symbol_matching_in_headlines():
    names = {"INFY": news.short_name("Infosys Limited"), "RELIANCE": news.short_name("Reliance Industries Limited"),
             "ITC": news.short_name("ITC Limited")}
    assert names["RELIANCE"] == "Reliance Industries" and names["ITC"] is None  # too short to match safely by name
    assert news.match_symbols("Infosys share price drops 4% after Q2", names, set(names)) == ["INFY"]
    assert news.match_symbols("RELIANCE and HDFCBANK lead gains", names, set(names)) == ["RELIANCE"]
    assert news.match_symbols("Market falls; ITC flat", names, set(names)) == []


def test_reference_day_rule():
    assert news.reference_day(datetime(2026, 10, 23, 16, 0, tzinfo=news.IST)) == (date(2026, 10, 23), True)
    assert news.reference_day(datetime(2026, 10, 23, 9, 0, tzinfo=news.IST)) == (date(2026, 10, 23), False)
    assert news.reference_day(datetime(2026, 10, 23)) == (date(2026, 10, 23), False)  # date only: start of day


def _q(sym, last, prev, as_of):
    t = datetime.fromisoformat(as_of)
    f = {k: DataPoint(field=k, value=v, provider="nse_mcp", retrieved_at=NOW, as_of=t, source_id="cm_get_stock_quote")
         for k, v in (("last_price", last), ("prev_close", prev), ("change_pct", round((last / prev - 1) * 100, 2)))}
    return QuoteResult(sym, f, "nse_mcp", NOW)


class FakeNse:
    def __init__(self, quotes, closes=None):
        self.quotes, self.closes, self.close_calls = quotes, closes or {}, []

    async def get_quote(self, sym, instrument_type="EQ"):
        if sym not in self.quotes:
            raise ProviderUnavailable("no quote", "unavailable", "nse_mcp")
        return self.quotes[sym]

    async def bulk_quotes(self, syms):
        return {}

    async def close_on(self, sym, day):
        self.close_calls.append((sym, day))
        return (None, self.closes[sym]) if sym in self.closes else None


async def test_move_since_news_uses_previous_close_stored_bars_or_waits_for_a_session(env, monkeypatch):
    c, _, _ = env
    ann = [  # Monday 26 Oct 2026 in session; Friday 23 Oct before the close; Friday after the close; Monday after the close
        {"symbol": "AAA", "sm_name": "Aaa Ltd", "desc": "Press Release", "an_dt": "26-Oct-2026 10:00:00", "seq_id": "1"},
        {"symbol": "BBB", "sm_name": "Bbb Ltd", "desc": "Acquisition", "an_dt": "23-Oct-2026 11:00:00", "seq_id": "2"},
        {"symbol": "CCC", "sm_name": "Ccc Ltd", "desc": "Outcome of Board Meeting", "an_dt": "23-Oct-2026 18:00:00", "seq_id": "3"},
        {"symbol": "DDD", "sm_name": "Ddd Ltd", "desc": "Press Release", "an_dt": "26-Oct-2026 17:00:00", "seq_id": "4"},
        {"symbol": "EEE", "sm_name": "Eee Ltd", "desc": "Trading Window", "an_dt": "26-Oct-2026 10:00:00", "seq_id": "5"},
    ]

    async def fake_ann(p, start, end, ttl=None):
        return ann

    monkeypatch.setattr(news.nf, "market_announcements", fake_ann)
    today = "2026-10-26T11:55:00+05:30"
    c.nse = FakeNse({s: _q(s, 110.0, 100.0, today) for s in ("AAA", "BBB", "CCC", "DDD", "EEE")}, closes={"CCC": 104.0})
    async with c.sessions() as s:
        bbb = Instrument(symbol="BBB", exchange="NSE", name="Bbb Ltd", instrument_type="EQ", currency="INR")
        s.add(bbb)
        await s.flush()
        s.add(PriceBar(instrument_id=bbb.id, date=date(2026, 10, 22), open=90, high=90, low=90, close=Decimal("88"), provider="t"))
        await s.commit()
        out = await news.stocks_in_news(c, s, days=7)
    by = {r["symbol"]: r for r in out["stocks"]}
    assert "EEE" not in by and out["routine_hidden"] == 1  # routine filing hidden by default
    assert by["AAA"]["ref_price"] == 100.0 and by["AAA"]["move_pct"] == 10.0 and "previous close" in by["AAA"]["ref_basis"]
    # before Friday's close: reference is the close before Friday (stored bar of Thursday 22 Oct)
    assert by["BBB"]["ref_price"] == 88.0 and by["BBB"]["ref_date"] == "2026-10-22" and by["BBB"]["move_pct"] == pytest.approx(25.0)
    # after Friday's close: reference is Friday's close, looked up through the MCP tool
    assert by["CCC"]["ref_price"] == 104.0 and c.nse.close_calls == [("CCC", date(2026, 10, 23))]
    assert by["DDD"]["move_pct"] is None and by["DDD"]["move_reason"] == "no trading session since the news yet"
    assert out["sources"]["quotes"]["live"] == 5 - 1  # EEE was not quoted
    assert out["sources"]["web_search"]["state"] == "not_configured"
    again = await news.stocks_in_news(c, None, days=7)
    assert again["cache"] == "hit"


# ------------------------------------------------------------------ macro calculations
def test_changes_yoy_and_freshness():
    assert macro._change(4.988, 4.953, "bps") == (pytest.approx(0.035), 3.5)
    assert macro._change(110.0, 100.0, "pct") == (10.0, 10.0)
    assert macro._change(None, 1.0, "pct") == (None, None)
    obs = [("2026-09-01", 336.298), ("2026-08-01", 334.131), ("2025-09-01", 324.0)]
    assert macro.yoy(obs) == [("2026-09-01", pytest.approx((336.298 / 324.0 - 1) * 100, abs=1e-3))]
    today = date(2026, 10, 26)
    assert macro.freshness("2026-10-23T20:00:00+00:00", False, today) == "fresh"
    assert macro.freshness("2026-10-20", False, today) == "stale"
    assert macro.freshness("2025-12-01", True, today) == "stale" and macro.freshness("2026-09-01", True, today) == "latest_release"


async def test_headline_indices_nse_and_sensex_with_honest_failure():
    class Nse:
        async def index_quote(self, name):
            if name == "NIFTY BANK":
                raise ProviderUnavailable("NSE CM market MCP unavailable: timeout", "unavailable", "nse_mcp")
            return {"value": 22562.6, "prev_close": 22600.0, "open": 1, "high": 2, "low": 0.5, "as_of": "2026-10-26T11:41:00+05:30"}

    class Chart:
        async def series(self, sym, rng="3mo"):
            assert sym == "^BSESN"
            return {"value": 73000.0, "prev_close": 73500.0, "as_of": "2026-10-26T06:05:51+00:00"}

    c = SimpleNamespace(settings=SimpleNamespace(is_demo=False), nse=Nse(), chart=Chart(), filings=SimpleNamespace(available=False))
    rows = {r["key"]: r for r in await macro.headline_indices(c)}
    assert list(rows) == ["NIFTY 50", "SENSEX", "NIFTY BANK"]
    assert rows["NIFTY 50"]["change_pct"] == pytest.approx(-0.165) and rows["NIFTY 50"]["source"] == "NSE CM market MCP"
    assert rows["SENSEX"]["exchange"] == "BSE" and rows["SENSEX"]["change"] == -500.0
    assert rows["NIFTY BANK"]["value"] is None and rows["NIFTY BANK"]["state"] == "unavailable"


async def test_macro_dashboard_in_demo_mode_shows_no_values():
    c = SimpleNamespace(settings=SimpleNamespace(is_demo=True), chart=SimpleNamespace(status=lambda: {}),
                        cpi=SimpleNamespace(status=lambda: {}), fred=SimpleNamespace(status=lambda: {}))
    out = await macro.dashboard(c)
    assert out["coverage"]["available"] == 0 and {g["group"] for g in out["groups"]} == set(macro.GROUPS)
    keys = {i["key"] for g in out["groups"] for i in g["indicators"]}
    assert {"brent", "dxy", "usdinr", "india_cpi", "us10y", "indiavix"} <= keys


async def test_many_symbols_use_one_equity_list_call_per_letter():
    calls = []

    class Nse:
        async def equity_snapshot(self, prefix):
            calls.append(prefix)
            return {f"{prefix}{i:02d}X": {"last_price": 10.0 + i, "prev_close": 10.0, "change_pct": None, "as_of": None} for i in range(20)}, False

        async def get_quote(self, sym, instrument_type="EQ"):
            raise ProviderUnavailable("not listed", "unavailable", "nse_mcp")

        async def bulk_quotes(self, syms):
            return {}

    syms = [f"A{i:02d}X" for i in range(20)] + [f"B{i:02d}X" for i in range(10)] + ["ZNONE"]
    out, state = await news.live_quotes(SimpleNamespace(nse=Nse(), settings=SimpleNamespace(is_demo=False), kite=None), syms)
    assert sorted(calls) == ["A", "B", "Z"] and len(out) == 30
    assert {k: state[k] for k in ("state", "live", "broker", "end_of_day", "missing")} == \
        {"state": "degraded", "live": 30, "broker": 0, "end_of_day": 0, "missing": 1}
    assert state["broker_state"]["state"] == "not_configured"  # ZNONE was offered to Kite, which is not configured here


async def test_index_falls_back_to_nse_index_feed_when_mcp_is_down(env):
    c, _, _ = env

    class Nse:
        async def index_quote(self, name):
            raise ProviderUnavailable("NSE CM market MCP unavailable: HTTP 504", "unavailable", "nse_mcp")

    c.nse = Nse()
    r = await macro._nse_index(c, "NIFTY 50", "Nifty 50")
    assert r["value"] == 22595.05 and r["source"] == "NSE allIndices feed" and "504" in r["note"]
    assert r["as_of"] == "2026-10-26T12:09:00+05:30" and r["change"] == pytest.approx(-4.95)
    missing = await macro._nse_index(c, "NIFTY IT", "Nifty IT")
    assert missing["value"] is None and missing["state"] == "unavailable"


async def test_web_headlines_outside_the_window_or_undated_are_dropped():
    class Search:
        def status(self):
            return {"state": "ok"}

        async def search(self, q, n):
            return [{"title": "Infosys rallies", "url": "https://a.example/1", "published_at": "2026-10-26", "provider": "ddgs"},
                    {"title": "Old Infosys story", "url": "https://a.example/2", "published_at": "2026-10-24", "provider": "ddgs"},
                    {"title": "Undated Infosys story", "url": "https://a.example/3", "published_at": None, "provider": "ddgs"}]

    c = SimpleNamespace(search=Search(), settings=SimpleNamespace(web_search_max_results=8))
    items, st = await news.web_items(c, {"INFY": "Infosys"}, {"INFY"}, date(2026, 10, 26))
    assert st == {"state": "ok"} and [i["headline"] for i in items] == ["Infosys rallies"] and items[0]["symbols"] == ["INFY"]


async def test_stocks_in_news_are_listed_newest_news_first(env, monkeypatch):
    c, _, _ = env
    ann = [{"symbol": "OLD", "sm_name": "Old Ltd", "desc": "Press Release", "an_dt": "26-Oct-2026 08:00:00", "seq_id": "1"},
           {"symbol": "MID", "sm_name": "Mid Ltd", "desc": "Press Release", "an_dt": "26-Oct-2026 10:00:00", "seq_id": "2"},
           {"symbol": "OLD", "sm_name": "Old Ltd", "desc": "Acquisition", "an_dt": "26-Oct-2026 11:30:00", "seq_id": "3"},
           {"symbol": "NEW", "sm_name": "New Ltd", "desc": "Press Release", "an_dt": "26-Oct-2026 11:00:00", "seq_id": "4"}]

    async def fake_ann(p, start, end, ttl=None):
        return ann

    monkeypatch.setattr(news.nf, "market_announcements", fake_ann)
    c.nse = FakeNse({s: _q(s, 110.0, 100.0, "2026-10-26T11:55:00+05:30") for s in ("OLD", "MID", "NEW")})
    async with c.sessions() as s:
        out = await news.stocks_in_news(c, s, days=1)
    # OLD's latest item (11:30) is the newest, even though its first item is the oldest
    assert [r["symbol"] for r in out["stocks"]] == ["OLD", "NEW", "MID"]
    assert out["stocks"][0]["latest_news_at"].startswith("2026-10-26T11:30") and out["stocks"][0]["first_news_at"].startswith("2026-10-26T08:00")
    assert [n["published_at"][11:16] for n in out["news"]] == ["11:30", "11:00", "10:00", "08:00"]
    # every stock carries a sentiment; OLD has an acquisition (positive) and is up 10% since its first news
    old = out["stocks"][0]["sentiment"]
    assert old["label"] == "Bullish" and old["confidence"] == "moderate" and "acquisition" in old["basis"][0]
    assert out["sentiment_counts"] == {"Bullish": 3, "Bearish": 0, "Neutral": 0}


def test_news_time_orders_dates_and_times_consistently():
    assert news.news_time("2026-10-26") < news.news_time("2026-10-26T09:00:00+05:30") < news.news_time("2026-10-26T05:00:00+00:00")


async def test_news_outage_keeps_last_good_items_marked_stale_and_is_not_cached(env, monkeypatch):
    """An NSE outage must not empty the page: the last announcements stay, labelled stale, and the next poll retries."""
    c, _, _ = env
    ann = [{"symbol": "OLD", "sm_name": "Old Ltd", "desc": "Press Release", "an_dt": "26-Oct-2026 08:00:00", "seq_id": "1"}]
    down = {"v": False}
    ttls: list[float | None] = []

    async def fake_ann(p, start, end, ttl=None):
        ttls.append(ttl)
        if down["v"]:
            raise ProviderUnavailable("NSE filings request failed: ConnectTimeout", "unavailable", "nse_filings")
        return ann

    monkeypatch.setattr(news.nf, "market_announcements", fake_ann)
    c.nse = FakeNse({"OLD": _q("OLD", 110.0, 100.0, "2026-10-26T11:55:00+05:30")})
    async with c.sessions() as s:
        first = await news.stocks_in_news(c, s, days=1)
        assert first["sources"]["nse_announcements"]["state"] == "ok" and len(first["news"]) == 1
        down["v"] = True
        cached = await news.stocks_in_news(c, s, days=1)
        assert cached["cache"] == "hit"  # within a minute the cached good result is served
        out = await news.stocks_in_news(c, s, days=1, refresh=True)
        assert ttls[-1] == 0  # a re-fetch skips the filings disk cache too
        assert out["sources"]["nse_announcements"]["state"] == "stale" and "ConnectTimeout" in out["sources"]["nse_announcements"]["reason"]
        assert [n["headline"] for n in out["news"]] == [first["news"][0]["headline"]]
        await c.cache.delete_prefix("news:stocks:")  # the good result's minute is up
        calls = len(ttls)
        again = await news.stocks_in_news(c, s, days=1)
        again2 = await news.stocks_in_news(c, s, days=1)
        assert again["sources"]["nse_announcements"]["state"] == "stale" and len(again["news"]) == 1
        assert again2.get("cache") != "hit" and len(ttls) == calls + 2  # stale results are not cached: every poll asks NSE again
        down["v"] = False
        back = await news.stocks_in_news(c, s, days=1)
        assert back["sources"]["nse_announcements"]["state"] == "ok"
