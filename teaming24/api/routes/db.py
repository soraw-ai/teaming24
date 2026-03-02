"""
Database persistence API — settings, history, sessions, nodes, tasks, chat.

This module provides CRUD endpoints for all SQLite-backed data: key-value
settings, connection history, sessions, known nodes, tasks with steps, and
chat sessions with messages. Also includes network search and security
config.

Endpoints
---------
Settings:
  - GET /api/db/settings — All settings
  - GET /api/db/settings/{key} — Single setting
  - POST /api/db/settings/{key} — Set setting
  - DELETE /api/db/settings/{key} — Delete setting
  - POST /api/db/settings/reset — Clear all settings

History:
  - GET /api/db/history — Connection history (limit param)
  - POST /api/db/history — Add history entry
  - DELETE /api/db/history/{node_id} — Remove entry
  - DELETE /api/db/history — Clear history

Sessions:
  - GET /api/db/sessions — Connection sessions (limit, node_id)
  - POST /api/db/sessions — Add session
  - DELETE /api/db/sessions — Clear sessions

Nodes:
  - GET /api/db/nodes — Known nodes (node_type filter)
  - POST /api/db/nodes — Upsert node
  - DELETE /api/db/nodes/{node_id} — Remove node

Tasks:
  - GET /api/db/tasks — List tasks (status, limit)
  - GET /api/db/tasks/{task_id} — Get task
  - POST /api/db/tasks — Save task
  - DELETE /api/db/tasks/{task_id} — Delete task
  - DELETE /api/db/tasks — Clear all tasks
  - GET /api/db/tasks/{task_id}/steps — Get task steps
  - POST /api/db/tasks/{task_id}/steps — Save step

Chat:
  - GET /api/db/chat/sessions — List chat sessions
  - GET /api/db/chat/sessions/{session_id} — Get session + messages
  - POST /api/db/chat/sessions — Save session
  - DELETE /api/db/chat/sessions/{session_id} — Delete session
  - GET /api/db/chat/sessions/{session_id}/messages — Get messages
  - POST /api/db/chat/sessions/{session_id}/messages — Save message

Other:
  - POST /api/db/reset — Full data reset (requires confirm)
  - GET /api/network/search — Search reachable nodes (q param)
  - POST /api/config/security — Set local password

Dependencies
------------
Uses ``teaming24.api.deps``: ``config``, ``logger``, ``get_database``,
``get_network_manager``.
Uses ``teaming24.config``: ``CONFIG_DIR``, ``UNIFIED_CONFIG_FILE``.
Also coordinates full local resets across runtime state registries.

Extending
---------
Use ``get_database()`` for all DB access. Add new endpoints following
the same CRUD pattern. For new tables, extend the database module first.
"""
from __future__ import annotations

import ipaddress
import json
import os
import queue as queue_module
import shutil
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from teaming24.api import state as _st
from teaming24.api.deps import config, get_database, get_network_manager, logger
from teaming24.config import CONFIG_DIR, UNIFIED_CONFIG_FILE
from teaming24.memory.manager import MemoryManager
from teaming24.session.compaction import DEFAULT_TRANSCRIPT_DIR
from teaming24.session.store import SessionStore
from teaming24.task.manager import get_task_manager

router = APIRouter(tags=["database"])


def _enforce_local_admin(request: Request) -> None:
    """Restrict sensitive writes to loopback unless explicitly allowed."""
    allow_remote = os.getenv("TEAMING24_ALLOW_REMOTE_ADMIN", "").lower() in ("1", "true", "yes")
    if allow_remote:
        return

    host = request.client.host if request and request.client else ""
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError as exc:
        logger.debug("Non-IP request host in _enforce_local_admin: %s (%s)", host, exc)
        if host == "localhost":
            return

    raise HTTPException(status_code=403, detail="local access only")


