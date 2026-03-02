"""
WebSocket protocol server for Teaming24.

Implements a typed req/res/event wire protocol for FastAPI.

Wire frames (all JSON):
  Request:  {"type": "req",   "id": "<uuid>", "method": "<method>", "params": {...}}
  Response: {"type": "res",   "id": "<uuid>", "ok": true/false,     "payload": {...}}
  Event:    {"type": "event", "event": "<name>", "payload": {...},   "seq": <int>}

Supported methods:
  connect       — handshake, returns agent/task snapshot
  send          — submit a chat message (creates task)
  cancel_task   — cancel a running task
  subscribe     — subscribe to scoped events
  status        — health check

This module provides:
  - WSClient: wraps a single FastAPI WebSocket connection
  - WSHub: manages all connected clients and broadcasts events
  - mount_websocket(app): adds the /ws endpoint to a FastAPI app
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from teaming24.utils.ids import random_hex
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Wire protocol types
# ---------------------------------------------------------------------------

@dataclass
class WSRequest:
    type: str  # always "req"
    id: str
    method: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class WSResponse:
    type: str = "res"
    id: str = ""
    ok: bool = True
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"type": self.type, "id": self.id, "ok": self.ok, "payload": self.payload}


@dataclass
class WSEvent:
    type: str = "event"
    event: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    seq: int = 0

    def to_dict(self) -> dict:
        return {"type": self.type, "event": self.event, "payload": self.payload, "seq": self.seq}


# ---------------------------------------------------------------------------
# WSClient — wraps one WebSocket connection
# ---------------------------------------------------------------------------

class WSClient:
    """Represents a single WebSocket client connection."""

    def __init__(self, ws: WebSocket, client_id: str = ""):
        self.ws = ws
        self.id = client_id or random_hex(12)
        self.connected_at = time.time()
        self.subscriptions: set[str] = set()  # event names this client wants
        self._send_lock = asyncio.Lock()

    async def send_response(self, req_id: str, ok: bool = True, payload: dict = None) -> None:
        resp = WSResponse(id=req_id, ok=ok, payload=payload or {})
        await self._send_json(resp.to_dict())

    async def send_event(self, event: str, payload: dict, seq: int = 0) -> None:
        evt = WSEvent(event=event, payload=payload, seq=seq)
        await self._send_json(evt.to_dict())

    async def _send_json(self, data: dict) -> None:
        async with self._send_lock:
            try:
                if self.ws.client_state == WebSocketState.CONNECTED:
                    await self.ws.send_json(data)
            except Exception as exc:
                logger.debug("[WS] send failed client=%s: %s", self.id, exc)

    async def recv_frame(self) -> WSRequest | None:
        """Read one request frame from the client. Returns None on disconnect."""
        try:
            raw = await self.ws.receive_text()
            data = json.loads(raw)
            if data.get("type") != "req":
                return None
            return WSRequest(
                type="req",
                id=data.get("id", ""),
                method=data.get("method", ""),
                params=data.get("params", {}),
            )
        except (WebSocketDisconnect, RuntimeError):
            logger.debug("WebSocket disconnected or runtime error during recv")
            return None
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON frame received: {e}")
            return None


# ---------------------------------------------------------------------------
# WSHub — manages all clients, broadcasts events
# ---------------------------------------------------------------------------

# Method handler type: async fn(client, params) -> payload dict
MethodHandler = Callable[[WSClient, dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]


class WSHub:
    """Central hub for all WebSocket connections."""

    def __init__(self):
        self._clients: dict[str, WSClient] = {}
        self._handlers: dict[str, MethodHandler] = {}
        self._seq = 0
        self._lock = asyncio.Lock()

        # Register built-in methods
        self.register_method("status", self._handle_status)
        self.register_method("subscribe", self._handle_subscribe)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def register_method(self, method: str, handler: MethodHandler) -> None:
        self._handlers[method] = handler

    # ----- Connection lifecycle -------------------------------------------

    async def handle_connection(self, ws: WebSocket) -> None:
        """Full lifecycle for one WebSocket connection."""
        await ws.accept()
        client = WSClient(ws)

        # First frame must be "connect"
        frame = await client.recv_frame()
        if frame is None or frame.method != "connect":
            await ws.close(code=1002, reason="First frame must be connect")
            return

        connect_payload = await self._handle_connect(client, frame.params)
        await client.send_response(frame.id, ok=True, payload=connect_payload)

        async with self._lock:
            self._clients[client.id] = client
        logger.info("[WS] client connected: %s  total=%d", client.id, len(self._clients))

        try:
            while True:
                frame = await client.recv_frame()
                if frame is None:
                    break
                await self._dispatch(client, frame)
        except WebSocketDisconnect:
            logger.debug("WebSocket client disconnected")
        finally:
            async with self._lock:
                self._clients.pop(client.id, None)
            logger.info("[WS] client disconnected: %s  total=%d", client.id, len(self._clients))

    async def _dispatch(self, client: WSClient, req: WSRequest) -> None:
        handler = self._handlers.get(req.method)
        if handler is None:
            await client.send_response(req.id, ok=False, payload={"error": f"unknown method: {req.method}"})
            return
        try:
            payload = await handler(client, req.params)
            await client.send_response(req.id, ok=True, payload=payload or {})
        except Exception as exc:
            logger.error("[WS] handler error method=%s: %s", req.method, exc)
            await client.send_response(req.id, ok=False, payload={"error": str(exc)})

    # ----- Broadcasting ---------------------------------------------------

    async def broadcast(self, event: str, payload: dict) -> None:
        """Push an event to all connected clients (with optional subscription filter)."""
        self._seq += 1
        seq = self._seq

        async with self._lock:
            clients = list(self._clients.values())

        for client in clients:
            if client.subscriptions and event not in client.subscriptions:
                continue
            try:
                await client.send_event(event, payload, seq)
            except Exception as e:
                logger.debug(f"Failed to send to WebSocket client: {e}")
                pass

    # ----- Built-in handlers ----------------------------------------------

    async def _handle_connect(self, client: WSClient, params: dict) -> dict:
        """Handshake handler. Returns initial snapshot."""
        return {
            "client_id": client.id,
            "server_time": time.time(),
            "protocol_version": "1.0",
        }

    async def _handle_status(self, client: WSClient, params: dict) -> dict:
        return {
            "connected_clients": len(self._clients),
            "uptime_seconds": time.time() - client.connected_at,
        }

    async def _handle_subscribe(self, client: WSClient, params: dict) -> dict:
        events = params.get("events", [])
        if isinstance(events, list):
            client.subscriptions.update(events)
        return {"subscribed": sorted(client.subscriptions)}


# ---------------------------------------------------------------------------
# Global hub singleton
# ---------------------------------------------------------------------------

_hub: WSHub | None = None


def get_ws_hub() -> WSHub:
    """Return the global WSHub singleton (created on first call)."""
    global _hub
    if _hub is None:
        _hub = WSHub()
    return _hub


def mount_websocket(app) -> None:
    """Add the ``/ws`` WebSocket endpoint to a FastAPI application."""
    hub = get_ws_hub()

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await hub.handle_connection(ws)
