# ruff: noqa: E402
"""
FastAPI server for teaming24 GUI backend.

Serves both API and frontend static files.

TODO(refactor): This module is still a monolith.
    Several helper blocks have been extracted into routes/ and services/,
    but endpoint definitions, Pydantic models, and route orchestration are
    still too concentrated here. Remaining high-value splits:
    1. Move more endpoint groups into `teaming24/api/routes/`.
    2. Move request/response models into shared model modules.
    3. Move `/v1/chat/completions` into its own route module.
"""
import asyncio
import json
import os
import queue
import threading
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# Load .env file early (before other imports that may need env vars)
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)
elif Path(".env").exists():
    load_dotenv()

# Disable CrewAI interactive features for server mode
# All output should go through API responses, not console
os.environ["CREWAI_TELEMETRY_OPT_OUT"] = "true"
os.environ["CREWAI_STORAGE_DIR"] = str(_PROJECT_ROOT / ".crewai")
os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"
os.environ["AGENTOPS_TELEMETRY_OPT_OUT"] = "true"  # AgentOps tracing
os.environ["OTEL_SDK_DISABLED"] = "true"  # OpenTelemetry
os.environ["NO_COLOR"] = "1"  # Disable colored output

# Disable execution traces prompt - comprehensive settings
os.environ["CREWAI_EXECUTION_TRACES"] = "false"
os.environ["CREWAI_TRACING_ENABLED"] = "false"
os.environ["CREWAI_TRACING"] = "false"
os.environ["CREWAI_SHOW_TRACES"] = "false"
os.environ["CREWAI_TRACE_CONSENT"] = "false"

# Also try to update the .crewai user config programmatically
_CREWAI_USER_CONFIG = _PROJECT_ROOT / ".crewai" / ".crewai_user.json"
try:
    _CREWAI_USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    import json
    _existing_config = {}
    if _CREWAI_USER_CONFIG.exists():
        with open(_CREWAI_USER_CONFIG) as f:
            _existing_config = json.load(f)
    _existing_config["trace_consent"] = False
    _existing_config["show_traces_prompt"] = False
    with open(_CREWAI_USER_CONFIG, "w") as f:
        json.dump(_existing_config, f, indent=2)
except Exception as e:
    import logging as _logging
    _logging.getLogger(__name__).debug(f"Failed to write CrewAI user config: {e}")
    del _logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from teaming24.agent import (
    check_agent_framework_available,
    check_crewai_available,
    create_local_crew,
)
from teaming24.api.services.agent_memory import (
    build_agent_execution_prompt,
    load_agent_memory_context,
    persist_agent_memory_after_completion,
)
from teaming24.api.services.fallback_agents import (
    build_fallback_coordinator_agent_info,
    build_fallback_organizer_agent_info,
)
from teaming24.api.services.task_progress import (
    remote_stage_default_pct,
    serialize_worker_statuses,
    should_emit_remote_milestone,
    upsert_worker_status,
)
from teaming24.agent.workforce_pool import AgenticNodeWorkforcePool
from teaming24.communication.central_client import get_central_client
from teaming24.communication.discovery import NodeInfo
from teaming24.communication.manager import NetworkManager
from teaming24.config import get_config
from teaming24.data.database import get_database
from teaming24.task import TaskStatus, get_task_manager
from teaming24.task.output import get_output_manager
from teaming24.utils.ids import (
    COORDINATOR_ID,
    LOCAL_COORDINATOR_NAME,
    ORGANIZER_ID,
    SANDBOX_PREFIX,
    build_worker_lookup,
    extract_agent_id_from_openhands_sandbox,
    generic_id,
    normalize_agent_name,
    resolve_agent_id,
    sandbox_id_demo,
    sandbox_id_for_openhands,
    sandbox_id_for_task,
    sandbox_id_generic,
)
from teaming24.utils.ids import (
    worker_id as make_worker_id,
)
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

# Directory paths
BASE_DIR = Path(__file__).parent.parent.parent
DOCS_DIR = BASE_DIR / "docs"
GUI_DIR = BASE_DIR / "teaming24" / "gui"
GUI_DIST_DIR = GUI_DIR / "dist"

# Get configuration
config = get_config()


def _runtime_backend_str() -> str:
    """Return configured runtime backend for sandbox display (openhands, sandbox, local)."""
    try:
        return str(get_config().runtime.default or "openhands").strip().lower()
    except Exception:
        return "openhands"


app = FastAPI(
    title="Teaming24 API",
    description="Backend API for Teaming24 multi-agent collaboration platform",
    version="0.1.0",
    docs_url="/docs" if config.api.docs_enabled else None,
    redoc_url="/redoc" if config.api.docs_enabled else None,
)

# CORS middleware - load from config
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors.allow_origins,
    allow_credentials=config.cors.allow_credentials,
    allow_methods=config.cors.allow_methods,
    allow_headers=config.cors.allow_headers,
)

# Mount WebSocket endpoint
from teaming24.communication.websocket import get_ws_hub, mount_websocket

mount_websocket(app)

# ---------------------------------------------------------------------------
# Register modular route sub-routers (Phase 1A extraction)
# ---------------------------------------------------------------------------
import teaming24.api.state as _st
from teaming24.api.event_buffer import get_event_buffer
from teaming24.api.routes.config import router as _config_router
from teaming24.api.routes.db import router as _db_router
from teaming24.api.routes.gateway import router as _gateway_router
from teaming24.api.routes.health import router as _health_router
from teaming24.api.routes.openclaw import router as _openclaw_router
from teaming24.api.routes.scheduler import router as _scheduler_router
from teaming24.api.routes.wallet import router as _wallet_router
from teaming24.api.services import approval as _approval_service
from teaming24.api.services import wallet as _wallet_service

app.include_router(_health_router)
app.include_router(_scheduler_router)
app.include_router(_gateway_router)
app.include_router(_config_router)
app.include_router(_db_router)
app.include_router(_wallet_router)
# OpenClaw routes: mount only when extensions.openclaw.enabled=True (modular design)
_oc_ext = (
    (config.extensions or {}).get("openclaw", {})
    if isinstance(getattr(config, "extensions", None), dict)
    else {}
)
if _oc_ext.get("enabled", False):
    app.include_router(_openclaw_router)
    logger.info("OpenClaw API routes mounted at /api/openclaw/*")

# Centralized error handling
from teaming24.api.errors import register_error_handlers

register_error_handlers(app)

# Re-export tool policy from canonical location


# =============================================================================
# OpenAI-compatible /v1/chat/completions endpoint
# =============================================================================

class OAIMessage(BaseModel):
    role: str
    content: str

class OAIChatRequest(BaseModel):
    model: str = "teaming24"
    messages: list[OAIMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    user: str | None = None

@app.post("/v1/chat/completions")
async def openai_chat_completions(request: OAIChatRequest, req: Request):
    """OpenAI-compatible chat completions endpoint.

    Accepts the same request format as the OpenAI API and routes
    the last user message through teaming24's agent pipeline.
    Supports both streaming and non-streaming responses.
    """
    import time as _time

    user_msgs = [m for m in request.messages if m.role == "user"]
    if not user_msgs:
        return JSONResponse(status_code=400, content={
            "error": {"message": "No user message found", "type": "invalid_request_error"}
        })
    prompt = user_msgs[-1].content
    req_id = f"chatcmpl-{generic_id()}"
    model_name = request.model or "teaming24"
    created = int(_time.time())

    # Non-streaming response
    if not request.stream:
        try:
            crew = create_local_crew()
            result = crew.execute_sync(prompt)
            content = result.get("result", "") if isinstance(result, dict) else str(result)
        except Exception as exc:
            logger.error(f"OpenAI-compat endpoint error: {exc}")
            content = f"Error: {exc}"

        return JSONResponse(content={
            "id": req_id,
            "object": "chat.completion",
            "created": created,
            "model": model_name,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    # Streaming response (SSE)
    import json as _json

    async def _stream():
        try:
            crew = create_local_crew()
            result = crew.execute_sync(prompt)
            content = result.get("result", "") if isinstance(result, dict) else str(result)
        except Exception as exc:
            logger.error(f"OpenAI-compat streaming error: {exc}")
            content = f"Error: {exc}"

        chunk = {
            "id": req_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant", "content": content},
                "finish_reason": None,
            }],
        }
        yield f"data: {_json.dumps(chunk)}\n\n"

        done_chunk = {
            "id": req_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {_json.dumps(done_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


# =============================================================================
# Scheduler (cron) API endpoints
# =============================================================================

class ScheduleJobRequest(BaseModel):
    name: str
    prompt: str
    cron: str = ""
    interval_seconds: int = 0
    agent_id: str = "main"

# =============================================================================
# Gateway API
# =============================================================================

# =============================================================================
# Channel management API endpoints
# =============================================================================

class ChannelConfigRequest(BaseModel):
    channel: str
    enabled: bool = False
    token: str = ""
    bot_token: str = ""
    app_token: str = ""

# =============================================================================
# Framework backend API endpoints
# =============================================================================

class Message(BaseModel):
    """Chat message model."""
    role: str
    content: str


class ChatRequest(BaseModel):
    """Chat request model."""
    messages: list[Message]
    stream: bool = True
    model: str | None = None
    mode: str = "agent"  # "agent" → multi-agent execution, "chat" → direct LLM
    session_id: str | None = None


class FrontendConfig(BaseModel):
    """Frontend configuration model - comprehensive config for frontend."""
    # Server settings
    server_host: str
    server_port: int

    # API settings
    api_base_url: str
    api_prefix: str

    # Local node settings
    local_node_an_id: str          # Canonical unique ID (wallet + random suffix)
    local_node_name: str           # Human-readable display name
    local_node_wallet_address: str # Crypto wallet address
    local_node_host: str
    local_node_port: int
    local_node_description: str
    local_node_capability: str
    local_node_region: str

    # Discovery settings
    discovery_broadcast_port: int
    discovery_broadcast_interval: int
    discovery_node_expiry_seconds: int
    discovery_max_lan_nodes: int
    discovery_max_wan_nodes: int

    # Connection settings
    connection_timeout: int
    connection_retry_attempts: int
    connection_keepalive_interval: int

    # Subscription settings
    subscription_max_queue_size: int
    subscription_keepalive_interval: int

    # Database settings
    database_path: str

    # Marketplace settings
    marketplace_url: str
    marketplace_auto_rejoin: bool
    agentanet_central_url: str

    # Network auto-connect
    auto_online: bool

    # Legacy fields for backward compatibility
    agentanet_local_host: str
    agentanet_local_port: int
    agentanet_local_name: str

    # Full config dict for advanced use
    full_config: dict | None = None
    # Config version timestamp for staleness detection
    config_version: float | None = None


async def generate_response(messages: list[Message]) -> AsyncGenerator[str, None]:
    """
    Generate streaming response for chat messages.

    This is a fallback when agent framework is not available.
    """
    user_message = ""
    for msg in reversed(messages):
        if msg.role == "user":
            user_message = msg.content
            break

    # Check what's missing
    framework_ok = check_agent_framework_available()
    llm_ok, llm_error = _check_llm_api_keys()

    missing = []
    if not framework_ok:
        missing.append("- No agent framework available. Install CrewAI (`uv pip install crewai`) or configure native backend.")
    if not llm_ok:
        missing.append(f"- {llm_error}")
        missing.append("- Configure provider/model in Settings -> LLM, or set provider API keys in `.env`.")

    if missing:
        response_text = f"""⚠️ **Agent Framework Not Available**

Your message: "{user_message}"

To enable the AI assistant, please configure the following:

{chr(10).join(missing)}

**Quick Setup:**
1. Copy `.env.example` to `.env`
2. Add provider key(s): `FLOCK_API_KEY=...` (or `OPENAI_API_KEY=...` / `ANTHROPIC_API_KEY=...`)
3. Restart the server

Check server logs for more details."""
        logger.info(f"Chat fallback - missing config: {missing}")
    else:
        response_text = f"""Hello! I received your message: "{user_message}"

The agent framework is available but encountered an issue. Check server logs for details."""

    words = response_text.split(' ')
    for i, word in enumerate(words):
        chunk = word + (' ' if i < len(words) - 1 else '')
        yield f"data: {json.dumps({'content': chunk})}\n\n"
        await asyncio.sleep(config.api.streaming_chunk_delay)

    yield "data: [DONE]\n\n"


@app.post("/api/chat")
async def chat(request: ChatRequest):
    """Chat endpoint with streaming support."""
    logger.debug("Chat request", extra={"stream": request.stream, "messages": len(request.messages)})

    if request.stream:
        return StreamingResponse(
            generate_response(request.messages),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
        )
    else:
        full_response = ""
        async for chunk in generate_response(request.messages):
            if chunk.startswith("data: ") and "[DONE]" not in chunk:
                data = json.loads(chunk[6:])
                full_response += data.get("content", "")
        return {"content": full_response, "finish_reason": "stop"}


# ============================================================================
# Sandbox Monitoring API
# ============================================================================

# In-memory sandbox registry and event queues
_sandboxes: dict = _st.sandboxes
_sandbox_events: dict = _st.sandbox_events  # sandbox_id -> list of events
_sandbox_stream_queue: queue.Queue = _st.sandbox_stream_queue  # Thread-safe queue for sandbox events

# Per-task chat SSE event buffer for reconnection.
# Stores raw SSE data strings so reconnecting clients receive the
# exact same events as the original stream.
# { task_id: [sse_data_string, ...] }
_chat_event_buffer: dict = _st.chat_event_buffer
_chat_event_buffer_lock = _st.chat_event_buffer_lock

# OpenHands runtime status tracking
_openhands_sandbox_id: str | None = _st.openhands_sandbox_id


def _sandbox_404() -> None:
    """Raise 404 for missing sandbox (reduces repeated HTTPException)."""
    raise HTTPException(status_code=404, detail="Sandbox not found")


def _task_404() -> None:
    """Raise 404 for missing task (reduces repeated HTTPException)."""
    raise HTTPException(status_code=404, detail="Task not found")


async def sync_openhands_status():
    """Synchronize OpenHands pool status to sandbox tracking.

    This function registers each OpenHands runtime in the pool as a sandbox
    so they can be tracked alongside other sandboxes in the frontend.
    Each runtime shows which agent it's allocated to.
    """
    global _openhands_sandbox_id

    try:
        from teaming24.runtime.openhands import (
            OPENHANDS_AVAILABLE,
            get_openhands_mode,
            get_openhands_pool,
        )

        if not OPENHANDS_AVAILABLE:
            return

        pool = get_openhands_pool()
        pool_status = pool.get_status()
        active_agents = set(pool_status.get("agents", []))

        now = time.time()
        mode = get_openhands_mode()

        # Track which sandbox IDs we've seen this sync
        synced_ids = set()

        def _append_openhands_event(sandbox_id: str, event_type: str, data: dict[str, Any]) -> None:
            entry = {
                "type": event_type,
                "timestamp": time.time(),
                "data": data,
            }
            _sandbox_events.setdefault(sandbox_id, [])
            _sandbox_events[sandbox_id].append(entry)
            _sandbox_events[sandbox_id] = _sandbox_events[sandbox_id][-config.api.max_events_kept:]
            try:
                get_database().save_sandbox_event(sandbox_id, entry)
            except Exception as _e:
                logger.debug("OpenHands event DB persist error sandbox=%s: %s", sandbox_id, _e)

        # Sync each runtime in the pool
        for agent_id in active_agents:
            runtime = pool.get(agent_id)
            if runtime is None:
                continue

            # Generate sandbox ID from agent ID
            sandbox_id = sandbox_id_for_openhands(agent_id)
            synced_ids.add(sandbox_id)
            _openhands_sandbox_id = sandbox_id  # Track last one for backward compat
            _st.openhands_sandbox_id = sandbox_id

            # Get status and metrics
            status = runtime.get_status()
            try:
                metrics = await runtime.get_metrics()
            except Exception as e:
                logger.debug(f"Failed to get runtime metrics: {e}")
                metrics = {"cpu_pct": 0, "mem_pct": 0, "mem_used_mb": 0, "disk_pct": 0}

            # Determine state based on connection
            state = "running" if runtime.is_connected else "disconnected"

            # Build display name
            display_name = f"OpenHands ({mode})"
            if agent_id != "default":
                display_name = f"OpenHands [{agent_id}]"

            # Register or update sandbox
            if sandbox_id not in _sandboxes:
                _sandboxes[sandbox_id] = {
                    "state": state,
                    "runtime": "openhands",
                    "workspace": status.get("workspace", "/workspace"),
                    "name": display_name,
                    "role": "openhands",
                    "created": now,
                    "last_heartbeat": now,
                    "uptime_sec": 0,
                    "cpu_pct": metrics.get("cpu_pct", 0),
                    "mem_pct": metrics.get("mem_pct", 0),
                    "mem_used_mb": metrics.get("mem_used_mb", 0),
                    "disk_pct": metrics.get("disk_pct", 0),
                    # Agent association
                    "agent_id": agent_id,
                    "agent_name": agent_id if agent_id != "default" else None,
                    # OpenHands specific info
                    "mode": mode,
                    "model": status.get("model"),
                    "sdk_available": status.get("sdk_available", False),
                    "tools_available": status.get("tools_available", False),
                    "workspace_available": status.get("workspace_available", False),
                    "_oh_last_log_seq": 0,
                }
                _sandbox_events[sandbox_id] = []
                _append_openhands_event(
                    sandbox_id,
                    "info",
                    {
                        "message": f"OpenHands runtime connected ({mode})",
                        "tool": "openhands_status",
                        "agent": agent_id,
                        "state": state,
                    },
                )
                await _broadcast_sandbox_update("registered", sandbox_id)
                logger.info(f"OpenHands runtime registered as sandbox: {sandbox_id} (agent: {agent_id})")
            else:
                # Update existing entry
                prev_state = _sandboxes[sandbox_id].get("state", "unknown")
                _sandboxes[sandbox_id].update({
                    "state": state,
                    "last_heartbeat": now,
                    "name": display_name,
                    "agent_id": agent_id,
                    "agent_name": agent_id if agent_id != "default" else None,
                    "cpu_pct": metrics.get("cpu_pct", 0),
                    "mem_pct": metrics.get("mem_pct", 0),
                    "mem_used_mb": metrics.get("mem_used_mb", 0),
                    "disk_pct": metrics.get("disk_pct", 0),
                })
                if prev_state != state:
                    _append_openhands_event(
                        sandbox_id,
                        "info",
                        {
                            "message": f"Runtime state changed: {prev_state} -> {state}",
                            "tool": "openhands_status",
                            "agent": agent_id,
                            "state": state,
                        },
                    )
                await _broadcast_sandbox_update("heartbeat", sandbox_id)

            # Pull incremental command logs from the OpenHands adapter.
            last_seq = int(_sandboxes[sandbox_id].get("_oh_last_log_seq", 0) or 0)
            new_last_seq = last_seq
            try:
                get_logs = getattr(runtime, "get_command_logs", None)
                cmd_logs = get_logs(after_seq=last_seq) if callable(get_logs) else []
            except Exception as e:
                logger.debug("Failed to read OpenHands command logs sandbox=%s: %s", sandbox_id, e)
                cmd_logs = []

            for log_entry in cmd_logs:
                seq = int(log_entry.get("seq", 0) or 0)
                if seq <= last_seq:
                    continue
                new_last_seq = max(new_last_seq, seq)

                cmd = str(log_entry.get("command", "") or "").strip()
                cwd = str(log_entry.get("cwd", "") or "").strip()
                exit_code = int(log_entry.get("exit_code", -1) or -1)
                out_text = str(log_entry.get("output", "") or "")
                err_text = str(log_entry.get("error", "") or "")

                _append_openhands_event(
                    sandbox_id,
                    "command",
                    {
                        "cmd": cmd,
                        "cwd": cwd,
                        "exit_code": exit_code,
                        "agent": agent_id,
                        "tool": "openhands_command",
                        "status": "completed",
                    },
                )
                if out_text:
                    _append_openhands_event(
                        sandbox_id,
                        "output",
                        {
                            "stream": "stdout",
                            "text": out_text[:4000],
                            "agent": agent_id,
                        },
                    )
                if err_text:
                    _append_openhands_event(
                        sandbox_id,
                        "output",
                        {
                            "stream": "stderr",
                            "text": err_text[:4000],
                            "agent": agent_id,
                        },
                    )

            _sandboxes[sandbox_id]["_oh_last_log_seq"] = new_last_seq

        # Mark any OpenHands sandboxes not in the pool as stopped
        for sandbox_id, info in list(_sandboxes.items()):
            if (info.get("runtime") == "openhands" and
                sandbox_id not in synced_ids and
                info.get("state") == "running"):
                _sandboxes[sandbox_id]["state"] = "stopped"
                await _broadcast_sandbox_update("state_changed", sandbox_id)

    except ImportError as e:
        logger.debug("OpenHands not installed: %s", e)
    except Exception as e:
        logger.debug(f"Failed to sync OpenHands status: {e}")


class SandboxStatus(BaseModel):
    """Sandbox status response."""
    id: str
    state: str
    runtime: str
    workspace: str
    name: str = ""
    role: str = ""
    uptime_sec: float
    cpu_pct: float
    mem_pct: float
    mem_used_mb: int
    disk_pct: float


class SandboxRegister(BaseModel):
    """Sandbox registration request."""
    id: str | None = None
    name: str = ""
    role: str = ""
    runtime: str = "local"
    workspace: str = ""
    # Container info
    container_name: str | None = None
    container_id: str | None = None
    # Task/Agent association
    task_id: str | None = None
    task_name: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    # VNC/CDP URLs for live view
    vnc_url: str | None = None
    cdp_url: str | None = None
    api_url: str | None = None


class SandboxCommand(BaseModel):
    """Command to execute in sandbox."""
    command: str
    cwd: str | None = None
    timeout: float | None = None


# Heartbeat timeout: read from config at runtime (system.api.heartbeat_timeout)

# Screenshot storage: sandbox_id -> {"data": base64_string, "timestamp": float, "width": int, "height": int}
_sandbox_screenshots: dict = _st.sandbox_screenshots

# Global shutdown event — set during app lifespan teardown to break all SSE loops.
# Without this, persistent SSE connections block uvicorn reload/shutdown because
# their async generators never exit (they sit in `while True` + `await`).
_shutdown_event = _st.shutdown_event

# SSE subscribers for real-time sandbox list updates
_sandbox_list_subscribers: list = _st.sandbox_list_subscribers


async def _broadcast_sandbox_update(event_type: str, sandbox_id: str = None):
    """Broadcast sandbox list update to all SSE subscribers.

    Args:
        event_type: Type of update (registered, deleted, state_changed, heartbeat)
        sandbox_id: ID of affected sandbox (optional)
    """
    import json
    import time

    if not _sandbox_list_subscribers:
        return

    # Build update payload
    update = {
        "type": event_type,
        "sandbox_id": sandbox_id,
        "timestamp": time.time(),
    }

    message = f"data: {json.dumps(update)}\n\n"

    # Send to all subscribers (remove dead ones)
    dead_subscribers = []
    for subscriber_queue in _sandbox_list_subscribers:
        try:
            await subscriber_queue.put(message)
        except Exception as e:
            logger.debug(f"Dead sandbox list subscriber detected: {e}")
            dead_subscribers.append(subscriber_queue)

    # Cleanup dead subscribers
    for subscriber_queue in dead_subscribers:
        if subscriber_queue in _sandbox_list_subscribers:
            _sandbox_list_subscribers.remove(subscriber_queue)


@app.get("/api/sandbox")
async def list_sandboxes():
    """List all registered sandboxes.

    Automatically marks sandboxes as 'disconnected' if no heartbeat received
    beyond the configured heartbeat_timeout.
    """
    import time
    # Ensure OpenHands runtime sandboxes and logs stay fresh for dashboard polling.
    await sync_openhands_status()
    heartbeat_timeout = config.api.heartbeat_timeout
    now = time.time()
    result = []

    for sid, info in _sandboxes.items():
        created = info.get("created", now)
        info["uptime_sec"] = now - created

        # Check heartbeat - mark as disconnected if stale
        current_state = info.get("state", "unknown")
        last_heartbeat = info.get("last_heartbeat", created)

        # Only check running sandboxes (not already completed/stopped)
        if current_state == "running" and (now - last_heartbeat) > heartbeat_timeout:
            info["state"] = "disconnected"
            logger.debug(f"Sandbox {sid} marked as disconnected (no heartbeat)")

        sandbox_data = {
            "id": sid,
            "name": info.get("name", sid),
            "state": info.get("state", "unknown"),
            "runtime": info.get("runtime", "local"),
            "role": info.get("role", ""),
            "created": created,
            # Task/Agent association (support both task_id and taskId)
            "taskId": info.get("task_id") or info.get("taskId"),
            "taskName": info.get("task_name") or info.get("taskName"),
            "agentId": info.get("agent_id") or info.get("agentId"),
            "agentName": info.get("agent_name") or info.get("agentName"),
            # Completion info
            "completed": info.get("completed"),
            "duration": info.get("duration"),
            # Heartbeat info
            "lastHeartbeat": last_heartbeat,
            # VNC/CDP URLs for live view
            "vncUrl": info.get("vnc_url"),
            "cdpUrl": info.get("cdp_url"),
            "apiUrl": info.get("api_url"),
        }
        result.append(sandbox_data)
    return {"sandboxes": result}


@app.get("/api/sandbox/openhands")
async def get_openhands_sandbox_status():
    """Get OpenHands pool status.

    Returns the current status of all OpenHands runtimes in the pool, including:
    - Connection state per agent
    - Runtime mode (sdk, workspace, legacy)
    - Available features
    - Resource usage

    This endpoint also syncs the OpenHands status to the sandbox tracking system.
    """
    # Sync OpenHands status to sandbox tracking
    await sync_openhands_status()

    try:
        from teaming24.runtime.openhands import (
            OPENHANDS_AVAILABLE,
            OPENHANDS_LEGACY_AVAILABLE,
            OPENHANDS_SDK_AVAILABLE,
            OPENHANDS_TOOLS_AVAILABLE,
            OPENHANDS_WORKSPACE_AVAILABLE,
            get_openhands_mode,
            get_openhands_pool,
        )

        mode = get_openhands_mode()

        result = {
            "available": OPENHANDS_AVAILABLE,
            "mode": mode,
            "packages": {
                "sdk": OPENHANDS_SDK_AVAILABLE,
                "tools": OPENHANDS_TOOLS_AVAILABLE,
                "workspace": OPENHANDS_WORKSPACE_AVAILABLE,
                "legacy": OPENHANDS_LEGACY_AVAILABLE,
            },
            "connected": False,
            "sandbox_id": _openhands_sandbox_id,
            "pool": {
                "agents": [],
                "count": 0,
            },
        }

        if OPENHANDS_AVAILABLE:
            pool = get_openhands_pool()
            pool_status = pool.get_status()
            agents = pool_status.get("agents", [])

            result["pool"] = {
                "agents": agents,
                "count": len(agents),
                "shutdown": pool_status.get("shutdown", False),
            }

            # Get details for each runtime
            runtimes_info = []
            for agent_id in agents:
                runtime = pool.get(agent_id)
                if runtime:
                    try:
                        status = runtime.get_status()
                        metrics = await runtime.get_metrics()
                        runtimes_info.append({
                            "agent_id": agent_id,
                            "sandbox_id": sandbox_id_for_openhands(agent_id),
                            "connected": runtime.is_connected,
                            "status": status,
                            "metrics": metrics,
                        })
                    except Exception as e:
                        logger.warning(
                            "Failed to fetch OpenHands runtime details agent_id=%s: %s",
                            agent_id,
                            e,
                            exc_info=True,
                        )
                        runtimes_info.append({
                            "agent_id": agent_id,
                            "sandbox_id": sandbox_id_for_openhands(agent_id),
                            "connected": False,
                            "error": str(e),
                        })

            result["runtimes"] = runtimes_info
            result["connected"] = any(r.get("connected") for r in runtimes_info)

        return result

    except ImportError as e:
        logger.warning("OpenHands runtime package unavailable: %s", e, exc_info=True)
        return {
            "available": False,
            "mode": "none",
            "error": f"OpenHands not installed: {e}",
            "packages": {
                "sdk": False,
                "tools": False,
                "workspace": False,
                "legacy": False,
            },
        }
    except Exception as e:
        logger.warning("Failed to fetch OpenHands status: %s", e, exc_info=True)
        return {
            "available": False,
            "mode": "error",
            "error": str(e),
        }


@app.post("/api/sandbox/openhands/sync")
async def sync_openhands_sandbox():
    """Manually trigger OpenHands status sync to sandbox tracking.

    Call this endpoint to force-update the OpenHands sandbox status.
    """
    await sync_openhands_status()
    return {"synced": True, "sandbox_id": _openhands_sandbox_id}


@app.get("/api/sandbox/stream")
async def sandbox_list_stream():
    """Real-time SSE stream for sandbox list updates.

    Clients subscribe to this stream to receive instant notifications when:
    - A sandbox is registered
    - A sandbox is deleted
    - A sandbox state changes
    - A heartbeat is received

    This allows the frontend to update immediately instead of polling.

    Events:
        {"type": "registered", "sandbox_id": "...", "timestamp": ...}
        {"type": "deleted", "sandbox_id": "...", "timestamp": ...}
        {"type": "state_changed", "sandbox_id": "...", "timestamp": ...}
        {"type": "heartbeat", "sandbox_id": "...", "timestamp": ...}
        {"type": "ping", "timestamp": ...}  # Keep-alive every 15s
    """
    import asyncio
    import json
    import time

    async def event_generator():
        queue = asyncio.Queue()
        _sandbox_list_subscribers.append(queue)

        try:
            # Send initial ping
            yield f"data: {json.dumps({'type': 'connected', 'timestamp': time.time()})}\n\n"

            while not _shutdown_event.is_set():
                try:
                    # Wait for update with timeout (for keep-alive)
                    message = await asyncio.wait_for(queue.get(), timeout=config.api.sse_keepalive_timeout)
                    yield message
                except TimeoutError:
                    logger.debug("Sandbox list SSE keepalive timeout; sending ping")
                    # Send keep-alive ping
                    yield f"data: {json.dumps({'type': 'ping', 'timestamp': time.time()})}\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            logger.debug("Sandbox list SSE stream closed by client")
            pass
        finally:
            # Cleanup on disconnect
            if queue in _sandbox_list_subscribers:
                _sandbox_list_subscribers.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.post("/api/sandbox/register")
async def register_sandbox(data: SandboxRegister):
    """Register a sandbox from external agent.

    Sandbox ID: Prefer container_id (Docker) when provided, else data.id, else generated.
    Events: Preserve existing _sandbox_events when re-registering (do not overwrite).
    """
    import time

    from teaming24.utils.ids import sandbox_id_from_container

    # Sandbox ID: data.id if provided, else Docker container_id when available, else generated
    sandbox_id = data.id or (
        sandbox_id_from_container(data.container_id) if data.container_id
        else sandbox_id_generic()
    )

    now = time.time()
    is_update = sandbox_id in _sandboxes
    preserved_created = _sandboxes.get(sandbox_id, {}).get("created", now) if is_update else now

    _sandboxes[sandbox_id] = {
        "state": "running",
        "runtime": data.runtime,
        "workspace": data.workspace,
        "name": data.name or sandbox_id,
        "role": data.role,
        "created": preserved_created,
        "last_heartbeat": now,  # Track last activity
        "uptime_sec": 0,
        "cpu_pct": 0,
        "mem_pct": 0,
        "mem_used_mb": 0,
        "disk_pct": 0,
        # Container info for cleanup
        "container_name": data.container_name,
        "container_id": data.container_id,
        # Task/Agent association (always set when provided)
        "task_id": data.task_id,
        "task_name": data.task_name,
        "agent_id": data.agent_id,
        "agent_name": data.agent_name,
        # VNC/CDP URLs
        "vnc_url": data.vnc_url,
        "cdp_url": data.cdp_url,
        "api_url": data.api_url,
    }
    # Preserve existing events when re-registering (fix Event Log loss)
    if not is_update:
        _sandbox_events[sandbox_id] = []

    logger.info("Sandbox registered", extra={
        "id": sandbox_id,
        "sandbox_name": data.name,
        "task_id": data.task_id,
        "agent_id": data.agent_id,
    })

    # Broadcast update to SSE subscribers
    await _broadcast_sandbox_update("registered", sandbox_id)

    return {"id": sandbox_id, "state": "running"}


@app.post("/api/sandbox/{sandbox_id}/heartbeat")
async def sandbox_heartbeat(sandbox_id: str):
    """Update sandbox heartbeat timestamp."""
    import time

    if sandbox_id not in _sandboxes:
        _sandbox_404()

    _sandboxes[sandbox_id]["last_heartbeat"] = time.time()

    # Note: Don't broadcast heartbeats - too noisy. Frontend will re-fetch on other events.
    return {"status": "ok"}


@app.get("/api/sandbox/{sandbox_id}")
async def get_sandbox(sandbox_id: str):
    """Get sandbox details."""
    import time

    if sandbox_id not in _sandboxes:
        _sandbox_404()

    info = _sandboxes[sandbox_id]
    created = info.get("created", time.time())
    info["uptime_sec"] = time.time() - created

    return SandboxStatus(
        id=sandbox_id,
        state=info.get("state", "unknown"),
        runtime=info.get("runtime", "local"),
        workspace=info.get("workspace", ""),
        name=info.get("name", sandbox_id),
        role=info.get("role", ""),
        uptime_sec=info.get("uptime_sec", 0),
        cpu_pct=info.get("cpu_pct", 0),
        mem_pct=info.get("mem_pct", 0),
        mem_used_mb=info.get("mem_used_mb", 0),
        disk_pct=info.get("disk_pct", 0),
    )


class DeleteRequest(BaseModel):
    """Delete sandbox request."""
    cleanup: bool = True  # Whether to cleanup container and files


@app.delete("/api/sandbox/{sandbox_id}")
async def delete_sandbox(sandbox_id: str, cleanup: bool = True):
    """Remove a sandbox registration and cleanup all associated data.

    This endpoint is designed to be resilient:
    - Removes from registry if present
    - For OpenHands: releases runtime from pool (stops Docker container)
    - For regular sandboxes: attempts Docker container cleanup
    - Attempts workspace cleanup (continues on failure)
    - Always returns success (cleanup is best-effort)

    Args:
        sandbox_id: Sandbox identifier
        cleanup: If True, attempt to cleanup Docker container and workspace files
    """
    sandbox_info = _sandboxes.get(sandbox_id)
    container_name = None
    workspace_path = None
    runtime_type = None
    agent_id = None
    errors = []

    if sandbox_info:
        container_name = sandbox_info.get("container_name")
        workspace_path = sandbox_info.get("workspace")
        runtime_type = sandbox_info.get("runtime")
        agent_id = sandbox_info.get("agent_id")

        # Remove from registry
        del _sandboxes[sandbox_id]

        # Cleanup associated data (in-memory and DB)
        if sandbox_id in _sandbox_events:
            del _sandbox_events[sandbox_id]
        if sandbox_id in _sandbox_screenshots:
            del _sandbox_screenshots[sandbox_id]
        try:
            get_database().clear_sandbox_events(sandbox_id)
        except Exception as e:
            logger.debug(f"Failed to clear sandbox events from DB: {e}")

    # Attempt cleanup even if not in registry (best-effort, continue on errors)
    cleanup_result = {"container": "skipped", "workspace": "skipped", "openhands": "skipped"}

    if cleanup:
        import shutil
        from pathlib import Path

        # Handle OpenHands runtime cleanup
        if runtime_type == "openhands":
            try:
                from teaming24.runtime.openhands import OPENHANDS_AVAILABLE, release_openhands
                if OPENHANDS_AVAILABLE:
                    # Extract agent_id from sandbox_id if not stored
                    if not agent_id:
                        agent_id = extract_agent_id_from_openhands_sandbox(sandbox_id)

                    if agent_id:
                        released = await release_openhands(agent_id)
                        cleanup_result["openhands"] = "released" if released else "not_found"
                        if released:
                            cleanup_result["container"] = "removed"  # OpenHands handles Docker cleanup
                            logger.info(f"OpenHands runtime released for agent: {agent_id}")
            except ImportError:
                logger.warning("OpenHands cleanup module unavailable while deleting sandbox %s", sandbox_id)
                cleanup_result["openhands"] = "not_available"
            except Exception as e:
                logger.warning("OpenHands cleanup failed for sandbox %s: %s", sandbox_id, e, exc_info=True)
                cleanup_result["openhands"] = "error"
                errors.append(f"openhands: {e}")

        # Remove container using centralized cleanup with fallback strategies
        if cleanup_result["container"] != "removed":
            try:
                from teaming24.runtime.sandbox.docker import remove_container
                # Try container_name first, then sandbox_id
                target = container_name or sandbox_id
                removed = await remove_container(target)
                if not removed and container_name and container_name != sandbox_id:
                    removed = await remove_container(sandbox_id)
                cleanup_result["container"] = "removed" if removed else "not_found"
            except Exception as e:
                logger.warning("Container cleanup failed for sandbox %s: %s", sandbox_id, e, exc_info=True)
                cleanup_result["container"] = "not_found"
                errors.append(f"container: {e}")

        # Cleanup workspace directory
        if workspace_path:
            try:
                workspace = Path(workspace_path)
                if workspace.exists():
                    shutil.rmtree(workspace, ignore_errors=True)
                    cleanup_result["workspace"] = "removed"
                else:
                    cleanup_result["workspace"] = "not_found"
            except Exception as e:
                logger.warning("Workspace cleanup failed for sandbox %s: %s", sandbox_id, e, exc_info=True)
                cleanup_result["workspace"] = "not_found"
                errors.append(f"workspace: {e}")

        # Also try default teaming24 workspace path
        try:
            default_workspace = Path.home() / ".teaming24" / "sandboxes" / sandbox_id
            if default_workspace.exists():
                shutil.rmtree(default_workspace, ignore_errors=True)
                cleanup_result["workspace"] = "removed"
        except Exception as e:
            logger.debug(f"Failed to remove default workspace for sandbox {sandbox_id}: {e}")
            pass  # Non-critical

    logger.info("Sandbox deleted", extra={
        "id": sandbox_id,
        "cleanup": cleanup,
        "result": cleanup_result,
        "errors": errors if errors else None,
    })

    # Broadcast deletion to SSE subscribers
    await _broadcast_sandbox_update("deleted", sandbox_id)

    # Always return success - cleanup is best-effort
    return {"status": "removed", "cleanup": cleanup_result}


async def _cleanup_sandbox_resources(sandbox_id: str) -> None:
    """Release runtime resources (Docker container, workspace) for a finished sandbox.

    Called automatically after task completion or failure.  The sandbox
    metadata stays in ``_sandboxes`` so the frontend can still show it in
    the archived / completed section, but the underlying heavy resources
    (Docker container, workspace files, OpenHands runtime) are freed.

    This is best-effort — any errors are logged but never propagated.
    """
    sandbox_info = _sandboxes.get(sandbox_id)
    if not sandbox_info:
        return

    container_name = sandbox_info.get("container_name")
    container_id = sandbox_info.get("container_id")
    workspace_path = sandbox_info.get("workspace")
    runtime_type = sandbox_info.get("runtime")
    agent_id = sandbox_info.get("agent_id")

    logger.info(f"Auto-cleanup sandbox resources: {sandbox_id}")

    # 1. Release OpenHands runtime (stops its Docker container internally)
    if runtime_type == "openhands":
        try:
            from teaming24.runtime.openhands import OPENHANDS_AVAILABLE, release_openhands
            if OPENHANDS_AVAILABLE:
                if not agent_id:
                    agent_id = extract_agent_id_from_openhands_sandbox(sandbox_id)
                if agent_id:
                    released = await release_openhands(agent_id)
                    if released:
                        logger.info(f"OpenHands runtime released for sandbox {sandbox_id}")
                        # OpenHands manages its own Docker cleanup
                        container_name = None  # skip Docker cleanup below
        except Exception as e:
            logger.debug(f"OpenHands cleanup for {sandbox_id}: {e}")

    # 2. Stop / remove Docker container (try container_name, then container_id)
    if container_name or container_id:
        try:
            from teaming24.runtime.sandbox.docker import remove_container
            for candidate in [container_name, container_id]:
                if candidate and await remove_container(candidate):
                    logger.info(f"Container removed for sandbox {sandbox_id}")
                    break
        except Exception as e:
            logger.debug(f"Container cleanup for {sandbox_id}: {e}")

    # 3. Cleanup workspace files
    import shutil
    from pathlib import Path

    for ws_path in [workspace_path, str(Path.home() / ".teaming24" / "sandboxes" / sandbox_id)]:
        if ws_path:
            try:
                p = Path(ws_path)
                if p.exists():
                    shutil.rmtree(p, ignore_errors=True)
                    logger.debug(f"Workspace removed: {ws_path}")
            except Exception as e:
                logger.debug(f"Failed to remove workspace {ws_path}: {e}")
                pass

    # 4. Mark the sandbox metadata as cleaned (keep entry for UI display)
    sandbox_info["resources_released"] = True

    # 5. Remove screenshot cache (no longer useful)
    _sandbox_screenshots.pop(sandbox_id, None)

    logger.info(f"Sandbox resources cleaned up: {sandbox_id}")


@app.get("/api/sandbox/cleanup/containers")
async def list_orphan_containers():
    """List all teaming24-managed Docker containers.

    This finds containers that have teaming24 labels but may not be
    tracked in the sandbox registry (orphaned).
    """
    import asyncio

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "ps", "-a",
            "--filter", "label=teaming24.managed=true",
            "--format", '{"id":"{{.ID}}","name":"{{.Names}}","status":"{{.Status}}","created":"{{.CreatedAt}}"}',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        containers = []
        if proc.returncode == 0 and stdout:
            import json
            for line in stdout.decode().strip().split("\n"):
                if line:
                    try:
                        container = json.loads(line)
                        # Check if tracked in registry
                        container["tracked"] = any(
                            s.get("container_name") == container["name"]
                            for s in _sandboxes.values()
                        )
                        containers.append(container)
                    except json.JSONDecodeError as e:
                        logger.debug("JSON decode in sandbox list: %s", e)

        return {
            "containers": containers,
            "total": len(containers),
            "orphaned": len([c for c in containers if not c["tracked"]]),
        }
    except Exception as e:
        logger.warning("Failed to list sandbox containers: %s", e, exc_info=True)
        return {"error": str(e), "containers": []}


@app.post("/api/sandbox/cleanup/containers")
async def cleanup_orphan_containers(force: bool = True):
    """Remove all teaming24-managed Docker containers.

    Args:
        force: Force remove running containers
    """
    import asyncio

    removed = []
    failed = []

    try:
        # Get all teaming24 containers
        proc = await asyncio.create_subprocess_exec(
            "docker", "ps", "-aq",
            "--filter", "label=teaming24.managed=true",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        if proc.returncode == 0 and stdout:
            for cid in stdout.decode().strip().split("\n"):
                if cid:
                    cmd = ["docker", "rm"]
                    if force:
                        cmd.append("-f")
                    cmd.append(cid)

                    rm_proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await rm_proc.wait()

                    if rm_proc.returncode == 0:
                        removed.append(cid)
                    else:
                        failed.append(cid)

        logger.info(f"Container cleanup: {len(removed)} removed, {len(failed)} failed")
        return {
            "removed": len(removed),
            "failed": len(failed),
            "removed_ids": removed,
            "failed_ids": failed,
        }
    except Exception as e:
        logger.warning("Container cleanup endpoint failed: %s", e, exc_info=True)
        return {"error": str(e), "removed": 0, "failed": 0}


@app.post("/api/sandbox/cleanup/workspaces")
async def cleanup_orphan_workspaces():
    """Clean up orphaned teaming24 workspace directories."""
    import shutil
    from pathlib import Path

    base_dir = Path.home() / ".teaming24" / "sandboxes"

    if not base_dir.exists():
        return {"removed": 0, "paths": []}

    removed = []
    failed = []

    for workspace in base_dir.iterdir():
        if workspace.is_dir() and workspace.name.startswith(SANDBOX_PREFIX):
            # Check if any sandbox is using this workspace
            in_use = any(
                str(workspace) == s.get("workspace")
                for s in _sandboxes.values()
            )

            if not in_use:
                try:
                    shutil.rmtree(workspace, ignore_errors=True)
                    removed.append(str(workspace))
                except Exception as e:
                    logger.debug(f"Failed to remove workspace {workspace}: {e}")
                    failed.append(str(workspace))

    logger.info(f"Workspace cleanup: {len(removed)} removed, {len(failed)} failed")
    return {
        "removed": len(removed),
        "failed": len(failed),
        "paths": removed,
    }


class SandboxStateUpdate(BaseModel):
    """Sandbox state update request."""
    state: str
    completed: bool = False


@app.patch("/api/sandbox/{sandbox_id}/state")
async def update_sandbox_state(sandbox_id: str, state: str, completed: str = "false"):
    """Update sandbox state (running, paused, stopped, completed, error).

    Args:
        sandbox_id: Sandbox ID
        state: New state (running, paused, stopped, completed, error)
        completed: Whether to mark as completed ("true" or "false")
    """
    import time

    if sandbox_id not in _sandboxes:
        _sandbox_404()

    # Parse completed flag (handle string "true"/"false" from query params)
    is_completed = completed.lower() in ("true", "1", "yes")

    info = _sandboxes[sandbox_id]
    old_state = info.get("state", "unknown")
    info["state"] = state

    # Mark completion time and calculate duration
    if is_completed or state in ("completed", "stopped"):
        created = info.get("created", time.time())
        completed_time = time.time()
        info["completed"] = completed_time
        info["duration"] = int((completed_time - created) * 1000)  # milliseconds

    logger.info("Sandbox state updated", extra={
        "id": sandbox_id,
        "old_state": old_state,
        "new_state": state,
        "completed": is_completed,
        "subscribers": len(_sandbox_list_subscribers),
    })

    # Broadcast state change to SSE subscribers
    logger.debug(f"Broadcasting state_changed to {len(_sandbox_list_subscribers)} subscribers")
    await _broadcast_sandbox_update("state_changed", sandbox_id)

    return {"id": sandbox_id, "state": state, "completed": is_completed}


@app.post("/api/sandbox/{sandbox_id}/stop")
async def stop_sandbox(sandbox_id: str):
    """Stop a running sandbox and its Docker container if applicable.

    This is a convenience endpoint that:
    1. Updates sandbox state to 'stopped'
    2. Attempts to stop the Docker container (if Docker runtime)
    3. Calculates and records duration
    """
    import asyncio
    import time

    if sandbox_id not in _sandboxes:
        _sandbox_404()

    info = _sandboxes[sandbox_id]
    old_state = info.get("state", "unknown")

    # Skip if already stopped/completed
    if old_state in ("stopped", "completed"):
        return {"id": sandbox_id, "state": old_state, "message": "Already stopped"}

    # Stop Docker container if applicable
    container_stopped = False
    container_name = info.get("container_name") or info.get("docker_container")

    if container_name:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "stop", "-t", "5", container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, _ = await asyncio.wait_for(proc.communicate(), timeout=config.api.docker_stop_timeout)
            container_stopped = proc.returncode == 0
            logger.info(f"Docker container stopped: {container_name}")
        except Exception as e:
            logger.warning(f"Failed to stop container {container_name}: {e}")

    # Update state
    info["state"] = "stopped"
    created = info.get("created", time.time())
    completed_time = time.time()
    info["completed"] = completed_time
    info["duration"] = int((completed_time - created) * 1000)  # milliseconds

    logger.info("Sandbox stopped", extra={
        "id": sandbox_id,
        "old_state": old_state,
        "container_stopped": container_stopped,
        "duration_ms": info["duration"],
    })

    # Broadcast state change
    await _broadcast_sandbox_update("state_changed", sandbox_id)

    # Full resource cleanup (remove container, workspace, OpenHands runtime)
    try:
        await _cleanup_sandbox_resources(sandbox_id)
    except Exception as cleanup_err:
        logger.debug(f"Sandbox stop cleanup error: {cleanup_err}")

    return {
        "id": sandbox_id,
        "state": "stopped",
        "container_stopped": container_stopped,
        "duration_ms": info["duration"],
    }


@app.post("/api/sandbox/{sandbox_id}/event")
async def add_sandbox_event(sandbox_id: str, event: dict):
    """Add event to sandbox event stream."""
    import time

    if sandbox_id not in _sandboxes:
        _sandbox_404()

    event["timestamp"] = time.time()
    if sandbox_id not in _sandbox_events:
        _sandbox_events[sandbox_id] = []

    _sandbox_events[sandbox_id].append(event)
    # Keep configured number of recent events.
    _sandbox_events[sandbox_id] = _sandbox_events[sandbox_id][-config.api.max_events_kept:]

    # Persist to DB so event history survives restarts
    try:
        get_database().save_sandbox_event(sandbox_id, event)
    except Exception as _e:
        logger.debug(f"Sandbox event DB persist error: {_e}")

    return {"status": "added"}


class ScreenshotData(BaseModel):
    """Browser screenshot data."""
    data: str  # Base64 encoded PNG
    width: int = 0
    height: int = 0


@app.post("/api/sandbox/{sandbox_id}/screenshot")
async def upload_screenshot(sandbox_id: str, screenshot: ScreenshotData):
    """Upload browser screenshot for real-time display."""
    import time

    if sandbox_id not in _sandboxes:
        _sandbox_404()

    _sandbox_screenshots[sandbox_id] = {
        "data": screenshot.data,
        "width": screenshot.width,
        "height": screenshot.height,
        "timestamp": time.time(),
    }

    # Also update heartbeat
    _sandboxes[sandbox_id]["last_heartbeat"] = time.time()

    return {"status": "ok", "size": len(screenshot.data)}


@app.get("/api/sandbox/{sandbox_id}/screenshot")
async def get_screenshot(sandbox_id: str):
    """Get latest browser screenshot.

    Returns 200 with screenshot data if available, 204 if no screenshot
    has been uploaded yet (normal for shell-only sandboxes), or 404 if the
    sandbox itself does not exist.
    """
    from starlette.responses import Response

    if sandbox_id not in _sandboxes:
        _sandbox_404()

    screenshot = _sandbox_screenshots.get(sandbox_id)
    if not screenshot:
        # 204 No Content — not an error, just no screenshot uploaded yet.
        # Using 204 instead of 404 avoids noisy INFO-level logs in uvicorn
        # for sandboxes that simply don't have browser capability.
        return Response(status_code=204)

    return screenshot


@app.get("/api/sandbox/{sandbox_id}/screenshot/stream")
async def stream_screenshots(sandbox_id: str, fps: float = 2.0):
    """Stream browser screenshots via SSE.

    Real-time screenshot streaming for live browser monitoring.

    Args:
        sandbox_id: Sandbox ID
        fps: Frames per second (default: 2, max: 10)
    """
    if sandbox_id not in _sandboxes:
        _sandbox_404()

    # Limit FPS to reasonable range
    fps = min(max(fps, 0.5), 10.0)
    interval = 1.0 / fps

    async def generate():
        last_timestamp = 0
        no_screenshot_count = 0
        max_no_screenshot = 30  # Stop after 30 intervals with no screenshot

        while not _shutdown_event.is_set():
            screenshot = _sandbox_screenshots.get(sandbox_id)

            if screenshot and screenshot.get("timestamp", 0) > last_timestamp:
                last_timestamp = screenshot["timestamp"]
                no_screenshot_count = 0

                # Send screenshot data
                data = {
                    "type": "screenshot",
                    "data": screenshot["data"],
                    "width": screenshot.get("width", 0),
                    "height": screenshot.get("height", 0),
                    "timestamp": screenshot["timestamp"],
                }
                yield f"data: {json.dumps(data)}\n\n"
            else:
                no_screenshot_count += 1
                if no_screenshot_count >= max_no_screenshot:
                    # Send disconnect event and stop
                    yield f"data: {json.dumps({'type': 'disconnected'})}\n\n"
                    break

            # Check if sandbox still exists
            if sandbox_id not in _sandboxes:
                yield f"data: {json.dumps({'type': 'disconnected'})}\n\n"
                break

            await asyncio.sleep(interval)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )


@app.get("/api/sandbox/{sandbox_id}/metrics")
async def get_sandbox_metrics(sandbox_id: str):
    """Get sandbox metrics stream (SSE).

    Returns real container metrics for Docker sandboxes using `docker stats`.
    For OpenHands sandboxes, uses OpenHands adapter metrics.
    """
    import time

    if sandbox_id not in _sandboxes:
        _sandbox_404()

    sandbox_info = _sandboxes.get(sandbox_id, {})
    runtime_type = sandbox_info.get("runtime", "")
    container_name = sandbox_info.get("container_name")
    container_id = sandbox_info.get("container_id")
    agent_id = sandbox_info.get("agent_id")

    async def get_real_metrics() -> dict:
        """Get real metrics based on sandbox type."""
        metrics = {"cpu_pct": 0, "mem_pct": 0, "mem_used_mb": 0, "disk_pct": 0}

        # Docker container metrics (only when we have an actual container)
        if container_id or container_name:
            try:
                from teaming24.runtime.sandbox.docker import get_container_metrics
                target = container_id or container_name
                metrics = await get_container_metrics(target)
            except ImportError as e:
                logger.debug("Docker metrics import failed: %s", e)
            except Exception as e:
                logger.debug(f"Error getting Docker metrics: {e}")

        # OpenHands sandbox metrics
        elif runtime_type == "openhands":
            try:
                from teaming24.runtime.openhands import get_openhands_pool
                pool = get_openhands_pool()
                # Extract agent_id from sandbox_id if not stored
                aid = agent_id or extract_agent_id_from_openhands_sandbox(sandbox_id)
                if aid:
                    runtime = pool.get(aid)
                    if runtime:
                        metrics = await runtime.get_metrics()
            except ImportError as e:
                logger.debug("OpenHands metrics import failed: %s", e)
            except Exception as e:
                logger.debug(f"Error getting OpenHands metrics: {e}")

        # Task / in-process sandbox: use host metrics (no container)
        else:
            try:
                from pathlib import Path

                from teaming24.runtime.sandbox.metrics import MetricsCollector
                from teaming24.runtime.types import RuntimeConfig
                # Use existing path for statvfs (home always exists)
                workspace = sandbox_info.get("workspace") or str(Path.home())
                config = RuntimeConfig(workspace=workspace)
                collector = MetricsCollector(config)
                snap = await collector.snapshot()
                metrics = {
                    "cpu_pct": snap.cpu_pct,
                    "mem_pct": snap.mem_pct,
                    "mem_used_mb": snap.mem_used_mb,
                    "disk_pct": snap.disk_pct,
                }
            except Exception as e:
                logger.debug(f"Host metrics fallback failed for sandbox {sandbox_id}: {e}")

        return metrics

    def _safe_num(v, default=0):
        try:
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            logger.debug("Failed numeric conversion in metrics stream for value=%r", v)
            return default

    async def generate():
        for _ in range(300):  # 5 minutes max
            if _shutdown_event.is_set():
                break
            # Get real metrics
            metrics_data = await get_real_metrics()

            metrics = {
                "timestamp": time.time(),
                "cpu_pct": round(_safe_num(metrics_data.get("cpu_pct")), 1),
                "mem_pct": round(_safe_num(metrics_data.get("mem_pct")), 1),
                "mem_used_mb": int(_safe_num(metrics_data.get("mem_used_mb"))),
                "disk_pct": round(_safe_num(metrics_data.get("disk_pct")), 1),
            }

            # Update stored metrics
            if sandbox_id in _sandboxes:
                _sandboxes[sandbox_id].update({
                    "cpu_pct": metrics["cpu_pct"],
                    "mem_pct": metrics["mem_pct"],
                    "mem_used_mb": metrics["mem_used_mb"],
                    "disk_pct": metrics["disk_pct"],
                })

            yield f"data: {json.dumps(metrics)}\n\n"
            await asyncio.sleep(2)  # Poll every 2 seconds (docker stats is slow)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )


@app.get("/api/sandbox/{sandbox_id}/events")
async def get_sandbox_events(sandbox_id: str):
    """Get sandbox events stream (SSE).

    Returns only real sandbox events from the event history.
    No demo/fake data is generated.
    """
    import time

    # If in-memory history is empty, try DB first (handles server restarts)
    if not _sandbox_events.get(sandbox_id):
        try:
            db_events = get_database().get_sandbox_events(sandbox_id)
            if db_events:
                _sandbox_events.setdefault(sandbox_id, [])
                _sandbox_events[sandbox_id] = db_events
        except Exception as _e:
            logger.debug(f"Sandbox events DB load error: {_e}")

    # Return 404 only if sandbox is not active AND has no historical events
    if sandbox_id not in _sandboxes and not _sandbox_events.get(sandbox_id):
        _sandbox_404()

    async def generate():
        # Send existing real events only
        if sandbox_id in _sandbox_events:
            for event in _sandbox_events[sandbox_id]:
                yield f"data: {json.dumps(event)}\n\n"

        # Keep connection alive for real-time updates
        # Check for new events periodically
        last_event_count = len(_sandbox_events.get(sandbox_id, []))
        while not _shutdown_event.is_set():
            # Check if new events were added
            current_events = _sandbox_events.get(sandbox_id, [])
            if len(current_events) > last_event_count:
                # Send new events
                for event in current_events[last_event_count:]:
                    yield f"data: {json.dumps(event)}\n\n"
                last_event_count = len(current_events)

            # Send heartbeat to keep connection alive
            heartbeat = {
                "type": "heartbeat",
                "timestamp": time.time(),
                "data": {"message": "keepalive"},  # Frontend expects data field
            }
            yield f"data: {json.dumps(heartbeat)}\n\n"
            await asyncio.sleep(2)  # Check for new events every 2 seconds

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )


@app.post("/api/sandbox/demo")
async def create_demo_sandbox():
    """Create a demo sandbox for testing UI."""
    import random
    import time

    sandbox_id = sandbox_id_demo()

    # Randomly assign to a task or agent for demo purposes
    demo_tasks = [
        ("task-001", "Data Analysis Pipeline"),
        ("task-002", "Code Review Automation"),
        ("task-003", "Web Scraping Job"),
    ]
    demo_agents = [
        ("agent-researcher", "Research Agent"),
        ("agent-developer", "Development Agent"),
        ("agent-tester", "Testing Agent"),
    ]

    # 40% chance task, 40% chance agent, 20% standalone
    rand = random.random()
    task_id, task_name, agent_id, agent_name = None, None, None, None

    if rand < 0.4:
        task_id, task_name = random.choice(demo_tasks)
    elif rand < 0.8:
        agent_id, agent_name = random.choice(demo_agents)

    now = time.time()
    _sandboxes[sandbox_id] = {
        "state": "running",
        "runtime": _runtime_backend_str(),
        "workspace": f"/tmp/sandbox/{sandbox_id}",
        "name": "Demo Sandbox",
        "role": "demo",
        "created": now,
        "last_heartbeat": now,
        "uptime_sec": 0,
        "cpu_pct": 12.5,
        "mem_pct": 35.2,
        "mem_used_mb": 512,
        "disk_pct": 15.0,
        # Task/Agent association
        "task_id": task_id,
        "task_name": task_name,
        "agent_id": agent_id,
        "agent_name": agent_name,
        # VNC/CDP URLs - None for demo (no actual container)
        "vnc_url": None,
        "cdp_url": None,
        "api_url": None,
    }
    _sandbox_events[sandbox_id] = []

    logger.info("Demo sandbox created", extra={"id": sandbox_id})
    return {"id": sandbox_id, "state": "running", "name": "Demo Agent"}


# ============================================================================
# Demo Script Runner
# ============================================================================

# Track running demo processes
_demo_processes: dict = _st.demo_processes

# Allowed demo scripts (security: only allow specific scripts)
ALLOWED_DEMO_SCRIPTS = {
    "sandbox_demo.py": {
        "name": "Sandbox Demo",
        "description": "Shell, files, code, metrics",
    },
    "browser_automation_demo.py": {
        "name": "Browser Demo",
        "description": "Browser automation with VNC",
    },
}


class DemoRunRequest(BaseModel):
    """Request to run a demo script."""
    script: str
    args: list[str] = Field(default_factory=list)
    demo_id: str | None = None  # Frontend-generated ID for tracking


@app.post("/api/demo/run")
async def run_demo_script(request: DemoRunRequest):
    """Run a demo script from the examples folder.

    This runs the script as a background process. The script will register
    itself with the sandbox API and appear in the sandbox list.

    Args:
        script: Name of the script in examples/ folder
        args: Additional command line arguments

    Returns:
        status: 'started' | 'error'
        pid: Process ID if started
    """
    import asyncio
    import os
    import shutil
    import time as time_module

    logger.info("Demo run request received", extra={
        "script": request.script,
        "script_args": request.args,
    })

    # Security: Only allow specific scripts
    if request.script not in ALLOWED_DEMO_SCRIPTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown demo script: {request.script}. Allowed: {list(ALLOWED_DEMO_SCRIPTS.keys())}"
        )

    # Build script path
    script_path = BASE_DIR / "examples" / request.script
    if not script_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Demo script not found: {request.script}"
        )

    # Build command
    cmd = ["uv", "run", "python", str(script_path)] + request.args

    logger.info("Starting demo script", extra={
        "script": request.script,
        "script_args": request.args,
        "cmd": cmd,
    })

    # Check if uv is available
    uv_path = shutil.which("uv")
    if not uv_path:
        logger.error("uv command not found in PATH")
        raise HTTPException(
            status_code=500,
            detail="uv command not found. Please install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
        )

    try:
        # Generate demo_id if not provided
        demo_id = request.demo_id or f"demo-{int(time_module.time() * 1000):x}"

        # Start the process in background
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(BASE_DIR),
            # Set environment to ensure proper Python path and pass demo_id
            env={
                **os.environ,
                "PYTHONPATH": str(BASE_DIR),
                "TEAMING24_DEMO_ID": demo_id,
            },
        )

        # Track the process
        _demo_processes[process.pid] = {
            "script": request.script,
            "args": request.args,
            "started": time_module.time(),
            "process": process,
            "demo_id": demo_id,
        }

        # Don't wait for completion - let it run in background
        # The script will register itself with the sandbox API

        script_info = ALLOWED_DEMO_SCRIPTS[request.script]

        logger.info("Demo script started", extra={
            "script": request.script,
            "pid": process.pid,
            "demo_id": demo_id,
            "script_args": request.args,
        })

        return {
            "status": "started",
            "pid": process.pid,
            "script": request.script,
            "name": script_info["name"],
            "demo_id": demo_id,
            "message": f"Demo '{script_info['name']}' started. It will appear in the sandbox list shortly.",
        }
    except FileNotFoundError as e:
        logger.error("Command not found", extra={
            "script": request.script,
            "error": str(e),
            "cmd": cmd,
        })
        raise HTTPException(
            status_code=500,
            detail=f"Command not found: {cmd[0]}. Make sure uv is installed."
        ) from e
    except Exception as e:
        logger.error("Failed to start demo script", extra={
            "script": request.script,
            "error": str(e),
            "error_type": type(e).__name__,
        })
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start demo: {type(e).__name__}: {str(e)}"
        ) from e


