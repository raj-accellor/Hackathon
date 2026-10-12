"""Mocked contract tests: Kite Connect REST, fundamentals files, web search, Hugging Face LLM.
No test here calls a paid or external service."""

import gzip
import hashlib
import json
import logging
from datetime import date
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import BaseModel

from app.connectors.base import ProviderUnavailable
from app.connectors.fundamentals_file import FileFundamentalsProvider, FundamentalsValidationError
from app.connectors.kite_connect import KiteConnectProvider
from app.connectors.llm import HuggingFaceLLM, LLMBudgetExceeded, LLMInvalidOutput, LLMUnavailable
from app.connectors.web_search import BraveSearch, NullSearch, TavilySearch, build_search
from app.devtools import demo_data as dd

# ------------------------------------------------------------ Kite Connect

CSV = ("instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange\n"
       "408065,1594,INFY,INFOSYS,0,,0,0.05,1,EQ,NSE,NSE\n256265,1001,NIFTY 50,NIFTY 50,0,,0,0,0,EQ,INDICES,NSE\n")


def kite(handler, token="tok"):
    return KiteConnectProvider("key123", "secret456", token, transport=httpx.MockTransport(handler), min_quote_interval=0, min_history_interval=0)


def test_kite_connect_unconfigured_reports_not_configured():
    k = KiteConnectProvider(None, None, None)
    assert k.capabilities()["history"]["supported"] is False
    assert "not configured" in k.capabilities()["history"]["reason"]
    assert k.health()["state"] == "not_configured"


def test_kite_connect_login_url_and_checksum():
    k = kite(lambda r: httpx.Response(500), token=None)
    assert k.login_url() == "https://kite.zerodha.com/connect/login?v=3&api_key=key123"
    assert k.checksum("reqtok") == hashlib.sha256(b"key123reqtoksecret456").hexdigest()


async def test_kite_connect_token_exchange_sends_checksum_and_never_returns_tokens():
    seen = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen["path"], seen["form"], seen["version"] = req.url.path, parse_qs(req.content.decode()), req.headers.get("x-kite-version")
        return httpx.Response(200, json={"status": "success", "data": {"access_token": "AT-SECRET", "public_token": "PT", "user_id": "AB1234", "user_name": "A B"}})

    k = kite(h, token=None)
    out = await k.exchange_token("reqtok")
    assert seen["path"] == "/session/token" and seen["version"] == "3"
    assert seen["form"]["checksum"] == [hashlib.sha256(b"key123reqtoksecret456").hexdigest()]
    assert out == {"user_id": "AB1234", "user_name": "A B"}
    assert "AT-SECRET" not in json.dumps(k.health()) and "AT-SECRET" not in repr(k)
    assert (await k.auth_state())["state"] in ("authorized", "unknown", "unavailable")


