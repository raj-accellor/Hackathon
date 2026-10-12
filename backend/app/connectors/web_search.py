"""Web search provider abstraction.

Providers: `ddgs` (DuckDuckGo-backed metasearch through the `ddgs` library, no API key) and `brave`
(keyed API). Only the search provider is contacted; result pages are not fetched, which keeps
server-side request forgery out of scope. Result URLs are validated before they are stored or
shown, and snippets are reduced to plain text. With no provider configured, web research is
reported as unavailable; nothing is "searched" from model memory.
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.connectors.base import ProviderUnavailable

# Sources treated as primary for material facts.
PRIMARY_DOMAINS = ("nseindia.com", "bseindia.com", "sebi.gov.in", "rbi.org.in", "mca.gov.in", "nsearchives.nseindia.com")
_TAG = re.compile(r"<[^>]+>")


def safe_url(url: Any) -> str | None:
    """Accept only absolute http(s) URLs with a public-looking hostname."""
    if not isinstance(url, str):
        return None
    try:
        u = urlsplit(url.strip())
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not host or "." not in host or host.endswith((".local", ".internal")):
        return None
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return None
    except ValueError:
        pass
    return url.strip()


def clean_text(s: Any, limit: int = 600) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub("", str(s or "")))).strip()[:limit]


def source_type(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return "primary" if any(host == d or host.endswith("." + d) for d in PRIMARY_DOMAINS) else "secondary"


def normalise(items: list[dict[str, Any]], provider: str) -> list[dict[str, Any]]:
    now = datetime.now(UTC).isoformat()
    out = []  # type: ignore[var-annotated]
    for it in items:
        url = safe_url(it.get("url"))
        if not url:
            continue
        pub = it.get("published")
        out.append({"id": f"S{len(out) + 1}", "title": clean_text(it.get("title"), 200), "url": url,
                    "snippet": clean_text(it.get("snippet")), "published_at": str(pub)[:25] if pub else None,
                    "retrieved_at": now, "source_type": source_type(url), "provider": provider})
    return out


class NullSearch:
    name = "none"
    available = False

    def __init__(self, reason: str | None = None) -> None:
        self.reason = reason or "WEB_SEARCH_PROVIDER is 'none'; web research is unavailable"

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "state": "not_configured", "reason": self.reason}

    async def search(self, query: str, max_results: int = 8) -> list[dict[str, Any]]:
        return []


class DdgsSearch:
    """Keyless search through the `ddgs` library. News results are preferred because they carry a
    publication date; general web results are the fallback. The library is synchronous, so calls
    run in a worker thread with an overall deadline. It relies on public search endpoints that
    rate-limit and can change, so failures are reported as unavailable, never as "no news"."""

    name = "ddgs"
    available = True

    def __init__(self, timeout: float = 15.0, region: str = "in-en", client_factory: Callable[[], Any] | None = None) -> None:
        self.timeout, self.region = timeout, region
        self._factory = client_factory or self._default_client
        self._last_error: str | None = None
        self._last_success: str | None = None

    def _default_client(self) -> Any:
        from ddgs import DDGS

        return DDGS(timeout=int(self.timeout))

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "state": "degraded" if self._last_error else "ok", "last_success_at": self._last_success,
                "last_error": self._last_error, "note": "Keyless DuckDuckGo-backed search; subject to rate limits."}

    def _run(self, query: str, max_results: int) -> list[dict[str, Any]]:
        client = self._factory()
        rows = client.news(query, max_results=max_results, region=self.region) or []
        items = [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("body"), "published": r.get("date") or None} for r in rows]
        if not items:
            rows = client.text(query, max_results=max_results, region=self.region) or []
            items = [{"title": r.get("title"), "url": r.get("href") or r.get("url"), "snippet": r.get("body"), "published": None} for r in rows]
        return items

    async def search(self, query: str, max_results: int = 8) -> list[dict[str, Any]]:
        try:
            items = await asyncio.wait_for(asyncio.to_thread(self._run, query[:400], max_results), timeout=self.timeout * 2 + 5)
        except TimeoutError as e:
            self._last_error = "timeout"
            raise ProviderUnavailable("web search timed out", "unavailable", self.name) from e
        except Exception as e:  # the library raises its own exception types for rate limits and upstream errors
            kind = type(e).__name__
            self._last_error = "rate limit reached" if "Ratelimit" in kind else kind
            raise ProviderUnavailable(f"web search unavailable: {self._last_error}", "unavailable", self.name) from e
        self._last_error, self._last_success = None, datetime.now(UTC).isoformat()
        return normalise(items, self.name)


class _HttpSearch:
    name = "base"
    available = True

    def __init__(self, api_key: str, timeout: float = 15.0, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._key = api_key
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False)
        self._last_error: str | None = None
        self._last_success: str | None = None

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "state": "degraded" if self._last_error and not self._last_success else "ok",
                "last_success_at": self._last_success, "last_error": self._last_error}

    async def _send(self, req: httpx.Request) -> Any:
        try:
            r = await self._http.send(req)
        except httpx.HTTPError as e:
            self._last_error = f"{type(e).__name__}"
            raise ProviderUnavailable(f"web search unavailable: {type(e).__name__}", "unavailable", self.name) from e
        if r.status_code != 200:
            self._last_error = f"HTTP {r.status_code}"
            raise ProviderUnavailable(f"web search returned HTTP {r.status_code}", "unavailable", self.name)
        self._last_error, self._last_success = None, datetime.now(UTC).isoformat()
        return r.json()


class BraveSearch(_HttpSearch):
    name = "brave"

    async def search(self, query: str, max_results: int = 8) -> list[dict[str, Any]]:
        req = self._http.build_request("GET", "https://api.search.brave.com/res/v1/news/search",
                                       headers={"X-Subscription-Token": self._key, "Accept": "application/json"},
                                       params={"q": query[:400], "count": max_results})
        data = await self._send(req)
        return normalise([{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("description"),
                           "published": r.get("page_age") or r.get("age")} for r in data.get("results", [])], self.name)


def build_search(provider: str, api_key: str | None, timeout: float = 15.0,
                 disabled_reason: str | None = None) -> NullSearch | DdgsSearch | BraveSearch:
    if disabled_reason:
        return NullSearch(disabled_reason)
    if provider == "ddgs":
        return DdgsSearch(timeout)
    if provider == "brave" and api_key:
        return BraveSearch(api_key, timeout)
    if provider == "brave":
        return NullSearch("WEB_SEARCH_PROVIDER is 'brave' but WEB_SEARCH_API_KEY is not set")
    if provider not in ("none", ""):
        return NullSearch(f"WEB_SEARCH_PROVIDER '{provider}' is not supported; use 'ddgs', 'brave' or 'none'")
    return NullSearch()