@app.get("/api/demo/list")
async def list_demo_scripts():
    """List available demo scripts.

    Returns:
        scripts: List of available demo scripts with metadata
    """
    scripts = []
    for script_name, info in ALLOWED_DEMO_SCRIPTS.items():
        script_path = BASE_DIR / "examples" / script_name
        scripts.append({
            "script": script_name,
            "name": info["name"],
            "description": info["description"],
            "exists": script_path.exists(),
        })
    return {"scripts": scripts}


@app.get("/api/demo/running")
async def list_running_demos():
    """List currently running demo processes.

    Returns:
        processes: List of running demo process info
    """
    running = []
    finished = []

    for pid, info in list(_demo_processes.items()):
        process = info["process"]
        if process.returncode is None:
            # Still running
            running.append({
                "pid": pid,
                "script": info["script"],
                "args": info["args"],
                "started": info["started"],
                "status": "running",
            })
        else:
            # Finished
            finished.append(pid)

    # Cleanup finished processes
    for pid in finished:
        del _demo_processes[pid]

    return {"running": running, "count": len(running)}


# ============================================================================
# Wallet Configuration (x402)
# ============================================================================

# Global wallet state (loaded from env or API)
_wallet_config = _st.wallet_config


def _update_env_file(env_path: Path, updates: dict):
    """Update or create .env file with new values.

    Preserves existing values and comments, only updates specified keys.
    """
    lines = []
    existing_keys = set()

    # Read existing file if it exists
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                stripped = line.strip()
                # Check if this is a key we're updating
                updated = False
                for key, value in updates.items():
                    if stripped.startswith(f"{key}=") or stripped.startswith(f"# {key}="):
                        lines.append(f"{key}={value}\n")
                        existing_keys.add(key)
                        updated = True
                        break
                if not updated:
                    lines.append(line)

    # Add any new keys that weren't in the file
    for key, value in updates.items():
        if key not in existing_keys:
            # Add a blank line before if the file doesn't end with one
            if lines and not lines[-1].strip() == "":
                lines.append("\n")
            lines.append(f"{key}={value}\n")

    # Write back
    with open(env_path, "w") as f:
        f.writelines(lines)

    logger.debug("Updated .env file", extra={"path": str(env_path), "keys": list(updates.keys())})


def _load_wallet_from_env():
    """Load wallet configuration from environment variables."""
    import os

    address = os.getenv("TEAMING24_WALLET_ADDRESS", "")
    private_key = os.getenv("TEAMING24_WALLET_PRIVATE_KEY", "")
    network = os.getenv("TEAMING24_WALLET_NETWORK", "base-sepolia")

    if address:
        _wallet_config["address"] = address
        _wallet_config["is_configured"] = True
        _wallet_config["network"] = network
        if private_key:
            _wallet_config["_private_key"] = private_key
        # Sync to app config so payment gate sees the address
        try:
            config.local_node.wallet_address = address
        except Exception as e:
            logger.debug("Could not set wallet_address on config: %s", e)
        # Auto-enable payment if wallet is configured from env
        if not config.payment.enabled:
            config.payment.enabled = True
            logger.info("[Wallet] Payment auto-enabled (wallet found in env)")
        logger.info("Wallet loaded from environment", extra={
            "address": address[:10] + "...",
            "network": network,
        })


# Load on startup
_load_wallet_from_env()


class WalletConfigRequest(BaseModel):
    """Wallet configuration request."""
    address: str
    private_key: str | None = None
    network: str = "base-sepolia"


# ============================================================================
# Wallet Ledger — Income / Expense Tracking
# ============================================================================

# In-memory ledger of all wallet transactions for this session.
# Each transaction is recorded when the payment gate processes a task.
# Mock mode starting balance is defined in teaming24.yaml → payment.mock.initial_balance.


def _record_wallet_transaction(
    tx_type: str,
    amount: float,
    task_id: str = "",
    task_name: str = "",
    description: str = "",
    tx_hash: str = "",
    payer: str = "",
    payee: str = "",
    mode: str = "mock",
    network: str = "mock",
) -> dict:
    """Record a wallet transaction and return frontend (camelCase) payload."""
    return _wallet_service.record_wallet_transaction(
        tx_type=tx_type,
        amount=amount,
        task_id=task_id,
        task_name=task_name,
        description=description,
        tx_hash=tx_hash,
        payer=payer,
        payee=payee,
        mode=mode,
        network=network,
    )


# ============================================================================
# Network Services (AgentaNet)
# ============================================================================

# Initialize managers (shared state singletons)
_subscription_manager = _st.subscription_manager
_network_manager: NetworkManager | None = _st.network_manager
_local_crew_singleton: Any | None = _st.local_crew_singleton


def get_local_crew_singleton():
    """Get or create the persistent LocalCrew singleton.

    The singleton preserves worker online/offline state across requests.
    It also has an ``on_pool_changed`` callback that refreshes the network
    manager's advertised capabilities whenever the worker pool changes.
    """
    global _local_crew_singleton
    runtime_settings = get_agent_runtime_settings()
    if _local_crew_singleton is None and _st.local_crew_singleton is not None:
        _local_crew_singleton = _st.local_crew_singleton
    if _local_crew_singleton is not None:
        existing_runtime = getattr(_local_crew_singleton, "runtime_settings", {}) or {}
        sync_keys = (
            "defaultLLMProvider",
            "defaultModel",
            "openaiApiKey",
            "anthropicApiKey",
            "flockApiKey",
            "localApiKey",
            "openaiBaseUrl",
            "anthropicBaseUrl",
            "flockBaseUrl",
            "localBaseUrl",
            "localCustomModel",
            "organizerModel",
            "coordinatorModel",
            "workerDefaultModel",
            "workerModelOverrides",
            "anRouterModel",
            "localAgentRouterModel",
            "crewaiProcess",
            "crewaiMemory",
            "crewaiVerbose",
            "crewaiPlanning",
            "crewaiPlanningLlm",
            "crewaiReasoning",
            "crewaiMaxReasoningAttempts",
            "crewaiStreaming",
        )
        if any(existing_runtime.get(k) != runtime_settings.get(k) for k in sync_keys):
            logger.info("Runtime model settings changed; rebuilding LocalCrew singleton")
            _local_crew_singleton = None
            _st.local_crew_singleton = None
    if _local_crew_singleton is None:
        if not check_agent_framework_available():
            return None
        try:
            _local_crew_singleton = create_local_crew(
                task_manager=get_task_manager(),
                runtime_settings=runtime_settings,
            )
            # Wire auto-refresh of network advertisement
            _local_crew_singleton.set_on_pool_changed(_refresh_local_node_advertisement)
            # Initial capability push
            _refresh_local_node_advertisement()
            _st.local_crew_singleton = _local_crew_singleton
        except Exception as e:
            logger.warning(f"Could not create LocalCrew singleton: {e}")
    return _local_crew_singleton


def _refresh_local_node_advertisement():
    """Push current worker capabilities to the network manager.

    Called automatically by ``LocalCrew.on_pool_changed`` whenever a
    worker goes online or offline, so the next LAN broadcast / WAN
    handshake carries accurate capability information.
    """
    crew = _local_crew_singleton
    if not crew:
        return
    try:
        nm = get_network_manager()
    except Exception as e:
        logger.debug("get_network_manager failed: %s", e)
        return

    worker_descriptions = crew.get_worker_descriptions()

    # Build capabilities list in NodeInfo format [{name, description}, ...]
    caps: list[dict[str, str]] = []
    seen_cap_names: set[str] = set()
    for wd in worker_descriptions:
        role = str(wd.get("role", "Worker")).strip() or "Worker"
        goal = str(wd.get("goal", "")).strip()
        key = role.lower()
        if key in seen_cap_names:
            continue
        seen_cap_names.add(key)
        caps.append({"name": role, "description": goal})

    # Always include base capabilities (de-duplicated by name)
    for base_cap in [
        {"name": "task_execution", "description": "Execute distributed tasks"},
        {"name": "code_execution", "description": "Run code in sandboxed environment"},
    ]:
        key = base_cap["name"].lower()
        if key not in seen_cap_names:
            seen_cap_names.add(key)
            caps.append(base_cap)

    # Build concise marketplace-friendly description.
    role_goal_pairs = []
    for wd in worker_descriptions[:4]:
        role = str(wd.get("role", "Worker")).strip() or "Worker"
        goal = str(wd.get("goal", "")).strip()
        if goal:
            role_goal_pairs.append(f"{role} ({goal})")
        else:
            role_goal_pairs.append(role)
    if role_goal_pairs:
        desc = "Agentic Node workforce: " + "; ".join(role_goal_pairs)
    else:
        desc = "Agentic Node for distributed task execution."
    desc = desc[:_MARKETPLACE_DESCRIPTION_MAX_CHARS]

    nm.update_local_capabilities(capabilities=caps, description=desc)
    logger.info(f"Local AN advertisement refreshed: {len(caps)} capabilities")


# Peer tracking state (shared with modular routes)
_inbound_connected_since: dict[str, float] = _st.inbound_connected_since
_peer_failure_counts: dict[str, int] = _st.peer_failure_counts

