"""
Framework, channel, memory, and tool-policy configuration endpoints.

This module covers agent tool definitions, channel config (Telegram/Slack/Discord),
framework backend (native/crewai), and memory search/save.

Endpoints
---------
- GET /api/agent/available-tools — List tool sections, groups, profiles
- POST /api/agent/resolve-tools — Resolve enabled tools from profile/allow/deny
- GET /api/channels — List channels (telegram, slack, discord, webchat) and status
- POST /api/channels/config — Update channel config (enabled, tokens)
- GET /api/framework — Get framework backend (native/crewai)
- POST /api/framework — Set framework backend
- GET /api/memory/status — Durable agent-memory usage and compaction status
- POST /api/memory/search — Semantic search over memory store
- GET /api/memory/recent — Recent memory entries (limit param)
- POST /api/memory/save — Save a memory entry

Dependencies
------------
Uses ``teaming24.api.deps``: ``config``, ``logger``, ``get_database``,
``UNIFIED_CONFIG_FILE``. Uses ``teaming24.agent.tool_policy`` for tool
definitions. No state.py usage.

Extending
---------
Add new endpoints with ``@router.get(...)`` or ``@router.post(...)``.
For YAML-backed config, read/write ``UNIFIED_CONFIG_FILE`` and reload
via ``/api/config/reload`` if needed.
"""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Canonical tool policy definitions live in agent/tool_policy.py
from teaming24.agent.tool_policy import (
    TOOL_GROUPS,
    TOOL_PROFILES,
    TOOL_SECTIONS,
    resolve_tool_policy,
)
from teaming24.api.deps import UNIFIED_CONFIG_FILE, config, logger
from teaming24.utils.format import format_timestamp
from teaming24.utils.ids import ORGANIZER_ID

router = APIRouter(tags=["config"])

def _require_local(request: Request) -> JSONResponse | None:
    """Restrict sensitive config writes to loopback unless explicitly allowed."""
    allow_remote = os.getenv("TEAMING24_ALLOW_REMOTE_ADMIN", "").lower() in ("1", "true", "yes")
    if allow_remote:
        return None
    host = request.client.host if request and request.client else ""
    try:
        if ipaddress.ip_address(host).is_loopback:
            return None
    except ValueError as exc:
        logger.debug("Non-IP request host in _require_local: %s (%s)", host, exc)
        if host in ("localhost",):
            return None
    return JSONResponse(status_code=403, content={"error": "local access only"})


@router.get("/api/agent/available-tools")
async def list_available_tools():
    sections = []
    sandbox_extra_ids = set()
    try:
        from teaming24.agent.tools.sandbox_tools import get_sandbox_tool_specs
        for spec in get_sandbox_tool_specs():
            sandbox_extra_ids.add(spec.name)
    except Exception as exc:
        logger.warning("Failed to load sandbox tool specs: %s", exc, exc_info=True)

    known_ids = {t["id"] for section in TOOL_SECTIONS for t in section["tools"]}
    for section in TOOL_SECTIONS:
        sec = {**section, "tools": list(section["tools"])}
        if section["id"] == "sandbox":
            for sid in sorted(sandbox_extra_ids - known_ids):
                sec["tools"].append({"id": sid, "label": sid, "description": f"Sandbox tool: {sid}"})
        sections.append(sec)

    flat_tools = [t for sec in sections for t in sec["tools"]]
    return JSONResponse(content={
        "tools": flat_tools,
        "sections": sections,
        "profiles": {pid: dict(pdef) for pid, pdef in TOOL_PROFILES.items()},
        "groups": TOOL_GROUPS,
    })


@router.post("/api/agent/resolve-tools")
async def resolve_tools_endpoint(request: Request):
    data = await request.json()
    enabled = resolve_tool_policy(
        profile=data.get("profile", "full"),
        allow=data.get("allow"),
        also_allow=data.get("alsoAllow"),
        deny=data.get("deny"),
    )
    return JSONResponse(content={"enabled": enabled})


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------

