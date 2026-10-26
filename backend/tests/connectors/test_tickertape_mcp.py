"""Tickertape MCP OAuth adapter against a mock authorisation server and MCP server: discovery, dynamic registration,
PKCE, single-use state, token refresh, the read-only tool policy, and that no secret appears in status output."""

import base64
import hashlib
import json
import os
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.connectors.base import ProviderUnavailable
from app.connectors.mcp_client import McpClient
from app.connectors.tickertape_mcp import SCOPE, TickertapeAuth, TickertapeProvider

MCP = "https://mcp.tt.example/mcp"
AS = "https://auth.tt.example"
REDIRECT = "http://127.0.0.1:5173/api/v1/sources/tickertape/callback"


class FakeTickertape:
    def __init__(self):
        self.registered: list[dict] = []
        self.codes: dict[str, str] = {}  # code -> challenge
        self.valid = {"AT1"}
        self.refreshes = 0
        self.token_requests: list[dict] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        u = req.url
        if u.path == "/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(200, json={"resource": MCP, "authorization_servers": [AS], "scopes_supported": ["market_data", "trade"]})
        if u.host == "auth.tt.example" and u.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={"issuer": AS, "authorization_endpoint": f"{AS}/oauth/authorize", "token_endpoint": f"{AS}/oauth/token",
                                             "registration_endpoint": f"{AS}/oauth/register", "code_challenge_methods_supported": ["S256"]})
        if u.path == "/oauth/register":
            body = json.loads(req.content)
            self.registered.append(body)
            return httpx.Response(201, json={"client_id": "CID", "registration_access_token": "REGSECRET", **body})
        if u.path == "/oauth/token":
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            self.token_requests.append(form)
            if form["grant_type"] == "authorization_code":
                challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
                if self.codes.get(form["code"]) != challenge:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json={"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600, "scope": SCOPE})
            if form["grant_type"] == "refresh_token":
                if form["refresh_token"] != "RT1":
                    return httpx.Response(400, json={"error": "invalid_grant"})
                self.refreshes += 1
                self.valid = {"AT2"}
                return httpx.Response(200, json={"access_token": "AT2", "expires_in": 3600})
        if u.host == "mcp.tt.example" and u.path == "/mcp":
            if req.headers.get("authorization", "")[7:] not in self.valid:
                return httpx.Response(401, json={"error": "Unauthorized"})
            msg = json.loads(req.content)
            m = msg.get("method")
            if m == "initialize":
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {},
                                                                                              "serverInfo": {"name": "tt"}}},
                                      headers={"mcp-session-id": "S1"})
            if m == "notifications/initialized":
                return httpx.Response(202)
            if m == "tools/list":
                tools = [{"name": "get_stock_ratios", "description": "Key ratios of a stock", "inputSchema": {"type": "object"}},
                         {"name": "place_order", "description": "Place an order", "inputSchema": {"type": "object"}},
                         {"name": "get_portfolio", "description": "User holdings", "inputSchema": {"type": "object"}}]
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": tools}})
            if m == "tools/call":
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"],
                                                 "result": {"content": [{"type": "text", "text": json.dumps({"pe": 21.5})}]}})
        return httpx.Response(404)


@pytest.fixture
def tt(tmp_path):
    fake = FakeTickertape()
    transport = httpx.MockTransport(fake.handler)
    auth = TickertapeAuth(MCP, REDIRECT, tmp_path / "tt.json", user_agent="t", transport=transport)
    client = McpClient("tickertape_mcp", MCP, allowed_tools=frozenset(), max_retries=0, min_interval=0, transport=transport)
    return fake, auth, TickertapeProvider(client, auth), tmp_path / "tt.json"


async def _login(fake, auth):
    link = await auth.login_link()
    q = {k: v[0] for k, v in parse_qs(urlsplit(link).query).items()}
    fake.codes["CODE"] = q["code_challenge"]
    return q


async def test_login_link_uses_pkce_market_data_scope_and_registers_once(tt):
    fake, auth, _p, store = tt
    q = await _login(fake, auth)
    assert q["scope"] == "market_data" and q["code_challenge_method"] == "S256" and q["redirect_uri"] == REDIRECT
    assert q["resource"] == MCP and q["client_id"] == "CID" and len(q["state"]) >= 20
    await auth.login_link()
    assert len(fake.registered) == 1 and fake.registered[0]["token_endpoint_auth_method"] == "none"
    assert oct(os.stat(store).st_mode & 0o777) == "0o600"  # secrets file is owner-only


async def test_callback_rejects_wrong_or_stale_state_and_accepts_the_latest(tt):
    fake, auth, _p, _s = tt
    old = await _login(fake, auth)
    new = await _login(fake, auth)
    with pytest.raises(ProviderUnavailable) as e:
        await auth.complete("CODE", old["state"])  # an earlier link is no longer valid
    assert e.value.state == "login_required"
    state = await auth.complete("CODE", new["state"])
    assert state["state"] == "authorized" and fake.token_requests[-1]["code_verifier"]
    with pytest.raises(ProviderUnavailable):
        await auth.complete("CODE", new["state"])  # single use