_RUNTIME_ENV_KEY_MAP: dict[str, str] = {
    "openaiApiKey": "OPENAI_API_KEY",
    "anthropicApiKey": "ANTHROPIC_API_KEY",
    "flockApiKey": "FLOCK_API_KEY",
    "localApiKey": "LOCAL_LLM_API_KEY",
    "openaiBaseUrl": "OPENAI_API_BASE",
    "anthropicBaseUrl": "ANTHROPIC_API_BASE",
    "flockBaseUrl": "FLOCK_API_BASE",
    "localBaseUrl": "LOCAL_LLM_API_BASE",
}

_ENV_RUNTIME_KEY_MAP: dict[str, str] = {env_key: key for key, env_key in _RUNTIME_ENV_KEY_MAP.items()}
_ENV_FILE = Path(UNIFIED_CONFIG_FILE).resolve().parents[2] / ".env"


def _read_env_file_values() -> dict[str, str]:
    """Read env-backed settings directly from .env so UI reflects file changes."""
    values: dict[str, str] = {}
    try:
        if not _ENV_FILE.exists():
            return values
        for raw_line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            env_key, env_value = line.split("=", 1)
            env_key = env_key.strip()
            env_value = env_value.strip().strip('"').strip("'")
            setting_key = _ENV_RUNTIME_KEY_MAP.get(env_key)
            if setting_key:
                values[setting_key] = env_value
    except Exception as exc:
        logger.warning("Failed to read %s: %s", _ENV_FILE, exc, exc_info=True)
    return values


def _merge_settings_with_env(settings: dict[str, Any]) -> dict[str, Any]:
    """Overlay DB settings with .env/process env values for env-backed keys."""
    merged = dict(settings or {})
    try:
        merged.setdefault("agentMemoryEnabled", bool(getattr(config.memory, "enabled", True)))
        merged.setdefault("respectContextWindow", bool(getattr(config.memory, "respect_context_window", True)))
    except Exception as exc:
        logger.warning("Failed to merge memory defaults from config: %s", exc, exc_info=True)
    file_values = _read_env_file_values()
    for key, env_key in _RUNTIME_ENV_KEY_MAP.items():
        if key in file_values:
            merged[key] = file_values[key]
            continue
        env_value = os.getenv(env_key, "")
        if env_value:
            merged[key] = env_value
    return merged


