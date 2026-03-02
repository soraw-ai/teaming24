"""
Configuration management for Teaming24.

All configuration comes from a single file: teaming24.yaml
This is the ONLY source of truth for all settings.

TODO(refactor): Identity and env-loader helpers have been extracted, but
    this module still mixes dataclass definitions with config assembly.
    Remaining splits:
    - config/types.py — dataclass definitions
    - config/builder.py — _build_config_from_dict
    The repeated _dict_to_dataclass pattern could use a registry/mapping
    approach.

Structure:
    - system.*     : Server, API, database, logging
    - security.*   : Authentication, encryption, rate limiting
    - network.*    : Node identity, discovery, connections
    - agents.*     : Agent types and configurations
    - llm.*        : LLM providers and models
    - tools.*      : Available tools
    - payment.*    : x402 payment protocol
    - runtime.*    : Sandbox and execution

Environment Variables (override config file):
    TEAMING24_PORT          - system.server.port
    TEAMING24_HOST          - system.server.host
    TEAMING24_LOG_LEVEL     - system.logging.level
    TEAMING24_DB_PATH       - system.database.path
    TEAMING24_CONFIG        - Use different config file
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from teaming24.config.identity import resolve_node_identity
from teaming24.config.loader import apply_env_overrides
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

# Config directory and file
CONFIG_DIR = Path(__file__).parent
CONFIG_FILE = "teaming24.yaml"


# =============================================================================
# System Configuration
# =============================================================================

@dataclass
class ServerConfig:
    """Server configuration.

    Attributes:
        host: Bind address (0.0.0.0 = all interfaces).
        port: HTTP listen port.
        workers: Number of Uvicorn worker processes.
        reload: Enable auto-reload on code changes (dev only).
    """
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1
    reload: bool = False


@dataclass
class ApiConfig:
    """API configuration.

    Centralizes all API-level constants that were previously hardcoded
    across server.py and other modules.

    Attributes:
        base_url: Base URL for API (used in links, callbacks).
        prefix: URL path prefix for API routes (e.g. /api).
        docs_enabled: Enable OpenAPI/Swagger docs at /docs.
        heartbeat_timeout: Seconds before task considered stale if no heartbeat.
        sse_keepalive_timeout: SSE keepalive comment interval (seconds).
        docker_stop_timeout: Seconds to wait when stopping Docker containers.
        http_client_timeout: Default timeout for outbound HTTP requests.
        streaming_chunk_delay: Delay between stream chunks (seconds).
        stream_truncate: Max chars for incremental stream; full content in result event.
        update_queue_timeout: Timeout for update queue operations.
        task_heartbeat_interval: How often task sends heartbeat when idle (seconds).
        step_queue_timeout: Timeout when waiting for step queue (shorter = more responsive).
        task_execution_timeout: Idle timeout in seconds—no step/keepalive for N s (0=disabled, keep waiting). Resets on activity.
        task_keepalive_interval: Keepalive interval for long-running task streams (seconds).
        health_check_interval: Interval between health checks (seconds).
        health_check_http_timeout: HTTP timeout for health check requests.
        outbound_max_failures: Mark peer offline after N consecutive failures.
        inbound_stale_timeout: Seconds before inbound peer marked stale.
        max_events_kept: Max events kept per task for replay.
        task_list_default_limit: Default limit for task list API.
        subscription_queue_size: Max size of subscription event queue.
        update_queue_size: Max size of task update queue.
        max_demo_poll_iterations: Max poll iterations for demo mode.
        approval_timeout: Seconds to wait for user approval before timeout.
        event_buffer_capacity: Max events in SSE replay buffer (reconnect).
        step_content_max_chars: Max chars for step content in API responses.
        step_thought_max_chars: Max chars for step thought in API responses.
        step_observation_max_chars: Max chars for step observation in API responses.
        step_event_content_max_chars: Max chars for content in SSE step events.
        step_event_thought_max_chars: Max chars for thought in SSE step events.
        step_event_observation_max_chars: Max chars for observation in SSE step events.
        step_event_string_repr_max_chars: Max chars for string repr in SSE events.
        result_empty_threshold: Min chars to consider result non-empty.
        result_fallback_min_chars: Min step content length for fallback result.
        aggregate_context_max_chars: Max chars sent to LLM for Organizer merge.
        aggregate_output_max_chars: Max chars in merged output.
        aggregate_llm_min_response: Min LLM response length to accept as valid.
        max_execution_rounds: Max self-improvement rounds per task (1 = no retry).
        execution_round_eval_ctx_chars: Max chars of result sent to LLM for round evaluation.
        quality_gate_enabled: Enable result quality gating.
        quality_benchmark_profile: Quality profile (fast, balanced, strict).
        quality_verifier_enabled: Enable independent verifier-model pass.
        quality_verifier_model: Model used by independent verifier pass.
        quality_verifier_temperature: Temperature for verifier model.
        quality_confidence_threshold: Min combined confidence (0-1) to accept result.
        quality_auto_fallback_low_confidence: Retry when confidence is below threshold.
        quality_task_class_policies: Per-task-class policy overrides.
        openclaw_event_queue_size: Max buffered OpenClaw SSE events per request.
        openclaw_stream_poll_timeout: OpenClaw SSE queue poll timeout (seconds).
        openclaw_execution_timeout: OpenClaw hard execution timeout (seconds).
        openclaw_delegate_timeout: OpenClaw delegate request total timeout (seconds).
        openclaw_delegate_connect_timeout: OpenClaw delegate request connect timeout (seconds).
        wallet_ledger_capacity: Max in-memory wallet transactions retained.
        chat_buffer_cleanup_delay: Grace period before cleaning chat event buffer and task budget state (seconds).
    """
    base_url: str = "http://localhost:8000"
    prefix: str = "/api"
    docs_enabled: bool = True
    heartbeat_timeout: int = 60
    sse_keepalive_timeout: float = 15.0
    docker_stop_timeout: int = 10
    http_client_timeout: float = 10.0
    streaming_chunk_delay: float = 0.02
    stream_truncate: int = 12000
    update_queue_timeout: float = 30.0
    task_heartbeat_interval: float = 1.0  # Shorter = more responsive when agent is thinking
    step_queue_timeout: float = 0.2      # Shorter = faster wake when steps arrive
    task_execution_timeout: float = 0.0  # 0 = disabled (keep waiting while task runs); >0 = idle timeout in seconds
    task_keepalive_interval: float = 60.0
    health_check_interval: int = 20
    health_check_http_timeout: float = 5.0
    outbound_max_failures: int = 10
    inbound_stale_timeout: int = 300
    max_events_kept: int = 100
    task_list_default_limit: int = 20
    subscription_queue_size: int = 100
    update_queue_size: int = 100
    max_demo_poll_iterations: int = 300
    approval_timeout: int = 120
    event_buffer_capacity: int = 500
    step_content_max_chars: int = 3000
    step_thought_max_chars: int = 3000
    step_observation_max_chars: int = 2000
    step_event_content_max_chars: int = 1000
    step_event_thought_max_chars: int = 500
    step_event_observation_max_chars: int = 500
    step_event_string_repr_max_chars: int = 500
    result_empty_threshold: int = 50
    result_fallback_min_chars: int = 80
    aggregate_context_max_chars: int = 16000
    aggregate_output_max_chars: int = 5000
    aggregate_llm_min_response: int = 80
    max_execution_rounds: int = 2  # Fewer rounds; give-up detection allows early exit
    execution_round_eval_ctx_chars: int = 6000
    openclaw_event_queue_size: int = 200
    openclaw_stream_poll_timeout: float = 5.0
    openclaw_execution_timeout: float = 600.0
    openclaw_delegate_timeout: float = 600.0
    openclaw_delegate_connect_timeout: float = 10.0
    wallet_ledger_capacity: int = 1000
    chat_buffer_cleanup_delay: float = 300.0
    # Local coordinator: refinement rounds before returning to Organizer
    local_coordinator_max_refinement_rounds: int = 1  # 1 = no local refinement; give-up allowed
    # Quality gate and verifier settings
    quality_gate_enabled: bool = True
    quality_benchmark_profile: str = "balanced"  # fast | balanced | strict
    quality_verifier_enabled: bool = True
    quality_verifier_model: str = "flock/gpt-5.2"
    quality_verifier_temperature: float = 0.0
    quality_confidence_threshold: float = 0.55
    quality_auto_fallback_low_confidence: bool = True
    quality_task_class_policies: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class FrontendConfig:
    """Frontend configuration.

    Attributes:
        dev_host: Host for frontend dev server.
        dev_port: Port for frontend dev server.
    """
    dev_host: str = "localhost"
    dev_port: int = 8088


@dataclass
class CorsConfig:
    """CORS configuration.

    Attributes:
        allow_origins: Allowed CORS origins.
        allow_credentials: Allow credentials in CORS requests.
        allow_methods: Allowed HTTP methods.
        allow_headers: Allowed request headers.
    """
    allow_origins: list[str] = field(default_factory=lambda: [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8088",
        "http://127.0.0.1:8088",
    ])
    allow_credentials: bool = True
    allow_methods: list[str] = field(default_factory=lambda: ["*"])
    allow_headers: list[str] = field(default_factory=lambda: ["*"])


@dataclass
class DatabaseConfig:
    """Database configuration.

    Attributes:
        path: Path to SQLite database file (~ expands to home).
        auto_migrate: Run migrations on startup.
    """
    path: str = "~/.teaming24/data.db"
    auto_migrate: bool = True


@dataclass
class LoggingConfig:
    """Logging configuration.

    Attributes:
        level: Log level (DEBUG, INFO, WARNING, ERROR).
        format: Log message format string.
        file: Optional log file path; None = stdout only.
    """
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: str | None = None


@dataclass
class DevModeConfig:
    """Dev mode configuration.

    When ``enabled=True``, workers are loaded from ``agents.dev_workers``.
    When ``enabled=False``, workers are loaded from ``agents.prod_workers``.

    All built-in worker definition modules are imported automatically.
    ``worker_modules`` remains only as a backward-compatibility field for
    old YAML files and is ignored at runtime.

    Attributes:
        enabled: If True, use dev_workers; else prod_workers.
        worker_modules: Deprecated compatibility field; ignored at runtime.
    """
    enabled: bool = False
    worker_modules: list[str] = field(default_factory=list)


@dataclass
class TaskManagerConfig:
    """Task manager runtime configuration.

    Attributes:
        max_tasks_in_memory: Max tasks kept in memory.
        task_expiry_seconds: Seconds before expired task is cleaned.
        cleanup_interval_seconds: Interval between cleanup runs.
        list_tasks_default_limit: Default limit for list_tasks API.
        phase_percentages: Progress % per phase (received, routing, etc.).
    """
    max_tasks_in_memory: int = 1000
    task_expiry_seconds: int = 86400
    cleanup_interval_seconds: int = 300
    list_tasks_default_limit: int = 100
    phase_percentages: dict[str, int] = field(default_factory=lambda: {
        "received": 5,
        "routing": 10,
        "dispatching": 20,
        "executing": 30,
        "aggregating": 85,
        "completed": 100,
    })


@dataclass
class SystemConfig:
    """System configuration container.

    Attributes:
        server: Server settings.
        api: API settings.
        frontend: Frontend dev settings.
        cors: CORS settings.
        database: Database settings.
        logging: Logging settings.
        dev_mode: Dev mode settings.
        task_manager: Task manager settings.
    """
    server: ServerConfig = field(default_factory=ServerConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    frontend: FrontendConfig = field(default_factory=FrontendConfig)
    cors: CorsConfig = field(default_factory=CorsConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    dev_mode: DevModeConfig = field(default_factory=DevModeConfig)
    task_manager: TaskManagerConfig = field(default_factory=TaskManagerConfig)


# =============================================================================
# Security Configuration
# =============================================================================

@dataclass
class JwtConfig:
    """JWT configuration.

    Attributes:
        secret: Secret key for signing tokens.
        algorithm: JWT algorithm (e.g. HS256).
        expiration: Access token expiry (seconds).
        refresh_expiration: Refresh token expiry (seconds).
    """
    secret: str = "change-this-in-production"
    algorithm: str = "HS256"
    expiration: int = 3600
    refresh_expiration: int = 604800


@dataclass
class ApiKeyConfig:
    """API Key configuration.

    Attributes:
        enabled: Enable API key authentication.
        header_name: HTTP header for API key.
        keys: List of keys with metadata (e.g. name, key, roles).
    """
    enabled: bool = False
    header_name: str = "X-API-Key"
    keys: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RateLimitConfig:
    """Rate limit configuration.

    Attributes:
        enabled: Enable rate limiting.
        requests_per_minute: Max requests per minute per client.
        burst: Max burst size (token bucket).
    """
    enabled: bool = True
    requests_per_minute: int = 60
    burst: int = 10


@dataclass
class EncryptionConfig:
    """Encryption configuration.

    Attributes:
        enabled: Enable encryption at rest.
        algorithm: Encryption algorithm (e.g. AES-256-GCM).
    """
    enabled: bool = False
    algorithm: str = "AES-256-GCM"


@dataclass
class SecurityConfig:
    """Security configuration container.

    Attributes:
        jwt: JWT settings.
        api_keys: API key auth settings.
        rate_limit: Rate limiting settings.
        encryption: Encryption settings.
        connection_password: Optional password for peer connections.
    """
    jwt: JwtConfig = field(default_factory=JwtConfig)
    api_keys: ApiKeyConfig = field(default_factory=ApiKeyConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    encryption: EncryptionConfig = field(default_factory=EncryptionConfig)
    connection_password: str | None = None


# =============================================================================
# Network Configuration
# =============================================================================

@dataclass
class LocalNodeConfig:
    """Local AgentaNet node configuration.

    Identity model:
      - ``an_id``  — the **canonical unique identifier** for this AN.
        Format: ``{wallet_address}-{6 random hex}``.
        Generated once at startup; used in delegation chains,
        ``requester_id``, loop detection, and all identity comparisons.
      - ``wallet_address`` — the node's crypto wallet (``0x…``).
        Read from ``.env`` (``TEAMING24_WALLET_ADDRESS``) or YAML.
        Auto-generated from hostname + MAC if empty.
      - ``name`` — human-readable display name for the dashboard /
        logs / peer discovery.  Has **no** uniqueness requirement.
        Defaults to ``{host}:{port}`` if not set.

    Attributes:
        name: Human-readable display name
        an_id: Canonical unique ID (populated in post-init)
        wallet_address: Crypto wallet. Auto-generated if empty.
        host: Node bind host.
        port: Node bind port.
        description: Node description for discovery.
        capability: Node capability string.
        region: Region/location label.
    """
    name: str = ""
    an_id: str = ""
    wallet_address: str = ""
    host: str = "127.0.0.1"
    port: int = 8000
    description: str = "Local node hosting Organizer, Coordinator, and Workers"
    capability: str = "General Purpose"
    region: str = "Local"


@dataclass
class DiscoveryConfig:
    """LAN Discovery configuration.

    Attributes:
        enabled: Enable LAN discovery.
        broadcast_enabled: If False, listen only (no outgoing broadcast/discover).
        broadcast_port: UDP port for discovery broadcasts.
        broadcast_interval: Seconds between broadcasts.
        node_expiry_seconds: Seconds before node removed from cache.
        cleanup_interval: Seconds between cleanup runs.
        max_lan_nodes: Max LAN nodes to cache.
        max_wan_nodes: Max WAN nodes to cache.
        udp_receive_timeout: UDP receive timeout (seconds).
        udp_recv_buffer_size: UDP receive buffer size in bytes.
        udp_payload_target_bytes: Target max payload size to avoid fragmentation.
        discover_dedupe_window_s: De-duplication window for repeated discover requests.
        broadcast_initial_delay: Delay before first broadcast.
        broadcast_error_delay: Delay after broadcast error before retry.
    """
    enabled: bool = True
    broadcast_enabled: bool = True
    broadcast_port: int = 54321
    broadcast_interval: int = 5
    node_expiry_seconds: int = 30
    cleanup_interval: int = 10
    max_lan_nodes: int = 1000
    max_wan_nodes: int = 100
    udp_receive_timeout: float = 1.0
    udp_recv_buffer_size: int = 65535
    udp_payload_target_bytes: int = 1200
    discover_dedupe_window_s: float = 1.0
    broadcast_initial_delay: float = 1.0
    broadcast_error_delay: float = 5.0


@dataclass
class ConnectionConfig:
    """Connection configuration.

    Attributes:
        timeout: Connection timeout (seconds).
        retry_attempts: Max retries on failure.
        retry_delay: Delay between retries (seconds).
        keepalive_interval: Keepalive ping interval (seconds).
        handshake_timeout: Handshake timeout (seconds).
        peer_info_timeout: Peer info fetch timeout (seconds).
        connect_node_timeout: Node connect timeout (seconds).
    """
    timeout: int = 30
    retry_attempts: int = 3
    retry_delay: int = 5
    keepalive_interval: int = 60
    handshake_timeout: float = 5.0
    peer_info_timeout: float = 5.0
    connect_node_timeout: float = 5.0


@dataclass
class MessagingConfig:
    """Messaging configuration.

    Attributes:
        max_message_size: Max message size in bytes.
        queue_size: Max messages in queue.
        message_ttl: Message TTL (seconds).
    """
    max_message_size: int = 1048576
    queue_size: int = 1000
    message_ttl: int = 3600


@dataclass
class SubscriptionConfig:
    """SSE Subscription configuration.

    Attributes:
        max_queue_size: Max events per subscription queue.
        max_subscribers: Max concurrent subscribers.
        keepalive_interval: SSE keepalive interval (seconds).
    """
    max_queue_size: int = 100
    max_subscribers: int = 100
    keepalive_interval: int = 15


@dataclass
class MarketplaceConfig:
    """Marketplace configuration.

    Attributes:
        url: Marketplace API URL.
        auto_rejoin: Auto-rejoin marketplace on disconnect.
    """
    url: str = "http://100.64.1.3:8080/api/marketplace"
    auto_rejoin: bool = True


@dataclass
class AgentaNetCentralConfig:
    """AgentaNet Central Service configuration.

    Attributes:
        url: Central service API URL.
        token: Auth token for central service.
        heartbeat_interval: Heartbeat interval (seconds).
        enabled: Enable central registration.
        register_timeout: Registration timeout (seconds).
        heartbeat_http_timeout: Heartbeat HTTP timeout (seconds).
        search_timeout: Search timeout (seconds).
        search_page_size: Page size for central marketplace search pagination.
        search_max_pages: Max pages to fetch from central search per request.
        get_node_timeout: Get node timeout (seconds).
        marketplace_cache_ttl: Cache TTL for marketplace fallback data (seconds).
    """
    url: str = "http://100.64.1.3:8080"
    token: str = ""
    heartbeat_interval: int = 60
    enabled: bool = True
    register_timeout: float = 10.0
    heartbeat_http_timeout: float = 5.0
    search_timeout: float = 10.0
    search_page_size: int = 100
    search_max_pages: int = 20
    get_node_timeout: float = 5.0
    marketplace_cache_ttl: float = 300.0


@dataclass
class NetworkConfig:
    """Network configuration container.

    Attributes:
        auto_online: Auto-mark node online on startup.
        local_node: Local node identity and bind settings.
        discovery: LAN discovery settings.
        connection: Peer connection settings.
        messaging: Message queue settings.
        subscription: SSE subscription settings.
        marketplace: Marketplace settings.
        agentanet_central: Central service settings.
        remote_nodes: Static list of remote nodes.
    """
    auto_online: bool = True
    local_node: LocalNodeConfig = field(default_factory=LocalNodeConfig)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    connection: ConnectionConfig = field(default_factory=ConnectionConfig)
    messaging: MessagingConfig = field(default_factory=MessagingConfig)
    subscription: SubscriptionConfig = field(default_factory=SubscriptionConfig)
    marketplace: MarketplaceConfig = field(default_factory=MarketplaceConfig)
    agentanet_central: AgentaNetCentralConfig = field(default_factory=AgentaNetCentralConfig)
    remote_nodes: list[dict[str, Any]] = field(default_factory=list)


# =============================================================================
# Agent Configuration
# =============================================================================

@dataclass
class AgentDefaultsConfig:
    """Default agent configuration.

    Attributes:
        model: Default LLM model (provider/model format).
        temperature: Default sampling temperature (0-1).
        max_tokens: Default max tokens per response.
        timeout: Default request timeout (seconds).
        retry_on_error: Retry on LLM errors.
        max_retries: Max retries on failure.
        planning_llm: Model for planning/reasoning.
        max_reasoning_attempts: Max reasoning iterations.
        verbose: Verbose logging.
        allow_delegation: Allow task delegation to other agents.
    """
    model: str = "flock/gpt-5.2"
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout: int = 120
    retry_on_error: bool = True
    max_retries: int = 3
    planning_llm: str = "flock/gpt-5.2"
    max_reasoning_attempts: int = 3
    verbose: bool = True
    allow_delegation: bool = True


@dataclass
class OrganizerConfig:
    """Organizer agent configuration.

    Must include all YAML fields consumed by ``AgentFactory.create_agent()``.
    Missing fields here will be silently dropped by ``_dict_to_dataclass``.

    Attributes:
        enabled: Enable Organizer agent.
        model: LLM model for Organizer.
        role: Agent role string.
        goal: Agent goal/prompt.
        backstory: Agent backstory.
        description: Agent description.
        system_prompt: System prompt override.
        planning_depth: Max planning depth.
        tools: Tool names for Organizer.
        allow_delegation: Allow delegation.
        reasoning: Enable reasoning mode.
        max_reasoning_attempts: Max reasoning iterations.
        memory: Enable memory.
        max_iter: Max iterations.
        max_execution_time: Max execution time (seconds).
        respect_context_window: Respect context window limits.
    """
    enabled: bool = True
    model: str = "flock/gpt-5.2"
    role: str = "Organizer"
    goal: str = ""
    backstory: str = ""
    description: str = "High-level task planning and decomposition"
    system_prompt: str = ""
    planning_depth: int = 3
    tools: list[str] = field(default_factory=list)
    allow_delegation: bool = True
    reasoning: bool = False
    max_reasoning_attempts: int | None = None
    memory: bool = False
    max_iter: int | None = None
    max_execution_time: int | None = None
    respect_context_window: bool | None = None


@dataclass
class CoordinatorConfig:
    """Coordinator agent configuration.

    Must include all YAML fields consumed by ``AgentFactory.create_agent()``.
    Missing fields here will be silently dropped by ``_dict_to_dataclass``.

    Attributes:
        enabled: Enable Coordinator agent.
        model: LLM model for Coordinator.
        role: Agent role string.
        goal: Agent goal/prompt.
        backstory: Agent backstory.
        description: Agent description.
        system_prompt: System prompt override.
        max_workers: Max workers to manage.
        tools: Tool names for Coordinator.
        allow_delegation: Allow delegation.
        reasoning: Enable reasoning mode.
        max_reasoning_attempts: Max reasoning iterations.
        memory: Enable memory.
        max_iter: Max iterations.
        max_execution_time: Max execution time (seconds).
        respect_context_window: Respect context window limits.
    """
    enabled: bool = True
    model: str = "flock/gpt-5.2"
    role: str = "local team coordinator"
    goal: str = ""
    backstory: str = ""
    description: str = "Task coordination and worker management"
    system_prompt: str = ""
    max_workers: int = 5
    tools: list[str] = field(default_factory=list)
    allow_delegation: bool = True
    reasoning: bool = False
    max_reasoning_attempts: int | None = None
    memory: bool = False
    max_iter: int | None = None
    max_execution_time: int | None = None
    respect_context_window: bool | None = None


@dataclass
class AgentsConfig:
    """Agents configuration container.

    Worker selection:
      - ``dev_workers``:  list of registered names, loaded in dev mode.
      - ``prod_workers``: list of registered names, loaded in production.
      - ``simulation_worker_groups``: mapping of simulated node ID -> worker names.
      - ``demo_active_group_id``: startup-selected demo group ID.
      - ``worker_overrides``: per-worker parameter overrides (merged on top
        of the Python-defined defaults).

    Attributes:
        defaults: Default settings for all agents.
        organizer: Organizer agent config.
        coordinator: Coordinator agent config.
        crewai: CrewAI-specific settings.
        dev_workers: Worker names for dev mode.
        prod_workers: Worker names for production.
        simulation_worker_groups: Simulated node groups keyed by numeric ID.
        demo_active_group_id: Startup-selected demo group ID.
        worker_overrides: Per-worker overrides.
        workers: Legacy worker definitions.
        scenarios: Scenario definitions.
        active_scenario: Active scenario name.
    """
    defaults: AgentDefaultsConfig = field(default_factory=AgentDefaultsConfig)
    organizer: OrganizerConfig = field(default_factory=OrganizerConfig)
    coordinator: CoordinatorConfig = field(default_factory=CoordinatorConfig)
    # CrewAI settings (optional)
    crewai: dict[str, Any] = field(default_factory=dict)
    # Worker name lists (resolved from the Python registry at runtime)
    dev_workers: list[str] = field(default_factory=list)
    prod_workers: list[str] = field(default_factory=list)
    simulation_worker_groups: dict[str, list[str]] = field(default_factory=dict)
    demo_active_group_id: int | None = None
    worker_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Legacy (kept for backward compat but no longer primary)
    workers: list[dict[str, Any]] = field(default_factory=list)
    scenarios: dict[str, Any] = field(default_factory=dict)
    active_scenario: str | None = None

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-like access for backward compatibility."""
        if hasattr(self, key):
            return getattr(self, key)
        return default

    @property
    def demo_active_profile_id(self) -> int | None:
        """Backward-compatible alias for the old config key."""
        return self.demo_active_group_id