async def test_tools_are_filtered_to_read_only_and_tokens_never_appear_in_status(tt):
    fake, auth, p, store = tt
    q = await _login(fake, auth)
    await auth.complete("CODE", q["state"])
    await p.start()
    assert set(p.tools()) == {"get_stock_ratios"}  # order and portfolio tools blocked by policy
    h = p.health()
    assert h["auth"]["state"] == "authorized" and set(h["blocked_tools"]) == {"place_order", "get_portfolio"}
    text = json.dumps(h, default=str) + repr(auth) + repr(p.auth)
    for secret in ("AT1", "RT1", "REGSECRET"):
        assert secret not in text
    assert "AT1" in store.read_text()  # kept server-side only
    with pytest.raises(ProviderUnavailable) as e:
        await p.call("place_order", {})
    assert e.value.state == "not_supported"
    assert await p.call("get_stock_ratios", {}) == {"pe": 21.5}


async def test_expired_session_is_refreshed_once_and_revoked_refresh_asks_for_login(tt):
    fake, auth, p, _s = tt
    q = await _login(fake, auth)
    await auth.complete("CODE", q["state"])
    await p.start()
    fake.valid = set()  # the server now rejects AT1; refreshing issues AT2
    p.client.connected = False
    fake.valid = {"AT2"}
    assert await p.call("get_stock_ratios", {}) == {"pe": 21.5} and fake.refreshes == 1
    fake.valid = set()
    p.client.connected = False
    auth._write({**auth._read(), "tokens": {**auth._read()["tokens"], "refresh_token": "REVOKED"}})
    with pytest.raises(ProviderUnavailable) as e:
        await p.call("get_stock_ratios", {})
    assert e.value.state == "login_required" and auth.state()["state"] == "login_required"


async def test_unconfigured_provider_reports_not_configured(tmp_path):
    p = TickertapeProvider(None, None)
    assert p.health()["auth"]["state"] == "not_configured"
    with pytest.raises(ProviderUnavailable) as e:
        await p.call("x", {})
    assert e.value.state == "not_configured"


async def test_disconnect_deletes_tokens_and_stops_access(tt):
    fake, auth, p, store = tt
    q = await _login(fake, auth)
    await auth.complete("CODE", q["state"])
    await p.start()
    assert await p.call("get_stock_ratios", {}) == {"pe": 21.5}
    assert await p.disconnect() == {"state": "login_required"}
    saved = json.loads(store.read_text())
    assert "tokens" not in saved and "pending" not in saved and auth.state()["state"] == "login_required"
    assert "authorization" not in {k.lower() for k in p.client._http.headers}  # bearer removed from the MCP client
    with pytest.raises(ProviderUnavailable) as e:
        await p.call("get_stock_ratios", {})
    assert e.value.state == "login_required"


# Tool names advertised by the live Tickertape MCP server on 2026-10-26 (names only, from tools/list).
LIVE_TOOLS = ("check_pro_status_read in_etf_chart_read in_etf_events_read in_etf_overview_read in_index_constituents_read in_index_events_read "
              "in_index_overview_read in_index_search_read in_market_news_read in_market_overview_read in_mf_chart_read in_mf_config_read "
              "in_mf_performance_read in_mf_profile_read in_mf_screener_read in_screener_meta_read in_stock_chart_read in_stock_corporate_read "
              "in_stock_financials_read in_stock_overview_read in_stock_search_read screener_saved_read us_assets_get_charts_read "
              "us_assets_get_filters_read us_assets_get_financials_read us_assets_get_info_read us_assets_get_overview_read "
              "us_quotes_get_latest_batch_read us_quotes_get_latest_read us_screener_meta_read us_screener_stocks_read us_screener_universes_read "
              "wl_list_read in_deals_read in_mf_data_read in_mf_search_read in_resolve_sid_read in_screen_stocks_read in_stock_analysis_read "
              "in_stock_extras_read in_stock_ownership_read pf_smallcase_holdings_read screener_manage_write us_account_action_read "
              "us_account_balance_read us_account_fund_history_read us_account_holding_detail_read us_account_order_history_read "
              "us_account_status_read us_market_get_calendar_read us_market_get_status_read us_trade_batch_preview_read us_trade_config_read "
              "us_trade_fees_read us_trade_list_read wl_write").split()


def test_live_tool_names_block_account_trading_portfolio_watchlists_and_writes():
    from app.connectors.tickertape_mcp import allowed_tool

    blocked = {n for n in LIVE_TOOLS if not allowed_tool(n)}
    assert blocked == {n for n in LIVE_TOOLS if n.endswith("_write") or n.startswith(("us_account_", "us_trade_", "pf_", "wl_"))}
    assert {"in_resolve_sid_read", "in_stock_financials_read", "in_stock_analysis_read", "in_screen_stocks_read"} <= set(LIVE_TOOLS) - blocked
