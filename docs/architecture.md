# Architecture

Teaming24 is built around the **AgentaNet** network model for distributed multi-agent collaboration.

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Teaming24                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌────────────┐    ┌────────────┐    ┌────────────────────┐             │
│  │   React    │    │  FastAPI   │    │     AgentaNet      │             │
│  │ Dashboard  │◄──►│   Server   │◄──►│      Network       │             │
│  │            │    │            │    │                    │             │
│  │ • Chat     │    │ • REST API │    │ • LAN Discovery    │             │
│  │ • Agents   │    │ • Settings │    │ • Node Connection  │             │
│  │ • Tasks    │    │ • Auth     │    │ • P2P Messaging    │             │
│  │ • Wallet   │    │            │    │ • x402 Payments    │             │
│  └────────────┘    └────────────┘    └─────────┬──────────┘             │
│                                                 │                        │
└─────────────────────────────────────────────────┼────────────────────────┘
                                                  │
                                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       AgentaNet Central Service                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌────────────┐    ┌────────────┐    ┌────────────────────┐             │
│  │   React    │    │  FastAPI   │    │      SQLite        │             │
│  │ Dashboard  │◄──►│   Server   │◄──►│     Database       │             │
│  │            │    │            │    │                    │             │
│  │ • Login    │    │ • Auth API │    │ • Users            │             │
│  │ • Tokens   │    │ • Tokens   │    │ • Tokens           │             │
│  │ • Admin    │    │ • Marketplace│   │ • Nodes            │             │
│  │ • Settings │    │ • Health   │    │ • Settings         │             │
│  │ • Docs     │    │            │    │ • Docs             │             │
│  └────────────┘    └────────────┘    └────────────────────┘             │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

## AgentaNet Network

AgentaNet is the distributed network layer that connects Agentic Nodes.

### Connection Protocol (unified)

All node connections use the same HTTP handshake (`POST /api/network/connect`)
regardless of how the target was found. The connection protocol is identical for
LAN-discovered and manually-entered (WAN) nodes.

### Discovery Methods

1.  **LAN Discovery (automatic):**
    *   **Mechanism:** UDP Broadcast on configurable port (default: 54321).
    *   **Protocol:** Nodes broadcast a "Hello" packet containing their ID, Name, IP, and Port every 5 seconds.
    *   **Discovery:** The `LANDiscovery` service listens for these packets and maintains a list of local peers.
    *   **Usage:** Zero-config setup for teams on the same local network.
    *   **Priority:** LAN-discovered info takes precedence when a node is reachable via both LAN and WAN.

2.  **Manual / WAN (explicit):**
    *   **Mechanism:** User enters remote IP/Port in the dashboard.
    *   **Handshake:** Same authenticated handshake as LAN connections (password optional).
    *   **Usage:** Connecting to cloud instances, remote workers, or partners across the internet.

3.  **AgentaNet Central Service (Marketplace):**
    *   **Mechanism:** Central authentication and marketplace service.
    *   **Authentication:** GitHub OAuth (mock for development).
    *   **Token Management:** Each user can create API tokens with unique node IDs (per-user limit is configurable).
    *   **Marketplace:** Register nodes, search by capability, auto-discovery.
    *   **Health Monitoring:** Automatic offline detection and cleanup.
    *   **Usage:** Finding and hiring specialized agents via the marketplace.

### Agentic Node (AN)

An Agentic Node is a unit in the network that can host multiple agents:

```
┌──────────────────────────────────┐
│         Agentic Node (AN)        │
├──────────────────────────────────┤
│                                  │
│  ┌──────────┐  ┌──────────────┐  │
│  │Organizer │  │ Coordinator  │  │
│  └────┬─────┘  └──────┬───────┘  │
│       │               │          │
│       ▼               ▼          │
│  ┌─────────────────────────────┐ │
│  │          Workers            │ │
│  │  ┌───┐ ┌───┐ ┌───┐ ┌───┐   │ │
│  │  │ W │ │ W │ │ W │ │ W │   │ │
│  │  └───┘ └───┘ └───┘ └───┘   │ │
│  └─────────────────────────────┘ │
│                                  │
└──────────────────────────────────┘
```

### Agent Roles

