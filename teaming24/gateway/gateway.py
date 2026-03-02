"""
Gateway — the central orchestrator for teaming24.

Responsibilities:
  1. Initialise and manage all channel adapters (via ChannelManager).
  2. Provide a unified ``execute()`` method that handles the full pipeline:
     task creation → payment gate → agent execution → event broadcast.
  3. Bridge channel inbound messages to the agent framework.
  4. Expose lifecycle hooks at every stage.
  5. Register WebSocket RPC methods (``send``, ``gateway_status``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from teaming24.channels.base import InboundMessage
from teaming24.channels.manager import ChannelManager
from teaming24.plugins.hooks import get_hook_registry
from teaming24.session.manager import SessionManager
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class GatewayStats:
    """Runtime counters."""
    started_at: float = 0.0
    total_messages: int = 0
    total_tasks: int = 0
    errors: int = 0
    channels_active: int = 0


class Gateway:
    """Central gateway that connects channels → routing → agents.

    The gateway owns the ``ChannelManager`` and provides the
    ``execute_fn`` callback that ChannelManager calls when a
    routed message needs to be processed by an agent.
    """

    def __init__(self) -> None:
        self._session_manager = SessionManager()
        self._channel_manager = ChannelManager(
            session_manager=self._session_manager,
            execute_fn=self._execute_for_channel,
        )
        self._started = False
        self.stats = GatewayStats()

        # Lazy references — set during start() from the API layer
        self._task_manager = None
        self._subscription_manager = None
        self._ws_hub = None

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(self) -> None:
        """Load bindings and channel adapters from teaming24.yaml."""
        self._channel_manager.configure_from_config()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start all channel adapters and register WS methods."""
        if self._started:
            return
        self.configure()

        # Register WebSocket RPC methods if hub is available
        if self._ws_hub is not None:
            self._ws_hub.register_method("send", self._ws_handle_send)
            self._ws_hub.register_method("gateway_status", self._ws_handle_status)

        await self._channel_manager.start()
        self._started = True
        self.stats.started_at = time.time()
        self.stats.channels_active = sum(
            1
            for adapter in self._channel_manager._adapters.values()
            if getattr(adapter, "_running", False)
        )
        logger.info(
            "[Gateway] started — %d channel adapters active",
            self.stats.channels_active,
        )

    async def stop(self) -> None:
        """Shut down all channel adapters."""
        if not self._started:
            return
        await self._channel_manager.stop()
        self._started = False
        logger.info("[Gateway] stopped")

    @property
    def is_running(self) -> bool:
        return self._started

    # ------------------------------------------------------------------
    # Dependency injection (called by server.py before start)
    # ------------------------------------------------------------------

    def set_task_manager(self, tm) -> None:
        self._task_manager = tm

    def set_subscription_manager(self, sm) -> None:
        self._subscription_manager = sm

    def set_ws_hub(self, hub) -> None:
        self._ws_hub = hub

    # ------------------------------------------------------------------
    # Unified execute  (used by both API endpoints and channels)
    # ------------------------------------------------------------------

    async def execute(
        self,
        text: str,
        *,
        channel: str = "webchat",
        peer_id: str = "api",
        agent_id: str = "",
        session_id: str = "",
        task_id: str = "",
        metadata: dict[str, Any] | None = None,
        skip_payment: bool = False,
    ) -> dict[str, Any]:
        """Full pipeline: route → session → payment → agent → result.

        Returns ``{"task_id": ..., "result": ..., "status": ...}``.
        """
        hooks = get_hook_registry()
        self.stats.total_messages += 1

        # 1. Resolve agent via binding router (if not pre-specified)
        if not agent_id:
            msg = InboundMessage(
                channel=channel,
                peer_id=peer_id,
                text=text,
            )
            agent_id = self._channel_manager._binding_router.route(msg)

        # 2. Resolve session
        if not session_id:
            session = self._session_manager.get_or_create(
                channel=channel,
                peer_id=peer_id,
                agent_id=agent_id,
            )
            session_id = session.id
        self._session_manager.record_message(session_id, "user", text)

        # 3. Create task
        task = None
        if self._task_manager:
            task = self._task_manager.create_task(
                prompt=text,
                metadata={
                    "channel": channel,
                    "peer_id": peer_id,
                    "agent_id": agent_id,
                    "session_id": session_id,
                    **(metadata or {}),
                },
            )
            task_id = task_id or task.id
        if not task_id:
            task_id = f"gw-{int(time.time() * 1000)}"

        self.stats.total_tasks += 1
        await hooks.fire("before_task_execute", task_id, text)

        # 4. Payment gate
        if not skip_payment:
            try:
                approved, payment_info = await self._check_payment(task_id)
                if not approved:
                    if self._task_manager and task:
                        self._task_manager.fail_task(task_id, "Payment required")
                    return {"task_id": task_id, "result": "", "status": "payment_required", "payment": payment_info}
            except Exception as exc:
                logger.warning("[Gateway] payment check error: %s", exc)

        # 5. Start task + Execute via agent framework
        if self._task_manager and task:
            self._task_manager.start_task(task_id)

        result_text = ""
        status = "completed"
        cost = {}
        try:
            result = await self._run_agent(text, task_id)
            result_text = result.get("result", "")
            status = result.get("status", "completed")
            cost = result.get("cost", {})
        except Exception as exc:
            logger.error("[Gateway] agent execution error: %s", exc)
            result_text = f"Error: {exc}"
            status = "failed"
            self.stats.errors += 1
            if self._task_manager and task:
                self._task_manager.fail_task(task_id, str(exc))

        # 6. Complete task on success (skip if agent already set terminal state)
        if status not in ("failed", "error") and self._task_manager and task:
            self._task_manager.complete_task(task_id, result_text)
        elif status == "error" and self._task_manager and task:
            self._task_manager.fail_task(task_id, result_text)

        # 7. Record response in session
        if result_text:
            self._session_manager.record_message(session_id, "assistant", result_text)

        # 8. Fire post-execution hook
        await hooks.fire("after_task_execute", task_id, result_text)

        # 9. Broadcast completion/failure event (format matches AgentEventBridge)
        _is_success = status not in ("failed", "error")
        if self._subscription_manager:
            event_type = "task_completed" if _is_success else "task_failed"
            await self._subscription_manager.broadcast(event_type, {
                "task": {
                    "id": task_id,
                    "status": status,
                    "result": result_text,
                    "cost": cost,
                    "completed_at": time.time(),
                },
            })

        return {
            "task_id": task_id,
            "result": result_text,
            "status": status,
            "cost": cost,
            "session_id": session_id,
        }

    # ------------------------------------------------------------------
    # Channel execute callback
    # ------------------------------------------------------------------

    async def _execute_for_channel(self, session_id: str, agent_id: str, text: str) -> str:
        """Callback provided to ChannelManager.

        The ChannelManager already handled routing, session creation, and
        message recording, so we skip those steps and only run
        payment → agent execution → broadcast.
        """
        hooks = get_hook_registry()
        self.stats.total_messages += 1

        # Create task
        task_id = f"gw-{int(time.time() * 1000)}"
        task = None
        if self._task_manager:
            task = self._task_manager.create_task(
                prompt=text,
                metadata={"channel": "external", "agent_id": agent_id, "session_id": session_id},
            )
            task_id = task.id

        self.stats.total_tasks += 1
        await hooks.fire("before_task_execute", task_id, text)

        # Payment gate
        try:
            approved, payment_info = await self._check_payment(task_id)
            if not approved:
                if self._task_manager and task:
                    self._task_manager.fail_task(task_id, "Payment required")
                return f"Payment required to process this request. {payment_info.get('message', '')}"
        except Exception as e:
            logger.warning("Payment gate check failed: %s", e, exc_info=True)

        # Start task + Agent execution
        if self._task_manager and task:
            self._task_manager.start_task(task_id)

        result_text = ""
        status = "completed"
        try:
            result = await self._run_agent(text, task_id)
            result_text = result.get("result", "")
            status = result.get("status", "completed")
        except Exception as exc:
            logger.error("[Gateway] channel execution error: %s", exc, exc_info=True)
            result_text = f"Error: {exc}"
            status = "failed"
            self.stats.errors += 1
            if self._task_manager and task:
                self._task_manager.fail_task(task_id, str(exc))

        if status not in ("failed", "error") and self._task_manager and task:
            self._task_manager.complete_task(task_id, result_text)
        elif status == "error" and self._task_manager and task:
            self._task_manager.fail_task(task_id, result_text)

        await hooks.fire("after_task_execute", task_id, result_text)

        _is_success = status not in ("failed", "error")
        if self._subscription_manager:
            event_type = "task_completed" if _is_success else "task_failed"
            await self._subscription_manager.broadcast(event_type, {
                "task": {
                    "id": task_id,
                    "status": status,
                    "result": result_text,
                    "completed_at": time.time(),
                },
            })

        return result_text

    # ------------------------------------------------------------------
    # Agent execution
    # ------------------------------------------------------------------

    async def _run_agent(self, prompt: str, task_id: str) -> dict[str, Any]:
        """Execute a prompt via the LocalCrew agent framework."""
        from teaming24.agent.core import create_local_crew, get_local_crew_singleton
        from teaming24.api.server import (
            _build_agentic_node_workforce_pool,
            get_agent_runtime_settings,
            get_network_manager,
        )

        crew = get_local_crew_singleton()
        if crew is None:
            runtime_settings = get_agent_runtime_settings()
            tm = self._task_manager
            if tm is None:
                from teaming24.task.manager import TaskManager
                tm = TaskManager()
            crew = create_local_crew(tm, runtime_settings=runtime_settings)

        # Bind AgenticNodeWorkforcePool for remote delegation
        try:
            nm = get_network_manager()
            pool = _build_agentic_node_workforce_pool(crew, nm)
            crew.bind_workforce_pool(pool, task_id=task_id)
        except Exception as exc:
            logger.debug("[Gateway] Agentic Node Workforce Pool bind failed: %s", exc)

        return await crew.execute(prompt, task_id)

    # ------------------------------------------------------------------
    # Payment gate
    # ------------------------------------------------------------------

    async def _check_payment(self, task_id: str) -> tuple:
        """Run the x402 payment gate. Returns (approved, info_dict)."""
        try:
            from teaming24.payment.crypto.x402.gate import get_payment_gate
            gate = get_payment_gate()
            receipt = await gate.process_task_payment(
                task_id=task_id,
                requester_id="local",
                payment_data=None,
                is_remote=False,
            )
            if not receipt.approved:
                return False, gate.build_402_response(receipt)
            return True, receipt.to_dict()
        except Exception as exc:
            logger.debug("[Gateway] payment gate unavailable: %s", exc)
            return True, {}

    # ------------------------------------------------------------------
    # WebSocket RPC handlers
    # ------------------------------------------------------------------

    async def _ws_handle_send(self, client, params: dict) -> dict:
        """Handle the ``send`` WS method — execute a message through the gateway."""
        text = params.get("text", "")
        channel = params.get("channel", "webchat")
        agent_id = params.get("agent_id", "")
        if not text:
            return {"error": "text is required"}

        result = await self.execute(
            text,
            channel=channel,
            peer_id=f"ws:{client.id}",
            agent_id=agent_id,
        )
        return result

    async def _ws_handle_status(self, _client, _params: dict) -> dict:
        """Handle the ``gateway_status`` WS method."""
        return self.get_status()

    # ------------------------------------------------------------------
    # Status / introspection
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return gateway runtime status."""
        uptime = time.time() - self.stats.started_at if self.stats.started_at else 0
        return {
            "running": self._started,
            "uptime_seconds": round(uptime, 1),
            "channels_active": self.stats.channels_active,
            "channel_adapters": list(self._channel_manager._adapters.keys()),
            "total_messages": self.stats.total_messages,
            "total_tasks": self.stats.total_tasks,
            "errors": self.stats.errors,
            "sessions_active": self._count_sessions(),
        }

    def _count_sessions(self) -> int:
        try:
            store = self._session_manager.store
            with store._conn() as conn:
                row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.warning("Failed to count sessions: %s", e, exc_info=True)
            return 0

    @property
    def channel_manager(self) -> ChannelManager:
        return self._channel_manager

    @property
    def session_manager(self) -> SessionManager:
        return self._session_manager


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_gateway: Gateway | None = None


def get_gateway() -> Gateway:
    """Return the global Gateway singleton."""
    global _gateway
    if _gateway is None:
        _gateway = Gateway()
    return _gateway
