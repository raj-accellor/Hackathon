"""Chat assistant: page routing from the catalog, instrument resolution, LLM path and rules fallback.
Market data is DEMO synthetic data; the LLM is a mocked HTTP transport (no paid inference)."""

import json

import httpx
import pytest
from sqlalchemy import select

from app.connectors.llm import ChatCompletionsLLM
from app.db.models import Theme
from app.services import assistant
from app.services.assistant import ChatTurn

API = "/api/v1"


def _llm(reply=None, status=200):
    def h(req):
        if status != 200:
            return httpx.Response(status, json={"error": "You have no remaining credits"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}],
                                         "usage": {"prompt_tokens": 300, "completion_tokens": 40, "total_tokens": 340}})

    return ChatCompletionsLLM(api_key="sk-test0000", base_url="https://openrouter.test/api/v1", model_id="z-ai/glm-5.3-flash", enabled=True,
                              transport=httpx.MockTransport(h), max_retries=0)


def _with_llm(demo, llm):
    from types import SimpleNamespace

    c = demo.container
    return SimpleNamespace(**{**c.__dict__, "llm": llm})


def _u(text):
    return [ChatTurn(role="user", content=text)]


@pytest.mark.parametrize("text,page", [
    ("take me to sector rotation", "rotation"),
    ("where can I see FII and DII flows?", "flows"),
    ("show the option chain and open interest", "derivatives"),
    ("I want to filter stocks by ROE", "screener"),
    ("set up a price alert", "alerts"),
    ("open my watchlist", "watchlists"),
    ("is the kite connector healthy?", "status"),
    ("concall analyzer", "concalls"),
    ("hello there", None),
])
def test_rule_page_matches_catalog(text, page):
    assert assistant.rule_page(text) == page


def test_every_catalog_path_is_a_frontend_route():
    import pathlib
    import re

    app_tsx = (pathlib.Path(__file__).parents[3] / "frontend/src/App.tsx").read_text()
    routes = {"/" + r for r in re.findall(r'path="([^"]+)"', app_tsx)} | {"/"}
    for p in assistant.PAGES:
        path = p["path"].replace("{ref}", ":ref").replace("{id}", ":id")
        assert path in routes, p["id"]


async def test_rules_fallback_routes_named_stock_to_research(demo):
    async with demo.container.sessions() as s:
        out = await assistant.chat(_with_llm(demo, _llm(status=402)), s, _u("research DEMOIT01 for me"))
    assert out["source"] == "rules" and "rejected" in out["llm_error"]
    assert out["navigate"]["path"] == "/research/NSE:DEMOIT01" and out["navigate"]["page_id"] == "research_stock"


async def test_llm_page_is_validated_and_symbol_resolved(demo):
    llm = _llm({"reply": "Here is the concall follow-through for that company.", "page_id": "concalls", "symbol": "demoit01", "theme": None})
    async with demo.container.sessions() as s:
        out = await assistant.chat(_with_llm(demo, llm), s, _u("did demoit01 deliver on its last concall?"))
    assert out["source"] == "llm" and out["model_id"] == "z-ai/glm-5.3-flash"
    assert out["navigate"]["path"] == "/concalls/NSE:DEMOIT01"


async def test_llm_unknown_stock_falls_back_to_list_page_with_note(demo):
    llm = _llm({"reply": "Opening research.", "page_id": "research_stock", "symbol": "NOSUCHCO", "theme": None})
    async with demo.container.sessions() as s:
        out = await assistant.chat(_with_llm(demo, llm), s, _u("research nosuchco"))
    assert out["navigate"]["path"] == "/research" and "could not find" in out["reply"]


async def test_llm_invented_page_is_rejected(demo):
    llm = _llm({"reply": "Opening the trading desk.", "page_id": "trade_desk", "symbol": None, "theme": None})
    async with demo.container.sessions() as s:
        out = await assistant.chat(_with_llm(demo, llm), s, _u("place an order for me"))
    # The reply failed schema validation twice, so the rules path answers and no invented route is returned.
    assert out["source"] == "rules" and (out["navigate"] is None or out["navigate"]["path"] in {p["path"] for p in assistant.PAGES})


async def test_theme_name_routes_to_theme_detail(demo):
    async with demo.container.sessions() as s:
        t = (await s.scalars(select(Theme).where(Theme.available.is_(True)).limit(1))).first()
        llm = _llm({"reply": "Opening the theme.", "page_id": "theme", "symbol": None, "theme": t.name})
        out = await assistant.chat(_with_llm(demo, llm), s, _u(f"show me the {t.name} theme"))
    assert out["navigate"]["path"] == f"/themes/{t.id}"


async def test_chat_endpoint_contract(demo):
    r = await demo.client.post(f"{API}/assistant/chat", json={"messages": [{"role": "user", "content": "open the screener"}],
                                                             "current_path": "/"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["navigate"]["path"] == "/screener" and body["source"] in {"llm", "rules"} and body["disclaimer"]
    bad = await demo.client.post(f"{API}/assistant/chat", json={"messages": [{"role": "assistant", "content": "hi"}]})
    assert bad.status_code == 422