| Role | Responsibility |
|------|----------------|
| **Organizer** | Initiates tasks, assigns to Coordinators across the network |
| **Coordinator** | Receives tasks, breaks them down, manages local Workers |
| **Worker** | Executes assigned subtasks, reports results |

### Communication Flow

```
1. User Request
      │
      ▼
┌──────────────┐
│  Organizer   │  Decomposes request into tasks
└──────┬───────┘
       │ Task assignment (via AgentaNet)
       ▼
┌──────────────┐
│ Coordinator  │  Routes to appropriate Workers
│   (Local or  │
│    Remote)   │
└──────┬───────┘
       │ Subtask execution
       ▼
┌──────────────┐
│   Workers    │  Execute and return results
└──────────────┘
```

## Multi-Agent Framework

Teaming24 uses a **pluggable framework abstraction** that decouples task
orchestration from any specific multi-agent engine. Two backends are
supported — teaming24's **native runtime** and **CrewAI** — switchable
via a single config flag.

### Framework Abstraction

```
┌─────────────────────────────────────────────────────────────────────┐
│                     FrameworkAdapter (ABC)                            │
│  run(agents, task, strategy) → AsyncIterator[StepOutput]             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────────────┐       ┌──────────────────────────────┐    │
│  │    NativeAdapter      │       │       CrewAIAdapter           │    │
│  │                       │       │                               │    │
│  │  • litellm direct    │       │  • Wraps CrewAI Crew.kickoff()│    │
│  │  • OpenAI tool calls │       │  • Maps AgentSpec → CrewAI    │    │
│  │  • HierarchicalRunner│       │  • Planning & Reasoning       │    │
│  │  • SequentialRunner   │       │  • Event Listeners            │    │
│  └──────────────────────┘       └──────────────────────────────┘    │
│                                                                      │
│  Shared data types:                                                  │
│  • AgentSpec   — framework-agnostic agent definition                 │
│  • ToolSpec    — framework-agnostic tool with OpenAI schema          │
│  • StepOutput  — unified execution event                             │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

**Switching backends:**

```yaml
# teaming24/config/teaming24.yaml
framework:
  backend: "native"    # or "crewai"
```

### Native Runtime

Teaming24's own agent execution engine — no external framework dependency:

| Component | Role |
|-----------|------|
| `AgentRuntime` | Single-agent LLM + tool loop (litellm) |
| `HierarchicalRunner` | Manager agent plans → workers execute in parallel |
| `SequentialRunner` | Agent chain — each agent's output feeds the next |
| `ToolRegistry` | Central tool registry with `@tool` decorator |

### CrewAI Integration

CrewAI remains fully supported through `CrewAIAdapter`:

- Planning mode (step-by-step plan before execution)
- Reasoning mode (agents reflect before acting)
- Event listeners for real-time streaming
- Existing CrewAI tools bridged via `crewai_tool_to_spec()`

### Task Execution Flow

```
1. User submits task via Chat / Channel / API
      │
      ▼
2. TaskManager creates task (unique ID)
      │
      ▼
3. FrameworkAdapter.run() — delegates to configured backend
      │
      ├──► Native: AgentRuntime + Runner (litellm tool calls)
      │
      └──► CrewAI: Crew.kickoff() (hierarchical delegation)
      │
      ▼
4. ANRouter selects pool members (local + remote ANs)
      │
      ├──► Local subtask → FrameworkAdapter (Coordinator → Workers)
      └──► Remote subtask → HTTP POST to Remote AN (x402)
      │
      ▼
5. Stream progress via SSE + WebSocket
      │
      ▼
