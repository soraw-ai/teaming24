"""
In-memory state registries shared across route modules.

This module holds all mutable singletons and cross-request state used
by the API. Centralizing state here avoids circular imports (routes
import from ``state`` and ``deps``, not from each other) and provides
a single place to inspect and manage shared mutable data.

Role
----
- **Centralized mutable state**: All in-memory registries, caches,
  and cross-request tracking live here.
- **No circular imports**: State imports only from ``deps`` and
  communication modules; routes import from ``state`` and ``deps``.

State registries
----------------
- **subscription_manager**: SSE subscription and broadcast (SubscriptionManager).
- **network_manager**, **local_crew_singleton**: Network and agent singletons.
- **inbound_connected_since**, **peer_failure_counts**: Peer connection tracking.
- **sandboxes**, **sandbox_events**, **sandbox_stream_queue**, **sandbox_screenshots**,
  **sandbox_list_subscribers**: Sandbox runtime state.
- **openhands_sandbox_id**: OpenHands sandbox ID.
- **chat_event_buffer**, **chat_event_buffer_lock**: SSE reconnection replay buffer.
- **shutdown_event**: Asyncio event set during lifespan teardown to break SSE loops.
- **demo_processes**: Demo process tracking.
- **wallet_config**, **wallet_ledger**, **mock_balance**: Wallet state.
- **approval_requests**, **approval_lock**: Human-in-the-loop approval queue.
- **task_budgets**: Per-task budget/spent tracker for auto-approval.

How to add new state
--------------------
1. Add the variable at module level with a clear name.
2. If it is shared across concurrent requests, add a lock and document
   thread-safety requirements.
3. Add a brief description to this docstring.

Thread safety notes
-------------------
- **chat_event_buffer**: Protected by ``chat_event_buffer_lock``. Acquire
  the lock before read/write.
- **approval_requests**: Protected by ``approval_lock``. Acquire before
  access.
- **subscription_manager**: Uses internal locking for broadcast.
- Other dicts (sandboxes, demo_processes, etc.): No built-in locking;
  access patterns should be coordinated by the route/service layer.
"""
from __future__ import annotations

import asyncio
import queue
import threading
from typing import Any

from teaming24.api.deps import config
from teaming24.communication.manager import NetworkManager
from teaming24.communication.subscription import SubscriptionManager

# ---------------------------------------------------------------------------
# Subscription / broadcast
# ---------------------------------------------------------------------------
subscription_manager = SubscriptionManager(config=config.subscription)

# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
network_manager: NetworkManager | None = None
local_crew_singleton: Any | None = None

# Peer tracking
inbound_connected_since: dict[str, float] = {}
peer_failure_counts: dict[str, int] = {}

# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------
sandboxes: dict = {}
sandbox_events: dict = {}
sandbox_stream_queue: queue.Queue = queue.Queue()
sandbox_screenshots: dict = {}
sandbox_list_subscribers: list = []

# OpenHands runtime tracking
openhands_sandbox_id: str | None = None

# ---------------------------------------------------------------------------
# Chat SSE event buffer (for reconnection replay)
# ---------------------------------------------------------------------------
chat_event_buffer: dict = {}
chat_event_buffer_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Global shutdown event — set during lifespan teardown to break SSE loops
# ---------------------------------------------------------------------------
shutdown_event = asyncio.Event()

# ---------------------------------------------------------------------------
# Demo processes
# ---------------------------------------------------------------------------
demo_processes: dict = {}

# ---------------------------------------------------------------------------
# Wallet
# ---------------------------------------------------------------------------
wallet_config: dict = {
    "address": "",
    "is_configured": False,
    "network": "base-sepolia",
    "_private_key": None,
}


def default_wallet_network() -> str:
    """Resolve the canonical default wallet network for local/mock usage."""
    try:
        configured = str(getattr(config.payment.network, "name", "") or "").strip().lower()
    except Exception:
        configured = ""
    if configured:
        return configured
    return "base-sepolia"


def default_mock_balance() -> float:
    """Resolve the default local mock balance.

    In development mode, always provide at least 100 ETH locally.
    """
    try:
        configured = float(getattr(config.payment.mock, "initial_balance", 100.0) or 100.0)
    except Exception:
        configured = 100.0
    try:
        if bool(getattr(config.system.dev_mode, "enabled", False)):
            return max(100.0, configured)
    except Exception:
        pass
    return configured


wallet_ledger: list = []
mock_balance: float = default_mock_balance()
wallet_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Human-in-the-loop approval
# ---------------------------------------------------------------------------
approval_requests: dict = {}
approval_lock = threading.Lock()
# task_id -> {"budget": float, "spent": float}
task_budgets: dict = {}