@router.get("/api/channels")
async def list_channels():
    cfg = config
    channels_cfg = getattr(cfg, "channels", None)
    if not channels_cfg:
        return JSONResponse(content={"channels": []})
    result = []
    for name in ("telegram", "slack", "discord"):
        ch_cfg = getattr(channels_cfg, name, None)
        if ch_cfg is None:
            continue
        accounts = getattr(ch_cfg, "accounts", {}) or {}
        has_token = any(
            bool(
                getattr(acct, "token", "") or
                getattr(acct, "bot_token", "") or
                getattr(acct, "app_token", "")
            )
            for acct in accounts.values()
        )
        result.append({
            "id": name,
            "enabled": getattr(ch_cfg, "enabled", False),
            "connected": False,
            "has_token": has_token,
        })
    webchat_cfg = getattr(channels_cfg, "webchat", None)
    result.append({
        "id": "webchat",
        "enabled": True if webchat_cfg is None else bool(getattr(webchat_cfg, "enabled", True)),
        "connected": False,
        "has_token": True,
    })
    try:
        from teaming24.gateway import gateway as gateway_module
        gw = getattr(gateway_module, "_gateway", None)
        adapters = gw.channel_manager._adapters if gw is not None else {}
        for ch in result:
            prefix = f"{ch['id']}:"
            channel_adapters = [
                adapter for key, adapter in adapters.items()
                if key.startswith(prefix)
            ]
            if channel_adapters:
                ch["connected"] = any(
                    bool(getattr(adapter, "_running", False))
                    for adapter in channel_adapters
                )
    except Exception as exc:
        logger.warning("Failed to read runtime channel adapter state: %s", exc, exc_info=True)
    return JSONResponse(content={"channels": result})


class ChannelConfigRequest(BaseModel):
    channel: str
    enabled: bool = False
    token: str = ""
    bot_token: str = ""
    app_token: str = ""


@router.post("/api/channels/config")
async def update_channel_config(req: ChannelConfigRequest, request: Request):
    guard = _require_local(request)
    if guard:
        return guard
    if req.channel not in ("telegram", "slack", "discord"):
        return JSONResponse(status_code=400, content={"error": "unsupported channel"})
    yaml_path = Path(UNIFIED_CONFIG_FILE)
    try:
        raw = yaml.safe_load(yaml_path.read_text()) or {}
    except Exception as exc:
        logger.warning(
            "Failed to parse %s while updating channel config; resetting to empty dict: %s",
            yaml_path,
            exc,
            exc_info=True,
        )
        raw = {}
    channels_section = raw.setdefault("channels", {})
    ch = channels_section.setdefault(req.channel, {})
    ch["enabled"] = req.enabled
    acct = ch.setdefault("accounts", {}).setdefault("default", {})
    if req.token:
        acct["token"] = req.token
    if req.bot_token:
        acct["bot_token"] = req.bot_token
    if req.app_token:
        acct["app_token"] = req.app_token
    yaml_path.write_text(yaml.dump(raw, default_flow_style=False, allow_unicode=True))
    return JSONResponse(content={"status": "saved", "channel": req.channel})


# ---------------------------------------------------------------------------
# Framework backend
# ---------------------------------------------------------------------------

@router.get("/api/framework")
async def get_framework_config():
    fw = getattr(config, "framework", None)
    backend = getattr(fw, "backend", "native") if fw else "native"
    return JSONResponse(content={"backend": backend})


class FrameworkConfigRequest(BaseModel):
    backend: str = "native"


@router.post("/api/framework")
async def set_framework_config(req: FrameworkConfigRequest, request: Request):
    guard = _require_local(request)
    if guard:
        return guard
    if req.backend not in ("native", "crewai"):
        return JSONResponse(status_code=400, content={"error": "backend must be 'native' or 'crewai'"})
    yaml_path = Path(UNIFIED_CONFIG_FILE)
    try:
        raw = yaml.safe_load(yaml_path.read_text()) or {}
    except Exception as exc:
        logger.warning(
            "Failed to parse %s while updating framework config; resetting to empty dict: %s",
            yaml_path,
            exc,
            exc_info=True,
        )
        raw = {}
    raw.setdefault("framework", {})["backend"] = req.backend
    yaml_path.write_text(yaml.dump(raw, default_flow_style=False, allow_unicode=True))
    return JSONResponse(content={"status": "saved", "backend": req.backend})


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

