# Configuration

Teaming24 uses a **single unified YAML configuration file**: `teaming24/config/teaming24.yaml`.

## Overview

All configuration is centralized in one file for simplicity. The structure is designed to be extensible for future features. Configuration is validated at startup by Pydantic schemas (`teaming24/config/validation.py`) — errors are logged as warnings but do not block startup.

| Section | Purpose |
|---------|---------|
| `system.*` | Server, API, database, logging, dev mode, task manager |
| `security.*` | JWT, API keys, rate limiting, encryption |
| `network.*` | Node identity, discovery, connections, AgentaNet Central |
| `agents.*` | Agent types, worker selection, tool profiles |
| `framework.*` | Agent framework backend (native / crewai) |
| `channels.*` | Multi-channel messaging (Telegram, Slack, Discord, WebChat) |
| `bindings.*` | Message routing rules (channel → agent) |
| `session.*` | Conversation session lifecycle |
| `llm.*` | LLM providers and models |
| `tools.*` | Available tools (browser, sandbox, etc.) |
| `payment.*` | x402 payment protocol |
| `runtime.*` | Sandbox, OpenHands, and execution environment |
| `an_router.*` | Cross-node task routing (ANRouter) |
| `output.*` | Task output storage |
| `scheduler.*` | Cron/scheduled task execution |
| `extensions.*` | Third-party integrations (OpenClaw, etc.) |

### Startup Validation

Configuration is validated automatically at startup using Pydantic schemas.
Validated constraints include:

| Section | Validation |
|---------|-----------|
| `system.server.port` | Must be 1–65535 |
| `system.server.workers` | Must be >= 1 |
| `system.logging.level` | Must be DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `network.local_node.port` | Must be 1–65535 |
| `network.discovery.broadcast_port` | Must be 1–65535 |
| `payment.mode` | Must be mock/live/testnet |
| `payment.task_price` | Must be a non-negative numeric string |
| `framework.backend` | Must be native/crewai |
| `agents.*.tool_profile` | Must be minimal/coding/research/networking/full |
| `agents.*.max_iter` | Must be >= 1 |

To add validation for a new section, add a Pydantic `BaseModel` in `teaming24/config/validation.py` and reference it from `ConfigSchema`.

## Quick Reference

```yaml
# teaming24/config/teaming24.yaml

system:
  server:
    host: "0.0.0.0"
    port: 8000
  database:
    path: "~/.teaming24/data.db"

network:
  discovery:
    enabled: true
    broadcast_port: 54321
  agentanet_central:
    url: "http://100.64.1.3:8080"
    token: ""
    enabled: true
```

## System Configuration

```yaml
system:
  server:
    host: "0.0.0.0"           # 0.0.0.0 = all interfaces
    port: 8000                # Single port for all services
    workers: 1
    reload: false             # Dev only

  api:
    base_url: "http://localhost:8000"
    prefix: "/api"
    docs_enabled: true        # OpenAPI docs at /docs

  frontend:
    dev_host: "localhost"
    dev_port: 8088

  cors:
    allow_origins:
      - "http://localhost:8000"
      - "http://localhost:8088"
    allow_credentials: true
    allow_methods: ["*"]
    allow_headers: ["*"]

  database:
    path: "~/.teaming24/data.db"
    auto_migrate: true

  logging:
    level: "INFO"             # DEBUG, INFO, WARNING, ERROR
    format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: null                # Set path for file logging
```

## Security Configuration

```yaml
security:
  jwt:
    secret: "change-this-in-production"
    algorithm: "HS256"
    expiration: 3600
    refresh_expiration: 604800

  api_keys:
    enabled: false
    header_name: "X-API-Key"

  rate_limit:
    enabled: true
    requests_per_minute: 60
    burst: 10

  encryption:
    enabled: false
    algorithm: "AES-256-GCM"

  connection_password: null   # Set to require password for incoming connections
```

## Network Configuration