def get_network_manager() -> NetworkManager:
    """Get or create the network manager singleton."""
    global _network_manager
    if _network_manager is None and _st.network_manager is not None:
        _network_manager = _st.network_manager
    if _network_manager is None:
        from teaming24.utils.ids import get_node_uid

        # an_id = wallet_address + random suffix.  Same value used in
        # delegation chains, requester_id, and loop detection.
        an_id = get_node_uid()  # e.g. "0x23489a…-a3f1b2"
        wallet_addr = config.local_node.wallet_address
        node_name = config.local_node.name  # display name (or ip:port)

        # Build initial capabilities from LocalCrew (if available) so the
        # first broadcast already carries accurate worker information.
        initial_caps = [
            {"name": "task_execution", "description": "Execute distributed tasks"},
            {"name": "code_execution", "description": "Run code in sandboxed environment"},
        ]
        initial_desc = "A Teaming24 Agentic Node ready for distributed tasks"
        crew = _local_crew_singleton
        if crew:
            for wd in crew.get_worker_descriptions():
                initial_caps.append({
                    "name": wd.get("role", "Worker"),
                    "description": wd.get("goal", ""),
                })
            worker_roles = [wd.get("role", "Worker") for wd in crew.get_worker_descriptions()]
            if worker_roles:
                initial_desc = f"Agentic Node with Workers: {', '.join(worker_roles)}"

        local_node = NodeInfo(
            id=an_id,  # Same an_id used in delegation chains
            name=node_name,  # Human-readable display name
            ip="0.0.0.0",  # Will be resolved by discovery
            port=config.local_node.port or 8000,
            role="manager",
            capability="General Purpose",
            capabilities=initial_caps,
            wallet_address=wallet_addr,
            agent_id=an_id,  # agent_id == an_id for consistency
            description=initial_desc,
        )

        async def on_network_event(event_type, data):
            # Refresh inbound peer "last seen" on LAN re-broadcasts.
            # This prevents the health-check from removing peers that are
            # still on the LAN and broadcasting, even if back-connect fails.
            if event_type == "node_seen":
                node_id = data.get("node_id")
                if node_id and node_id in _inbound_connected_since:
                    _inbound_connected_since[node_id] = time.time()
                return  # Don't broadcast internal heartbeat events
            await _subscription_manager.broadcast(event_type, data)

        _network_manager = NetworkManager(
            local_node,
            config=config.discovery,
            on_event=on_network_event
        )
        _st.network_manager = _network_manager
    return _network_manager


async def _peer_health_loop():
    """Periodically probe connected peers and broadcast status changes.

    Strategy:
      - **Outbound peers** (we connected to them): use ``/api/info`` with
        moderate tolerance (3 consecutive failures → mark offline, but keep
        in registry — reconnection is possible).
      - **Inbound peers** (they connected to us): the back-connect is
        **unreliable** (NAT, firewall, remote busy with tasks).  We rely
        primarily on the **"last seen" timestamp** which is refreshed on
        every handshake / re-handshake.  Only remove an inbound peer if:
          a) we haven't heard from it in ``inbound_stale_timeout`` seconds, OR
          b) we can reach it but it explicitly no longer lists us as linked.
        Back-connection failures alone NEVER cause removal.
    """
    import httpx

    interval = config.api.health_check_interval
    outbound_max_fail = config.api.outbound_max_failures  # mark outbound peer offline after N consecutive failures
    inbound_stale_timeout = config.api.inbound_stale_timeout

    while True:
        await asyncio.sleep(interval)

        manager = get_network_manager()
        outbound_peers = list(manager.wan_nodes.values())
        inbound_peers = list(manager.inbound_peers.values())

        if not outbound_peers and not inbound_peers:
            continue

        async with httpx.AsyncClient(timeout=config.api.health_check_http_timeout) as client:
            # ── Outbound peers: basic liveness via /api/info ──
            for peer in outbound_peers:
                key = f"info:{peer.ip}:{peer.port}"
                url = f"http://{peer.ip}:{peer.port}/api/info"
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        raise RuntimeError(f"status={resp.status_code}")

                    _peer_failure_counts[key] = 0
                    if peer.status != "online":
                        peer.status = "online"
                        await _subscription_manager.broadcast(
                            "node_status_changed",
                            {"nodeId": peer.id, "status": "online", "ip": peer.ip, "port": peer.port},
                        )
                except Exception as e:
                    _peer_failure_counts[key] = _peer_failure_counts.get(key, 0) + 1
                    fail_n = _peer_failure_counts[key]
                    # Log individual failures at DEBUG to avoid spam during task execution
                    logger.debug(
                        f"Outbound peer {peer.name} ({peer.ip}:{peer.port}) "
                        f"health check failed ({fail_n}/{outbound_max_fail}): {e}"
                    )
                    if fail_n >= outbound_max_fail and peer.status != "offline":
                        peer.status = "offline"
                        logger.warning(
                            f"Outbound peer {peer.name} ({peer.ip}:{peer.port}) "
                            f"offline after {fail_n} consecutive failures"
                        )
                        await _subscription_manager.broadcast(
                            "node_status_changed",
                            {"nodeId": peer.id, "status": "offline", "ip": peer.ip, "port": peer.port},
                        )

            # ── Inbound peers: "last seen" based liveness ──
            # Inbound peers connected TO us.  Back-connecting to them is
            # unreliable — they may be behind NAT, firewalled, or busy
            # executing a delegated task.  We NEVER remove a peer solely
            # because back-connection fails.
            #
            # Removal triggers:
            #   1. Stale: no handshake received for ``inbound_stale_timeout``.
            #   2. Explicitly unlinked: peer is reachable AND doesn't list us.
            now = time.time()
            for peer in inbound_peers:
                last_seen = _inbound_connected_since.get(peer.id, 0)
                age = now - last_seen

                # ── Check 1: stale timeout ──
                if age >= inbound_stale_timeout:
                    # Haven't heard from this peer in a long time.
                    # Try one last back-connect before removing.
                    still_alive = False
                    try:
                        resp = await client.get(f"http://{peer.ip}:{peer.port}/api/info")
                        if resp.status_code == 200:
                            still_alive = True
                            # Peer is alive — reset the timer
                            _inbound_connected_since[peer.id] = now
                    except Exception as e:
                        logger.debug(f"Back-connection health check to {peer.ip}:{peer.port} failed: {e}")
                        pass

                    if not still_alive:
                        logger.info(
                            f"Inbound peer {peer.name} ({peer.ip}:{peer.port}) "
                            f"stale for {int(age)}s — removing"
                        )
                        _inbound_connected_since.pop(peer.id, None)
                        _peer_failure_counts.pop(f"inbound:{peer.ip}:{peer.port}", None)
                        manager.remove_inbound_peer(peer.id)
                        await _subscription_manager.broadcast(
                            "peer_disconnected",
                            {
                                "nodeId": peer.id,
                                "reason": "stale",
                                "connected_since": last_seen,
                                "peer": peer.model_dump(),
                            },
                        )
                    continue

                # ── Check 2 (optional): explicit unlink verification ──
                # Only attempt if the peer is reachable.  Failure is ignored.
                # We do this less frequently — only when age > 2 minutes.
                if age > 120:
                    try:
                        resp = await client.get(
                            f"http://{peer.ip}:{peer.port}/api/network/links",
                            timeout=config.api.health_check_http_timeout,
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            peers_set = set(data.get("peers") or [])
                            if manager.local_node.id not in peers_set:
                                logger.info(
                                    f"Inbound peer {peer.name} ({peer.ip}:{peer.port}) "
                                    "no longer lists us as linked — removing"
                                )
                                _inbound_connected_since.pop(peer.id, None)
                                manager.remove_inbound_peer(peer.id)
                                await _subscription_manager.broadcast(
                                    "peer_disconnected",
                                    {
                                        "nodeId": peer.id,
                                        "reason": "unlinked",
                                        "connected_since": last_seen,
                                        "peer": peer.model_dump(),
                                    },
                                )
                            else:
                                # Peer still lists us — refresh last_seen
                                _inbound_connected_since[peer.id] = now
                    except Exception as e:
                        logger.debug(f"Back-connection peer list check failed: {e}")
                        pass  # Back-connection failed — not a problem




@app.get("/api/network/status")
async def get_network_status():
    """Get current network connection status including live local node info."""
    manager = get_network_manager()
    ln = manager.local_node
    return {
        "status": "online" if manager.is_running else "offline",
        "node_id": ln.id,
        "is_discovering": manager.discovery.running,
        "peer_count": len(manager.known_nodes),
        # Live local node data (capabilities aggregated from workers)
        "local_node": {
            "id": ln.id,
            "name": ln.name,
            "description": ln.description,
            "capability": ln.capability,
            "capabilities": ln.capabilities,
            "region": getattr(ln, 'region', None),
        },
    }


@app.get("/api/network/lan/status")
async def get_lan_discovery_status():
    """LAN discovery state for frontend sync after refresh.
    running = UDP service active, discoverable = LAN Visible, is_scanning = Scan toggle ON."""
    manager = get_network_manager()
    return {
        "running": manager.discovery.running,
        "discoverable": manager.is_discoverable,
        "is_scanning": manager.is_scanning,
    }


@app.post("/api/network/lan/start")
async def start_lan_discovery():
    """Start UDP listener (used when LAN Visible is enabled). Listener runs and responds to discover regardless of Scan."""
    manager = get_network_manager()
    await manager.start()
    return {"status": "started"}


@app.post("/api/network/lan/stop")
async def stop_lan_discovery():
    """Stop LAN discovery."""
    manager = get_network_manager()
    await manager.stop()
    return {"status": "stopped"}


@app.get("/api/network/lan/discoverable")
async def get_lan_discoverable():
    """Get whether this node is discoverable on LAN."""
    manager = get_network_manager()
    return {"discoverable": manager.is_discoverable}


class DiscoverableRequest(BaseModel):
    discoverable: bool


@app.post("/api/network/lan/discoverable")
async def set_lan_discoverable(request: DiscoverableRequest):
    """LAN Visible: when ON, UDP listener runs and responds to discover requests (independent of Scan)."""
    manager = get_network_manager()
    if request.discoverable and not manager.discovery.running:
        if not manager.config.enabled:
            raise HTTPException(status_code=400, detail="LAN discovery is disabled by configuration")
        logger.info("LAN Visible enabled; starting UDP listener")
        await manager.start()
    manager.set_discoverable(request.discoverable)
    return {"discoverable": manager.is_discoverable}


@app.get("/api/network/lan/nodes")
async def get_lan_nodes():
    """Get list of discovered LAN nodes."""
    manager = get_network_manager()
    lan_nodes = [
        node.model_dump() for node in manager.discovery.known_nodes.values()
    ]
    return {"nodes": lan_nodes}


@app.post("/api/network/lan/broadcast")
async def trigger_lan_broadcast():
    """Manually trigger a LAN scan broadcast (discover request) to find visible nodes."""
    manager = get_network_manager()
    if not manager.discovery.running:
        return JSONResponse(
            status_code=400,
            content={"status": "not_running", "message": "Discovery is not running. Start LAN discovery first."},
        )
    sent = await manager.discovery.discover_once()
    if sent:
        return {"status": "broadcasted", "type": "discover"}
    return {"status": "no_route", "message": "Discover request could not be sent (no route to host). Discovery is listening for responses."}


@app.post("/api/network/lan/scan/start")
async def start_lan_scan():
    """Enable Scan: actively send discover broadcasts. UDP listener runs when LAN Visible is on (independent)."""
    manager = get_network_manager()
    if not manager.discovery.running:
        await manager.start()
    manager.set_scanning(True)
    manager.set_discoverable(True)
    discover_sent = await manager.discovery.discover_once()
    return {"status": "scanning", "is_scanning": True, "discover_sent": discover_sent}


@app.post("/api/network/lan/scan/stop")
async def stop_lan_scan():
    """Disable active LAN Scan (Scan toggle OFF).

    Sets is_scanning=False. Stops the UDP service only when LAN Visible is
    also off — if LAN Visible is still on, the UDP listener must keep running
    so this node remains discoverable by others.
    """
    manager = get_network_manager()
    manager.set_scanning(False)
    # Only tear down the UDP service if we no longer need it for LAN Visible either
    if not manager.is_discoverable:
        await manager.stop()
    return {"status": "stopped", "is_scanning": False}


class ConnectRequest(BaseModel):
    ip: str
    port: int
    password: str | None = None


class ProbeRequest(BaseModel):
    ip: str
    port: int


@app.post("/api/network/probe")
async def probe_remote_node(request: ProbeRequest):
    """Probe a remote node to get its info before connecting.

    Returns node name, capabilities, status, wallet, agent_id, etc.
    """
    import httpx

    url = f"http://{request.ip}:{request.port}/api/info"
    try:
        async with httpx.AsyncClient(timeout=config.connection.peer_info_timeout) as client:
            response = await client.get(url)

            if response.status_code != 200:
                raise HTTPException(status_code=400, detail="Node not reachable")

            data = response.json()

            return {
                "name": data.get("name", f"Node@{request.ip}"),
                "capability": data.get("capability", "General Purpose"),
                "capabilities": data.get("capabilities", []),
                "status": data.get("status", "online"),
                "version": data.get("version"),
                "ip": request.ip,
                "port": request.port,
                # Extended info
                "walletAddress": data.get("wallet_address"),
                "agentId": data.get("agent_id"),
                "description": data.get("description"),
                "price": data.get("price"),
                "region": data.get("region"),
            }
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=408, detail="Connection timeout") from e
    except httpx.ConnectError as e:
        raise HTTPException(status_code=503, detail="Node not reachable") from e
    except Exception as e:
        logger.warning(f"Failed to probe {request.ip}:{request.port}: {e}")
        raise HTTPException(status_code=400, detail=str(e)) from e


class VerifyNodeRequest(BaseModel):
    node_id: str


@app.post("/api/network/verify")
async def verify_marketplace_node(request: VerifyNodeRequest):
    """
    Verify a node found via marketplace search.

    1. Lookup node by ID from central service to get IP/port
    2. Send direct probe to verify reachability and status

    Returns verified node info or error.
    """
    central = get_central_client()

    # Step 1: Get node info from central
    if not central.is_configured:
        raise HTTPException(status_code=400, detail="AgentaNet token not configured")

    try:
        node = await central.get_node(request.node_id)
        if not node:
            raise HTTPException(status_code=404, detail=f"Node {request.node_id} not found in marketplace")
    except Exception as e:
        logger.warning(f"Failed to lookup node {request.node_id}: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to lookup node: {e}") from e

    # Step 2: Direct probe
    ip = node.get("ip")
    port = node.get("port")

    if not ip or not port:
        return {
            "verified": False,
            "node": node,
            "error": "Node has no IP/port information",
        }

    import httpx as _httpx
    try:
        async with _httpx.AsyncClient(timeout=config.connection.peer_info_timeout) as client:
            url = f"http://{ip}:{port}/api/info"
            response = await client.get(url)

            if response.status_code != 200:
                return {
                    "verified": False,
                    "node": node,
                    "error": f"Probe failed: status {response.status_code}",
                }

            probe_data = response.json()

            return {
                "verified": True,
                "node": {
                    **node,
                    "probe_status": "online",
                    "probe_name": probe_data.get("name"),
                    "probe_capabilities": probe_data.get("capabilities"),
                    "probe_version": probe_data.get("version"),
                },
                "error": None,
            }
    except _httpx.TimeoutException:
        logger.warning("Node verification timeout for %s:%s", node.get("ip"), node.get("port"), exc_info=True)
        return {
            "verified": False,
            "node": node,
            "error": "Connection timeout",
        }
    except _httpx.ConnectError:
        logger.warning("Node verification connect error for %s:%s", node.get("ip"), node.get("port"), exc_info=True)
        return {
            "verified": False,
            "node": node,
            "error": "Node not reachable",
        }
    except Exception as e:
        logger.warning("Node verification failed for %s:%s: %s", node.get("ip"), node.get("port"), e, exc_info=True)
        return {
            "verified": False,
            "node": node,
            "error": str(e),
        }


@app.get("/api/info")
async def get_node_info():
    """Return this node's info for remote probing."""
    ln = config.local_node
    return {
        "an_id": ln.an_id,                # Canonical unique identifier
        "name": ln.name,                   # Human-readable display name
        "wallet_address": ln.wallet_address,
        "capability": ln.capability or "General Purpose",
        "capabilities": [
            {"name": "task_execution", "description": "Execute distributed tasks"},
            {"name": "agent_hosting", "description": "Host AI agents"},
            {"name": "code_execution", "description": "Run code in sandboxed environment"},
        ],
        "status": "online",
        "version": "0.1.0",
        "agent_id": ln.an_id,
        "description": ln.description or "A Teaming24 Agentic Node ready for distributed tasks",
        "price": "Free",
        "region": ln.region or "Local",
    }


@app.post("/api/network/connect")
async def connect_node(request: ConnectRequest):
    """Connect to a remote node (LAN or WAN — same handshake protocol).

    If the target is unreachable, the request fails with a clear error.
    If the target is already an inbound peer but unreachable outbound,
    the request is rejected — bidirectional requires mutual reachability.
    """
    manager = get_network_manager()
    try:
        node = await manager.connect_node(request.ip, request.port, request.password or "")
        return node
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class DisconnectRequest(BaseModel):
    ip: str
    port: int


@app.post("/api/network/disconnect")
async def disconnect_node(request: DisconnectRequest):
    """Send disconnect notification to a remote node."""
    import httpx

    url = f"http://{request.ip}:{request.port}/api/network/peer-disconnect"
    try:
        manager = get_network_manager()
        async with httpx.AsyncClient(timeout=config.connection.connect_node_timeout) as client:
            # Send disconnect notification
            await client.post(url, json={
                "nodeId": manager.local_node.id,
                "reason": "user_initiated"
            })
        # Stop tracking as an active outbound peer
        manager.disconnect_node_by_endpoint(request.ip, request.port)
        await _subscription_manager.broadcast(
            "node_status_changed",
            {"nodeId": None, "status": "offline", "ip": request.ip, "port": request.port},
        )
        return {"status": "disconnected", "ip": request.ip, "port": request.port}
    except Exception as e:
        # Even if notification fails, we consider it disconnected
        logger.warning(f"Failed to notify {request.ip}:{request.port} of disconnect: {e}")
        try:
            manager = get_network_manager()
            manager.disconnect_node_by_endpoint(request.ip, request.port)
        except Exception as e:
            logger.debug(f"Failed to disconnect node by endpoint {request.ip}:{request.port}: {e}")
            pass
        return {"status": "disconnected", "ip": request.ip, "port": request.port, "notified": False}


@app.post("/api/network/peer-disconnect")
async def handle_peer_disconnect(request: dict):
    """Handle disconnect notification from a remote peer."""
    node_id = request.get("nodeId")
    reason = request.get("reason", "unknown")

    logger.info(f"Received disconnect notification from {node_id}, reason: {reason}")

    # Remove inbound peer tracking (if present) and include metadata for session recording.
    peer_snapshot = None
    connected_since = None
    if node_id:
        manager = get_network_manager()
        peer_snapshot = manager.inbound_peers.get(node_id)
        connected_since = _inbound_connected_since.pop(node_id, None)
        manager.remove_inbound_peer(node_id)
        manager.mark_node_offline(node_id)

    # Broadcast disconnect event to all local subscribers
    await _subscription_manager.broadcast("peer_disconnected", {
        "nodeId": node_id,
        "reason": reason,
        "connected_since": connected_since,
        "peer": peer_snapshot.model_dump() if peer_snapshot else None,
        "timestamp": time.time()
    })

    return {"status": "acknowledged"}


