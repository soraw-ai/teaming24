# API Reference

Teaming24 REST API -- comprehensive endpoint documentation.

## Base URL

```
http://localhost:8000
```

All endpoints use the `/api` prefix. In development the Vite proxy forwards
`/api/*` to the backend automatically.

---

## Health & System

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api` | API information (name, version, status) |
| GET | `/api/health` | Health check |
| GET | `/api/info` | Node identity & capabilities |

---

## Configuration

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/config` | Full frontend config (from `teaming24.yaml`) |
| POST | `/api/config/reload` | Reload config from YAML + DB overrides |
| POST | `/api/config/security` | Update connection password |

### GET /api/config

Returns all config fields that the frontend needs. Includes a `config_version`
timestamp for staleness detection.

**Response:**

```json
{
  "server_host": "0.0.0.0",
  "server_port": 8000,
  "api_base_url": "http://localhost:8000",
  "api_prefix": "/api",
  "local_node_name": "Local Agentic Node",
  "local_node_host": "127.0.0.1",
  "local_node_port": 8000,
  "discovery_broadcast_port": 54321,
  "config_version": 1738764180.123,
  "full_config": { "..." : "..." }
}
```

### POST /api/config/reload

Triggers a re-read of `teaming24.yaml` and merges DB overrides. Returns the
new config version.

---

## Chat

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/chat` | Chat with streaming SSE support |
| POST | `/api/chat/simple` | Simple single-turn chat |
| POST | `/api/chat/agent` | Agent-powered chat (multi-step execution) |

### POST /api/chat/agent

Primary chat endpoint. The `mode` field controls routing:

| `mode` | Behaviour |
|--------|-----------|
| `"agent"` (default) | Creates a Task, runs the full multi-agent pipeline (Organizer → Coordinator → Workers). Task appears in the Dashboard. |
| `"chat"` | Sends messages directly to the configured LLM — no agents, no task, no Dashboard entry. Lower latency. |

**Request:**

```json
{
  "messages": [{"role": "user", "content": "Analyze this dataset"}],
  "stream": true,
  "model": null,
  "mode": "agent"
}
```

**Response (stream=true):** Server-Sent Events

```
data: {"type": "task_started", "task_id": "abc123"}
data: {"type": "step", "agent": "Organizer", ...}
data: {"type": "result", "content": "...", "cost": {...}}
data: [DONE]
```

### POST /api/chat

**Request:**

```json
{
  "messages": [{"role": "user", "content": "Hello"}],
  "stream": true,
  "model": null
}
```

**Response (stream=true):** Server-Sent Events

```
data: {"content": "Hello"}
data: {"content": " there!"}
data: [DONE]
```

---

## Agent

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agent/status` | Agent framework status |
| GET | `/api/agent/agents` | List all configured agents |
| GET | `/api/agent/events` | SSE stream for agent/task events (with replay support) |
| POST | `/api/agent/execute` | Execute a task via the agent framework |
| GET | `/api/agent/tasks` | List recent tasks |
| GET | `/api/agent/tasks/{task_id}` | Get task details |
| GET | `/api/agent/outputs` | List task outputs |
| GET | `/api/agent/outputs/{task_id}` | Get output for a specific task |
| GET | `/api/state/snapshot` | Point-in-time snapshot of all tasks, agents, and wallet |

### GET /api/agent/events

Persistent SSE endpoint with **event replay** support.

Every event carries an SSE `id:` field with a monotonic sequence number. On
reconnect the browser automatically sends the `Last-Event-ID` header; the
server replays all buffered events with `seq > last_id` before resuming the
live stream (buffer capacity: 500 events, ~8 min at 1 event/s).

**First connect** (`Last-Event-ID` absent or 0): server sends `agents_init`
and a full `tasks_init` (no cap), then starts the live event loop.

**Reconnect** (`Last-Event-ID` present): server replays missed events from
the in-memory buffer, then continues from the live stream.

Events:

