"""Tickertape MCP adapter (fallback source for fundamental metrics NSE filings do not provide).

Authorisation follows the MCP OAuth flow the server advertises (RFC 9728 protected-resource metadata, RFC 8414
authorisation-server metadata, RFC 7591 dynamic client registration, PKCE S256, refresh tokens):

1. The app registers itself once as a public client with the configured redirect URI.
2. `login_link()` returns a Tickertape authorisation URL; the account holder signs in and approves in a browser.
3. Tickertape redirects to the app's callback, which exchanges the code for tokens (`complete()`).
4. Tokens are refreshed before they expire. They live only in a server-side file with owner-only permissions; they
   are never sent to the browser, written to logs, or put in prompts.

Policy: only the `market_data` scope is requested, and write tools and tools for the user's account, trading, portfolio,
holdings, watchlists or money movement are blocked by name before any request is sent, whatever the server advertises.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from app.connectors.base import ProviderUnavailable
from app.connectors.mcp_client import McpClient, McpError, McpToolError, McpUnauthorized

NAME = "tickertape_mcp"
SCOPE = "market_data"
LINK_MAX_AGE_SECONDS = 600
REFRESH_MARGIN_SECONDS = 60
# Read-only policy, by tool name (Tickertape names tools <area>_<object>_<read|write>): every write tool, and the families
# that reach the user's account, trading, portfolio or watchlists, are blocked; any other name that mentions orders,
# trading or money movement is blocked too, so a tool added later is refused unless it is plainly market data.
BLOCKED_PREFIXES = ("us_account_", "us_trade_", "pf_", "wl_")
BLOCKED_NAME = re.compile(r"_write$|order|trade|trading|\bbuy\b|\bsell\b|place|cancel|modify|portfolio|holding|position|"
                          r"fund[s]?_?transfer|payment|withdraw|deposit|wallet|kyc|account|balance", re.I)


def allowed_tool(name: str) -> bool:
    return not name.startswith(BLOCKED_PREFIXES) and not BLOCKED_NAME.search(name)


class TickertapeAuth:
    """OAuth client for the Tickertape MCP server. Secrets stay in `store` (chmod 600)."""

    def __init__(self, mcp_url: str, redirect_uri: str, store: Path, *, user_agent: str, timeout: float = 20.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.mcp_url = mcp_url
        self.redirect_uri = redirect_uri
        self.store = Path(store)
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False,
                                       headers={"User-Agent": user_agent, "Accept": "application/json"})
        self._meta: dict[str, Any] | None = None
        self.last_error: str | None = None

    def __repr__(self) -> str:
        return f"TickertapeAuth(mcp_url={self.mcp_url!r}, authorised={self.authorised})"

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ storage
    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.store.read_text())
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self.store.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self.store)

    @property
    def authorised(self) -> bool:
        return bool((self._read().get("tokens") or {}).get("access_token"))

    def state(self) -> dict[str, Any]:
        d = self._read()
        tok = d.get("tokens") or {}
        pending = d.get("pending") or {}
        if tok.get("access_token"):
            return {"state": "authorized", "scope": tok.get("scope"), "expires_at": tok.get("expires_at"),
                    "refreshable": bool(tok.get("refresh_token")), "authorised_at": tok.get("obtained_at")}
        out: dict[str, Any] = {"state": "login_required", "reason": "Connect Tickertape to use it as a fallback for fundamentals"}
        if pending and time.time() - pending.get("created", 0) < LINK_MAX_AGE_SECONDS:
            out["login_pending"] = True
        if self.last_error:
            out["last_error"] = self.last_error
        return out

    # ------------------------------------------------------------------ discovery and registration
    async def _json(self, method: str, url: str, **kw: Any) -> Any:
        try:
            r = await self._http.request(method, url, **kw)
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"Tickertape authorisation server unreachable: {type(e).__name__}", "unavailable", NAME) from e
        if r.status_code >= 400:
            raise ProviderUnavailable(f"Tickertape authorisation server returned HTTP {r.status_code}: {r.text[:160]}", "unavailable", NAME)
        try:
            return r.json()
        except ValueError as e:
            raise ProviderUnavailable("Tickertape authorisation server did not return JSON", "unavailable", NAME) from e

    async def metadata(self) -> dict[str, Any]:
        """Authorisation-server metadata, found from the MCP server's protected-resource metadata."""
        if self._meta is None:
            u = urlsplit(self.mcp_url)
            prm = await self._json("GET", f"{u.scheme}://{u.netloc}/.well-known/oauth-protected-resource{u.path}")
            issuer = (prm.get("authorization_servers") or [None])[0]
            if not issuer:
                raise ProviderUnavailable("Tickertape MCP advertises no authorisation server", "not_supported", NAME)
            meta = await self._json("GET", f"{issuer.rstrip('/')}/.well-known/oauth-authorization-server")
            if "S256" not in (meta.get("code_challenge_methods_supported") or []):
                raise ProviderUnavailable("Tickertape authorisation server does not support PKCE S256", "not_supported", NAME)
            self._meta = {**meta, "resource": prm.get("resource") or self.mcp_url}
        return self._meta

    async def _client_id(self) -> str:
        d = self._read()
        client = d.get("client") or {}
        if client.get("client_id") and client.get("redirect_uri") == self.redirect_uri:
            return str(client["client_id"])
        meta = await self.metadata()
        if not meta.get("registration_endpoint"):
            raise ProviderUnavailable("Tickertape does not offer dynamic client registration", "not_supported", NAME)
        reg = await self._json("POST", meta["registration_endpoint"], json={
            "client_name": "Markets Intelligence (research, read-only)", "redirect_uris": [self.redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "none", "scope": SCOPE})
        if not reg.get("client_id"):
            raise ProviderUnavailable("Tickertape registration returned no client id", "unavailable", NAME)
        d["client"] = {"client_id": reg["client_id"], "redirect_uri": self.redirect_uri,
                       "registration_client_uri": reg.get("registration_client_uri"),
                       "registration_access_token": reg.get("registration_access_token"), "registered_at": time.time()}
        self._write(d)
        return str(reg["client_id"])

    # ------------------------------------------------------------------ authorisation code flow
    async def login_link(self) -> str:
        """A fresh authorisation URL (PKCE S256, single-use state) for the account holder to open."""
        meta = await self.metadata()
        client_id = await self._client_id()
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(24)
        d = self._read()
        d["pending"] = {"state": state, "verifier": verifier, "created": time.time()}
        self._write(d)
        q = {"response_type": "code", "client_id": client_id, "redirect_uri": self.redirect_uri, "scope": SCOPE, "state": state,
             "code_challenge": challenge, "code_challenge_method": "S256", "resource": meta["resource"]}
        return f"{meta['authorization_endpoint']}?{urlencode(q)}"

    async def complete(self, code: str, state: str) -> dict[str, Any]:
        """Exchange the authorisation code from the callback for tokens. The state must match the pending login."""
        d = self._read()
        pending = d.get("pending") or {}
        if not pending or not secrets.compare_digest(str(pending.get("state", "")), state):
            raise ProviderUnavailable("this Tickertape login link is not the latest one; start the connection again", "login_required", NAME)
        if time.time() - pending.get("created", 0) > LINK_MAX_AGE_SECONDS:
            raise ProviderUnavailable("the Tickertape login link expired; start the connection again", "login_required", NAME)
        meta = await self.metadata()
        tok = await self._json("POST", meta["token_endpoint"], data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri,
            "client_id": await self._client_id(), "code_verifier": pending["verifier"], "resource": meta["resource"]})
        d = self._read()
        d.pop("pending", None)
        d["tokens"] = self._tokens(tok)
        self._write(d)
        self.last_error = None
        return self.state()

    @staticmethod
    def _tokens(tok: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
        if not tok.get("access_token"):
            raise ProviderUnavailable("Tickertape returned no access token", "unavailable", NAME)
        exp = tok.get("expires_in")
        return {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token") or (previous or {}).get("refresh_token"),
                "expires_at": time.time() + float(exp) if exp else None, "scope": tok.get("scope") or SCOPE,
                "obtained_at": datetime.now(UTC).isoformat()}

    async def access_token(self, force_refresh: bool = False) -> str:
        """A valid access token, refreshed when it is about to expire (or when `force_refresh`)."""
        d = self._read()
        tok = d.get("tokens") or {}
        if not tok.get("access_token"):
            raise ProviderUnavailable("Tickertape login required: connect Tickertape from the Data status page", "login_required", NAME)
        expiring = tok.get("expires_at") is not None and tok["expires_at"] - time.time() < REFRESH_MARGIN_SECONDS
        if not (force_refresh or expiring):
            return str(tok["access_token"])
        if not tok.get("refresh_token"):
            self.forget_tokens("the Tickertape session expired")
            raise ProviderUnavailable("Tickertape session expired: connect Tickertape again", "login_required", NAME)
        meta = await self.metadata()
        try:
            new = await self._json("POST", meta["token_endpoint"], data={
                "grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": await self._client_id(),
                "resource": meta["resource"]})
        except ProviderUnavailable as e:
            if "HTTP 4" in str(e):  # the refresh token was revoked or expired
                self.forget_tokens("Tickertape refused to renew the session")
                raise ProviderUnavailable("Tickertape session ended: connect Tickertape again", "login_required", NAME) from e
            raise
        d = self._read()
        d["tokens"] = self._tokens(new, tok)
        self._write(d)
        return str(d["tokens"]["access_token"])

    def forget_tokens(self, why: str | None = None) -> None:
        d = self._read()
        d.pop("tokens", None)
        d.pop("pending", None)
        self._write(d)
        self.last_error = why


class TickertapeProvider:
    name = NAME

    def __init__(self, client: McpClient | None, auth: TickertapeAuth | None, is_demo: bool = False) -> None:
        self.client = client
        self.auth = auth
        self.is_demo = is_demo
        self._start_error: str | None = None

    @property
    def configured(self) -> bool:
        return self.client is not None and self.auth is not None

    async def start(self) -> None:
        if not self.configured or not self.auth.authorised:  # type: ignore[union-attr]
            return
        with contextlib.suppress(ProviderUnavailable):
            await self._connect()

    async def _connect(self) -> None:
        assert self.client is not None and self.auth is not None
        for attempt in (0, 1):
            self.client.set_bearer(await self.auth.access_token(force_refresh=attempt == 1))
            try:
                await self.client.connect(refresh=True)
                break
            except McpUnauthorized as e:
                if attempt == 1:
                    self.auth.forget_tokens("Tickertape rejected the saved session")
                    raise ProviderUnavailable("Tickertape login required: connect Tickertape again", "login_required", NAME) from e
            except McpError as e:
                self._start_error = str(e)
                raise ProviderUnavailable(f"Tickertape MCP unavailable: {e}", "unavailable", NAME) from e
        # Read-only policy: block anything that could trade or touch an account, whatever the server advertises.
        allowed = {n for n in self.client.advertised if allowed_tool(n)}
        self.client.allowed_tools = frozenset(allowed)
        self._start_error = None

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
        if self.auth is not None:
            await self.auth.close()

    def health(self) -> dict[str, Any]:
        if not self.configured:
            return {"name": NAME, "state": "not_configured", "url": None, "reason": "TICKERTAPE_MCP_URL is not set",
                    "auth": {"state": "not_configured"}}
        assert self.client is not None and self.auth is not None
        h = self.client.health()
        auth = self.auth.state()
        if auth["state"] != "authorized":
            h = {**h, "state": "login_required"}
        return {**h, "auth": auth, "scope_requested": SCOPE, "start_error": self._start_error,
                "blocked_tools": sorted(set(self.client.advertised) - set(self.client.tools))}

    def tools(self) -> dict[str, dict[str, Any]]:
        if self.client is None:
            return {}
        return {n: {"description": t.description, "input_schema": t.input_schema} for n, t in self.client.tools.items()}

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        """Call an allowed, read-only tool, reconnecting with a refreshed token once if the session lapsed."""
        if not self.configured:
            raise ProviderUnavailable("TICKERTAPE_MCP_URL is not set", "not_configured", NAME)
        assert self.client is not None
        if not self.client.connected:
            await self._connect()
        if not self.client.has_tool(tool):
            raise ProviderUnavailable(f"Tickertape MCP does not offer an allowed tool named {tool}", "not_supported", NAME)
        for attempt in (0, 1):
            try:
                return await self.client.call(tool, arguments)
            except McpUnauthorized as e:
                if attempt == 1:
                    raise ProviderUnavailable("Tickertape login required: connect Tickertape again", "login_required", NAME) from e
                await self._connect()
            except McpToolError as e:
                raise ProviderUnavailable(f"{tool}: {e}", "unavailable", NAME) from e
            except McpError as e:
                raise ProviderUnavailable(str(e), "unavailable", NAME) from e
        raise ProviderUnavailable("unreachable", "unavailable", NAME)

    async def disconnect(self) -> dict[str, Any]:
        if self.auth is not None:
            self.auth.forget_tokens(None)
        if self.client is not None:
            self.client.set_bearer(None)
            self.client.connected = False
        return {"state": "login_required"}


def build_tickertape(s: Any, clock: Any = None) -> TickertapeProvider:
    if s.is_demo or not s.tickertape_mcp_url:
        return TickertapeProvider(None, None, is_demo=s.is_demo)
    client = McpClient(NAME, s.tickertape_mcp_url, connect_timeout=s.mcp_connect_timeout_seconds, tool_timeout=s.mcp_tool_timeout_seconds,
                       max_concurrency=2, max_retries=s.mcp_max_retries, min_interval=s.tickertape_min_interval_seconds,
                       allowed_tools=frozenset(), breaker_threshold=s.breaker_failure_threshold, breaker_reset=s.breaker_reset_seconds,
                       clock=clock)
    auth = TickertapeAuth(s.tickertape_mcp_url, s.tickertape_redirect_uri, s.tickertape_session_file, user_agent=s.nse_filings_user_agent)
    return TickertapeProvider(client, auth)
