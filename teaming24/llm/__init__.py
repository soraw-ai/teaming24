"""
LLM provider abstraction for Teaming24.

Multi-provider support with failover chains, usage tracking, and
a unified interface for both chat completions and embeddings.

Usage:
    from teaming24.llm import LLMProvider, get_provider
    provider = get_provider()
    response = await provider.complete("gpt-4o", messages=[...])
"""

from teaming24.llm.provider import LLMProvider, LLMResponse, get_provider
from teaming24.llm.model_resolver import resolve_model_and_call_params

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "get_provider",
    "resolve_model_and_call_params",
]