6. Organizer aggregates results → User
```

## Runtime & Sandbox

Teaming24 provides isolated execution environments through the RuntimeManager, which aligns with OpenHands SDK patterns.

### RuntimeManager Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                      RuntimeManager (Singleton)                      │
├─────────────────────────────────────────────────────────────────────┤
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                    Runtime Selection                            │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │ │
│  │  │   Sandbox    │  │  OpenHands   │  │    Local     │          │ │
│  │  │  (Default)   │  │   Runtime    │  │  (Dev Only)  │          │ │
│  │  └──────────────┘  └──────────────┘  └──────────────┘          │ │
│  └────────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                    Agent Interface                              │ │
│  │  • execute()         : Run shell commands                       │ │
│  │  • run_code()        : Execute code (Python, JS, Bash)          │ │
│  │  • run_tests()       : Execute test scripts                     │ │
│  │  • get_capabilities(): Query available runtime features         │ │
│  └────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### Supported Runtimes

| Runtime | Description | Use Case |
|---------|-------------|----------|
| `docker` | Teaming24 native Docker sandbox | Default, production use |
| `openhands` | OpenHands SDK runtime | Advanced code execution |
| `local` | Direct host execution | Development only |

### Sandbox Pool & Lifecycle

The `SandboxPool` maintains "hot" (persistent) sandboxes for instant reuse:

```
acquire(agent_id)
  │
  ├── Sandbox exists & running? → Mark in-use, return it
  │
  └── No? → Create new Docker container → Start → Mark in-use
                                                      │
                                                      ▼
                                                  Use sandbox
                                                      │
                                                      ▼
release(agent_id)  ← Sandbox stays running (hot)
  │
  └── stop(agent_id) ← Container removed, workspace cleaned
