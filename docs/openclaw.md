# OpenClaw Integration

Teaming24 integrates with [OpenClaw](https://github.com/openclaw/openclaw) — a personal AI
assistant that connects to messaging channels (WhatsApp, Telegram, Slack, Discord, and more).

## Modular Design

Teaming24 can run **standalone** (default) or **with OpenClaw**:

| Mode | `extensions.openclaw.enabled` | Result |
|------|------------------------------|--------|
| **Standalone** | `false` (default) | No `/api/openclaw/*` routes; no openclaw_* worker tools. Full Teaming24 via Dashboard, REST, WebSocket. |
| **OpenClaw** | `true` | `/api/openclaw/*` routes active; workers may use openclaw_browser_*, openclaw_notify if expose_* enabled. |

Set `enabled: true` only when you have OpenClaw running and install the Teaming24 plugin.

## Architecture

Integration uses two components:

| Component | Role |
|-----------|------|
| **Teaming24 Plugin** (`packages/openclaw-plugin`) | Registers `teaming24_*` tools in OpenClaw; calls Teaming24 REST API |
| **Teaming24 API** (`/api/openclaw/*`) | Execute, delegate, wallet, network endpoints |
| **Worker Tools** (`openclaw_tools.py`) | CrewAI agents use OpenClaw's browser, notify, session_send via HTTP |

```
User (WhatsApp / Telegram / Slack / Discord / …)
        │
        ▼
 OpenClaw (with Teaming24 plugin installed)
        │  teaming24_execute / teaming24_wallet / teaming24_network / teaming24_delegate
        ▼
 POST /api/openclaw/execute   (SSE stream)
 GET  /api/openclaw/wallet
 GET  /api/openclaw/network
 POST /api/openclaw/delegate
        │
        ▼
 Teaming24 Task Pipeline
   Organizer → Coordinator → Workers
        │
        │  Workers may call OpenClaw HTTP /tools/invoke
        │  (browser, notify, sessions_send) for browser/notification features
        ▼
 Result streamed back to OpenClaw session
```

---

## Prerequisites

1. **OpenClaw** running locally — https://github.com/openclaw/openclaw
2. **Teaming24** server running (e.g. `uv run python main.py`)

---

## Setup

### 1. Install the Teaming24 plugin in OpenClaw

Copy the plugin directory to OpenClaw's extensions folder:

```bash
cp -r packages/openclaw-plugin ~/.openclaw/extensions/teaming24/
```

Add to `~/.openclaw/openclaw.json`:

```json
{
  "plugins": {
    "entries": {
      "teaming24": {
        "enabled": true,
        "config": {
          "base_url": "http://localhost:8000",
          "token": "",
          "stream_progress": true
        }
      }
    }
  }
}
```

### 2. Configure Teaming24 (worker tools)

Edit `teaming24/config/teaming24.yaml` and set `enabled: true` to activate OpenClaw integration:

```yaml
extensions:
  openclaw:
    enabled: true                          # Required: enables routes + worker tools
    gateway_url: "ws://127.0.0.1:18789"   # Derives http://127.0.0.1:18789 for /tools/invoke
    token: ""                              # Optional; for OpenClaw API auth
    expose_browser_tool: true
    expose_notify_tool: true
    expose_session_tool: false             # sessions_send denied on HTTP unless allowlisted
    tool_timeout: 30
```

### 3. Use from OpenClaw

From any linked chat channel:

> "Use teaming24 to write and test a Python web scraper for example.com"

> "Check my teaming24 wallet balance"

> "What teaming24 network peers are available?"

---

## Registered Tools (via Plugin)

| Tool | Description |
|------|-------------|
| `teaming24_execute` | Multi-agent task execution with live progress stream |
| `teaming24_delegate` | Delegate task to a specific network peer |
| `teaming24_wallet` | Query balance, transactions, or summary |
| `teaming24_network` | List connected peers and node status |

---

## Worker Tools (CrewAI agents)

When enabled via config, CrewAI workers get these tools that call OpenClaw's HTTP `POST /tools/invoke`:

| Teaming24 Tool | OpenClaw Tool | Description |
|----------------|---------------|-------------|
| `openclaw_browser_snapshot` | `browser` (navigate + snapshot) | Visit URL, capture page text |
| `openclaw_browser_action` | `browser` (navigate \| act) | Navigate, click, type, scroll |
| `openclaw_notify` | `nodes` (action=notify) | Send desktop/mobile notification |
| `openclaw_session_send` | `sessions_send` | Agent-to-agent messaging (denied on HTTP unless allowlisted) |

**Note:** `sessions_send` is denied by default on HTTP. To enable it, configure OpenClaw with `gateway.tools.allow: ["sessions_send"]` and set `expose_session_tool: true` in teaming24.yaml.

---

## Configuration Reference

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Master switch: `true` = mount routes + allow worker tools; `false` = standalone |
| `gateway_url` | `ws://127.0.0.1:18789` | OpenClaw Gateway URL (ws → http for worker tool calls) |
| `token` | `""` | Optional auth token for `/api/openclaw/*` (X-OpenClaw-Token header) |
| `expose_browser_tool` | `true` | Enable browser snapshot + action for workers |
| `expose_notify_tool` | `true` | Enable openclaw_notify for workers |
| `expose_session_tool` | `false` | Enable openclaw_session_send (sessions_send denied on HTTP unless OpenClaw allowlists it) |
| `tool_timeout` | `30` | Seconds to wait for OpenClaw tool responses |

---

## Troubleshooting

**Plugin not loading:**
- Ensure `~/.openclaw/extensions/teaming24/` contains the plugin files
- Check OpenClaw logs for plugin load errors

**Execute returns 403/401:**
- For remote access, set `token` in plugin config and `extensions.openclaw.token` in teaming24.yaml
- For localhost, leave token blank

**Worker tools fail:**
- OpenClaw must be running; worker tools call `http://127.0.0.1:18789/tools/invoke`
- Verify `gateway_url` matches your OpenClaw Gateway address