@app.get("/api/network/events")
async def network_events_stream():
    """Persistent SSE endpoint for network events."""
    return StreamingResponse(
        _subscription_manager.subscribe(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/api/network/nodes")
async def list_nodes():
    """List all known nodes."""
    manager = get_network_manager()
    return {"nodes": manager.get_nodes()}


@app.get("/api/network/links")
async def get_network_links():
    """Return which peers this node currently considers linked/connected."""
    manager = get_network_manager()
    peer_ids = set()
    peer_ids.update(manager.wan_nodes.keys())
    peer_ids.update(manager.inbound_peers.keys())
    return {"node_id": manager.local_node.id, "peers": sorted(peer_ids)}


@app.get("/api/network/outbound")
async def list_outbound_peers():
    """Return full info for all outbound-connected peers (nodes we connected to).

    This lets the frontend restore the "Connected Nodes" list when
    a new browser tab is opened—even though the Zustand store only persists
    history, the backend still holds live connections in memory.
    """
    manager = get_network_manager()
    peers = []
    for peer in manager.wan_nodes.values():
        peers.append({
            "node": peer.model_dump(),
            "connected_since": getattr(peer, "connected_since", None),
        })
    return {"peers": peers}


# Marketplace listings storage: node_id -> listing info
# In production this mirrors central registration state and local fallback state.
_marketplace_listings: dict[str, dict[str, Any]] = {}
_MARKETPLACE_FALLBACK_REGION = "Local"
_MARKETPLACE_DESCRIPTION_MAX_CHARS = 320


def _is_central_marketplace_enabled() -> bool:
    """Whether central marketplace integration is enabled by config."""
    return bool(getattr(config.agentanet_central, "enabled", True))


def _central_link_required_message() -> str:
    """User-facing guidance when central URL/token is not linked yet."""
    return (
        "AgentaNet Central is enabled but not linked. "
        "Create a token in Central, then set `agentanetCentralUrl` and `agentanetToken` in Teaming24 Settings."
    )


def _local_marketplace_node_id() -> str:
    """Return stable local node id used for marketplace listing state."""
    return str(getattr(config.local_node, "an_id", "") or getattr(config.local_node, "name", "local-node"))


def _normalize_listing_capabilities(raw_caps: list[Any] | None) -> tuple[list[dict[str, str]], list[str]]:
    """Normalize capability payload to both detailed and name-only forms."""
    normalized: list[dict[str, str]] = []
    names: list[str] = []
    seen: set[str] = set()

    for item in raw_caps or []:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            description = str(item.get("description", "")).strip()
        else:
            name = str(item).strip()
            description = ""

        if not name:
            continue
        lowered = name.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append({"name": name, "description": description})
        names.append(name)

    return normalized, names


def _build_marketplace_description(
    description: str,
    primary_capability: str,
    capabilities: list[dict[str, str]],
) -> str:
    """Ensure listing description is concise and non-empty."""
    clean = (description or "").strip()
    if clean:
        return clean[:_MARKETPLACE_DESCRIPTION_MAX_CHARS]

    cap_names = [c.get("name", "").strip() for c in capabilities if c.get("name")]
    if primary_capability and primary_capability not in cap_names:
        cap_names.insert(0, primary_capability)
    cap_names = cap_names[:4]
    if cap_names:
        return (
            "Agentic Node specialized in "
            + cap_names[0]
            + ("; additional capabilities: " + ", ".join(cap_names[1:]) if len(cap_names) > 1 else "")
        )[:_MARKETPLACE_DESCRIPTION_MAX_CHARS]
    return "Agentic Node available for distributed task execution."


def _build_marketplace_listing_payload(request: "MarketplaceListingRequest") -> dict[str, Any]:
    """Build normalized listing payload from request body."""
    normalized_caps, cap_names = _normalize_listing_capabilities(request.capabilities)
    capability = str(request.capability or "").strip()
    if not capability and cap_names:
        capability = cap_names[0]
    if capability and capability.lower() not in {n.lower() for n in cap_names}:
        normalized_caps.insert(0, {"name": capability, "description": ""})
        cap_names.insert(0, capability)
    capability = capability or "General Purpose"
    return {
        "name": str(request.name or "").strip(),
        "description": _build_marketplace_description(
            description=request.description,
            primary_capability=capability,
            capabilities=normalized_caps,
        ),
        "capability": capability,
        "capabilities": normalized_caps,
        "capability_names": cap_names,
        "price": str(request.price or "").strip() or "Free",
    }


def _compose_local_marketplace_node(node_id: str, listing: dict[str, Any]) -> dict[str, Any]:
    """Convert local listing state into marketplace node payload."""
    return {
        "id": node_id,
        "an_id": listing.get("an_id") or getattr(config.local_node, "an_id", None),
        "wallet_address": listing.get("wallet_address") or getattr(config.local_node, "wallet_address", None),
        "name": listing.get("name", "Unknown"),
        "description": listing.get("description", ""),
        "capability": listing.get("capability", "General"),
        "capabilities": listing.get("capabilities", []),
        "price": listing.get("price", "Free"),
        "status": listing.get("status", "online"),
        "ip": listing.get("ip"),
        "port": listing.get("port"),
        "region": listing.get("region") or _MARKETPLACE_FALLBACK_REGION,
        "isLocal": True,
    }


def _filter_local_marketplace_nodes(search: str | None, capability: str | None) -> list[dict[str, Any]]:
    """Filter local fallback listings with same semantics as central search."""
    search_q = (search or "").strip().lower()
    cap_q = (capability or "").strip().lower()
    nodes: list[dict[str, Any]] = []
    for node_id, listing in _marketplace_listings.items():
        node = _compose_local_marketplace_node(node_id, listing)
        cap_names = ",".join(
            str(c.get("name", "")).strip()
            for c in node.get("capabilities", [])
            if isinstance(c, dict)
        )
        haystack = " ".join(
            str(node.get(k, "") or "")
            for k in ("id", "name", "description", "capability", "price", "region")
        ).lower() + f" {cap_names.lower()}"
        if search_q and search_q not in haystack:
            continue
        if cap_q:
            primary = str(node.get("capability", "") or "").lower()
            caps_joined = " ".join(
                str(c.get("name", "")).strip().lower()
                for c in (node.get("capabilities") or [])
                if isinstance(c, dict)
            )
            if cap_q not in primary and cap_q not in caps_joined:
                continue
        nodes.append(node)
    return nodes


def _local_marketplace_endpoints() -> set[tuple[str, int]]:
    """Return local endpoints used to identify self in marketplace results."""
    port = int(getattr(config.local_node, "port", 0) or config.server.port or 0)
    hosts = {
        str(getattr(config.local_node, "host", "") or "").strip(),
        _detect_local_advertise_ip(),
        "127.0.0.1",
        "localhost",
        "0.0.0.0",
    }
    return {(h, port) for h in hosts if h and port > 0}


def _local_marketplace_ids() -> set[str]:
    """Return local node IDs used in marketplace payloads."""
    return {
        str(_local_marketplace_node_id()).strip(),
        str(getattr(config.local_node, "an_id", "") or "").strip(),
    }


def _is_self_marketplace_node(
    node: dict[str, Any],
    local_ids: set[str] | None = None,
    local_endpoints: set[tuple[str, int]] | None = None,
) -> bool:
    """Whether marketplace node payload points to this local node."""
    local_ids = local_ids or _local_marketplace_ids()
    local_endpoints = local_endpoints or _local_marketplace_endpoints()

    node_id = str(node.get("id", "") or "").strip()
    if node_id and node_id in local_ids:
        return True

    for k in ("an_id", "agent_id", "node_id"):
        v = str(node.get(k, "") or "").strip()
        if v and v in local_ids:
            return True

    node_ip = str(node.get("ip", "") or "").strip()
    node_port_raw = node.get("port", 0)
    try:
        node_port = int(node_port_raw)
    except (TypeError, ValueError):
        node_port = 0

    if node_ip and node_port and (node_ip, node_port) in local_endpoints:
        return True

    local_wallet = str(getattr(config.local_node, "wallet_address", "") or "").strip().lower()
    node_wallet = str(node.get("wallet_address", "") or "").strip().lower()
    if local_wallet and node_wallet and local_wallet == node_wallet:
        return True

    return False


def _exclude_self_marketplace_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop local node entries so marketplace only shows remote ANs."""
    local_ids = _local_marketplace_ids()
    local_endpoints = _local_marketplace_endpoints()
    return [
        n
        for n in nodes
        if not _is_self_marketplace_node(
            n,
            local_ids=local_ids,
            local_endpoints=local_endpoints,
        )
    ]


def _marketplace_node_identity_tokens(node: dict[str, Any]) -> set[str]:
    """Build identity tokens to dedupe marketplace/LAN/WAN node duplicates."""
    tokens: set[str] = set()

    for key in ("id", "an_id", "agent_id", "node_id"):
        val = str(node.get(key, "") or "").strip()
        if val:
            tokens.add(f"{key}:{val}")

    wallet = str(node.get("wallet_address", "") or "").strip().lower()
    if wallet:
        tokens.add(f"wallet:{wallet}")

    ip = str(node.get("ip", "") or "").strip().lower()
    try:
        port = int(node.get("port", 0) or 0)
    except (TypeError, ValueError):
        port = 0
    if ip and port > 0:
        tokens.add(f"endpoint:{ip}:{port}")

    return tokens


def _dedupe_marketplace_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate nodes across IDs, wallet, and endpoint identity."""
    deduped: list[dict[str, Any]] = []
    seen_tokens: set[str] = set()
    for node in _exclude_self_marketplace_nodes(nodes):
        tokens = _marketplace_node_identity_tokens(node)
        if not tokens:
            continue
        if tokens & seen_tokens:
            continue
        seen_tokens.update(tokens)
        deduped.append(node)
    return deduped


def _get_marketplace_nodes_for_workforce_pool() -> list[dict[str, Any]]:
    """Load deduped marketplace AN snapshots for ANRouter pool augmentation."""
    try:
        db = get_database()
        ttl = max(0.0, float(config.agentanet_central.marketplace_cache_ttl))
        cached_nodes = db.get_marketplace_cache_nodes(
            max_age_seconds=ttl if ttl > 0 else None
        )
        deduped = _dedupe_marketplace_nodes(cached_nodes)
        normalized: list[dict[str, Any]] = []
        for node in deduped:
            item = dict(node)
            if not str(item.get("type", "") or "").strip():
                item["type"] = "marketplace"
            normalized.append(item)
        return normalized
    except Exception as exc:
        logger.warning("Failed to load marketplace nodes for workforce pool: %s", exc, exc_info=True)
        return []


def _build_agentic_node_workforce_pool(
    crew: Any,
    network_manager: NetworkManager | None,
) -> AgenticNodeWorkforcePool:
    """Create AN workforce pool with connected + marketplace nodes."""
    return AgenticNodeWorkforcePool(
        crew,
        network_manager,
        extra_remote_nodes_provider=_get_marketplace_nodes_for_workforce_pool,
    )


def _set_local_marketplace_listing(db: Any, node_id: str, listing: dict[str, Any]) -> None:
    """Persist current local listing snapshot in-memory and in DB cache."""
    _marketplace_listings.clear()
    _marketplace_listings[node_id] = listing
    db.upsert_marketplace_cache_nodes([_compose_local_marketplace_node(node_id, listing)])


def _clear_local_marketplace_listings(db: Any) -> None:
    """Clear local listing state and corresponding cache rows."""
    existing_ids = list(_marketplace_listings.keys())
    for listing_id in existing_ids:
        db.remove_marketplace_cache_node(listing_id)
    _marketplace_listings.clear()
    # Defensive cleanup for legacy local-id keyed rows.
    db.remove_marketplace_cache_node(_local_marketplace_node_id())


def _first_local_marketplace_listing() -> tuple[str | None, dict[str, Any] | None]:
    """Return the single local listing entry if present."""
    for listing_id, listing in _marketplace_listings.items():
        return listing_id, listing
    return None, None


def _detect_local_advertise_ip() -> str:
    """Detect a routable local IPv4 for marketplace registration."""
    import socket

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        logger.debug("Could not determine local advertise IP, using 127.0.0.1")
        return "127.0.0.1"


class MarketplaceListingRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field("", max_length=1024)
    capability: str = Field("", max_length=128)
    price: str = Field("Free", min_length=1, max_length=128)
    capabilities: list[Any] = Field(default_factory=list)


@app.get("/api/network/marketplace")
async def get_marketplace(search: str | None = None, capability: str | None = None):
    """Return marketplace nodes, preferring central and falling back to cache/local."""
    central = get_central_client()
    db = get_database()
    central_enabled = _is_central_marketplace_enabled()

    if central_enabled and central.has_base_url:
        try:
            remote_nodes = await central.search_nodes(
                search=search,
                capability=capability,
                raise_on_error=True,
            )
            remote_nodes = _exclude_self_marketplace_nodes(remote_nodes)
            db.upsert_marketplace_cache_nodes(remote_nodes)
            logger.debug("Marketplace fetched from central: %d nodes", len(remote_nodes))
            return {"nodes": _dedupe_marketplace_nodes(remote_nodes), "source": "central"}
        except Exception as e:
            logger.warning(f"Failed to fetch marketplace from central: {e}")
            cache_ttl = max(0.0, float(config.agentanet_central.marketplace_cache_ttl))
            remote_nodes = db.get_marketplace_cache_nodes(max_age_seconds=cache_ttl)
            remote_nodes = _exclude_self_marketplace_nodes(remote_nodes)
            local_nodes = _filter_local_marketplace_nodes(search=search, capability=capability)
            source = "cache" if remote_nodes else "local"
            return {"nodes": _dedupe_marketplace_nodes(local_nodes + remote_nodes), "source": source}

    if central_enabled:
        return {
            "nodes": [],
            "source": "central_unlinked",
            "reason": "central_token_not_linked",
            "detail": _central_link_required_message(),
        }

    local_nodes = _exclude_self_marketplace_nodes(
        _filter_local_marketplace_nodes(search=search, capability=capability)
    )
    return {"nodes": local_nodes, "source": "local"}


@app.post("/api/network/marketplace/join")
async def join_marketplace(request: MarketplaceListingRequest):
    """Register local node on marketplace (central service if configured)."""
    central = get_central_client()
    db = get_database()
    central_enabled = _is_central_marketplace_enabled()

    if central_enabled and not central.is_configured:
        raise HTTPException(
            status_code=409,
            detail=_central_link_required_message(),
        )

    fallback_node_id = _local_marketplace_node_id()
    listing = _build_marketplace_listing_payload(request)

    local_ip = _detect_local_advertise_ip()
    local_port = int(getattr(config.local_node, "port", 0) or config.server.port or 0)
    if not (1 <= local_port <= 65535):
        local_port = 8000
    listing.update(
        {
            "ip": local_ip,
            "port": local_port,
            "region": getattr(config.local_node, "region", _MARKETPLACE_FALLBACK_REGION),
            "an_id": str(getattr(config.local_node, "an_id", "") or "").strip(),
            "wallet_address": str(getattr(config.local_node, "wallet_address", "") or "").strip(),
            "status": "online",
            "updated_at": time.time(),
        }
    )

    if central_enabled and central.is_configured:
        try:
            result = await central.register(
                name=listing["name"],
                description=listing["description"],
                capability=listing["capability"],
                capabilities=listing["capabilities"],
                an_id=listing.get("an_id"),
                wallet_address=listing.get("wallet_address"),
                price=listing["price"],
                ip=local_ip,
                port=local_port,
                region=config.local_node.region,
            )

            await central.start_heartbeat_loop()
            registered_node_id = str(result.get("id") or fallback_node_id)
            _set_local_marketplace_listing(db, registered_node_id, listing)
            logger.info(f"Node registered on central marketplace: {registered_node_id}")

            return {
                "status": "listed",
                "node_id": registered_node_id,
                "ip": local_ip,
                "port": local_port,
                "central": True,
                "listing": listing,
            }
        except Exception as e:
            logger.warning(f"Failed to register with central: {e}")
            detail = str(e)
            resp = getattr(e, "response", None)
            if resp is not None:
                try:
                    body = resp.json()
                    if isinstance(body, dict) and body.get("detail"):
                        d = body["detail"]
                        if isinstance(d, list):
                            parts = [f"{x.get('loc', [])}: {x.get('msg', '')}" for x in d if isinstance(x, dict)]
                            detail = "; ".join(parts) if parts else str(d)
                        else:
                            detail = str(d)
                        logger.warning("Central marketplace 422/4xx: %s", body)
                except Exception:
                    pass
            raise HTTPException(status_code=400, detail=f"Failed to register: {detail}") from e

    _set_local_marketplace_listing(db, fallback_node_id, listing)
    logger.info(f"Node {config.local_node.name} joined local marketplace as {fallback_node_id}")

    return {
        "status": "listed",
        "node_id": fallback_node_id,
        "ip": local_ip,
        "port": local_port,
        "central": False,
        "listing": listing,
    }


@app.post("/api/network/marketplace/leave")
async def leave_marketplace():
    """Remove local node from marketplace."""
    central = get_central_client()
    db = get_database()
    removed = len(_marketplace_listings)

    # If this Teaming24 instance is linked to Central, keep Central listing state in sync
    # even when local fallback mode is enabled/disabled.
    should_unlist_central = central.is_configured
    if should_unlist_central:
        try:
            ok = await central.unlist(raise_on_error=True)
            if not ok:
                raise RuntimeError("Central unlist returned unsuccessful result")
            await central.stop_heartbeat_loop()
        except Exception as e:
            logger.warning(f"Failed to unlist from central: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to unlist from central: {e}") from e

    _clear_local_marketplace_listings(db)
    logger.info("Removed marketplace listing for local node")

    return {"status": "unlisted", "removed": removed}


@app.post("/api/network/marketplace/update")
async def update_marketplace_listing(request: MarketplaceListingRequest):
    """Update local node marketplace listing (central if configured, else local fallback)."""
    central = get_central_client()
    db = get_database()
    existing_node_id, _ = _first_local_marketplace_listing()
    listing = _build_marketplace_listing_payload(request)

    central_enabled = _is_central_marketplace_enabled()
    if central_enabled and not central.is_configured:
        raise HTTPException(status_code=409, detail=_central_link_required_message())

    if not central_enabled and not central.is_configured and not existing_node_id:
        raise HTTPException(status_code=404, detail="No active marketplace listing found")

    _raw_port = getattr(config.local_node, "port", 0) or config.server.port or 0
    _port = int(_raw_port) if _raw_port else 8000
    if not (1 <= _port <= 65535):
        _port = 8000
    listing.update(
        {
            "ip": _detect_local_advertise_ip(),
            "port": _port,
            "region": getattr(config.local_node, "region", _MARKETPLACE_FALLBACK_REGION),
            "an_id": str(getattr(config.local_node, "an_id", "") or "").strip(),
            "wallet_address": str(getattr(config.local_node, "wallet_address", "") or "").strip(),
            "status": "online",
            "updated_at": time.time(),
        }
    )

    node_id = existing_node_id or _local_marketplace_node_id()
    if central_enabled and central.is_configured:
        try:
            result = await central.register(
                name=listing["name"],
                description=listing["description"],
                capability=listing["capability"],
                capabilities=listing["capabilities"],
                an_id=listing.get("an_id"),
                wallet_address=listing.get("wallet_address"),
                price=listing["price"],
                ip=listing["ip"],
                port=listing["port"],
                region=listing["region"],
            )
            node_id = str(result.get("id") or node_id)
            await central.start_heartbeat_loop()
        except Exception as e:
            logger.warning(f"Failed to update central marketplace listing: {e}")
            detail = str(e)
            resp = getattr(e, "response", None)
            if resp is not None:
                try:
                    body = resp.json()
                    if isinstance(body, dict) and body.get("detail"):
                        detail = str(body["detail"])
                except Exception:
                    pass
            raise HTTPException(status_code=400, detail=f"Failed to update listing: {detail}") from e

    _set_local_marketplace_listing(db, node_id, listing)
    logger.info(f"Updated marketplace listing {node_id}")
    return {"status": "updated", "node_id": node_id, "listing": listing}


@app.get("/api/network/marketplace/status")
async def get_marketplace_status():
    """Check local node listing status, preferring authoritative central state."""
    central = get_central_client()
    db = get_database()
    local_node_id, local_listing = _first_local_marketplace_listing()
    central_enabled = _is_central_marketplace_enabled()

    if central_enabled and not central.is_configured:
        if local_listing:
            _clear_local_marketplace_listings(db)
        return {
            "listed": False,
            "node_id": None,
            "listing": None,
            "central": True,
            "central_enabled": True,
            "central_configured": False,
            "reason": "central_token_not_linked",
            "detail": _central_link_required_message(),
        }

    if central_enabled and central.is_configured:
        try:
            central_node = await central.get_my_node(raise_on_error=True)
            if central_node:
                await central.start_heartbeat_loop()
                capabilities, _ = _normalize_listing_capabilities(central_node.get("capabilities"))
                listing = {
                    "name": central_node.get("name", getattr(config.local_node, "name", "Local Node")),
                    "description": _build_marketplace_description(
                        description=str(central_node.get("description") or ""),
                        primary_capability=str(central_node.get("capability") or ""),
                        capabilities=capabilities,
                    ),
                    "capability": (
                        str(central_node.get("capability") or "").strip()
                        or (capabilities[0]["name"] if capabilities else "General Purpose")
                    ),
                    "capabilities": capabilities,
                    "capability_names": [c["name"] for c in capabilities],
                    "an_id": central_node.get("an_id"),
                    "wallet_address": central_node.get("wallet_address"),
                    "price": central_node.get("price", "Free"),
                    "ip": central_node.get("ip"),
                    "port": central_node.get("port"),
                    "region": central_node.get("region") or _MARKETPLACE_FALLBACK_REGION,
                    "status": central_node.get("status", "online"),
                    "updated_at": time.time(),
                }
                central_node_id = str(central_node.get("id") or _local_marketplace_node_id())
                _set_local_marketplace_listing(db, central_node_id, listing)
                return {
                    "listed": True,
                    "node_id": central_node_id,
                    "listing": listing,
                    "central": True,
                    "central_enabled": True,
                    "central_configured": True,
                }

            # Central is reachable and says this token is not listed.
            _clear_local_marketplace_listings(db)
            return {
                "listed": False,
                "node_id": None,
                "listing": None,
                "central": True,
                "central_enabled": True,
                "central_configured": True,
            }
        except Exception as e:
            logger.warning(f"Failed to get marketplace status from central: {e}")

    if local_listing:
        return {
            "listed": True,
            "node_id": local_node_id,
            "listing": local_listing,
            "central": central_enabled and central.is_configured,
            "central_enabled": central_enabled,
            "central_configured": central.is_configured,
        }

    return {
        "listed": False,
        "node_id": None,
        "listing": None,
        "central": central_enabled and central.is_configured,
        "central_enabled": central_enabled,
        "central_configured": central.is_configured,
    }


@app.get("/api/network/marketplace/node/{node_id}")
async def get_marketplace_node(node_id: str):
    """Get specific node from marketplace by node_id."""
    central = get_central_client()
    db = get_database()

    if central.is_configured:
        try:
            node = await central.get_node(node_id)
            if node:
                return {"found": True, "node": node}
        except Exception as e:
            logger.warning(f"Failed to get node {node_id}: {e}")

    if node_id in _marketplace_listings:
        return {"found": True, "node": _compose_local_marketplace_node(node_id, _marketplace_listings[node_id])}

    for cached in db.get_marketplace_cache_nodes(limit=1000):
        if str(cached.get("id", "")).strip() == node_id:
            return {"found": True, "node": cached}

    return {"found": False, "node": None}


# ============================================================================
# Database API - Persistent Storage
# ============================================================================


class SettingUpdate(BaseModel):
    value: Any


def get_agent_runtime_settings() -> dict:
    """Get runtime settings from DB helper to avoid divergence across modules."""
    try:
        from teaming24.api.routes.db import get_agent_runtime_settings as _db_runtime_settings
        return _db_runtime_settings()
    except Exception as exc:
        logger.warning("Failed to get agent runtime settings: %s", exc, exc_info=True)
        return {}


# ============================================================================
# Human-in-the-Loop Approval API
# ============================================================================

_approval_requests: dict = _st.approval_requests   # id → {request, event, decision, ...}
_approval_lock = _st.approval_lock
# task_id -> {"budget": float, "spent": float} for budget-based auto-approve
_task_budgets: dict = _st.task_budgets


class ApprovalRequest(BaseModel):
    task_id: str | None = None
    approval_type: str  # "routing", "payment", "execution"
    title: str
    description: str
    options: list       # [{id, label, style?}]
    metadata: dict = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    decision: str       # option id chosen by user
    budget: float | None = None  # optional budget (USDC) for routing approval


@app.post("/api/agent/tasks/{task_id}/approval")
async def request_approval(task_id: str, req: ApprovalRequest):
    """Create an approval request. Backend calls this internally,
    frontend receives it via SSE and responds via resolve endpoint."""
    if req.task_id and req.task_id != task_id:
        raise HTTPException(
            status_code=400,
            detail=f"task_id mismatch: path={task_id}, body={req.task_id}",
        )
    approval_id, _ = _approval_service.create_approval(
        task_id=task_id,
        approval_type=req.approval_type,
        title=req.title,
        description=req.description,
        options=req.options,
        metadata=req.metadata,
    )
    return {"approval_id": approval_id}


@app.get("/api/agent/tasks/{task_id}/approvals/pending")
async def get_pending_approvals(task_id: str):
    """Query any pending (unresolved) approval requests for a task."""
    with _approval_lock:
        pending = [
            {
                "id": v["id"],
                "task_id": v["task_id"],
                "type": v["type"],
                "title": v["title"],
                "description": v["description"],
                "options": v["options"],
                "metadata": v.get("metadata", {}),
            }
            for v in _approval_requests.values()
            if v.get("task_id") == task_id and v.get("decision") is None
        ]
    return {"approvals": pending}


@app.post("/api/agent/approvals/{approval_id}/resolve")
async def resolve_approval(approval_id: str, body: ApprovalDecision):
    """User resolves an approval request."""
    with _approval_lock:
        record = _approval_requests.get(approval_id)
        if not record:
            raise HTTPException(status_code=404, detail="Approval not found")
        options = record.get("options") or []
        option_ids: set[str] = set()
        for opt in options:
            if isinstance(opt, dict):
                oid = opt.get("id")
                if isinstance(oid, str) and oid:
                    option_ids.add(oid)
            elif isinstance(opt, str) and opt:
                option_ids.add(opt)

        if option_ids and body.decision not in option_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid decision '{body.decision}', expected one of {sorted(option_ids)}",
            )
        if body.budget is not None and body.budget < 0:
            raise HTTPException(status_code=400, detail="budget must be non-negative")
        record["decision"] = body.decision
        record["budget"] = body.budget
        record["resolved_at"] = time.time()
        evt = record.get("event")
        if evt is not None:
            evt.set()
    await _subscription_manager.broadcast("approval_resolved", {
        "approval_id": approval_id,
        "decision": body.decision,
    })
    return {"status": "resolved", "decision": body.decision}


# ============================================================================
# Task Persistence API
# ============================================================================

# ============================================================================
# Chat Persistence API
# ============================================================================

class SecurityUpdate(BaseModel):
    password: str


class HandshakeRequest(BaseModel):
    password: str | None = None
    peer: dict | None = None


class PeerHello(BaseModel):
    id: str
    name: str
    port: int
    role: str = "worker"
    capability: str | None = None
    capabilities: list | None = None
    wallet_address: str | None = None
    agent_id: str | None = None
    description: str | None = None
    region: str | None = None


@app.post("/api/network/handshake")
async def network_handshake(request: HandshakeRequest, http_request: Request):
    """Handshake endpoint for remote nodes."""
    # Verify password if set in config
    expected_password = config.security.connection_password
    if expected_password and request.password != expected_password:
        raise HTTPException(status_code=401, detail="Invalid password")

    manager = get_network_manager()
    peer_registered = False
    # Register inbound peer if provided
    if request.peer:
        try:
            peer = PeerHello.model_validate(request.peer)
            peer_ip = http_request.client.host if http_request.client else "unknown"
            logger.info(f"Handshake from peer: id={peer.id}, name={peer.name}, ip={peer_ip}:{peer.port}")
            peer_node = NodeInfo(
                id=peer.id,
                name=peer.name,
                ip=peer_ip,
                port=peer.port,
                role=peer.role,
                status="online",
                type="wan",
                capability=peer.capability,
                capabilities=peer.capabilities,
                wallet_address=peer.wallet_address,
                agent_id=peer.agent_id,
                description=peer.description,
                region=peer.region,
            )
            manager.register_inbound_peer(peer_node)
            now = time.time()
            # Always update: acts as a heartbeat / "last seen" timestamp.
            # Re-handshakes happen when LAN discovery re-broadcasts, so this
            # resets the health-check grace timer automatically.
            _inbound_connected_since[peer_node.id] = now
            # Also reset failure counter on re-handshake
            _peer_failure_counts.pop(f"inbound:{peer_node.ip}:{peer_node.port}", None)
            await _subscription_manager.broadcast(
                "inbound_peer_connected",
                {"node": peer_node.model_dump(), "connected_since": _inbound_connected_since[peer_node.id]},
            )
            peer_registered = True
            logger.info(
                f"Inbound peer registered: {peer.name} ({peer_ip}:{peer.port}), "
                f"total inbound={len(manager.inbound_peers)}, "
                f"SSE subscribers={len(_subscription_manager.subscribers)}"
            )
        except Exception as e:
            # Log at ERROR level — silent failures here cause "Connected To Me" to be empty
            logger.error(f"Failed to register inbound peer: {e}", exc_info=True)

    # Return local node info (always, so the connector gets our identity)
    result = manager.local_node.model_dump()
    result["peer_registered"] = peer_registered
    return result


@app.get("/api/network/inbound")
async def list_inbound_peers():
    """List peers that have connected to this node."""
    manager = get_network_manager()
    all_inbound = manager.get_inbound_peers()
    peers = []
    for peer in all_inbound:
        if peer.status == "offline":
            continue
        peers.append({
            "node": peer.model_dump(),
            "connected_since": _inbound_connected_since.get(peer.id),
        })
    logger.debug(
        f"/api/network/inbound: total_registered={len(all_inbound)}, "
        f"online={len(peers)}, ids={[p.id for p in all_inbound]}"
    )
    return {"peers": peers}


# ============================================================================
# Agent Execution API (CrewAI Integration)
# ============================================================================


class AgentExecuteRequest(BaseModel):
    """Request to execute a task with local agents."""
    task: str
    session_id: str | None = None
    requester_id: str | None = None
    payment: dict | None = None  # x402 payment info
    async_mode: bool = False  # If True, return task_id immediately without waiting
    delegation_chain: list[str] = Field(default_factory=list)  # an_id values ({wallet}-{6hex}) that already handled this task (loop prevention)
    parent_task_id: str | None = None  # Main task ID when this is a subtask/retry — for hierarchical task IDs


class AgentExecuteResponse(BaseModel):
    """Response from agent execution."""
    task_id: str
    status: str
    result: str | None = None
    error: str | None = None
    cost: dict | None = None
    duration: float | None = None


# Task manager for tracking agent executions
_task_manager = None


def get_task_manager_instance():
    """Get or create the global task manager."""
    global _task_manager
    if _task_manager is None:
        # Use local node name as agent ID
        agent_id = config.local_node.name if config.local_node else "local"
        _task_manager = get_task_manager(agent_id)
    return _task_manager


@app.get("/api/agent/status")
async def agent_status():
    """Get agent framework status."""
    crewai_available = check_crewai_available()
    task_manager = get_task_manager_instance()

    # Get local capabilities (from singleton — reflects offline workers)
    local_caps = []
    crew = get_local_crew_singleton()
    if crew:
        try:
            local_caps = crew.get_capabilities()
        except Exception as e:
            logger.error(f"Failed to get local capabilities: {e}")

    from teaming24.config import get_config as _sc
    _fw_backend = _sc().framework.backend

    return {
        "crewai_available": crewai_available,
        "framework_available": check_agent_framework_available(),
        "framework_backend": _fw_backend,
        "active_scenario": config.agents.get("active_scenario", ""),
        "local_capabilities": local_caps,
        "running_tasks": len([t for t in task_manager.list_tasks()
                             if t.status == TaskStatus.RUNNING]),
    }


@app.get("/api/agent/tasks")
async def list_agent_tasks(status: str | None = None, limit: int = 50):
    """List agent tasks."""
    task_manager = get_task_manager_instance()

    # Parse status filter
    status_filter = None
    if status:
        try:
            status_filter = TaskStatus(status)
        except ValueError:
            logger.debug("Invalid task status filter: %s", status)
            pass

    tasks = task_manager.list_tasks(status=status_filter, limit=limit)
    return {"tasks": [t.to_dict() for t in tasks]}


@app.get("/api/agent/tasks/{task_id}")
async def get_agent_task(task_id: str):
    """Get a specific task by ID."""
    task_manager = get_task_manager_instance()
    task = task_manager.get_task(task_id)
    if not task:
        _task_404()
    return task.to_dict()


@app.get("/api/agent/tasks/{task_id}/status")
async def get_agent_task_status(task_id: str):
    """Get lightweight task status (used by frontend reconnection fallback)."""
    task_manager = get_task_manager_instance()
    task = task_manager.get_task(task_id)
    if not task:
        # Frontend reconnect fallback may probe stale task IDs from old sessions.
        # Return a non-error payload to avoid noisy 404s in browser console.
        logger.debug("Task status requested for missing task_id=%s", task_id)
        return {
            "task_id": task_id,
            "status": "not_found",
            "progress": None,
        }
    return {
        "task_id": task_id,
        "status": task.status.value,
        "progress": task.progress.to_dict() if task.progress else None,
    }


@app.post("/api/agent/tasks/{task_id}/cancel")
async def cancel_agent_task(task_id: str):
    """Cancel a running task by ID."""
    # Unblock any pending approval for this task so exec thread exits promptly
    with _approval_lock:
        for v in _approval_requests.values():
            if v.get("task_id") == task_id and v.get("decision") is None:
                v["decision"] = "deny"
                evt = v.get("event")
                if evt is not None:
                    evt.set()
                break
    task = get_task_manager_instance().cancel_task(task_id)
    if not task:
        return JSONResponse(status_code=404, content={"error": "Task not found"})
    await _subscription_manager.broadcast("task_cancelled", {
        "task": {
            "id": task_id,
            "status": "cancelled",
            "completed_at": task.completed_at,
        }
    })
    return {"status": "cancelled", "task_id": task_id}


@app.get("/api/agent/tasks/{task_id}/subscribe")
async def subscribe_to_task(task_id: str):
    """Subscribe to real-time task updates via SSE.

    Returns an SSE stream with task status and artifact updates.
    The stream follows a structured event pattern:

    1. First event: current Task state (``task`` type)
    2. Subsequent events: ``status_update`` with incremental changes
    3. When ``final: true`` is set, the task has reached a terminal
       state and the stream closes.

    Terminal states: ``completed``, ``failed``, ``cancelled``.

    Event format::

        data: {"type": "task", "data": {<task dict>}, "timestamp": ...}
        data: {"type": "status_update", "data": {"taskId": "...",
               "status": {"state": "completed", ...}, "final": true},
               "timestamp": ...}
    """
    import asyncio as _aio
    import json as _json

    task_manager = get_task_manager_instance()
    task = task_manager.get_task(task_id)
    if not task:
        _task_404()

    # If task is already in a terminal state, return it immediately
    terminal_states = {"completed", "failed", "cancelled"}
    if task.status.value in terminal_states:
        async def _terminal_stream():
            payload = _json.dumps({
                "type": "task",
                "data": task.to_dict(),
                "timestamp": time.time(),
            })
            yield f"data: {payload}\n\n"
            # Send final status_update
            final = _json.dumps({
                "type": "status_update",
                "data": {
                    "taskId": task_id,
                    "status": {"state": task.status.value},
                    "final": True,
                    "result": task.result,
                    "error": task.error,
                },
                "timestamp": time.time(),
            })
            yield f"data: {final}\n\n"

        return StreamingResponse(
            _terminal_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    # For running/pending tasks: set up a listener and stream updates
    update_queue: _aio.Queue = _aio.Queue(maxsize=config.api.update_queue_size)

    def _on_task_event(updated_task, event_type: str):
        """TaskManager listener — push updates to our SSE queue.

        May be called from a background thread (e.g. ThreadPoolExecutor),
        so we use call_soon_threadsafe to put into the asyncio queue.
        """
        if updated_task.id != task_id:
            return
        try:
            is_final = updated_task.status.value in terminal_states
            latest_step = None
            try:
                if getattr(updated_task, "steps", None):
                    latest = updated_task.steps[-1]
                    latest_step = latest.to_dict() if hasattr(latest, "to_dict") else None
            except Exception as step_err:
                logger.debug("Failed to serialize latest step for task=%s: %s", task_id, step_err)
            update = {
                "type": "status_update",
                "data": {
                    "taskId": task_id,
                    "status": {
                        "state": updated_task.status.value,
                        "event": event_type,
                    },
                    "final": is_final,
                    "result": updated_task.result if is_final else None,
                    "error": updated_task.error if is_final else None,
                    "duration": updated_task.duration if is_final else None,
                    "progress": updated_task.progress.to_dict() if getattr(updated_task, "progress", None) else None,
                    "step_count": updated_task.step_count if hasattr(updated_task, "step_count") else 0,
                    "executing_agents": list(getattr(updated_task, "executing_agents", []) or []),
                    "delegated_agents": list(getattr(updated_task, "delegated_agents", []) or []),
                    "latest_step": latest_step,
                },
                "timestamp": time.time(),
            }
            msg = _json.dumps(update)
            try:
                _aio.get_running_loop()
                update_queue.put_nowait(msg)
            except RuntimeError:
                logger.debug("No running event loop in task SSE callback; scheduling thread-safe enqueue")
                # Called from a non-event-loop thread — schedule via
                # call_soon_threadsafe on the main loop.
                import threading
                main_loop = getattr(threading, '_teaming24_main_loop', None)
                if main_loop and main_loop.is_running():
                    main_loop.call_soon_threadsafe(update_queue.put_nowait, msg)
        except _aio.QueueFull:
            logger.debug("Task SSE update queue full for task_id=%s", task_id)
            pass
        except Exception as e:
            logger.debug(f"Failed to enqueue task event for SSE listener: {e}")

    task_manager.add_listener(_on_task_event)

    # Re-check status after adding listener to close the race window:
    # if the task completed between the initial check and add_listener(),
    # manually enqueue the final event now.
    _recheck = task_manager.get_task(task_id)
    if _recheck and _recheck.status.value in terminal_states:
        _on_task_event(_recheck, "completed")

    async def _stream():
        try:
            # First event: current task state (re-read for freshness)
            _fresh = task_manager.get_task(task_id) or task
            payload = _json.dumps({
                "type": "task",
                "data": _fresh.to_dict(),
                "timestamp": time.time(),
            })
            yield f"data: {payload}\n\n"

            # Stream updates until terminal state
            while True:
                try:
                    msg = await _aio.wait_for(update_queue.get(), timeout=config.api.update_queue_timeout)
                    yield f"data: {msg}\n\n"

                    # Check if this is the final event
                    parsed = _json.loads(msg)
                    if parsed.get("data", {}).get("final", False):
                        break
                except TimeoutError:
                    logger.debug("Task SSE keepalive timeout for task_id=%s", task_id)
                    # Keep-alive ping
                    ping = _json.dumps({
                        "type": "ping",
                        "timestamp": time.time(),
                    })
                    yield f"data: {ping}\n\n"
                except _aio.CancelledError:
                    logger.debug("Task SSE stream cancelled task_id=%s", task_id)
                    break
        finally:
            task_manager.remove_listener(_on_task_event)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.get("/api/agent/outputs")
async def list_task_outputs(limit: int = 20):
    """List saved task outputs."""
    try:
        from teaming24.task.output import get_output_manager
        manager = get_output_manager()
        outputs = manager.list_outputs(limit=limit)
        return {
            "outputs": [o.to_dict() for o in outputs],
            "base_dir": str(manager.base_dir),
        }
    except Exception as e:
        logger.warning(f"Failed to list task outputs: {e}")
        return {"outputs": [], "base_dir": ""}


@app.get("/api/agent/outputs/{task_id}")
async def get_task_output(task_id: str):
    """Get output for a specific task."""
    try:
        from teaming24.task.output import get_output_manager
        manager = get_output_manager()
        output = manager.get_task_output(task_id)
        if not output:
            raise HTTPException(status_code=404, detail="Task output not found")
        return output.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Failed to get task output: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/agent/outputs/{task_id}/file")
async def serve_task_output_file(task_id: str, name: str):
    """Serve a file from task output (for multimodal chat display).

    name: filename only (no path) — must match a file in the task output.
    """
    if ".." in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    try:
        from pathlib import Path

        from fastapi.responses import FileResponse

        from teaming24.task.output import get_output_manager
        manager = get_output_manager()
        output = manager.get_task_output(task_id)
        if not output:
            raise HTTPException(status_code=404, detail="Task output not found")
        target = None
        for f in output.files:
            if f.filename == name:
                target = Path(f.filepath)
                break
        if not target or not target.exists():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(str(target), filename=name)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Failed to serve task file: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/agent/events")
async def agent_events_stream(request: Request):
    """
    SSE endpoint for real-time agent and task events.

    Events include:
    - agent_registered: New agent registered
    - agent_status: Agent status change
    - task_created: New task created
    - task_started: Task execution started
    - task_step: Task step completed (agent delegation/action)
    - task_completed: Task finished
    - task_failed: Task failed
    - delegation: Organizer -> Coordinator or Coordinator -> Worker delegation
    """
    async def event_generator():
        import asyncio

        buf = get_event_buffer()
        raw_last_id = request.headers.get('last-event-id', '')
        since_seq = int(raw_last_id) if raw_last_id.isdigit() else 0

        # Send initial agent list
        task_manager = get_task_manager_instance()

        logger.info(f"[Agent Events] New SSE client connected (since_seq={since_seq})")

        # Reconnect: replay missed incremental events from buffer
        if since_seq > 0:
            missed = buf.get_since(since_seq)
            if missed:
                logger.info(f"[Agent Events] Replaying {len(missed)} events since seq={since_seq}")
                for _seq, _raw in missed:
                    yield f"id: {_seq}\n{_raw}"

        # Send initial agents and tasks (first connect only; reconnect uses buffer replay)
        if since_seq == 0:
            # Build agents info only on first connect (not needed for reconnect replay)
            agents_info = []
            crew = get_local_crew_singleton()
            has_organizer = False
            has_coordinator = False
            if crew:
                try:
                    # Organizer
                    if crew.organizer:
                        agents_info.append({
                            "id": ORGANIZER_ID,
                            "name": getattr(crew.organizer, 'role', 'Organizer'),
                            "type": "organizer",
                            "status": "online",
                            "model": _agent_model_name(crew.organizer),
                            "goal": getattr(crew.organizer, 'goal', ''),
                            "backstory": getattr(crew.organizer, 'backstory', ''),
                            "capabilities": [
                                {"name": "task_routing", "description": "Routes tasks to appropriate agents"},
                                {"name": "network_delegation", "description": "Delegates to remote nodes"},
                            ],
                        })
                        has_organizer = True

                    # Coordinator — include dynamic worker capabilities
                    if crew.coordinator:
                        coord_caps = [
                            {"name": "task_decomposition", "description": "Breaks down complex tasks"},
                            {"name": "worker_coordination", "description": "Coordinates worker agents"},
                        ]
                        for wd in crew.get_worker_descriptions():
                            coord_caps.append({
                                "name": wd.get("role", "Worker"),
                                "description": wd.get("goal", ""),
                            })
                        agents_info.append({
                            "id": COORDINATOR_ID,
                            "name": normalize_agent_name(getattr(crew.coordinator, 'role', LOCAL_COORDINATOR_NAME)),
                            "type": "coordinator",
                            "status": "online",
                            "model": _agent_model_name(crew.coordinator),
                            "goal": getattr(crew.coordinator, 'goal', ''),
                            "backstory": getattr(crew.coordinator, 'backstory', ''),
                            "capabilities": coord_caps,
                        })
                        has_coordinator = True

                    # Build role → capabilities lookup from config
                    worker_cap_map = _build_worker_cap_map(crew)
                    worker_meta_map = _build_worker_meta_map(crew)

                    # Workers — stable name-based IDs, reflect offline state
                    for i, worker in enumerate(crew.workers):
                        role = getattr(worker, 'role', f'Worker {i+1}')
                        goal = getattr(worker, 'goal', '')
                        backstory = getattr(worker, 'backstory', '')
                        is_offline = crew.is_worker_offline(role)
                        worker_meta = worker_meta_map.get(role, {})
                        agents_info.append({
                            "id": _get_stable_worker_id(crew, i),
                            "name": role,
                            "type": "worker",
                            "status": "offline" if is_offline else "idle",
                            "model": _agent_model_name(worker),
                            "goal": goal,
                            "backstory": backstory,
                            "capabilities": worker_cap_map.get(role, []),
                            **worker_meta,
                        })
                except Exception as e:
                    logger.warning(f"Could not get agent info: {e}")

            # Ensure Organizer and Coordinator always appear (structural roles)
            runtime_settings = get_agent_runtime_settings()
            if not has_organizer:
                agents_info.insert(0, build_fallback_organizer_agent_info(runtime_settings))
            if not has_coordinator:
                agents_info.insert(1 if not has_organizer else 1, build_fallback_coordinator_agent_info(runtime_settings))

            cur_seq = buf.latest_seq
            yield f"id: {cur_seq}\ndata: {json.dumps({'type': 'agents_init', 'agents': agents_info})}\n\n"

            # Send current tasks — no artificial limit
            tasks = task_manager.list_tasks()
            yield f"id: {cur_seq}\ndata: {json.dumps({'type': 'tasks_init', 'tasks': [t.to_dict() for t in tasks]})}\n\n"

        # Subscribe to task manager events (for legacy events)
        event_queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        connection_active = True

        def on_task_event(task, event_type: str):
            """Callback for task events - put in queue for async processing."""
            if not connection_active:
                return
            try:
                loop.call_soon_threadsafe(
                    event_queue.put_nowait,
                    {"type": f"task_{event_type}", "task": task.to_dict()}
                )
            except RuntimeError:
                # Loop is closed
                logger.debug("Agent events loop closed; dropping task event %s", event_type)
                pass
            except Exception as e:
                logger.debug(f"Failed to put task event into agent event queue: {e}")
                pass

        task_manager.add_listener(on_task_event)

        # Also subscribe to subscription manager for chat->dashboard sync
        subscription_queue: asyncio.Queue = asyncio.Queue(maxsize=config.api.subscription_queue_size)
        _subscription_manager.subscribers.append(subscription_queue)
        logger.info(f"[Agent Events] Subscribed to _subscription_manager (total subscribers: {len(_subscription_manager.subscribers)})")

        try:
            last_keepalive = time.time()

            while not _shutdown_event.is_set():
                # Check both queues with short timeout
                event_found = False

                # Try task manager queue first (non-blocking).
                # These events overlap with subscription_manager broadcasts; yield
                # them for immediacy but do NOT push to the buffer (buffer is fed
                # once-per-event inside subscription_manager.broadcast()).
                try:
                    event = event_queue.get_nowait()
                    yield f"data: {json.dumps(event)}\n\n"
                    event_found = True
                    last_keepalive = time.time()
                except asyncio.QueueEmpty:
                    logger.debug("Agent events task queue empty")
                    pass
                except Exception as e:
                    logger.debug(f"Failed to read from event queue: {e}")
                    pass

                # Try subscription queue (non-blocking).
                # broadcast() already embedded id: {seq}\n for non-ephemeral events.
                try:
                    event = subscription_queue.get_nowait()
                    if event is None:
                        # close_all() sentinel — shut down this connection gracefully
                        break
                    logger.debug(f"[Agent Events] Forwarding subscription event: {str(event)[:100]}...")
                    if isinstance(event, str):
                        yield event
                    else:
                        yield f"data: {json.dumps(event)}\n\n"
                    event_found = True
                    last_keepalive = time.time()
                except asyncio.QueueEmpty:
                    logger.debug("Agent events subscription queue empty")
                    pass
                except Exception as e:
                    logger.debug(f"[Agent Events] Error getting subscription event: {e}")

                # If no events, wait a bit
                if not event_found:
                    await asyncio.sleep(0.1)
                    # Send keepalive based on configured SSE timeout.
                    if time.time() - last_keepalive > config.api.sse_keepalive_timeout:
                        yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"
                        last_keepalive = time.time()

        except asyncio.CancelledError:
            # Client disconnected - this is normal
            logger.debug("Agent events stream cancelled by client")
            pass
        except GeneratorExit:
            # Generator closed - this is normal
            logger.debug("Agent events stream generator exit")
            pass
        finally:
            # Mark connection as inactive to prevent further events
            connection_active = False
            task_manager.remove_listener(on_task_event)
            try:
                _subscription_manager.subscribers.remove(subscription_queue)
            except ValueError:
                logger.debug("Subscription queue already removed during agent events cleanup")
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )


@app.get("/api/state/snapshot")
async def state_snapshot():
    """Full state snapshot for SSE reconnect reconciliation (Layer 2 fallback).

    Returns current task list + wallet balance + the latest event seq so
    the frontend can merge in-memory state with server state after a long
    SSE disconnect (when the circular buffer may have been exhausted).
    """
    buf = get_event_buffer()
    tm = get_task_manager_instance()
    tasks = tm.list_tasks()
    return {
        "tasks": [t.to_dict() for t in tasks],
        "wallet": {"balance": round(_st.mock_balance, 6)},
        "event_seq": buf.latest_seq,
        "timestamp": time.time(),
    }


@app.get("/api/agent/agents")
async def list_local_agents():
    """
    Get list of local agents (Organizer, Coordinator, Workers).

    Returns agent hierarchy with status and capabilities.
    """
    task_manager = get_task_manager_instance()
    agents = []

    # Use the singleton so offline state is reflected correctly
    crew = get_local_crew_singleton()
    if crew is None and check_agent_framework_available():
        try:
            runtime_settings = get_agent_runtime_settings()
            crew = create_local_crew(task_manager, runtime_settings=runtime_settings)
        except Exception as e:
            logger.warning(f"Could not create crew: {e}")

    has_organizer = False
    has_coordinator = False
    if crew:
        try:
            # Organizer
            if crew.organizer:
                agents.append({
                    "id": ORGANIZER_ID,
                    "name": getattr(crew.organizer, 'role', 'Organizer'),
                    "type": "organizer",
                    "status": "online",
                    "model": _agent_model_name(crew.organizer),
                    "goal": getattr(crew.organizer, 'goal', ''),
                    "backstory": getattr(crew.organizer, 'backstory', ''),
                    "capabilities": [
                        {"name": "task_routing", "description": "Routes tasks to coordinators"},
                        {"name": "network_delegation", "description": "Delegates to remote nodes"},
                    ],
                })
                has_organizer = True

            # Coordinator — include dynamic worker capabilities
            if crew.coordinator:
                coord_caps = [
                    {"name": "task_decomposition", "description": "Breaks down complex tasks"},
                    {"name": "worker_coordination", "description": "Coordinates worker agents"},
                ]
                for wd in crew.get_worker_descriptions():
                    coord_caps.append({
                        "name": wd.get("role", "Worker"),
                        "description": wd.get("goal", ""),
                    })
                agents.append({
                    "id": COORDINATOR_ID,
                    "name": normalize_agent_name(getattr(crew.coordinator, 'role', LOCAL_COORDINATOR_NAME)),
                    "type": "coordinator",
                    "status": "online",
                    "model": _agent_model_name(crew.coordinator),
                    "goal": getattr(crew.coordinator, 'goal', ''),
                    "backstory": getattr(crew.coordinator, 'backstory', ''),
                    "capabilities": coord_caps,
                })
                has_coordinator = True

            # Build role → capabilities lookup from config
            worker_cap_map = _build_worker_cap_map(crew)
            worker_meta_map = _build_worker_meta_map(crew)

            # Workers — stable name-based IDs, report offline status
            for i, worker in enumerate(crew.workers):
                role = getattr(worker, 'role', f'Worker {i+1}')
                goal = getattr(worker, 'goal', '')
                backstory = getattr(worker, 'backstory', '')
                is_offline = crew.is_worker_offline(role)
                worker_meta = worker_meta_map.get(role, {})

                agents.append({
                    "id": _get_stable_worker_id(crew, i),
                    "name": role,
                    "type": "worker",
                    "status": "offline" if is_offline else "idle",
                    "model": _agent_model_name(worker),
                    "goal": goal,
                    "backstory": backstory,
                    "capabilities": worker_cap_map.get(role, []),
                    **worker_meta,
                })
        except Exception as e:
            logger.warning(f"Could not get agent info: {e}")

    # Ensure Organizer and Coordinator always appear (structural roles)
    runtime_settings = get_agent_runtime_settings()
    if not has_organizer:
        agents.insert(0, build_fallback_organizer_agent_info(runtime_settings))
    if not has_coordinator:
        agents.insert(min(1, len(agents)), build_fallback_coordinator_agent_info(runtime_settings))

    # Merge with DB-persisted agents (user-created)
    try:
        db = get_database()
        db_agents = db.get_agents()
        existing_ids = {a["id"] for a in agents}
        for db_agent in db_agents:
            if db_agent["id"] not in existing_ids:
                agents.append({
                    "id": db_agent["id"],
                    "name": db_agent.get("name", ""),
                    "type": db_agent.get("type", "worker"),
                    "status": db_agent.get("status", "offline"),
                    "goal": db_agent.get("goal", ""),
                    "backstory": db_agent.get("backstory", ""),
                    "capabilities": db_agent.get("capabilities", []),
                    "model": db_agent.get("model"),
                    "tools": db_agent.get("tools", []),
                    "system_prompt": db_agent.get("system_prompt", ""),
                    "allow_delegation": db_agent.get("allow_delegation", True),
                    "source": "database",
                })
    except Exception as e:
        logger.warning(f"Could not load DB agents: {e}")

    return {"agents": agents, "count": len(agents)}


@app.get("/api/agent/simulation-groups")
async def get_simulation_worker_groups():
    """Get demo worker-group mapping from YAML and the active group selection."""
    cfg_active_group_raw = getattr(config.agents, "demo_active_group_id", None)
    cfg_active_group_id: int | None = None
    try:
        if cfg_active_group_raw is not None and str(cfg_active_group_raw).strip() != "":
            cfg_active_group_id = int(str(cfg_active_group_raw).strip())
    except Exception as parse_exc:
        logger.warning(
            "Invalid demo_active_group_id %r ignored: %s",
            cfg_active_group_raw,
            parse_exc,
            exc_info=True,
        )

    cfg_ids: list[int] = [cfg_active_group_id] if cfg_active_group_id is not None else []

    groups_raw = getattr(config.agents, "simulation_worker_groups", {}) or {}
    groups: dict[str, list[str]] = {}
    if isinstance(groups_raw, dict):
        for key, workers in groups_raw.items():
            groups[str(key)] = [str(name).strip() for name in (workers or []) if str(name).strip()]

    return {
        "groups": groups,
        "active_group_id": cfg_active_group_id,
        "selected_group_ids": cfg_ids,
    }


@app.post("/api/agent/simulation-groups")
async def set_simulation_worker_groups():
    """Deprecated runtime override endpoint (YAML group ID is the single control)."""
    raise HTTPException(
        status_code=400,
        detail="Simulation worker selection is YAML-only. Set agents.demo_active_group_id and restart.",
    )


@app.post("/api/agent/agents")
async def create_agent(request: Request):
    """Create a new agent and persist to database."""
    global _local_crew_singleton
    data = await request.json()
    db = get_database()
    agent_id = data.get("id") or f"worker-{generic_id()[:8]}"
    agent_type = data.get("type", "worker")
    model_name = data.get("model", "")
    db.save_agent({
        "id": agent_id,
        "name": data.get("name", "New Agent"),
        "type": agent_type,
        "status": data.get("status", "offline"),
        "capabilities": data.get("capabilities", []),
        "endpoint": data.get("endpoint"),
        "model": model_name,
        "goal": data.get("goal"),
        "backstory": data.get("backstory"),
        "tools": data.get("tools", []),
        "system_prompt": data.get("system_prompt", ""),
        "allow_delegation": data.get("allow_delegation", True),
        "metadata": data.get("metadata", {}),
    })
    try:
        if agent_type == "organizer":
            db.set_setting("organizerModel", model_name or "")
            _local_crew_singleton = None
            _st.local_crew_singleton = None
        elif agent_type == "coordinator":
            db.set_setting("coordinatorModel", model_name or "")
            _local_crew_singleton = None
            _st.local_crew_singleton = None
    except Exception as exc:
        logger.warning(
            "Failed to persist role model setting during create_agent for %s: %s",
            agent_id,
            exc,
            exc_info=True,
        )
    logger.info(f"Agent created: {agent_id} ({data.get('name')})")
    return {"id": agent_id, "status": "created"}


@app.put("/api/agent/agents/{agent_id}")
async def update_agent_endpoint(agent_id: str, request: Request):
    """Update an existing agent.

    If the ``status`` field is changed to ``"offline"`` or ``"online"``,
    the local crew's worker pool is updated accordingly so that:
      - ``AgenticNodeWorkforcePool.get_pool()`` reflects the change immediately
      - The next LAN broadcast advertises updated capabilities
      - An SSE event notifies the frontend dashboard in real time
    """
    global _local_crew_singleton
    data = await request.json()
    db = get_database()
    existing = db.get_agent(agent_id)
    is_builtin_runtime_agent = False
    if not existing:
        if agent_id == ORGANIZER_ID:
            existing = {"id": ORGANIZER_ID, "name": "Organizer", "type": "organizer", "status": "online"}
            is_builtin_runtime_agent = True
        elif agent_id == COORDINATOR_ID:
            existing = {"id": COORDINATOR_ID, "name": LOCAL_COORDINATOR_NAME, "type": "coordinator", "status": "online"}
            is_builtin_runtime_agent = True
        else:
            crew = get_local_crew_singleton()
            if crew:
                for idx, worker in enumerate(getattr(crew, "workers", []) or []):
                    worker_id = _get_stable_worker_id(crew, idx)
                    if worker_id != agent_id:
                        continue
                    worker_name = str(getattr(worker, "role", "") or f"Worker {idx + 1}").strip()
                    existing = {
                        "id": agent_id,
                        "name": worker_name,
                        "type": "worker",
                        "status": "offline" if crew.is_worker_offline(worker_name) else "idle",
                        "model": _agent_model_name(worker),
                    }
                    is_builtin_runtime_agent = True
                    break
            if not existing:
                raise HTTPException(status_code=404, detail="Agent not found")

    persisted_data = dict(data)
    if is_builtin_runtime_agent:
        role_type = str(existing.get("type", "")).strip().lower()
        allowed_keys = {"model"}
        if role_type == "worker":
            allowed_keys.add("status")
        persisted_data = {k: v for k, v in persisted_data.items() if k in allowed_keys}

    merged = {**existing, **persisted_data, "id": agent_id}
    if db.get_agent(agent_id):
        db.update_agent(agent_id, persisted_data)
    else:
        db.save_agent(merged)

    # Sync role model overrides so runtime execution picks them up immediately.
    if "model" in persisted_data:
        try:
            role_type = str(merged.get("type", "")).strip().lower()
            if role_type == "organizer":
                db.set_setting("organizerModel", persisted_data.get("model", ""))
                _local_crew_singleton = None
                _st.local_crew_singleton = None
            elif role_type == "coordinator":
                db.set_setting("coordinatorModel", persisted_data.get("model", ""))
                _local_crew_singleton = None
                _st.local_crew_singleton = None
            elif role_type == "worker":
                crew = _local_crew_singleton or _st.local_crew_singleton
                if crew:
                    target_name = str(merged.get("name", "") or existing.get("name", "") or "").strip()
                    target_model = str(persisted_data.get("model", "") or "").strip()
                    for idx, worker in enumerate(getattr(crew, "workers", []) or []):
                        worker_name = str(getattr(worker, "role", "") or "").strip()
                        worker_id = _get_stable_worker_id(crew, idx)
                        if agent_id != worker_id and target_name and target_name != worker_name:
                            continue
                        worker.llm = crew.factory._create_llm(target_model)
                        if idx < len(getattr(crew, "_worker_configs", []) or []):
                            cfg = crew._worker_configs[idx]
                            if isinstance(cfg, dict):
                                cfg["model"] = target_model
                        runtime_overrides = getattr(crew, "runtime_settings", None)
                        if isinstance(runtime_overrides, dict):
                            merged_overrides = dict(runtime_overrides.get("workerModelOverrides") or {})
                            for override_key in (agent_id, target_name, target_name.lower() if target_name else ""):
                                if not override_key:
                                    continue
                                if target_model:
                                    merged_overrides[override_key] = target_model
                                else:
                                    merged_overrides.pop(override_key, None)
                            runtime_overrides["workerModelOverrides"] = merged_overrides
                            runtime_overrides["worker_model_overrides"] = dict(merged_overrides)
                        break
        except Exception as exc:
            logger.warning(
                "Failed to persist role model setting for %s: %s",
                agent_id,
                exc,
                exc_info=True,
            )

    # Propagate status change to the live LocalCrew worker pool
    new_status = persisted_data.get("status")
    if new_status in ("offline", "online"):
        crew = get_local_crew_singleton()
        if crew:
            agent_name = existing.get("name") or data.get("name", "")
            if new_status == "offline":
                crew.set_worker_offline(agent_name)
            else:
                crew.set_worker_online(agent_name)

    # Broadcast agent_updated event so all SSE clients see the change
    await _subscription_manager.broadcast("agent_updated", {
        "agent_id": agent_id,
        "name": merged.get("name", ""),
        "type": merged.get("type", "worker"),
        "status": merged.get("status", "offline"),
        "model": merged.get("model"),
        "capabilities": merged.get("capabilities", []),
        "goal": merged.get("goal", ""),
        "backstory": merged.get("backstory", ""),
    })

    # If the worker pool changed, also broadcast a pool_updated event
    # with refreshed coordinator capabilities
    if new_status in ("offline", "online"):
        await _broadcast_pool_update()
    elif "model" in persisted_data and str(merged.get("type", "")).strip().lower() == "worker":
        await _broadcast_pool_update()

    logger.info(f"Agent updated: {agent_id}")
    return {"id": agent_id, "status": "updated"}


def _build_worker_cap_map(crew) -> dict:
    """Build a role → capabilities lookup for all workers.

    Returns ``{role: [{name, description}, ...]}`` for every worker
    (including offline ones), so the dashboard always shows each
    worker's capabilities.  Uses ``crew._worker_configs`` which is
    populated at creation time from either dev profiles or scenario.
    """
    cap_map: dict = {}
    worker_configs = getattr(crew, '_worker_configs', []) or []
    for wc in worker_configs:
        if isinstance(wc, dict):
            role = wc.get("role", "")
            if role:
                cap_map[role] = [
                    {"name": c, "description": ""} for c in wc.get("capabilities", [])
                ]
    return cap_map


def _build_worker_meta_map(crew) -> dict:
    """Build a role -> metadata map for worker source flags."""
    meta_map: dict = {}
    worker_configs = getattr(crew, '_worker_configs', []) or []
    for wc in worker_configs:
        if not isinstance(wc, dict):
            continue
        role = wc.get("role", "")
        if not role:
            continue
        entry: dict[str, Any] = {}
        if wc.get("_source"):
            entry["source"] = wc.get("_source")
        if wc.get("_is_predefined_demo_agent"):
            entry["is_predefined_demo_agent"] = True
        if wc.get("_demo_group_id") is not None:
            entry["demo_group_id"] = wc.get("_demo_group_id")
        if entry:
            meta_map[role] = entry
    return meta_map


def _agent_model_name(agent: Any) -> str | None:
    """Return a stable display model string for a runtime agent."""
    llm = getattr(agent, "llm", None)
    if llm is None:
        return None
    if isinstance(llm, str):
        return llm
    model = getattr(llm, "model", None)
    return str(model).strip() if model else None


def _get_stable_worker_id(crew, index: int) -> str:
    """Return the stable worker ID for the worker at *index* (0-based).

    Uses the new ``crew.get_worker_id_for_index()`` when available,
    falling back to the legacy positional ``worker-{index+1}`` ID.
    """
    if hasattr(crew, 'get_worker_id_for_index'):
        return crew.get_worker_id_for_index(index)
    return make_worker_id(index + 1)


async def _broadcast_pool_update():
    """Broadcast a pool_updated SSE event with the coordinator's
    refreshed capabilities and the full agent roster.

    Called whenever the local worker pool changes (worker goes
    offline/online) so the dashboard reflects the current state.
    """
    crew = get_local_crew_singleton()
    if not crew:
        return

    # Build coordinator capabilities from online workers
    coordinator_caps = []
    for wd in crew.get_worker_descriptions():
        coordinator_caps.append({
            "name": wd.get("role", "Worker"),
            "description": wd.get("goal", ""),
        })

    # Build full agent list with current status
    agents_snapshot = []
    if crew.organizer:
        agents_snapshot.append({
            "id": ORGANIZER_ID,
            "name": getattr(crew.organizer, 'role', 'Organizer'),
            "type": "organizer",
            "status": "online",
            "model": _agent_model_name(crew.organizer),
            "capabilities": [
                {"name": "task_routing", "description": "Routes tasks to appropriate agents"},
                {"name": "network_delegation", "description": "Delegates to remote nodes"},
            ],
        })
    if crew.coordinator:
        agents_snapshot.append({
            "id": COORDINATOR_ID,
            "name": normalize_agent_name(getattr(crew.coordinator, 'role', LOCAL_COORDINATOR_NAME)),
            "type": "coordinator",
            "status": "online",
            "model": _agent_model_name(crew.coordinator),
            "capabilities": coordinator_caps,
        })
    worker_cap_map = _build_worker_cap_map(crew)
    worker_meta_map = _build_worker_meta_map(crew)
    for i, worker in enumerate(crew.workers):
        role = getattr(worker, 'role', f'Worker {i+1}')
        is_offline = crew.is_worker_offline(role)
        worker_meta = worker_meta_map.get(role, {})
        agents_snapshot.append({
            "id": _get_stable_worker_id(crew, i),
            "name": role,
            "type": "worker",
            "status": "offline" if is_offline else "idle",
            "model": _agent_model_name(worker),
            "capabilities": worker_cap_map.get(role, []),
            **worker_meta,
        })

    await _subscription_manager.broadcast("pool_updated", {
        "agents": agents_snapshot,
        "coordinator_capabilities": coordinator_caps,
        "online_worker_count": len(crew.get_online_workers()),
        "total_worker_count": len(crew.workers),
    })


@app.delete("/api/agent/agents/{agent_id}")
async def delete_agent_endpoint(agent_id: str):
    """Delete an agent from database."""
    db = get_database()
    existing = db.get_agent(agent_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Agent not found")
    db.delete_agent(agent_id)
    logger.info(f"Agent deleted: {agent_id}")
    return {"id": agent_id, "status": "deleted"}


# =============================================================================
# Skills Management API
# =============================================================================

@app.get("/api/skills")
async def list_skills():
    """List all skills (DB + filesystem)."""
    from teaming24.agent.skills import Skill as SkillModel
    from teaming24.agent.skills import get_skill_registry
    db = get_database()
    registry = get_skill_registry()
    if not registry.list_all():
        registry.load()
    db_skills_raw = db.get_skills()
    db_skill_objs = [SkillModel.from_dict(s) for s in db_skills_raw]
    registry.merge_db_skills(db_skill_objs)
    all_skills = [s.to_dict() for s in registry.list_all()]
    return JSONResponse(content={"skills": all_skills})


@app.get("/api/skills/{skill_id}")
async def get_skill_endpoint(skill_id: str):
    """Get a single skill with full instructions."""
    from teaming24.agent.skills import Skill as SkillModel
    from teaming24.agent.skills import get_skill_registry
    db = get_database()
    registry = get_skill_registry()
    if not registry.list_all():
        registry.load()
    db_skill = db.get_skill(skill_id)
    if db_skill:
        registry.merge_db_skills([SkillModel.from_dict(db_skill)])
    skill = registry.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return JSONResponse(content=skill.to_dict())


@app.post("/api/skills")
async def create_skill(request: Request):
    """Create a new skill."""
    data = await request.json()
    if not data.get("name"):
        raise HTTPException(status_code=400, detail="name is required")
    skill_id = data.get("id") or data["name"].lower().replace(" ", "-").replace("/", "-")
    from teaming24.agent.skills import validate_skill_name
    name_err = validate_skill_name(skill_id)
    if name_err:
        raise HTTPException(status_code=400, detail=f"Invalid skill name: {name_err}")
    data["id"] = skill_id
    data.setdefault("source", "user")
    db = get_database()
    db.save_skill(data)
    from teaming24.agent.skills import Skill as SkillModel
    from teaming24.agent.skills import get_skill_registry
    registry = get_skill_registry()
    registry.register(SkillModel.from_dict(data))
    logger.info(f"Skill created: {skill_id}")
    return JSONResponse(content={"id": skill_id, "status": "created"})


@app.put("/api/skills/{skill_id}")
async def update_skill_endpoint(skill_id: str, request: Request):
    """Update a skill (upserts if the skill only exists on the filesystem)."""
    data = await request.json()
    db = get_database()
    existing = db.get_skill(skill_id)
    if existing:
        db.update_skill(skill_id, data)
    else:
        from teaming24.agent.skills import get_skill_registry
        registry = get_skill_registry()
        fs_skill = registry.get(skill_id)
        if not fs_skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        merged = fs_skill.to_dict()
        merged.update(data)
        merged["id"] = skill_id
        merged["source"] = "user"
        db.save_skill(merged)
    from teaming24.agent.skills import Skill as SkillModel
    from teaming24.agent.skills import get_skill_registry as _get_reg
    updated = db.get_skill(skill_id)
    if updated:
        _get_reg().register(SkillModel.from_dict(updated))
    logger.info(f"Skill updated: {skill_id}")
    return JSONResponse(content={"id": skill_id, "status": "updated"})


@app.delete("/api/skills/{skill_id}")
async def delete_skill_endpoint(skill_id: str):
    """Delete a skill."""
    db = get_database()
    existing = db.get_skill(skill_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Skill not found")
    db.delete_skill(skill_id)
    from teaming24.agent.skills import get_skill_registry
    get_skill_registry().unregister(skill_id)
    logger.info(f"Skill deleted: {skill_id}")
    return JSONResponse(content={"id": skill_id, "status": "deleted"})


@app.get("/api/agent/agents/{agent_id}/skills")
async def get_agent_skills_endpoint(agent_id: str):
    """Get skills assigned to an agent."""
    db = get_database()
    skill_ids = db.get_agent_skill_ids(agent_id)
    skills = db.get_agent_skills(agent_id)
    return JSONResponse(content={"agent_id": agent_id, "skill_ids": skill_ids, "skills": skills})


@app.put("/api/agent/agents/{agent_id}/skills")
async def assign_agent_skills(agent_id: str, request: Request):
    """Assign skills to an agent (replaces existing assignments).

    The agent may exist only at runtime (from YAML config), so we don't
    require it to be present in the DB.
    """
    data = await request.json()
    skill_ids = data.get("skill_ids", [])
    db = get_database()
    db.assign_skills_to_agent(agent_id, skill_ids)
    logger.info(f"Agent {agent_id} assigned skills: {skill_ids}")
    return JSONResponse(content={"agent_id": agent_id, "skill_ids": skill_ids, "status": "updated"})


@app.post("/api/agent/execute", response_model=AgentExecuteResponse)
async def execute_agent_task(request: AgentExecuteRequest):
    """
    Execute a task with local agent crew.

    This endpoint is called by:
    1. Local chat to execute user requests
    2. Remote nodes delegating tasks via x402

    For remote requests, payment verification is performed (mock mode).
    All executions are tracked and broadcast to the Dashboard SSE stream.
    """
    if not check_agent_framework_available():
        raise HTTPException(
            status_code=503,
            detail="No agent framework available. Install CrewAI (uv pip install crewai) or configure native backend.",
        )

    task_manager = get_task_manager_instance()

    # Determine if this is a remote request.
    # Use get_node_uid() — a deterministic hash of hostname+MAC+port — as the
    # unique identifier for loop detection.  config.local_node.name / host:port
    # are NOT unique across different machines (both default to "Local Agentic
    # Node" / "127.0.0.1:8000").
    from teaming24.utils.ids import get_node_uid
    local_node_uid = get_node_uid()
    is_remote = bool(request.requester_id and request.requester_id != local_node_uid)
    requester = request.requester_id or "local"

    # --- Loop prevention: reject if this node already handled this task ---
    # Chain entries are an_id values ({wallet}-{6hex}), NOT names or host:port.
    MAX_DELEGATION_DEPTH = config.an_router.max_delegation_depth
    chain = request.delegation_chain or []
    if local_node_uid in chain:
        logger.warning(
            f"[LOOP DETECTED] Task delegation loop: {' → '.join(chain)} → {local_node_uid}. "
            f"Rejecting to prevent infinite recursion."
        )
        raise HTTPException(
            status_code=409,
            detail=f"Delegation loop detected: this node ({local_node_uid}) "
                   f"is already in the delegation chain: {chain}"
        )
    if len(chain) >= MAX_DELEGATION_DEPTH:
        logger.warning(
            f"[DEPTH LIMIT] Delegation chain too deep ({len(chain)} hops): "
            f"{' → '.join(chain)}. Rejecting."
        )
        raise HTTPException(
            status_code=409,
            detail=f"Delegation chain depth limit reached ({len(chain)}/{MAX_DELEGATION_DEPTH}). "
                   f"Chain: {chain}"
        )

    if is_remote:
        logger.info(
            f"[REMOTE TASK] Received task from remote AN: requester={requester}, "
            f"chain={chain}, task={request.task[:100]}..."
        )

    memory_agent_id = ORGANIZER_ID if not is_remote else COORDINATOR_ID
    memory_session_id = str(getattr(request, "session_id", "") or "").strip() or None
    memory_context = load_agent_memory_context(
        memory_agent_id, request.task, session_id=memory_session_id, cfg=config, logger=logger
    )
    execution_task_text = build_agent_execution_prompt(
        [Message(role="user", content=request.task)],
        request.task,
        cfg=config,
        agent_memory_context=memory_context,
    )

    # --- x402 Payment Gate ---
    # Only remote tasks (from another AN) go through the payment gate.
    # Local tasks don't pay themselves — payments only flow between nodes.
    # If parent_task_id was already paid by this requester, skip payment requirement.
    from teaming24.payment.crypto.x402.gate import get_payment_gate
    payment_gate = get_payment_gate()

    payment_receipt = None
    parent_task_id = str(getattr(request, "parent_task_id", "") or "").strip() or None

    if is_remote:
        # Skip payment if this parent_task_id was already paid by this requester
        if parent_task_id:
            try:
                if get_database().is_payment_recorded(parent_task_id, requester):
                    from teaming24.payment.crypto.x402.gate import PaymentReceipt
                    payment_receipt = PaymentReceipt(
                        approved=True,
                        mode="mock",
                        task_id=f"pre-{int(time.time() * 1000)}",
                        amount="0",
                        amount_atomic="0",
                        currency=config.payment.token_symbol,
                        network="none",
                        payer=requester,
                        payee="",
                    )
                    logger.info(
                        f"[x402] Skipping payment — parent_task_id={parent_task_id} "
                        f"already paid by requester={requester}"
                    )
            except Exception as _e:
                logger.debug(f"[x402] Payment record check failed: {_e}")

        if payment_receipt is None:
            payment_receipt = await payment_gate.process_task_payment(
                task_id=f"pre-{int(time.time() * 1000)}",
                requester_id=requester,
                payment_data=request.payment,
                is_remote=True,
            )
            if not payment_receipt.approved:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=402,
                    content=payment_gate.build_402_response(payment_receipt),
                )
            # Persist payment record so re-requests for same parent_task_id skip payment
            if parent_task_id:
                try:
                    get_database().save_payment_record(parent_task_id, requester)
                except Exception as _e:
                    logger.debug(f"[x402] Failed to save payment record: {_e}")

    # Create or reuse task.
    # For delegated/refine requests, reuse master task ID so task-flow stays
    # incremental and payment/idempotency checks map to the same task lineage.
    canonical_task_id = parent_task_id or None
    if canonical_task_id:
        existing_task = task_manager.get_task(canonical_task_id)
        if existing_task and existing_task.status == TaskStatus.RUNNING:
            logger.info(
                "[REMOTE TASK] Task already running for parent_task_id=%s requester=%s; returning existing task",
                canonical_task_id,
                requester,
            )
            return AgentExecuteResponse(
                task_id=existing_task.id,
                status=existing_task.status.value,
                result=existing_task.result,
                error=existing_task.error,
                cost=existing_task.cost.to_dict() if getattr(existing_task, "cost", None) else None,
            )

    task_metadata: dict = {
        "remote": is_remote,
        "requester_id": requester,
        "memory_agent_id": memory_agent_id,
        "memory_recall_used": bool(memory_context),
    }
    if memory_session_id:
        task_metadata["session_id"] = memory_session_id
    task_metadata["original_prompt"] = request.task
    if payment_receipt:
        task_metadata["payment"] = payment_receipt.to_dict()
    if parent_task_id:
        task_metadata["parent_task_id"] = parent_task_id
        task_metadata["master_task_id"] = parent_task_id

    task = task_manager.create_task(
        prompt=request.task,
        user_id=requester,
        metadata=task_metadata,
        task_id=canonical_task_id,
        reuse_if_exists=bool(canonical_task_id),
        preserve_history=bool(canonical_task_id),
    )

    # --- Record wallet transaction (remote tasks only — income) ---
    if payment_receipt and payment_receipt.mode != "disabled":
        try:
            price_str = payment_receipt.amount.replace(" ETH", "").strip()
            price_val = float(price_str) if price_str else 0
        except (ValueError, AttributeError) as e:
            logger.warning(f"[x402] Could not parse payment amount '{payment_receipt.amount}': {e}")
            price_val = 0

        if price_val > 0:
            tx = _record_wallet_transaction(
                tx_type="income",
                amount=price_val,
                task_id=task.id,
        task_name=request.task[:80],
                description=f"Received task from {requester}",
                tx_hash=payment_receipt.tx_hash or "",
                payer=requester,
                payee=payment_receipt.payee,
                mode=payment_receipt.mode,
                network=payment_receipt.network,
            )

            # Broadcast wallet update to Dashboard
            await _subscription_manager.broadcast("wallet_transaction", {
                "transaction": tx,
                "balance": round(_st.mock_balance, 6),
            })

    # Broadcast task_created to Dashboard
    _task_created_payload: dict = {
        "id": task.id,
        "prompt": request.task,
        "status": "pending",
        "created_at": task.created_at,
        "remote": is_remote,
        "requester_id": requester,
        "metadata": dict(task_metadata),
    }
    if payment_receipt:
        _task_created_payload["payment"] = {
            "mode": payment_receipt.mode,
            "amount": payment_receipt.amount,
            "tx_hash": payment_receipt.tx_hash,
        }
    await _subscription_manager.broadcast("task_created", {
        "task": _task_created_payload
    })

    # --- Worker activity tracking for /api/agent/execute endpoint ---
    _exec_seen_workers: set = set()
    _exec_worker_step_count: dict = {}
    _exec_completed_workers: set = set()
    _exec_worker_states: dict[str, dict[str, Any]] = {}
    _remote_progress_by_node: dict = {}
    _remote_terminal_nodes: set = set()
    _remote_milestone_tracker: dict[str, tuple[Any, ...]] = {}

    def _seed_exec_worker_statuses(worker_names: list[str]) -> None:
        for idx, worker_name in enumerate(worker_names):
            upsert_worker_status(
                _exec_worker_states,
                worker_name,
                status="pending",
                action="queued",
                detail="Selected and waiting to start",
                step_count=_exec_worker_step_count.get(worker_name, 0),
                order_hint=idx,
            )

    def _sync_exec_worker_statuses() -> None:
        try:
            task_manager.update_progress(
                task.id,
                worker_statuses=serialize_worker_statuses(_exec_worker_states),
            )
        except Exception as prog_exc:
            logger.warning(
                "Failed to sync worker status roster for task=%s: %s",
                task.id,
                prog_exc,
                exc_info=True,
            )

    # Setup SSE streaming for task progress (broadcasts to Dashboard)
    # NOTE: CrewAI calls on_step synchronously from its thread, so this
    # must be a sync function that schedules the async broadcast.
    def on_step(step_data: dict):
        """Stream step updates to Dashboard (sync callback).

        Enriches each step with agent identity, worker tracking, and
        progress data before broadcasting via SSE.
        """
        from teaming24.utils.ids import resolve_agent_id as _resolve
        agent_name = normalize_agent_name(step_data.get("agent", "Unknown"))
        step_data["agent"] = agent_name
        _aid, _atype = _resolve(agent_name, {})
        explicit_type = step_data.get("agent_type")
        step_data["agent_id"] = _aid
        step_data["agent_type"] = explicit_type or _atype
        agent_type = step_data["agent_type"]
        action_str = str(step_data.get("action", "") or "").strip()
        action_key = action_str.lower()
        tool_name = str(step_data.get("tool", "") or "").strip()

        enriched = {**step_data, "task_id": task.id}
        if is_remote:
            enriched["remote"] = True
            enriched["requester_id"] = requester

        # --- Record wallet expense for each remote AN delegation ---
        if action_key == "payment_sent":
            pay_info = step_data.get("payment_info", {})
            if pay_info.get("is_retry"):
                pass  # Refinement round — same master task, no charge
            else:
                _def_amt, _mode, _network = _wallet_service.resolve_payment_defaults()
                _amt = pay_info.get("amount_num")
                if _amt is None:
                    _amt = _def_amt
                if _amt > 0:
                    _target = pay_info.get("target_an") or ""
                    _already = get_database().is_expense_recorded(task.id, _target)
                    if not _already:
                        tx = _record_wallet_transaction(
                            tx_type="expense",
                            amount=_amt,
                            task_id=task.id,
                            task_name=f"Delegation to {pay_info.get('target_an', 'Remote AN')}",
                            description=f"Payment to {pay_info.get('target_an', '')} ({pay_info.get('ip', '')}:{pay_info.get('port', '')})",
                            tx_hash="",
                            payer=requester,
                            payee=pay_info.get("target_an", ""),
                            mode=_mode,
                            network=_network,
                        )
                        try:
                            get_database().save_expense_record(task.id, _target, _amt)
                        except Exception as _e:
                            logger.debug("Failed to save expense record: %s", _e)
                        # Capture values now to avoid stale reads from lambda closure
                        _tx_snap = dict(tx)
                        _bal_snap = round(_st.mock_balance, 6)
                        import threading as _thr
                        _main_loop = getattr(_thr, '_teaming24_main_loop', None)
                        if _main_loop and _main_loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                _subscription_manager.broadcast("wallet_transaction", {
                                    "transaction": _tx_snap,
                                    "balance": _bal_snap,
                                }),
                                _main_loop,
                            )

        # --- Worker activity tracking for runtime callbacks ---
        if agent_type == "worker" and agent_name:
            counts_as_worker_step = action_key not in ("tool_heartbeat", "worker_heartbeat")
            if counts_as_worker_step:
                _exec_worker_step_count[agent_name] = _exec_worker_step_count.get(agent_name, 0) + 1
            _exec_seen_workers.add(agent_name)

            if action_key == "worker_completed":
                _exec_completed_workers.add(agent_name)
            elif action_key in ("worker_failed", "tool_error"):
                _exec_completed_workers.add(agent_name)

            current_detail = str(step_data.get("content", "") or "").strip()
            current_status = "running"
            detail_lower = current_detail.lower()
            if action_key in ("worker_completed",):
                current_status = "completed"
                if not current_detail:
                    current_detail = "Worker completed"
            elif action_key in ("worker_skipped",):
                current_status = "skipped"
                if not current_detail:
                    current_detail = "Worker skipped"
            elif action_key in ("worker_failed", "tool_error"):
                current_status = "failed"
                if not current_detail:
                    current_detail = "Worker failed"
            elif action_key == "tool_call" and "timed out" in detail_lower:
                current_status = "timeout"
                if not current_detail:
                    current_detail = "Command timed out"
            elif action_key in ("tool_heartbeat", "worker_heartbeat"):
                current_status = "running"
                current_detail = current_detail or "Healthy"
            elif action_key == "tool_start":
                current_status = "running"
                current_detail = current_detail or (f"Running {tool_name}" if tool_name else "Running")
            elif action_key == "tool_call":
                current_status = "running"
                current_detail = current_detail or (f"Finished {tool_name}" if tool_name else "Tool finished")
            elif action_key in ("queued", "pending"):
                current_status = "pending"
                current_detail = current_detail or "Waiting to start"
            elif not current_detail:
                current_detail = "Working"

            upsert_worker_status(
                _exec_worker_states,
                agent_name,
                status=current_status,
                action=action_key or "working",
                detail=current_detail,
                tool=tool_name or None,
                step_count=_exec_worker_step_count.get(agent_name, 0),
            )

            current_task = task_manager.get_task(task.id)
            current_progress = current_task.progress if current_task else None
            current_pct = int(getattr(current_progress, "percentage", 25) or 25)
            total_workers = max(int(getattr(current_progress, "total_workers", 0) or 0), len(_exec_seen_workers))
            completed_workers = max(
                int(getattr(current_progress, "completed_workers", 0) or 0),
                min(len(_exec_completed_workers), total_workers),
            )
            active_workers = max(total_workers - completed_workers, 0) if total_workers else len(_exec_seen_workers)

            worker_step_n = _exec_worker_step_count.get(agent_name, 0)
            if action_key == "tool_start" and tool_name:
                phase_label = f"{agent_name} running {tool_name}"
            elif action_key in ("tool_heartbeat", "worker_heartbeat"):
                phase_label = f"{agent_name} healthy"
                if tool_name:
                    phase_label += f" | {tool_name}"
            elif action_key == "tool_call" and tool_name:
                phase_label = f"{agent_name} finished {tool_name}"
            elif worker_step_n > 0:
                phase_label = f"{agent_name} step {worker_step_n}"
            else:
                phase_label = f"{agent_name} started"

            next_pct = current_pct
            if action_key in ("tool_start", "tool_heartbeat", "worker_heartbeat"):
                next_pct = max(current_pct, 40)
            elif counts_as_worker_step:
                next_pct = min(max(current_pct, 40) + (4 if worker_step_n <= 1 else 2), 85)

            try:
                task_manager.update_progress(
                    task.id,
                    phase="executing",
                    percentage=next_pct,
                    total_workers=total_workers,
                    completed_workers=completed_workers,
                    active_workers=active_workers,
                    current_agent=agent_name,
                    current_action=action_key,
                    phase_label=phase_label,
                    worker_statuses=serialize_worker_statuses(_exec_worker_states),
                )
            except Exception as prog_exc:
                logger.warning(
                    "Failed to update worker progress for task=%s agent=%s: %s",
                    task.id,
                    agent_name,
                    prog_exc,
                    exc_info=True,
                )

        elif agent_type in ("organizer", "router", "coordinator") and agent_name:
            current_task = task_manager.get_task(task.id)
            current_progress = current_task.progress if current_task else None
            current_phase = str(getattr(current_progress, "phase", "received") or "received")
            current_pct = int(getattr(current_progress, "percentage", 0) or 0)
            current_total = int(getattr(current_progress, "total_workers", 0) or 0)
            current_completed = int(getattr(current_progress, "completed_workers", 0) or 0)
            current_active = int(getattr(current_progress, "active_workers", 0) or 0)
            label = str(step_data.get("content", "") or "").strip()
            if action_key == "waiting_remote":
                label = label or "Waiting on remote AN(s)"
                current_phase = "executing"
                current_pct = max(current_pct, 55)
            elif current_phase in ("received", "planning", "routing", "dispatching"):
                if agent_type in ("organizer", "router"):
                    current_phase = "routing"
                    current_pct = max(current_pct, 10)
                else:
                    current_phase = "executing"
                    current_pct = max(current_pct, 25)
            elif agent_type == "coordinator" and current_phase not in ("completed", "aggregating"):
                current_phase = "executing"
                current_pct = max(current_pct, 25)

            try:
                task_manager.update_progress(
                    task.id,
                    phase=current_phase,
                    percentage=current_pct,
                    total_workers=current_total,
                    completed_workers=current_completed,
                    active_workers=current_active,
                    current_agent=agent_name,
                    current_action=action_key,
                    phase_label=(label or f"{agent_name} {action_key.replace('_', ' ')}")[:140],
                    worker_statuses=serialize_worker_statuses(_exec_worker_states),
                )
            except Exception as prog_exc:
                logger.warning(
                    "Failed to update workflow progress for task=%s agent=%s: %s",
                    task.id,
                    agent_name,
                    prog_exc,
                    exc_info=True,
                )

        # --- Remote AN progress aggregation (smooths 25% -> 100% jumps) ---
        remote_progress = step_data.get("remote_progress")
        if not isinstance(remote_progress, dict):
            remote_progress = {}
        if agent_type == "remote":
            stage_str = str(remote_progress.get("stage", "") or "").strip().lower()
            node_key = (
                step_data.get("remote_node_id")
                or step_data.get("agent_id")
                or agent_name
                or "remote"
            )
            if remote_progress:
                _remote_progress_by_node[node_key] = remote_progress

            if (
                action_key in ("remote_done", "remote_completed", "remote_failed")
                or stage_str in ("completed", "failed")
            ):
                _remote_terminal_nodes.add(node_key)

            known_nodes = set(_remote_progress_by_node.keys()) | set(_remote_terminal_nodes)
            if known_nodes:
                pct_values = []
                for _k in known_nodes:
                    p = _remote_progress_by_node.get(_k, {})
                    raw_pct = p.get("percentage")
                    stage_hint = str(p.get("stage", "") or "").strip().lower()
                    try:
                        pct_num = (
                            int(raw_pct)
                            if raw_pct is not None
                            else (
                                100
                                if _k in _remote_terminal_nodes
                                else remote_stage_default_pct(stage_hint)
                            )
                        )
                    except (TypeError, ValueError):
                        pct_num = 100 if _k in _remote_terminal_nodes else remote_stage_default_pct(stage_hint)
                    pct_values.append(max(0, min(100, pct_num)))

                avg_pct = int(sum(pct_values) / max(1, len(pct_values)))
                blended_pct = 25 + int((avg_pct / 100.0) * 55)
                blended_pct = max(25, min(80, blended_pct))

                current_task = task_manager.get_task(task.id)
                current_pct = current_task.progress.percentage if current_task else 0
                current_phase = str(current_task.progress.phase) if current_task else "executing"
                if current_phase == "completed":
                    current_phase = "completed"
                elif current_phase == "aggregating":
                    current_phase = "aggregating"
                else:
                    current_phase = "executing"
                phase_label = (
                    str(remote_progress.get("phase_label", "") or "").strip()
                    or (
                        str(remote_progress.get("stage", "") or "").strip().replace("_", " ").title()
                        if remote_progress else ""
                    )
                    or f"Remote execution {len(_remote_terminal_nodes)}/{len(known_nodes)}"
                )
                try:
                    task_manager.update_progress(
                        task.id,
                        phase=current_phase,
                        percentage=max(current_pct, blended_pct),
                        total_workers=len(known_nodes),
                        completed_workers=min(len(_remote_terminal_nodes), len(known_nodes)),
                        active_workers=max(len(known_nodes) - len(_remote_terminal_nodes), 0),
                        current_agent=agent_name,
                        current_action=action_key,
                        phase_label=phase_label,
                        worker_statuses=serialize_worker_statuses(_exec_worker_states),
                    )
                except Exception as prog_exc:
                    logger.warning(
                        "Failed to update remote progress for task=%s node=%s: %s",
                        task.id,
                        node_key,
                        prog_exc,
                        exc_info=True,
                    )

        # --- Build progress from task's progress data ---
        # The StepCallback and _wf_step already update the task's progress;
        # we just read and forward it to the frontend.
        progress_data = step_data.get("progress")
        if not progress_data and remote_progress:
            progress_data = remote_progress
        if not progress_data:
            # Fallback: read from task object directly
            current_task = task_manager.get_task(task.id)
            if current_task:
                progress_data = current_task.progress.to_dict()

        # Get executing/delegated agents from task
        current_task = task_manager.get_task(task.id)
        executing_agents = list(current_task.executing_agents) if current_task else []
        delegated_agents = list(current_task.delegated_agents) if current_task else []

        if agent_type == "remote" and not should_emit_remote_milestone(step_data, _remote_milestone_tracker):
            logger.debug(
                "Suppressed noisy remote milestone task=%s agent=%s action=%s",
                task.id,
                agent_name,
                action_str,
            )
            return

        # Log task step with structured format
        step_num = step_data.get("step_number", "?")
        content_preview = str(step_data.get("content", ""))[:100].replace("\n", " ")
        phase_str = progress_data.get("phase", "?") if progress_data else "?"
        pct_str = progress_data.get("percentage", "?") if progress_data else "?"
        logger.info(
            f"[{'REMOTE' if is_remote else 'LOCAL'} TASK] Step #{step_num}: "
            f"task={task.id}, agent={agent_name} ({agent_type}), "
            f"action={action_str}, phase={phase_str} ({pct_str}%), "
            f"content={content_preview}"
        )

        async def _broadcast():
            await _subscription_manager.broadcast("agent_step", enriched)
            await _subscription_manager.broadcast("task_step", {
                "task": {
                    "id": task.id,
                    "status": "running",
                    "steps": [{
                        "agent": step_data.get("agent"),
                        "action": step_data.get("action"),
                        "content": step_data.get("content"),
                        "thought": step_data.get("thought"),
                        "observation": step_data.get("observation"),
                        "agent_type": step_data.get("agent_type"),
                        "agent_id": step_data.get("agent_id"),
                        "timestamp": time.time(),
                        "step_number": step_data.get("step_number"),
                        "step_duration": step_data.get("step_duration"),
                    }],
                    "progress": progress_data,
                    "executing_agents": executing_agents,
                    "delegated_agents": delegated_agents,
                },
                "agent_id": step_data.get("agent_id"),
                "agent_type": step_data.get("agent_type"),
                "is_delegation": step_data.get("is_delegation", False),
            })
        # Schedule the broadcast on the running event loop
        _bcast_coro = _broadcast  # capture reference
        try:
            _step_loop = asyncio.get_running_loop()
            asyncio.ensure_future(_bcast_coro(), loop=_step_loop)
        except RuntimeError:
            # Called from a worker thread without a running loop —
            # schedule via call_soon_threadsafe on the main event loop.
            try:
                import threading
                main_loop = getattr(threading, '_teaming24_main_loop', None)
                if main_loop and main_loop.is_running():
                    def _schedule_bcast(_fn=_bcast_coro, _loop=main_loop):
                        asyncio.ensure_future(_fn(), loop=_loop)
                    main_loop.call_soon_threadsafe(_schedule_bcast)
                else:
                    logger.warning(
                        f"[on_step] No main loop available for broadcast: "
                        f"task={task.id}, action={step_data.get('action')}"
                    )
            except Exception as bcast_err:
                logger.warning(f"[on_step] Broadcast scheduling failed: {bcast_err}")

        # Add to sandbox event log if this task has a registered sandbox
        _PAYMENT_ACTS = {"payment_sent", "payment_approved", "payment_processing", "payment_received"}
        _task_sb_id = sandbox_id_for_task(task.id)
        _act = step_data.get("action", "")
        if _task_sb_id in _sandboxes and _act not in _PAYMENT_ACTS:
            _sb_entry = {
                "type": "info",
                "timestamp": time.time(),
                "data": {
                    "message": (
                        f"[{_act}] {step_data.get('content', '')[:200]}"
                        if step_data.get("content") else _act
                    ),
                    "agent": agent_name,
                    "tool": _act,
                },
            }
            _sandbox_events.setdefault(_task_sb_id, [])
            _sandbox_events[_task_sb_id].append(_sb_entry)
            _sandbox_events[_task_sb_id] = _sandbox_events[_task_sb_id][-config.api.max_events_kept:]
            try:
                get_database().save_sandbox_event(_task_sb_id, _sb_entry)
            except Exception as exc:
                logger.warning(
                    "Failed to persist sandbox event task=%s sandbox=%s: %s",
                    task.id,
                    _task_sb_id,
                    exc,
                    exc_info=True,
                )

    # Mark task as started (sets started_at, status=RUNNING)
    started_task = task_manager.start_task(task.id)
    if started_task is None:
        current = task_manager.get_task(task.id)
        if current and current.status == TaskStatus.RUNNING:
            logger.info(
                "Task %s is already running; returning current state",
                task.id,
            )
            return AgentExecuteResponse(
                task_id=current.id,
                status=current.status.value,
                result=current.result,
                error=current.error,
                cost=current.cost.to_dict() if getattr(current, "cost", None) else None,
            )
        raise HTTPException(status_code=409, detail=f"Task {task.id} cannot be started")
    task = started_task

    # Broadcast task_started
    logger.info(
        f"[{'REMOTE' if is_remote else 'LOCAL'} TASK] Starting execution: "
        f"task_id={task.id}, requester={requester}"
    )
    await _subscription_manager.broadcast("task_started", {
        "task": {
            "id": task.id,
            "prompt": request.task,
            "status": "running",
            "started_at": task.started_at or time.time(),
            "remote": is_remote,
            "requester_id": requester,
            "metadata": dict(task.metadata) if isinstance(task.metadata, dict) else {},
        }
    })

    # ---- Async mode: return task_id immediately, execute in background ----
    if request.async_mode:
        logger.info(
            f"[{'REMOTE' if is_remote else 'LOCAL'} TASK] Async mode — "
            f"returning task_id={task.id} immediately, execution in background"
        )

        async def _run_in_background():
            try:
                # Register a sandbox for this background task so the dashboard can track it
                bg_sandbox_id = sandbox_id_for_task(task.id)
                _sandboxes[bg_sandbox_id] = {
                    "id": bg_sandbox_id,
                    "name": f"{'Remote' if is_remote else 'Background'} Task",
                    "runtime": _runtime_backend_str(),
                    "state": "running",
                    "role": "task-execution",
                    "taskId": task.id,
                    "taskName": request.task[:50],
                    "agentName": LOCAL_COORDINATOR_NAME if is_remote else "Organizer",
                    "created_at": time.time(),
                    "last_heartbeat": time.time(),
                }
                _sandbox_events[bg_sandbox_id] = []
                await _broadcast_sandbox_update("registered", bg_sandbox_id)

                # ── x402 payment visibility (remote tasks only) ──
                # Only show the "payment received" step for remote tasks
                # (a remote AN paid us). Local tasks never self-pay.
                if is_remote:
                    _bg_pay = (task.metadata or {}).get("payment", {})
                    _bg_pay_mode = _bg_pay.get("mode", "disabled")
                    if _bg_pay_mode != "disabled":
                        _bg_amt = _bg_pay.get("amount", "0")
                        _bg_net = _bg_pay.get("network", "")
                        _bg_tx = _bg_pay.get("tx_hash", "")
                        _bg_tx_short = (_bg_tx[:18] + "...") if _bg_tx and len(_bg_tx) > 18 else _bg_tx
                        logger.info(
                            f"[x402] Payment received from remote AN: mode={_bg_pay_mode}, "
                            f"amount={_bg_amt}, tx={_bg_tx_short}"
                        )
                        on_step({
                            "agent": "Organizer",
                            "agent_type": "organizer",
                            "action": "payment_processing",
                            "content": (
                                f"💳 x402 Payment received — {_bg_amt} "
                                f"from {requester} (mode: {_bg_pay_mode})"
                            ),
                            "is_delegation": False,
                            "payment": {"mode": _bg_pay_mode, "amount": _bg_amt,
                                        "network": _bg_net, "tx_hash": _bg_tx},
                        })
                        on_step({
                            "agent": "Organizer",
                            "agent_type": "organizer",
                            "action": "payment_approved",
                            "content": f"✅ Payment received — {_bg_amt} (tx: {_bg_tx_short})",
                            "is_delegation": False,
                            "payment": {"mode": _bg_pay_mode, "amount": _bg_amt,
                                        "tx_hash": _bg_tx, "status": "approved"},
                        })

                logger.info(
                    f"[{'REMOTE' if is_remote else 'LOCAL'} TASK] Background: "
                    f"creating crew for task_id={task.id}"
                )
                network_manager = get_network_manager()
                runtime_settings = get_agent_runtime_settings()
                crew = create_local_crew(
                    task_manager, on_step=on_step,
                    runtime_settings=runtime_settings,
                )
                _seed_exec_worker_statuses([
                    str(getattr(worker, "role", "") or "").strip()
                    for worker in getattr(crew, "workers", [])
                    if str(getattr(worker, "role", "") or "").strip()
                ])
                _sync_exec_worker_statuses()
                # Propagate the incoming chain so core.py can append this node's
                # UID before forwarding to further remote ANs.
                crew._delegation_chain = chain

                worker_roles = [getattr(a, 'role', '?') for a in crew.workers]
                logger.info(
                    f"[{'REMOTE' if is_remote else 'LOCAL'} TASK] Background: "
                    f"crew created with {len(crew.agents)} agents, "
                    f"workers={worker_roles}, coordinator={getattr(crew.coordinator, 'role', 'N/A')}"
                )

                exec_start = time.time()
                if is_remote:
                    # Remote task: bypass Organizer/ANRouter, go directly
                    # to Coordinator → Workers
                    logger.info(
                        f"[REMOTE TASK] Background: dispatching to Coordinator-only path "
                        f"(task_id={task.id}, prompt={request.task[:80]}...)"
                    )
                    result = await crew.execute_as_coordinator(request.task, task.id)
                else:
                    # Local task: full Organizer → ANRouter → dispatch flow
                    pool = _build_agentic_node_workforce_pool(crew, network_manager)
                    crew.bind_workforce_pool(pool, task_id=task.id)
                    result = await crew.execute(request.task, task.id)
                exec_duration = time.time() - exec_start

                result_status = result.get("status", "unknown")
                logger.info(
                    f"[{'REMOTE' if is_remote else 'LOCAL'} TASK] Background completed: "
                    f"task_id={task.id}, status={result_status}, "
                    f"duration={exec_duration:.1f}s"
                )

                if result_status == "error":
                    # The crew returned an error — task_manager.fail_task was
                    # already called inside the crew method.
                    if bg_sandbox_id in _sandboxes:
                        _sandboxes[bg_sandbox_id]["state"] = "error"
                        _sandboxes[bg_sandbox_id]["fetchError"] = result.get("error", "")[:200]
                        await _broadcast_sandbox_update("state_changed", bg_sandbox_id)
                    final_task = task_manager.get_task(task.id)
                    final_progress = final_task.progress.to_dict() if final_task else None
                    await _subscription_manager.broadcast("task_failed", {
                        "task": {
                            "id": task.id,
                            "status": "failed",
                            "error": result.get("error", "Unknown error"),
                            "completed_at": final_task.completed_at if final_task and final_task.completed_at else time.time(),
                            "remote": is_remote,
                            "requester_id": requester,
                            "progress": final_progress,
                        }
                    })
                    try:
                        db = get_database()
                        db.save_task({
                            "id": task.id,
                            "name": request.task[:100],
                            "description": request.task,
                            "status": "failed",
                            "task_type": "remote" if is_remote else "local",
                            "assigned_to": final_task.assigned_to if final_task else None,
                            "delegated_agents": final_task.delegated_agents if final_task else [],
                            "executing_agents": final_task.executing_agents if final_task else [],
                            "steps": _serialize_task_steps_for_db(final_task.steps if final_task else []),
                            "result": final_task.result if final_task else None,
                            "error": result.get("error", "Unknown error"),
                            "cost": final_task.cost.to_dict() if final_task else {},
                            "output_dir": final_task.output_dir if final_task else None,
                            "created_at": final_task.created_at if final_task else time.time(),
                            "started_at": final_task.started_at if final_task else None,
                            "completed_at": time.time(),
                            "metadata": {
                                "requester_id": requester,
                                "origin": "remote" if is_remote else "local",
                                "pool_members": final_task.pool_members if final_task else None,
                                "selected_members": final_task.selected_members if final_task else None,
                                "execution_mode": final_task.execution_mode if final_task else None,
                                "progress": final_task.progress.to_dict() if final_task else None,
                            },
                        })
                    except Exception as db_err:
                        logger.warning(f"Failed to persist background failed task to DB: {db_err}")
                else:
                    # Mark sandbox as completed
                    if bg_sandbox_id in _sandboxes:
                        _sandboxes[bg_sandbox_id]["state"] = "completed"
                        _sandboxes[bg_sandbox_id]["duration"] = exec_duration
                        await _broadcast_sandbox_update("state_changed", bg_sandbox_id)

                    # Collect final task state for broadcast
                    final_task = task_manager.get_task(task.id)
                    final_delegated = list(final_task.delegated_agents) if final_task else []
                    final_executing = list(final_task.executing_agents) if final_task else []
                    final_progress = final_task.progress.to_dict() if final_task else None
                    final_step_count = final_task.step_count if final_task else 0

                    await _subscription_manager.broadcast("task_completed", {
                        "task": {
                            "id": task.id,
                            "status": "completed",
                            "result": result.get("result"),
                            "cost": result.get("cost"),
                            "duration": exec_duration,
                            "completed_at": final_task.completed_at if final_task else time.time(),
                            "remote": is_remote,
                            "requester_id": requester,
                            "delegated_agents": final_delegated,
                            "executing_agents": final_executing,
                            "assigned_to": final_task.assigned_to if final_task else None,
                            "progress": final_progress,
                            "step_count": final_step_count,
                        }
                    })

                    # Persist task (including steps) to DB so frontend DB sync
                    # doesn't lose step data accumulated via SSE.
                    try:
                        db = get_database()
                        task_steps_list = final_task.steps if final_task else []
                        db.save_task({
                            "id": task.id,
                            "name": request.task[:100],
                            "description": request.task,
                            "status": "completed",
                            "task_type": "remote" if is_remote else "local",
                            "assigned_to": final_task.assigned_to if final_task else None,
                            "delegated_agents": final_delegated,
                            "executing_agents": final_executing,
                            "steps": _serialize_task_steps_for_db(task_steps_list),
                            "result": result.get("result"),
                            "cost": result.get("cost"),
                            "output_dir": result.get("output_dir"),
                            "created_at": final_task.created_at if final_task else time.time(),
                            "started_at": final_task.started_at if final_task else None,
                            "completed_at": time.time(),
                            "metadata": {
                                "requester_id": requester,
                                "origin": "remote" if is_remote else "local",
                                "pool_members": final_task.pool_members if final_task else None,
                                "selected_members": final_task.selected_members if final_task else None,
                                "execution_mode": final_task.execution_mode if final_task else None,
                                "progress": final_task.progress.to_dict() if final_task else None,
                            },
                        })
                    except Exception as db_err:
                        logger.warning(f"Failed to persist background task to DB: {db_err}")

            except Exception as exc:
                logger.error(
                    f"[{'REMOTE' if is_remote else 'LOCAL'} TASK] Background failed: "
                    f"task_id={task.id}, error={exc}"
                )
                # Mark sandbox as error
                if bg_sandbox_id in _sandboxes:
                    _sandboxes[bg_sandbox_id]["state"] = "error"
                    _sandboxes[bg_sandbox_id]["fetchError"] = str(exc)[:200]
                    await _broadcast_sandbox_update("state_changed", bg_sandbox_id)
                task_manager.fail_task(task.id, str(exc))
                final_task = task_manager.get_task(task.id)
                final_progress = final_task.progress.to_dict() if final_task else None
                await _subscription_manager.broadcast("task_failed", {
                    "task": {
                        "id": task.id,
                        "status": "failed",
                        "error": str(exc),
                        "completed_at": final_task.completed_at if final_task else time.time(),
                        "remote": is_remote,
                        "requester_id": requester,
                        "progress": final_progress,
                    }
                })
                try:
                    db = get_database()
                    db.save_task({
                        "id": task.id,
                        "name": request.task[:100],
                        "description": request.task,
                        "status": "failed",
                        "task_type": "remote" if is_remote else "local",
                        "assigned_to": final_task.assigned_to if final_task else None,
                        "delegated_agents": final_task.delegated_agents if final_task else [],
                        "executing_agents": final_task.executing_agents if final_task else [],
                        "steps": _serialize_task_steps_for_db(final_task.steps if final_task else []),
                        "result": final_task.result if final_task else None,
                        "error": str(exc),
                        "cost": final_task.cost.to_dict() if final_task else {},
                        "output_dir": final_task.output_dir if final_task else None,
                        "created_at": final_task.created_at if final_task else time.time(),
                        "started_at": final_task.started_at if final_task else None,
                        "completed_at": time.time(),
                        "metadata": {
                            "requester_id": requester,
                            "origin": "remote" if is_remote else "local",
                            "pool_members": final_task.pool_members if final_task else None,
                            "selected_members": final_task.selected_members if final_task else None,
                            "execution_mode": final_task.execution_mode if final_task else None,
                            "progress": final_task.progress.to_dict() if final_task else None,
                        },
                    })
                except Exception as db_err:
                    logger.warning(f"Failed to persist background exception task to DB: {db_err}")

        import asyncio as _asyncio
        _asyncio.ensure_future(_run_in_background())

        return AgentExecuteResponse(
            task_id=task.id,
            status="pending",
            result=None,
        )

    # ---- Synchronous mode (default): wait for completion ----
    sync_sandbox_id: str | None = None
    try:
        # Register sandbox for sync mode so on_step can add events (same as async)
        sync_sandbox_id = sandbox_id_for_task(task.id)
        _sandboxes[sync_sandbox_id] = {
            "id": sync_sandbox_id,
            "name": f"{'Remote' if is_remote else 'Local'} Task",
            "runtime": _runtime_backend_str(),
            "state": "running",
            "role": "task-execution",
            "taskId": task.id,
            "taskName": request.task[:50],
            "agentName": LOCAL_COORDINATOR_NAME if is_remote else "Organizer",
            "created": time.time(),
            "last_heartbeat": time.time(),
        }
        _sandbox_events[sync_sandbox_id] = []
        await _broadcast_sandbox_update("registered", sync_sandbox_id)

        # Create and execute crew
        logger.info(
            f"[{'REMOTE' if is_remote else 'LOCAL'} TASK] Sync mode: "
            f"creating crew for task_id={task.id}"
        )
        network_manager = get_network_manager()
        runtime_settings = get_agent_runtime_settings()
        crew = create_local_crew(task_manager, on_step=on_step, runtime_settings=runtime_settings)
        _seed_exec_worker_statuses([
            str(getattr(worker, "role", "") or "").strip()
            for worker in getattr(crew, "workers", [])
            if str(getattr(worker, "role", "") or "").strip()
        ])
        _sync_exec_worker_statuses()
        # Propagate the incoming chain so core.py can append this node's
        # UID before forwarding to further remote ANs.
        crew._delegation_chain = chain

        worker_roles = [getattr(a, 'role', '?') for a in crew.workers]
        logger.info(
            f"[{'REMOTE' if is_remote else 'LOCAL'} TASK] Sync mode: "
            f"crew created with {len(crew.agents)} agents, "
            f"workers={worker_roles}, coordinator={getattr(crew.coordinator, 'role', 'N/A')}"
        )

        exec_start = time.time()
        try:
            from teaming24.agent.tools.memory_tools import memory_tool_context
        except Exception:
            memory_tool_context = None  # type: ignore[assignment]
        if is_remote:
            # Remote task from another AN: bypass Organizer/ANRouter,
            # go directly to Coordinator → Workers.
            logger.info(
                f"[REMOTE TASK] Dispatching to Coordinator-only path: "
                f"task_id={task.id}, requester={requester}, "
                f"prompt={request.task[:80]}..."
            )
            if memory_tool_context is not None:
                with memory_tool_context(agent_id=memory_agent_id, session_id=memory_session_id):
                    result = await crew.execute_as_coordinator(execution_task_text, task.id)
            else:
                result = await crew.execute_as_coordinator(execution_task_text, task.id)
        else:
            # Local task from chat UI: full Organizer → ANRouter → dispatch flow.
            # Build Agentic Node Workforce Pool and bind to Organizer's tools so it can
            # route tasks to remote ANs (not just the local Coordinator).
            pool = _build_agentic_node_workforce_pool(crew, network_manager)
            crew.bind_workforce_pool(pool, task_id=task.id)
            if memory_tool_context is not None:
                with memory_tool_context(agent_id=memory_agent_id, session_id=memory_session_id):
                    result = await crew.execute(execution_task_text, task.id)
            else:
                result = await crew.execute(execution_task_text, task.id)
        exec_duration = time.time() - exec_start

        result_preview = str(result.get("result", ""))[:150].replace('\n', ' ')
        logger.info(
            f"[{'REMOTE' if is_remote else 'LOCAL'} TASK] Completed: "
            f"task_id={task.id}, status={result.get('status', 'unknown')}, "
            f"duration={exec_duration:.1f}s"
        )
        if is_remote:
            logger.info(
                f"[REMOTE TASK] Result preview: {result_preview}..."
            )

        # Collect final task state for broadcast
        final_task = task_manager.get_task(task.id)
        final_delegated = list(final_task.delegated_agents) if final_task else []
        final_executing = list(final_task.executing_agents) if final_task else []
        final_progress = final_task.progress.to_dict() if final_task else None
        final_step_count = final_task.step_count if final_task else 0

        # Mark sync sandbox as completed
        if sync_sandbox_id is not None and sync_sandbox_id in _sandboxes:
            _sandboxes[sync_sandbox_id]["state"] = "completed"
            _sandboxes[sync_sandbox_id]["duration"] = exec_duration
            await _broadcast_sandbox_update("state_changed", sync_sandbox_id)

        # Broadcast task_completed to Dashboard
        await _subscription_manager.broadcast("task_completed", {
            "task": {
                "id": task.id,
                "status": "completed",
                "result": result.get("result"),
                "cost": result.get("cost"),
                "duration": exec_duration,
                "completed_at": final_task.completed_at if final_task else time.time(),
                "remote": is_remote,
                "requester_id": requester,
                "delegated_agents": final_delegated,
                "executing_agents": final_executing,
                "assigned_to": final_task.assigned_to if final_task else None,
                "progress": final_progress,
                "step_count": final_step_count,
            }
        })

        try:
            asyncio.create_task(
                persist_agent_memory_after_completion(
                    agent_id=memory_agent_id,
                    session_id=memory_session_id,
                    task_id=task.id,
                    user_message=request.task,
                    assistant_message=str(result.get("result") or ""),
                    cfg=config,
                    logger=logger,
                    resolve_runtime_chat_model=_resolve_runtime_chat_model,
                )
            )
        except Exception as memory_exc:
            logger.warning(
                "Failed to schedule agent memory persistence task=%s: %s",
                task.id,
                memory_exc,
                exc_info=True,
            )

        return AgentExecuteResponse(
            task_id=task.id,
            status=result.get("status", "unknown"),
            result=result.get("result"),
            error=result.get("error"),
            cost=result.get("cost"),
            duration=result.get("duration"),
        )

    except Exception as e:
        logger.error(
            f"[{'REMOTE' if is_remote else 'LOCAL'} TASK] Failed: "
            f"task_id={task.id}, error={e}"
        )
        task_manager.fail_task(task.id, str(e))

        # Mark sync sandbox as error
        if sync_sandbox_id is not None and sync_sandbox_id in _sandboxes:
            _sandboxes[sync_sandbox_id]["state"] = "error"
            _sandboxes[sync_sandbox_id]["fetchError"] = str(e)[:200]
            await _broadcast_sandbox_update("state_changed", sync_sandbox_id)

        # Collect final task state for broadcast
        final_task = task_manager.get_task(task.id)
        final_progress = final_task.progress.to_dict() if final_task else None

        # Broadcast task_failed to Dashboard
        await _subscription_manager.broadcast("task_failed", {
            "task": {
                "id": task.id,
                "status": "failed",
                "error": str(e),
                "completed_at": final_task.completed_at if final_task else time.time(),
                "remote": is_remote,
                "requester_id": requester,
                "progress": final_progress,
            }
        })

        return AgentExecuteResponse(
            task_id=task.id,
            status="error",
            error=str(e),
        )


def _check_llm_api_keys() -> tuple[bool, str]:
    """
    Check whether a usable LLM runtime configuration is available.

    Returns:
        Tuple of (is_configured, error_message)
    """
    resolved_model, call_params, provider, err = _resolve_runtime_chat_model()
    if err:
        return False, err
    if not resolved_model:
        return False, "No default LLM model configured."
    api_key = str(call_params.get("api_key", "")).strip()
    api_base = str(call_params.get("api_base", "")).strip()
    if provider == "flock":
        if api_key:
            return True, ""
        return False, "Provider 'flock' requires API key (FLOCK_API_KEY or Settings -> LLM)."
    if provider == "anthropic":
        if api_key:
            return True, ""
        return False, "Provider 'anthropic' requires API key (ANTHROPIC_API_KEY or Settings -> LLM)."
    if provider == "openai":
        if api_key or api_base:
            return True, ""
        return False, "Provider 'openai' requires OPENAI_API_KEY or OPENAI_API_BASE."
    if provider == "local":
        if api_base or api_key:
            return True, ""
        return False, "Provider 'local' requires base URL (LOCAL_LLM_API_BASE or Settings -> LLM)."
    if api_key or api_base:
        return True, ""
    return False, f"Provider '{provider}' is not configured with api_key/api_base."


def _resolve_runtime_chat_model() -> tuple[str, dict[str, Any], str, str]:
    """Resolve runtime chat model/provider using config + DB overrides."""
    try:
        from teaming24.llm.model_resolver import (
            build_runtime_llm_config,
            resolve_model_and_call_params,
        )

        runtime_settings = get_agent_runtime_settings()
        cfg = get_config()
        runtime_provider = str(
            runtime_settings.get("defaultLLMProvider")
            or runtime_settings.get("default_llm_provider")
            or ""
        ).strip() or None
        llm_cfg = build_runtime_llm_config(
            cfg.llm,
            runtime_settings=runtime_settings,
            runtime_default_provider=runtime_provider,
        )
        model_name = str(
            runtime_settings.get("defaultModel")
            or runtime_settings.get("default_model")
            or ""
        ).strip()
        resolved_model, call_params, provider = resolve_model_and_call_params(model_name, llm_cfg)
        return resolved_model, call_params, provider, ""
    except Exception as exc:
        logger.error("Failed to resolve runtime chat model: %s", exc, exc_info=True)
        return "", {}, "", f"Failed to resolve LLM runtime config: {exc}"




@app.post("/api/chat/simple")
async def chat_simple(request: ChatRequest):
    """
    Simple chat endpoint - direct LLM call without multi-agent workflow.

    This provides fast, direct responses for simple Q&A without:
    - Task creation/tracking
    - Agent delegation
    - Dashboard visibility

    Good for quick questions and conversational interaction.
    """
    # Check LLM API keys
    has_api_key, api_error = _check_llm_api_keys()
    if not has_api_key:
        logger.error("LLM runtime not configured: %s", api_error)
        error_msg = f"⚠️ LLM not configured: {api_error}"
        if request.stream:
            async def error_stream():
                yield f"data: {json.dumps({'type': 'error', 'error': error_msg})}\n\n"
                yield f"data: {json.dumps({'type': 'result', 'content': error_msg})}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(error_stream(), media_type="text/event-stream")
        return {"error": error_msg}

    # Extract user message
    user_message = ""
    for msg in reversed(request.messages):
        if msg.role == "user":
            user_message = msg.content
            break

    if not user_message:
        return {"error": "No user message found"}

    if request.stream:
        return StreamingResponse(
            generate_simple_chat_response(request.messages),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
        )
    else:
        # Non-streaming: collect full response
        full_content = ""
        async for chunk in generate_simple_chat_response(request.messages):
            if chunk.startswith("data: ") and "[DONE]" not in chunk:
                try:
                    data = json.loads(chunk[6:])
                    if data.get("type") == "stream":
                        full_content += data.get("content", "")
                    elif data.get("type") == "result":
                        full_content = data.get("content", full_content)
                except Exception as e:
                    logger.debug(f"Failed to parse SSE chunk: {e}")
        return {"content": full_content}


async def generate_simple_chat_response(messages: list[Message]):
    """
    Generate streaming response using direct LLM call.

    Uses runtime-resolved provider/model configuration for direct chat.
    """
    import time
    start_time = time.time()
    total_tokens = {"input": 0, "output": 0}

    try:
        # Build messages for LLM
        system_prompt = """You are a helpful AI assistant. Respond concisely and accurately.
If the user asks in Chinese, respond in Chinese. If in English, respond in English."""

        llm_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            llm_messages.append({"role": msg.role, "content": msg.content})

        resolved_model, call_params, provider, resolve_error = _resolve_runtime_chat_model()
        if resolve_error:
            yield f"data: {json.dumps({'type': 'error', 'error': resolve_error})}\n\n"
            return
        if not resolved_model:
            yield f"data: {json.dumps({'type': 'error', 'error': 'No LLM configured'})}\n\n"
            return

        full_content = ""
        try:
            import litellm

            yield f"data: {json.dumps({'type': 'stream_start'})}\n\n"

            stream = await litellm.acompletion(
                model=resolved_model,
                messages=llm_messages,
                stream=True,
                max_tokens=4096,
                **call_params,
            )

            async for chunk in stream:
                content = ""
                try:
                    choice = (chunk.choices or [None])[0]
                    delta = getattr(choice, "delta", None) if choice is not None else None
                    if delta is not None:
                        content = (
                            getattr(delta, "content", None)
                            or (delta.get("content") if isinstance(delta, dict) else "")
                            or ""
                        )
                    if not content:
                        message = getattr(choice, "message", None) if choice is not None else None
                        content = (
                            getattr(message, "content", None)
                            or (message.get("content") if isinstance(message, dict) else "")
                            or ""
                        )
                except Exception as parse_exc:
                    logger.debug("Failed to parse litellm stream chunk: %s", parse_exc)
                    content = ""

                if content:
                    full_content += content
                    total_tokens["output"] += 1
                    yield f"data: {json.dumps({'type': 'stream', 'content': content})}\n\n"

                usage = getattr(chunk, "usage", None)
                if usage:
                    total_tokens["input"] = int(getattr(usage, "prompt_tokens", 0) or 0)
                    total_tokens["output"] = int(getattr(usage, "completion_tokens", 0) or total_tokens["output"])

        except ImportError as exc:
            logger.error("litellm not installed: %s", exc, exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'error': 'litellm is not installed. Run: uv pip install litellm'})}\n\n"
            return
        except Exception as llm_exc:
            logger.error(
                "Simple chat LLM stream failed provider=%s model=%s: %s",
                provider,
                resolved_model,
                llm_exc,
                exc_info=True,
            )
            yield f"data: {json.dumps({'type': 'error', 'error': str(llm_exc)})}\n\n"
            return

        # Calculate duration
        duration = time.time() - start_time

        # Send final result with cost info
        yield f"data: {json.dumps({'type': 'result', 'content': full_content, 'cost': {'input_tokens': total_tokens['input'], 'output_tokens': total_tokens['output'], 'total_tokens': total_tokens['input'] + total_tokens['output']}, 'duration': round(duration, 2)})}\n\n"
        yield "data: [DONE]\n\n"

    except Exception as e:
        logger.error(f"Simple chat error: {e}")
        yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"


