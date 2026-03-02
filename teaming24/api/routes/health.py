"""
Health, frontend config, and documentation endpoints.

This module provides the root API entry point, liveness check, frontend
configuration blob, config reload, and markdown documentation serving.

Endpoints
---------
- GET /api — Root; returns API name, version, status
- GET /api/health — Liveness check; returns {"status": "healthy"}
- GET /api/config — Full frontend config (server, discovery, connection, etc.)
- POST /api/config/reload — Reload config from YAML + DB overrides
- GET /api/docs — List available markdown docs
- GET /api/docs/{filename:path} — Serve a markdown document (e.g., README.md)

Dependencies
------------
Uses ``teaming24.api.deps``: ``config``, ``logger``, ``BASE_DIR``,
``DOCS_DIR``, ``UNIFIED_CONFIG_FILE``. No state.py usage.

Extending
---------
To add a new endpoint, define a route with ``@router.get(...)`` or
``@router.post(...)`` and ensure the path is under ``/api``.
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from teaming24.api.deps import DOCS_DIR, config, logger

router = APIRouter()


# ---------------------------------------------------------------------------
# Health / root
# ---------------------------------------------------------------------------

@router.get("/api")
async def api_root():
    return {"name": "Teaming24 API", "version": "0.1.0", "status": "running"}


@router.get("/api/health")
async def health_check():
    return {"status": "healthy"}


# ---------------------------------------------------------------------------
# Frontend config
# ---------------------------------------------------------------------------

class FrontendConfig(BaseModel):
    server_host: str
    server_port: int
    api_base_url: str
    api_prefix: str
    local_node_an_id: str
    local_node_name: str
    local_node_wallet_address: str
    local_node_host: str
    local_node_port: int
    local_node_description: str
    local_node_capability: str
    local_node_region: str
    discovery_broadcast_port: int
    discovery_broadcast_interval: int
    discovery_node_expiry_seconds: int
    discovery_max_lan_nodes: int
    discovery_max_wan_nodes: int
    connection_timeout: int
    connection_retry_attempts: int
    connection_keepalive_interval: int
    subscription_max_queue_size: int
    subscription_keepalive_interval: int
    database_path: str
    marketplace_url: str
    marketplace_auto_rejoin: bool
    agentanet_central_url: str
    auto_online: bool
    agentanet_local_host: str
    agentanet_local_port: int
    agentanet_local_name: str
    full_config: dict | None = None
    config_version: float | None = None


@router.get("/api/config", response_model=FrontendConfig)
async def get_frontend_config():
    """Return the full frontend configuration blob."""
    return FrontendConfig(
        server_host=config.server.host,
        server_port=config.server.port,
        api_base_url=config.api.base_url,
        api_prefix=config.api.prefix,
        local_node_an_id=config.local_node.an_id,
        local_node_name=config.local_node.name,
        local_node_wallet_address=config.local_node.wallet_address,
        local_node_host=config.local_node.host,
        local_node_port=config.local_node.port,
        local_node_description=config.local_node.description,
        local_node_capability=config.local_node.capability,
        local_node_region=config.local_node.region,
        discovery_broadcast_port=config.discovery.broadcast_port,
        discovery_broadcast_interval=config.discovery.broadcast_interval,
        discovery_node_expiry_seconds=config.discovery.node_expiry_seconds,
        discovery_max_lan_nodes=config.discovery.max_lan_nodes,
        discovery_max_wan_nodes=config.discovery.max_wan_nodes,
        connection_timeout=config.connection.timeout,
        connection_retry_attempts=config.connection.retry_attempts,
        connection_keepalive_interval=config.connection.keepalive_interval,
        subscription_max_queue_size=config.subscription.max_queue_size,
        subscription_keepalive_interval=config.subscription.keepalive_interval,
        database_path=config.database.path,
        marketplace_url=config.marketplace.url,
        marketplace_auto_rejoin=config.marketplace.auto_rejoin,
        agentanet_central_url=config.agentanet_central.url,
        auto_online=config.network.auto_online,
        agentanet_local_host=config.local_node.host,
        agentanet_local_port=config.local_node.port,
        agentanet_local_name=config.local_node.name,
        full_config=config.to_dict(),
        config_version=time.time(),
    )


@router.post("/api/config/reload")
async def reload_config():
    """Reload configuration from YAML + DB overrides."""
    try:
        import teaming24.api.deps as _deps
        from teaming24.config import load_config
        _deps.config = load_config()
        version = time.time()
        logger.info(f"Configuration reloaded (version={version})")
        return {"status": "ok", "config_version": version}
    except Exception as e:
        logger.error(f"Failed to reload config: {e}")
        return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------

@router.get("/api/docs/{filename:path}")
async def get_docs(filename: str):
    if not filename.endswith('.md'):
        raise HTTPException(status_code=400, detail="Only .md files are allowed")
    safe_filename = Path(filename).name
    file_path = DOCS_DIR / safe_filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Document not found: {safe_filename}")
    try:
        content = file_path.read_text(encoding="utf-8")
        return PlainTextResponse(content, media_type="text/markdown")
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to read document") from e


@router.get("/api/docs")
async def list_docs():
    if not DOCS_DIR.exists():
        return {"docs": []}
    docs = [
        {"id": f.stem, "title": f.stem.replace("-", " ").title(), "path": f.name}
        for f in sorted(DOCS_DIR.glob("*.md"))
        if f.name != "README.md"
    ]
    readme = DOCS_DIR / "README.md"
    if readme.exists():
        docs.insert(0, {"id": "readme", "title": "Overview", "path": "README.md"})
    return {"docs": docs}
