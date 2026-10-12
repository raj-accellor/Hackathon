"""Wires settings into providers and shared infrastructure (one per process)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.connectors.fundamentals_file import FileFundamentalsProvider
from app.connectors.kite_connect import KiteConnectProvider
from app.connectors.kite_mcp import ALLOWED_TOOLS, KiteMcpProvider
from app.connectors.llm import HuggingFaceLLM
from app.connectors.mcp_client import McpClient
from app.connectors.nse_mcp import NseMcpProvider
from app.connectors.web_search import build_search
from app.core.cache import build_cache
from app.core.config import Settings
from app.core.resilience import Clock, SlidingWindowLimiter
from app.db.session import make_engine, make_sessionmaker
from app.services.market_data import CorrelatedMarketData


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    cache: Any
    clock: Clock
    nse: NseMcpProvider
    kite: KiteMcpProvider
    kite_rest: KiteConnectProvider | None
    fundamentals: FileFundamentalsProvider
    search: Any
    llm: HuggingFaceLLM
    market_data: CorrelatedMarketData
    limiters: dict[str, SlidingWindowLimiter] = field(default_factory=dict)
    scheduler: Any = None
    cache_note: str | None = None

    async def check_cache(self) -> None:
        """Confirm the configured cache answers. In local runs an unreachable Redis is replaced by
        the in-process cache (single process only) and the fact is reported; elsewhere it stays an
        error, because separate API and worker processes need a shared cache for job locks."""
        from app.core.cache import MemoryCache

        self.cache_note = None
        try:
            await self.cache.set("health:startup", 1, 5)
        except Exception as e:
            if self.settings.app_env in ("local", "test") and not isinstance(self.cache, MemoryCache):
                self.cache_note = (f"REDIS_URL is set but Redis is unreachable ({type(e).__name__}); using the in-process cache. "
                                   "Start Redis or clear REDIS_URL.")
                try:
                    await self.cache.close()
                except Exception:  # noqa: S110 - the connection never opened
                    pass
                self.cache = MemoryCache()
                self.market_data.cache = self.cache

    async def start(self) -> None:
        await self.check_cache()
        await self.nse.start()
        await self.kite.start()

    async def close(self) -> None:
        await self.nse.close()
        await self.kite.close()
        if self.kite_rest is not None:
            await self.kite_rest.close()
        await self.llm.close()
        await self.cache.close()
        await self.engine.dispose()

    @property
    def benchmark_symbol(self) -> str:
        if self.settings.is_demo:
            from app.devtools.demo_data import BENCHMARK

            return BENCHMARK
        return self.settings.benchmark_symbol


def _secret(v: Any) -> str | None:
    return v.get_secret_value() if v is not None else None


def build_container(s: Settings, clock: Clock | None = None) -> Container:
    clock = clock or Clock()
    urls = s.resolved_mcp_urls()

    def client(name: str, url: str | None, interval: float, allowed: frozenset[str] | None = None) -> McpClient | None:
        if not url:
            return None
        return McpClient(name, url, connect_timeout=s.mcp_connect_timeout_seconds, tool_timeout=s.mcp_tool_timeout_seconds,
                         max_concurrency=s.mcp_max_concurrency, max_retries=s.mcp_max_retries, min_interval=interval,
                         allowed_tools=allowed, breaker_threshold=s.breaker_failure_threshold, breaker_reset=s.breaker_reset_seconds,
                         clock=clock)

    demo_interval = 0.0 if s.is_demo else None
    nse = NseMcpProvider(
        client("nse_bhavcopy", urls["nse_bhavcopy"], demo_interval if demo_interval is not None else s.nse_mcp_min_interval_seconds),
        client("nse_cm_market", urls["nse_cm_market"], demo_interval if demo_interval is not None else s.nse_mcp_min_interval_seconds),
        is_demo=s.is_demo)
    kite = KiteMcpProvider(
        client("kite_mcp", urls["broker"], demo_interval if demo_interval is not None else s.broker_mcp_min_interval_seconds, ALLOWED_TOOLS),
        is_demo=s.is_demo)
    kite_rest = None
    if s.kite_api_key and not s.is_demo:
        kite_rest = KiteConnectProvider(s.kite_api_key, _secret(s.kite_api_secret), _secret(s.kite_access_token),
                                        base_url=s.kite_api_base_url, login_base=s.kite_login_url, clock=clock)
    cache = build_cache(s.redis_url)
    engine = make_engine(s.database_url)
    fundamentals_dir = s.fundamentals_dir / ("demo" if s.is_demo else "live")
    limiters = {
        "research": SlidingWindowLimiter(s.rate_limit_research_per_minute, 60, clock),
        "refresh": SlidingWindowLimiter(s.rate_limit_refresh_per_minute, 60, clock),
        "default": SlidingWindowLimiter(s.rate_limit_default_per_minute, 60, clock),
    }
    return Container(
        settings=s, engine=engine, sessions=make_sessionmaker(engine), cache=cache, clock=clock, nse=nse, kite=kite,
        kite_rest=kite_rest, fundamentals=FileFundamentalsProvider(fundamentals_dir, allow_demo=s.is_demo),
        search=build_search(s.web_search_provider, _secret(s.web_search_api_key), s.web_search_timeout_seconds,
                            disabled_reason="Web research is off in demo mode: demo symbols are fictional, so a real search "
                                            "would return unrelated results." if s.is_demo else None),
        llm=HuggingFaceLLM(token=_secret(s.hf_token), model_id=s.hf_model_id, enabled=s.llm_enabled, endpoint_url=s.hf_endpoint_url,
                           router_url=s.hf_router_url, timeout=s.llm_timeout_seconds, max_retries=s.llm_max_retries,
                           max_output_tokens=s.llm_max_output_tokens, temperature=s.llm_temperature,
                           daily_budget=s.llm_daily_budget, daily_token_budget=s.llm_daily_token_budget),
        market_data=CorrelatedMarketData(nse, kite, kite_rest, cache, s.cache_ttl_quote), limiters=limiters,
    )
