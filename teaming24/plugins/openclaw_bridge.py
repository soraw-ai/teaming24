"""
OpenClaw ↔ Teaming24 Deep Integration Bridge.

Connects to the OpenClaw Gateway (WebSocket) as an operator and maintains
a persistent connection.  Session messages are pushed via the HTTP
``/tools/invoke`` endpoint (sessions_send tool).

NOTE: Tool registration for OpenClaw agents is done via the TypeScript plugin
(packages/openclaw-plugin/), NOT through this bridge.  This bridge's sole
responsibility is:

  1. Maintain a reconnecting WebSocket operator connection to the Gateway.
  2. Forward task-step progress to OpenClaw sessions via the HTTP
     /tools/invoke → sessions_send tool.
  3. Handle the connect.challenge → connect → hello-ok handshake.

OpenClaw WebSocket Protocol v3 (verified against source):
  - Server sends: {type:"event", event:"connect.challenge", payload:{nonce:"…"}}
  - Client sends: {type:"req", id:"…", method:"connect", params:{…}}
  - Server responds: {type:"res", id:"…", ok:true, payload:{type:"hello-ok",…}}

Connect params structure (ConnectParamsSchema):
  {
    minProtocol: int,
    maxProtocol: int,
    client: {id, displayName?, version, platform, mode},
    role: "operator",
    scopes: ["operator.admin"],
    auth?: {token?, password?},
  }

Session messaging uses HTTP /tools/invoke (not WS "send" which targets
external channels and requires a channel-specific "to" address):
  POST /tools/invoke  {"tool":"sessions_send","args":{"sessionKey":"…","message":"…"}}

Architecture::

    Teaming24 server
        └── OpenClawBridge
                ├── WebSocket operator connection (keep-alive, event subscription)
                └── HTTP /tools/invoke → sessions_send → session progress notes

    TypeScript Plugin (packages/openclaw-plugin/)
        └── registers 4 tools on the OpenClaw agent:
                teaming24_execute / teaming24_delegate / teaming24_wallet / teaming24_network
                └── each tool calls Teaming24 REST API (/api/openclaw/*)

Configuration (teaming24.yaml)::

    extensions:
      openclaw:
        enabled: true
        gateway_url: "ws://127.0.0.1:18789"
        token: ""                # OpenClaw gateway token (blank = loopback/none)
        reconnect_delay: 5       # seconds between reconnect attempts
        progress_in_session: true
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlparse, urlunparse

from teaming24.utils.ids import uuid_str

logger = logging.getLogger(__name__)


def _read_oc_config() -> dict:
    """Return the openclaw extension config dict (safe fallback to {})."""
    try:
        from teaming24.config import get_config
        cfg = get_config()
        ext = cfg.extensions if isinstance(cfg.extensions, dict) else {}
        return ext.get("openclaw", {})
    except Exception as exc:
        logger.warning(
            "[OpenClawBridge] Failed to read openclaw config, using defaults: %s",
            exc,
            exc_info=True,
        )
        return {}


def _ws_to_http(ws_url: str) -> str:
    """Convert a WebSocket URL to an HTTP URL for the REST API."""
    parsed = urlparse(ws_url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    return urlunparse((scheme, parsed.netloc, "", "", "", ""))


# ── Optional deps ─────────────────────────────────────────────────────────────

try:
    import websockets
    import websockets.exceptions as _ws_exc
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    websockets = None           # type: ignore[assignment]
    _ws_exc = None              # type: ignore[assignment]
    logger.debug("websockets package unavailable for OpenClaw bridge")

try:
    import urllib.request as _urllib_req
    URLLIB_AVAILABLE = True
except ImportError:
    URLLIB_AVAILABLE = False
    logger.debug("urllib.request unavailable for OpenClaw bridge")


# ── Bridge ────────────────────────────────────────────────────────────────────


class OpenClawBridge:
    """
    Bidirectional bridge: maintains a WebSocket operator connection to the
    OpenClaw Gateway so Teaming24 can receive events and send progress
    messages into sessions via HTTP /tools/invoke.

    Lifecycle managed by openclaw_plugin.py via start() / stop().
    """

    def __init__(
        self,
        gateway_url: str = "ws://127.0.0.1:18789",
        reconnect_delay: float = 5.0,
        progress_in_session: bool = True,
        token: str = "",
    ) -> None:
        self._gateway_url = gateway_url
        self._http_base = _ws_to_http(gateway_url)
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = 60.0
        self._progress_in_session = progress_in_session
        self._token = token

        self._ws: Any = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        # Stable client ID for this bridge instance.
        self._client_id = uuid_str()
        # Whether we are currently authenticated on the WS connection.
        self._authenticated = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._main_loop = asyncio.get_running_loop()
        self._running = True
        asyncio.create_task(self._gateway_loop(), name="openclaw-bridge-gateway")
        logger.info("[OpenClawBridge] Started — gateway=%s", self._gateway_url)

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception as e:
                logger.debug("[OpenClawBridge] WS close error (ignored): %s", e)
        logger.info("[OpenClawBridge] Stopped")

    # ── Gateway connection loop ──────────────────────────────────────────────

    async def _gateway_loop(self) -> None:
        delay = self._reconnect_delay
        while self._running:
            try:
                await self._connect_and_run()
                delay = self._reconnect_delay   # reset on clean disconnect
            except Exception as e:
                if not self._running:
                    break
                logger.warning(
                    "[OpenClawBridge] Gateway disconnected (%s: %s) — retry in %.0fs",
                    type(e).__name__, e, delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, self._max_reconnect_delay)

    async def _connect_and_run(self) -> None:
        if not WEBSOCKETS_AVAILABLE:
            logger.error(
                "[OpenClawBridge] 'websockets' package not installed — "
                "run: pip install websockets>=12"
            )
            await asyncio.sleep(30)
            return

        self._authenticated = False
        logger.info("[OpenClawBridge] Connecting to %s", self._gateway_url)
        async with websockets.connect(          # type: ignore[attr-defined]
            self._gateway_url,
            ping_interval=30,
            ping_timeout=10,
            open_timeout=10,
        ) as ws:
            self._ws = ws
            logger.info("[OpenClawBridge] Connected to OpenClaw Gateway")
            # OpenClaw WS protocol:
            #   1. Server sends: event "connect.challenge" with payload.nonce
            #   2. Client sends: req method "connect" with ConnectParams
            #   3. Server responds: res with payload.type == "hello-ok"
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    await self._handle_message(ws, msg)
                except json.JSONDecodeError:
                    logger.debug("[OpenClawBridge] Ignoring non-JSON WS frame: %r", raw[:200])
                except Exception as e:
                    logger.warning("[OpenClawBridge] Message handling error: %s", e)
        self._ws = None
        self._authenticated = False

    async def _send_connect(self, ws: Any, nonce: str) -> None:
        """Send operator connect request after receiving the gateway challenge."""
        token = self._token or _read_oc_config().get("token", "")
        # Build auth only if we have a token (loopback connections skip auth).
        auth = {"token": token} if token else None

        connect_req: dict = {
            "type": "req",
            "id": "openclaw-bridge-connect",
            "method": "connect",
            "params": {
                "minProtocol": 3,
                "maxProtocol": 3,
                "client": {
                    "id": "teaming24-bridge",
                    "displayName": "Teaming24 Bridge",
                    "version": "1.0.0",
                    "platform": "teaming24",
                    "mode": "backend",
                },
                "role": "operator",
                "scopes": ["operator.admin"],
            },
        }
        if auth:
            connect_req["params"]["auth"] = auth

        await ws.send(json.dumps(connect_req))
        logger.debug("[OpenClawBridge] Connect request sent (role=operator, nonce=%s…)", nonce[:8])

    # ── Incoming message dispatch ────────────────────────────────────────────

    async def _handle_message(self, ws: Any, msg: dict) -> None:
        msg_type = msg.get("type", "")
        msg_id = msg.get("id")

        # Event frames
        if msg_type == "event":
            event = msg.get("event", "")

            # connect.challenge — server wants us to authenticate
            if event == "connect.challenge":
                payload = msg.get("payload") or {}
                nonce = payload.get("nonce", "") if isinstance(payload, dict) else ""
                await self._send_connect(ws, nonce)
                return

            # tick / keepalive events (no response needed)
            if event in ("tick", "shutdown"):
                return

            logger.debug("[OpenClawBridge] Unhandled event: %r", event)
            return

        # Response frames
        if msg_type == "res":
            ok = msg.get("ok", False)
            payload = msg.get("payload") or {}

            # Response to our connect request
            if msg_id == "openclaw-bridge-connect":
                if ok and isinstance(payload, dict) and payload.get("type") == "hello-ok":
                    self._authenticated = True
                    protocol = payload.get("protocol", "?")
                    server = payload.get("server") or {}
                    logger.info(
                        "[OpenClawBridge] Authenticated as operator ✓ "
                        "(protocol=%s, server=%s)",
                        protocol, server.get("version", "?"),
                    )
                else:
                    err = payload.get("message", "unknown") if isinstance(payload, dict) else str(payload)
                    logger.warning("[OpenClawBridge] Auth failed: %s", err)
                return

            # Other response frames (e.g. ping responses) — silently ignore
            return

        # Request frames from the gateway (e.g. ping/keepalive)
        if msg_type == "req":
            method = msg.get("method", "")
            if method in ("ping", "keepalive"):
                await ws.send(json.dumps({
                    "type": "res", "id": msg_id, "ok": True, "payload": "pong"
                }))
                return

            logger.debug("[OpenClawBridge] Unhandled req: method=%r", method)
            return

        logger.debug("[OpenClawBridge] Unknown frame type: %r", msg_type)

    # ── Public API: send progress to an OpenClaw session ─────────────────────

    async def send_session_message(
        self,
        session_key: str,
        content: str,
        ephemeral: bool = False,   # reserved for future use
    ) -> None:
        """
        Push a progress/info message into an OpenClaw session.

        Uses the OpenClaw HTTP /tools/invoke endpoint with the sessions_send
        tool.  The WS connection is kept for event subscriptions; session
        messaging goes over HTTP to avoid protocol mismatches (the WS "send"
        method targets external channels and requires a channel-specific "to"
        address, which Teaming24 does not have).
        """
        if not session_key or not content:
            return

        loop = self._main_loop
        if not (loop and loop.is_running()):
            return

        # Run the blocking HTTP call in an executor so we don't block the event loop.
        await loop.run_in_executor(None, self._http_sessions_send, session_key, content)

    def _http_sessions_send(self, session_key: str, content: str) -> None:
        """Synchronous HTTP POST to /tools/invoke (runs in executor thread)."""
        token = self._token or _read_oc_config().get("token", "")
        url = f"{self._http_base}/tools/invoke"
        body = json.dumps({
            "tool": "sessions_send",
            "args": {
                "sessionKey": session_key,
                "message": content,
                "timeoutSeconds": 0,    # fire-and-forget
            },
        }).encode()
        headers: dict = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            req = _urllib_req.Request(url, data=body, headers=headers, method="POST")
            with _urllib_req.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode()
            data = json.loads(raw)
            if not data.get("ok"):
                err = data.get("error") or {}
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                logger.debug("[OpenClawBridge] sessions_send failed: %s", msg)
        except Exception as e:
            logger.debug("[OpenClawBridge] Session message HTTP call failed: %s", e)
