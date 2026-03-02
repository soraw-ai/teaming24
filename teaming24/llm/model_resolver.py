"""
Model/provider resolution helpers for Teaming24.

Normalizes model names from config/runtime settings into a concrete LiteLLM
model string and provider call params (api_base/api_key).
"""

from __future__ import annotations

from copy import deepcopy
import os
from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _is_secret_placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    return text.startswith("${") and text.endswith("}")


def _resolve_env_placeholder(value: Any) -> Any:
    """Resolve ${ENV_VAR} placeholders to concrete env values when available."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not (text.startswith("${") and text.endswith("}")):
        return value
    env_key = text[2:-1].strip()
    if not env_key:
        return value
    env_val = os.getenv(env_key)
    if env_val is None:
        return value
    resolved = str(env_val).strip()
    return resolved or value


def _runtime_str(runtime_settings: dict[str, Any] | None, *keys: str) -> str:
    data = runtime_settings or {}
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _provider_env_api_key(provider_key: str) -> str:
    env_by_provider = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "flock": "FLOCK_API_KEY",
        "local": "LOCAL_LLM_API_KEY",
    }
    env_key = env_by_provider.get(provider_key, "")
    return str(os.getenv(env_key, "")).strip() if env_key else ""


def _provider_env_base_url(provider_key: str) -> str:
    env_by_provider = {
        "openai": "OPENAI_API_BASE",
        "anthropic": "ANTHROPIC_API_BASE",
        "flock": "FLOCK_API_BASE",
        "local": "LOCAL_LLM_API_BASE",
    }
    env_key = env_by_provider.get(provider_key, "")
    return str(os.getenv(env_key, "")).strip() if env_key else ""


def _normalize_provider_model(
    provider_key: str,
    model_id: str,
    provider_config: dict[str, Any],
) -> str:
    """Return the concrete LiteLLM model name for the configured provider."""
    litellm_provider = str(provider_config.get("litellm_provider") or "").strip()
    if provider_key == "local":
        target_provider = litellm_provider or "openai"
        return f"{target_provider}/{model_id}" if model_id else target_provider
    if litellm_provider:
        return f"{litellm_provider}/{model_id}" if model_id else litellm_provider
    return f"{provider_key}/{model_id}" if model_id else provider_key


def build_runtime_llm_config(
    llm_config: Any,
    runtime_settings: dict[str, Any] | None = None,
    runtime_default_provider: str | None = None,
) -> Any:
    """Build an LLM config proxy with runtime provider overrides applied."""
    class _LLMConfigProxy:
        pass

    providers_raw = getattr(llm_config, "providers", {}) if llm_config is not None else {}
    providers_copy: dict[str, Any] = deepcopy(providers_raw if isinstance(providers_raw, dict) else {})

    def _override_provider(
        provider_key: str,
        api_key_keys: tuple[str, ...],
        base_url_keys: tuple[str, ...],
    ) -> None:
        existing = providers_copy.get(provider_key, {})
        provider_cfg = dict(existing) if isinstance(existing, dict) else {}
        api_key = _runtime_str(runtime_settings, *api_key_keys)
        base_url = _runtime_str(runtime_settings, *base_url_keys)
        if api_key:
            provider_cfg["api_key"] = api_key
        if base_url:
            provider_cfg["base_url"] = base_url
            provider_cfg["api_base"] = base_url
        providers_copy[provider_key] = provider_cfg

    _override_provider("openai", ("openaiApiKey", "openai_api_key"), ("openaiBaseUrl", "openai_base_url"))
    _override_provider("anthropic", ("anthropicApiKey", "anthropic_api_key"), ("anthropicBaseUrl", "anthropic_base_url"))
    _override_provider("flock", ("flockApiKey", "flock_api_key"), ("flockBaseUrl", "flock_base_url"))
    _override_provider("local", ("localApiKey", "local_api_key"), ("localBaseUrl", "local_base_url"))

    flock_cfg_raw = providers_copy.get("flock", {})
    flock_cfg = dict(flock_cfg_raw) if isinstance(flock_cfg_raw, dict) else {}
    if flock_cfg:
        flock_cfg.setdefault("litellm_provider", "openai")
        flock_cfg.setdefault("base_url", "https://api.flock.io/v1")
        flock_cfg.setdefault("api_base", flock_cfg.get("base_url"))
        flock_cfg.setdefault("default_model", "gpt-5.2")
        providers_copy["flock"] = flock_cfg

    local_model = _runtime_str(runtime_settings, "localCustomModel", "local_custom_model")
    if local_model:
        local_cfg_raw = providers_copy.get("local", {})
        local_cfg = dict(local_cfg_raw) if isinstance(local_cfg_raw, dict) else {}
        local_cfg["default_model"] = local_model
        providers_copy["local"] = local_cfg

    proxy = _LLMConfigProxy()
    proxy.default_provider = (
        str(runtime_default_provider or "").strip()
        or _runtime_str(runtime_settings, "defaultLLMProvider", "default_llm_provider")
        or str(getattr(llm_config, "default_provider", "") or "").strip()
        or "flock"
    )
    proxy.providers = providers_copy
    return proxy


def resolve_model_and_call_params(
    model: str,
    llm_config: Any = None,
) -> tuple[str, dict[str, Any], str]:
    """
    Resolve a model string to an executable LiteLLM model + call params.

    Returns:
        (resolved_model, call_params, provider_key)
    """
    llm_cfg = llm_config
    providers_raw = getattr(llm_cfg, "providers", {}) if llm_cfg is not None else {}
    providers: dict[str, dict[str, Any]] = {}
    if isinstance(providers_raw, dict):
        providers = {str(k): _as_dict(v) for k, v in providers_raw.items()}

    default_provider = str(
        getattr(llm_cfg, "default_provider", "") if llm_cfg is not None else ""
    ).strip() or "flock"

    requested = str(model or "").strip()
    provider = default_provider
    provider_cfg = providers.get(provider, {})

    if "/" in requested:
        prefix, rest = requested.split("/", 1)
        prefix = prefix.strip()
        rest = rest.strip()
        if prefix in providers:
            provider = prefix
            provider_cfg = providers.get(provider, {})
            model_id = rest or str(provider_cfg.get("default_model", "")).strip()
            resolved_model = _normalize_provider_model(provider, model_id, provider_cfg)
        else:
            resolved_model = requested
    elif requested in providers:
        provider = requested
        provider_cfg = providers.get(provider, {})
        model_id = str(provider_cfg.get("default_model", "")).strip()
        resolved_model = _normalize_provider_model(provider, model_id, provider_cfg)
    else:
        provider_cfg = providers.get(provider, {})
        model_id = requested or str(provider_cfg.get("default_model", "")).strip()
        if provider in providers:
            resolved_model = _normalize_provider_model(provider, model_id, provider_cfg)
        else:
            resolved_model = model_id or requested

    call_params: dict[str, Any] = {}
    base_url_raw = provider_cfg.get("base_url") or provider_cfg.get("api_base") or ""
    base_url = str(_resolve_env_placeholder(base_url_raw) or "").strip() or _provider_env_base_url(provider)
    if base_url:
        call_params["api_base"] = base_url

    api_key = _resolve_env_placeholder(provider_cfg.get("api_key"))
    if api_key and not _is_secret_placeholder(api_key):
        call_params["api_key"] = str(api_key)
    else:
        env_api_key = _provider_env_api_key(provider)
        if env_api_key:
            call_params["api_key"] = env_api_key

    # OpenAI-compatible local endpoints often don't require a real key, but
    # LiteLLM/OpenAI adapters may still validate that one exists.
    if provider == "local" and "api_key" not in call_params:
        call_params["api_key"] = os.getenv("LOCAL_LLM_API_KEY", "local")

    return resolved_model, call_params, provider
