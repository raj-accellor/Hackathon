"""Application settings. All values come from the environment (or `.env`); nothing secret has a
real default and nothing here is ever sent to the browser."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
_INLINE_COMMENT = re.compile(r"\s+#")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"), extra="ignore")

    app_env: Literal["local", "test", "staging", "production"] = "local"
    # demo: connectors point at the local mock MCP servers and every record is labelled demo.
    data_mode: Literal["demo", "live"] = "demo"
    log_level: str = "INFO"
    # PostgreSQL through psycopg 3. Any postgres:// / postgresql:// / +asyncpg URL is rewritten to this driver.
    database_url: str = "postgresql+psycopg://markets:markets@localhost:5432/markets"
    redis_url: str | None = None
    api_auth_token: SecretStr | None = None
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    default_market_timezone: str = "Asia/Kolkata"
    scheduler_enabled: bool = True
    # The schema comes from Alembic (`alembic upgrade head`, run by scripts/dev.sh and the Compose
    # `migrate` service). True creates tables straight from the models, which tests use on SQLite.
    auto_create_schema: bool = False
    auto_initial_refresh: bool = True

    # --- MCP connectors
    nse_bhavcopy_mcp_url: str | None = None
    nse_cm_market_mcp_url: str | None = None
    broker_mcp_url: str | None = None
    mcp_connect_timeout_seconds: float = 10.0
    mcp_tool_timeout_seconds: float = 30.0
    mcp_max_concurrency: int = 4
    mcp_max_retries: int = 2
    nse_mcp_min_interval_seconds: float = 0.2
    broker_mcp_min_interval_seconds: float = 0.35
    breaker_failure_threshold: int = 5
    breaker_reset_seconds: float = 60.0

    # --- Fundamentals (no MCP source supplies statements)
    fundamentals_dir: Path = BACKEND_DIR / "var" / "fundamentals"

    # --- LLM through Hugging Face
    llm_enabled: bool = False
    hf_token: SecretStr | None = None
    hf_model_id: str | None = None
    hf_endpoint_url: str | None = None
    hf_router_url: str = "https://router.huggingface.co/v1"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 1
    llm_max_output_tokens: int = 2000
    llm_temperature: float = 0.1
    llm_daily_budget: int = 200  # research calls per UTC day
    llm_daily_token_budget: int = 2_000_000

    # --- Web research
    # ddgs needs no key. In demo mode web research is switched off: demo symbols are fictional.
    # An unsupported value does not stop start-up; web research is then reported as unavailable.
    web_search_provider: str = "ddgs"
    web_search_api_key: SecretStr | None = None
    web_search_max_results: int = 8
    web_search_timeout_seconds: float = 15.0

    # --- Scheduling (cron, in DEFAULT_MARKET_TIMEZONE)
    theme_refresh_cron: str = "30 6 1 * *"  # monthly
    rrg_refresh_cron: str = "0 */12 * * *"  # every 12 hours
    price_refresh_cron: str = "30 19 * * 1-5"  # after NSE close
    instrument_refresh_cron: str = "0 7 * * 1-5"
    alerts_cron: str = "*/15 * * * *"

    # --- Universe and benchmark
    universe_file: Path = BACKEND_DIR / "data" / "reference" / "nse_universe.csv"
    benchmark_symbol: str = "NIFTY 50"
    history_years: int = 3
    ingest_max_concurrency: int = 4

    # --- Cache TTL (seconds) and rate limits
    cache_ttl_quote: int = 60
    cache_ttl_history: int = 6 * 3600
    cache_ttl_instrument: int = 24 * 3600
    rate_limit_research_per_minute: int = 6
    rate_limit_refresh_per_minute: int = 3
    rate_limit_default_per_minute: int = 300
    max_request_bytes: int = 5_000_000

    mock_mcp_host: str = "127.0.0.1"
    mock_mcp_port: int = Field(default=9100, description="Base port of the local mock MCP servers used in demo mode")

    @model_validator(mode="before")
    @classmethod
    def _clean_env_values(cls, data: Any) -> Any:
        """Make values from .env files and Docker Compose behave the same way.

        - `KEY=value   # note`: Compose keeps the note as part of the value. A `#` preceded by
          whitespace starts a comment and is removed; a `#` inside a value (a URL fragment) is kept.
        - Leading and trailing spaces are removed.
        - `KEY=` means "not set" for optional settings, so a blank API_AUTH_TOKEN does not switch
          authentication on with an empty token and a blank URL does not count as configured.
        """
        if not isinstance(data, dict):
            return data
        optional = {n for n, f in cls.model_fields.items() if f.default is None}
        out: dict[str, Any] = {}
        for k, v in data.items():
            if isinstance(v, str):
                v = _INLINE_COMMENT.split(v, maxsplit=1)[0].strip()
                if not v and k in optional:
                    v = None
            out[k] = v
        return out

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, v: str) -> str:
        """Use psycopg 3 for PostgreSQL whatever form the URL was written in."""
        m = re.match(r"^(postgres|postgresql)(\+[a-z0-9_]+)?://", v)
        if not m:
            return v
        v = "postgresql+psycopg://" + v[m.end():]
        # asyncpg spells TLS as ?ssl=…; libpq/psycopg uses ?sslmode=…
        return re.sub(r"([?&])ssl=", r"\1sslmode=", v)

    @property
    def is_demo(self) -> bool:
        return self.data_mode == "demo"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def resolved_mcp_urls(self) -> dict[str, str | None]:
        """In demo mode the three connectors use the local mock servers unless a URL is set."""
        if self.is_demo:
            # Demo mode always uses the local mock servers, whatever live URLs are present in .env.
            p, h = self.mock_mcp_port, self.mock_mcp_host
            return {"nse_bhavcopy": f"http://{h}:{p}/mcp", "nse_cm_market": f"http://{h}:{p + 1}/mcp", "broker": f"http://{h}:{p + 2}/mcp"}
        return {"nse_bhavcopy": self.nse_bhavcopy_mcp_url, "nse_cm_market": self.nse_cm_market_mcp_url,
                "broker": self.broker_mcp_url}


@lru_cache
def get_settings() -> Settings:
    return Settings()