async def test_kite_connect_quotes_use_auth_header_and_parse_fields():
    def h(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "token key123:tok" and req.headers["x-kite-version"] == "3"
        assert req.url.params.get_list("i") == ["NSE:INFY", "NSE:NOSUCH"]
        return httpx.Response(200, json={"status": "success", "data": {"NSE:INFY": {
            "instrument_token": 408065, "timestamp": "2026-10-09 15:30:00", "last_price": 1412.95, "volume": 4075699,
            "ohlc": {"open": 1396, "high": 1421.75, "low": 1395.55, "close": 1389.65}, "upper_circuit_limit": 1528.6, "lower_circuit_limit": 1250.7}}})

    qs = await kite(h).get_quotes(["INFY", "NOSUCH"])
    assert set(qs) == {"INFY"}
    f = qs["INFY"].fields
    assert f["last_price"].value == 1412.95 and f["prev_close"].value == 1389.65 and f["last_price"].provider == "kite_connect"


async def test_kite_connect_history_resolves_token_from_instrument_dump():
    calls = []

    def h(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if req.url.path == "/instruments/NSE":
            return httpx.Response(200, content=gzip.compress(CSV.encode()), headers={"content-type": "text/csv"})
        assert req.url.path == "/instruments/historical/408065/day"
        assert req.url.params["from"] == "2026-10-01 00:00:00" and req.url.params["to"] == "2026-10-09 23:59:59"
        return httpx.Response(200, json={"status": "success", "data": {"candles": [
            ["2026-10-08T00:00:00+0530", 1390, 1400, 1380, 1389.65, 3000000], ["2026-10-09T00:00:00+0530", 1396, 1421.75, 1395.55, 1412.95, 4075699]]}})

    k = kite(h)
    res = await k.get_history("INFY", date(2026, 10, 1), date(2026, 10, 9))
    assert res.frame["close"].tolist() == [1389.65, 1412.95] and res.provider == "kite_connect"
    await k.get_history("INFY", date(2026, 10, 1), date(2026, 10, 9))
    assert calls.count("/instruments/NSE") == 1, "the instrument dump is fetched once and cached"


async def test_kite_connect_expired_token_is_login_required_and_rate_limit_is_unavailable():
    k = kite(lambda r: httpx.Response(403, json={"status": "error", "message": "Incorrect `api_key` or `access_token`.", "error_type": "TokenException"}))
    with pytest.raises(ProviderUnavailable) as e:
        await k.get_quotes(["INFY"])
    assert e.value.state == "login_required"
    k2 = kite(lambda r: httpx.Response(429, json={"status": "error", "message": "Too many requests", "error_type": "NetworkException"}))
    with pytest.raises(ProviderUnavailable) as e2:
        await k2.get_quotes(["INFY"])
    assert e2.value.state == "unavailable"


# ------------------------------------------------------------ fundamentals files

def test_fundamentals_file_roundtrip_and_validation(tmp_path):
    p = FileFundamentalsProvider(tmp_path, allow_demo=True)
    assert p.get_statements("DEMOIT01") is None
    assert p.status()["instruments_with_data"] == 0
    p.save("DEMOIT01", dd.fundamentals("DEMOIT01"))
    got = p.get_statements("demoit01")
    assert got["currency"] == "INR" and len(got["periods"]) == 5 and got["is_demo"] is True
    assert got["ingested_at"] and p.status()["instruments_with_data"] == 1


def test_fundamentals_rejects_bad_payloads(tmp_path):
    p = FileFundamentalsProvider(tmp_path, allow_demo=True)
    good = dd.fundamentals("DEMOIT01")
    for mutate, msg in [
        (lambda d: d.update(unit="lakh-ish"), "unit"),
        (lambda d: d["periods"].append(dict(d["periods"][0])), "duplicate"),
        (lambda d: d["periods"][0].update(revenue="abc"), "revenue"),
        (lambda d: d.update(periods=[]), "period"),
        (lambda d: d.update(symbol="../../etc/passwd"), "symbol"),
    ]:
        bad = json.loads(json.dumps(good))
        mutate(bad)
        with pytest.raises(FundamentalsValidationError) as e:
            p.save(bad.get("symbol", "X") if "/" not in str(bad.get("symbol")) else "X", bad)
        assert msg in str(e.value).lower()


def test_live_mode_refuses_demo_fundamentals(tmp_path):
    p = FileFundamentalsProvider(tmp_path, allow_demo=False)
    with pytest.raises(FundamentalsValidationError):
        p.save("DEMOIT01", dd.fundamentals("DEMOIT01"))
    (tmp_path / "DEMOIT01.json").write_text(json.dumps(dd.fundamentals("DEMOIT01")))
    assert p.get_statements("DEMOIT01") is None, "a demo file on disk is ignored in live mode"


# ------------------------------------------------------------ web search

async def test_no_search_provider_reports_unavailable():
    s = build_search("none", None)
    assert isinstance(s, NullSearch) and s.available is False
    assert await s.search("INFY results") == []


async def test_tavily_results_are_normalised_and_classified():
    def h(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        assert req.url.host == "api.tavily.com" and body["query"] == "INFY results"
        assert req.headers["authorization"] == "Bearer k"
        return httpx.Response(200, json={"results": [
            {"title": "Outcome of Board Meeting", "url": "https://www.nseindia.com/x", "content": "<b>Board</b> approved results", "published_date": "2026-10-09"},
            {"title": "Infosys Q2", "url": "https://news.example.com/a", "content": "Revenue grew", "published_date": None},
            {"title": "bad", "url": "javascript:alert(1)", "content": "x"},
            {"title": "internal", "url": "http://169.254.169.254/latest", "content": "x"}]})

    s = TavilySearch("k", transport=httpx.MockTransport(h))
    out = await s.search("INFY results")
    assert [r["url"] for r in out] == ["https://www.nseindia.com/x", "https://news.example.com/a"]
    assert out[0]["source_type"] == "primary" and out[1]["source_type"] == "secondary"
    assert out[0]["snippet"] == "Board approved results" and out[0]["published_at"] == "2026-10-09"
    assert out[1]["published_at"] is None and out[0]["retrieved_at"] and out[0]["id"] == "S1"


async def test_search_failure_raises_unavailable_not_empty_success():
    s = BraveSearch("k", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(ProviderUnavailable):
        await s.search("x")


# ------------------------------------------------------------ LLM

class Out(BaseModel):
    answer: str
    score: int


def llm(handler, **kw):
    kw.setdefault("daily_budget", 10)
    return HuggingFaceLLM(token="hf_SECRET", model_id="zai-org/GLM-test", enabled=True, transport=httpx.MockTransport(handler), max_retries=0, **kw)


def _resp(content, usage=None):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": usage or {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})


def test_llm_unavailable_without_token_or_model_or_flag():
    assert HuggingFaceLLM(token=None, model_id="m", enabled=True).available is False
    assert HuggingFaceLLM(token="t", model_id=None, enabled=True).available is False
    assert HuggingFaceLLM(token="t", model_id="m", enabled=False).available is False
    assert "HF_TOKEN" in HuggingFaceLLM(token=None, model_id="m", enabled=True).status()["reason"]


async def test_llm_sends_json_schema_request_and_validates_output():
    seen = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers["authorization"], json.loads(req.content)
        return _resp('{"answer": "ok", "score": 3}')

    m = llm(h)
    out = await m.complete_validated("sys", "user", Out)
    assert out == Out(answer="ok", score=3)
    assert seen["url"] == "https://router.huggingface.co/v1/chat/completions" and seen["auth"] == "Bearer hf_SECRET"
    b = seen["body"]
    assert b["model"] == "zai-org/GLM-test" and b["temperature"] <= 0.2 and b["max_tokens"] > 0
    assert b["response_format"]["type"] == "json_schema" and b["response_format"]["json_schema"]["schema"]["required"] == ["answer", "score"]
    u = m.status()["usage"]
    assert u["calls"] == 1 and u["total_tokens"] == 120 and u["model_id"] == "zai-org/GLM-test"


async def test_llm_repairs_once_then_fails_cleanly():
    replies = iter(['{"answer": "ok"}', '```json\n{"answer": "ok", "score": 2}\n```'])
    bodies = []

    def h(req):
        bodies.append(json.loads(req.content))
        return _resp(next(replies))

    out = await llm(h).complete_validated("sys", "user", Out)
    assert out.score == 2 and len(bodies) == 2
    assert "did not match" in bodies[1]["messages"][-1]["content"]

    m2 = llm(lambda r: _resp("not json at all"))
    with pytest.raises(LLMInvalidOutput):
        await m2.complete_validated("sys", "user", Out)
    assert m2.status()["usage"]["calls"] == 2 and m2.status()["usage"]["invalid_outputs"] == 1


async def test_llm_budget_cap_blocks_further_calls():
    m = llm(lambda r: _resp('{"answer": "ok", "score": 1}'), daily_budget=1)
    await m.complete_validated("s", "u", Out)
    with pytest.raises(LLMBudgetExceeded):
        await m.complete_validated("s", "u", Out)


async def test_llm_http_error_is_unavailable_and_secret_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    m = llm(lambda r: httpx.Response(401, json={"error": "Invalid credentials in Authorization header"}))
    with pytest.raises(LLMUnavailable):
        await m.complete_validated("s", "u", Out)
    assert "hf_SECRET" not in caplog.text and "hf_SECRET" not in json.dumps(m.status()) and "hf_SECRET" not in repr(m)
    assert m.status()["usage"]["errors"] == 1


async def test_dedicated_endpoint_url_overrides_router():
    seen = {}

    def h(req):
        seen["url"] = str(req.url)
        return _resp('{"answer": "a", "score": 1}')

    m = HuggingFaceLLM(token="t", model_id="m", enabled=True, endpoint_url="https://xyz.endpoints.huggingface.cloud", transport=httpx.MockTransport(h))
    await m.complete_validated("s", "u", Out)
    assert seen["url"] == "https://xyz.endpoints.huggingface.cloud/v1/chat/completions"