```yaml
network:
  local_node:
    name: "Local Agentic Node"
    host: "127.0.0.1"
    port: 8000
    description: "Local node"
    capability: "General Purpose"
    region: "Local"

  discovery:
    enabled: true
    broadcast_port: 54321     # UDP broadcast port
    broadcast_interval: 5
    node_expiry_seconds: 30

  connection:
    timeout: 30
    retry_attempts: 3
    retry_delay: 5
    keepalive_interval: 60

  agentanet_central:
    url: "http://100.64.1.3:8080"
    token: ""                 # Get from AgentaNet Central
    heartbeat_interval: 60
    enabled: true

  remote_nodes: []            # Pre-configured remote nodes
```

## Framework Configuration

```yaml
# Agent execution backend — "native" (litellm) or "crewai"
framework:
  backend: "native"          # "native" or "crewai"
```

When `backend: "native"`, teaming24 uses its own agent runtime powered by
litellm for LLM calls with OpenAI-compatible tool calling. When `backend: "crewai"`,
the existing CrewAI integration is used.

## Agent Configuration

```yaml
agents:
  # CrewAI framework settings (used when framework.backend = "crewai")
  crewai:
    enabled: true
    process: "hierarchical"    # sequential or hierarchical
    verbose: true
    memory: false
    max_rpm: 10
    planning: false
    planning_llm: "flock/gpt-5.2"
    reasoning: false
    max_reasoning_attempts: 3
    streaming: true

  # Default settings for all agents
  defaults:
    model: "flock/gpt-5.2"
    temperature: 0.7
    max_tokens: 4096
    timeout: 120
    allow_delegation: true
    verbose: true

  # Organizer, Coordinator, Workers — see teaming24.yaml for full config

  # Optional simulation group selection by numeric node IDs
  simulation_worker_groups:
    "0": ["financial_analyst", "quant_researcher", "data_analyst", "blockchain_dev"]  # Alpha
    "1": ["fullstack_dev", "systems_architect", "devops_engineer", "security_engineer"]  # Beta
    "2": ["ml_scientist", "algorithm_designer", "nlp_engineer", "cv_engineer"]  # Gamma
    "3": ["qa_engineer", "technical_writer", "project_manager", "ux_designer"]  # Delta

  # Single-number startup control.
  # Selects one entry from simulation_worker_groups directly.
  demo_active_group_id: 1
```

## Channel Configuration

```yaml
channels:
  telegram:
    enabled: false
    token: "${TELEGRAM_BOT_TOKEN}"
  slack:
    enabled: false
    app_token: "${SLACK_APP_TOKEN}"
    bot_token: "${SLACK_BOT_TOKEN}"
  discord:
    enabled: false
    token: "${DISCORD_BOT_TOKEN}"
  webchat:
    enabled: true              # Always available for the GUI
```

## Binding Configuration

Bindings route incoming channel messages to agents using a most-specific-wins
algorithm. More specific bindings (channel + account + peer) take priority
over broader ones (channel only).

```yaml
bindings:
  - channel: "telegram"
    account_id: "my_bot"
    peer: "12345678"           # Specific Telegram user → specific agent
    agent_id: "researcher"
  - channel: "slack"
    agent_id: "default"        # All Slack messages → default agent
```

## Session Configuration

```yaml
session:
  idle_timeout_s: 1800         # Close session after 30 min idle
  max_history: 200             # Max messages per session
  store_path: "~/.teaming24/sessions.db"
  reset_triggers:              # Phrases that start a new session
    - "/reset"
    - "/new"
```

## Scheduler Configuration

```yaml
scheduler:
  auto_start: false            # Start scheduler on server boot
  jobs:
    - id: "daily_report"
      cron: "0 9 * * *"       # Every day at 9 AM
      prompt: "Generate daily status report"
      enabled: true
```

## LLM Configuration