# =============================================================================
# LLM Configuration
# =============================================================================

@dataclass
class LLMConfig:
    """LLM configuration container.

    Attributes:
        default_provider: Default provider (e.g. openai, anthropic).
        providers: Provider configs (api_key, base_url, models, etc.).
    """
    default_provider: str = "flock"
    providers: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Tools Configuration
# =============================================================================

@dataclass
class BrowserToolConfig:
    """Browser tool configuration.

    Attributes:
        enabled: Enable browser tool.
        headless: Run browser in headless mode.
        viewport: Viewport size (width, height).
        timeout: Page load timeout (ms).
        user_agent: Custom user agent string.
    """
    enabled: bool = True
    headless: bool = True
    viewport: dict[str, int] = field(default_factory=lambda: {"width": 1280, "height": 720})
    timeout: int = 30000
    user_agent: str | None = None


@dataclass
class SandboxToolConfig:
    """Sandbox tool configuration.

    Attributes:
        enabled: Enable sandbox tool.
        image: Docker image for sandbox.
        timeout: Sandbox timeout (seconds).
        memory_limit: Memory limit (e.g. 512m).
        cpu_limit: CPU limit (e.g. 1.0).
        network_enabled: Enable network in sandbox.
        mount_workspace: Mount workspace into sandbox.
    """
    enabled: bool = True
    image: str = "python:3.11-slim"
    timeout: int = 300
    memory_limit: str = "512m"
    cpu_limit: str = "1.0"
    network_enabled: bool = False
    mount_workspace: bool = False