def _update_env_file(updates: dict[str, Any]) -> None:
    """Persist env-backed settings into .env and remove cleared keys."""
    if not updates:
        return
    lines: list[str] = []
    remaining_updates = {
        str(env_key): ("" if value is None else str(value).strip())
        for env_key, value in updates.items()
    }
    try:
        if _ENV_FILE.exists():
            for line in _ENV_FILE.read_text(encoding="utf-8").splitlines(keepends=True):
                stripped = line.strip()
                replaced = False
                for env_key, value in list(remaining_updates.items()):
                    if stripped.startswith(f"{env_key}=") or stripped.startswith(f"# {env_key}="):
                        if value:
                            lines.append(f"{env_key}={value}\n")
                        remaining_updates.pop(env_key, None)
                        replaced = True
                        break
                if not replaced:
                    lines.append(line)
        for env_key, value in remaining_updates.items():
            if not value:
                continue
            if lines and lines[-1].strip():
                lines.append("\n")
            lines.append(f"{env_key}={value}\n")
        _ENV_FILE.write_text("".join(lines), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to update %s: %s", _ENV_FILE, exc, exc_info=True)


def _sync_runtime_env_from_settings(updates: dict[str, Any]) -> None:
    """Mirror runtime LLM key/base-url settings into process env for consistency."""
    if not updates:
        return
    for key, env_key in _RUNTIME_ENV_KEY_MAP.items():
        if key not in updates:
            continue
        try:
            raw = updates.get(key)
            value = str(raw).strip() if raw is not None else ""
            if value:
                os.environ[env_key] = value
            else:
                os.environ.pop(env_key, None)
        except Exception as exc:
            logger.warning(
                "Failed syncing runtime env key from settings key=%s env=%s: %s",
                key,
                env_key,
                exc,
                exc_info=True,
            )


def _clear_transcript_files() -> int:
    """Delete persisted JSONL transcript files."""
    deleted = 0
    try:
        if DEFAULT_TRANSCRIPT_DIR.exists():
            for path in DEFAULT_TRANSCRIPT_DIR.iterdir():
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
                deleted += 1
    except Exception as exc:
        logger.warning("Failed to clear transcript dir %s: %s", DEFAULT_TRANSCRIPT_DIR, exc, exc_info=True)
    return deleted


def _clear_runtime_wallet_state() -> None:
    """Reset in-memory wallet state so API responses match the cleared DB."""
    with _st.wallet_lock:
        _st.wallet_ledger.clear()
        _st.mock_balance = float(config.payment.mock.initial_balance)
        _st.wallet_config.clear()
        _st.wallet_config.update({
            "address": "",
            "is_configured": False,
            "network": "base-sepolia",
            "_private_key": None,
        })


def _clear_runtime_task_state() -> int:
    """Clear in-memory task manager cache."""
    try:
        return get_task_manager().clear_tasks()
    except Exception as exc:
        logger.warning("Failed to clear in-memory task manager state: %s", exc, exc_info=True)
        return 0


def _clear_runtime_network_state() -> None:
    """Clear runtime node/network caches without reinitializing services."""
    manager = _st.network_manager
    if manager is not None:
        try:
            manager.clear_wan_nodes()
        except Exception as exc:
            logger.warning("Failed clearing WAN nodes during reset: %s", exc, exc_info=True)
        try:
            manager.inbound_peers.clear()
            manager.discovery.known_nodes.clear()
            manager.set_scanning(False)
        except Exception as exc:
            logger.warning("Failed clearing runtime network caches during reset: %s", exc, exc_info=True)

    _st.inbound_connected_since.clear()
    _st.peer_failure_counts.clear()

    try:
        from teaming24.api import server as api_server

        listings = getattr(api_server, "_marketplace_listings", None)
        if isinstance(listings, dict):
            listings.clear()
    except Exception as exc:
        logger.warning("Failed clearing in-memory marketplace listings during reset: %s", exc, exc_info=True)


def _clear_runtime_misc_state() -> None:
    """Clear other transient registries that survive the DB reset."""
    with _st.chat_event_buffer_lock:
        _st.chat_event_buffer.clear()

    _st.approval_requests.clear()
    _st.task_budgets.clear()
    _st.sandboxes.clear()
    _st.sandbox_events.clear()
    _st.sandbox_screenshots.clear()
    _st.sandbox_list_subscribers.clear()
    _st.openhands_sandbox_id = None

    while True:
        try:
            _st.sandbox_stream_queue.get_nowait()
        except queue_module.Empty:
            break
        except Exception as exc:
            logger.warning("Failed draining sandbox stream queue during reset: %s", exc, exc_info=True)
            break


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class SettingUpdate(BaseModel):
    value: Any


class SettingsBatchUpdate(BaseModel):
    settings: dict[str, Any]


@router.get("/api/db/settings")
async def get_all_settings():
    return {"settings": _merge_settings_with_env(get_database().get_all_settings())}


@router.get("/api/db/settings/{key}")
async def get_setting(key: str):
    merged = _merge_settings_with_env(get_database().get_all_settings())
    return {"key": key, "value": merged.get(key)}


@router.post("/api/db/settings/batch")
async def set_settings_batch(update: SettingsBatchUpdate):
    settings = update.settings or {}
    get_database().set_settings(settings)
    _sync_runtime_env_from_settings(settings)
    _update_env_file({
        _RUNTIME_ENV_KEY_MAP[key]: settings[key]
        for key in settings
        if key in _RUNTIME_ENV_KEY_MAP
    })
    return {"status": "ok", "count": len(settings)}


@router.post("/api/db/settings/{key}")
async def set_setting(key: str, update: SettingUpdate):
    get_database().set_setting(key, update.value)
    _sync_runtime_env_from_settings({key: update.value})
    env_key = _RUNTIME_ENV_KEY_MAP.get(key)
    if env_key:
        _update_env_file({env_key: update.value})
    return {"key": key, "value": update.value}


@router.delete("/api/db/settings/{key}")
async def delete_setting(key: str):
    get_database().delete_setting(key)
    if key in _RUNTIME_ENV_KEY_MAP:
        _sync_runtime_env_from_settings({key: ""})
        _update_env_file({_RUNTIME_ENV_KEY_MAP[key]: ""})
    return {"status": "deleted", "key": key}


@router.post("/api/db/settings/reset")
async def reset_all_settings():
    get_database().clear_all_settings()
    _sync_runtime_env_from_settings({k: "" for k in _RUNTIME_ENV_KEY_MAP})
    _update_env_file({env_key: "" for env_key in _RUNTIME_ENV_KEY_MAP.values()})
    return {"status": "reset", "message": "All settings cleared, defaults will be used"}


# ---------------------------------------------------------------------------
# Agent runtime settings helper (used by agent service layer)
# ---------------------------------------------------------------------------

def get_agent_runtime_settings() -> dict:
    """Return agent runtime settings from the database."""
    try:
        db = get_database()
        s = _merge_settings_with_env(db.get_all_settings())
        _sync_runtime_env_from_settings(s)
        worker_model_overrides: dict[str, str] = {}
        try:
            for agent in db.get_agents():
                if str(agent.get("type", "")).strip().lower() != "worker":
                    continue
                model = str(agent.get("model", "") or "").strip()
                if not model:
                    continue
                agent_id = str(agent.get("id", "") or "").strip()
                agent_name = str(agent.get("name", "") or "").strip()
                if agent_id:
                    worker_model_overrides[agent_id] = model
                if agent_name:
                    worker_model_overrides[agent_name] = model
                    worker_model_overrides[agent_name.lower()] = model
        except Exception as exc:
            logger.warning("Failed to build worker model overrides: %s", exc, exc_info=True)
        return {
            'crewaiVerbose': s.get('crewaiVerbose', True),
            'crewaiProcess': s.get('crewaiProcess', 'hierarchical'),
            'crewaiMemory': s.get('crewaiMemory', False),
            'crewaiMaxRpm': s.get('crewaiMaxRpm', 10),
            'agentScenario': s.get('agentScenario', 'product_team'),
            'defaultLLMProvider': s.get('defaultLLMProvider', 'flock'),
            'defaultModel': s.get('defaultModel', 'gpt-5.2'),
            'openaiApiKey': s.get('openaiApiKey', ''),
            'anthropicApiKey': s.get('anthropicApiKey', ''),
            'flockApiKey': s.get('flockApiKey', ''),
            'localApiKey': s.get('localApiKey', 'local'),
            'openaiBaseUrl': s.get('openaiBaseUrl', 'https://api.openai.com/v1'),
            'anthropicBaseUrl': s.get('anthropicBaseUrl', 'https://api.anthropic.com'),
            'flockBaseUrl': s.get('flockBaseUrl', 'https://api.flock.io/v1'),
            'localBaseUrl': s.get('localBaseUrl', 'http://localhost:11434/v1'),
            'localCustomModel': s.get('localCustomModel', 'llama3.1'),
            'organizerModel': s.get('organizerModel', 'flock/gpt-5.2'),
            'coordinatorModel': s.get('coordinatorModel', 'flock/gpt-5.2'),
            'workerDefaultModel': s.get('workerDefaultModel', 'flock/gpt-5.2'),
            'anRouterModel': s.get('anRouterModel', 'flock/gpt-5.2'),
            'localAgentRouterModel': s.get('localAgentRouterModel', 'flock/gpt-5.2'),
            'agentMemoryEnabled': s.get('agentMemoryEnabled', bool(getattr(config.memory, 'enabled', True))),
            'respectContextWindow': s.get(
                'respectContextWindow',
                bool(getattr(config.memory, 'respect_context_window', True)),
            ),
            'crewaiPlanning': s.get('crewaiPlanning', False),
            'crewaiPlanningLlm': s.get('crewaiPlanningLlm', 'flock/gpt-5.2'),
            'crewaiReasoning': s.get('crewaiReasoning', False),
            'crewaiMaxReasoningAttempts': s.get('crewaiMaxReasoningAttempts', 3),
            'crewaiStreaming': s.get('crewaiStreaming', True),
            'default_llm_provider': s.get('defaultLLMProvider', 'flock'),
            'default_model': s.get('defaultModel', 'gpt-5.2'),
            'openai_api_key': s.get('openaiApiKey', ''),
            'anthropic_api_key': s.get('anthropicApiKey', ''),
            'flock_api_key': s.get('flockApiKey', ''),
            'local_api_key': s.get('localApiKey', 'local'),
            'openai_base_url': s.get('openaiBaseUrl', 'https://api.openai.com/v1'),
            'anthropic_base_url': s.get('anthropicBaseUrl', 'https://api.anthropic.com'),
            'flock_base_url': s.get('flockBaseUrl', 'https://api.flock.io/v1'),
            'local_base_url': s.get('localBaseUrl', 'http://localhost:11434/v1'),
            'local_custom_model': s.get('localCustomModel', 'llama3.1'),
            'organizer_model': s.get('organizerModel', 'flock/gpt-5.2'),
            'coordinator_model': s.get('coordinatorModel', 'flock/gpt-5.2'),
            'worker_default_model': s.get('workerDefaultModel', 'flock/gpt-5.2'),
            'an_router_model': s.get('anRouterModel', 'flock/gpt-5.2'),
            'local_agent_router_model': s.get('localAgentRouterModel', 'flock/gpt-5.2'),
            'crewai_planning': s.get('crewaiPlanning', False),
            'crewai_planning_llm': s.get('crewaiPlanningLlm', 'flock/gpt-5.2'),
            'crewai_reasoning': s.get('crewaiReasoning', False),
            'crewai_max_reasoning_attempts': s.get('crewaiMaxReasoningAttempts', 3),
            'crewai_streaming': s.get('crewaiStreaming', True),
            'taskOutputEnabled': s.get('taskOutputEnabled', True),
            'taskOutputDir': s.get('taskOutputDir', '~/.teaming24/outputs'),
            'workerModelOverrides': worker_model_overrides,
            'worker_model_overrides': worker_model_overrides,
        }
    except Exception as e:
        logger.warning("Failed to get agent runtime settings: %s", e, exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# Connection history
# ---------------------------------------------------------------------------

@router.get("/api/db/history")
async def get_connection_history(limit: int = 20):
    return {"history": get_database().get_connection_history(limit)}


@router.post("/api/db/history")
async def add_to_history(node_data: dict):
    get_database().add_connection_history(node_data)
    return {"status": "added"}


@router.delete("/api/db/history/{node_id}")
async def remove_from_history(node_id: str):
    get_database().remove_connection_history(node_id)
    return {"status": "removed"}


@router.delete("/api/db/history")
async def clear_history():
    get_database().clear_connection_history()
    return {"status": "cleared"}


# ---------------------------------------------------------------------------
# Connection sessions
# ---------------------------------------------------------------------------

@router.get("/api/db/sessions")
async def get_connection_sessions(limit: int = 200, node_id: str | None = None):
    return {"sessions": get_database().get_connection_sessions(limit=limit, node_id=node_id)}


@router.post("/api/db/sessions")
async def add_connection_session(session_data: dict):
    get_database().add_connection_session(session_data)
    return {"status": "added"}


@router.delete("/api/db/sessions")
async def clear_connection_sessions():
    get_database().clear_connection_sessions()
    return {"status": "cleared"}


# ---------------------------------------------------------------------------
# Known nodes
# ---------------------------------------------------------------------------

@router.get("/api/db/nodes")
async def get_known_nodes(node_type: str | None = None):
    return {"nodes": get_database().get_all_nodes(node_type)}


@router.post("/api/db/nodes")
async def save_node(node_data: dict):
    get_database().upsert_node(node_data)
    return {"status": "saved"}


@router.delete("/api/db/nodes/{node_id}")
async def delete_node(node_id: str):
    get_database().remove_node(node_id)
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@router.get("/api/db/tasks")
async def get_db_tasks(status: str | None = None, limit: int = 50):
    return {"tasks": get_database().list_tasks(status=status, limit=limit)}


@router.get("/api/db/tasks/{task_id}")
async def get_db_task(task_id: str):
    task = get_database().get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/api/db/tasks")
async def save_db_task(task_data: dict):
    get_database().save_task(task_data)
    return {"status": "saved", "id": task_data.get("id")}


@router.delete("/api/db/tasks/{task_id}")
async def delete_db_task(task_id: str):
    get_database().delete_task(task_id)
    return {"status": "deleted"}


@router.delete("/api/db/tasks")
async def clear_all_tasks():
    get_database().clear_all_tasks()
    logger.info("All tasks cleared via API")
    return {"status": "cleared"}


@router.post("/api/db/reset")
async def reset_all_data(request: Request):
    _enforce_local_admin(request)
    data = await request.json()
    if data.get("confirm") != "DELETE ALL DATA":
        raise HTTPException(status_code=400, detail="Confirmation text does not match")
    db = get_database()
    db.clear_all_data()

    memory_result = {"deleted_entries": 0, "deleted_vectors": 0, "deleted_markdown_files": 0}
    try:
        memory_result = MemoryManager().reset_all()
    except Exception as exc:
        logger.warning("Failed clearing memory stores during reset: %s", exc, exc_info=True)

    cleared_sessions = 0
    try:
        session_db_path = Path(config.session.store_path).expanduser() if config.session.store_path else None
        cleared_sessions = SessionStore(db_path=session_db_path).clear_all()
    except Exception as exc:
        logger.warning("Failed clearing session store during reset: %s", exc, exc_info=True)

    deleted_transcript_files = _clear_transcript_files()
    cleared_runtime_tasks = _clear_runtime_task_state()
    _clear_runtime_wallet_state()
    _clear_runtime_network_state()
    _clear_runtime_misc_state()

    logger.warning(
        "Full data reset performed via API (sessions=%s tasks=%s memories=%s vectors=%s markdown=%s transcripts=%s)",
        cleared_sessions,
        cleared_runtime_tasks,
        memory_result.get("deleted_entries", 0),
        memory_result.get("deleted_vectors", 0),
        memory_result.get("deleted_markdown_files", 0),
        deleted_transcript_files,
    )
    return {
        "status": "reset",
        "cleared": {
            "database": True,
            "sessions": cleared_sessions,
            "runtimeTasks": cleared_runtime_tasks,
            "memoryEntries": memory_result.get("deleted_entries", 0),
            "memoryVectors": memory_result.get("deleted_vectors", 0),
            "memoryMarkdownFiles": memory_result.get("deleted_markdown_files", 0),
            "transcriptFiles": deleted_transcript_files,
        },
    }


@router.get("/api/db/tasks/{task_id}/steps")
async def get_task_steps(task_id: str):
    return {"steps": get_database().get_task_steps(task_id)}


@router.post("/api/db/tasks/{task_id}/steps")
async def save_task_step(task_id: str, step_data: dict):
    get_database().save_task_step(task_id, step_data)
    return {"status": "saved"}


# ---------------------------------------------------------------------------
# Chat sessions & messages
# ---------------------------------------------------------------------------

@router.get("/api/db/chat/sessions")
async def get_chat_sessions(limit: int = 50):
    db = get_database()
    sessions = db.list_chat_sessions(limit=limit)
    for s in sessions:
        meta = s.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception as exc:
                logger.warning(
                    "Failed to parse chat session metadata for session=%s: %s",
                    s.get("id"),
                    exc,
                    exc_info=True,
                )
                meta = {}
        s["active_task_id"] = meta.get("active_task_id") or s.get("active_task_id")
        s["active_message_id"] = meta.get("active_message_id") or s.get("active_message_id")
    return {"sessions": sessions}


@router.get("/api/db/chat/sessions/{session_id}")
async def get_chat_session(session_id: str):
    db = get_database()
    session = db.get_chat_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session['messages'] = db.get_chat_messages(session_id)
    return session


@router.post("/api/db/chat/sessions")
async def save_chat_session(session_data: dict):
    meta = session_data.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception as exc:
            logger.warning(
                "Failed to parse incoming chat session metadata for session=%s: %s",
                session_data.get("id"),
                exc,
                exc_info=True,
            )
            meta = {}
    if session_data.get("active_task_id"):
        meta["active_task_id"] = session_data["active_task_id"]
    if session_data.get("active_message_id"):
        meta["active_message_id"] = session_data["active_message_id"]
    session_data["metadata"] = meta
    get_database().save_chat_session(session_data)
    return {"status": "saved", "id": session_data.get("id")}


@router.delete("/api/db/chat/sessions/{session_id}")
async def delete_chat_session(session_id: str):
    get_database().delete_chat_session(session_id)
    return {"status": "deleted"}


@router.get("/api/db/chat/sessions/{session_id}/messages")
async def get_chat_messages(session_id: str):
    return {"messages": get_database().get_chat_messages(session_id)}


@router.post("/api/db/chat/sessions/{session_id}/messages")
async def save_chat_message(session_id: str, message_data: dict):
    db = get_database()
    session = db.get_chat_session(session_id)
    if not session:
        db.save_chat_session({"id": session_id, "title": "Chat"})
    db.save_chat_message(session_id, message_data)
    return {"status": "saved"}


# ---------------------------------------------------------------------------
# Network search
# ---------------------------------------------------------------------------

@router.get("/api/network/search")
async def search_nodes(q: str = ""):
    query = (q or "").strip().lower()
    nodes: list[dict[str, Any]] = []

    def _normalize_node(node: Any) -> dict[str, Any]:
        if isinstance(node, dict):
            node_id = node.get("id", "")
            name = node.get("name", "")
            ip = node.get("ip", "")
            port = node.get("port", 0)
            capability = node.get("capability")
            description = node.get("description")
            status = node.get("status", "unknown")
            node_type = node.get("type", node.get("node_type", "unknown"))
            region = node.get("region")
        else:
            node_id = getattr(node, "id", "")
            name = getattr(node, "name", "")
            ip = getattr(node, "ip", "")
            port = getattr(node, "port", 0)
            capability = getattr(node, "capability", None)
            description = getattr(node, "description", None)
            status = getattr(node, "status", "unknown")
            node_type = getattr(node, "type", "unknown")
            region = getattr(node, "region", None)

        return {
            "id": node_id,
            "name": name,
            "ip": ip,
            "port": port,
            "capability": capability or "General",
            "description": description or "",
            "status": status,
            "type": node_type,
            "region": region or "",
        }

    try:
        manager = get_network_manager()
        raw_nodes = manager.get_nodes() if manager else []
        nodes = [_normalize_node(node) for node in raw_nodes]
    except Exception as exc:
        logger.debug("Network manager unavailable for /api/network/search: %s", exc)
        try:
            rows = get_database().get_known_nodes()
            nodes = [_normalize_node(row) for row in rows]
        except Exception as db_exc:
            logger.debug("DB fallback unavailable for /api/network/search: %s", db_exc)
            nodes = []

    def _node_text(node: dict[str, Any]) -> str:
        parts = [
            str(node.get("id", "")),
            str(node.get("name", "")),
            str(node.get("ip", "")),
            str(node.get("capability", "")),
            str(node.get("description", "")),
            str(node.get("region", "")),
        ]
        return " ".join(parts).lower()

    filtered = [node for node in nodes if not query or query in _node_text(node)]
    return {"results": filtered}


# ---------------------------------------------------------------------------
# Security config
# ---------------------------------------------------------------------------

class SecurityUpdate(BaseModel):
    password: str


@router.post("/api/config/security")
async def set_security_config(update: SecurityUpdate, request: Request):
    _enforce_local_admin(request)
    config_file = Path(CONFIG_DIR) / UNIFIED_CONFIG_FILE if not Path(UNIFIED_CONFIG_FILE).is_absolute() else Path(UNIFIED_CONFIG_FILE)
    if config_file.exists():
        with open(config_file) as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    security = data.setdefault("security", {})
    # Canonical key
    security["connection_password"] = update.password
    # Backward-compat mirror for older tooling expecting local_password
    security["local_password"] = update.password
    with open(config_file, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    config.security.connection_password = update.password
    logger.info("Security password updated")
    return {"status": "saved"}
