"""
Pydantic-based config validation for Teaming24.

Validates configuration at startup, catching type errors, missing fields,
and constraint violations before they cause runtime failures. Operates as
a validation layer on top of the existing dataclass config — it does NOT
replace the dataclass definitions, but mirrors the most error-prone
sections with Pydantic models that include validators.

Integration with Config Loading Pipeline
----------------------------------------
This module is called from ``load_config`` (or equivalent) during startup.
Validation runs on the raw YAML dict before it is converted to dataclasses.
Any errors are typically logged as warnings or printed; the caller decides
whether to abort startup or proceed with defaults. Example::

    raw = yaml.safe_load(config_path.read_text())
    errors = validate_config(raw)
    if errors:
        for e in errors:
            logging.warning("Config validation: %s", e)

How to Add Validation for a New Config Section
----------------------------------------------
1. Define a Pydantic schema class (e.g. ``NewSectionSchema``) with fields
   and ``@field_validator`` / ``@model_validator`` decorators.
2. Add the schema as an optional field on ``ConfigSchema``::

       class ConfigSchema(BaseModel):
           ...
           new_section: Optional[NewSectionSchema] = None

3. Ensure the raw YAML key matches the field name (e.g. ``new_section``).

Pydantic Schema → Dataclass Relationship
----------------------------------------
- Schemas here mirror the structure expected by the dataclass config.
- Validation is read-only: schemas validate the raw dict; the actual config
  objects are built elsewhere from the same dict.
- No automatic conversion: this module returns error strings, not config objects.

All Validated Sections and Constraints
--------------------------------------
+------------------+----------------------------------------------------------+
| Section          | Constraints                                              |
+------------------+----------------------------------------------------------+
| system.server    | port 1–65535, workers >= 1                               |
| system.api       | timeouts/queue sizes must be positive                    |
| system.database  | path, auto_migrate                                      |
| system.logging   | level in DEBUG/INFO/WARNING/ERROR/CRITICAL              |
| network.local_node | port 1–65535                                         |
| network.discovery | broadcast_port 1–65535, broadcast_interval, node_expiry |
| payment          | mode in mock/testnet/mainnet, task_price non-negative num  |
| llm              | default_provider (known providers)                      |
| agents           | organizer/coordinator/workers with AgentConfigSchema    |
| framework        | backend in native/crewai                                |
| agent (single)   | tool_profile in minimal/coding/research/networking/full,|
|                  | max_iter >= 1                                            |
+------------------+----------------------------------------------------------+

Usage Examples
--------------
validate_config::

    from teaming24.config.validation import validate_config

    raw = {"system": {"server": {"port": 99999}}, "payment": {"mode": "invalid"}}
    errors = validate_config(raw)
    # errors: ["system → server → port: port must be 1–65535, got 99999",
    #          "payment → mode: payment.mode must be mock/testnet/mainnet, got 'invalid'"]

validate_agent_config::

    from teaming24.config.validation import validate_agent_config

    agent_raw = {"model": "gpt-4", "tool_profile": "unknown", "max_iter": 0}
    errors = validate_agent_config(agent_raw)
    # errors: ["tool_profile: tool_profile must be one of {...}, got 'unknown'",
    #          "max_iter: max_iter must be >= 1"]
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent validation schemas
# ---------------------------------------------------------------------------

class ToolPolicyOverride(BaseModel):
    """Validates per-agent tool policy overlay (allow/deny/also_allow lists).

    Used when ``tools`` is an object rather than a list of tool names.
    """
    allow: list[str] | None = None
    deny: list[str] | None = None
    also_allow: list[str] | None = None


class AgentConfigSchema(BaseModel):
    """Validates a single agent config (organizer / coordinator / worker).

    Enforces: tool_profile in {minimal, coding, research, networking, full};
    max_iter >= 1 when present.
    """
    id: str | None = None
    role: str | None = None
    goal: str | None = None
    backstory: str | None = None
    model: str | None = None
    enabled: bool = True
    tools: list[str] | ToolPolicyOverride | None = None
    tool_profile: str | None = None
    allow_delegation: bool = True
    reasoning: bool = False
    memory: bool = False
    max_iter: int | None = None
    max_execution_time: int | None = None
    respect_context_window: bool | None = None

    @field_validator("tool_profile")
    @classmethod
    def validate_profile(cls, v: str | None) -> str | None:
        if v is not None:
            valid = {"minimal", "coding", "research", "networking", "full"}
            if v not in valid:
                raise ValueError(f"tool_profile must be one of {valid}, got '{v}'")
        return v

    @field_validator("max_iter")
    @classmethod
    def positive_max_iter(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("max_iter must be >= 1")
        return v


# ---------------------------------------------------------------------------
# Server validation schemas
# ---------------------------------------------------------------------------

class ServerSchema(BaseModel):
    """Validates the system.server section. Enforces: port 1–65535, workers >= 1."""
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1

    @field_validator("port")
    @classmethod
    def valid_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"port must be 1–65535, got {v}")
        return v

    @field_validator("workers")
    @classmethod
    def positive_workers(cls, v: int) -> int:
        if v < 1:
            raise ValueError("workers must be >= 1")
        return v


class DatabaseSchema(BaseModel):
    """Validates the system.database section (path, auto_migrate)."""
    path: str = "~/.teaming24/data.db"
    auto_migrate: bool = True


class LoggingSchema(BaseModel):
    """Validates the system.logging section. Enforces: level in DEBUG/INFO/WARNING/ERROR/CRITICAL."""
    level: str = "INFO"

    @field_validator("level")
    @classmethod
    def valid_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid_levels:
            raise ValueError(f"logging.level must be one of {valid_levels}")
        return v.upper()


class ApiSchema(BaseModel):
    """Validates the system.api section (timeouts and queue sizes)."""
    sse_keepalive_timeout: float = 15.0
    approval_timeout: int = 120
    task_keepalive_interval: float = 60.0
    openclaw_event_queue_size: int = 200
    openclaw_stream_poll_timeout: float = 5.0
    openclaw_execution_timeout: float = 600.0
    openclaw_delegate_timeout: float = 600.0
    openclaw_delegate_connect_timeout: float = 10.0
    wallet_ledger_capacity: int = 1000
    chat_buffer_cleanup_delay: float = 300.0
    quality_gate_enabled: bool = True
    quality_benchmark_profile: str = "balanced"
    quality_verifier_enabled: bool = True
    quality_verifier_model: str = "flock/gpt-5.2"
    quality_verifier_temperature: float = 0.0
    quality_confidence_threshold: float = 0.65
    quality_auto_fallback_low_confidence: bool = True
    quality_task_class_policies: dict[str, dict] | None = None

    @field_validator(
        "sse_keepalive_timeout",
        "task_keepalive_interval",
        "openclaw_stream_poll_timeout",
        "openclaw_execution_timeout",
        "openclaw_delegate_timeout",
        "openclaw_delegate_connect_timeout",
    )
    @classmethod
    def positive_float(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("value must be > 0")
        return v

    @field_validator("chat_buffer_cleanup_delay")
    @classmethod
    def non_negative_float(cls, v: float) -> float:
        if v < 0:
            raise ValueError("value must be >= 0")
        return v

    @field_validator(
        "quality_verifier_temperature",
        "quality_confidence_threshold",
    )
    @classmethod
    def bounded_zero_one(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("value must be between 0.0 and 1.0")
        return v

    @field_validator("quality_benchmark_profile")
    @classmethod
    def valid_quality_profile(cls, v: str) -> str:
        valid = {"fast", "balanced", "strict"}
        if str(v).lower() not in valid:
            raise ValueError(f"quality_benchmark_profile must be one of {valid}")
        return str(v).lower()

    @field_validator("approval_timeout", "openclaw_event_queue_size", "wallet_ledger_capacity")
    @classmethod
    def positive_int(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("value must be > 0")
        return v


class SystemSchema(BaseModel):
    """Validates the top-level system section (server, api, database, logging)."""
    server: ServerSchema | None = None
    api: ApiSchema | None = None
    database: DatabaseSchema | None = None
    logging: LoggingSchema | None = None


# ---------------------------------------------------------------------------
# Network validation schemas
# ---------------------------------------------------------------------------

class LocalNodeSchema(BaseModel):
    """Validates network.local_node. Enforces: port 1–65535."""
    name: str = ""
    host: str = "127.0.0.1"
    port: int = 8000
    wallet_address: str = ""

    @field_validator("port")
    @classmethod
    def valid_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"port must be 1–65535, got {v}")
        return v


class DiscoverySchema(BaseModel):
    """Validates network.discovery. Enforces: broadcast_port 1–65535."""
    enabled: bool = True
    broadcast_enabled: bool = True
    broadcast_port: int = 54321
    broadcast_interval: int = 5
    node_expiry_seconds: int = 30
    cleanup_interval: int = 10
    udp_receive_timeout: float = 1.0
    udp_recv_buffer_size: int = 65535
    udp_payload_target_bytes: int = 1200
    discover_dedupe_window_s: float = 1.0
    broadcast_initial_delay: float = 1.0
    broadcast_error_delay: float = 5.0
    max_lan_nodes: int = 1000
    max_wan_nodes: int = 100

    @field_validator("broadcast_port")
    @classmethod
    def valid_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"broadcast_port must be 1–65535, got {v}")
        return v


class NetworkSchema(BaseModel):
    """Validates the top-level network section (local_node, discovery)."""
    local_node: LocalNodeSchema | None = None
    discovery: DiscoverySchema | None = None


# ---------------------------------------------------------------------------
# Payment validation schemas
# ---------------------------------------------------------------------------

class PaymentSchema(BaseModel):
    """Validates the payment section. Enforces: mode in mock/testnet/mainnet; task_price non-negative numeric string."""
    enabled: bool = False
    mode: str = "mock"
    task_price: str = "0.001"

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, v: str) -> str:
        if v not in ("mock", "testnet", "mainnet"):
            raise ValueError(f"payment.mode must be mock/testnet/mainnet, got '{v}'")
        return v

    @field_validator("task_price")
    @classmethod
    def valid_price(cls, v: str) -> str:
        try:
            price = float(v)
        except (ValueError, TypeError) as e:
            raise ValueError(f"task_price must be a numeric string, got '{v}'") from e
        if price < 0:
            raise ValueError(f"task_price must be non-negative, got '{v}'")
        return v


# ---------------------------------------------------------------------------
# LLM validation schemas
# ---------------------------------------------------------------------------

class LLMSchema(BaseModel):
    """Validates the llm section (default_provider). Accepts known providers; unknown values pass through."""
    default_provider: str = "flock"

    @field_validator("default_provider")
    @classmethod
    def valid_provider(cls, v: str) -> str:
        known = {"openai", "anthropic", "google", "azure", "ollama", "local", "litellm", "groq", "deepseek", "flock"}
        if v.lower() not in known:
            # Allow unknown providers (could be custom), but warn-style
            pass
        return v


# ---------------------------------------------------------------------------
# Agents container
# ---------------------------------------------------------------------------

class AgentsSchema(BaseModel):
    """Validates the agents section (organizer, coordinator, workers, dev_workers, prod_workers)."""
    organizer: AgentConfigSchema | None = None
    coordinator: AgentConfigSchema | None = None
    workers: list[AgentConfigSchema] | None = None
    dev_workers: list[str] | None = None
    prod_workers: list[str] | None = None
    simulation_worker_groups: dict[str, list[str]] | None = None
    demo_active_group_id: int | None = None
    demo_active_profile_id: int | None = None


# ---------------------------------------------------------------------------
# Session & bindings
# ---------------------------------------------------------------------------

class PeerMatchSchema(BaseModel):
    kind: str | None = None
    id: str | None = None


class BindingMatchSchema(BaseModel):
    channel: str | None = None
    account_id: str | None = None
    peer: PeerMatchSchema | str | int | float | None = None


class BindingSchema(BaseModel):
    """Supports both preferred and legacy binding shapes."""
    agent_id: str = "main"
    match: BindingMatchSchema | None = None
    channel: str | None = None
    account_id: str | None = None
    peer: PeerMatchSchema | str | int | float | None = None


class SessionSchema(BaseModel):
    dm_scope: str = "per-channel-peer"
    idle_minutes: int | None = 120
    idle_timeout_s: int | None = None
    max_history: int = 200
    store_path: str = "~/.teaming24/sessions.db"
    reset_triggers: list[str] | str = ["/new", "/reset"]

    @field_validator("dm_scope")
    @classmethod
    def valid_dm_scope(cls, v: str) -> str:
        valid = {"main", "per-peer", "per-channel-peer"}
        if v not in valid:
            raise ValueError(f"session.dm_scope must be one of {valid}, got '{v}'")
        return v

    @field_validator("idle_minutes", "idle_timeout_s", "max_history")
    @classmethod
    def non_negative_int(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("value must be >= 0")
        return v


# ---------------------------------------------------------------------------
# AN router
# ---------------------------------------------------------------------------

class ANRouterSchema(BaseModel):
    strategy: str = "organizer_llm"
    model: str = "flock/gpt-5.2"
    routing_temperature: float = 0.1
    routing_max_tokens: int = 1000
    min_pool_members: int = 2
    prefer_remote: bool = False
    capability_match_threshold: float = 0.3
    max_delegation_depth: int = 5
    remote_submit_timeout: float = 30.0
    remote_sse_timeout: float = 600.0
    remote_poll_interval: float = 5.0
    remote_poll_timeout: float = 600.0
    remote_poll_http_timeout: float = 15.0
    remote_http_connect_timeout: float = 10.0
    remote_http_write_timeout: float = 10.0
    remote_http_pool_timeout: float = 10.0

    @field_validator("routing_temperature")
    @classmethod
    def valid_temperature(cls, v: float) -> float:
        if not (0.0 <= v <= 2.0):
            raise ValueError("routing_temperature must be between 0.0 and 2.0")
        return v

    @field_validator("routing_max_tokens", "min_pool_members", "max_delegation_depth")
    @classmethod
    def positive_int(cls, v: int) -> int:
        if v < 1:
            raise ValueError("value must be >= 1")
        return v

    @field_validator(
        "remote_submit_timeout",
        "remote_sse_timeout",
        "remote_poll_interval",
        "remote_poll_timeout",
        "remote_poll_http_timeout",
        "remote_http_connect_timeout",
        "remote_http_write_timeout",
        "remote_http_pool_timeout",
    )
    @classmethod
    def positive_timeout(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("timeout must be > 0")
        return v


# ---------------------------------------------------------------------------
# Framework
# ---------------------------------------------------------------------------

class FrameworkSchema(BaseModel):
    """Validates the framework section. Enforces: backend in native/crewai."""
    backend: str = "native"

    @field_validator("backend")
    @classmethod
    def valid_backend(cls, v: str) -> str:
        if v not in ("native", "crewai"):
            raise ValueError(f"framework.backend must be 'native' or 'crewai', got '{v}'")
        return v


# ---------------------------------------------------------------------------
# Top-level config schema
# ---------------------------------------------------------------------------

class ConfigSchema(BaseModel):
    """Top-level config schema that validates the raw YAML dict.

    Covers: system, network, agents, llm, payment, framework.
    """
    system: SystemSchema | None = None
    network: NetworkSchema | None = None
    agents: AgentsSchema | None = None
    llm: LLMSchema | None = None
    payment: PaymentSchema | None = None
    framework: FrameworkSchema | None = None
    bindings: list[BindingSchema] | None = None
    session: SessionSchema | None = None
    an_router: ANRouterSchema | None = None


# ---------------------------------------------------------------------------
# Public validation function
# ---------------------------------------------------------------------------

def validate_config(raw: dict[str, Any]) -> list[str]:
    """Validate a raw YAML config dict and return a list of error messages.

    Returns:
        List of human-readable error strings. Empty list if the config is valid.
        Each string is formatted as "loc1 → loc2 → ...: message" (e.g.
        "system → server → port: port must be 1–65535, got 99999").
    """
    errors: list[str] = []
    try:
        ConfigSchema.model_validate(raw)
    except Exception as exc:
        logger.warning("Config validation failed: %s", exc, exc_info=True)
        if hasattr(exc, "errors"):
            for err in exc.errors():
                loc = " → ".join(str(loc_part) for loc_part in err.get("loc", []))
                msg = err.get("msg", str(err))
                errors.append(f"{loc}: {msg}")
        else:
            errors.append(str(exc))
    return errors


def validate_agent_config(raw: dict[str, Any]) -> list[str]:
    """Validate a single agent configuration dict.

    Returns:
        List of human-readable error strings. Empty list if the agent config
        is valid. Each string is formatted as "field: message" (e.g.
        "tool_profile: tool_profile must be one of {...}, got 'unknown'").
    """
    errors: list[str] = []
    try:
        AgentConfigSchema.model_validate(raw)
    except Exception as exc:
        logger.warning("Agent config validation failed: %s", exc, exc_info=True)
        if hasattr(exc, "errors"):
            for err in exc.errors():
                loc = " → ".join(str(loc_part) for loc_part in err.get("loc", []))
                msg = err.get("msg", str(err))
                errors.append(f"{loc}: {msg}")
        else:
            errors.append(str(exc))
    return errors
