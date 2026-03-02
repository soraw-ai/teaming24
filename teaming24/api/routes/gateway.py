"""
Gateway API endpoints.

This module exposes the message gateway: status, execute (send a message
through the pipeline), and restart. The router uses prefix ``/api/gateway``.

Endpoints
---------
- GET /api/gateway/status — Gateway status (running, etc.)
- POST /api/gateway/execute — Execute message (body: text, channel, peer_id, agent_id, session_id)
- POST /api/gateway/restart — Stop and restart the gateway

Dependencies
------------
Uses ``teaming24.gateway.get_gateway()`` for all operations.
No deps.py or state.py usage.

Extending
---------
Add new endpoints with ``@router.get(...)`` or ``@router.post(...)``.
Paths are relative to the router prefix, so ``/status`` becomes
``/api/gateway/status``.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/gateway", tags=["gateway"])


@router.get("/status")
async def gateway_status():
    from teaming24.gateway import get_gateway
    return JSONResponse(content=get_gateway().get_status())


@router.post("/execute")
async def gateway_execute(request: Request):
    """Execute a message through the gateway pipeline."""
    from teaming24.gateway import get_gateway
    gw = get_gateway()
    if not gw.is_running:
        return JSONResponse(status_code=503, content={"error": "gateway not running"})
    data = await request.json()
    text = data.get("text", "")
    if not text:
        return JSONResponse(status_code=400, content={"error": "text is required"})
    result = await gw.execute(
        text,
        channel=data.get("channel", "webchat"),
        peer_id=data.get("peer_id", "api"),
        agent_id=data.get("agent_id", ""),
        session_id=data.get("session_id", ""),
    )
    return JSONResponse(content=result)


@router.post("/restart")
async def gateway_restart():
    from teaming24.gateway import get_gateway
    gw = get_gateway()
    await gw.stop()
    await gw.start()
    return JSONResponse(content={"status": "restarted", **gw.get_status()})