@app.post("/api/chat/agent")
async def chat_with_agent(request: ChatRequest):
    """
    Chat endpoint that uses the multi-agent framework (Task mode).

    This integrates CrewAI agents to handle user requests:
    1. Parse the user message
    2. Determine if local agents can handle it
    3. If not, delegate to remote nodes via AgentaNet
    4. Stream progress and return results
    """
    # Check agent framework availability (native or CrewAI)
    if not check_agent_framework_available():
        logger.warning("No agent framework available - falling back to simple chat.")
        return await chat(request)

    # Check LLM API keys
    has_api_key, api_error = _check_llm_api_keys()
    if not has_api_key:
        logger.warning("LLM runtime not configured - falling back to simple chat: %s", api_error)
        return await chat(request)

    # Extract user message
    user_message = ""
    for msg in reversed(request.messages):
        if msg.role == "user":
            user_message = msg.content
            break

    if not user_message:
        return {"error": "No user message found"}

    memory_agent_id = ORGANIZER_ID
    memory_session_id = str(request.session_id or "").strip() or None
    agent_memory_context = load_agent_memory_context(
        memory_agent_id, user_message, session_id=memory_session_id, cfg=config, logger=logger
    )
    agent_prompt = build_agent_execution_prompt(
        request.messages,
        user_message,
        cfg=config,
        agent_memory_context=agent_memory_context,
    )

    # Route based on explicit mode: 'chat' → direct LLM (no task/dashboard overhead)
    if request.mode == 'chat':
        logger.debug(f"[ChatAgent] Chat mode — direct LLM response: {user_message[:80]!r}")
        if request.stream:
            return StreamingResponse(
                generate_simple_chat_response(request.messages),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )
        full_content = ""
        async for chunk in generate_simple_chat_response(request.messages):
            if chunk.startswith("data: ") and "[DONE]" not in chunk:
                try:
                    data = json.loads(chunk[6:])
                    if data.get("type") == "stream":
                        full_content += data.get("content", "")
                    elif data.get("type") == "result":
                        full_content = data.get("content", full_content)
                except Exception:
                    logger.warning("Failed to parse simple chat stream chunk", exc_info=True)
        return {"content": full_content}

    task_manager = get_task_manager_instance()

    task = task_manager.create_task(
        prompt=user_message,
        metadata={
            "original_prompt": user_message,
            "session_id": request.session_id,
            "context_message_count": max(0, len(request.messages) - 1),
            "memory_agent_id": memory_agent_id,
            "memory_recall_used": bool(agent_memory_context),
        },
    )

    if request.stream:
        return StreamingResponse(
            generate_agent_response(agent_prompt, task.id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
        )
    else:
        # Non-streaming: delegate to Gateway for unified pipeline
        from teaming24.gateway import get_gateway
        gw = get_gateway()
        result = await gw.execute(
            agent_prompt,
            channel="webchat",
            peer_id="api",
            task_id=task.id,
            skip_payment=True,  # payment already checked above
        )
        return {
            "task_id": task.id,
            "content": result.get("result", ""),
            "cost": result.get("cost", {}),
            "status": result.get("status", "unknown"),
        }


@app.get("/api/chat/tasks/{task_id}/reconnect")
async def chat_task_reconnect(task_id: str):
    """Reconnect to a running (or completed) chat task's SSE stream.

    Replays all buffered SSE events that the original ``/api/chat/agent``
    stream produced, then continues streaming live events if the task is
    still running.  This enables seamless page-switch behaviour: the user
    can navigate away and come back without losing any progress.

    If the buffer has been lost (server restart), falls back to
    reconstructing events from the persisted task steps.
    """
    import asyncio as _aio

    task_manager = get_task_manager_instance()
    task = task_manager.get_task(task_id)
    if not task:
        _task_404()

    async def _reconnect_stream():
        # ── Phase 1: Replay buffered events ──
        with _chat_event_buffer_lock:
            buffered = list(_chat_event_buffer.get(task_id, []))

        if buffered:
            for evt in buffered:
                yield evt
        else:
            # Buffer lost (e.g. server restart) — reconstruct from task data
            yield f"data: {json.dumps({'type': 'task_started', 'task_id': task_id})}\n\n"
            for step in task.steps:
                agent_type = "worker"
                agent_name = step.agent or "Unknown"
                al = agent_name.lower()
                if "organizer" in al:
                    agent_type = "organizer"
                elif "coordinator" in al:
                    agent_type = "coordinator"
                elif "router" in al or "anrouter" in al:
                    agent_type = "router"
                yield f"data: {json.dumps({'type': 'step', 'agent': agent_name, 'agent_type': agent_type, 'action': step.action, 'content': step.content, 'thought': step.thought, 'observation': step.observation, 'step_number': step.step_number})}\n\n"

            if task.status.value in ("completed", "failed"):
                if task.status.value == "failed":
                    yield f"data: {json.dumps({'type': 'error', 'error': task.error or 'Task failed'})}\n\n"
                else:
                    result_content = task.result or ""
                    cost = task.cost.to_dict() if task.cost else {}
                    yield f"data: {json.dumps({'type': 'result', 'content': result_content, 'cost': cost, 'duration': task.duration})}\n\n"

        # ── Phase 2: If task is still running, stream live updates ──
        terminal_states = {"completed", "failed", "cancelled"}
        if task.status.value in terminal_states:
            yield f"data: {json.dumps({'type': 'reconnect_done', 'status': task.status.value})}\n\n"
            return

        # Subscribe to live events via the buffer growing
        last_idx = len(buffered)
        poll_count = 0
        while True:
            current_task = task_manager.get_task(task_id)
            if not current_task or current_task.status.value in terminal_states:
                # Drain any remaining buffered events
                with _chat_event_buffer_lock:
                    remaining = list(_chat_event_buffer.get(task_id, []))[last_idx:]
                for evt in remaining:
                    yield evt
                yield f"data: {json.dumps({'type': 'reconnect_done', 'status': current_task.status.value if current_task else 'unknown'})}\n\n"
                return

            with _chat_event_buffer_lock:
                buf = _chat_event_buffer.get(task_id, [])
                new_events = buf[last_idx:]
                last_idx = len(buf)

            for evt in new_events:
                yield evt
                poll_count = 0

            poll_count += 1
            await _aio.sleep(0.2 if poll_count < 50 else 1.0)

    return StreamingResponse(
        _reconnect_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


def _serialize_task_step_for_db(step: Any) -> dict[str, Any]:
    """Normalize step payload to a durable schema for DB persistence."""
    if hasattr(step, "to_dict"):
        raw = dict(step.to_dict() or {})
    elif isinstance(step, dict):
        raw = dict(step)
    else:
        raw = {}

    timestamp = raw.get("timestamp")
    if isinstance(timestamp, (int, float)):
        started_at = float(timestamp)
    else:
        started_at = None

    duration = raw.get("duration", raw.get("step_duration"))
    completed_at = None
    if started_at is not None and isinstance(duration, (int, float)):
        completed_at = started_at + float(duration)

    return {
        "id": raw.get("id") or raw.get("step_id"),
        "agent": raw.get("agent"),
        "agent_id": raw.get("agent_id"),
        "agent_name": raw.get("agent_name") or raw.get("agent"),
        "agent_type": raw.get("agent_type"),
        "action": raw.get("action"),
        "status": raw.get("status") or "running",
        "input": raw.get("input", raw.get("thought")),
        "output": raw.get("output", raw.get("content", raw.get("observation"))),
        "content": raw.get("content"),
        "thought": raw.get("thought"),
        "observation": raw.get("observation"),
        "error": raw.get("error"),
        "step_number": raw.get("step_number"),
        "timestamp": timestamp,
        "duration": duration,
        "step_duration": raw.get("step_duration", duration),
        "started_at": raw.get("started_at", started_at),
        "completed_at": raw.get("completed_at", completed_at),
        "worker_count": raw.get("worker_count"),
        "selected_members": raw.get("selected_members"),
    }


def _serialize_task_steps_for_db(steps: list[Any] | None) -> list[dict[str, Any]]:
    """Normalize all step payloads for durable DB storage."""
    return [_serialize_task_step_for_db(s) for s in (steps or [])]


async def generate_agent_response(prompt: str, task_id: str):
    """
    Generate streaming response from agent execution.

    Streams:
    1. Task creation confirmation
    2. Periodic heartbeat events (to show processing is happening)
    3. Agent thinking steps
    4. Sandbox events (when tools are used)
    5. Final result with cost

    Also broadcasts to _subscription_manager for Dashboard sync.
    """
    import queue
    import time

    task_manager = get_task_manager_instance()
    task = task_manager.get_task(task_id)

    # Guard: task must exist (caller creates it before invoking)
    if not task:
        logger.warning(f"[generate_agent_response] Task {task_id} not found — refusing to execute")
        yield f"data: {json.dumps({'type': 'error', 'error': f'Task {task_id} not found'})}\n\n"
        return
    # Guard: refuse to execute if task is already running/completed/failed
    if task.status.value in ("running", "completed", "failed"):
        logger.warning(
            f"[generate_agent_response] Task {task_id} already "
            f"'{task.status.value}' — refusing to execute again"
        )
        yield f"data: {json.dumps({'type': 'error', 'error': f'Task already {task.status.value}'})}\n\n"
        return

    execution_prompt = prompt
    display_prompt = str(getattr(task, "prompt", "") or prompt)
    task_meta = getattr(task, "metadata", {}) if isinstance(getattr(task, "metadata", {}), dict) else {}
    memory_agent_id = str(task_meta.get("memory_agent_id") or ORGANIZER_ID).strip() or ORGANIZER_ID
    memory_session_id = str(task_meta.get("session_id") or "").strip()
    original_user_prompt = str(task_meta.get("original_prompt") or display_prompt).strip() or display_prompt

    # Broadcast task_created event to Dashboard
    subscriber_count = len(_subscription_manager.subscribers)
    logger.debug(f"[Dashboard Sync] Broadcasting task_created: {task_id} (subscribers: {subscriber_count})")
    await _subscription_manager.broadcast("task_created", {
        "task": {
            "id": task_id,
            "prompt": display_prompt,
            "status": "pending",
            "created_at": time.time(),
            "metadata": dict(task_meta) if isinstance(task_meta, dict) else {},
        }
    })

    # Initialize chat event buffer for reconnection
    with _chat_event_buffer_lock:
        _chat_event_buffer[task_id] = []

    def _buffer_and_yield(evt: str) -> str:
        """Store the SSE data for replay and return it for yielding."""
        with _chat_event_buffer_lock:
            buf = _chat_event_buffer.get(task_id)
            if buf is not None:
                buf.append(evt)
        return evt

    # Send task started event (to chat SSE)
    yield _buffer_and_yield(f"data: {json.dumps({'type': 'task_started', 'task_id': task_id})}\n\n")

    # Broadcast task_started to Dashboard (include initial progress)
    current_task = task_manager.get_task(task_id)
    start_progress = current_task.progress.to_dict() if current_task else None
    await _subscription_manager.broadcast("task_started", {
        "task": {
            "id": task_id,
            "prompt": display_prompt,
            "status": "running",
            "progress": start_progress,
            "metadata": dict(task_meta) if isinstance(task_meta, dict) else {},
        }
    })

    # Thread-safe queue for collecting step events (sync callback from CrewAI thread)
    step_queue = queue.Queue()
    execution_complete = threading.Event()
    execution_result = {"result": None, "error": None}

    # Track sandbox for this task
    sandbox_id = None
    sandbox_tools = {
        'shell', 'shell_command', 'shell_exec',
        'file_read', 'file_write', 'file_edit', 'file_search', 'file_list', 'file_find',
        'browser', 'browser_navigate', 'browser_screenshot', 'browser_action',
        'python', 'python_interpreter', 'python_exec',
        'process_start', 'process_list', 'process_stop',
    }

    # Worker name -> ID lookup (built when crew is created in run_crew via build_worker_lookup)
    _worker_name_to_id: dict = {}

    # Pool members (shared between run_crew thread and SSE generator)
    _pool_members_shared: list = []

    # Server-side progress tracking — sent with every task_step broadcast
    _task_progress: dict = {
        "phase": "received",
        "percentage": 0,
        "total_workers": 0,
        "completed_workers": 0,
        "active_workers": 0,
        "skipped_workers": 0,
        "phase_label": "Starting",
        "worker_statuses": [],
    }

    def register_task_sandbox():
        """Register a sandbox for this task's tool execution."""
        nonlocal sandbox_id
        if sandbox_id is None:
            sandbox_id = sandbox_id_for_task(task_id)
            sandbox_data = {
                "id": sandbox_id,
                "name": "Task Sandbox",
                "runtime": _runtime_backend_str(),
                "state": "running",
                "role": "task-execution",
                "taskId": task_id,
                "taskName": display_prompt[:50] if display_prompt else "Agent Task",
                "agentId": ORGANIZER_ID,
                "agentName": "Organizer",
                "created": time.time(),
                "lastHeartbeat": time.time(),
                "events": [],
                "metrics": {"cpu_pct": 0, "mem_pct": 0, "disk_pct": 0},
            }
            _sandboxes[sandbox_id] = sandbox_data
            _sandbox_events[sandbox_id] = []
            # Add initial registration event
            _init_event = {
                "type": "info",
                "timestamp": time.time(),
                "data": {"message": f"Sandbox started for task: {display_prompt[:80] if display_prompt else task_id}", "tool": "system"},
            }
            _sandbox_events[sandbox_id].append(_init_event)
            try:
                get_database().save_sandbox_event(sandbox_id, _init_event)
            except Exception as _e:
                logger.debug(f"Sandbox init event DB persist error: {_e}")
            # Notify sandbox stream subscribers (schedule from worker thread)
            try:
                import threading as _thr
                _sb_ml = getattr(_thr, "_teaming24_main_loop", None)
                _sb_id = sandbox_id  # capture
                if _sb_ml and _sb_ml.is_running():
                    _sb_ml.call_soon_threadsafe(
                        lambda _id=_sb_id: asyncio.ensure_future(
                            _broadcast_sandbox_update("registered", _id), loop=_sb_ml
                        )
                    )
            except Exception:
                logger.warning(
                    "Failed to broadcast sandbox registration update sandbox_id=%s",
                    sandbox_id,
                    exc_info=True,
                )
        return sandbox_id

    def add_sandbox_event_entry(event_type: str, data: dict):
        """Add an event to the sandbox event log (if sandbox exists)."""
        nonlocal sandbox_id
        if sandbox_id and sandbox_id in _sandboxes:
            entry = {
                "type": event_type,
                "timestamp": time.time(),
                "data": data,
            }
            if sandbox_id not in _sandbox_events:
                _sandbox_events[sandbox_id] = []
            _sandbox_events[sandbox_id].append(entry)
            # Keep configured number of recent events.
            _sandbox_events[sandbox_id] = _sandbox_events[sandbox_id][-config.api.max_events_kept:]
            _sandboxes[sandbox_id]["lastHeartbeat"] = time.time()
            # Persist to DB
            try:
                get_database().save_sandbox_event(sandbox_id, entry)
            except Exception as _e:
                logger.debug(f"Sandbox event DB persist error: {_e}")

    # --- Worker activity tracking for detailed Event History ---
    _seen_workers: set = set()          # Workers that have started working
    _worker_step_count: dict = {}       # role -> number of steps seen
    _worker_first_content: dict = {}    # role -> first meaningful content (subtask)
    _worker_states: dict[str, dict[str, Any]] = {}
    _remote_progress_by_node: dict[str, dict[str, Any]] = {}
    _remote_terminal_nodes: set[str] = set()
    _remote_milestone_tracker: dict[str, tuple[Any, ...]] = {}

    def _sync_progress_to_task():
        """Sync local _task_progress dict to the TaskManager's Task object."""
        try:
            task_manager.update_progress(task_id, **_task_progress)
        except Exception as e:
            logger.debug(f"Failed to sync task progress for {task_id}: {e}")
            pass  # Non-critical — dashboard gets progress via SSE anyway

    def _sync_worker_statuses_to_progress():
        _task_progress["worker_statuses"] = serialize_worker_statuses(_worker_states)
        _sync_progress_to_task()

    def on_step(step_data: dict):
        """Synchronous callback that CrewAI can call from its thread."""
        # Merge progress from workflow steps (e.g. sequential completed_workers) into _task_progress
        # so broadcast and dashboard stay in sync during execution.
        if step_data.get("progress"):
            _task_progress.update(step_data["progress"])
            _sync_progress_to_task()

        # Enrich step data with agent type info for dashboard tracking
        agent_name = normalize_agent_name(step_data.get('agent', 'Unknown'))
        step_data['agent'] = agent_name

        # Preserve explicit agent_type from workflow steps (e.g. router, remote)
        # Only fall back to resolve_agent_id when no type is set.
        explicit_type = step_data.get('agent_type')
        agent_id, resolved_type = resolve_agent_id(agent_name, _worker_name_to_id)
        step_data['agent_id'] = agent_id
        if not explicit_type:
            step_data['agent_type'] = resolved_type
        else:
            step_data['agent_type'] = explicit_type
        agent_type = step_data['agent_type']   # local alias for checks below

        # Detect delegation actions
        action = step_data.get('action', '').lower()
        if 'delegate' in action or 'assign' in action:
            step_data['is_delegation'] = True

        # Ensure sandbox is registered for every task (register early)
        register_task_sandbox()

        # --- Fine-grained progress: every step advances percentage and phase_label ---
        base_pct = _task_progress.get("percentage", 25)

        if agent_type == 'organizer' or agent_type == 'router':
            # Organizer/Router: show current action, bump progress slightly (up to 25%)
            action_short = (action or "working")[:40]
            label = f"Organizer: {action_short}"
            pct = min(base_pct + 1, 25)
            _task_progress.update(
                percentage=pct,
                phase_label=label,
                current_agent=agent_name,
                current_action=action,
                worker_statuses=serialize_worker_statuses(_worker_states),
            )
            _sync_progress_to_task()
        elif agent_type == 'coordinator':
            # Coordinator: show dispatching/executing, bump progress (25-55% range)
            action_short = (action or "dispatching")[:40]
            label = f"{LOCAL_COORDINATOR_NAME}: {action_short}"
            pct = min(base_pct + 2, 55)
            _task_progress.update(
                phase="executing",
                percentage=pct,
                phase_label=label,
                current_agent=agent_name,
                current_action=action,
                worker_statuses=serialize_worker_statuses(_worker_states),
            )
            _sync_progress_to_task()
        elif agent_type == 'remote':
            remote_progress = step_data.get("remote_progress")
            if not isinstance(remote_progress, dict):
                remote_progress = {}
            node_key = str(
                step_data.get("remote_task_id")
                or step_data.get("remote_node_id")
                or step_data.get("agent_id")
                or agent_name
                or "remote"
            )
            if remote_progress:
                _remote_progress_by_node[node_key] = remote_progress

            remote_stage = str(remote_progress.get("stage", "") or "").strip().lower()
            if (
                action in ("remote_done", "remote_completed", "remote_failed")
                or remote_stage in ("completed", "failed")
            ):
                _remote_terminal_nodes.add(node_key)

            known_nodes = set(_remote_progress_by_node.keys()) | set(_remote_terminal_nodes)
            pct_values: list[int] = []
            for key in known_nodes:
                snapshot = _remote_progress_by_node.get(key, {})
                raw_pct = snapshot.get("percentage")
                stage_hint = str(snapshot.get("stage", "") or "").strip().lower()
                try:
                    pct_num = (
                        int(raw_pct)
                        if raw_pct is not None
                        else (
                            100
                            if key in _remote_terminal_nodes
                            else remote_stage_default_pct(stage_hint)
                        )
                    )
                except (TypeError, ValueError):
                    pct_num = 100 if key in _remote_terminal_nodes else remote_stage_default_pct(stage_hint)
                pct_values.append(max(0, min(100, pct_num)))

            avg_remote_pct = int(sum(pct_values) / max(1, len(pct_values))) if pct_values else 0
            blended_pct = 25 + int((avg_remote_pct / 100.0) * 55)
            blended_pct = max(25, min(80, blended_pct))
            phase_label = (
                str(remote_progress.get("phase_label", "") or "").strip()
                or (
                    str(remote_progress.get("stage", "") or "").strip().replace("_", " ").title()
                    if remote_progress else ""
                )
                or f"Remote execution {len(_remote_terminal_nodes)}/{max(1, len(known_nodes))}"
            )
            current_total = int(_task_progress.get("total_workers", 0) or 0)
            total_workers = max(current_total, len(known_nodes))
            _task_progress.update(
                phase="executing",
                percentage=max(int(_task_progress.get("percentage", 25) or 25), blended_pct),
                total_workers=total_workers,
                completed_workers=min(len(_remote_terminal_nodes), total_workers),
                active_workers=max(total_workers - len(_remote_terminal_nodes), 0),
                phase_label=phase_label,
                current_agent=agent_name,
                current_action=action,
                worker_statuses=serialize_worker_statuses(_worker_states),
            )
            _sync_progress_to_task()
        elif agent_type == 'worker' and agent_name:
            counts_as_worker_step = action not in ('tool_heartbeat', 'worker_heartbeat')
            if counts_as_worker_step:
                _worker_step_count[agent_name] = _worker_step_count.get(agent_name, 0) + 1
            worker_step_n = _worker_step_count.get(agent_name, 0)

            tool_name = str(step_data.get('tool') or '').strip()
            detail_text = str(step_data.get('content') or '').strip()
            worker_state = 'running'
            detail_lower = detail_text.lower()
            if action == 'worker_completed':
                worker_state = 'completed'
                detail_text = detail_text or 'Worker completed'
            elif action == 'worker_skipped':
                worker_state = 'skipped'
                detail_text = detail_text or 'Worker skipped'
            elif action in ('worker_failed', 'tool_error'):
                worker_state = 'failed'
                detail_text = detail_text or 'Worker failed'
            elif action == 'tool_call' and 'timed out' in detail_lower:
                worker_state = 'timeout'
                detail_text = detail_text or 'Command timed out'
            elif action in ('tool_heartbeat', 'worker_heartbeat'):
                worker_state = 'running'
                detail_text = detail_text or 'Healthy'
            elif action == 'tool_start':
                detail_text = detail_text or (f'Running {tool_name}' if tool_name else 'Running')
            elif action == 'tool_call':
                detail_text = detail_text or (f'Finished {tool_name}' if tool_name else 'Tool finished')
            elif not detail_text:
                detail_text = 'Working'
            upsert_worker_status(
                _worker_states,
                agent_name,
                status=worker_state,
                action=action or 'working',
                detail=detail_text,
                tool=tool_name or None,
                step_count=worker_step_n,
            )

            if agent_name not in _seen_workers:
                _seen_workers.add(agent_name)
                active = len(_seen_workers)
                pct = min(base_pct + 5, 75)
                _task_progress.update(
                    phase="executing", percentage=pct,
                    active_workers=active,
                    phase_label=f"{agent_name} started ({active} active)",
                    worker_statuses=serialize_worker_statuses(_worker_states),
                )
                _sync_progress_to_task()
                # First step from this worker — capture its subtask from thought/content
                thought = step_data.get('thought', '') or ''
                content = step_data.get('content', '') or ''
                subtask_hint = (thought or content)[:120].replace('\n', ' ').strip()
                _worker_first_content[agent_name] = subtask_hint

                # Emit "worker started" event to both SSE and sandbox
                start_msg = f"🔧 {agent_name} started working"
                if subtask_hint:
                    start_msg += f": {subtask_hint[:80]}..."
                step_queue.put({
                    "agent": agent_name,
                    "agent_type": "worker",
                    "agent_id": agent_id,
                    "action": "worker_started",
                    "content": start_msg,
                    "is_delegation": False,
                })
                add_sandbox_event_entry("info", {
                    "message": start_msg,
                    "agent": agent_name,
                    "tool": "worker_started",
                })
            else:
                # Subsequent steps from this worker — finer granularity (+2% per step, cap 85)
                active = len(_seen_workers)
                new_pct = min(base_pct + 2, 85)
                phase_label = f"{agent_name} step {worker_step_n}"
                if action == 'tool_start' and tool_name:
                    phase_label = f"{agent_name} running {tool_name}"
                elif action in ('tool_heartbeat', 'worker_heartbeat') and tool_name:
                    phase_label = f"{agent_name} healthy | {tool_name}"
                elif action == 'tool_call' and tool_name:
                    phase_label = f"{agent_name} finished {tool_name}"
                _task_progress.update(
                    phase="executing", percentage=new_pct,
                    active_workers=active,
                    phase_label=phase_label,
                    current_agent=agent_name,
                    current_action=action,
                    worker_statuses=serialize_worker_statuses(_worker_states),
                )
                _sync_progress_to_task()

        emit_step = True
        if agent_type == "remote":
            emit_step = should_emit_remote_milestone(step_data, _remote_milestone_tracker)
            if not emit_step:
                logger.debug(
                    "Suppressed noisy remote milestone task=%s agent=%s action=%s",
                    task_id,
                    agent_name,
                    action,
                )
                return

        # Detect tool usage for sandbox tracking
        action_content = step_data.get('content', '').lower()
        is_tool_step = False
        for tool in sandbox_tools:
            if tool in action.lower() or tool in action_content:
                step_data['uses_sandbox'] = True
                step_data['sandbox_id'] = sandbox_id
                is_tool_step = True
                # Add tool-specific command event
                add_sandbox_event_entry(
                    "command" if 'shell' in tool or 'python' in tool else "info",
                    {
                            "agent": agent_name,
                            "tool": tool,
                            "cmd": action_content[:200] if action_content else action,
                        }
                )
                break

        # For non-tool steps, add info events to sandbox log (skip payment events —
        # those belong to the task/wallet log, not the sandbox execution log)
        _SANDBOX_SKIP_ACTIONS = {
            "payment_sent", "payment_approved", "payment_processing", "payment_received",
        }
        if not is_tool_step:
            step_action = step_data.get('action', '')
            step_content = step_data.get('content', '')
            if step_action not in _SANDBOX_SKIP_ACTIONS:
                add_sandbox_event_entry(
                    "info",
                    {
                        "message": f"[{step_action}] {step_content[:200]}" if step_content else step_action,
                        "agent": agent_name,
                        "tool": step_action,
                    }
                )

        # Record wallet expense for each remote AN payment
        if step_data.get("action") == "payment_sent":
            pay_info = step_data.get("payment_info", {})
            if pay_info.get("is_retry"):
                pass  # Refinement round — same master task, no charge
            else:
                _def_amt, _mode, _network = _wallet_service.resolve_payment_defaults()
                _amt = pay_info.get("amount_num")
                if _amt is None:
                    _amt = _def_amt
                if _amt > 0:
                    _target = pay_info.get("target_an") or ""
                    _already = get_database().is_expense_recorded(task_id, _target)
                    if not _already:
                        tx = _record_wallet_transaction(
                            tx_type="expense",
                            amount=_amt,
                            task_id=task_id,
                            task_name=f"Delegation to {pay_info.get('target_an', 'Remote AN')}",
                            description=(
                                f"Payment to {pay_info.get('target_an', '')} "
                                f"({pay_info.get('ip', '')}:{pay_info.get('port', '')})"
                            ),
                            tx_hash="",
                            payer="local",
                            payee=pay_info.get("target_an", ""),
                            mode=_mode,
                            network=_network,
                        )
                        try:
                            get_database().save_expense_record(task_id, _target, _amt)
                        except Exception as _e:
                            logger.debug("Failed to save expense record: %s", _e)
                        # Broadcast wallet_transaction so frontend syncs in real-time (runs in worker thread)
                        try:
                            _wl_loop = asyncio.get_running_loop()
                            asyncio.ensure_future(
                                _subscription_manager.broadcast("wallet_transaction", {
                                    "transaction": tx,
                                    "balance": round(_st.mock_balance, 6),
                                }),
                                loop=_wl_loop,
                            )
                        except RuntimeError:
                            logger.debug("No running loop for wallet broadcast; using main loop handoff")
                            import threading
                            _ml = getattr(threading, "_teaming24_main_loop", None)
                            if _ml and _ml.is_running():
                                _ml.call_soon_threadsafe(
                                    lambda: asyncio.ensure_future(
                                        _subscription_manager.broadcast("wallet_transaction", {
                                            "transaction": tx,
                                            "balance": round(_st.mock_balance, 6),
                                        }),
                                        loop=_ml,
                                    )
                                )

        # Ensure step carries latest progress for frontend (ChatView/Dashboard)
        step_data["progress"] = dict(_task_progress)

        # Persist live task snapshot so Steps survive refresh/restart mid-execution.
        try:
            db = get_database()
            live_task = task_manager.get_task(task_id)
            if live_task:
                live_steps = _serialize_task_steps_for_db(live_task.steps)
                incoming = _serialize_task_step_for_db(step_data)
                incoming_no = incoming.get("step_number")
                if incoming_no and not any(s.get("step_number") == incoming_no for s in live_steps):
                    live_steps.append(incoming)

                db.save_task({
                    "id": task_id,
                    "name": display_prompt[:100],
                    "description": display_prompt,
                    "status": live_task.status.value,
                    "task_type": live_task.task_type.value if getattr(live_task, "task_type", None) else "local",
                    "assigned_to": live_task.assigned_to,
                    "delegated_agents": live_task.delegated_agents,
                    "executing_agents": live_task.executing_agents,
                    "steps": live_steps,
                    "result": live_task.result,
                    "error": live_task.error,
                    "cost": live_task.cost.to_dict() if getattr(live_task, "cost", None) else {},
                    "output_dir": live_task.output_dir,
                    "created_at": live_task.created_at,
                    "started_at": live_task.started_at,
                    "completed_at": live_task.completed_at,
                    "metadata": {
                        "pool_members": live_task.pool_members,
                        "selected_members": live_task.selected_members,
                        "execution_mode": live_task.execution_mode,
                        "progress": live_task.progress.to_dict() if getattr(live_task, "progress", None) else None,
                    },
                })
        except Exception as persist_err:
            logger.debug(f"Live task snapshot DB persist failed for {task_id}: {persist_err}")

        step_queue.put(step_data)

    def run_crew():
        """Run crew execution in background thread."""
        try:
            # Register sandbox early so ALL lifecycle events are captured
            register_task_sandbox()

            # Send initial step notification
            _task_progress.update(phase="received", percentage=5, phase_label="Task received by Organizer")
            step_queue.put({
                "agent": "Organizer",
                "agent_type": "organizer",
                "action": "receiving",
                "content": "📥 Received task request",
                "is_delegation": False,
            })
            add_sandbox_event_entry("info", {
                "message": "📥 Received task request",
                "agent": "Organizer",
                "tool": "receiving",
            })

            # No self-payment for local chat tasks.
            # Per-remote-AN payments are emitted by core.py's
            # _execute_remote_subtasks when dispatching to remote ANs.

            runtime_settings = get_agent_runtime_settings()
            crew = create_local_crew(task_manager, on_step=on_step, runtime_settings=runtime_settings)

            # Bind Agentic Node Workforce Pool so Organizer can route to remote ANs
            pool_members_info: list = []
            try:
                network_manager = get_network_manager()
                pool = _build_agentic_node_workforce_pool(crew, network_manager)
                crew.bind_workforce_pool(pool, task_id=task_id)
                # Capture pool snapshot for frontend display
                for entry in pool.get_pool():
                    ni = getattr(entry, "node_info", None)
                    pool_members_info.append({
                        "id": entry.id,
                        "name": entry.name,
                        "type": entry.entry_type,  # "local" or "remote"
                        "status": entry.status,
                        "capabilities": entry.capabilities[:10] if entry.capabilities else [],
                        "description": entry.description,
                        "source": getattr(entry, "source", None),
                        "region": getattr(entry, "region", None),
                        "cost": entry.cost,
                        "wallet_address": getattr(entry, "wallet_address", None),
                        "ip": getattr(ni, "ip", None) if ni else None,
                        "port": getattr(ni, "port", None) if ni else None,
                        "an_id": (getattr(ni, "agent_id", None) or getattr(ni, "id", None)) if ni else entry.id,
                    })
            except Exception as e:
                logger.warning(f"Could not bind Agentic Node Workforce Pool: {e}")

            # Share pool_members with SSE generator for continuous broadcast
            _pool_members_shared.extend(pool_members_info)

            # Persist pool_members on the Task object for tasks_init restoration
            if task_id and pool_members_info:
                try:
                    task_manager.set_pool_members(task_id, pool_members_info)
                except Exception as e:
                    logger.debug(f"Failed to persist pool members for task {task_id}: {e}")
                    pass

            # Build worker name -> ID lookup via centralized build_worker_lookup
            _worker_name_to_id.update(build_worker_lookup(
                crew.workers,
                worker_configs=getattr(crew, '_worker_configs', None),
            ))
            logger.debug(f"Worker name->ID mapping: {_worker_name_to_id}")

            # Get agent info for step notifications
            agent_count = len(crew.agents) if hasattr(crew, 'agents') else 0
            worker_count = max(0, agent_count - 1)  # Exclude manager

            # Build worker roster for detailed Event History
            worker_roster = []
            for w in crew.workers:
                role = getattr(w, 'role', 'Worker')
                goal = getattr(w, 'goal', '')
                goal_short = goal.strip().split('\n')[0][:80] if goal else ''
                worker_roster.append({"role": role, "goal": goal_short})
            for idx, wr in enumerate(worker_roster):
                upsert_worker_status(
                    _worker_states,
                    wr["role"],
                    status="pending",
                    action="queued",
                    detail="Selected and waiting to start",
                    step_count=_worker_step_count.get(wr["role"], 0),
                    order_hint=idx,
                )

            # Send planning notification with worker count
            _task_progress.update(phase="routing", percentage=15, total_workers=worker_count,
                                  phase_label=f"Routing with {worker_count} candidate workers",
                                  worker_statuses=serialize_worker_statuses(_worker_states))
            step_queue.put({
                "agent": "Organizer",
                "agent_type": "organizer",
                "action": "routing",
                "content": f"📋 Routing task execution with {worker_count} worker agents",
                "is_delegation": False,
            })
            add_sandbox_event_entry("info", {
                "message": f"📋 Planning task execution with {worker_count} worker agents",
                "agent": "Organizer",
                "tool": "planning",
            })

            # Emit pool snapshot event so Dashboard shows remote AN info
            if pool_members_info:
                remote_members = [m for m in pool_members_info if m["type"] == "remote"]
                local_members = [m for m in pool_members_info if m["type"] == "local"]
                pool_summary_parts = []
                for m in pool_members_info:
                    tag = "LOCAL" if m["type"] == "local" else "REMOTE"
                    addr = f" @ {m['ip']}:{m['port']}" if m.get("ip") else ""
                    caps_preview = ", ".join(m["capabilities"][:4]) if m["capabilities"] else "general"
                    source = f" source={m['source']}" if m.get("source") else ""
                    region = f" region={m['region']}" if m.get("region") else ""
                    pool_summary_parts.append(f"[{tag}] {m['name']}{addr}{source}{region} — {caps_preview}")
                pool_msg = (
                    f"🌐 Agentic Node Workforce Pool: {len(pool_members_info)} member(s) "
                    f"(local={len(local_members)}, remote={len(remote_members)})\n"
                    + "\n".join(f"  {i+1}. {p}" for i, p in enumerate(pool_summary_parts))
                )
                step_queue.put({
                    "agent": "Organizer",
                    "agent_type": "organizer",
                    "action": "pool_snapshot",
                    "content": pool_msg,
                    "is_delegation": False,
                    "pool_members": pool_members_info,
                })
                add_sandbox_event_entry("info", {
                    "message": pool_msg,
                    "agent": "Organizer",
                    "tool": "pool_snapshot",
                })

            # Emit per-worker roster events so Event History shows available workers
            _task_progress.update(phase="dispatching", percentage=20,
                                  phase_label=f"{worker_count} worker capabilities identified",
                                  worker_statuses=serialize_worker_statuses(_worker_states))
            for wr in worker_roster:
                roster_msg = f"👤 {wr['role']}"
                if wr['goal']:
                    roster_msg += f" — {wr['goal']}"
                step_queue.put({
                    "agent": "Organizer",
                    "agent_type": "organizer",
                    "action": "worker_roster",
                    "content": roster_msg,
                    "is_delegation": False,
                    "worker_count": worker_count,
                })
                add_sandbox_event_entry("info", {
                    "message": roster_msg,
                    "agent": "Organizer",
                    "tool": "worker_roster",
            })

            if worker_count > 0:
                # Send delegation notification
                _task_progress.update(phase="dispatching", percentage=25,
                                      phase_label="Dispatching to coordinators")
                step_queue.put({
                    "agent": "Organizer",
                    "agent_type": "organizer",
                    "action": "dispatching",
                    "content": f"📤 Dispatching to coordinator team: {display_prompt[:80]}...",
                    "is_delegation": True,
                })
                add_sandbox_event_entry("info", {
                    "message": f"📤 Dispatching to coordinator team: {display_prompt[:80]}...",
                    "agent": "Organizer",
                    "tool": "delegating",
                })

            # Run synchronously in this thread
            try:
                from teaming24.agent.tools.memory_tools import memory_tool_context
            except Exception:
                memory_tool_context = None  # type: ignore[assignment]

            if memory_tool_context is not None:
                with memory_tool_context(agent_id=memory_agent_id, session_id=memory_session_id):
                    result = crew.execute_sync(execution_prompt, task_id)
            else:
                result = crew.execute_sync(execution_prompt, task_id)

            # --- Emit per-worker completion summary ---
            # Workers that appeared in on_step are confirmed active
            completed_count = 0
            for w_role in list(_seen_workers):
                completed_count += 1
                steps = _worker_step_count.get(w_role, 0)
                done_msg = f"✅ {w_role} completed ({steps} steps)"
                total_w = _task_progress.get("total_workers", 1) or 1
                pct = 25 + int(55 * completed_count / total_w)  # 25-80% range
                pct = min(pct, 80)
                upsert_worker_status(
                    _worker_states,
                    w_role,
                    status="completed",
                    action="worker_completed",
                    detail="Completed",
                    step_count=steps,
                )
                _task_progress.update(
                    phase="executing", percentage=pct,
                    completed_workers=completed_count,
                    phase_label=f"Workers {completed_count}/{total_w} completed",
                    worker_statuses=serialize_worker_statuses(_worker_states),
                )
                step_queue.put({
                    "agent": w_role,
                    "agent_type": "worker",
                    "action": "worker_completed",
                    "content": done_msg,
                    "is_delegation": False,
                })
                add_sandbox_event_entry("info", {
                    "message": done_msg,
                    "agent": w_role,
                    "tool": "worker_completed",
                })

            # Workers in the roster that never appeared — mark as not assigned
            skipped_count = 0
            for wr in worker_roster:
                if wr['role'] not in _seen_workers:
                    skipped_count += 1
                    skip_msg = f"⏭️ {wr['role']} was not assigned a subtask"
                    upsert_worker_status(
                        _worker_states,
                        wr['role'],
                        status="skipped",
                        action="worker_skipped",
                        detail="Not assigned in this run",
                        step_count=_worker_step_count.get(wr['role'], 0),
                    )
                    _task_progress.update(
                        skipped_workers=skipped_count,
                        worker_statuses=serialize_worker_statuses(_worker_states),
                    )
                    step_queue.put({
                        "agent": wr['role'],
                        "agent_type": "worker",
                        "action": "worker_skipped",
                        "content": skip_msg,
                        "is_delegation": False,
                    })
                    add_sandbox_event_entry("info", {
                        "message": skip_msg,
                        "agent": wr['role'],
                        "tool": "worker_skipped",
                    })

            # Send aggregation step
            _task_progress.update(phase="aggregating", percentage=85,
                                  phase_label="Aggregating results",
                                  worker_statuses=serialize_worker_statuses(_worker_states))
            step_queue.put({
                "agent": "Organizer",
                "agent_type": "organizer",
                "action": "aggregating",
                "content": f"📊 Aggregating results from {len(_seen_workers)} workers",
                "is_delegation": False,
            })
            add_sandbox_event_entry("info", {
                "message": f"📊 Aggregating results from {len(_seen_workers)} workers",
                "agent": "Organizer",
                "tool": "aggregating",
            })

            # Send completion step
            _task_progress.update(phase="completed", percentage=100,
                                  phase_label="Task completed",
                                  worker_statuses=serialize_worker_statuses(_worker_states))
            step_queue.put({
                "agent": "Organizer",
                "agent_type": "organizer",
                "action": "completed",
                "content": "✅ Task completed successfully",
                "is_delegation": False,
            })

            execution_result["result"] = result
        except Exception as e:
            # Log error directly
            logger.error(f"Crew execution error: {e}")
            execution_result["error"] = str(e)
        finally:
            execution_complete.set()

    # Keepalive: while task runs, periodically put a keepalive step so idle timeout resets
    # (Allows long-running LLM/AN work without timing out; stops when execution_complete)
    def _keepalive_pump():
        interval = max(1.0, float(config.api.task_keepalive_interval))
        while not execution_complete.is_set():
            time.sleep(interval)
            if execution_complete.is_set():
                break
            try:
                step_queue.put_nowait({"action": "task_keepalive", "agent": "Organizer", "agent_type": "organizer"})
            except Exception as exc:
                logger.warning(
                    "Failed to enqueue keepalive step task=%s: %s",
                    task.id,
                    exc,
                    exc_info=True,
                )

    # Start execution in background thread
    exec_thread = threading.Thread(target=run_crew, daemon=True)
    keepalive_thread = threading.Thread(target=_keepalive_pump, daemon=True)
    time.time()
    exec_thread.start()
    keepalive_thread.start()

    try:
        # Stream step events while waiting for completion
        # Send periodic heartbeat events to show processing is happening
        last_heartbeat = time.time()
        # Idle timeout: reset on each step or heartbeat; only timeout if no activity for N seconds
        last_activity = time.time()
        heartbeat_interval = config.api.task_heartbeat_interval
        # Prefer DB setting (from Settings UI), fallback to config. 0 = disabled (keep waiting).
        db_timeout = None
        try:
            db_timeout = get_database().get_setting("taskExecutionTimeout")
            exec_timeout = float(db_timeout) if db_timeout is not None else 0
        except (TypeError, ValueError):
            logger.debug("Invalid taskExecutionTimeout DB value: %r", db_timeout)
            exec_timeout = 0
        if exec_timeout <= 0:
            exec_timeout = config.api.task_execution_timeout or 0
        exec_timeout = max(0.0, exec_timeout)  # Ensure non-negative
        heartbeat_count = 0
        thinking_messages = [
            "Processing your request...",
            "Analyzing the task...",
            "Generating response...",
            "Working on it...",
            "Almost there...",
        ]

        while not execution_complete.is_set():
            try:
                step = step_queue.get(timeout=config.api.step_queue_timeout)
                if "progress" not in step:
                    step = {**step, "progress": dict(_task_progress)}

                # Task keepalive: reset idle timer only, don't stream or broadcast
                if step.get("action") == "task_keepalive":
                    last_activity = time.time()
                    continue

                # Approval requests are sent as a different SSE event type
                # so the frontend can show an interactive approval card.
                if step.get("action") == "approval_request":
                    with _approval_lock:
                        pending = [v for v in _approval_requests.values()
                                   if v.get("task_id") == task_id and v.get("decision") is None]
                    if pending:
                        a = pending[-1]
                        yield _buffer_and_yield(f"data: {json.dumps({'type': 'approval_request', 'approval': {'id': a['id'], 'task_id': task_id, 'type': a.get('type'), 'title': a['title'], 'description': a['description'], 'options': a['options'], 'metadata': a.get('metadata', {})}})}\n\n")
                    else:
                        yield _buffer_and_yield(f"data: {json.dumps({'type': 'step', **step})}\n\n")
                elif step.get("action") == "approval_resolved":
                    yield _buffer_and_yield(f"data: {json.dumps({'type': 'approval_resolved'})}\n\n")
                    yield _buffer_and_yield(f"data: {json.dumps({'type': 'step', **step})}\n\n")
                else:
                    yield _buffer_and_yield(f"data: {json.dumps({'type': 'step', **step})}\n\n")

                # Broadcast step to Dashboard with agent tracking + progress
                task = task_manager.get_task(task_id)
                step_broadcast: dict = {
                    "task": {
                        "id": task_id,
                        "status": "running",
                        "steps": [{"agent": step.get("agent"), "action": step.get("action"),
                                   "content": step.get("content"), "thought": step.get("thought"),
                                   "agent_type": step.get("agent_type"),
                                   "agent_id": step.get("agent_id"),
                                   "step_number": step.get("step_number"),
                                   "timestamp": time.time(),
                                   "worker_count": step.get("worker_count"),
                                   "selected_members": step.get("selected_members")}],
                        # Include current agent tracking
                        "worker_count": step.get("worker_count"),
                        "assigned_to": task.assigned_to if task else None,
                        "executing_agents": task.executing_agents if task else [],
                        "delegated_agents": task.delegated_agents if task else [],
                        # Server-side progress snapshot
                        "progress": dict(_task_progress),
                    },
                    "agent_id": step.get("agent_id"),
                    "agent_type": step.get("agent_type"),
                    "is_delegation": step.get("is_delegation", False),
                }
                # Always attach pool_members so late-arriving dashboard clients
                # can build topology even for running tasks.
                pm = step.get("pool_members") or (_pool_members_shared if _pool_members_shared else None)
                if pm:
                    step_broadcast["pool_members"] = pm
                # Pass through and persist selected_members and execution_mode from routing_decision step
                sm = step.get("selected_members")
                if sm:
                    step_broadcast["selected_members"] = sm
                    task_manager.set_selected_members(task_id, sm)
                em = step.get("execution_mode")
                if em in ("parallel", "sequential"):
                    step_broadcast["execution_mode"] = em
                    task_manager.set_execution_mode(task_id, em)
                await _subscription_manager.broadcast("task_step", step_broadcast)

                last_heartbeat = time.time()  # Reset heartbeat timer after real step
                last_activity = time.time()   # Reset idle timeout — task is alive
            except queue.Empty:
                logger.debug("Task step queue timeout for task_id=%s", task_id)
                # Exit if task was externally cancelled
                _t = task_manager.get_task(task_id)
                if _t and _t.status == TaskStatus.CANCELLED:
                    yield _buffer_and_yield(f"data: {json.dumps({'type': 'cancelled', 'error': 'Task cancelled'})}\n\n")
                    yield "data: [DONE]\n\n"
                    # Schedule buffer/budget cleanup (same as normal completion)
                    cleanup_delay = max(0.0, float(config.api.chat_buffer_cleanup_delay))
                    async def _cleanup(delay: float = cleanup_delay):
                        await asyncio.sleep(delay)
                        with _chat_event_buffer_lock:
                            _chat_event_buffer.pop(task_id, None)
                        with _approval_lock:
                            _task_budgets.pop(task_id, None)
                    asyncio.ensure_future(_cleanup())
                    return
                # Check idle timeout: no step/heartbeat for N seconds → task considered stuck
                now = time.time()
                if exec_timeout > 0 and (now - last_activity) >= exec_timeout:
                    execution_result["error"] = (
                        f"Task idle timeout: no activity for {exec_timeout:.0f}s. "
                        "The agent may be stuck. Try a simpler request or check logs."
                    )
                    execution_complete.set()  # Allow loop to exit
                    break
                # No step available, send heartbeat to keep client connected
                # (Idle timeout only resets on real steps from crew/AN — they must send keepalive)
                if now - last_heartbeat >= heartbeat_interval:
                    heartbeat_count += 1
                    heartbeat_msg = thinking_messages[min(heartbeat_count - 1, len(thinking_messages) - 1)]
                    yield f"data: {json.dumps({'type': 'heartbeat', 'message': heartbeat_msg, 'count': heartbeat_count})}\n\n"
                    last_heartbeat = now
                await asyncio.sleep(0.1)
                continue

        # Drain remaining steps — also broadcast to dashboard
        while not step_queue.empty():
            try:
                step = step_queue.get_nowait()
                if "progress" not in step:
                    step = {**step, "progress": dict(_task_progress)}
                yield _buffer_and_yield(f"data: {json.dumps({'type': 'step', **step})}\n\n")
                task = task_manager.get_task(task_id)
                drain_broadcast: dict = {
                    "task": {
                        "id": task_id,
                        "status": "running",
                        "steps": [{"agent": step.get("agent"), "action": step.get("action"),
                                   "content": step.get("content"), "thought": step.get("thought"),
                                   "agent_type": step.get("agent_type"),
                                   "agent_id": step.get("agent_id"),
                                   "step_number": step.get("step_number"),
                                   "timestamp": time.time(),
                                   "worker_count": step.get("worker_count")}],
                        "worker_count": step.get("worker_count"),
                        "assigned_to": task.assigned_to if task else None,
                        "executing_agents": task.executing_agents if task else [],
                        "delegated_agents": task.delegated_agents if task else [],
                        "progress": dict(_task_progress),
                    },
                    "agent_id": step.get("agent_id"),
                    "agent_type": step.get("agent_type"),
                    "is_delegation": step.get("is_delegation", False),
                }
                drain_pm = step.get("pool_members") or (_pool_members_shared if _pool_members_shared else None)
                if drain_pm:
                    drain_broadcast["pool_members"] = drain_pm
                await _subscription_manager.broadcast("task_step", drain_broadcast)
            except queue.Empty:
                logger.debug("Task drain queue empty for task_id=%s", task_id)
                break

        # Check for errors
        if execution_result["error"]:
            task_manager.fail_task(task_id, execution_result["error"])
            yield _buffer_and_yield(f"data: {json.dumps({'type': 'error', 'error': execution_result['error']})}\n\n")

            # Broadcast task_failed to Dashboard
            _failed_task = task_manager.get_task(task_id)
            await _subscription_manager.broadcast("task_failed", {
                "task": {
                    "id": task_id,
                    "status": "failed",
                    "error": execution_result["error"],
                    "completed_at": _failed_task.completed_at if _failed_task else time.time(),
                    "progress": {"phase": "completed", "percentage": 100, "phase_label": "Task failed",
                                 "total_workers": _task_progress.get("total_workers", 0),
                                 "completed_workers": _task_progress.get("completed_workers", 0),
                                 "active_workers": 0, "skipped_workers": _task_progress.get("skipped_workers", 0),
                                 "worker_statuses": _task_progress.get("worker_statuses", [])},
                }
            })
            # Persist failed task to database
            try:
                db = get_database()
                failed_steps = _serialize_task_steps_for_db(_failed_task.steps if _failed_task else [])
                db.save_task({
                    "id": task_id,
                    "name": display_prompt[:100],
                    "description": display_prompt,
                    "status": "failed",
                    "task_type": "local",
                    "assigned_to": _failed_task.assigned_to if _failed_task else None,
                    "delegated_agents": _failed_task.delegated_agents if _failed_task else [],
                    "executing_agents": _failed_task.executing_agents if _failed_task else [],
                    "steps": failed_steps,
                    "error": execution_result["error"],
                    "result": _failed_task.result if _failed_task else None,
                    "cost": _failed_task.cost.to_dict() if _failed_task else {},
                    "output_dir": _failed_task.output_dir if _failed_task else None,
                    "created_at": _failed_task.created_at if _failed_task else time.time(),
                    "started_at": _failed_task.started_at if _failed_task else None,
                    "completed_at": time.time(),
                    "metadata": {
                        "pool_members": _failed_task.pool_members if _failed_task else None,
                        "selected_members": _failed_task.selected_members if _failed_task else None,
                        "execution_mode": _failed_task.execution_mode if _failed_task else None,
                        "progress": _failed_task.progress.to_dict() if _failed_task else None,
                    },
                })
            except Exception as db_err:
                logger.warning(f"Failed to persist failed task to database: {db_err}")
            # Mark sandbox as failed and cleanup (crew exception or timeout)
            if sandbox_id and sandbox_id in _sandboxes:
                add_sandbox_event_entry("error", {
                    "message": f"Task failed: {execution_result['error'][:200]}",
                    "error": execution_result["error"][:200],
                })
                _sandboxes[sandbox_id]["state"] = "error"
                await _broadcast_sandbox_update("state_changed", sandbox_id)
                try:
                    await _cleanup_sandbox_resources(sandbox_id)
                except Exception as cleanup_err:
                    logger.debug(f"Post-failure sandbox cleanup error: {cleanup_err}")
        else:
            result = execution_result["result"] or {}
            status = result.get("status", "completed")

            # User cancelled (deny) or task failed — agent may have already called fail_task
            if status in ("cancelled", "failed"):
                err_msg = result.get("result") or result.get("error") or (
                    "Task cancelled by user" if status == "cancelled" else "Task failed"
                )
                task_manager.fail_task(task_id, err_msg)
                yield _buffer_and_yield(f"data: {json.dumps({'type': 'cancelled' if status == 'cancelled' else 'error', 'error': err_msg})}\n\n")
                _failed_task = task_manager.get_task(task_id)
                await _subscription_manager.broadcast("task_failed", {
                    "task": {
                        "id": task_id,
                        "status": "failed",
                        "error": err_msg,
                        "completed_at": _failed_task.completed_at if _failed_task else time.time(),
                        "progress": {"phase": "completed", "percentage": 100, "phase_label": "Task cancelled" if status == "cancelled" else "Task failed",
                                     "total_workers": _task_progress.get("total_workers", 0),
                                     "completed_workers": _task_progress.get("completed_workers", 0),
                                     "active_workers": 0, "skipped_workers": _task_progress.get("skipped_workers", 0),
                                     "worker_statuses": _task_progress.get("worker_statuses", [])},
                    }
                })
                # Persist failed/cancelled task to database
                try:
                    db = get_database()
                    cancel_steps = _serialize_task_steps_for_db(_failed_task.steps if _failed_task else [])
                    db.save_task({
                        "id": task_id,
                        "name": display_prompt[:100],
                        "description": display_prompt,
                        "status": "failed",
                        "task_type": "local",
                        "assigned_to": _failed_task.assigned_to if _failed_task else None,
                        "delegated_agents": _failed_task.delegated_agents if _failed_task else [],
                        "executing_agents": _failed_task.executing_agents if _failed_task else [],
                        "steps": cancel_steps,
                        "error": err_msg,
                        "result": _failed_task.result if _failed_task else None,
                        "cost": _failed_task.cost.to_dict() if _failed_task else {},
                        "output_dir": _failed_task.output_dir if _failed_task else None,
                        "created_at": _failed_task.created_at if _failed_task else time.time(),
                        "started_at": _failed_task.started_at if _failed_task else None,
                        "completed_at": time.time(),
                        "metadata": {
                            "pool_members": _failed_task.pool_members if _failed_task else None,
                            "selected_members": _failed_task.selected_members if _failed_task else None,
                            "execution_mode": _failed_task.execution_mode if _failed_task else None,
                            "progress": _failed_task.progress.to_dict() if _failed_task else None,
                        },
                    })
                except Exception as db_err:
                    logger.warning(f"Failed to persist cancelled/failed task to database: {db_err}")
                # Mark sandbox as failed and cleanup when user cancels
                if sandbox_id and sandbox_id in _sandboxes:
                    add_sandbox_event_entry("error", {
                        "message": f"Task cancelled: {err_msg[:200]}",
                        "error": err_msg[:200],
                    })
                    _sandboxes[sandbox_id]["state"] = "error"
                    await _broadcast_sandbox_update("state_changed", sandbox_id)
                    try:
                        await _cleanup_sandbox_resources(sandbox_id)
                    except Exception as cleanup_err:
                        logger.debug(f"Post-cancel sandbox cleanup error: {cleanup_err}")
            else:
                final_task = task_manager.get_task(task_id)
                content = result.get('result', '')
                cost = result.get('cost', {})
                duration = final_task.duration if final_task else 0
                output_info = result.get('output', {})

                # Fallback: when content is empty, show completion message
                if not content or not content.strip():
                    content = (
                        "Task completed successfully. No text output was produced. "
                        "Check the output files below if the task generated artifacts."
                    )

                # Paragraph-aware chunking instead of word-by-word
                stream_truncate = config.api.stream_truncate
                stream_content = content[:stream_truncate] if len(content) > stream_truncate else content
                is_truncated = len(content) > stream_truncate

                yield _buffer_and_yield(f"data: {json.dumps({'type': 'stream_start'})}\n\n")

                if stream_content:
                    paragraphs = stream_content.split('\n\n')
                    for pi, para in enumerate(paragraphs):
                        if pi > 0:
                            yield f"data: {json.dumps({'type': 'stream', 'content': chr(10) + chr(10)})}\n\n"
                        lines = para.split('\n')
                        for li, line in enumerate(lines):
                            if li > 0:
                                yield f"data: {json.dumps({'type': 'stream', 'content': chr(10)})}\n\n"
                            words = line.split(' ')
                            chunk_size = max(1, len(words) // 4) if len(words) > 8 else 1
                            for ci in range(0, len(words), chunk_size):
                                chunk = ' '.join(words[ci:ci + chunk_size])
                                if ci > 0:
                                    chunk = ' ' + chunk
                                yield f"data: {json.dumps({'type': 'stream', 'content': chunk})}\n\n"
                                await asyncio.sleep(config.api.streaming_chunk_delay)

                if output_info and output_info.get('files'):
                    yield _buffer_and_yield(f"data: {json.dumps({'type': 'output_saved', 'output': output_info})}\n\n")

                # Send full content in the result event (frontend can show expandable)
                yield _buffer_and_yield(f"data: {json.dumps({'type': 'result', 'content': content, 'cost': cost, 'duration': duration, 'output': output_info, 'truncated': is_truncated})}\n\n")

                # Get agent tracking info from final task
                final_task = task_manager.get_task(task_id)
                task_steps = final_task.steps if final_task else []

                # Extract executing agents from task tracking and steps
                assigned_to = final_task.assigned_to if final_task else COORDINATOR_ID
                delegated_agents = final_task.delegated_agents if final_task else []
                executing_agents = final_task.executing_agents if final_task else []

                # Fallback: Extract unique agent names from steps (exclude Organizer)
                if not delegated_agents and task_steps:
                    delegated_agents = list({
                        s.agent for s in task_steps
                        if s.agent and 'organizer' not in s.agent.lower()
                    })

                # Broadcast task_completed to Dashboard with agent info + pool members
                await _subscription_manager.broadcast("task_completed", {
                    "task": {
                        "id": task_id,
                        "status": "completed",
                        "result": content,
                        "cost": cost,
                        "duration": duration,
                        "completed_at": final_task.completed_at if final_task else time.time(),
                        "assigned_to": assigned_to,
                        "delegated_agents": delegated_agents,
                        "executing_agents": executing_agents,
                        "output_dir": output_info.get("output_dir") if output_info else None,
                        "progress": {"phase": "completed", "percentage": 100, "phase_label": "Task completed",
                                     "total_workers": _task_progress.get("total_workers", 0),
                                     "completed_workers": _task_progress.get("completed_workers", 0),
                                     "active_workers": 0, "skipped_workers": _task_progress.get("skipped_workers", 0),
                                     "worker_statuses": _task_progress.get("worker_statuses", [])},
                    },
                    "pool_members": _pool_members_shared if _pool_members_shared else None,
                })

                if memory_session_id and content:
                    try:
                        asyncio.create_task(
                            persist_agent_memory_after_completion(
                                agent_id=memory_agent_id,
                                session_id=memory_session_id,
                                task_id=task_id,
                                user_message=original_user_prompt,
                                assistant_message=content,
                                cfg=config,
                                logger=logger,
                                resolve_runtime_chat_model=_resolve_runtime_chat_model,
                            )
                        )
                    except Exception as memory_exc:
                        logger.warning(
                            "Failed to schedule agent memory persistence task=%s session=%s: %s",
                            task_id,
                            memory_session_id,
                            memory_exc,
                            exc_info=True,
                        )

                # Persist task to database
                try:
                    db = get_database()
                    db.save_task({
                        "id": task_id,
                        "name": display_prompt[:100],
                        "description": display_prompt,
                        "status": "completed",
                        "task_type": "local",
                        "assigned_to": assigned_to,
                        "delegated_agents": delegated_agents,
                        "executing_agents": executing_agents,
                        "steps": _serialize_task_steps_for_db(task_steps),
                        "result": content,
                        "cost": cost,
                        "output_dir": output_info.get("output_dir") if output_info else None,
                        "created_at": final_task.created_at if final_task else time.time(),
                        "started_at": final_task.started_at if final_task else None,
                        "completed_at": time.time(),
                        "metadata": {
                            "pool_members": final_task.pool_members if final_task else None,
                            "selected_members": final_task.selected_members if final_task else None,
                            "execution_mode": final_task.execution_mode if final_task else None,
                            "progress": final_task.progress.to_dict() if final_task else None,
                        },
                    })
                    logger.info(f"Task {task_id} persisted to database")
                except Exception as e:
                    logger.warning(f"Failed to persist task to database: {e}")

                # Mark sandbox as completed if one was created
                if sandbox_id and sandbox_id in _sandboxes:
                    # Add completion event to sandbox event log
                    add_sandbox_event_entry("info", {
                        "message": "✅ Task completed successfully",
                        "tool": "system",
                    })
                    if cost:
                        token_info = f"{cost.get('total_tokens', 0)} tokens"
                        if cost.get('input_tokens'):
                            token_info += f" ({cost['input_tokens']} in / {cost.get('output_tokens', 0)} out)"
                        add_sandbox_event_entry("info", {
                            "message": f"Token usage: {token_info}, Duration: {duration:.1f}s" if duration else f"Token usage: {token_info}",
                            "tool": "metrics",
                        })

                    _sandboxes[sandbox_id]["state"] = "completed"
                    _sandboxes[sandbox_id]["completed"] = time.time()
                    created_ts = _sandboxes[sandbox_id].get("created", _sandboxes[sandbox_id].get("lastHeartbeat", time.time()))
                    _sandboxes[sandbox_id]["duration"] = int((time.time() - created_ts) * 1000)
                    # Notify sandbox stream subscribers
                    await _broadcast_sandbox_update("state_changed", sandbox_id)
                    # Send sandbox completed event
                    yield f"data: {json.dumps({'type': 'sandbox_completed', 'sandbox_id': sandbox_id})}\n\n"

                    # Auto-cleanup sandbox resources (Docker container, workspace, etc.)
                    try:
                        await _cleanup_sandbox_resources(sandbox_id)
                    except Exception as cleanup_err:
                        logger.debug(f"Post-task sandbox cleanup error: {cleanup_err}")

    except Exception as e:
        logger.error(f"Agent response generation failed: {e}")
        task_manager.fail_task(task_id, str(e))
        yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

        # Broadcast task_failed to Dashboard
        _exc_failed_task = task_manager.get_task(task_id)
        await _subscription_manager.broadcast("task_failed", {
            "task": {
                "id": task_id,
                "status": "failed",
                "error": str(e),
                "completed_at": _exc_failed_task.completed_at if _exc_failed_task else time.time(),
                "progress": {"phase": "completed", "percentage": 100, "phase_label": "Task failed",
                             "total_workers": _task_progress.get("total_workers", 0),
                             "completed_workers": _task_progress.get("completed_workers", 0),
                             "active_workers": 0, "skipped_workers": _task_progress.get("skipped_workers", 0),
                             "worker_statuses": _task_progress.get("worker_statuses", [])},
            }
        })

        # Persist failed task to database
        try:
            db = get_database()
            final_task = task_manager.get_task(task_id)
            final_steps = _serialize_task_steps_for_db(final_task.steps if final_task else [])
            db.save_task({
                "id": task_id,
                "name": display_prompt[:100],
                "description": display_prompt,
                "status": "failed",
                "task_type": "local",
                "assigned_to": final_task.assigned_to if final_task else None,
                "delegated_agents": final_task.delegated_agents if final_task else [],
                "executing_agents": final_task.executing_agents if final_task else [],
                "steps": final_steps,
                "error": str(e),
                "result": final_task.result if final_task else None,
                "cost": final_task.cost.to_dict() if final_task else {},
                "output_dir": final_task.output_dir if final_task else None,
                "created_at": final_task.created_at if final_task else time.time(),
                "started_at": final_task.started_at if final_task else None,
                "completed_at": time.time(),
                "metadata": {
                    "pool_members": final_task.pool_members if final_task else None,
                    "selected_members": final_task.selected_members if final_task else None,
                    "execution_mode": final_task.execution_mode if final_task else None,
                    "progress": final_task.progress.to_dict() if final_task else None,
                },
            })
        except Exception as db_err:
            logger.warning(f"Failed to persist failed task to database: {db_err}")

        # Mark sandbox as failed if one was created
        if sandbox_id and sandbox_id in _sandboxes:
            add_sandbox_event_entry("error", {
                "message": f"Task failed: {str(e)[:200]}",
                "error": str(e)[:200],
            })
            _sandboxes[sandbox_id]["state"] = "error"
            await _broadcast_sandbox_update("state_changed", sandbox_id)

            # Auto-cleanup sandbox resources even on failure
            try:
                await _cleanup_sandbox_resources(sandbox_id)
            except Exception as cleanup_err:
                logger.debug(f"Post-failure sandbox cleanup error: {cleanup_err}")

    yield "data: [DONE]\n\n"

    # Schedule buffer and budget cleanup after a configurable grace period
    # so returning clients can still reconnect shortly after completion.
    cleanup_delay = max(0.0, float(config.api.chat_buffer_cleanup_delay))
    async def _cleanup_chat_buffer():
        await asyncio.sleep(cleanup_delay)
        with _chat_event_buffer_lock:
            _chat_event_buffer.pop(task_id, None)
        with _approval_lock:
            _task_budgets.pop(task_id, None)
        logger.debug(f"[ChatBuffer] Cleaned up buffer and budget for task {task_id}")

    asyncio.ensure_future(_cleanup_chat_buffer())


# ============================================================================
# Frontend Static Files
# ============================================================================


_frontend_built = GUI_DIST_DIR.exists() and (GUI_DIST_DIR / "index.html").exists()

if _frontend_built:
    # Mount static assets directory
    app.mount("/assets", StaticFiles(directory=GUI_DIST_DIR / "assets"), name="assets")


@app.get("/")
async def serve_root():
    """Serve frontend or instructions."""
    if _frontend_built:
        return FileResponse(GUI_DIST_DIR / "index.html")
    else:
        return HTMLResponse(content="""
<!DOCTYPE html>
<html>
<head><title>Teaming24</title><meta charset="utf-8"></head>
<body style="font-family: system-ui; padding: 40px; max-width: 600px; margin: 0 auto; background: #1a1a2e; color: #eee;">
    <h1>🚀 Teaming24 API Server</h1>
    <p style="color: #0f0;">✓ API is running!</p>

    <h3>Frontend Setup</h3>

    <h4>Option 1: Build for production</h4>
    <pre style="background: #16213e; padding: 15px; border-radius: 5px; overflow-x: auto;">
cd teaming24/gui
npm install
npm run build
# Restart: uv run python -m teaming24.server.cli</pre>

    <h4>Option 2: Dev mode (hot reload)</h4>
    <pre style="background: #16213e; padding: 15px; border-radius: 5px; overflow-x: auto;">
cd teaming24/gui
npm install
npm run dev
# Open http://localhost:3000</pre>

    <h3>API Endpoints</h3>
    <ul>
        <li><a href="/docs" style="color: #4fc3f7;">/docs</a> - Swagger UI</li>
        <li><a href="/api/health" style="color: #4fc3f7;">/api/health</a> - Health check</li>
        <li><a href="/api/sandbox" style="color: #4fc3f7;">/api/sandbox</a> - Sandbox list</li>
    </ul>
</body>
</html>
        """)


# Catch-all route for SPA (must be last)
if _frontend_built:
    @app.get("/{path:path}")
    async def serve_spa(path: str):
        """Serve SPA for non-API routes."""
        # Don't intercept API routes
        if path.startswith("api") or path in ("docs", "redoc", "openapi.json"):
            raise HTTPException(status_code=404)

        # Serve static files if they exist
        file_path = GUI_DIST_DIR / path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)

        # Otherwise serve index.html for SPA routing
        return FileResponse(GUI_DIST_DIR / "index.html")


# ============================================================================
# Lifecycle (Lifespan Event Handler)
# ============================================================================


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    """
    Application lifespan handler.

    Combines startup and shutdown events using the modern FastAPI lifespan pattern.
    Reference: https://fastapi.tiangolo.com/advanced/events/
    """
    # === STARTUP ===
    # Suppress noisy polling endpoints from uvicorn access logs (must run after uvicorn init)
    import logging

    from teaming24.utils.logger import NoisyAccessFilter
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.addFilter(NoisyAccessFilter())
    uvicorn_access.setLevel(logging.WARNING)

    # Store the main event loop so worker threads can schedule coroutines
    threading._teaming24_main_loop = asyncio.get_running_loop()

    # Clean up old task outputs (configurable via output.cleanup_max_age_days)
    try:
        removed = get_output_manager().cleanup_old_outputs()
        if removed > 0:
            logger.info(f"Startup: cleaned up {removed} old task outputs")
    except Exception as e:
        logger.warning(f"Output cleanup at startup failed: {e}")

    # Restore wallet transactions from DB
    try:
        _wallet_limit = max(1, int(config.api.wallet_ledger_capacity))
        db_txs = get_database().get_wallet_transactions(limit=_wallet_limit)
        if db_txs:
            _wallet_service.restore_wallet_transactions(db_txs)
            logger.info(f"Startup: restored {len(db_txs)} wallet transactions from DB, balance={_st.mock_balance}")
    except Exception as e:
        logger.warning(f"Wallet restore at startup failed: {e}")

    # Initialize network manager
    manager = get_network_manager()

    # Initialize LocalCrew early so the network manager's local_node carries
    # accurate worker capabilities from the very first broadcast/handshake.
    try:
        crew = get_local_crew_singleton()
        if crew:
            _refresh_local_node_advertisement()
            logger.info("Local crew initialized at startup — AN advertisement up to date")
    except Exception as e:
        logger.warning(f"Could not initialize LocalCrew at startup: {e}")

    # Auto-start LAN discovery if config.network.auto_online is true.
    # This makes the node discoverable without needing the frontend.
    if config.network.auto_online:
        try:
            await manager.start()
            manager.set_discoverable(True)
            logger.info("Auto-online: LAN discovery started, node is discoverable")
        except Exception as e:
            logger.warning(f"Auto-online: failed to start LAN discovery: {e}")

    # Start peer health check background task
    app.state.peer_health_task = asyncio.create_task(_peer_health_loop())

    # Start OpenHands status sync loop (keeps sandbox heartbeat fresh)
    async def _openhands_sync_loop():
        """Periodically sync OpenHands status to keep heartbeat updated."""
        while True:
            try:
                await sync_openhands_status()
            except Exception as e:
                logger.debug(f"OpenHands sync: {e}")
            await asyncio.sleep(5)

    app.state.openhands_sync_task = asyncio.create_task(_openhands_sync_loop())

    # Log startup info
    frontend_status = "serving from dist" if _frontend_built else "not built (use npm run dev)"
    logger.info("Teaming24 API started", extra={
        "docs": config.api.docs_enabled,
        "frontend": frontend_status,
    })

    # Wire the global shutdown event into the SubscriptionManager
    _subscription_manager.set_shutdown_event(_shutdown_event)

    # Initialize Skill Registry — load bundled + managed + workspace skills
    try:
        from teaming24.agent.skills import Skill as SkillModel
        from teaming24.agent.skills import get_skill_registry
        skill_registry = get_skill_registry()
        skill_registry.load()
        db = get_database()
        db_skills_raw = db.get_skills()
        db_skill_objs = [SkillModel.from_dict(s) for s in db_skills_raw]
        skill_registry.merge_db_skills(db_skill_objs)
        logger.info(f"Skill registry: {len(skill_registry)} skills loaded")
    except Exception as e:
        logger.debug(f"Skill registry init: {e}")

    # Initialize Gateway — central orchestrator for channels → agents
    from teaming24.gateway import get_gateway
    gateway = get_gateway()
    gateway.set_task_manager(get_task_manager_instance())
    gateway.set_subscription_manager(_subscription_manager)
    gateway.set_ws_hub(get_ws_hub())
    try:
        await gateway.start()
        logger.info("Gateway started — channels connected to agent framework")
    except Exception as e:
        logger.warning(f"Gateway startup failed (channels will not be active): {e}")

    # 13. Start OpenClaw bridge (no-op when disabled in config)
    try:
        from teaming24.plugins.openclaw_plugin import setup_openclaw_plugin
        await setup_openclaw_plugin()
    except Exception as e:
        logger.debug(f"OpenClaw bridge startup: {e}")

    yield  # Application runs here

    # === SHUTDOWN ===
    logger.info("Shutting down — signalling all SSE connections to close...")

    # Stop the gateway (graceful channel adapter shutdown)
    try:
        await gateway.stop()
    except Exception as e:
        logger.debug(f"Gateway shutdown: {e}")

    # Signal all SSE generators to exit their while-loops.
    # This is the critical step that prevents "Waiting for connections to close"
    # hanging forever during uvicorn --reload.
    _shutdown_event.set()

    # Also explicitly drain the SubscriptionManager queues
    _subscription_manager.close_all()

    # Drain sandbox list subscriber queues
    for subscriber_queue in list(_sandbox_list_subscribers):
        try:
            subscriber_queue.put_nowait(None)
        except Exception as e:
            logger.debug(f"Failed to drain sandbox list subscriber queue: {e}")
            pass
    _sandbox_list_subscribers.clear()

    # Give SSE generators a moment to notice the shutdown event and exit
    await asyncio.sleep(0.3)

    # Stop background tasks
    for task_name in ["peer_health_task", "openhands_sync_task"]:
        task = getattr(app.state, task_name, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.debug("Background task cancelled during shutdown: %s", task_name)
                pass

    # Notify connected peers we are going offline (best-effort)
    import httpx

    try:
        manager = get_network_manager()
        peers: list[NodeInfo] = []
        peers.extend(list(manager.wan_nodes.values()))
        peers.extend(list(manager.inbound_peers.values()))

        if peers:
            async with httpx.AsyncClient(timeout=config.api.health_check_http_timeout) as client:
                await asyncio.gather(
                    *[
                        client.post(
                            f"http://{peer.ip}:{peer.port}/api/network/peer-disconnect",
                            json={"nodeId": manager.local_node.id, "reason": "shutdown"},
                        )
                        for peer in peers
                    ],
                    return_exceptions=True,
                )
    except Exception as e:
        logger.debug(f"Failed to notify peers of shutdown: {e}")
        pass

    # Stop network manager
    if _network_manager:
        await _network_manager.stop()

    # Cleanup ALL registered sandboxes (Docker containers, workspaces)
    active_sandbox_ids = [
        sid for sid, info in _sandboxes.items()
        if not info.get("resources_released")
    ]
    if active_sandbox_ids:
        logger.info(f"Cleaning up {len(active_sandbox_ids)} sandbox(es) on shutdown")
        cleanup_tasks = [_cleanup_sandbox_resources(sid) for sid in active_sandbox_ids]
        await asyncio.gather(*cleanup_tasks, return_exceptions=True)

    # Cleanup OpenHands pool (catches any runtimes not tracked in _sandboxes)
    try:
        from teaming24.runtime.openhands import OPENHANDS_AVAILABLE, cleanup_all_openhands
        if OPENHANDS_AVAILABLE:
            await cleanup_all_openhands()
            logger.info("OpenHands pool cleaned up")
    except ImportError:
        logger.debug("OpenHands cleanup skipped: module not available")
        pass
    except Exception as e:
        logger.debug(f"OpenHands cleanup: {e}")

    # Stop OpenClaw bridge
    try:
        from teaming24.plugins.openclaw_plugin import teardown_openclaw_plugin
        await teardown_openclaw_plugin()
    except Exception as e:
        logger.debug(f"OpenClaw bridge teardown: {e}")

    logger.info("Teaming24 API stopped")


# Set the lifespan on the app router
app.router.lifespan_context = _app_lifespan


def create_app() -> FastAPI:
    """Create and return the FastAPI application."""
    return app