```

Key properties:
- **Singleton** via `SingletonMixin` (thread-safe double-checked locking)
- **Async lock** (`asyncio.Lock`) guards acquire/release for coroutine safety
- **Crash recovery**: `RuntimeManager.initialize()` cleans orphaned containers on startup
- **Atexit cleanup**: All sandboxes are shut down on process exit

### OpenHands Adapter & Pool

The `OpenHandsPool` provides per-agent runtime allocation:

```
allocate(agent_id)  → Create or reuse OpenHandsAdapter
release(agent_id)   → Disconnect runtime
```

Both pools extend `SingletonMixin` and register atexit handlers via
`sync_async_cleanup` for safe shutdown in both sync and async contexts.

### Agent Tools (Sandbox-First)

All tools use RuntimeManager for sandbox execution:

| Tool | Description |
|------|-------------|
| `shell_command` | Execute shell commands in sandbox |
| `file_read` | Read files from workspace |
| `file_write` | Write files to workspace |
| `python_interpreter` | Execute Python code via IPython |
| `browser` | Browse URLs and fetch content |

### OpenHands SDK Alignment

The RuntimeManager follows OpenHands SDK patterns:

1. **Same Interface**: Same methods work regardless of backend
2. **Event-Driven Design**: Runtime events can be captured and streamed
3. **Sandbox-First**: All untrusted code runs in containers

Reference: [OpenHands SDK](https://docs.openhands.dev/sdk)

See [Runtime Documentation](runtime.md) for detailed usage.

## Project Structure

```
teaming24/
├── main.py                    # Application entry point
├── pyproject.toml             # Python dependencies (uv)
├── teaming24/
│   ├── agent/                 # Multi-agent framework (modular)
│   │   ├── core.py            # LocalCrew orchestrator (2928 lines)
│   │   ├── an_router.py       # AN routing strategies (pluggable)
│   │   ├── events.py          # CrewAI event listeners & step callbacks
│   │   ├── factory.py         # Agent creation factory
│   │   ├── crew_wrapper.py    # CrewAI Crew execution wrapper
│   │   ├── tool_policy.py     # Profile-based tool filtering (plug-and-play)
│   │   ├── context.py         # Context window management (token counting, compaction)
│   │   ├── streaming.py       # SSE streaming callbacks
│   │   ├── framework/         # Pluggable framework abstraction
│   │   │   ├── base.py        # FrameworkAdapter (ABC), AgentSpec, ToolSpec, StepOutput
│   │   │   ├── __init__.py    # create_framework_adapter() factory
│   │   │   ├── crewai_adapter.py  # CrewAI backend
│   │   │   └── native/        # Native runtime (litellm)
│   │   │       ├── runtime.py # AgentRuntime — single-agent LLM + tool loop
│   │   │       ├── runner.py  # HierarchicalRunner, SequentialRunner
│   │   │       └── adapter.py # NativeAdapter
│   │   ├── tools/             # Agent tools
│   │   │   ├── base.py        # @tool decorator, ToolRegistry, crewai_tool_to_spec
│   │   │   ├── network_tools.py   # Network delegation
│   │   │   ├── openhands_tools.py # OpenHands sandbox tools
│   │   │   └── memory_tools.py    # memory_search, memory_save
│   │   └── workers/           # Worker agent blueprints (registry)
│   ├── api/                   # FastAPI server (modular)
│   │   ├── server.py          # Main app + remaining endpoints
│   │   ├── deps.py            # Shared dependencies & singletons
│   │   ├── state.py           # Mutable in-memory state registries
│   │   ├── errors.py          # Typed error codes & exception handlers
│   │   └── routes/            # Plug-and-play route modules
│   │       ├── health.py      # Health, config, docs endpoints
│   │       ├── config.py      # Agent tools, channels, framework
│   │       ├── db.py          # Database CRUD (tasks, settings, sessions)
│   │       ├── wallet.py      # Wallet & x402 payment endpoints
│   │       ├── scheduler.py   # Cron job management
│   │       └── gateway.py     # Gateway status & execution
│   ├── events/                # Typed event bus (plug-and-play)
│   │   ├── types.py           # EventType enum (23 event types)
│   │   ├── bus.py             # EventBus (async + sync pub/sub)
│   │   └── bridge.py          # Thread → asyncio bridge
│   ├── channels/              # Multi-channel messaging
│   │   ├── base.py            # ChannelAdapter (ABC), InboundMessage
│   │   ├── router.py          # BindingRouter (most-specific-wins)
│   │   ├── manager.py         # ChannelManager — orchestrates adapters
│   │   ├── telegram.py        # Telegram bot adapter
│   │   ├── slack.py           # Slack bot adapter
│   │   ├── discord.py         # Discord bot adapter
│   │   └── webchat.py         # WebChat (internal GUI) adapter
│   ├── communication/         # Network layer
│   │   ├── discovery.py       # LAN discovery (UDP broadcast)
│   │   ├── manager.py         # Network manager (unified connection)
│   │   ├── subscription.py    # SSE subscription service (+ WS broadcast)
│   │   ├── central_client.py  # AgentaNet Central Service client
│   │   └── websocket.py       # WSHub, WSClient, WebSocket server
│   ├── config/
│   │   ├── __init__.py        # Config loader (all dataclasses)
│   │   ├── teaming24.yaml     # UNIFIED configuration file
│   │   └── validation.py      # Pydantic startup validation
│   ├── data/
│   │   └── database.py        # SQLite local database
│   ├── llm/                   # LLM provider abstraction
│   │   ├── __init__.py        # get_provider(), LLMProvider, LLMResponse
│   │   └── provider.py        # Unified LLM interface (litellm)
│   ├── memory/                # Persistent agent memory
│   │   ├── store.py           # MemoryStore — SQLite + FTS5
│   │   ├── vector_store.py    # VectorStore — ChromaDB (optional)
│   │   ├── search.py          # hybrid_search() — keyword + vector
│   │   └── manager.py         # MemoryManager — high-level API, Markdown logs
│   ├── plugins/               # Plugin / hook system
│   │   └── hooks.py           # HookRegistry, get_hook_registry()
│   ├── scheduler/             # Cron / scheduled tasks
│   │   └── service.py         # TaskScheduler (APScheduler / asyncio)
│   ├── session/               # Conversation session management
│   │   ├── types.py           # Session, SessionMessage dataclasses
│   │   ├── store.py           # SessionStore — SQLite-backed
│   │   ├── manager.py         # SessionManager — lifecycle, resolution
│   │   ├── context.py         # Token tracking & auto-compaction
│   │   └── compaction.py      # JSONL transcripts & summarization
│   ├── task/                  # Task management
│   │   └── manager.py         # Task tracking, IDs, costs
│   ├── runtime/               # Execution environments
│   │   ├── sandbox/           # Native Docker sandbox
│   │   └── openhands/         # OpenHands adapter
│   ├── payment/
│   │   └── crypto/
│   │       └── x402/          # x402 payment protocol
│   │           ├── types.py   # Types, errors, config
│   │           ├── gate.py    # TaskPaymentGate — enforcement layer
│   │           ├── merchant.py # Server-side (requirements)
│   │           ├── wallet.py  # Client-side (signing)
│   │           └── protocol.py # Verify, settle
│   ├── utils/
│   │   ├── logger.py          # Logging (LogSource, get_agent_logger)
│   │   └── shared.py          # SingletonMixin, HTTP factory, helpers
│   ├── server/
│   │   └── cli.py             # CLI and startup
│   └── gui/                   # React dashboard
│       ├── src/
│       │   ├── components/    # UI components
│       │   │   └── dashboard/
│       │   │       ├── WSEventBridge.tsx  # WebSocket → store bridge
│       │   │       └── ...
│       │   └── store/         # State management
│       │       ├── wsStore.ts # WebSocket connection store
│       │       └── ...
│       └── package.json
├── agentanet_central/         # AgentaNet Central Service (separate project)
│   ├── pyproject.toml
│   ├── config.yaml
│   ├── backend/
│   └── frontend/
├── examples/                  # Demo scripts
└── docs/                      # Documentation
```

## Multi-Channel Messaging

Agents are reachable from external messaging platforms through a unified
`ChannelAdapter` interface:

| Channel | Adapter | Library |
|---------|---------|---------|
| Telegram | `TelegramAdapter` | python-telegram-bot |
| Slack | `SlackAdapter` | slack-bolt (async) |
| Discord | `DiscordAdapter` | discord.py |
| WebChat | `WebChatAdapter` | Internal (GUI) |

Messages from any channel are normalised to `InboundMessage`, routed
by `BindingRouter` (most-specific-wins), resolved to a `Session` by
`SessionManager`, and dispatched through the normal agent execution flow.

## Memory System

Persistent agent memory with hybrid search:

| Component | Purpose |
|-----------|---------|
| `MemoryStore` | SQLite + FTS5 for keyword search (BM25) |
| `VectorStore` | ChromaDB for semantic search (optional) |
| `hybrid_search` | Combines keyword + vector results with RRF |
| `MemoryManager` | High-level API, daily Markdown logs |

Agent-callable tools: `memory_search`, `memory_save`.

## Session Management

Conversation sessions provide context continuity:

- **Resolution**: Route key (channel + account + peer) maps to session.
- **Lifecycle**: Auto-create on first message, close on idle timeout.
- **Reset triggers**: Configurable phrases (e.g., "/reset") start new session.
- **Storage**: SQLite-backed via `SessionStore`.

## Plugin / Hook System

Lifecycle hooks for extensibility via `HookRegistry`:

| Hook | When |
|------|------|
| `before_task_execute` | Before task runs |
| `after_task_execute` | After task completes |

## Scheduler

Cron/scheduled task execution via `TaskScheduler`:

- Uses APScheduler when available, asyncio fallback otherwise.
- Jobs defined in YAML or added via REST API.
- Each job creates a task through the normal Organizer flow.

## Data Flow

### Chat Flow

```
User Input → API Server → FrameworkAdapter → LLM → Streaming Response → Dashboard
```

### Multi-Channel Flow

```
Telegram/Slack/Discord → ChannelAdapter → BindingRouter → SessionManager → Agent → Response
```

### Payment Flow (x402)

```
1. Client requests protected resource
2. Server returns 402 with PaymentRequirements
3. Client signs payment with wallet
4. Client resubmits with X-PAYMENT header
5. Server verifies and settles payment
6. Server returns resource
```

### Real-time Event Flow

```
Backend events → SubscriptionService.broadcast()
  ├── SSE streams (/api/agent/events, /api/network/events)
  └── WebSocket hub (/ws) → all connected clients
```

## Technology Stack

| Layer | Technology |
|-------|------------|
| Frontend | React, TypeScript, Tailwind, Zustand |
| Backend | Python, FastAPI, Pydantic |
| Agent Runtime | litellm (native), CrewAI (pluggable) |
| Network | UDP Broadcast, HTTP/2, WebSocket |
| Real-time | SSE + WebSocket (bidirectional) |
| Memory | SQLite FTS5, ChromaDB (optional) |
| Payments | x402 Protocol, Web3, EIP-3009 |
| Messaging | python-telegram-bot, slack-bolt, discord.py |
| Scheduling | APScheduler (optional), asyncio fallback |
| Config | YAML, python-dotenv |
| Logging | Custom logger (JSON/colored, source categories, agent identity tagging) |
