"""
OpenClaw Plugin — lifecycle manager for the OpenClaw bridge.

Teaming24 is modular: runs standalone by default (extensions.openclaw.enabled: false).
When enabled (extensions.openclaw.enabled: true in teaming24.yaml):
  - Starts the OpenClawBridge WebSocket operator connection to the Gateway.
  - The TypeScript plugin (packages/openclaw-plugin/) registers teaming24_* tools
    on the OpenClaw agent — tool registration is NOT done here.

Bridge responsibilities:
  1. Maintain a reconnecting WebSocket operator connection to the OpenClaw Gateway.
  2. Forward task progress to OpenClaw sessions via HTTP /tools/invoke.
  3. Handle the connect.challenge → connect → hello-ok handshake.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_bridge = None


def get_openclaw_bridge():
    """Return the active OpenClawBridge instance, or None if not running."""
    return _bridge


def _load_oc_config() -> dict:
    """Return the openclaw extension config dict (safe fallback to {})."""
    try:
        from teaming24.config import get_config
        cfg = get_config()
        ext = cfg.extensions if isinstance(cfg.extensions, dict) else {}
        return ext.get("openclaw", {})
    except Exception as exc:
        logger.warning(
            "[OpenClawPlugin] Failed to load openclaw config, using empty defaults: %s",
            exc,
            exc_info=True,
        )
        return {}


async def setup_openclaw_plugin() -> None:
    """Start the OpenClaw bridge if enabled in config."""
    global _bridge

    oc = _load_oc_config()
    if not oc.get("enabled", False):
        logger.debug("[OpenClawPlugin] Disabled — skipping bridge setup")
        return

    try:
        from teaming24.plugins.openclaw_bridge import OpenClawBridge
    except ImportError as e:
        logger.error("[OpenClawPlugin] Cannot import OpenClawBridge: %s", e)
        return

    gateway_url = oc.get("gateway_url", "ws://127.0.0.1:18789")
    reconnect_delay = float(oc.get("reconnect_delay", 5))
    progress_in_session = bool(oc.get("progress_in_session", True))
    token = str(oc.get("token") or "")

    _bridge = OpenClawBridge(
        gateway_url=gateway_url,
        reconnect_delay=reconnect_delay,
        progress_in_session=progress_in_session,
        token=token,
    )
    await _bridge.start()
    logger.info(
        "[OpenClawPlugin] Bridge started (gateway=%s, session_progress=%s)",
        gateway_url, progress_in_session,
    )


async def teardown_openclaw_plugin() -> None:
    """Stop the OpenClaw bridge gracefully."""
    global _bridge
    if _bridge is not None:
        await _bridge.stop()
        _bridge = None
        logger.info("[OpenClawPlugin] Bridge stopped")
