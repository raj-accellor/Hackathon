"""LLM provider: the configured GLM model through Hugging Face.

Uses the OpenAI-compatible chat-completions route of Hugging Face Inference Providers
(`HF_ROUTER_URL`) or a dedicated Inference Endpoint (`HF_ENDPOINT_URL`). The model id is
configuration, not an assumption: `check_availability()` verifies it against the account.

The model is used for synthesis and classification only. Output must validate against a Pydantic
schema; one constrained repair attempt is made, after which the caller falls back to a
deterministic summary. Tokens, latency, errors and a daily call/token budget are tracked. The
token is never logged or returned.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class LLMUnavailable(LLMError):
    pass


class LLMInvalidOutput(LLMError):
    pass


class LLMBudgetExceeded(LLMError):
    pass


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def extract_json(text: str) -> Any:
    t = _FENCE.sub("", (text or "").strip()).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a != -1 and b > a:
            return json.loads(t[a: b + 1])
        raise


class HuggingFaceLLM:
    name = "huggingface"

    def __init__(self, *, token: str | None, model_id: str | None, enabled: bool, endpoint_url: str | None = None,
                 router_url: str = "https://router.huggingface.co/v1", timeout: float = 60.0, max_retries: int = 1,
                 max_output_tokens: int = 2000, temperature: float = 0.1, daily_budget: int = 200,
                 daily_token_budget: int = 2_000_000, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._token, self.model_id, self.enabled = token, model_id, enabled
        self.base_url = self._normalise(endpoint_url or router_url)
        self.max_retries, self.max_output_tokens, self.temperature = max_retries, max_output_tokens, temperature
        self.daily_budget, self.daily_token_budget = daily_budget, daily_token_budget
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._day = datetime.now(UTC).date()
        self._usage: dict[str, Any] = self._zero()
        self._verified: dict[str, Any] | None = None

    @staticmethod
    def _normalise(url: str) -> str:
        """Accept a base with or without `/v1`, or a full chat-completions URL; return `<base>/v1`."""
        u = url.strip().rstrip("/")
        u = re.sub(r"/chat/completions$", "", u)
        return u if u.endswith("/v1") else u + "/v1"

    def __repr__(self) -> str:
        return f"HuggingFaceLLM(model_id={self.model_id!r}, available={self.available})"

    @staticmethod
    def _zero() -> dict[str, Any]:
        return {"calls": 0, "errors": 0, "invalid_outputs": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "latency_ms_avg": None, "last_error": None, "last_call_at": None}

    @property
    def available(self) -> bool:
        return bool(self.enabled and self._token and self.model_id)

    def _reason(self) -> str | None:
        if not self.enabled:
            return "LLM_ENABLED is false"
        if not self._token:
            return "HF_TOKEN is not set"
        if not self.model_id:
            return "HF_MODEL_ID is not set"
        return None

    def status(self) -> dict[str, Any]:
        self._roll()
        return {"name": self.name, "state": "ok" if self.available else "not_configured", "reason": self._reason(),
                "model_id": self.model_id, "base_url": self.base_url, "verified": self._verified,
                "usage": {**self._usage, "model_id": self.model_id, "day": self._day.isoformat(), "daily_budget": self.daily_budget,
                          "daily_token_budget": self.daily_token_budget,
                          "note": "Cost is not reported by the provider response; token counts are the usage measure."}}

    def _roll(self) -> None:
        today = datetime.now(UTC).date()
        if today != self._day:
            self._day, self._usage = today, self._zero()

    async def close(self) -> None:
        await self._http.aclose()

    async def check_availability(self) -> dict[str, Any]:
        """One minimal request to confirm the token, route and model id work for this account."""
        if not self.available:
            self._verified = {"ok": False, "reason": self._reason(), "checked_at": datetime.now(UTC).isoformat()}
            return self._verified
        try:
            await self._chat([{"role": "user", "content": "Reply with the single word OK."}], None, max_tokens=5)
            self._verified = {"ok": True, "checked_at": datetime.now(UTC).isoformat()}
        except LLMError as e:
            self._verified = {"ok": False, "reason": str(e), "checked_at": datetime.now(UTC).isoformat(),
                              "suggestions": await self._similar_models()}
        return self._verified

    async def _similar_models(self) -> list[str]:
        """Model ids the route offers that resemble the configured one (helps with a mistyped id)."""
        import difflib

        try:
            r = await self._http.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self._token}"})
            ids = [m["id"] for m in r.json().get("data", []) if isinstance(m, dict) and "id" in m] if r.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            return []
        want = (self.model_id or "").lower()
        key = lambda s: re.sub(r"[^a-z0-9.]", "", s.split("/")[-1].lower())  # noqa: E731
        by_key = {i: key(i) for i in ids}
        close = difflib.get_close_matches(key(want), list(by_key.values()), n=5, cutoff=0.6)
        return [i for c in close for i, k in by_key.items() if k == c][:5]

    async def _chat(self, messages: list[dict[str, str]], response_format: dict[str, Any] | None, max_tokens: int | None = None) -> str:
        if not self.available:
            raise LLMUnavailable(self._reason() or "LLM is not configured")
        self._roll()
        if self._usage["calls"] >= self.daily_budget or self._usage["total_tokens"] >= self.daily_token_budget:
            raise LLMBudgetExceeded("daily LLM budget reached; research falls back to deterministic summaries until tomorrow (UTC)")
        body: dict[str, Any] = {"model": self.model_id, "messages": messages, "temperature": self.temperature,
                                "max_tokens": max_tokens or self.max_output_tokens}
        if response_format:
            body["response_format"] = response_format
        last: Exception | None = None
        for _ in range(self.max_retries + 1):
            self._usage["calls"] += 1
            self._usage["last_call_at"] = datetime.now(UTC).isoformat()
            t0 = time.perf_counter()
            try:
                r = await self._http.post(f"{self.base_url}/chat/completions", json=body,
                                          headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"})
            except httpx.HTTPError as e:
                last = LLMUnavailable(f"LLM request failed: {type(e).__name__}")
                self._err(last)
                continue
            if r.status_code >= 500 or r.status_code == 429:
                last = LLMUnavailable(f"LLM provider returned HTTP {r.status_code}")
                self._err(last)
                continue
            if r.status_code != 200:
                detail = ""
                try:
                    err = r.json().get("error")
                    detail = str((err.get("message") if isinstance(err, dict) else err) or "")[:200]
                except (ValueError, AttributeError):
                    pass
                if self._token:
                    detail = detail.replace(self._token, "[REDACTED]")
                e2 = LLMUnavailable(f"LLM provider rejected the request (HTTP {r.status_code})" + (f": {detail}" if detail else "")
                                    + ". Check HF_TOKEN, HF_MODEL_ID and the route.")
                self._err(e2)
                raise e2
            data = r.json()
            ms = (time.perf_counter() - t0) * 1000
            prev = self._usage["latency_ms_avg"]
            self._usage["latency_ms_avg"] = round(ms if prev is None else prev * 0.8 + ms * 0.2, 1)
            u = data.get("usage") or {}
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                self._usage[k] += int(u.get(k) or 0)
            try:
                return data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as e:
                raise LLMInvalidOutput("LLM response had no message content") from e
        raise last or LLMUnavailable("LLM request failed")

    def _err(self, e: Exception) -> None:
        self._usage["errors"] += 1
        self._usage["last_error"] = str(e)[:200]

    async def complete_validated(self, system: str, user: str, model: type[T]) -> T:
        """Chat completion constrained to `model`'s JSON schema and validated with Pydantic.
        Makes one repair attempt when the output does not validate."""
        schema = model.model_json_schema()
        fmt = {"type": "json_schema", "json_schema": {"name": model.__name__, "schema": schema, "strict": True}}
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        text = await self._chat(messages, fmt)
        try:
            return model.model_validate(extract_json(text))
        except (ValidationError, json.JSONDecodeError, ValueError) as first:
            problem = str(first)[:600]
        messages += [
            {"role": "assistant", "content": text[:4000]},
            {"role": "user", "content": "Your previous reply did not match the required JSON schema.\n"
                                        f"Problem: {problem}\nReturn only one JSON object that validates against this schema, "
                                        f"with no commentary and no code fences:\n{json.dumps(schema)}"},
        ]
        text = await self._chat(messages, fmt)
        try:
            return model.model_validate(extract_json(text))
        except (ValidationError, json.JSONDecodeError, ValueError) as e:
            self._usage["invalid_outputs"] += 1
            raise LLMInvalidOutput(f"LLM output failed schema validation after one repair attempt: {str(e)[:200]}") from e