- `agents_init` -- initial agent list (first connect only)
- `tasks_init` -- full task list (first connect only)
- `task_created` -- new task created
- `task_started` -- task execution began
- `task_step` -- agent performed a step (includes agent name, action, content)
- `task_completed` -- task finished successfully
- `task_failed` -- task execution failed
- `task_delegated` -- task delegated to remote AN
- `task_cost_update` -- task cost changed
- `sandbox_registered` -- new sandbox created
- `sandbox_completed` -- sandbox task finished
- `wallet_transaction` -- payment recorded (income/expense)
- `agent_updated` -- agent status/capabilities changed
- `pool_updated` -- Agentic Node Workforce Pool or Local Agent Workforce Pool refreshed

These events are also broadcast to WebSocket clients via `/ws`.

### GET /api/state/snapshot

Returns a point-in-time snapshot of the full system state. Used by the
frontend as a long-disconnect fallback when the SSE buffer has been exhausted.

**Response:**

```json
{
  "tasks": [...],
  "agents": [...],
  "wallet": { "balance": 1.23 },
  "event_seq": 142,
  "timestamp": 1740182400.0
}
```

---

## Sandbox

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/sandbox` | List all sandboxes |
| GET | `/api/sandbox/openhands` | List OpenHands runtimes |
| POST | `/api/sandbox/openhands/sync` | Sync OpenHands pool state |
| GET | `/api/sandbox/stream` | SSE stream for sandbox events |
| POST | `/api/sandbox/register` | Register a new sandbox |
| POST | `/api/sandbox/{id}/heartbeat` | Sandbox heartbeat |
| GET | `/api/sandbox/{id}` | Get sandbox details |
| DELETE | `/api/sandbox/{id}` | Delete sandbox + container + workspace |
| PATCH | `/api/sandbox/{id}/state` | Update sandbox state |
| POST | `/api/sandbox/{id}/stop` | Stop sandbox |
| POST | `/api/sandbox/{id}/event` | Push event to sandbox |
| POST | `/api/sandbox/{id}/screenshot` | Upload screenshot |
| GET | `/api/sandbox/{id}/screenshot` | Get latest screenshot |
| GET | `/api/sandbox/{id}/screenshot/stream` | SSE screenshot stream |
| GET | `/api/sandbox/{id}/metrics` | Get sandbox metrics |
| GET | `/api/sandbox/{id}/events` | Get sandbox event log |

### Cleanup

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/sandbox/cleanup/containers` | List orphaned teaming24 containers |
| POST | `/api/sandbox/cleanup/containers` | Remove orphaned containers |
| POST | `/api/sandbox/cleanup/workspaces` | Remove orphaned workspace dirs |

### Demo

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/sandbox/demo` | Start a sandbox demo |
| POST | `/api/demo/run` | Run a dev-only demo script with args |
| GET | `/api/demo/list` | List available demos |
| GET | `/api/demo/running` | Get running demo status |

### Memory

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/memory/status` | Get durable memory usage, remaining budget, and compaction status for an agent |
| POST | `/api/memory/search` | Search durable agent memory (scoped by `agent_id`) |
| GET | `/api/memory/recent` | Get recent durable memory entries for an agent |
| POST | `/api/memory/save` | Save a durable memory entry for an agent |

---

## Network

### Connection (unified — same protocol for LAN and WAN)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/network/connect` | Connect to a node by IP:port (LAN or WAN) |
| POST | `/api/network/disconnect` | Disconnect from a node |
| POST | `/api/network/handshake` | Handshake endpoint (called by connecting peer) |
| POST | `/api/network/peer-disconnect` | Handle incoming disconnect notification |

### Status & Topology

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/network/status` | Network status & local node info |
| GET | `/api/network/events` | SSE stream for network events |
| GET | `/api/network/nodes` | List all known nodes |
| GET | `/api/network/links` | Active node links (for health-check) |
| GET | `/api/network/inbound` | Inbound peer connections (they → me) |
| GET | `/api/network/search?q=...` | Search nodes by capability |
| POST | `/api/network/probe` | Probe remote node connectivity |
| POST | `/api/network/verify` | Verify node authentication |

### LAN Discovery (UDP broadcast — LAN-only)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/network/lan/start` | Start UDP discovery listener |
| POST | `/api/network/lan/stop` | Stop UDP discovery listener |
| GET | `/api/network/lan/discoverable` | Check if discoverable on LAN |
| POST | `/api/network/lan/discoverable` | Toggle LAN discoverability |
| GET | `/api/network/lan/nodes` | List LAN-discovered nodes |
| POST | `/api/network/lan/broadcast` | Trigger immediate LAN scan |