```yaml
llm:
  default_provider: "flock"

  providers:
    flock:
      enabled: true
      api_key: "${FLOCK_API_KEY}"
      base_url: "https://api.flock.io/v1"
      default_model: "gpt-5.2"

    anthropic:
      enabled: true
      api_key: "${ANTHROPIC_API_KEY}"
      default_model: "claude-sonnet-4-6"

    openai:
      enabled: true
      api_key: "${OPENAI_API_KEY}"
      base_url: "https://api.openai.com/v1"
      default_model: "gpt-5.2"

    local:
      enabled: false
      base_url: "http://localhost:11434/v1"
```

## Runtime Configuration

```yaml
runtime:
  # Default runtime backend
  # Options: "docker" (teaming24 sandbox), "openhands" (OpenHands SDK), "local" (dev only)
  default: "openhands"        # Use OpenHands as default for agent tools

  # Sandbox pool for native Docker runtime
  # Hot sandboxes persist for fast, stateful execution
  sandbox_pool:
    min_size: 0               # Minimum sandboxes to keep warm
    max_size: 10              # Maximum concurrent sandboxes
    idle_timeout: 300         # Remove idle sandboxes after (seconds)

  # OpenHands runtime (OpenHands SDK)
  # Reference: https://docs.openhands.dev/sdk/getting-started
  # Installation: pip install openhands-sdk openhands-tools openhands-workspace
  openhands:
    enabled: true             # Enable OpenHands as backend option
    runtime_type: "docker"    # docker, local
    container_image: "ghcr.io/openhands/agent-server:latest-python"
    workspace_path: "/workspace"
    timeout: 120              # Default command timeout
    enable_auto_lint: true    # Auto-lint code after changes
    enable_jupyter: true      # Enable IPython/Jupyter
    headless_mode: true       # Run without UI
    env_vars: {}              # Environment variables for runtime

  # Execution settings
  execution:
    max_concurrent_tasks: 5
    task_timeout: 600
    checkpoint_interval: 60

  # Memory/context settings
  memory:
    enabled: true
    backend: "local"
    max_context_length: 100000
    respect_context_window: true
    compression_enabled: true
    chat_context_message_preview: 24000
    chat_context_token_reserve: 4096
    agent_recall_max_chars: 8000
    agent_recall_top_k: 5
    agent_summary_trigger_chars: 4000
    agent_summary_max_chars: 2400
    api_search_top_k_max: 50
    api_recent_limit_max: 100
    persistent_max_chars: 200000
    persistent_recent_keep_chars: 140000
    persistent_min_recent_entries: 8
    persistent_summary_max_chars: 16000
    persistent_summary_line_chars: 320
    persistent_compaction_max_passes: 4
```

### OpenHands SDK Modes

The adapter automatically selects the best available mode:

| Mode | Packages Required | Description |
|------|-------------------|-------------|
| `sdk_workspace` | openhands-sdk, openhands-tools, openhands-workspace | Full SDK with Docker sandbox (best) |
| `workspace` | openhands-workspace | Docker workspace only |
| `sdk` | openhands-sdk, openhands-tools | SDK with local workspace |
| `local` | None | Local fallback (no isolation) |

Check available mode:
```python
from teaming24.runtime.openhands import get_openhands_mode
print(get_openhands_mode())  # e.g., "sdk_workspace"
```

### Task Output Configuration

Task outputs are saved to organized directories:

```yaml
# In runtime or via frontend settings
task_output:
  enabled: true
  output_dir: "~/.teaming24/outputs"
```

Output structure:
```
~/.teaming24/outputs/
├── task_20260205_143025_abc123/
│   ├── README.md           # Task summary and run instructions
│   ├── snake.py            # Extracted code files
│   └── requirements.txt    # Dependencies (if any)
```

## Payment Configuration (x402)

```yaml
payment:
  enabled: false
  mode: "mock"                # mock, testnet, mainnet

  network:
    name: "base-sepolia"

  settings:
    scheme: "exact"
    timeout_seconds: 600

  facilitator:
    url: "https://x402.org/facilitator"
```

## Environment Variables

Environment variables override config file values:

