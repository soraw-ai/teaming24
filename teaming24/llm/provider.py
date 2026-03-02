"""
Unified LLM provider with multi-model support and failover.

Uses litellm under the hood so all providers (OpenAI, Anthropic, Google,
Ollama, OpenRouter, etc.) work through a single interface.  Adds:
  - Failover chains: if the primary model fails, try fallbacks.
  - Usage tracking per model/provider.
  - Embedding support.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class LLMResponse:
    """Standardised response from any LLM provider."""
    content: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    raw: Any = None


@dataclass
class UsageRecord:
    """Tracks cumulative usage for a model."""
    model: str = ""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    errors: int = 0
    total_latency_ms: float = 0.0


class LLMProvider:
    """Multi-provider LLM interface with failover and usage tracking.

    Args:
        fallback_chain: Ordered list of model names to try on failure.
    """

    def __init__(self, fallback_chain: list[str] | None = None):
        self.fallback_chain = fallback_chain or []
        self._usage: dict[str, UsageRecord] = {}

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Send a chat completion request with automatic failover.

        Tries ``model`` first, then each model in ``fallback_chain``
        until one succeeds.
        """
        models_to_try = [model] + [m for m in self.fallback_chain if m != model]

        last_error = None
        for m in models_to_try:
            try:
                return await self._call(m, messages, temperature, max_tokens, tools, **kwargs)
            except Exception as exc:
                last_error = exc
                self._record_error(m)
                logger.warning("[LLM] %s failed: %s — trying next", m, exc)

        raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")

    async def embed(self, text: str, model: str = "text-embedding-3-small") -> list[float]:
        """Generate an embedding vector for text."""
        import litellm
        try:
            resp = await litellm.aembedding(model=model, input=[text])
            return resp.data[0]["embedding"]
        except Exception as exc:
            logger.error("[LLM] embedding failed: %s", exc)
            return []

    def get_usage(self, model: str = "") -> dict[str, Any]:
        """Return usage statistics. If model is empty, return all."""
        if model:
            rec = self._usage.get(model, UsageRecord(model=model))
            return {
                "model": rec.model, "calls": rec.calls,
                "prompt_tokens": rec.prompt_tokens,
                "completion_tokens": rec.completion_tokens,
                "errors": rec.errors,
                "avg_latency_ms": rec.total_latency_ms / max(rec.calls, 1),
            }
        return {m: self.get_usage(m) for m in self._usage}

    # ----- Internal -------------------------------------------------------

    async def _call(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int | None,
        tools: list[dict] | None,
        **kwargs,
    ) -> LLMResponse:
        import litellm

        start = time.monotonic()
        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            call_kwargs["max_tokens"] = max_tokens
        if tools:
            call_kwargs["tools"] = tools
        call_kwargs.update(kwargs)

        resp = await litellm.acompletion(**call_kwargs)
        latency = (time.monotonic() - start) * 1000

        usage = getattr(resp, "usage", None)
        prompt_t = getattr(usage, "prompt_tokens", 0) if usage else 0
        comp_t = getattr(usage, "completion_tokens", 0) if usage else 0

        content = ""
        if resp.choices:
            msg = resp.choices[0].message
            content = getattr(msg, "content", "") or ""

        self._record_usage(model, prompt_t, comp_t, latency)

        return LLMResponse(
            content=content,
            model=model,
            prompt_tokens=prompt_t,
            completion_tokens=comp_t,
            total_tokens=prompt_t + comp_t,
            latency_ms=latency,
            raw=resp,
        )

    def _record_usage(self, model: str, prompt_t: int, comp_t: int, latency: float):
        if model not in self._usage:
            self._usage[model] = UsageRecord(model=model)
        rec = self._usage[model]
        rec.calls += 1
        rec.prompt_tokens += prompt_t
        rec.completion_tokens += comp_t
        rec.total_latency_ms += latency

    def _record_error(self, model: str):
        if model not in self._usage:
            self._usage[model] = UsageRecord(model=model)
        self._usage[model].errors += 1


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    """Return the global LLMProvider singleton."""
    global _provider
    if _provider is None:
        _provider = LLMProvider()
    return _provider