@router.get("/api/memory/status")
async def memory_status(agent_id: str | None = None, session_id: str | None = None):
    """Memory status. When session_id is provided, shows that session's independent memory (9.4k/200k)."""
    try:
        from teaming24.memory import MemoryManager

        mgr = MemoryManager()
        effective_agent_id = str(agent_id or "").strip() or ORGANIZER_ID
        status = mgr.get_usage_status(effective_agent_id, session_id=session_id)
        return JSONResponse(content=status.to_dict())
    except Exception as exc:
        logger.warning("Memory status query failed agent_id=%r: %s", agent_id, exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": str(exc), "agent_id": str(agent_id or "").strip() or ORGANIZER_ID},
        )

class MemorySearchRequest(BaseModel):
    query: str
    top_k: int = 10
    agent_id: str | None = None


@router.post("/api/memory/search")
async def memory_search(req: MemorySearchRequest):
    try:
        from teaming24.memory import MemoryManager
        mgr = MemoryManager()
        effective_agent_id = str(req.agent_id or "").strip() or ORGANIZER_ID
        top_k = max(1, min(int(req.top_k), int(getattr(config.memory, "api_search_top_k_max", 50) or 50)))
        results = mgr.search(req.query, agent_id=effective_agent_id, top_k=top_k)
        return JSONResponse(content={"results": [
            {"id": e.id, "content": e.content, "tags": e.tags, "source": e.source,
             "score": e.score, "agent_id": e.agent_id, "created_at": format_timestamp(e.created_at)}
            for e in results
        ]})
    except Exception as e:
        logger.warning("Memory search failed query=%r top_k=%s: %s", req.query, req.top_k, e, exc_info=True)
        return JSONResponse(status_code=500, content={"status": "error", "results": [], "error": str(e)})


@router.get("/api/memory/recent")
async def memory_recent(limit: int = 20, agent_id: str | None = None):
    try:
        from teaming24.memory import MemoryManager

        mgr = MemoryManager()
        effective_agent_id = str(agent_id or "").strip() or ORGANIZER_ID
        normalized_limit = max(1, min(int(limit), int(getattr(config.memory, "api_recent_limit_max", 100) or 100)))
        entries = mgr.list_for_agent(effective_agent_id, limit=normalized_limit)
        return JSONResponse(content={"entries": [
            {"id": e.id, "content": e.content, "tags": e.tags, "source": e.source,
             "score": 0.0, "agent_id": e.agent_id, "created_at": format_timestamp(e.created_at)}
            for e in entries
        ]})
    except Exception as e:
        logger.warning("Memory recent query failed limit=%s: %s", limit, e, exc_info=True)
        return JSONResponse(content={"entries": [], "error": str(e)})


class MemorySaveRequest(BaseModel):
    content: str
    tags: list[str] | None = None
    source: str = "manual"
    agent_id: str | None = None


@router.post("/api/memory/save")
async def memory_save(req: MemorySaveRequest):
    try:
        from teaming24.memory import MemoryManager
        mgr = MemoryManager()
        effective_agent_id = str(req.agent_id or "").strip() or ORGANIZER_ID
        content = str(req.content or "").strip()
        if not content:
            return JSONResponse(status_code=400, content={"error": "content is required"})
        mem_id = mgr.save(agent_id=effective_agent_id, content=content, tags=req.tags or [], source=req.source)
        return JSONResponse(content={"id": mem_id, "status": "saved"})
    except Exception as e:
        logger.warning("Memory save failed source=%s: %s", req.source, e, exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})