```bash
# Server
TEAMING24_PORT=8000
TEAMING24_HOST=0.0.0.0
TEAMING24_LOG_LEVEL=INFO
TEAMING24_DB_PATH=~/.teaming24/data.db
TEAMING24_CONFIG=/path/to/custom.yaml

# LLM Keys
FLOCK_API_KEY=...
OPENAI_API_KEY=sk-xxx
ANTHROPIC_API_KEY=sk-ant-xxx

# x402 Payments
TEAMING24_WALLET_ADDRESS=0x...
TEAMING24_WALLET_PRIVATE_KEY=0x...
TEAMING24_MERCHANT_ADDRESS=0x...
FACILITATOR_PRIVATE_KEY=0x...       # For LocalFacilitator (testnet)
X402_MODE=mock                      # Override payment mode

# Channel Bot Tokens
TELEGRAM_BOT_TOKEN=123456:ABC-...
SLACK_APP_TOKEN=xapp-...
SLACK_BOT_TOKEN=xoxb-...
DISCORD_BOT_TOKEN=...
```

## AgentaNet Central Configuration

The AgentaNet Central Service has its own config file: `agentanet_central/config.yaml`

```yaml
# agentanet_central/config.yaml

server:
  host: "0.0.0.0"
  port: 8080

frontend:
  host: "0.0.0.0"             # Central frontend page listen host (Vite dev)
  port: 5173
  backend_url: "http://127.0.0.1:8080"

database:
  path: "data/agentanet.db"

security:
  secret_key: null            # Set via AGENTANET_SECRET_KEY
  session_expire_hours: 24
  token:
    max_per_user: 5
    prefix: "agn_"

rate_limit:
  enabled: true
  window_seconds: 60
  max_requests: 60

health_check:
  interval: 60
  offline_threshold: 300      # 5 minutes
  delist_threshold: 3600      # 1 hour
```

For Teaming24 -> Central integration, default endpoint is:

```yaml
network:
  agentanet_central:
    url: "http://100.64.1.3:8080"
```

Environment variables for AgentaNet Central:

```bash
AGENTANET_SECRET_KEY=your-secret-key  # Required in production
AGENTANET_PORT=8080
AGENTANET_HOST=0.0.0.0
AGENTANET_DB_PATH=/var/data/agentanet.db
# Frontend dev server (agentanet_central/frontend/vite.config.ts)
AGENTANET_FRONTEND_HOST=0.0.0.0
AGENTANET_FRONTEND_PORT=5173
AGENTANET_FRONTEND_BACKEND_URL=http://127.0.0.1:8080
```

## Priority Order

1. CLI arguments (highest)
2. Environment variables
3. Config file values
4. Default values (lowest)

## Extensions Configuration

### OpenClaw Integration

Teaming24 is **modular**: it runs standalone by default (`enabled: false`). Set `enabled: true` only when using OpenClaw.

| Mode | `enabled` | Result |
|------|-----------|--------|
| Standalone | `false` (default) | No OpenClaw routes or tools. Full Teaming24 via Dashboard, REST, WebSocket. |
| OpenClaw | `true` | Mounts `/api/openclaw/*`; workers may use openclaw_browser_*, openclaw_notify. |

```yaml
extensions:
  openclaw:
    enabled: false                        # true = OpenClaw integration (routes + worker tools)
    gateway_url: "ws://127.0.0.1:18789"   # OpenClaw Gateway (for worker tool calls)
    token: ""                             # X-OpenClaw-Token for API auth
    expose_browser_tool: true
    expose_notify_tool: true
    expose_session_tool: false
    tool_timeout: 30
```

See [OpenClaw Integration Guide](openclaw.md) for setup.

## Running with Custom Config

```bash
# Teaming24
python main.py --config /path/to/custom.yaml
# or
export TEAMING24_CONFIG=/path/to/custom.yaml

# AgentaNet Central
uv run python backend/run.py --config /path/to/custom.yaml
```