@dataclass
class SearchToolConfig:
    """Search tool configuration.

    Attributes:
        enabled: Enable search tool.
        provider: Search provider (e.g. google).
        api_key: API key (supports ${ENV_VAR}).
        max_results: Max results per query.
    """
    enabled: bool = True
    provider: str = "google"
    api_key: str = "${SEARCH_API_KEY}"
    max_results: int = 10


@dataclass
class FilesystemToolConfig:
    """Filesystem tool configuration.

    Attributes:
        enabled: Enable filesystem tool.
        allowed_paths: Allowed base paths (~ expands).
        max_file_size: Max file size in bytes.
    """
    enabled: bool = True
    allowed_paths: list[str] = field(default_factory=lambda: ["~/.teaming24/workspace"])
    max_file_size: int = 10485760


@dataclass
class NetworkToolConfig:
    """Network delegation tool configuration.

    Attributes:
        http_timeout: HTTP request timeout (seconds).
        sync_timeout: Sync wait timeout (seconds).
        default_max_cost: Default max cost for delegation.
    """
    http_timeout: float = 120.0
    sync_timeout: int = 120
    default_max_cost: float = 1.0


@dataclass
class OpenHandsToolConfig:
    """OpenHands agent tool configuration.

    Attributes:
        shell_timeout: Shell command timeout (seconds).
        shell_sync_timeout_buffer: Buffer for sync timeout.
        file_read_timeout: File read timeout (seconds).
        file_write_timeout: File write timeout (seconds).
        python_timeout: Python execution timeout (seconds).
        browser_timeout: Browser action timeout (seconds).
    """
    shell_timeout: int = 60
    shell_sync_timeout_buffer: int = 10
    file_read_timeout: int = 30
    file_write_timeout: int = 30
    python_timeout: int = 120
    browser_timeout: int = 60


@dataclass
class ToolsConfig:
    """Tools configuration container.

    Attributes:
        browser: Browser tool config.
        sandbox: Sandbox tool config.
        search: Search tool config.
        filesystem: Filesystem tool config.
        network: Network delegation config.
        openhands_tools: OpenHands tool config.
    """
    browser: BrowserToolConfig = field(default_factory=BrowserToolConfig)
    sandbox: SandboxToolConfig = field(default_factory=SandboxToolConfig)
    search: SearchToolConfig = field(default_factory=SearchToolConfig)
    filesystem: FilesystemToolConfig = field(default_factory=FilesystemToolConfig)
    network: NetworkToolConfig = field(default_factory=NetworkToolConfig)
    openhands_tools: OpenHandsToolConfig = field(default_factory=OpenHandsToolConfig)


