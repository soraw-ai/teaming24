# Teaming24 Documentation

## Getting Started

- [Getting Started](getting-started.md) — Installation, first run, quick tour
- [Configuration](configuration.md) — Unified config file, environment variables

## Core Concepts

- [Architecture](architecture.md) — System design, agent framework, channels, memory
- [Runtime & Sandbox](runtime.md) — RuntimeManager, sandbox execution, OpenHands SDK
- [Network Guide](network.md) — LAN discovery, node connection, AgentaNet Central
- [x402 Payments](x402-payments.md) — Crypto payments between Agentic Nodes
- [ID Generation](id-generation.md) — Unified ID strategy for backend/frontend/central

## Architecture Highlights

| Module | Description |
|--------|-------------|
| **Framework Abstraction** | Pluggable agent backend — native (litellm) or CrewAI |
| **Multi-Channel Messaging** | Telegram, Slack, Discord, WebChat via unified `ChannelAdapter` |
| **Binding Router** | Most-specific-wins routing from channels to agents |
| **Session Management** | Conversation lifecycle, idle timeout, SQLite-backed |
| **Memory System** | Hybrid search — SQLite FTS5 (keyword) + ChromaDB (semantic) |
| **WebSocket** | Bidirectional real-time communication alongside SSE |
| **Plugin Hooks** | Lifecycle events for extensibility |
| **Scheduler** | Cron/scheduled agent tasks via APScheduler |
| **LLM Provider** | Unified LLM interface with failover (litellm) |
| **OpenAI-Compatible API** | `/v1/chat/completions` for interoperability |

## Reference

- [API Reference](api.md) — REST, WebSocket, and SSE endpoints
- [CLI Reference](cli.md) — Command line options

## AgentaNet Central Service

A separate service for authentication and marketplace:

- **Location:** `agentanet_central/` directory
- **README:** [agentanet_central/README.md](../agentanet_central/README.md)
- **Features:**
  - User authentication (GitHub OAuth)
  - Token management (per-user limit is configurable)
  - Marketplace registration and discovery
  - Admin dashboard (stats, users, settings, docs)
  - Health monitoring and auto-cleanup

## Development

- [DEVELOPMENT.md](../DEVELOPMENT.md) — Development guidelines, architecture flows, coding principles
