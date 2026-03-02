from dataclasses import dataclass, field

from teaming24.llm.model_resolver import (
    build_runtime_llm_config,
    resolve_model_and_call_params,
)


@dataclass
class _LLMConfig:
    default_provider: str = "openai"
    providers: dict = field(default_factory=dict)


def test_local_provider_alias_maps_to_openai_compatible_model() -> None:
    cfg = _LLMConfig(
        default_provider="openai",
        providers={
            "local": {
                "base_url": "http://localhost:11434/v1",
                "default_model": "llama3.1",
            }
        },
    )

    model, params, provider = resolve_model_and_call_params("local/llama3.1", cfg)

    assert provider == "local"
    assert model == "openai/llama3.1"
    assert params["api_base"] == "http://localhost:11434/v1"
    assert params["api_key"] == "local"


def test_unprefixed_model_uses_default_provider(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = _LLMConfig(
        default_provider="openai",
        providers={
            "openai": {"api_key": "${OPENAI_API_KEY}"},
        },
    )

    model, params, provider = resolve_model_and_call_params("gpt-4o-mini", cfg)

    assert provider == "openai"
    assert model == "openai/gpt-4o-mini"
    assert "api_key" not in params


def test_placeholder_api_key_uses_environment_when_present(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-openai-key")
    cfg = _LLMConfig(
        default_provider="openai",
        providers={
            "openai": {"api_key": "${OPENAI_API_KEY}"},
        },
    )

    model, params, provider = resolve_model_and_call_params("gpt-4o-mini", cfg)

    assert provider == "openai"
    assert model == "openai/gpt-4o-mini"
    assert params["api_key"] == "env-openai-key"


def test_provider_name_without_model_uses_provider_default_model() -> None:
    cfg = _LLMConfig(
        default_provider="openai",
        providers={
            "anthropic": {
                "default_model": "claude-3-sonnet-20240229",
                "api_key": "anthropic-key",
            }
        },
    )

    model, params, provider = resolve_model_and_call_params("anthropic", cfg)

    assert provider == "anthropic"
    assert model == "anthropic/claude-3-sonnet-20240229"
    assert params["api_key"] == "anthropic-key"


def test_build_runtime_llm_config_applies_provider_overrides() -> None:
    cfg = _LLMConfig(
        default_provider="openai",
        providers={
            "flock": {
                "litellm_provider": "openai",
                "default_model": "gpt-5.2",
            },
        },
    )
    runtime = {
        "defaultLLMProvider": "flock",
        "flockApiKey": "runtime-flock-key",
        "flockBaseUrl": "https://api.flock.io/v1",
    }

    runtime_cfg = build_runtime_llm_config(
        cfg,
        runtime_settings=runtime,
        runtime_default_provider="flock",
    )
    model, params, provider = resolve_model_and_call_params("gpt-5.2", runtime_cfg)

    assert provider == "flock"
    assert model == "openai/gpt-5.2"
    assert params["api_base"] == "https://api.flock.io/v1"
    assert params["api_key"] == "runtime-flock-key"