# =============================================================================
# Payment Configuration (x402)
# =============================================================================

@dataclass
class PaymentNetworkConfig:
    """Payment network configuration.

    Attributes:
        name: Network name (e.g. base-sepolia).
        rpc_url: RPC URL override.
    """
    name: str = "base-sepolia"
    rpc_url: str | None = None


@dataclass
class PaymentSettingsConfig:
    """Payment settings configuration.

    Attributes:
        scheme: Payment scheme (e.g. exact).
        timeout_seconds: Payment timeout.
        default_asset: Token contract address on Base Sepolia (testnet).
        mainnet_asset: Token contract address on Base mainnet.
    """
    scheme: str = "exact"
    timeout_seconds: int = 600
    default_asset: str = "0x4182528b6660B9c0875c6e94260A2E425F00797f"
    mainnet_asset: str = "0x4182528b6660B9c0875c6e94260A2E425F00797f"


@dataclass
class FacilitatorConfig:
    """Facilitator configuration.

    Attributes:
        url: Facilitator API URL.
        timeout: Request timeout (seconds).
        max_retries: Max retries on failure.
    """
    url: str = "https://x402.org/facilitator"
    timeout: int = 30
    max_retries: int = 3


@dataclass
class MerchantConfig:
    """Merchant configuration.

    Attributes:
        pay_to_address: Address to receive payments.
        default_description: Default payment description.
    """
    pay_to_address: str | None = None
    default_description: str = "Payment required for this service"


@dataclass
class WalletConfig:
    """Wallet configuration.

    Attributes:
        valid_hours: How long wallet credentials are valid (hours).
    """
    valid_hours: float = 1.0


@dataclass
class MockPaymentConfig:
    """Mock payment configuration.

    Attributes:
        always_valid: Always accept mock payments.
        always_settled: Always treat as settled.
        initial_balance: Starting USDC balance in mock mode.
    """
    always_valid: bool = True
    always_settled: bool = True
    initial_balance: float = 100.0


@dataclass
class PaymentConfig:
    """Payment configuration container.

    Attributes:
        enabled: Enable payment (x402).
        mode: Payment mode (mock, testnet, mainnet).
        task_price: Default price per task.
        token_symbol: Token symbol used for payments (e.g. "ETH", "USDC").
        network: Network settings.
        settings: Payment settings.
        facilitator: Facilitator settings.
        merchant: Merchant settings.
        wallet: Wallet settings.
        mock: Mock payment settings.
    """
    enabled: bool = False
    mode: str = "mock"
    task_price: str = "0.001"
    token_symbol: str = "ETH"
    network: PaymentNetworkConfig = field(default_factory=PaymentNetworkConfig)
    settings: PaymentSettingsConfig = field(default_factory=PaymentSettingsConfig)
    facilitator: FacilitatorConfig = field(default_factory=FacilitatorConfig)
    merchant: MerchantConfig = field(default_factory=MerchantConfig)
    wallet: WalletConfig = field(default_factory=WalletConfig)
    mock: MockPaymentConfig = field(default_factory=MockPaymentConfig)


# =============================================================================
# Runtime Configuration
# =============================================================================

@dataclass
class SandboxPoolConfig:
    """Sandbox pool configuration.

    Attributes:
        min_size: Min sandboxes to keep warm.
        max_size: Max sandboxes in pool.
        idle_timeout: Idle timeout before recycle (seconds).
        creation_timeout: Timeout for creating sandbox (seconds).
    """
    min_size: int = 0
    max_size: int = 10
    idle_timeout: int = 300
    creation_timeout: float = 60.0


@dataclass
class SandboxRuntimeConfig:
    """Sandbox runtime defaults.

    Attributes:
        docker_image: Docker image for sandbox.
        max_memory_mb: Max memory per sandbox (MB).
        default_timeout: Default execution timeout (seconds).
        api_url: Sandbox API URL.
        ready_timeout: Wait for sandbox ready (seconds).
        shm_size: Shared memory size.
        api_ready_timeout: API ready check timeout.
        api_ready_check_interval: API ready check interval.
        health_check_timeout: Health check timeout.
        stop_timeout: Stop timeout (seconds).
        enable_browser: Enable browser in sandbox.
        enable_vnc: Enable VNC for debugging.
        extra_env: Extra env vars passed to sandbox container (e.g. UVICORN_ACCESS_LOG=false).
    """
    docker_image: str = "ghcr.io/agent-infra/sandbox:latest"
    max_memory_mb: int = 2048
    default_timeout: float = 300.0
    api_url: str = "http://localhost:8080"
    ready_timeout: float = 30.0
    shm_size: str = "512m"
    api_ready_timeout: float = 30.0
    api_ready_check_interval: float = 0.5
    health_check_timeout: float = 2.0
    stop_timeout: int = 3
    enable_browser: bool = True
    enable_vnc: bool = False
    extra_env: dict[str, str] = field(default_factory=dict)


@dataclass
class ExecutionConfig:
    """Execution configuration.

    Attributes:
        max_concurrent_tasks: Max concurrent task executions.
        task_timeout: Task timeout (seconds).
        checkpoint_interval: Checkpoint interval (seconds).
    """
    max_concurrent_tasks: int = 5
    task_timeout: int = 600
    checkpoint_interval: int = 60


@dataclass
class MemoryConfig:
    """Memory/context configuration.

    Attributes:
        enabled: Enable durable local agent memory recall/persistence.
        backend: Memory backend (local, etc.).
        max_context_length: Max context length (tokens).
        respect_context_window: Enforce context-window trimming.
        compression_enabled: Enable context compression.
        chat_context_message_preview: Max chars retained per chat-history message.
        chat_context_token_reserve: Minimum token headroom reserved for responses.
        agent_recall_max_chars: Max chars injected from durable memory recall.
        agent_recall_top_k: Max durable memory entries recalled per task.
        agent_summary_trigger_chars: Output size threshold before semantic summarization.
        agent_summary_max_chars: Max chars stored in semantic task/chat memory summaries.
        api_search_top_k_max: Upper bound for /api/memory/search top_k.
        api_recent_limit_max: Upper bound for /api/memory/recent limit.
        persistent_max_chars: Per-agent durable memory budget before compaction.
        persistent_recent_keep_chars: Target recent durable memory kept uncompressed.
        persistent_min_recent_entries: Minimum recent durable entries kept uncompressed.
        persistent_summary_max_chars: Max chars in a compaction summary entry.
        persistent_summary_line_chars: Max chars per line included in a compaction summary.
        persistent_compaction_max_passes: Safety cap for repeated compaction attempts.
    """
    enabled: bool = True
    backend: str = "local"
    max_context_length: int = 100000
    respect_context_window: bool = True
    compression_enabled: bool = True
    chat_context_message_preview: int = 24000
    chat_context_token_reserve: int = 4096
    agent_recall_max_chars: int = 8000
    agent_recall_top_k: int = 5
    agent_summary_trigger_chars: int = 4000
    agent_summary_max_chars: int = 2400
    api_search_top_k_max: int = 50
    api_recent_limit_max: int = 100
    persistent_max_chars: int = 200000
    persistent_recent_keep_chars: int = 140000
    persistent_min_recent_entries: int = 8
    persistent_summary_max_chars: int = 16000
    persistent_summary_line_chars: int = 320
    persistent_compaction_max_passes: int = 4


@dataclass
class OpenHandsRuntimeConfig:
    """OpenHands runtime configuration.

    Attributes:
        enabled: Enable OpenHands runtime.
        runtime_type: Runtime type (docker, etc.).
        container_image: Container image.
        workspace_path: Workspace path in container.
        timeout: Execution timeout (seconds).
        enable_auto_lint: Enable auto linting.
        enable_jupyter: Enable Jupyter.
        headless_mode: Run in headless mode.
        env_vars: Environment variables for container.
    """
    enabled: bool = False
    runtime_type: str = "docker"
    container_image: str = "ghcr.io/openhands/agent-server:latest-python"
    workspace_path: str = "/workspace"
    timeout: int = 120
    enable_auto_lint: bool = True
    enable_jupyter: bool = True
    headless_mode: bool = True
    env_vars: dict[str, str] = field(default_factory=dict)