### Marketplace

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/network/marketplace` | List marketplace nodes |
| POST | `/api/network/marketplace/join` | Join marketplace |
| POST | `/api/network/marketplace/leave` | Leave marketplace |
| POST | `/api/network/marketplace/update` | Update listing |
| GET | `/api/network/marketplace/status` | Marketplace participation status |
| GET | `/api/network/marketplace/node/{id}` | Get node details |

---

## Wallet (x402)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/wallet/config` | Get wallet configuration |
| POST | `/api/wallet/config` | Update wallet configuration |
| GET | `/api/wallet/balance` | Get USDC balance |
| GET | `/api/wallet/transactions` | Transaction history (newest first) |
| GET | `/api/wallet/summary` | Income, expenses, net profit totals |

---

## Payment

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/payment/config` | Payment gate settings (mode, price, enabled) |
| POST | `/api/payment/config` | Update mode, price, enabled flag |
| GET | `/api/payment/status` | Quick status summary |

---

## Scheduler (Cron)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/scheduler/jobs` | List all scheduled jobs |
| POST | `/api/scheduler/jobs` | Add a new scheduled job |
| DELETE | `/api/scheduler/jobs/{job_id}` | Remove a scheduled job |
| POST | `/api/scheduler/start` | Start the scheduler |
| POST | `/api/scheduler/stop` | Stop the scheduler |

### POST /api/scheduler/jobs

**Request:**

```json
{
  "id": "daily_report",
  "cron": "0 9 * * *",
  "prompt": "Generate daily status report",
  "enabled": true
}
```

---

## WebSocket

| Path | Description |
|------|-------------|
| `/ws` | Bidirectional WebSocket endpoint |

### Wire Protocol

```json
// Request (client → server)
{"type": "request", "method": "ping", "params": {}, "seq": 1}

// Response (server → client)
{"type": "response", "seq": 1, "result": {"pong": true}}

// Event (server → client, broadcast)
{"type": "event", "event": "task_step", "data": {...}}
```

All SSE events are also broadcast via WebSocket to connected clients.

---

## OpenAI-Compatible API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/chat/completions` | OpenAI-compatible chat completions |

Standard OpenAI request/response format for interoperability with tools
that speak the OpenAI API.

---

## Database

### Settings

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/db/settings` | Get all settings |
| GET | `/api/db/settings/{key}` | Get single setting |
| POST | `/api/db/settings/{key}` | Update setting |
| DELETE | `/api/db/settings/{key}` | Delete setting |
| POST | `/api/db/settings/reset` | Reset all settings to defaults |

### Connection History

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/db/history` | Get connection history |
| POST | `/api/db/history` | Add history entry |
| DELETE | `/api/db/history/{node_id}` | Delete history for node |
| DELETE | `/api/db/history` | Clear all history |

### Sessions

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/db/sessions` | List sessions |
| POST | `/api/db/sessions` | Create session |
| DELETE | `/api/db/sessions` | Clear all sessions |

### Nodes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/db/nodes` | List saved nodes |
| POST | `/api/db/nodes` | Save node |
| DELETE | `/api/db/nodes/{node_id}` | Delete node |

### Tasks

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/db/tasks` | List tasks |
| GET | `/api/db/tasks/{task_id}` | Get task |
| POST | `/api/db/tasks` | Create task |
| DELETE | `/api/db/tasks/{task_id}` | Delete task |
| GET | `/api/db/tasks/{task_id}/steps` | Get task steps |
| POST | `/api/db/tasks/{task_id}/steps` | Add task step |

### Chat Persistence

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/db/chat/sessions` | List chat sessions |
| GET | `/api/db/chat/sessions/{id}` | Get chat session |
| POST | `/api/db/chat/sessions` | Create chat session |
| DELETE | `/api/db/chat/sessions/{id}` | Delete chat session |
| GET | `/api/db/chat/sessions/{id}/messages` | Get messages |
| POST | `/api/db/chat/sessions/{id}/messages` | Add message |

---

## Docs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/docs` | List available documentation files |
| GET | `/api/docs/{filename}` | Get documentation file content |

---

## Frontend

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serve frontend SPA |
| GET | `/{path}` | Serve static assets / SPA fallback |