@dataclass
class RuntimeConfig:
    """Runtime configuration container.

    Attributes:
        default: Default runtime (openhands, sandbox, local).
        sandbox_pool: Sandbox pool settings.
        sandbox: Sandbox runtime settings.
        execution: Execution settings.
        memory: Memory/context settings.
        openhands: OpenHands runtime settings.
    """
    default: str = "openhands"
    sandbox_pool: SandboxPoolConfig = field(default_factory=SandboxPoolConfig)
    sandbox: SandboxRuntimeConfig = field(default_factory=SandboxRuntimeConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    openhands: OpenHandsRuntimeConfig = field(default_factory=OpenHandsRuntimeConfig)

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-like access for backward compatibility."""
        if hasattr(self, key):
            return getattr(self, key)
        return default


# =============================================================================
# AN Router Configuration
# =============================================================================

@dataclass
class ANRouterConfig:
    """Configuration for the AN Router (task-to-pool-member routing).

    The pool consists of the **local team coordinator** and all connected
    **Remote Agentic Nodes (ANs)**.  The AN Router decides how many
    and which pool members handle each task.

    Attributes:
        strategy: Routing strategy (organizer_llm, etc.).
        model: LLM model for routing decisions.
        routing_temperature: Temperature for routing LLM call.
        routing_max_tokens: Max tokens for routing response.
        min_pool_members: Min pool members to involve.
        prefer_remote: Prefer remote ANs over local.
        capability_match_threshold: Min capability match score.
        max_delegation_depth: Max delegation depth (loop prevention).
        remote_submit_timeout: Remote task submit timeout (seconds).
        remote_sse_timeout: Remote SSE stream timeout (seconds).
        remote_poll_interval: Poll interval for remote status (seconds).
        remote_poll_timeout: Total poll timeout (seconds).
        remote_poll_http_timeout: HTTP timeout per poll (seconds).
        remote_http_connect_timeout: HTTP connect timeout.
        remote_http_write_timeout: HTTP write timeout.
        remote_http_pool_timeout: HTTP pool timeout.
    """
    strategy: str = "organizer_llm"
    model: str = "flock/gpt-5.2"
    # LLM parameters for the routing call
    routing_temperature: float = 0.1
    routing_max_tokens: int = 1000
    # Pool member selection
    min_pool_members: int = 2
    prefer_remote: bool = False
    capability_match_threshold: float = 0.3
    # Delegation loop prevention
    max_delegation_depth: int = 5
    # Remote task dispatch timeouts (seconds)
    remote_submit_timeout: float = 30.0
    remote_sse_timeout: float = 900.0
    remote_poll_interval: float = 5.0
    remote_poll_timeout: float = 900.0
    remote_poll_http_timeout: float = 15.0
    # HTTP connection timeouts (seconds)
    remote_http_connect_timeout: float = 10.0
    remote_http_write_timeout: float = 10.0
    remote_http_pool_timeout: float = 10.0


@dataclass
class LocalAgentRouterConfig:
    """Configuration for the Local Agent Router (Worker selection from Local Agent Pool)."""
    model: str = "flock/gpt-5.2"
    routing_temperature: float = 0.2
    routing_max_tokens: int = 1000


# =============================================================================
# Output Configuration
# =============================================================================

@dataclass
class OutputConfig:
    """Task output storage configuration.

    All task results — local and remote — are saved under ``base_dir``
    in per-task subdirectories (``{base_dir}/{task_id}/``).

    The structure for each task folder:
      {task_id}/
        manifest.json   — metadata, file list, timing
        result.txt      — raw aggregated result text
        local/          — files extracted from local crew execution
        remote/         — results received from remote ANs
          {an_name}/    — one subfolder per remote AN
            result.txt  — raw result from that AN
            *.py / ...  — extracted code files

    Attributes:
        base_dir: Base directory for task outputs (~ expands).
        cleanup_max_age_days: Auto-delete outputs older than N days (0=never).
        save_remote_results: Save remote AN results into task folder.
        filename_max_chars: Max filename length; longer names truncated.
        result_preview_max_chars: Max chars for result preview in manifest.json.
    """
    # Base directory for all task outputs.
    # Supports ~ for home directory expansion.
    # Override: TEAMING24_OUTPUT_DIR env var.
    base_dir: str = "~/.teaming24/outputs"

    # Auto-delete outputs older than this many days (0 = never).
    cleanup_max_age_days: int = 30

    # Whether to save remote AN results into the task folder.
    save_remote_results: bool = True

    # Max filename length (chars). Longer names are truncated.
    filename_max_chars: int = 200

    # Max chars for result preview in manifest.json.
    result_preview_max_chars: int = 200


# =============================================================================
# Framework Configuration
# =============================================================================

@dataclass
class NativeFrameworkConfig:
    """Native runtime configuration.

    Attributes:
        max_iterations: Max agent loop iterations.
        planning_model: Model for planning/reasoning.
    """
    max_iterations: int = 40
    planning_model: str = "flock/gpt-5.2"


@dataclass
class CrewAIFrameworkConfig:
    """CrewAI adapter configuration.

    Attributes:
        verbose: Verbose CrewAI output.
        memory: Enable CrewAI memory.
        planning: Enable CrewAI planning.
        planning_llm: Model for CrewAI planning.
    """
    verbose: bool = False
    memory: bool = False
    planning: bool = False
    planning_llm: str = "flock/gpt-5.2"


@dataclass
class FrameworkConfig:
    """Multi-agent framework selection and settings.

    ``backend`` selects which adapter LocalCrew uses for local execution:
      - ``"native"`` — teaming24's own agentic loop (litellm + tool calling).
      - ``"crewai"`` — CrewAI Crew.kickoff() (requires ``crewai`` package).

    Attributes:
        backend: Framework backend (native, crewai).
        native: Native framework settings.
        crewai: CrewAI framework settings.
    """
    backend: str = "native"
    native: NativeFrameworkConfig = field(default_factory=NativeFrameworkConfig)
    crewai: CrewAIFrameworkConfig = field(default_factory=CrewAIFrameworkConfig)


# =============================================================================
# Channel & Binding Configuration
# =============================================================================

@dataclass
class ChannelAccountConfig:
    """Single account within a channel (e.g. one Telegram bot).

    Attributes:
        bot_token: Bot token (Telegram, etc.).
        app_token: App token (Slack, etc.).
        token: Generic token fallback.
    """
    bot_token: str = ""
    app_token: str = ""
    token: str = ""


@dataclass
class ChannelConfig:
    """Configuration for one messaging channel type.

    Attributes:
        enabled: Enable this channel.
        accounts: Account ID -> config mapping.
    """
    enabled: bool = False
    accounts: dict[str, ChannelAccountConfig] = field(default_factory=dict)


@dataclass
class ChannelsConfig:
    """All messaging channels.

    Attributes:
        telegram: Telegram channel config.
        slack: Slack channel config.
        discord: Discord channel config.
        webchat: WebChat channel config (GUI/internal).
    """
    telegram: ChannelConfig = field(default_factory=ChannelConfig)
    slack: ChannelConfig = field(default_factory=ChannelConfig)
    discord: ChannelConfig = field(default_factory=ChannelConfig)
    webchat: ChannelConfig = field(default_factory=lambda: ChannelConfig(enabled=True))


@dataclass
class PeerMatchConfig:
    """Peer matching criteria for a binding.

    Attributes:
        kind: Peer kind (user, bot, etc.).
        id: Peer identifier.
    """
    kind: str = ""
    id: str = ""


@dataclass
class BindingMatchConfig:
    """Match criteria for routing inbound messages.

    Attributes:
        channel: Channel name (telegram, slack, etc.).
        account_id: Account ID within channel.
        peer: Optional peer match criteria.
    """
    channel: str = ""
    account_id: str = ""
    peer: PeerMatchConfig | None = None


@dataclass
class BindingConfig:
    """Route inbound messages from a channel to an agent.

    Attributes:
        agent_id: Target agent ID.
        match: Match criteria for this binding.
    """
    agent_id: str = "main"
    match: BindingMatchConfig = field(default_factory=BindingMatchConfig)


# =============================================================================
# Session Configuration
# =============================================================================

@dataclass
class SessionConfig:
    """Conversation session management.

    ``dm_scope`` controls how direct messages are grouped:
      - ``"main"``             — all DMs share one session (default).
      - ``"per-peer"``         — one session per sender across channels.
      - ``"per-channel-peer"`` — one session per (channel, sender).

    Attributes:
        dm_scope: DM scope (main, per-peer, per-channel-peer).
        idle_minutes: Minutes before session considered idle.
        idle_timeout_s: Legacy alias for ``idle_minutes`` in seconds.
        max_history: Maximum messages retained per session (0 = unlimited).
        store_path: SQLite path for session store.
        reset_triggers: Commands that reset session (e.g. /new, /reset).
    """
    dm_scope: str = "per-channel-peer"
    idle_minutes: int = 120
    idle_timeout_s: int | None = None
    max_history: int = 200
    store_path: str = "~/.teaming24/sessions.db"
    reset_triggers: list[str] = field(default_factory=lambda: ["/new", "/reset"])


@dataclass
class SchedulerJobConfig:
    """A pre-defined scheduled job.

    Attributes:
        name: Job name.
        prompt: Task prompt to run.
        cron: Cron expression (if cron-based).
        interval_seconds: Interval in seconds (if interval-based).
        agent_id: Agent to run job.
    """
    name: str = ""
    prompt: str = ""
    cron: str = ""
    interval_seconds: int = 0
    agent_id: str = "main"


@dataclass
class SchedulerConfig:
    """Cron/scheduled task execution settings.

    Attributes:
        auto_start: Start scheduler on startup.
        jobs: List of scheduled jobs.
    """
    auto_start: bool = False
    jobs: list[SchedulerJobConfig] = field(default_factory=list)



# =============================================================================
# Main Configuration
# =============================================================================

@dataclass
class Config:
    """Main configuration container.

    All settings come from teaming24.yaml - the single source of truth.

    Attributes:
        system: System config (server, api, database, etc.).
        security: Security config (jwt, api_keys, rate_limit, etc.).
        network: Network config (local_node, discovery, etc.).
        agents: Agents config (organizer, coordinator, workers).
        llm: LLM config (providers, models).
        tools: Tools config (browser, sandbox, search, etc.).
        payment: Payment config (x402).
        runtime: Runtime config (sandbox, execution).
        an_router: AN Router config.
        local_agent_router: Local Agent Router config (Worker selection).
        output: Output config (task results).
        framework: Framework config (native, crewai).
        channels: Channels config (telegram, slack, discord).
        bindings: Message bindings (channel -> agent).
        session: Session config.
        scheduler: Scheduler config.
        extensions: Extension configs.
    """
    system: SystemConfig = field(default_factory=SystemConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    payment: PaymentConfig = field(default_factory=PaymentConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    an_router: ANRouterConfig = field(default_factory=ANRouterConfig)
    local_agent_router: LocalAgentRouterConfig = field(default_factory=LocalAgentRouterConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    framework: FrameworkConfig = field(default_factory=FrameworkConfig)
    channels: ChannelsConfig = field(default_factory=ChannelsConfig)
    bindings: list[BindingConfig] = field(default_factory=list)
    session: SessionConfig = field(default_factory=SessionConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    extensions: dict[str, Any] = field(default_factory=dict)

    # -------------------------------------------------------------------------
    # Convenience Properties (backward compatibility)
    # -------------------------------------------------------------------------

    @property
    def server(self) -> ServerConfig:
        """Shortcut to system.server."""
        return self.system.server

    @property
    def api(self) -> ApiConfig:
        """Shortcut to system.api."""
        return self.system.api

    @property
    def frontend(self) -> FrontendConfig:
        """Shortcut to system.frontend."""
        return self.system.frontend

    @property
    def cors(self) -> CorsConfig:
        """Shortcut to system.cors."""
        return self.system.cors

    @property
    def database(self) -> DatabaseConfig:
        """Shortcut to system.database."""
        return self.system.database

    @property
    def logging(self) -> LoggingConfig:
        """Shortcut to system.logging."""
        return self.system.logging

    @property
    def local_node(self) -> LocalNodeConfig:
        """Shortcut to network.local_node."""
        return self.network.local_node

    @property
    def discovery(self) -> DiscoveryConfig:
        """Shortcut to network.discovery."""
        return self.network.discovery

    @property
    def connection(self) -> ConnectionConfig:
        """Shortcut to network.connection."""
        return self.network.connection

    @property
    def messaging(self) -> MessagingConfig:
        """Shortcut to network.messaging."""
        return self.network.messaging

    @property
    def subscription(self) -> SubscriptionConfig:
        """Shortcut to network.subscription."""
        return self.network.subscription

    @property
    def marketplace(self) -> MarketplaceConfig:
        """Shortcut to network.marketplace."""
        return self.network.marketplace

    @property
    def agentanet_central(self) -> AgentaNetCentralConfig:
        """Shortcut to network.agentanet_central."""
        return self.network.agentanet_central

    @property
    def remote_nodes(self) -> list[dict[str, Any]]:
        """Shortcut to network.remote_nodes."""
        return self.network.remote_nodes

    @property
    def memory(self) -> MemoryConfig:
        """Shortcut to runtime.memory."""
        return self.runtime.memory

    # Legacy compatibility for config.agentanet.local_node pattern
    @property
    def agentanet(self):
        """Legacy compatibility - returns network config."""
        return self.network

    def to_dict(self) -> dict[str, Any]:
        """Convert config to dictionary for API export."""
        return {
            "system": {
                "server": {
                    "host": self.system.server.host,
                    "port": self.system.server.port,
                    "workers": self.system.server.workers,
                },
                "api": {
                    "base_url": self.system.api.base_url,
                    "prefix": self.system.api.prefix,
                    "docs_enabled": self.system.api.docs_enabled,
                    "max_demo_poll_iterations": self.system.api.max_demo_poll_iterations,
                    "step_content_max_chars": self.system.api.step_content_max_chars,
                    "step_thought_max_chars": self.system.api.step_thought_max_chars,
                    "step_observation_max_chars": self.system.api.step_observation_max_chars,
                    "max_execution_rounds": self.system.api.max_execution_rounds,
                    "quality_gate_enabled": self.system.api.quality_gate_enabled,
                    "quality_benchmark_profile": self.system.api.quality_benchmark_profile,
                    "quality_verifier_enabled": self.system.api.quality_verifier_enabled,
                    "quality_verifier_model": self.system.api.quality_verifier_model,
                    "quality_confidence_threshold": self.system.api.quality_confidence_threshold,
                    "quality_auto_fallback_low_confidence": self.system.api.quality_auto_fallback_low_confidence,
                    "quality_task_class_policies": self.system.api.quality_task_class_policies,
                },
                "frontend": {
                    "dev_host": self.system.frontend.dev_host,
                    "dev_port": self.system.frontend.dev_port,
                },
                "database": {
                    "path": self.system.database.path,
                },
                "logging": {
                    "level": self.system.logging.level,
                },
                "dev_mode": {
                    "enabled": self.system.dev_mode.enabled,
                },
                "task_manager": {
                    "max_tasks_in_memory": self.system.task_manager.max_tasks_in_memory,
                    "task_expiry_seconds": self.system.task_manager.task_expiry_seconds,
                    "cleanup_interval_seconds": self.system.task_manager.cleanup_interval_seconds,
                    "list_tasks_default_limit": self.system.task_manager.list_tasks_default_limit,
                    "phase_percentages": self.system.task_manager.phase_percentages,
                },
            },
            "network": {
                "local_node": {
                    "an_id": self.network.local_node.an_id,
                    "name": self.network.local_node.name,
                    "wallet_address": self.network.local_node.wallet_address,
                    "host": self.network.local_node.host,
                    "port": self.network.local_node.port,
                    "description": self.network.local_node.description,
                    "capability": self.network.local_node.capability,
                    "region": self.network.local_node.region,
                },
                "discovery": {
                    "enabled": self.network.discovery.enabled,
                    "broadcast_enabled": self.network.discovery.broadcast_enabled,
                    "broadcast_port": self.network.discovery.broadcast_port,
                    "broadcast_interval": self.network.discovery.broadcast_interval,
                    "node_expiry_seconds": self.network.discovery.node_expiry_seconds,
                    "cleanup_interval": self.network.discovery.cleanup_interval,
                    "udp_receive_timeout": self.network.discovery.udp_receive_timeout,
                    "udp_recv_buffer_size": self.network.discovery.udp_recv_buffer_size,
                    "udp_payload_target_bytes": self.network.discovery.udp_payload_target_bytes,
                    "discover_dedupe_window_s": self.network.discovery.discover_dedupe_window_s,
                    "broadcast_initial_delay": self.network.discovery.broadcast_initial_delay,
                    "broadcast_error_delay": self.network.discovery.broadcast_error_delay,
                    "max_lan_nodes": self.network.discovery.max_lan_nodes,
                    "max_wan_nodes": self.network.discovery.max_wan_nodes,
                },
                "connection": {
                    "timeout": self.network.connection.timeout,
                    "retry_attempts": self.network.connection.retry_attempts,
                    "keepalive_interval": self.network.connection.keepalive_interval,
                },
                "subscription": {
                    "max_queue_size": self.network.subscription.max_queue_size,
                    "keepalive_interval": self.network.subscription.keepalive_interval,
                },
                "marketplace": {
                    "url": self.network.marketplace.url,
                    "auto_rejoin": self.network.marketplace.auto_rejoin,
                },
                "agentanet_central": {
                    "url": self.network.agentanet_central.url,
                    "token": self.network.agentanet_central.token,
                    "heartbeat_interval": self.network.agentanet_central.heartbeat_interval,
                    "enabled": self.network.agentanet_central.enabled,
                    "register_timeout": self.network.agentanet_central.register_timeout,
                    "heartbeat_http_timeout": self.network.agentanet_central.heartbeat_http_timeout,
                    "search_timeout": self.network.agentanet_central.search_timeout,
                    "search_page_size": self.network.agentanet_central.search_page_size,
                    "search_max_pages": self.network.agentanet_central.search_max_pages,
                    "get_node_timeout": self.network.agentanet_central.get_node_timeout,
                    "marketplace_cache_ttl": self.network.agentanet_central.marketplace_cache_ttl,
                },
                "remote_nodes": self.network.remote_nodes,
            },
            "agents": {
                "defaults": {
                    "model": self.agents.defaults.model,
                    "temperature": self.agents.defaults.temperature,
                    "max_tokens": self.agents.defaults.max_tokens,
                },
                "organizer": {
                    "enabled": self.agents.organizer.enabled,
                    "model": self.agents.organizer.model,
                },
                "coordinator": {
                    "enabled": self.agents.coordinator.enabled,
                    "model": self.agents.coordinator.model,
                },
                "dev_workers": self.agents.dev_workers,
                "prod_workers": self.agents.prod_workers,
                "simulation_worker_groups": self.agents.simulation_worker_groups,
                "demo_active_group_id": self.agents.demo_active_group_id,
                "worker_overrides": self.agents.worker_overrides,
            },
            "llm": {
                "default_provider": self.llm.default_provider,
                "providers": self.llm.providers,
            },
            "tools": {
                "browser": {"enabled": self.tools.browser.enabled},
                "sandbox": {"enabled": self.tools.sandbox.enabled},
                "search": {"enabled": self.tools.search.enabled},
            },
            "payment": {
                "enabled": self.payment.enabled,
                "mode": self.payment.mode,
                "task_price": self.payment.task_price,
            },
            "runtime": {
                "sandbox_pool": {
                    "max_size": self.runtime.sandbox_pool.max_size,
                },
                "execution": {
                    "max_concurrent_tasks": self.runtime.execution.max_concurrent_tasks,
                },
                "memory": {
                    "enabled": self.runtime.memory.enabled,
                    "backend": self.runtime.memory.backend,
                    "max_context_length": self.runtime.memory.max_context_length,
                    "respect_context_window": self.runtime.memory.respect_context_window,
                    "compression_enabled": self.runtime.memory.compression_enabled,
                    "chat_context_message_preview": self.runtime.memory.chat_context_message_preview,
                    "chat_context_token_reserve": self.runtime.memory.chat_context_token_reserve,
                    "agent_recall_max_chars": self.runtime.memory.agent_recall_max_chars,
                    "agent_recall_top_k": self.runtime.memory.agent_recall_top_k,
                    "agent_summary_trigger_chars": self.runtime.memory.agent_summary_trigger_chars,
                    "agent_summary_max_chars": self.runtime.memory.agent_summary_max_chars,
                    "api_search_top_k_max": self.runtime.memory.api_search_top_k_max,
                    "api_recent_limit_max": self.runtime.memory.api_recent_limit_max,
                    "persistent_max_chars": self.runtime.memory.persistent_max_chars,
                    "persistent_recent_keep_chars": self.runtime.memory.persistent_recent_keep_chars,
                    "persistent_min_recent_entries": self.runtime.memory.persistent_min_recent_entries,
                    "persistent_summary_max_chars": self.runtime.memory.persistent_summary_max_chars,
                    "persistent_summary_line_chars": self.runtime.memory.persistent_summary_line_chars,
                    "persistent_compaction_max_passes": self.runtime.memory.persistent_compaction_max_passes,
                },
            },
            "framework": {
                "backend": self.framework.backend,
            },
            "an_router": {
                "strategy": self.an_router.strategy,
                "model": self.an_router.model,
                "min_pool_members": self.an_router.min_pool_members,
                "prefer_remote": self.an_router.prefer_remote,
                "routing_temperature": self.an_router.routing_temperature,
                "routing_max_tokens": self.an_router.routing_max_tokens,
            },
            "channels": {
                "telegram": {"enabled": self.channels.telegram.enabled},
                "slack": {"enabled": self.channels.slack.enabled},
                "discord": {"enabled": self.channels.discord.enabled},
                "webchat": {"enabled": self.channels.webchat.enabled},
            },
            "bindings": [
                {
                    "agent_id": b.agent_id,
                    "match": {
                        "channel": b.match.channel,
                        "account_id": b.match.account_id,
                        "peer": (
                            {"kind": b.match.peer.kind, "id": b.match.peer.id}
                            if b.match.peer else None
                        ),
                    },
                }
                for b in self.bindings
            ],
            "session": {
                "dm_scope": self.session.dm_scope,
                "idle_minutes": self.session.idle_minutes,
                "idle_timeout_s": self.session.idle_timeout_s,
                "max_history": self.session.max_history,
                "store_path": self.session.store_path,
                "reset_triggers": self.session.reset_triggers,
            },
            "scheduler": {
                "auto_start": self.scheduler.auto_start,
                "jobs": [
                    {
                        "name": j.name,
                        "prompt": j.prompt,
                        "cron": j.cron,
                        "interval_seconds": j.interval_seconds,
                        "agent_id": j.agent_id,
                    }
                    for j in self.scheduler.jobs
                ],
            },
        }


# =============================================================================
# Config Loading Helpers
# =============================================================================

def _dict_to_dataclass(cls, data: dict[str, Any]):
    """Convert a dictionary to a dataclass instance."""
    if data is None:
        return cls()

    field_types = {f.name: f.type for f in cls.__dataclass_fields__.values()}
    kwargs = {}

    for key, value in data.items():
        if key in field_types:
            field_type = field_types[key]
            # Handle nested dataclasses
            if hasattr(field_type, '__dataclass_fields__') and isinstance(value, dict):
                kwargs[key] = _dict_to_dataclass(field_type, value)
            else:
                kwargs[key] = value

    return cls(**kwargs)


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """Load a YAML file safely."""
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"Failed to load {path}: {e}")
        return {}


_ENV_REF_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _resolve_env_placeholders(value: Any) -> Any:
    """Recursively resolve ${ENV_VAR} placeholders in config values."""
    if isinstance(value, dict):
        return {k: _resolve_env_placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_placeholders(v) for v in value]
    if isinstance(value, str):
        def _replace(match: re.Match) -> str:
            env_key = match.group(1)
            env_val = os.getenv(env_key)
            return env_val if env_val is not None else match.group(0)

        return _ENV_REF_PATTERN.sub(_replace, value)
    return value


def _build_config_from_dict(data: dict[str, Any]) -> Config:
    """Build Config object from dictionary."""
    # System config
    system_data = data.get("system", {})
    system_config = SystemConfig(
        server=_dict_to_dataclass(ServerConfig, system_data.get("server", {})),
        api=_dict_to_dataclass(ApiConfig, system_data.get("api", {})),
        frontend=_dict_to_dataclass(FrontendConfig, system_data.get("frontend", {})),
        cors=_dict_to_dataclass(CorsConfig, system_data.get("cors", {})),
        database=_dict_to_dataclass(DatabaseConfig, system_data.get("database", {})),
        logging=_dict_to_dataclass(LoggingConfig, system_data.get("logging", {})),
        dev_mode=_dict_to_dataclass(DevModeConfig, system_data.get("dev_mode", {})),
        task_manager=_dict_to_dataclass(TaskManagerConfig, system_data.get("task_manager", {})),
    )

    # Security config
    security_data = data.get("security", {})
    connection_password = security_data.get("connection_password")
    if connection_password is None and security_data.get("local_password") is not None:
        connection_password = security_data.get("local_password")
        logger.warning(
            "security.local_password is deprecated; use security.connection_password"
        )
    security_config = SecurityConfig(
        jwt=_dict_to_dataclass(JwtConfig, security_data.get("jwt", {})),
        api_keys=_dict_to_dataclass(ApiKeyConfig, security_data.get("api_keys", {})),
        rate_limit=_dict_to_dataclass(RateLimitConfig, security_data.get("rate_limit", {})),
        encryption=_dict_to_dataclass(EncryptionConfig, security_data.get("encryption", {})),
        connection_password=connection_password,
    )

    # Network config
    network_data = data.get("network", {})
    network_config = NetworkConfig(
        local_node=_dict_to_dataclass(LocalNodeConfig, network_data.get("local_node", {})),
        discovery=_dict_to_dataclass(DiscoveryConfig, network_data.get("discovery", {})),
        connection=_dict_to_dataclass(ConnectionConfig, network_data.get("connection", {})),
        messaging=_dict_to_dataclass(MessagingConfig, network_data.get("messaging", {})),
        subscription=_dict_to_dataclass(SubscriptionConfig, network_data.get("subscription", {})),
        marketplace=_dict_to_dataclass(MarketplaceConfig, network_data.get("marketplace", {})),
        agentanet_central=_dict_to_dataclass(AgentaNetCentralConfig, network_data.get("agentanet_central", {})),
        remote_nodes=network_data.get("remote_nodes", []),
    )

    # Agents config
    agents_data = data.get("agents", {})
    agents_config = AgentsConfig(
        defaults=_dict_to_dataclass(AgentDefaultsConfig, agents_data.get("defaults", {})),
        organizer=_dict_to_dataclass(OrganizerConfig, agents_data.get("organizer", {})),
        coordinator=_dict_to_dataclass(CoordinatorConfig, agents_data.get("coordinator", {})),
        crewai=agents_data.get("crewai", {}),
        dev_workers=agents_data.get("dev_workers", []),
        prod_workers=agents_data.get("prod_workers", []),
        simulation_worker_groups=agents_data.get("simulation_worker_groups", {}),
        demo_active_group_id=agents_data.get(
            "demo_active_group_id",
            agents_data.get("demo_active_profile_id"),
        ),
        worker_overrides=agents_data.get("worker_overrides", {}),
        # Legacy fields (backward compat)
        workers=agents_data.get("workers", []),
        scenarios=agents_data.get("scenarios", {}),
        active_scenario=agents_data.get("active_scenario"),
    )

    # LLM config
    llm_data = data.get("llm", {})
    llm_config = LLMConfig(
        default_provider=llm_data.get("default_provider", "flock"),
        providers=llm_data.get("providers", {}),
    )

    # Tools config
    tools_data = data.get("tools", {})
    tools_config = ToolsConfig(
        browser=_dict_to_dataclass(BrowserToolConfig, tools_data.get("browser", {})),
        sandbox=_dict_to_dataclass(SandboxToolConfig, tools_data.get("sandbox", {})),
        search=_dict_to_dataclass(SearchToolConfig, tools_data.get("search", {})),
        filesystem=_dict_to_dataclass(FilesystemToolConfig, tools_data.get("filesystem", {})),
        network=_dict_to_dataclass(NetworkToolConfig, tools_data.get("network", {})),
        openhands_tools=_dict_to_dataclass(OpenHandsToolConfig, tools_data.get("openhands_tools", {})),
    )

    # Payment config
    payment_data = data.get("payment", {})
    payment_config = PaymentConfig(
        enabled=payment_data.get("enabled", False),
        mode=payment_data.get("mode", "mock"),
        task_price=payment_data.get("task_price", "0.001"),
        network=_dict_to_dataclass(PaymentNetworkConfig, payment_data.get("network", {})),
        settings=_dict_to_dataclass(PaymentSettingsConfig, payment_data.get("settings", {})),
        facilitator=_dict_to_dataclass(FacilitatorConfig, payment_data.get("facilitator", {})),
        merchant=_dict_to_dataclass(MerchantConfig, payment_data.get("merchant", {})),
        wallet=_dict_to_dataclass(WalletConfig, payment_data.get("wallet", {})),
        mock=_dict_to_dataclass(MockPaymentConfig, payment_data.get("mock", {})),
    )

    # Runtime config
    runtime_data = data.get("runtime", {})
    runtime_config = RuntimeConfig(
        default=runtime_data.get("default", "openhands"),
        sandbox_pool=_dict_to_dataclass(SandboxPoolConfig, runtime_data.get("sandbox_pool", {})),
        sandbox=_dict_to_dataclass(SandboxRuntimeConfig, runtime_data.get("sandbox", {})),
        execution=_dict_to_dataclass(ExecutionConfig, runtime_data.get("execution", {})),
        memory=_dict_to_dataclass(MemoryConfig, runtime_data.get("memory", {})),
        openhands=_dict_to_dataclass(OpenHandsRuntimeConfig, runtime_data.get("openhands", {})),
    )

    # AN Router config
    an_router_config = _dict_to_dataclass(
        ANRouterConfig, data.get("an_router", {})
    )

    # Local Agent Router config
    local_agent_router_config = _dict_to_dataclass(
        LocalAgentRouterConfig, data.get("local_agent_router", {})
    )

    # Output config
    output_config = _dict_to_dataclass(
        OutputConfig, data.get("output", {})
    )

    # Framework config
    fw_data = data.get("framework", {})
    framework_config = FrameworkConfig(
        backend=fw_data.get("backend", "native"),
        native=_dict_to_dataclass(NativeFrameworkConfig, fw_data.get("native", {})),
        crewai=_dict_to_dataclass(CrewAIFrameworkConfig, fw_data.get("crewai", {})),
    )

    # Channels config
    ch_data = data.get("channels", {})
    channels_config = ChannelsConfig(
        telegram=_dict_to_dataclass(ChannelConfig, ch_data.get("telegram", {})),
        slack=_dict_to_dataclass(ChannelConfig, ch_data.get("slack", {})),
        discord=_dict_to_dataclass(ChannelConfig, ch_data.get("discord", {})),
        webchat=_dict_to_dataclass(ChannelConfig, ch_data.get("webchat", {"enabled": True})),
    )
    if not channels_config.webchat.enabled:
        logger.warning(
            "channels.webchat.enabled=false is not supported; forcing webchat enabled"
        )
        channels_config.webchat.enabled = True

    # Bindings config
    bindings_raw = data.get("bindings", [])
    bindings_config: list[BindingConfig] = []
    for b in (bindings_raw or []):
        if not isinstance(b, dict):
            logger.warning("Ignoring non-dict binding config: %r", b)
            continue

        # Preferred shape:
        #   - agent_id: ...
        #     match: {channel, account_id, peer}
        # Legacy/doc shape (still supported):
        #   - channel: ...
        #     account_id: ...
        #     peer: "12345" | {kind, id}
        raw_match = b.get("match")
        if isinstance(raw_match, dict):
            match_data = raw_match
        else:
            match_data = {
                "channel": b.get("channel", ""),
                "account_id": b.get("account_id", ""),
                "peer": b.get("peer"),
            }

        peer_data = match_data.get("peer")
        peer: PeerMatchConfig | None
        if isinstance(peer_data, dict):
            peer = _dict_to_dataclass(PeerMatchConfig, peer_data)
        elif isinstance(peer_data, (str, int, float)):
            peer = PeerMatchConfig(id=str(peer_data))
        elif peer_data is None:
            peer = None
        else:
            logger.warning("Ignoring unsupported binding peer value: %r", peer_data)
            peer = None

        bindings_config.append(BindingConfig(
            agent_id=b.get("agent_id", "main"),
            match=BindingMatchConfig(
                channel=match_data.get("channel", ""),
                account_id=match_data.get("account_id", ""),
                peer=peer,
            ),
        ))

    # Session config
    session_data = data.get("session", {})
    if not isinstance(session_data, dict):
        logger.warning("Invalid session config type: %s", type(session_data).__name__)
        session_data = {}

    def _to_int(value: Any, default: int, *, key: str) -> int:
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            logger.warning("Invalid session.%s=%r; using default=%d", key, value, default)
            return default

    idle_minutes = session_data.get("idle_minutes")
    idle_timeout_s = session_data.get("idle_timeout_s")
    idle_timeout_s_int: int | None = None
    if idle_timeout_s is not None:
        try:
            idle_timeout_s_int = int(idle_timeout_s)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid session.idle_timeout_s=%r; ignoring legacy field",
                idle_timeout_s,
            )

    if idle_minutes is None and idle_timeout_s_int is not None:
        seconds = idle_timeout_s_int
        if seconds <= 0:
            idle_minutes = 0
        else:
            idle_minutes = (seconds + 59) // 60

    reset_triggers_raw = session_data.get("reset_triggers", ["/new", "/reset"])
    if isinstance(reset_triggers_raw, str):
        reset_triggers = [reset_triggers_raw]
    elif isinstance(reset_triggers_raw, list):
        reset_triggers = [str(x) for x in reset_triggers_raw if str(x).strip()]
    else:
        logger.warning(
            "Invalid session.reset_triggers=%r; using defaults",
            reset_triggers_raw,
        )
        reset_triggers = ["/new", "/reset"]

    store_path_raw = session_data.get("store_path", "~/.teaming24/sessions.db")
    store_path = str(store_path_raw) if store_path_raw else "~/.teaming24/sessions.db"

    session_config = SessionConfig(
        dm_scope=session_data.get("dm_scope", "per-channel-peer"),
        idle_minutes=_to_int(idle_minutes, 120, key="idle_minutes"),
        idle_timeout_s=idle_timeout_s_int,
        max_history=max(0, _to_int(session_data.get("max_history", 200), 200, key="max_history")),
        store_path=store_path,
        reset_triggers=reset_triggers,
    )

    # Scheduler config
    sched_data = data.get("scheduler", {})
    sched_jobs = [
        _dict_to_dataclass(SchedulerJobConfig, j)
        for j in (sched_data.get("jobs") or [])
    ]
    scheduler_config = SchedulerConfig(
        auto_start=sched_data.get("auto_start", False),
        jobs=sched_jobs,
    )

    cfg = Config(
        system=system_config,
        security=security_config,
        network=network_config,
        agents=agents_config,
        llm=llm_config,
        tools=tools_config,
        payment=payment_config,
        runtime=runtime_config,
        an_router=an_router_config,
        local_agent_router=local_agent_router_config,
        output=output_config,
        framework=framework_config,
        channels=channels_config,
        bindings=bindings_config,
        session=session_config,
        scheduler=scheduler_config,
        extensions=data.get("extensions", {}),
    )

    # ------------------------------------------------------------------
    # Post-init: resolve wallet_address, an_id, and display name
    # ------------------------------------------------------------------
    resolve_node_identity(cfg.network.local_node, logger=logger)

    return cfg


def load_config(config_path: str | None = None) -> Config:
    """
    Load configuration from teaming24.yaml.

    Priority:
    1. Environment variables
    2. Custom config file (via config_path or TEAMING24_CONFIG env var)
    3. teaming24.yaml (default config file)

    The loaded config is also stored as the global singleton
    (accessible via ``get_config()``), so ``an_id`` and other
    process-scoped values remain stable across all callers.

    Args:
        config_path: Optional path to a custom config file.

    Returns:
        Config object with loaded settings.
    """
    global _config
    data: dict[str, Any] = {}
    try:
        from dotenv import load_dotenv
        project_root = CONFIG_DIR.parent.parent.parent
        env_file = project_root / ".env"
        if env_file.exists():
            load_dotenv(env_file)
    except Exception as exc:
        logger.debug("Skipping .env load in config loader: %s", exc)

    # Check for custom config file
    custom_config_path = config_path or os.environ.get("TEAMING24_CONFIG")

    if custom_config_path:
        path = Path(custom_config_path)
        if path.exists():
            logger.info(f"Loading config from: {path}")
            data = _load_yaml_file(path)
        else:
            logger.warning(f"Config file not found: {path}")

    # If no custom config, use default config file
    if not data:
        config_file = CONFIG_DIR / CONFIG_FILE
        if config_file.exists():
            logger.info(f"Loading config from: {config_file}")
            data = _load_yaml_file(config_file)
        else:
            logger.warning(f"Config file not found: {config_file}, using defaults")

    # Apply environment variable overrides
    data = apply_env_overrides(data, environ=os.environ, logger=logger)
    # Resolve ${ENV_VAR} placeholders from environment after overrides.
    data = _resolve_env_placeholders(data)

    # Pydantic validation (logs warnings but doesn't block startup)
    try:
        from teaming24.config.validation import validate_config as _validate
        errors = _validate(data)
        for err in errors:
            logger.warning("[Config validation] %s", err)
    except Exception as exc:
        logger.debug("[Config validation] skipped: %s", exc)

    cfg = _build_config_from_dict(data)
    # Store as global singleton so an_id stays stable
    _config = cfg
    return cfg


def get_config() -> Config:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config(config_path: str | None = None) -> Config:
    """Reload configuration from file, preserving the node identity."""
    global _config
    old_an_id = None
    if _config and hasattr(_config, 'network'):
        ln = getattr(_config.network, 'local_node', None)
        if ln:
            old_an_id = getattr(ln, 'an_id', None)
    _config = load_config(config_path)
    if old_an_id and hasattr(_config, 'network'):
        ln = getattr(_config.network, 'local_node', None)
        if ln:
            ln.an_id = old_an_id
    return _config


# Global config instance
_config: Config | None = None


# =============================================================================
# Exports
# =============================================================================

# For backward compatibility, also export with old names
UNIFIED_CONFIG_FILE = CONFIG_FILE

__all__ = [
    # Main config
    'Config',
    'load_config',
    'get_config',
    'reload_config',
    'CONFIG_DIR',
    'CONFIG_FILE',
    'UNIFIED_CONFIG_FILE',

    # System config
    'SystemConfig',
    'ServerConfig',
    'ApiConfig',
    'FrontendConfig',
    'CorsConfig',
    'DatabaseConfig',
    'LoggingConfig',
    'DevModeConfig',
    'TaskManagerConfig',

    # Security config
    'SecurityConfig',
    'JwtConfig',
    'ApiKeyConfig',
    'RateLimitConfig',
    'EncryptionConfig',

    # Network config
    'NetworkConfig',
    'LocalNodeConfig',
    'DiscoveryConfig',
    'ConnectionConfig',
    'MessagingConfig',
    'SubscriptionConfig',
    'MarketplaceConfig',
    'AgentaNetCentralConfig',

    # Agent config
    'AgentsConfig',
    'AgentDefaultsConfig',
    'OrganizerConfig',
    'CoordinatorConfig',

    # LLM config
    'LLMConfig',

    # Tools config
    'ToolsConfig',
    'BrowserToolConfig',
    'SandboxToolConfig',
    'SearchToolConfig',
    'FilesystemToolConfig',
    'NetworkToolConfig',
    'OpenHandsToolConfig',

    # Payment config
    'PaymentConfig',
    'PaymentNetworkConfig',
    'PaymentSettingsConfig',
    'FacilitatorConfig',
    'MerchantConfig',
    'WalletConfig',

    # Runtime config
    'RuntimeConfig',
    'SandboxPoolConfig',
    'SandboxRuntimeConfig',
    'ExecutionConfig',
    'MemoryConfig',
    'OpenHandsRuntimeConfig',
    # AN Router config
    'ANRouterConfig',

    # Output config
    'OutputConfig',

    # Scheduler config
    'SchedulerConfig',
    'SchedulerJobConfig',
]
